"""Import the cogs' modules the way Red does.

Red appends Downloader's lib folder (where it pip-installs cog requirements)
to sys.path, *after* its own venv. scripts/test-container.sh installs the
requirements the same way and passes the folder in ``DOGE_COGS_LIB``, so a
requirement that only works with lib's copy of a package Red already has
fails here too.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if lib := os.environ.get("DOGE_COGS_LIB"):
        sys.path.append(lib)
