#!/usr/bin/env bash
# Run the test suite inside dogebot's Red image, with the Python, ImageMagick,
# fonts and Wand the live bot uses, so differences from the dev machine (such
# as ImageMagick 6 vs 7) fail here instead of in Discord.
#
#   DOGEBOT_HOST       ssh host with the image (default yveltal_local)
#   DOGEBOT_RED_IMAGE  image to test in (default localhost/dogebot-red:latest)
#   extra arguments go to pytest
set -euo pipefail
cd "$(dirname "$0")/.."

host=${DOGEBOT_HOST:-yveltal_local}
image=${DOGEBOT_RED_IMAGE:-localhost/dogebot-red:latest}

# The cogs' requirements are installed the way Red's Downloader installs them:
# `pip install -U -t <lib>` (honouring the image's PIP_CONSTRAINT), with lib
# appended to sys.path after Red's venv (DOGE_COGS_LIB, see tests/conftest.py).
# The media-preload client comes from this checkout instead of GitHub, so it's
# the code under test. Only the test runner goes into the venv, constrained to
# Red's versions so it can't upgrade anything Red ships.
cog_reqs=$(python3 -c 'import json, sys
for f in sys.argv[1:]:
    for req in json.load(open(f))["requirements"]:
        if not req.startswith("media-preload-client"):
            print(req)' */info.json)
test_reqs=$(uv export --frozen --only-group dev --no-hashes --no-emit-project \
        | grep -E '^(pytest|pytest-asyncio)==')

dir=$(ssh "$host" mktemp -d)
trap 'ssh "$host" rm -rf "$dir"' EXIT
git ls-files -z --cached --others --exclude-standard \
        | tar --null -T - -czf - \
        | ssh "$host" tar xzf - -C "$dir"
printf '%s\n' "$cog_reqs" | ssh "$host" "cat > $dir/.cog-requirements.txt"
printf '%s\n' "$test_reqs" | ssh "$host" "cat > $dir/.test-requirements.txt"

# shellcheck disable=SC2029 # $dir and $image expand here on purpose
ssh "$host" "podman run --rm --entrypoint sh -v $dir:/src:Z -w /src \
        -e PYTHONDONTWRITEBYTECODE=1 -e DOGE_COGS_LIB=/tmp/lib $image -c '
        set -e
        constraint=\${PIP_CONSTRAINT:+--constraint \$PIP_CONSTRAINT}
        uv pip install --quiet --python /app/.venv/bin/python \$constraint \\
                -r .test-requirements.txt
        python -m pip install --quiet --disable-pip-version-check -U \\
                -t /tmp/lib -r .cog-requirements.txt ./clients/media-preload-client
        echo PIP_CONSTRAINT=\${PIP_CONSTRAINT:-none}
        ls /tmp/lib | grep -E \"^(anyio|typing_extensions|Wand|wand)-.*dist-info\"
        python -m pytest -q -p no:cacheprovider $*
'"
