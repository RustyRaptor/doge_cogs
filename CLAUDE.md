# doge_cogs: notes for AI agents

- Work test first. Read `TESTING.md` before changing code or tests, and use the superpowers
  `test-driven-development` skill: failing test, watch it fail, minimal code, whole suite green.
- Existing code without tests: add tests, then run a mutation check (break the code, see a test fail, undo).
- `mise run check` before pushing; it includes the run inside dogebot's Red image on yveltal.
- Never edit cogs inside the running bot. Push here; dogebot's dogesetup installs the new version.
