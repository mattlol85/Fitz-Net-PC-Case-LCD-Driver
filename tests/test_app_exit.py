from __future__ import annotations

import sys

from fitzlcd.ui import app


class FlushSpy:
    def __init__(self) -> None:
        self.flushed = False

    def flush(self) -> None:
        self.flushed = True


def test_flush_tolerates_missing_streams(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    app._flush_std_streams()


def test_flush_still_flushes_present_streams(monkeypatch):
    out, err = FlushSpy(), FlushSpy()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    app._flush_std_streams()
    assert out.flushed and err.flushed


def test_flush_handles_one_missing_stream(monkeypatch):
    err = FlushSpy()
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", err)
    app._flush_std_streams()
    assert err.flushed
