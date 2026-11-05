"""Recognise the image formats avatars come in, without decoding them."""

from typing import Literal

ImageFormat = Literal["png", "jpeg", "gif", "webp"]


def sniff(data: bytes) -> ImageFormat | None:
        """Return the format of image ``data``, if it's one we decode."""
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
                return "png"
        if data.startswith(b"\xff\xd8\xff"):
                return "jpeg"
        if data.startswith((b"GIF87a", b"GIF89a")):
                return "gif"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
                return "webp"
        return None
