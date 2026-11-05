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

# The cogs' own requirements (what Red's Downloader installs), plus the
# locked test tools. The media-preload client comes from this checkout
# (editable, in the dev group) rather than from GitHub, so it's the code
# under test.
cog_reqs=$(python3 -c 'import json, sys
for f in sys.argv[1:]:
    for req in json.load(open(f))["requirements"]:
        if not req.startswith("media-preload-client"):
            print(req)' */info.json)
test_reqs=$(uv export --frozen --only-group dev --no-hashes --no-emit-project \
        | grep -vE '^(ruff|ty)==|^#|^ *#')

dir=$(ssh "$host" mktemp -d)
trap 'ssh "$host" rm -rf "$dir"' EXIT
git ls-files -z --cached --others --exclude-standard \
        | tar --null -T - -czf - \
        | ssh "$host" tar xzf - -C "$dir"
printf '%s\n' "$cog_reqs" | ssh "$host" "cat > $dir/.cog-requirements.txt"
printf '%s\n' "$test_reqs" | ssh "$host" "cat > $dir/.test-requirements.txt"

# shellcheck disable=SC2029 # $dir and $image expand here on purpose
ssh "$host" "podman run --rm --entrypoint sh -v $dir:/src:Z -w /src \
        -e PYTHONDONTWRITEBYTECODE=1 $image -c '
        set -e
        uv pip install --quiet --python /app/.venv/bin/python \
                -r .cog-requirements.txt -r .test-requirements.txt
        python -c \"from wand.version import MAGICK_VERSION; print(MAGICK_VERSION)\"
        python -m pytest -q -p no:cacheprovider $*
'"
