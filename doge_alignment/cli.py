"""Command-line front end: render a YAML chart without Discord.

    python -m doge_alignment text chart.yaml
    python -m doge_alignment image chart.yaml -o chart.png
    python -m doge_alignment tiles chart.yaml -o tiles/

The YAML is what ``/alignment export`` produces. Everyone in the file counts as
present, and avatars are downloaded from their saved URLs unless
``--no-avatars`` is given.
"""

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

import aiohttp

from .core.fetch import avatar_urls, by_user, fetch_images
from .core.storage import ChartFormatError, from_yaml
from .core.view import (
        Cell,
        cells,
        everyone,
        format_text,
        tile_filename,
)


def _parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
                prog="python -m doge_alignment",
                description="Show an exported alignment chart.",
        )
        parser.add_argument(
                "mode",
                choices=("text", "image", "tiles"),
                help="text, one PNG, or one PNG per alignment",
        )
        parser.add_argument("chart", type=Path, help="exported YAML chart")
        parser.add_argument(
                "-o",
                "--output",
                type=Path,
                help="PNG file (image) or directory (tiles)",
        )
        parser.add_argument(
                "--no-avatars",
                action="store_true",
                help="draw initials instead of downloading avatars",
        )
        return parser


async def _avatars(chart_cells: tuple[Cell, ...]) -> dict[str, bytes]:
        people = everyone(chart_cells)
        async with aiohttp.ClientSession() as session:
                images = await fetch_images(session, avatar_urls(people))
        return by_user(people, images)


def main(argv: Sequence[str] | None = None) -> int:
        """Run the command line; return the exit status."""
        parser = _parser()
        args = parser.parse_args(argv)
        try:
                chart = from_yaml(args.chart.read_text(encoding="utf-8"))
        except (OSError, ChartFormatError) as error:
                parser.error(f"{args.chart}: {error}")
        chart_cells = cells(chart)
        if args.mode == "text":
                print(format_text(chart_cells) or "Nobody is on the chart.")  # noqa: T201
                return 0
        # Rendering needs ImageMagick; text mode doesn't
        from .core.render import render_chart, render_tiles  # noqa: PLC0415

        avatars = {} if args.no_avatars else asyncio.run(_avatars(chart_cells))
        if args.mode == "image":
                out = args.output or Path("alignment.png")
                out.write_bytes(render_chart(chart_cells, avatars))
                print(out)  # noqa: T201
                return 0
        folder = args.output or Path("alignment-tiles")
        folder.mkdir(parents=True, exist_ok=True)
        for index, (cell, png) in enumerate(
                zip(
                        chart_cells,
                        render_tiles(chart_cells, avatars),
                        strict=True,
                ),
                start=1,
        ):
                (folder / tile_filename(index, cell)).write_bytes(png)
        print(folder)  # noqa: T201
        return 0
