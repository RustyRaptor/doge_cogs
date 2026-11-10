"""The core and the command line work without Discord or Red."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Every module of the core, found on disk so new ones are covered too, plus
# the package itself and the command line front end
MODULES = (
        "doge_alignment",
        "doge_alignment.cli",
        *sorted(
                f"doge_alignment.core.{p.stem}"
                for p in (ROOT / "doge_alignment/core").glob("*.py")
                if p.stem != "__init__"
        ),
)

CHECK = f"""
import importlib, os, sys
# Cog requirements live in Red's lib folder in the bot image (see conftest)
if os.environ.get("DOGE_COGS_LIB"):
    sys.path.append(os.environ["DOGE_COGS_LIB"])
for name in {MODULES!r}:
    importlib.import_module(name)
banned = ("discord", "redbot")
leaked = sorted(m for m in sys.modules if m.split(".")[0] in banned)
print(leaked)
sys.exit(1 if leaked else 0)
"""


def test_every_core_module_is_checked() -> None:
        """The list really is discovered: modules added later are in it."""
        assert {
                "doge_alignment.core.guess",
                "doge_alignment.core.render",
        } <= set(MODULES)


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
