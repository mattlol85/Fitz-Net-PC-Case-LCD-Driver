from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

from fitzlcd.sources.cs2gsi import Cs2GsiProvider


def _payload(
    *,
    health: float = 100,
    armor: float = 100,
    round_phase: str = "live",
    previously_health: float | None = None,
    previously_armor: float | None = None,
) -> dict:
    previously: dict = {}
    if previously_health is not None or previously_armor is not None:
        state: dict = {}
        if previously_health is not None:
            state["health"] = previously_health
        if previously_armor is not None:
            state["armor"] = previously_armor
        previously = {"player": {"state": state}}

    return {
        "provider": {"steamid": "123", "timestamp": 1},
        "map": {
            "name": "de_dust2",
            "phase": "live",
            "round": 3,
            "team_ct": {"score": 1},
            "team_t": {"score": 2},
        },
        "round": {"phase": round_phase},
        "player": {
            "name": "matt",
            "team": "CT",
            "state": {"health": health, "armor": armor, "money": 4000},
            "weapons": {},
            "match_stats": {"kills": 5, "deaths": 2, "assists": 1, "mvps": 1, "score": 20},
        },
        "previously": previously,
    }


@pytest.fixture
def provider():
    p = Cs2GsiProvider(port=0, token="secret", heartbeat=5.0, hit_window=12.0)
    yield p
    p.close()


class TestIngest:
    def test_read_never_raises_before_any_post(self, provider):
        assert provider.read() == {"cs2.connected": False, "cs2.status_text": "WAITING FOR MATCH…"}

    def test_publishes_flat_cs2_namespace(self, provider):
        provider._ingest(_payload())
        snap = provider.read()
        assert snap["cs2.player.state.health"] == 100
        assert snap["cs2.map.name"] == "de_dust2"
        assert snap["cs2.connected"] is True

    def test_health_drop_produces_a_hit_event(self, provider):
        provider._ingest(_payload(health=100))
        provider._ingest(_payload(health=73, previously_health=100))
        events = provider.read()["cs2.player.hit_events"]
        assert events[-1]["kind"] == "health"
        assert events[-1]["amount"] == pytest.approx(27)

    def test_armor_drop_produces_an_armor_event(self, provider):
        provider._ingest(_payload(armor=100))
        provider._ingest(_payload(armor=60, previously_armor=100))
        events = provider.read()["cs2.player.hit_events"]
        assert events[-1]["kind"] == "armor"
        assert events[-1]["amount"] == pytest.approx(40)

    def test_health_regen_or_round_reset_is_not_a_hit(self, provider):
        provider._ingest(_payload(health=0))
        provider._ingest(_payload(health=100, previously_health=0))
        assert provider.read().get("cs2.player.hit_events", []) == []

    def test_freezetime_transition_clears_stale_hit_buffer(self, provider):
        provider._ingest(_payload(health=100))
        provider._ingest(_payload(health=50, previously_health=100))
        assert provider.read()["cs2.player.hit_events"]

        provider._ingest(_payload(round_phase="freezetime"))
        assert provider.read()["cs2.player.hit_events"] == []

    def test_hit_events_outside_the_window_are_pruned(self, provider):
        provider._ingest(_payload(health=100))
        provider._hit_events.append({"t": time.monotonic() - 999, "amount": 10, "kind": "health"})
        provider._ingest(_payload(health=90, previously_health=100))
        events = provider.read()["cs2.player.hit_events"]
        assert all(time.monotonic() - e["t"] <= provider._hit_window for e in events)

    def test_no_post_within_heartbeat_reports_connected_false(self):
        p = Cs2GsiProvider(port=0, token="", heartbeat=0.01)
        try:
            p._ingest(_payload())
            time.sleep(0.05)
            assert p.read()["cs2.connected"] is False
        finally:
            p.close()


class TestAuth:
    def test_missing_token_is_rejected_when_configured(self, provider):
        addr = f"http://127.0.0.1:{provider._server.server_address[1]}/gsi"
        req = urllib.request.Request(addr, data=json.dumps(_payload()).encode(), method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=2)
        assert exc_info.value.code == 401
        assert provider.read()["cs2.connected"] is False

    def test_empty_configured_token_accepts_anything(self):
        p = Cs2GsiProvider(port=0, token="")
        try:
            addr = f"http://127.0.0.1:{p._server.server_address[1]}/gsi"
            urllib.request.urlopen(
                urllib.request.Request(addr, data=json.dumps(_payload()).encode(), method="POST"),
                timeout=2,
            )
            time.sleep(0.1)
            assert p.read()["cs2.connected"] is True
        finally:
            p.close()


class TestHttpEndToEnd:
    def test_post_to_the_real_socket_updates_the_snapshot(self):
        p = Cs2GsiProvider(port=0, token="")
        try:
            addr = f"http://127.0.0.1:{p._server.server_address[1]}/gsi"
            body = json.dumps(_payload()).encode()
            urllib.request.urlopen(
                urllib.request.Request(addr, data=body, method="POST"), timeout=2
            )
            time.sleep(0.1)
            assert p.read()["cs2.connected"] is True
        finally:
            p.close()
