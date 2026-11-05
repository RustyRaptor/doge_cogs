"""``python -m doge_alignment`` on an exported chart."""

from pathlib import Path

import pytest
from alignment_helpers import chart_with

from doge_alignment.cli import main
from doge_alignment.core.storage import to_yaml


@pytest.fixture
def chart_file(tmp_path: Path) -> Path:
        """Write a small exported chart."""
        path = tmp_path / "chart.yaml"
        path.write_text(
                to_yaml(chart_with(("1", "Lawful Good"), ("2", "Chaotic Evil")))
        )
        return path


def test_text(chart_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Text mode prints the chart."""
        assert main(["text", str(chart_file)]) == 0
        assert capsys.readouterr().out.splitlines() == [
                "**Lawful Good**: user1",
                "**Chaotic Evil**: user2",
        ]


def test_bad_file_is_a_usage_error(tmp_path: Path) -> None:
        """Unreadable charts exit with argparse's usage status."""
        bad = tmp_path / "bad.yaml"
        bad.write_text("users: [")
        with pytest.raises(SystemExit) as exit_info:
                main(["text", str(bad)])
        assert exit_info.value.code == 2


def test_image_and_tiles(chart_file: Path, tmp_path: Path) -> None:
        """Image and tile modes write PNGs (needs ImageMagick)."""
        pytest.importorskip("wand.image", reason="needs ImageMagick")
        out = tmp_path / "chart.png"
        assert (
                main(["image", str(chart_file), "-o", str(out), "--no-avatars"])
                == 0
        )
        assert out.read_bytes().startswith(b"\x89PNG")
        tiles = tmp_path / "tiles"
        assert (
                main(
                        [
                                "tiles",
                                str(chart_file),
                                "-o",
                                str(tiles),
                                "--no-avatars",
                        ]
                )
                == 0
        )
        assert sorted(p.name for p in tiles.iterdir())[:2] == [
                "1-lg.png",
                "2-ng.png",
        ]
        assert len(list(tiles.iterdir())) == 9
