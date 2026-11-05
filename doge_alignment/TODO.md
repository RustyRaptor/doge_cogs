# doge_alignment: TODO

## Next

1. **Guess alignments from messages (bonus).** Pass someone's most recent 35 messages to a locally hosted
   model (an LLM, or a smaller text classifier) and suggest an alignment for them.
   - Core: a pure `guess` module that turns messages into a prompt and parses the model's answer with
     `parse_alignment`, plus a model client behind a small protocol, so the cog, the command line or a web
     app can use any backend (llama.cpp's OpenAI-compatible API on porygon is the obvious first one).
   - Cog: `alignment guess [member]`, reading the history only in channels the bot and the invoker can both
     see. The result is a suggestion with an "Apply" button, which follows the usual `may_set` rules.
   - Privacy: say in the data statement that messages are sent to the model and not stored. Probably let
     members opt out.
2. Let members suggest an alignment for someone else, applied when that person or an alignment admin
   accepts.
3. Custom charts per server: other axes or labels than lawful/chaotic and good/evil. `layout.Theme` already
   takes the cell colours; labels and axes would need to come from the chart.
4. Emoji and non-Latin names render as boxes with DejaVu Sans. Fall back to Noto fonts (Noto Color Emoji
   needs ImageMagick built with color-font support).

## Done on 2026-11-05

- The chart is drawn as an image: one 3x3 picture (`alignment image`), or nine pictures in Discord's media
  gallery (`alignment gallery`), which Discord lays out as three rows of three. Avatars are cut into circles.
- Names and avatars are looked up live. People who left are greyed out and struck through, and
  `alignment prune` takes them off.
- One `alignment` command group, both as text commands and as `/alignment` slash commands. `set` covers
  setting your own alignment and someone else's.
- `alignment admin list`, `alignment clear` (with a confirmation button), `alignment style`.
- Red's admin and mod roles, server Administrators and the server owner always count as alignment admins.
- YAML export and import, in the format the old cog used.
- The logic moved into `core/`, which doesn't import Discord or Red, with `python -m doge_alignment` as a
  second front end.

## Fixed in the 2026-11-05 cleanup

- Setting or removing an alignment wiped the server's admin list.
- Charts were YAML files inside the cog folder, which Red's Downloader replaces on every update. They now
  live in Red's Config, in the bot's database.
- The cog imported Wand (ImageMagick) at load time, so it couldn't load on dogebot.
- `info.json` pip-installed this whole repo, including `red-discordbot` and `discord`, into Red's lib folder.
- `/alignment_remove_admin` let the bot owner past one check and then refused them at the next.
- Added `red_delete_data_for_user`, so Red's data deletion also clears alignment charts and admin lists.
