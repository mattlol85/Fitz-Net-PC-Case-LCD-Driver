# Jonsbo DS916 / "Jungle Leopard" 9.16" USB Display — Protocol Spec

Reverse-engineered from the vendor's own compiled software: the JONSBO-AIO (.NET,
obfuscated) app confirmed the transport and some commands via reflection against
the live device, and the "Jungle Leopard Display" Electron app (`app.asar`, plain
JavaScript, not obfuscated) provided the full, exact, and confirmed-working wire
protocol below. Everything in this document has been validated against the real
hardware except where marked "unverified / sourced from app code only."

## 1. Device identification

| Property | Value |
|---|---|
| USB VID:PID | `33C3:7788` |
| Windows device class | `Ports` (CDC-ACM virtual COM port) |
| Model string (from device) | `D215-FL7707N-9.16inch-hor` |
| Firmware version (from device) | `2.2` |
| Reported resolution | `width: 1920, height: 462` (landscape, logical/reported) |
| Actual frame-buffer orientation | **Portrait, 462×1920** — see §4 |
| USB Composite Device | also exposes other interfaces (isochronous audio etc. seen in traffic capture belong to *other* USB devices on the same hub — do not confuse with this device's traffic) |

The device enumerates as a normal CDC-ACM serial port (e.g. `COM4`, `COM5` — the
number depends on which physical USB header it's plugged into). No special driver
is required beyond the stock Windows USB CDC driver.

## 2. Serial connection settings

```
Baud rate:      2,000,000  (value used by the vendor app; CDC-ACM devices
                             generally ignore the configured baud rate since
                             the real transport is full-speed/high-speed USB,
                             but set it anyway for driver compliance)
Data bits:      8
Parity:         None
Stop bits:      1
Flow control:   None (no RTS/CTS, no XON/XOFF)
```

## 3. Command packet framing

All non-image commands use this framing:

```
Offset  Size  Field
0       2     Magic header: 0x55 0xAA
2       2     Total packet length (little-endian uint16) = payload_len + 7
4       1     Command key (1 byte)
5       N     Payload (N = payload_len, may be 0 bytes)
5+N     2     Checksum (little-endian uint16)
```

**Checksum algorithm:** 16-bit additive checksum (NOT CRC) of every byte in the
packet *before* the checksum field itself (i.e. sum of header + length + key +
payload), masked to 16 bits.

```python
def build_packet(key: int, payload: bytes = b"") -> bytes:
    payload = bytes(payload)
    length = len(payload) + 7          # total packet size
    header = bytes([0x55, 0xAA, length & 0xFF, (length >> 8) & 0xFF, key])
    body = header + payload
    checksum = sum(body) & 0xFFFF
    return body + bytes([checksum & 0xFF, (checksum >> 8) & 0xFF])
```

**Worked example (captured from real hardware):** sending `key=0x11` with no
payload produced the response `55 AA 08 00 11 00 18 01`:
- `55 AA` — magic
- `08 00` — length = 8 = 0 (payload) + 7 + 1 (response payload byte, see below)
- `11` — key echoed back
- `00` — 1-byte payload (ack/status code, `0x00` = success)
- `18 01` — checksum = `0x0118` = sum of `[0x55,0xAA,0x08,0x00,0x11,0x00]` = 280 = `0x0118` ✓ (matches, little-endian)

**Parsing a response:** strip the first 5 bytes and the last 2 bytes to get the
payload: `payload = response[5:-2]`.

## 4. Image / live-video protocol

Image frames are **not** wrapped in the `0x55 0xAA` command framing — they are
sent as raw bytes directly to the serial port.

**Sequence to display an image:**
1. Send the "start/wake" command once: `build_packet(0x11)` — **this is required.**
   Without it, the device keeps playing its internal idle demo loop and silently
   ignores raw image data written to the port.
2. JPEG-encode your frame and write the raw JPEG bytes directly to the serial
   port, chunked into **20,480-byte (20 KiB) pieces**, back-to-back, with **no
   per-chunk header or footer**. The device detects frame boundaries via the
   JPEG stream itself.
3. For continuous/live video, repeat step 2 for each new frame (no need to
   resend the start command between frames, only once at the start of a live
   session).

```python
def write_image(serial_conn, jpeg_bytes: bytes, chunk_size: int = 20 * 1024):
    for i in range(0, len(jpeg_bytes), chunk_size):
        serial_conn.write(jpeg_bytes[i:i + chunk_size])
    serial_conn.flush()
```

The vendor app uses **JPEG quality 100** for live rendering.

### 4.1 Orientation quirk — IMPORTANT

The device reports `width: 1920, height: 462` (landscape) via `getDeviceInfo`
(§5), **but the actual image buffer it expects is portrait: 462 (w) × 1920 (h)**.
This model's `getDeviceInfo` response includes an `angle` field containing a
garbled/magic byte sequence (hex `05 0B ?? 65 15` — the `??` byte varies) that
the vendor app specifically detects as a sentinel meaning "this panel is
physically mounted landscape but the frame buffer is native portrait; swap
width/height and apply a 90° rotation before rendering."

**In practice:** compose your image normally in landscape (1920×462, text
reading horizontally), then rotate it **-90° (clockwise)** so it becomes a
462×1920 portrait image, JPEG-encode *that*, and send it. This was empirically
confirmed on real hardware — rotating the other direction (+90°) produced
upside-down/sideways output.

```python
from PIL import Image
scene = Image.new("RGB", (1920, 462), ...)   # build your landscape scene here
send_buffer = scene.rotate(-90, expand=True)  # -> 462x1920, ready to JPEG-encode
```

### 4.2 Clearing / blanking the screen

```python
write_image(serial_conn, bytes([0xFF, 0xD9, 0xFF, 0xD9]))  # two JPEG EOI markers
write_image(serial_conn, bytes([0x00, 0x00, 0x00, 0x00]))
```
This is called by the vendor app right after connecting, before querying
device info.

## 5. Command reference

| Key | Name | Payload | Notes |
|---|---|---|---|
| `0x01` | Restart | none | Restarts the device |
| `0x03` | Set brightness | `[level]` (1 byte, 0–100) | *Unverified by us directly — sourced from app code* |
| `0x06` | Get device info | none | **Confirmed.** Response payload is a JSON string (see §5.1) |
| `0x0C` | OTA transfer header | 10 bytes: `[0xF2, 0xFF, size&0xFF, (size>>8)&0xFF, (size>>16)&0xFF, (size>>24)&0xFF, 0,0,0,0]` | Precedes a firmware `.bin` sent via the raw `writeFile` chunking mechanism (§4). **Do not experiment with this unless you accept the risk of bricking the device.** |
| `0x11` | Start / wake live stream | none | **Confirmed working.** Must be sent once before pushing image frames |
| `0x14` | Set motion-before-off-screen | `[byte, byte]` | Also sent with `[0,0]` as part of `stop` on some sub-models |
| `0x15` | Set motion timeout | `[byte, byte]` | Only sent when firmware version ≥ 2.8 |
| `0x20` | Set region | UTF-8 bytes of a region string | Region/locale identifier |
| `0x21` | Close | none | Only sent when firmware version ≥ 3.1 |
| `0x23` | Set serial number | provided value | Preceded/followed by the clear sequence (§4.2) |
| `0x25` | Set motor | `[value]` (0 or 1) | Only applies to a motorized-region variant (`ycc28_v1`) — **not applicable to the DS916** |
| `0x26` | Set real-time play timeout | `[value]` | Only sent when firmware version ≥ 4.1 |

All keys other than `0x06`, `0x11`, and the image protocol (§4) are sourced from
the vendor app's source code but were **not individually tested against the
real device** in this session — treat them as a strong starting point, not
gospel, and verify each before relying on it.

### 5.1 `getDeviceInfo` (key `0x06`) response

Response payload is a JSON object:

```json
{
  "cmd": "info",
  "data": {
    "uid": "8370D07832275608000B32020000B004",
    "width": 1920,
    "height": 462,
    "diplay_on": true,
    "brightness": 100,
    "i_blocks": 204560,
    "i_block_size": 512,
    "i_block_free": 126744,
    "i_path": "/data",
    "e_blocks": 0,
    "e_block_size": 0,
    "e_block_free": 0,
    "e_path": "/sdcard",
    "model": "D215-FL7707N-9.16inch-hor",
    "version": "2.2",
    "region": "",
    "angle": "<magic sentinel bytes — see §4.1>"
  }
}
```

Note the response can exceed a single USB packet read — read/accumulate bytes
until the JSON parses successfully (the vendor app retries parsing against a
growing buffer for this reason).

Also note the typo `diplay_on` is in the device's own firmware, not a transcription
error here.

## 6. Minimal end-to-end working example (Python)

```python
import io, time, serial
from PIL import Image, ImageDraw, ImageFont

PORT = "COM5"        # adjust to your enumerated port
BAUD = 2_000_000
LANDSCAPE_W, LANDSCAPE_H = 1920, 462

def build_packet(key: int, payload: bytes = b"") -> bytes:
    payload = bytes(payload)
    length = len(payload) + 7
    header = bytes([0x55, 0xAA, length & 0xFF, (length >> 8) & 0xFF, key])
    body = header + payload
    checksum = sum(body) & 0xFFFF
    return body + bytes([checksum & 0xFF, (checksum >> 8) & 0xFF])

def write_image(ser, jpeg_bytes: bytes, chunk_size: int = 20 * 1024):
    for i in range(0, len(jpeg_bytes), chunk_size):
        ser.write(jpeg_bytes[i:i + chunk_size])
    ser.flush()

def get_device_info(ser) -> bytes:
    ser.write(build_packet(0x06))
    ser.flush()
    time.sleep(0.3)
    resp = ser.read(4096)
    return resp[5:-2]   # JSON payload bytes

# Build a landscape scene, then rotate into the portrait buffer the device wants
scene = Image.new("RGB", (LANDSCAPE_W, LANDSCAPE_H), (10, 10, 40))
draw = ImageDraw.Draw(scene)
draw.text((40, 180), "Hello, DS916!", fill=(255, 255, 0))
send_img = scene.rotate(-90, expand=True)   # -> 462x1920

buf = io.BytesIO()
send_img.save(buf, format="JPEG", quality=100)
jpeg_bytes = buf.getvalue()

ser = serial.Serial(PORT, BAUD, timeout=1.5, write_timeout=5)
time.sleep(0.3)
ser.reset_input_buffer()

ser.write(build_packet(0x11))   # start/wake — required
ser.flush()
time.sleep(0.2)
ser.read(64)                    # drain ack

write_image(ser, jpeg_bytes)    # push the frame
ser.close()
```

## 7. Open questions / suggested next steps for your own program

- **Brightness (`0x03`)** and most other command keys in §5 are sourced from
  app code, not individually verified — test each in isolation before building
  on it.
- The exact meaning of the single-byte ack/status codes (e.g. `0x00` seen after
  `0x11`) isn't mapped out — the app has a `DeviceConstant.receivedCode` lookup
  table for named result codes that wasn't extracted in this session.
- Live-video frame rate / pacing: the vendor app targets a configurable rate
  (tens of FPS depending on sub-model) — no explicit "next frame" signalling
  was found beyond just writing the next JPEG; back-to-back writes appear to be
  how continuous video works, but sustained-throughput behavior (buffering,
  backpressure, `drain()` handling) wasn't stress-tested here.
- `0x0C` (OTA/firmware update) is documented for completeness only — treat
  with caution.
- The garbled `angle` sentinel value is specific to this `-hor` sub-model;
  other sizes/orientations in the same product family may report a normal
  numeric angle (`0`, `90`, `180`, `270`) instead — don't assume the rotation
  quirk applies universally if you ever target a different panel size.

---

## 8. Findings from building FitzLCD (2026-08-21)

Everything below was measured against the real DS916 on `COM5` while building the
driver in this repo. It corrects and extends the sections above.

### 8.1 The `angle` sentinel is not valid UTF-8 — parse the info reply leniently

The `getDeviceInfo` payload is *almost* JSON. The `angle` field contains a raw
`0x93` byte inside the string, so `json.loads(payload.decode("utf-8"))` raises
`UnicodeDecodeError` — a strict parser simply never completes and the handshake
appears to time out. Decode the JSON with `errors="replace"`, and read the raw
bytes separately if you need the sentinel itself (`protocol.raw_angle`).

Confirmed device values: model `D215-FL7707N-9.16inch-hor`, firmware `2.2`,
1920×462, uid `8370D07832275608000B32020000B004`.

### 8.2 Transport throughput — not the bottleneck

Host-side write rates for 462×1920 JPEGs sent back to back, measured with
`tools/ds916_probe.py bench`:

| Quality | Avg frame | Frames/s | MB/s |
|---|---|---|---|
| 60 | 27.4 KB | 364 | 10.2 |
| 75 | 30.3 KB | 347 | 10.8 |
| 85 | 33.3 KB | 333 | 11.4 |
| 95 | 55.9 KB | 237 | 13.6 |
| 100 | 68.2 KB | 208 | 14.6 |

Quality 100 is affordable on this link. **But see §8.3 — these are host write
rates, not the panel's render rate.**

### 8.3 Flooding the panel wedges it until it is power-cycled

This is the most important finding, and it corrects §7's open question about
sustained throughput. The benchmark above measures how fast the host can push
bytes into the USB pipe, **not** how fast the panel consumes them. After roughly
20 seconds of unpaced writing (~350 fps), the device stopped draining its bulk
endpoint and did not recover:

- `Serial.write()` blocks once the driver's output buffer fills.
- `Serial.flush()` blocks **forever** — on Windows it waits on
  `FlushFileBuffers`, which takes no timeout. Never call it on the frame path.
- Worse, `serial.Serial()` — the *open* itself — then blocks indefinitely too, so
  even a fresh process cannot recover the port. The device still enumerates and
  still reports `Status: OK` in Windows, which makes it look healthy.
- It did not recover on its own after 15+ minutes. Only a power cycle (unplug the
  USB header, or reboot) clears it.

Consequences baked into this driver:

1. **Pace every frame.** `PanelCaps.max_fps` defaults to 30, and the engine drops
   frames rather than queueing them.
2. **No `flush()`** in `push_frame`; rely on `write_timeout` and treat a timeout
   as backpressure.
3. **Open on a watchdog thread** (`_open_serial_guarded`) so a wedged panel gives
   a clear error in 5 s instead of hanging the app.
4. The abandoned open thread can block interpreter shutdown, so the GUI calls
   `os._exit()` once Qt's event loop returns.

If you write your own client, "how fast can I write" is the wrong question. The
right one is "how fast will the panel render", and the answer is much lower.

### 8.4 Command keys — still unverified

`0x03` (brightness) and the rest of §5 remain **unverified** on hardware: the
panel wedged before that test ran. `tools/ds916_probe.py brightness <0-100>`
exists to check it, and `PanelCaps.supports_brightness` stays `False` (with the
GUI slider disabled) until it is confirmed. `0x0C` (OTA) is deliberately never
sent by this codebase.

### 8.5 Confirmed working

- Autodetect by USB VID:PID `33C3:7788` via `serial.tools.list_ports`.
- `GET_INFO` (`0x06`) handshake and lenient JSON parse.
- `START` (`0x11`) wake, then raw chunked JPEG at 20 KiB — image displayed.
- The −90° (clockwise, `ROT_270`) transform: compose 1920×462 landscape, rotate
  to 462×1920, encode, send. Text reads horizontally on the panel.
