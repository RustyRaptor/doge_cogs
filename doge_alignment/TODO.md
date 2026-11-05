# doge_alignment: TODO

Missing features and known gaps, roughly in priority order. The cleanup of 2026-11-05 already fixed the bugs
listed at the end.

## Features

1. **Draw the chart as an image.** This was the original goal: a 3x3 grid with a labelled cell per
   alignment, filled with members' avatars. `render.py` has the building blocks (backgrounds, a grid layout,
   round or rounded avatars with borders), but nothing composes them or sends the result yet.
   - Decide on the image library first. Wand needs ImageMagick, which dogebot's Red container doesn't have.
     Either add `libmagickwand` to `dogebot/containers/red/Containerfile` and `Wand` to `info.json`, or
     port `render.py` to Pillow, which needs no system library.
   - Download avatars with Red's aiohttp session, cache them briefly, and fall back to the default avatar.
   - Show it from `/alignment_show`, perhaps with a `text` option for the current list.
2. **Use live names and avatars.** The chart stores each member's display name and avatar URL from when the
   alignment was set, and both go stale. Look the member up when showing the chart, and keep the stored
   copy only for members who have left.
3. **Group the slash commands** under one `/alignment` group (`set`, `show`, `remove`, `admin add`,
   `admin remove`, `admin list`) instead of six top-level commands. Merge `alignment_set` and
   `alignment_set_other`, since the second already covers the first.
4. **List the admins:** `/alignment admin list`. There's no way to see who they are today.
5. **Clear a chart** (alignment admins): wipe the chart, keeping the admins, with a confirmation button.
6. **Members who leave:** take them off the chart on `on_member_remove`, or show them greyed out.
7. **Recognise Red's admin and mod roles** (`bot.is_admin` and `bot.is_mod`) as alignment admins, so each
   server doesn't have to set up its own list.
8. **Text commands** (`[p]alignment …`) for servers that haven't enabled slash commands. Today the cog only
   works after `[p]slash enable` for each command and a `[p]slash sync`.
9. **Export and import** a chart as YAML, the format the cog used before it moved to Config, for backups and
   for moving between servers.
10. Ideas, not commitments:
    - let members suggest an alignment for someone else, applied when that person or an admin accepts;
    - custom charts per server (other axes or labels than lawful/chaotic and good/evil).

## Fixed in the 2026-11-05 cleanup

- Setting or removing an alignment wiped the server's admin list.
- Charts were YAML files inside the cog folder, which Red's Downloader replaces on every update. They now
  live in Red's Config, in the bot's database.
- The cog imported Wand (ImageMagick) at load time, so it couldn't load on dogebot.
- `info.json` pip-installed this whole repo, including `red-discordbot` and `discord`, into Red's lib folder.
- `/alignment_remove_admin` let the bot owner past one check and then refused them at the next.
- Added `red_delete_data_for_user`, so Red's data deletion also clears alignment charts and admin lists.
