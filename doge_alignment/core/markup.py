r"""Pango markup for names and labels, made safe for ImageMagick.

ImageMagick's ``pango:`` reader expands its own escapes before Pango sees
the text: ``%[fx:...]`` runs an expression, ``%w`` and friends print image
properties, ``\n`` becomes a newline, and ``@file`` reads a file. Names
come from Discord, so every piece of text goes through :func:`escape`, and
the markup always starts with ``<`` and ends with ``>`` (no ``@`` prefix, no
trailing ``[...]`` read modifier).
"""

from html import escape as html_escape

FAMILY = "DejaVu Sans"


def escape(text: str) -> str:
        """Escape ``text`` for Pango markup and ImageMagick's text escapes."""
        return (
                html_escape(text, quote=True)
                .replace("\\", "\\\\")
                .replace("%", "%%")
        )


def markup(text: str, size: int, color: str, family: str = FAMILY) -> str:
        """Return bold Pango markup for ``text`` at ``size`` pixels.

        ``size`` is in pixels when rendered at 72 dpi (Pango sizes are in
        1/1024 of a point).
        """
        return (
                f'<span font_family="{escape(family)}" weight="bold" '
                f'size="{size * 1024}" foreground="{escape(color)}">'
                f"{escape(text)}</span>"
        )
