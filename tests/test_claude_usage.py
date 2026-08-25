from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from fitzlcd.sources.claude_usage import ClaudeUsageProvider


def _write_transcript(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def _usage_record(
    *,
    timestamp: str,
    session_id: str = "sess-1",
    model: str = "claude-sonnet-5",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_creation: int = 0,
    cache_read: int = 0,
) -> dict:
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "sessionId": session_id,
        "message": {
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
            },
        },
    }


def _now_iso() -> str:
    """Current instant in UTC, so it always lands in today's *local* bucket."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@pytest.fixture
def claude_dir(tmp_path: Path) -> Path:
    return tmp_path / ".claude"


class TestClaudeUsageProvider:
    def test_no_data_reports_unavailable_status(self, claude_dir: Path):
        provider = ClaudeUsageProvider(root=claude_dir)
        snap = provider.read()
        assert snap["claude.status_text"] == "NO CLAUDE CODE ACTIVITY FOUND"
        assert "claude.last_active_seconds_ago" not in snap

    def test_aggregates_todays_usage(self, claude_dir: Path):
        today = _now_iso()
        _write_transcript(
            claude_dir / "projects" / "proj" / "a.jsonl",
            [_usage_record(timestamp=today, input_tokens=100, output_tokens=50)],
        )

        provider = ClaudeUsageProvider(root=claude_dir)
        snap = provider.read()

        assert snap["claude.today.input_tokens"] == 100
        assert snap["claude.today.output_tokens"] == 50
        assert snap["claude.today.tokens"] == 150
        assert snap["claude.today.messages"] == 1
        assert snap["claude.today.sessions"] == 1
        assert snap["claude.status_text"] == ""
        assert snap["claude.last_active_seconds_ago"] >= 0

    def test_ignores_records_without_usage(self, claude_dir: Path):
        today = _now_iso()
        _write_transcript(
            claude_dir / "projects" / "proj" / "a.jsonl",
            [
                {"type": "user", "timestamp": today, "message": {"role": "user"}},
                _usage_record(timestamp=today),
            ],
        )

        provider = ClaudeUsageProvider(root=claude_dir)
        snap = provider.read()
        assert snap["claude.today.messages"] == 1

    def test_multiple_sessions_are_counted_distinctly(self, claude_dir: Path):
        today = _now_iso()
        _write_transcript(
            claude_dir / "projects" / "proj" / "a.jsonl",
            [
                _usage_record(timestamp=today, session_id="sess-1"),
                _usage_record(timestamp=today, session_id="sess-2"),
            ],
        )

        provider = ClaudeUsageProvider(root=claude_dir)
        snap = provider.read()
        assert snap["claude.today.sessions"] == 2

    def test_old_usage_excluded_from_today_but_included_all_time(self, claude_dir: Path):
        _write_transcript(
            claude_dir / "projects" / "proj" / "a.jsonl",
            [_usage_record(timestamp="2020-01-01T12:00:00Z", input_tokens=10, output_tokens=5)],
        )

        provider = ClaudeUsageProvider(root=claude_dir)
        snap = provider.read()
        assert snap["claude.today.tokens"] == 0
        assert snap["claude.total.tokens"] == 15

    def test_cost_is_positive_for_known_model_tiers(self, claude_dir: Path):
        today = _now_iso()
        _write_transcript(
            claude_dir / "projects" / "proj" / "a.jsonl",
            [_usage_record(timestamp=today, model="claude-opus-5", input_tokens=1000, output_tokens=1000)],
        )

        provider = ClaudeUsageProvider(root=claude_dir)
        snap = provider.read()
        assert snap["claude.today.cost_usd"] > 0
        assert snap["claude.model"] == "claude-opus-5"

    def test_rescans_changed_files_without_double_counting(self, claude_dir: Path):
        today = _now_iso()
        path = claude_dir / "projects" / "proj" / "a.jsonl"
        _write_transcript(path, [_usage_record(timestamp=today, input_tokens=100, output_tokens=0)])

        provider = ClaudeUsageProvider(root=claude_dir, refresh_seconds=0.0)
        assert provider.read()["claude.today.input_tokens"] == 100

        _write_transcript(
            path,
            [
                _usage_record(timestamp=today, input_tokens=100, output_tokens=0),
                _usage_record(timestamp=today, input_tokens=200, output_tokens=0),
            ],
        )
        snap = provider.read()
        assert snap["claude.today.input_tokens"] == 300

    def test_available_reflects_root_existence(self, claude_dir: Path):
        provider = ClaudeUsageProvider(root=claude_dir)
        assert provider.available is False

        claude_dir.mkdir(parents=True)
        assert provider.available is True
