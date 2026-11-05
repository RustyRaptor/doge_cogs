"""Name the colours of an avatar's palette in words a model understands."""

from collections.abc import Sequence

NAMED: dict[str, tuple[int, int, int]] = {
        "black": (0, 0, 0),
        "charcoal": (54, 69, 79),
        "grey": (128, 128, 128),
        "silver": (192, 192, 192),
        "white": (255, 255, 255),
        "dark red": (139, 0, 0),
        "red": (220, 20, 60),
        "pink": (255, 105, 180),
        "pale pink": (255, 209, 220),
        "maroon": (128, 0, 64),
        "brown": (139, 69, 19),
        "tan": (210, 180, 140),
        "beige": (245, 245, 220),
        "orange": (255, 140, 0),
        "gold": (255, 200, 0),
        "yellow": (255, 255, 0),
        "olive": (128, 128, 0),
        "lime": (50, 205, 50),
        "green": (0, 128, 0),
        "dark green": (0, 70, 30),
        "teal": (0, 128, 128),
        "cyan": (0, 220, 220),
        "sky blue": (135, 206, 235),
        "blue": (30, 80, 220),
        "navy": (0, 0, 110),
        "indigo": (75, 0, 130),
        "purple": (128, 0, 160),
        "lavender": (190, 160, 230),
        "magenta": (230, 0, 230),
}


def hex_rgb(value: str) -> tuple[int, int, int]:
        """Return ``#rrggbb`` as an RGB triple."""
        text = value.lstrip("#")
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def name_color(value: str) -> str:
        """Return the nearest named colour to ``#rrggbb``."""
        r, g, b = hex_rgb(value)
        return min(
                NAMED,
                key=lambda n: (
                        2 * (r - NAMED[n][0]) ** 2
                        + 4 * (g - NAMED[n][1]) ** 2
                        + 3 * (b - NAMED[n][2]) ** 2
                ),
        )


def describe_palette(palette: Sequence[tuple[str, float]]) -> str:
        """Return a palette as ``"navy 41%, gold 22%, ..."`` (merged names)."""
        merged: dict[str, float] = {}
        for value, share in palette:
                name = name_color(value)
                merged[name] = merged.get(name, 0) + share
        ordered = sorted(merged.items(), key=lambda kv: -kv[1])
        return ", ".join(f"{name} {share:.0%}" for name, share in ordered)
