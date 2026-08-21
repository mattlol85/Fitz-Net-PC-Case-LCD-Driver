"""Standalone diagnostic CLI for the Jonsbo DS916 panel.

Talks to the device directly (no app, no render engine) so protocol behaviour can
be checked in isolation:

    python tools/ds916_probe.py detect
    python tools/ds916_probe.py info
    python tools/ds916_probe.py push assets/test.png
    python tools/ds916_probe.py bench --seconds 6
    python tools/ds916_probe.py brightness 60
    python tools/ds916_probe.py clear

Only one process may hold the port, so close the app (and the vendor software)
before running this.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import serial  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from serial.tools import list_ports  # noqa: E402

from fitzlcd.panels.ds916.protocol import (  # noqa: E402
    BAUD_RATE,
    CLEAR_SEQUENCE,
    KNOWN_IDS,
    Command,
    build_packet,
    chunk_image,
    extract_json,
)
from fitzlcd.render.encode import Transform, encode_jpeg  # noqa: E402

LANDSCAPE = (1920, 462)


def find_ports() -> list:
    return [p for p in list_ports.comports() if (p.vid, p.pid) in KNOWN_IDS]


def open_port(port: str | None) -> serial.Serial:
    if port is None:
        found = find_ports()
        if not found:
            raise SystemExit("no DS916-family panel found on any serial port")
        port = found[0].device
    ser = serial.Serial(port, BAUD_RATE, timeout=1.5, write_timeout=10)
    time.sleep(0.3)
    ser.reset_input_buffer()
    return ser


def write_raw(ser: serial.Serial, payload: bytes) -> None:
    for chunk in chunk_image(payload):
        ser.write(chunk)
    ser.flush()


def clear(ser: serial.Serial) -> None:
    for seq in CLEAR_SEQUENCE:
        write_raw(ser, seq)


def get_info(ser: serial.Serial, timeout: float = 2.0) -> dict | None:
    ser.write(build_packet(Command.GET_INFO))
    ser.flush()
    buf = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        buf += ser.read(4096)
        info = extract_json(bytes(buf))
        if info is not None:
            return info
        time.sleep(0.05)
    return None


def wake(ser: serial.Serial) -> bytes:
    ser.write(build_packet(Command.START))
    ser.flush()
    time.sleep(0.2)
    return ser.read(64)


def make_test_image(label: str) -> Image.Image:
    img = Image.new("RGB", LANDSCAPE, (8, 10, 32))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, LANDSCAPE[0] - 1, LANDSCAPE[1] - 1], outline=(255, 204, 0), width=6)
    d.text((60, 120), label, fill=(255, 204, 0))
    d.text((60, 200), "if this reads left-to-right, the transform is correct", fill=(200, 200, 200))
    for i, colour in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]):
        d.rectangle([1500 + i * 90, 320, 1570 + i * 90, 400], fill=colour)
    return img


def cmd_detect(args) -> int:
    found = find_ports()
    if not found:
        print("no DS916-family panel found")
        print("serial ports present:", [p.device for p in list_ports.comports()] or "none")
        return 1
    for p in found:
        print(f"{p.device}  VID:PID {p.vid:04X}:{p.pid:04X}  {p.description}")
        with open_port(p.device) as ser:
            clear(ser)
            info = get_info(ser)
            if info:
                print(f"    model    {info.get('model')}")
                print(f"    firmware {info.get('version')}")
                print(f"    size     {info.get('width')}x{info.get('height')}")
    return 0


def cmd_info(args) -> int:
    with open_port(args.port) as ser:
        clear(ser)
        info = get_info(ser)
    if info is None:
        print("no parseable response to GET_INFO")
        return 1
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0


def cmd_push(args) -> int:
    src = Path(args.image)
    img = make_test_image("FitzLCD test frame") if str(src) == "-" else Image.open(src)
    img = img.convert("RGB")
    if img.size != LANDSCAPE:
        img = img.resize(LANDSCAPE, Image.LANCZOS)
    jpeg = encode_jpeg(img, Transform.ROT_270, args.quality)
    with open_port(args.port) as ser:
        clear(ser)
        wake(ser)
        write_raw(ser, jpeg)
    print(f"pushed {len(jpeg):,} bytes at quality {args.quality}")
    return 0


def cmd_bench(args) -> int:
    qualities = [int(q) for q in args.qualities.split(",")]
    frames = {}
    for q in qualities:
        frames[q] = [
            encode_jpeg(make_test_image(f"bench q{q} frame {i}"), Transform.ROT_270, q)
            for i in range(8)
        ]
    with open_port(args.port) as ser:
        clear(ser)
        wake(ser)
        print(f"{'quality':>8} {'avg KB':>8} {'fps':>7} {'MB/s':>7}")
        for q in qualities:
            pool = frames[q]
            sent = count = 0
            start = time.perf_counter()
            deadline = start + args.seconds
            while time.perf_counter() < deadline:
                jpeg = pool[count % len(pool)]
                write_raw(ser, jpeg)
                sent += len(jpeg)
                count += 1
            elapsed = time.perf_counter() - start
            avg_kb = sent / count / 1024
            print(f"{q:>8} {avg_kb:>8.1f} {count / elapsed:>7.1f} {sent / elapsed / 1e6:>7.2f}")
    return 0


def cmd_brightness(args) -> int:
    """SET_BRIGHTNESS is unverified in the spec -- this is how we verify it."""
    with open_port(args.port) as ser:
        clear(ser)
        wake(ser)
        ser.write(build_packet(Command.SET_BRIGHTNESS, bytes([args.level])))
        ser.flush()
        time.sleep(0.3)
        reply = ser.read(64)
        print(f"reply: {reply.hex(' ') or '(none)'}")
        info = get_info(ser)
        if info:
            print(f"device reports brightness = {info.get('brightness')}")
    return 0


def cmd_clear(args) -> int:
    with open_port(args.port) as ser:
        clear(ser)
    print("cleared")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--port", help="serial port; autodetected when omitted")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("detect").set_defaults(func=cmd_detect)
    sub.add_parser("info").set_defaults(func=cmd_info)
    sub.add_parser("clear").set_defaults(func=cmd_clear)

    p_push = sub.add_parser("push")
    p_push.add_argument(
        "image", nargs="?", default="-", help="image path, or '-' for a test pattern"
    )
    p_push.add_argument("--quality", type=int, default=90)
    p_push.set_defaults(func=cmd_push)

    p_bench = sub.add_parser("bench")
    p_bench.add_argument("--seconds", type=float, default=5.0)
    p_bench.add_argument("--qualities", default="60,75,85,95,100")
    p_bench.set_defaults(func=cmd_bench)

    p_bright = sub.add_parser("brightness")
    p_bright.add_argument("level", type=int, choices=range(0, 101), metavar="LEVEL")
    p_bright.set_defaults(func=cmd_brightness)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
