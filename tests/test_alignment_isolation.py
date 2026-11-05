"""The core and the command line work without Discord or Red."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MODULES = (
        "doge_alignment",
        "doge_alignment.cli",
        "doge_alignment.core.alignments",
        "doge_alignment.core.chart",
        "doge_alignment.core.fetch",
        "doge_alignment.core.layout",
        "doge_alignment.core.storage",
        "doge_alignment.core.view",
)

CHECK = f"""
import importlib, sys
for name in {MODULES!r}:
    importlib.import_module(name)
banned = ("discord", "redbot")
leaked = sorted(m for m in sys.modules if m.split(".")[0] in banned)
print(leaked)
sys.exit(1 if leaked else 0)
"""


def test_core_imports_neither_discord_nor_red() -> None:
        """Importing the core (and the CLI) never loads discord.py or Red."""
        result = subprocess.run(  # noqa: S603
                [sys.executable, "-c", CHECK],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
