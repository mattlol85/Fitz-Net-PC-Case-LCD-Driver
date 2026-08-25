from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from fitzlcd.sources.claude_limits import (
    OAUTH_BETA,
    USAGE_URL,
    ClaudeLimitsProvider,
    detect_client_version,
    read_access_token,
)

# Verbatim shape of the real endpoint's response.
PAYLOAD = {
    "five_hour": {"utilization": 33.0, "resets_at": "2099-04-11T07:00:00.528743+00:00"},
    "seven_day": {"utilization": 13.0, "resets_at": "2099-04-17T00:59:59.951713+00:00"},
    "seven_day_opus": None,
    "seven_day_sonnet": {"utilization": 1.0, "resets_at": "2099-04-16T03:00:00.951719+00:00"},
    "extra_usage": {
        "is_enabled": False,
        "monthly_limit": None,
        "used_credits": None,
        "utilization": None,
    },
}


def write_credentials(root: Path, *, expires_in_ms: float = 3_600_000) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".credentials.json").write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "test-token",
                    "refreshToken": "test-refresh",
                    "expiresAt": (time.time() * 1000) + expires_in_ms,
                    "subscriptionType": "max",
                }
            }
        ),
        encoding="utf-8",
    )


class Recorder:
    """Stand-in for the HTTP call; records every request so tests can count them."""

    def __init__(self, *responses: tuple[int, object]) -> None:
        self.responses = list(responses) or [(200, PAYLOAD)]
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, headers: dict) -> tuple[int, str]:
        self.calls.append((url, headers))
        status, body = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(body, Exception):
            raise body
        return status, body if isinstance(body, str) else json.dumps(body)


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch):
    """A real token or config dir on the dev machine must not reach these tests."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / ".claude"


def make(root: Path, fetch, **kw) -> ClaudeLimitsProvider:
    kw.setdefault("cache_path", root / "cache.json")
    kw.setdefault("version", "9.9.9")
    return ClaudeLimitsProvider(root=root, fetch=fetch, **kw)


class TestRequest:
    def test_sends_the_headers_that_avoid_the_throttled_bucket(self, root: Path):
        write_credentials(root)
        fetch = Recorder()
        make(root, fetch).read()

        url, headers = fetch.calls[0]
        assert url == USAGE_URL
        assert headers["Authorization"] == "Bearer test-token"
        assert headers["anthropic-beta"] == OAUTH_BETA
        # Without a claude-code User-Agent the endpoint 429s at any interval.
        assert headers["User-Agent"] == "claude-code/9.9.9"

    def test_polls_once_per_interval_not_once_per_read(self, root: Path):
        write_credentials(root)
        fetch = Recorder()
        provider = make(root, fetch)
        for _ in range(50):
            provider.read()
        assert len(fetch.calls) == 1, "read() must not hit the network every tick"

    def test_poll_interval_is_floor_clamped(self, root: Path):
        write_credentials(root)
        provider = make(root, Recorder(), poll_seconds=5.0)
        assert provider._poll_seconds >= 180.0, "a too-eager interval must be clamped"


class TestParsing:
    def test_publishes_session_and_week_percentages(self, root: Path):
        write_credentials(root)
        snap = make(root, Recorder()).read()
        assert snap["claude.limits.session.pct"] == 33.0
        assert snap["claude.limits.week.pct"] == 13.0
        assert snap["claude.limits.ok"] is True
        assert snap["claude.limits.status_text"] == ""

    def test_null_window_is_absent_rather_than_zero(self, root: Path):
        """seven_day_opus is null on some plans; a confident 0% would be a lie."""
        write_credentials(root)
        snap = make(root, Recorder()).read()
        assert "claude.limits.week_opus.pct" not in snap
        assert snap["claude.limits.week_sonnet.pct"] == 1.0

    def test_reset_times_become_countdowns(self, root: Path):
        write_credentials(root)
        snap = make(root, Recorder()).read()
        assert snap["claude.limits.session.resets_in"] > 0
        assert snap["claude.limits.session.resets_text"]

    def test_null_extra_usage_fields_are_omitted(self, root: Path):
        write_credentials(root)
        snap = make(root, Recorder()).read()
        assert snap["claude.limits.extra.enabled"] is False
        assert "claude.limits.extra.used_credits" not in snap

    def test_garbage_body_does_not_raise(self, root: Path):
        write_credentials(root)
        snap = make(root, Recorder((200, "<html>nope</html>"))).read()
        assert snap["claude.limits.ok"] is False


class TestFailureModes:
    def test_missing_credentials_reports_not_signed_in(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        fetch = Recorder()
        snap = make(root, fetch).read()
        assert snap["claude.limits.status_text"] == "NOT SIGNED IN"
        assert not fetch.calls, "must not call the endpoint without a token"

    def test_expired_token_does_not_attempt_a_refresh(self, root: Path):
        """Rotating the refresh token could invalidate the user's real login."""
        write_credentials(root, expires_in_ms=-1000)
        fetch = Recorder()
        snap = make(root, fetch).read()
        assert not fetch.calls, "expired token must not be sent"
        assert snap["claude.limits.status_text"] in {"STALE", "NO USAGE DATA"}

    def test_rate_limit_backs_off_instead_of_hammering(self, root: Path):
        write_credentials(root)
        fetch = Recorder((429, '{"error":"rate_limit_error"}'))
        provider = make(root, fetch)
        for _ in range(30):
            provider.read()
        assert len(fetch.calls) == 1, "a 429 must not be retried immediately"
        assert provider.read()["claude.limits.status_text"] == "RATE LIMITED"

    def test_backoff_grows_then_resets_after_success(self, root: Path):
        write_credentials(root)
        provider = make(root, Recorder((429, "{}")))
        delays = []
        for _ in range(5):
            provider._back_off()
            delays.append(provider._next_attempt - time.monotonic())
        assert delays[0] < delays[1] < delays[2] < delays[3]
        assert delays[4] == pytest.approx(
            delays[3], abs=1.0
        ), "backoff should cap, not grow forever"

    def test_rate_limit_still_serves_the_last_good_reading(self, root: Path):
        write_credentials(root)
        provider = make(root, Recorder((200, PAYLOAD), (429, "{}")))
        assert provider.read()["claude.limits.session.pct"] == 33.0

        provider._next_attempt = 0.0  # due again
        snap = provider.read()
        assert snap["claude.limits.session.pct"] == 33.0, "cached value should survive a 429"
        assert snap["claude.limits.status_text"] == "RATE LIMITED"
        assert snap["claude.limits.ok"] is False

    def test_network_error_is_not_fatal(self, root: Path):
        write_credentials(root)
        snap = make(root, Recorder((200, OSError("no route to host")))).read()
        assert snap["claude.limits.ok"] is False
        assert snap["claude.limits.status_text"] == "OFFLINE"


class TestCache:
    def test_cache_survives_a_restart_without_refetching(self, root: Path):
        write_credentials(root)
        cache = root / "cache.json"
        first = Recorder()
        make(root, first, cache_path=cache).read()
        assert len(first.calls) == 1

        second = Recorder()
        revived = make(root, second, cache_path=cache)
        snap = revived.read()
        assert snap["claude.limits.session.pct"] == 33.0
        assert not second.calls, "a restart must reuse the cache, not spend a request"

    def test_age_is_reported(self, root: Path):
        write_credentials(root)
        snap = make(root, Recorder()).read()
        assert snap["claude.limits.age_seconds"] == pytest.approx(0, abs=5)

    def test_a_corrupt_cache_is_ignored(self, root: Path):
        write_credentials(root)
        cache = root / "cache.json"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("{not json", encoding="utf-8")
        snap = make(root, Recorder(), cache_path=cache).read()
        assert snap["claude.limits.session.pct"] == 33.0


class TestCredentialHelpers:
    def test_env_var_wins(self, root: Path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "from-env")
        token, expires = read_access_token(root)
        assert (token, expires) == ("from-env", None)

    def test_expiry_milliseconds_are_converted(self, root: Path, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        write_credentials(root, expires_in_ms=3_600_000)
        _, expires = read_access_token(root)
        assert expires == pytest.approx(time.time() + 3600, abs=30)

    def test_missing_file_is_not_an_error(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        assert read_access_token(tmp_path / "nope") == (None, None)

    def test_version_detected_from_transcripts(self, root: Path):
        path = root / "projects" / "proj" / "a.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"type": "assistant", "version": "2.1.243", "message": {}}),
            encoding="utf-8",
        )
        assert detect_client_version(root) == "2.1.243"

    def test_version_falls_back_when_nothing_local(self, tmp_path: Path):
        assert detect_client_version(tmp_path / "empty") == "2.1.0"


class TestCountdownFormatting:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0, "now"),
            (-5, "now"),
            (30, "<1m"),
            (90, "1m"),
            (59 * 60, "59m"),
            (60 * 60, "1h 00m"),
            (3 * 60 * 60 + 13 * 60, "3h 13m"),
            (23 * 60 * 60 + 59 * 60, "23h 59m"),
            # The weekly window really does run to days; 47h reads badly.
            (47 * 60 * 60 + 3 * 60, "1d 23h"),
            (6 * 24 * 60 * 60, "6d 00h"),
        ],
    )
    def test_humanise(self, seconds, expected):
        from fitzlcd.sources.claude_limits import _humanise

        assert _humanise(seconds) == expected
