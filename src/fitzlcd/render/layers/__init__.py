"""Layer implementations.

Importing this package registers every built-in layer type with the scene
registry, so ``Scene.load`` can resolve them by name.
"""

from fitzlcd.render.layers.gauge import GaugeLayer, SparklineLayer
from fitzlcd.render.layers.media import MediaLayer, SolidLayer
from fitzlcd.render.layers.text import ClockLayer, TextLayer

__all__ = [
    "ClockLayer",
    "GaugeLayer",
    "MediaLayer",
    "SolidLayer",
    "SparklineLayer",
    "TextLayer",
]
