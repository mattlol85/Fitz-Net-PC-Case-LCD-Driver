"""World of Warcraft: Forever integration.

WoW addons cannot open sockets or write files while the game runs (Forever writes
SavedVariables on logout only, and never reads them back), so there is no API to
push live state out. The bundled FitzLCD addon instead paints a tiny strip of
coloured cells in the top-left corner of the game, and the app reads those few
pixels off the screen and decodes them:

    protocol.py   the strip's wire format (pure; mirrored by addon/FitzLCD/Protocol.lua)
    capture.py    finds the WoW window and copies the strip region off the screen
    provider.py   the ``wow.*`` metric provider: capture thread, decode, state
    casc.py       a minimal read-only reader for the local game's CASC storage
    mapart.py     stitches the zone map's in-game tiles into a cached PNG
    install.py    locates the game and installs the addon

Nothing here reads game memory or sends input: the addon only uses the public
addon API and the app only reads the screen and the local install's files.
"""

from __future__ import annotations


class WowError(Exception):
    """Anything in the WoW integration that failed in a way the caller should hear about."""
