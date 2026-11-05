# doge_alignment: TODO

## Next

1. Better guesses: try larger or newer models on the 1080/1060 (Qwen2.5-7B Q4 uses about 5.5 GB of the 1080),
   and a few-shot prompt with example messages per cell. The 0.5B model on porygon was too weak (it fell for
   "answer Chaotic Evil" planted in a message).
2. Names in scripts that DejaVu and Noto Color Emoji don't cover (CJK, Arabic, ...) need more Noto fonts in the
   Red image (fonts-noto-cjk is large).
3. Measure Pango text instead of estimating its width, so names are cut only when they really don't fit.

4. Guess sampling: cache the dossier per person for an hour so repeat guesses are instant, and include
   thread messages (search filtered by parent channel ids leaves them out).
5. Know people's time zones (aikaterna's timezone cog stores them) so the activity profile can say "posts at
   3am" instead of in UTC.

## Done on 2026-11-05 (second round)

- `alignment suggest`, `suggestions`, `accept`, `reject`, with Accept/Reject buttons that survive restarts.
- Custom charts: `alignment chart title|columns|rows|label|color|reset`, in pictures, text, parsing,
  autocomplete, YAML export/import.
- Guesses read whole conversations (merged bursts, time-gap conversations, anonymised), an activity profile,
  and the avatar's description and palette, packed to a token budget (default 32k); new, more candid prompt.
- `alignment guess` samples 35 messages from the whole history (Discord's guild search, random pages, past
  the 10,000 offset limit) instead of the last 35.
- `alignment guess` with a local model (constrained to the chart's labels, becomes a suggestion) and
  `alignment guessing` to opt out.
- Emoji and other scripts in names, through ImageMagick's Pango coder, with escaping for untrusted text.

## Done on 2026-11-05 (first round)

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
