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
4. The profile picture: its main colours (ImageMagick) and a short description from the model's vision.
5. Conversations are packed into the budget round-robin across pages (so the whole history is covered) and
   sent oldest first. Discord markup is tidied first (custom emoji keep their name, links their site, mentions
   become labels): raw Discord text ran at 1.6 characters per token, tidied at about 2.4.

The system prompt asks for a fair, candid judgement: patterns over single jokes, no default cell, activity and
avatar as hints only, nothing about protected traits, and a reason that's a friendly roast in good taste.
Times (busiest hours, late night, weekends, transcript headers) are in Mountain Time (`America/Denver`, MST/MDT)
for everyone for now; `ALIGNMENT_TIMEZONE` or the `timezone` key of the `alignment_model` API tokens changes it.
Per-person zones from the timezone cog are on the TODO list.

`ALIGNMENT_CONTEXT_TOKENS` (default 32,000; Qwen3.5-9B allows 131,072 on yveltal) sets the budget. On yveltal's
GTX 1080 + 1060 the model reads about 400-600 tokens/s, so a 32k guess takes around 1.5-2 minutes in all; the
message shows progress meanwhile.

Guessing needs an OpenAI-compatible chat server (llama.cpp, Ollama, vLLM): `ALIGNMENT_MODEL_URL` (and
`ALIGNMENT_MODEL_NAME`, `ALIGNMENT_MODEL_KEY`) in the bot's environment, or `[p]set api alignment_model
url,<url> model,<name>`. The answer is constrained by a JSON schema to the chart's own nine labels and only
ever becomes a suggestion. dogebot runs Qwen2.5-7B-Instruct on a GTX 1080 for it.

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

### The client library

`clients/media-preload-client` is generated from media-preload's OpenAPI spec with
[openapi-python-client](https://github.com/openapi-generators/openapi-python-client), so don't edit it by hand.
The cog's `info.json` installs it from this repo with pip. To regenerate it after the API changes:

```sh
mise run generate-client   # fetches the live spec into clients/media-preload.openapi.json and regenerates
mise run check             # ruff, ty and pytest for the watch cog
```

## Development

mise installs uv, and uv installs Python 3.11 (what Red 3.5 needs) and the dependencies, including the
generated client in editable mode. ruff (the lptn settings), ty and pytest cover both cogs. Each cog folder is
self-contained, so Red's Downloader installs it without installing this repo as a package.

```sh
mise install && mise run sync
mise run fmt && mise run check
```

`mise run check` includes `mise run test-container`, which copies this checkout to yveltal and runs the tests
inside dogebot's Red image (`localhost/dogebot-red:latest`), with the bot's Python, ImageMagick 6, fonts and
the exact requirements from each `info.json`. The dev machine has ImageMagick 7, and the two differ (an
operator name broke pictures once), so run it before pushing anything that draws. Set `DOGEBOT_HOST` or
`DOGEBOT_RED_IMAGE` to test elsewhere; extra arguments go to pytest. Keep `wand` in `pyproject.toml` equal to
`doge_alignment/info.json`. ImageMagick itself is pinned in dogebot's `containers/red/Containerfile`.
