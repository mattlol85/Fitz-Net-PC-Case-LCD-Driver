"""A believable ``wow.*`` snapshot for previews and tests, no game needed.

``tools/scene_sheet.py`` fills these in when the real provider has nothing, so
the WoW scene's layout can be checked from the contact sheet alone.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw


@lru_cache(maxsize=1)
def demo_portrait() -> Image.Image:
    """A stand-in for the game's portrait: a hooded figure on a dusky backdrop."""
    size = 128
    image = Image.new("RGB", (size, size), (38, 52, 78))
    d = ImageDraw.Draw(image)
    for i in range(size):  # a soft vertical dusk
        d.line((0, i, size, i), fill=(38 + i // 4, 52 + i // 6, 78 + i // 8))
    d.ellipse((24, 70, 104, 160), fill=(40, 90, 150))  # shoulders and robe
    d.ellipse((40, 26, 88, 82), fill=(226, 186, 150))  # face
    d.pieslice((32, 14, 96, 84), 180, 360, fill=(52, 110, 180))  # hood
    d.ellipse((52, 48, 58, 54), fill=(30, 30, 40))
    d.ellipse((70, 48, 76, 54), fill=(30, 30, 40))
    return image


def demo_metrics() -> dict[str, Any]:
    return {
        "wow.connected": True,
        "wow.status_text": "",
        "wow.last_seen_text": "",
        "wow.player.name": "Fitzwilliam",
        "wow.player.map_id": 1429,
        "wow.player.x": 0.421,
        "wow.player.y": 0.652,
        "wow.player.facing": 0.8,
        "wow.player.level": 23,
        "wow.player.class": "Mage",
        "wow.player.class_color": "#3FC7EB",
        "wow.player.health_pct": 86.0,
        "wow.player.power_pct": 64.0,
        "wow.player.hp": 1_548,
        "wow.player.hp_max": 1_800,
        "wow.player.power": 1_280,
        "wow.player.power_max": 2_000,
        "wow.player.power_type": "Mana",
        "wow.player.power_color": "#0070DD",
        "wow.player.portrait": demo_portrait(),
        "wow.player.portrait_source": "live",
        "wow.player.combat": True,
        "wow.player.instance": False,
        "wow.player.state_text": "IN COMBAT",
        "wow.zone": "Elwynn Forest",
        "wow.subzone": "Goldshire",
        "wow.location_text": "Goldshire, Elwynn Forest",
        "wow.coords_text": "42.1, 65.2",
        "wow.xp.pct": 61.4,
        "wow.xp.rested_pct": 37.0,
        "wow.xp.text": "11,890 / 19,360",
        "wow.gold": 1234.5678,
        "wow.gold_text": "1,234g 56s 78c",
        "wow.session.played_text": "2h 17m",
        "wow.session.xp": 48_210,
        "wow.session.xp_per_hour": 21_099.0,
        "wow.session.gold_text": "+38g 12s 4c",
        "wow.session.deaths": 2,
        "wow.durability.pct": 64,
        "wow.bags.free": 11,
        "wow.target.name": "Hogger",
        "wow.target.text": "Hogger · 11 Elite",
        "wow.target.reaction": "hostile",
        "wow.map.id": 1429,
        "wow.map.name": "Elwynn Forest",
        "wow.map.aspect": 1.5,
        "wow.party.count": 4,
        "wow.party": [
            {
                "slot": 1,
                "name": "Brannoch",
                "class": "Warrior",
                "color": "#C69B6D",
                "level": 24,
                "health_pct": 41.0,
                "map_id": 1429,
                "x": 0.445,
                "y": 0.631,
                "distance_yd": 98.0,
                "online": True,
                "dead": False,
            },
            {
                "slot": 2,
                "name": "Seraphinelle",
                "class": "Priest",
                "color": "#FFFFFF",
                "level": 23,
                "map_id": 1429,
                "x": 0.405,
                "y": 0.675,
                "distance_yd": 87.0,
                "online": True,
                "dead": False,
            },
            {
                "slot": 3,
                "name": "Quickfang",
                "class": "Rogue",
                "color": "#FFF468",
                "level": 22,
                "map_id": 1429,
                "x": 0.47,
                "y": 0.69,
                "distance_yd": 210.0,
                "online": True,
                "dead": True,
            },
            {
                "slot": 4,
                "name": "Thornwhisper",
                "class": "Druid",
                "color": "#FF7C0A",
                "level": 23,
                "map_id": 1453,
                "zone": "Stormwind City",
                "distance_yd": 1_240.0,
                "bearing": 2.6,
                "online": True,
                "dead": False,
            },
        ],
    }
