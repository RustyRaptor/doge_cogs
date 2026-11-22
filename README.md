# doge_cogs

Red-DiscordBot cogs for dogebot.

## Cogs

| Cog | What it does |
|---|---|
| `doge_alignment` | A server's D&D alignment chart, drawn with members' avatars ([TODO](doge_alignment/TODO.md)) |
| `watch` | Preload a movie or episodes into the home lab's media cache before you watch |

## doge_alignment

A 3x3 D&D alignment chart per server, drawn with members' avatars cut into circles. Every command works as
`[p]alignment …` and, once enabled (`[p]slash enable alignment`, `[p]slash sync`), as `/alignment …`.

| Command | |
|---|---|
| `alignment [image\|gallery\|text]` (`/alignment show`) | the chart: one picture, nine pictures in Discord's 3x3 media gallery, or text |
| `alignment set <cell> [member]` | put yourself on the chart, or someone who isn't on it yet; admins can move anyone. Takes the server's labels, `CG`, `neutral`, … |
| `alignment suggest <member> <cell>` | suggest where someone belongs; they get Accept/Reject buttons (they still work after a restart) |
| `alignment suggestions [member]`, `accept <who>`, `reject <who>` | list and answer suggestions by command (`who` is a mention or `guess`) |
| `alignment guess [member]` | a local language model reads conversations from random points in the person's whole history (in channels you can both see), their activity pattern and their profile picture, and suggests a cell; takes 1.5-2 minutes |
| `alignment guessing <true\|false>` | allow or block guesses from your messages |
| `alignment baseline` | (chart admins) rebuild and show what guesses treat as normal on this server |
| `alignment remove [user]` | take yourself off; admins can take anyone off |
| `alignment prune` | admins: take off everyone who has left the server (until then they show greyed out) |
| `alignment clear` | admins: take everyone off, after a confirmation |
| `alignment style <image\|gallery\|text>` | admins: how the chart shows by default |
| `alignment chart` | the chart's title, axes and labels; admins change them with `chart title`, `chart columns <l> <m> <r>`, `chart rows <t> <m> <b>`, `chart label <cell> [text]`, `chart color <cell> [#hex]`, `chart reset` |
| `alignment export` / `alignment import <file>` | the chart (and custom labels) as YAML; admins can import, which replaces everyone |
| `alignment admin add\|remove\|list` | alignment admins, who may move anyone |

Admins are the alignment admins plus, always, the server owner, members with Administrator, Red's admin and
mod roles, and the bot owner. Names and avatars are looked up live; people who left keep the ones saved when
they were placed. Custom charts relabel the nine cells (columns × rows, or one cell at a time) without moving
anyone: entries keep their classic position.

Pictures need ImageMagick (`libmagickwand`) on the bot's host; without it, `alignment` falls back to text. With
ImageMagick's Pango coder (Debian: `libmagickcore-6.q16-6-extra`) text falls back per glyph to other fonts, so
names with emoji (with `fonts-noto-color-emoji`) and other scripts render. All text going to Pango is escaped
for both Pango markup and ImageMagick's own `%`/`\`/`@` escapes, since names are untrusted.

How a guess works (`core/sampling.py`, `gather.py`, `core/conversations.py`, `core/activity.py`,
`core/dossier.py`, `core/guess.py`):

1. Discord's guild message search finds the person's whole history in channels the asker and the bot can both
   read, and random pages of it are fetched (past the 10,000-result offset limit too). The page count follows the
   token budget: about 25 pages for 32k tokens.
2. Around a couple of their messages per page, the channel history gives the conversation they were in. A
   person's quick consecutive messages are merged into one turn; a 20-minute lull starts a new conversation;
   replies stay with what they answer. Others are anonymised as A, B, …, and anyone who blocked guesses is shown
   as hidden. Bots are left out.
3. An activity profile: posting rate between the sampled pages over the years and how steady it is, busiest
   hours, late-night and weekend shares, burstiness, typical run length, message length, and style (links,
   caps, emoji, questions, replies…).
4. The profile picture: its main colours (ImageMagick) and a short description from the model's vision; the
   nickname, custom status and roles. Discord doesn't give bots the "About me" bio (`/users/{id}/profile` answers
   403 to bots), so these are the nearest thing.
5. The server baseline (`core/baseline.py`): judged on their own, nearly everyone came out Chaotic Neutral,
   because Discord chat is jokey and irregular everywhere. So guesses also get what's normal on this server and
   where the person sits among its regulars. The baseline samples 24 random moments across the server's life in
   its public channels (one search each, plus the conversation around one message), keeps per-regular style
   numbers (anyone with 8+ sampled messages; no names or ids) and each author's share of messages, and asks the
   model for a 150-word summary of the culture from the anonymised conversations. It's cached in Config for a
   week; `alignment baseline` rebuilds it. The prompt then asks the model to judge relative to the server and
   spread people out. `ALIGNMENT_BASELINE=off` (or the `baseline` API token) turns it off.
   In a first experiment it helped only a little (Chaotic Neutral 9/10 → 8/10); see
   `scripts/experiments/README.md`.
6. Conversations are packed into the budget round-robin across pages (so the whole history is covered) and
   sent oldest first. Discord markup is tidied first (custom emoji keep their name, links their site, mentions
   become labels): raw Discord text ran at 1.6 characters per token, tidied at about 2.4.

The system prompt asks for a fair, candid judgement: patterns over single jokes, no default cell, activity and
avatar as hints only, nothing about protected traits, and a reason that's a friendly roast in good taste.
Times (busiest hours, late night, weekends, transcript headers) are in the person's own zone if they set one
with aikaterna's timezone cog (`[p]time me <zone>`; read from its Config, so it works even if that cog is
unloaded), and otherwise in Mountain Time (`America/Denver`, MST/MDT). `ALIGNMENT_TIMEZONE` or the `timezone` key
of the `alignment_model` API tokens changes the default; the server baseline uses the default.

`ALIGNMENT_CONTEXT_TOKENS` (default 32,000; Qwen3.5-9B allows 131,072 on yveltal) sets the budget. On yveltal's
GTX 1080 + 1060 the model reads about 400-600 tokens/s, so a 32k guess takes around 1.5-2 minutes in all; the
message shows progress meanwhile.

Guessing needs an OpenAI-compatible chat server (llama.cpp, Ollama, vLLM): `ALIGNMENT_MODEL_URL` (and
`ALIGNMENT_MODEL_NAME`, `ALIGNMENT_MODEL_KEY`) in the bot's environment, or `[p]set api alignment_model
url,<url> model,<name>`. The answer is constrained by a JSON schema to the chart's own nine labels and only
ever becomes a suggestion. dogebot uses Qwen3.5-9B on yveltal's Ollama (GTX 1080 + 1060), with thinking off (`ALIGNMENT_MODEL_REASONING=none`).

### Layout of the code

The cog is a thin Discord front end on `doge_alignment/core`, which imports neither discord.py nor Red (a test
checks), so a command-line tool or web app can reuse all of it:

| Module | |
|---|---|
| `core/alignments.py` | the nine alignments, their grid positions, parsing `CG`/`chaotic-good` |
| `core/chart.py` | the immutable `Chart` and pure operations: place, remove, clear, admins, permission rules |
| `core/storage.py` | to and from plain data (Red's Config) and YAML |
| `core/spec.py` | custom charts: title, axis names, per-cell labels and colours, parsing cells by label |
| `core/suggestions.py` | suggesting a cell for someone, accepting and rejecting |
| `core/sampling.py` | random pages from a whole message history, a few dozen search requests at most |
| `core/messages.py`, `core/conversations.py` | messages, merged turns, conversations, anonymised transcripts |
| `core/activity.py` | posting rate over time, rhythm, burstiness and style |
| `core/baseline.py` | the server baseline: regulars' numbers, comparison, the culture summary |
| `core/colors.py` | naming an avatar's palette |
| `core/dossier.py` | assembling what the model is told |
| `core/guess.py` | the prompt, packing to a token budget, the JSON schema, and the HTTP calls |
| `core/view.py` | the nine cells to show, with labels, live names and who has left |
| `core/layout.py` | pure geometry: where each label, avatar and name goes |
| `core/markup.py` | Pango markup, escaped for Pango and ImageMagick |
| `core/render.py` | paints a layout with ImageMagick (Wand); the only stateful drawing code |
| `core/fetch.py` | downloads avatars (HTTPS only, size-limited, image formats only) |
| `cog.py`, `ui.py`, `gather.py` | Red Config, permissions, commands, views, and fetching history for guesses |
| `cli.py` | the second front end, below |

```sh
python -m doge_alignment text chart.yaml               # from `alignment export`
python -m doge_alignment image chart.yaml -o chart.png
python -m doge_alignment tiles chart.yaml -o tiles/    # nine PNGs
```

Planned work is in [doge_alignment/TODO.md](doge_alignment/TODO.md).

## watch

`[p]watch <title>` asks the home lab's media-preload service to fetch a movie or episodes into the VPS media
cache, so they stream quickly from webdav, and keeps a progress message updated until it's done.

| Command | |
|---|---|
| `[p]watch The Matrix` | a movie |
| `[p]watch Dark Matter s2e8-10` | episodes 8 to 10 of season 2 (`s2e8`, `s2e1,3,5` work too) |
| `[p]watch Dark Matter s2` | a whole season |
| `[p]watch Dark Matter next 4` | your next 4 unwatched episodes (needs `link`) |
| `[p]watch Dark Matter` | a series without episodes: buttons for "next 3" or a season/episodes form |
| `[p]watch link <jellyfin name>` / `unlink` | link your Jellyfin user, for "next" |
| `[p]watch status [id]` | the latest requests, or one request's progress |
| `[p]watch cancel <id>` | cancel the files that haven't started (requester or moderators) |

When several titles match, a menu asks which one. The progress message has a Cancel button.

Configuration, read when each command runs:
- token: the `PRELOAD_BOT_TOKEN` environment variable, or `[p]set api media_preload token,<token>`;
- URL: `PRELOAD_API_URL`, or the `url` key of the same API tokens, defaulting to
  `http://host.containers.internal:8125`. That is the host as seen from a rootless podman container. Inside
  such a container the host's LAN address points back at the container itself.

### Request movies and TV downloads

`[p]watch` preloads existing library files. If nothing cacheable matches, it
points to `[p]request <title>` instead of downloading automatically.

| Command | Purpose |
|---|---|
| `[p]request Dune` | Choose Movie/TV, title/year, a TV season, then 1080p or 4K |
| `[p]request allow @person` | Bot owner: grant an individual download permission |
| `[p]request revoke @person` | Bot owner: remove that permission |

Only Red bot owners (including configured co-owners) may request by default.
Server administrators/moderators do not receive automatic access. Grants apply
across servers and are stored per Discord user; Red user-data deletion removes
them. Revocation is rechecked immediately before submitting to ARR.

Each menu belongs to its requester, includes Cancel, and expires after two
minutes. Even a single matching title requires explicit selection. Results show
title/year (up to24 matches); search again with a year to narrow them. The final
quality choice confirms the request. No ARR add/search happens before that step.
TV requests select **one regular season per Discord user per show**; specials
are excluded. Movie requests use released-only availability. Spanish libraries
and music acquisition are outside this command.

Title lookup uses Radarr/Sonarr's metadata catalogues, rather than exposing raw
release/torrent names. ARR subsequently searches its configured indexers under
the chosen quality/custom-format profile. Existing movies show status without
being added or searched again. TV choices update shared season retention as
described below. A successful
request means ARR accepted it, not that a matching release was found or imported.
The existing RDTC download/import/NFO/Jellyfin integration handles subsequent work.

Owner configuration uses Red's shared API-token stores `radarr` and `sonarr`:
each needs `url` (base URL, without `/api/v3`) and `api_key`. Supply keys through
a private administrative session, never a public Discord channel or git file.
The deployed instance uses English Sonarr on Dragapult8969 and Radarr7878.
Profiles are resolved by name: `Doge - 1080p default` / `Doge - 4K optional`.
New roots are `/arr/library/TV` and `/arr/library/Movies`. A missing selected
profile/root stops the request instead of silently selecting another one.
Raw ARR errors/headers are not sent to Discord. Catalogue lookups have a
60-second read budget and retry one read timeout after a short pause. Writes,
download requests, and deletions are never automatically retried. A timed-out
write tells the user to check its status before retrying; catalogue timeouts
are distinguished from an unreachable ARR service. No new service/API endpoint
is required.

### Shared TV season retention

TV requests now follow **TV → title → season → quality/confirmation**. The
season menu supports long-running shows through pagination. Each person has one
selected season per show, shared across Discord servers. The stored selection
uses the Discord user ID and TVDB ID, not a changeable user/title name.

- If Alice chooses season1 and Bob chooses season2, both seasons are retained.
- If Alice switches to season3, season1 is removed only if nobody else still
  selects it; Bob's season2 remains.
- If both choose season1 and Alice switches to season2, season1 stays for Bob.
- Requesting an already-downloaded/downloading season records the person's
  selection without starting a duplicate download. `watch` does not claim it.

**The user decides when to switch.** Jellyfin watched/completion status is not
consulted. The final confirmation lists the seasons that would be deleted.
To keep a season while somebody else switches, request that season once so the
bot records your selection. Existing partial shows have no historical per-user
claims; their unclaimed seasons can be removed when an authorized user confirms
a new season request. Deployment does not perform a bulk cleanup.

**Complete existing shows are exempt.** The first adoption snapshot records
Sonarr shows with every aired regular episode present. That protected TVDB list
is frozen; completing a new on-demand show later does not automatically turn it
into a permanent archive. Protected shows keep all their existing files and
monitoring. Untracked legacy files outside Sonarr are never deleted by this code.

For other shows, Sonarr monitors only the union of users' requested seasons and
sets `monitorNewItems=none`; it cannot automatically fetch later seasons. Future
episodes of an explicitly selected ongoing season remain monitored. TV quality
profiles apply to the entire show, which the confirmation states.

A switch first unmonitors released seasons, preserving other users' selections.
The actor's new season is activated only after cleanup succeeds. Old unclaimed
downloads are cancelled without blocklisting, and episode files are deleted
through Sonarr's scoped API using freshly resolved file IDs. Multi-season packs
needed by another person are not cancelled; ambiguous cases stop for review.
Imports racing a switch are re-read before cleanup/search. Newly added shows use
post-add `monitor=skip` so Sonarr's initial scan cannot overwrite the selection.
Only `SeasonSearch` for the chosen season is submitted, never a whole-show search.

Permission is rechecked before every Sonarr mutation. Expanded/stale deletion
previews stop rather than silently deleting additional seasons. If cleanup fails,
the saved choice remains for retry, the new season stays paused, and no new
season search starts. If the bot restarts mid-switch, repeat the same request to
reconcile it. File removal follows Sonarr's normal deletion settings; the current
instance has no recycle bin configured. A missing/revoked download grant prevents
further mutations. Revoking access does not automatically erase retained choices.

Choices live in Red Config `Watch` user field `tv_seasons`; the archive baseline
is global `season_retention_baseline`. Red user-data deletion clears that user's
choices without deleting shared media immediately. No periodic watched-status
job or extra service/API endpoint is involved.

### In-progress download status

When `watch` cannot find playable library media, it checks existing English
Sonarr/Radarr entries before suggesting a new request. It also checks when a
preload fails (for example, a series is in Jellyfin but its requested episode
has not finished downloading). This is read-only and works for users without
download permission. Existing playable media keeps the normal preload behavior.

`request` shows an existing movie's status after title selection, skipping the
quality step. For TV it records a season choice, then includes download progress
in the response. `watch` remains a read-only progress/preload command; use it
when you want status without changing your season choice. Download permissions
remain owner-controlled.

Status is a snapshot of ARR's queue, with percentage and ETA when available.
Paused, queued, needs-attention and downloaded/importing states are shown
separately. Unknown sizes produce **progress unavailable** rather than an
invented percentage. A managed title with no active queue entry is described
as waiting, not downloading. Movies already imported are reported as downloaded;
a missing Jellyfin result may need its library scan to finish.

Series progress is shown per transfer; multiple episode rows for one season
pack count as one download. If multiple requested titles match, a menu lets the
user choose title/type/year. Replies show at most five transfers plus the
remaining count. Raw release names, private download IDs, filesystem paths and
upstream error messages are excluded. A failed queue API is reported as
**temporarily unavailable**.

This adds no background message polling and does not modify ARR Discord webhook
notifications. Run the command again for an updated snapshot; existing channel
notifications continue to handle completion events.

### The client library

`clients/media-preload-client` is generated from media-preload's OpenAPI spec with
[openapi-python-client](https://github.com/openapi-generators/openapi-python-client), so don't edit it by hand.
The cog's `info.json` installs it from this repo with pip. To regenerate it after the API changes:

```sh
mise run generate-client   # fetches the live spec into clients/media-preload.openapi.json and regenerates
mise run check             # ruff, ty and pytest, here and inside the bot's image
```

## Development

Everything here is built test first: see [TESTING.md](TESTING.md) for the loop, what makes a good test, and
the test layers (core, cog glue, real HTTP, the bot's image, and the live self-test).

mise installs uv, and uv installs Python 3.11 (what Red 3.5 needs) and the dependencies, including the
generated client in editable mode. ruff (the lptn settings), ty and pytest cover both cogs. Each cog folder is
self-contained, so Red's Downloader installs it without installing this repo as a package.

```sh
mise install && mise run sync
mise run fmt && mise run check
```

`mise run check` includes `mise run test-container`, which copies this checkout to yveltal and runs the tests
inside dogebot's Red image (`localhost/dogebot-red:latest`), with the bot's Python, ImageMagick 6, fonts and
the exact requirements from each `info.json`, installed the way Red's Downloader installs them. The dev machine has ImageMagick 7, and the two differ (an
operator name broke pictures once), so run it before pushing anything that draws. Set `DOGEBOT_HOST` or
`DOGEBOT_RED_IMAGE` to test elsewhere; extra arguments go to pytest. Keep `wand` in `pyproject.toml` equal to
`doge_alignment/info.json`. ImageMagick itself is pinned in dogebot's `containers/red/Containerfile`.
