# doge_cogs

Red-DiscordBot cogs for dogebot.

## Cogs

| Cog | What it does |
|---|---|
| `doge_alignment` | A server's D&D alignment chart, with slash commands ([TODO](doge_alignment/TODO.md)) |
| `watch` | Preload a movie or episodes into the home lab's media cache before you watch |

## doge_alignment

Slash commands for a 3x3 D&D alignment chart per server. Enable them with `[p]slash enable <command>` and
`[p]slash sync`.

| Command | |
|---|---|
| `/alignment_show` | the chart, grouped by alignment |
| `/alignment_set` | put yourself on the chart |
| `/alignment_set_other [target]` | place someone who isn't on the chart yet; alignment admins can move anyone |
| `/alignment_remove` | take yourself off |
| `/alignment_add_admin`, `/alignment_remove_admin` | server owner, bot owner or alignment admins only |

Charts are stored in Red's Config. The rules live in `doge_alignment/chart.py` as pure, tested functions.
`render.py` holds image helpers for drawing the chart, which isn't wired up yet. Missing features are in
[doge_alignment/TODO.md](doge_alignment/TODO.md).

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
