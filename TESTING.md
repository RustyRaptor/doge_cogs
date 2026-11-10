# Testing doge_cogs

Every cog here is built test first. This page is the rulebook for people and for AI agents (`CLAUDE.md` points
here). It follows the superpowers `test-driven-development` skill and its `writing-good-tests.md`.

## The loop

1. **Red.** Write one test for one behaviour. Run it and **watch it fail**, for the reason you expect (the
   feature is missing, not a typo or an import error).
2. **Green.** Write the least code that makes it pass. Run the whole suite (`mise run test`), not just the file.
3. **Refactor.** Clean up with the suite green. Don't add behaviour while refactoring.

No production code without a failing test first. Bug fixes start with a test that reproduces the bug.

When you add tests to code that already exists (it happens: `watch` got its cog tests after it shipped), they pass
straight away, which proves nothing. Do a **mutation check** instead: break the code on purpose (drop a branch,
change a constant, skip a side effect), confirm a test fails, then undo it. On 2026-11-10 the watch tests were
checked against six such mutations; each one failed at least one test.

## What a good test looks like

- **Name the break it catches.** Before writing the body, say which production change would make it fail. If you
  can't, test something observable instead.
- **Literal expectations.** Write the expected value by hand (tables of `input -> want` are the preferred
  shape). Never compute it with the code under test.
- **Behaviour, not text.** Don't grep source files or assert a constant's value; run the code and check what it
  does. `test_alignment_isolation.py` imports the modules in a fresh interpreter rather than reading them.
- **Fakes only at the edge.** Fake what's external or slow (Discord, the media-preload API, Ollama) at the lowest
  level you can, and keep everything above it real. Never assert on the fake itself.
- **Fakes mirror reality.** A fake API response has every field the real one has, in the real shape
  (camelCase JSON for media-preload, straight from `clients/media-preload.openapi.json`).
- **Test utilities live in `tests/`**, never as methods on production classes.

## Layers

| Layer | Where | What it proves |
|---|---|---|
| Core (pure functions) | `tests/test_alignment_*.py` for `doge_alignment/core`, `tests/test_watch_spec.py` | the logic, with no Discord or Red at all |
| Cog glue | `tests/test_alignment_cog.py`, `tests/test_watch_cog.py` | commands and Config against Red's test Config backend (`config` fixture from Red's pytest plugin) and fakes at the edge |
| Real HTTP | `tests/watch_helpers.py` | the watch cog's calls go over a real socket to a fake media-preload, through the generated client, httpx, httpcore and anyio |
| The bot's environment | `mise run test-container` | the same tests inside dogebot's Red image, with cog requirements installed **the way Red installs them** |
| Live | dogebot's `selftest` cog | the real bot, after every cog update: commands in `#bot-testing`, music at volume 0 in an empty voice channel; failures turn the Kuma monitor "dogebot self-test" red |

### Why test-container installs requirements like Red

Red's Downloader runs `pip install -U -t <lib> <requirements>` and appends `lib` to `sys.path` *after* its own
venv. A package Red already has (like `typing_extensions`) always comes from the venv, whatever pip put in lib.
On 2026-11-09 pip put anyio 4.16 in lib, which needs typing_extensions 4.16; Red has 4.13.2, so `d!watch` broke.

`scripts/test-container.sh` copies that exactly: requirements go to `/tmp/lib` with `pip -t` (under the image's
`PIP_CONSTRAINT`, which dogebot now sets to Red's own versions), and `tests/conftest.py` appends
`DOGE_COGS_LIB` to `sys.path`. Run against the old image
(`DOGEBOT_RED_IMAGE=localhost/dogebot-red:pre-constraint-2026-11-09`), 20 watch tests fail with the production
error; against the current image they pass.

## Before you push

```sh
mise run fmt
mise run check     # ruff, ty, pytest here, and pytest inside the bot's image
```

After the bot picks up the new version (dogesetup updates cogs daily, or on restart), the self-test runs on its
own. `d!selftest run` runs it on demand. Never fix a cog by editing it inside the bot's container: the next
update overwrites it, and until then the bot runs code that isn't in git (that happened on 2026-11-09).
