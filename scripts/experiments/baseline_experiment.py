"""A/B/C experiment: does a server baseline spread guesses out?

Run inside the bot container (see scripts/experiments/README.md).

REST-only, run inside the dogebot-red container. Prints structure and
alignment labels only: no names, ids or message content.

  A  current prompt (no baseline, no profile, Mountain Time)
  B  + server baseline, comparison with regulars, profile (nick, roles),
       the member's zone from the timezone cog
  C  B + "notes" (reason about each axis before picking)

Usage: python baseline_experiment.py [members=8] [budget=32000] [guild_index]
"""

import asyncio
import math
import os
import random
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import aiohttp

sys.path[:0] = ["/tmp/align", "/data/cogs/Downloader/lib"]

from redbot.core import Config, data_manager  # noqa: E402
from redbot.core import _drivers as drivers  # noqa: E402

from doge_alignment.core import render  # noqa: E402
from doge_alignment.core.alignments import abbreviation  # noqa: E402
from doge_alignment.core.baseline import (  # noqa: E402
        build_baseline,
        culture_evidence,
        describe_baseline,
        summarise_culture,
        time_points,
)
from doge_alignment.core.colors import describe_palette  # noqa: E402
from doge_alignment.core.dossier import build_dossier, describe_profile  # noqa: E402
from doge_alignment.core.guess import (  # noqa: E402
        ModelConfig,
        ModelError,
        ask,
        build_prompt,
        describe_avatar,
        estimate_tokens,
        pages_for,
)
from doge_alignment.core.messages import snowflake_at  # noqa: E402
from doge_alignment.core.sampling import PAGE_SIZE, sample_pages  # noqa: E402
from doge_alignment.core.storage import from_data as chart_from_data  # noqa: E402
from doge_alignment.gather import (  # noqa: E402
        anchor_ids,
        fresh,
        message_from_json,
        search_hits,
        total_from_search,
)

API = "https://discord.com/api/v10"
H = {"Authorization": "Bot " + os.environ["RED_TOKEN"]}
MEMBERS = int(sys.argv[1]) if len(sys.argv) > 1 else 8
BUDGET = int(sys.argv[2]) if len(sys.argv) > 2 else 32000
GUILD_INDEX = int(sys.argv[3]) if len(sys.argv) > 3 else None
DENVER = ZoneInfo("America/Denver")
VIEW, HISTORY = 1 << 10, 1 << 16


def log(*a):
        print(*a, flush=True)


def entropy(counts: Counter) -> float:
        n = sum(counts.values())
        return -sum(c / n * math.log2(c / n) for c in counts.values() if c)


async def red_config():
        data_manager.load_basic_configuration("dogebot")
        cls = drivers.get_driver_class()
        await cls.initialize(**data_manager.storage_details())
        align = Config.get_conf(None, identifier=0xA119E5C4A7, cog_name="AlignmentCog")
        zones = Config.get_conf(None, identifier=278049241001, cog_name="Timezone")
        return align, zones


async def main():
        t0 = time.time()
        align, zones = await red_config()
        guilds_data = await align.all_guilds()
        users_data = await align.all_users()
        blocked = frozenset(str(u) for u, d in users_data.items() if d.get("guess_blocked"))
        zone_data = await zones.all_users()
        cfg = ModelConfig(
                "http://host.containers.internal:11434",
                "qwen3.5-9b-gpu",
                extra={"reasoning_effort": "none"},
                context_tokens=BUDGET,
        )
        async with aiohttp.ClientSession(headers=H) as s, aiohttp.ClientSession() as plain:

                async def get(path, params=None):
                        for _ in range(10):
                                async with s.get(API + path, params=params) as r:
                                        if r.status == 429:
                                                await asyncio.sleep(float((await r.json()).get("retry_after", 1)) + 0.2)
                                                continue
                                        body = await r.json()
                                        if isinstance(body, dict) and body.get("code") == 110000:
                                                await asyncio.sleep(3)
                                                continue
                                        return body
                        return {}

                guilds = await get("/users/@me/guilds")
                if GUILD_INDEX is None:
                        # the guild with the most people on its chart
                        ranked = sorted(
                                range(len(guilds)),
                                key=lambda i: -len((guilds_data.get(int(guilds[i]["id"])) or {}).get("users", {})),
                        )
                        gi = ranked[0]
                else:
                        gi = GUILD_INDEX
                guild = guilds[gi]
                gid = guild["id"]
                chart = chart_from_data(guilds_data.get(int(gid)) or {})
                spec = chart.spec
                chans = await get(f"/guilds/{gid}/channels")
                roles = {r["id"]: r for r in await get(f"/guilds/{gid}/roles")}
                everyone = int(roles[gid]["permissions"])
                names = {c["id"]: c["name"] for c in chans}

                def public(c):
                        perms = everyone
                        for o in c.get("permission_overwrites", []):
                                if o["id"] == gid:
                                        perms = (perms & ~int(o["deny"])) | int(o["allow"])
                        return c["type"] in (0, 5) and perms & VIEW and perms & HISTORY

                public_ids = [c["id"] for c in chans if public(c)][:500]
                chan_params = [("channel_id", c) for c in public_ids]
                log(f"guild #{gi}: {len(public_ids)} public text channels, {len(chart.users)} on its chart")

                async def search(params):
                        return await get(f"/guilds/{gid}/messages/search", params)

                async def around(m):
                        body = await get(f"/channels/{m.channel_id}/messages", [("around", m.id), ("limit", "60")])
                        return tuple(message_from_json(x, names) for x in body) if isinstance(body, list) else ()

                # 1. Baseline -------------------------------------------------
                base = [("author_type", "user"), *chan_params]
                server_total = total_from_search(await search([*base, ("limit", "1")]))
                rng = random.Random(7)
                created = datetime.fromtimestamp(((int(gid) >> 22) + 1420070400000) / 1000, tz=UTC)
                seen: set[str] = set()
                groups = []
                for moment in time_points(created, datetime.now(UTC), 24, rng):
                        body = await search([*base, ("limit", str(PAGE_SIZE)), ("sort_by", "timestamp"), ("sort_order", "desc"), ("max_id", str(snowflake_at(moment)))])
                        hits = fresh(search_hits(body, names), seen)
                        if hits:
                                groups.append((*hits, *fresh(await around(rng.choice(hits)), seen)))
                messages = [m for g in groups for m in g]
                t1 = time.time()
                culture = await summarise_culture(plain, cfg, culture_evidence(groups, blocked, DENVER))
                baseline = build_baseline(messages, server_total, built=datetime.now(UTC), tz=DENVER, culture=culture)
                log(f"baseline: {server_total:,} server messages, {len(groups)} sample groups, "
                    f"{baseline.sampled} sampled from {baseline.people} people, {len(baseline.regulars)} regulars; "
                    f"sampling {t1 - t0:.0f}s, culture {time.time() - t1:.0f}s ({len(culture.split())} words)")
                log("BASELINE TEXT (abstracted, no names):\n" + describe_baseline(baseline))

                # 2. Members --------------------------------------------------
                sample_counts = Counter(m.author_id for m in messages if not m.bot)
                placed = [u for u in chart.users if u not in blocked]
                busy = [u for u, _ in sample_counts.most_common() if u not in blocked and u not in placed]
                candidates = (placed + busy)[: MEMBERS * 2]
                results = []
                for uid in candidates:
                        if len(results) >= MEMBERS:
                                break
                        member = await get(f"/guilds/{gid}/members/{uid}")
                        user = member.get("user") if isinstance(member, dict) else None
                        if not user or user.get("bot"):
                                continue
                        name = member.get("nick") or user.get("global_name") or user["username"]
                        author = [("author_id", uid), *chan_params]
                        total = total_from_search(await search([*author, ("limit", "1")]))
                        if total < 50:
                                continue

                        async def fetch(max_id, offset, author=author):
                                p = [*author, ("limit", str(PAGE_SIZE)), ("sort_by", "timestamp"), ("sort_order", "desc"), ("offset", str(offset))]
                                if max_id:
                                        p.append(("max_id", max_id))
                                return search_hits(await search(p), names)

                        t2 = time.time()
                        prng = random.Random(int(uid) % 1000)
                        pages = await sample_pages(fetch, total, pages_for(BUDGET), prng)
                        contexts = {}
                        for i, page in enumerate(pages):
                                msgs = []
                                for a in anchor_ids(page, uid, prng):
                                        msgs += await around(a)
                                contexts[i] = tuple(msgs)
                        fetched = time.time() - t2
                        png = b""
                        if user.get("avatar"):
                                async with plain.get(f"https://cdn.discordapp.com/avatars/{uid}/{user['avatar']}.png?size=256") as r:
                                        png = await r.read()
                        desc = await describe_avatar(plain, cfg, png) if png else ""
                        pal = describe_palette(render.palette(png)) if png else ""
                        member_roles = sorted(
                                (roles[r] for r in member.get("roles", []) if r in roles and not roles[r].get("managed")),
                                key=lambda r: -r["position"],
                        )
                        profile = describe_profile(
                                username=user["username"], display_name=name, roles=[r["name"] for r in member_roles]
                        )
                        zone_name = (zone_data.get(int(uid)) or {}).get("usertime")
                        tz = ZoneInfo(zone_name) if zone_name else DENVER
                        common = dict(name=name, target_id=uid, pages=pages, contexts=contexts, total=total,
                                      joined=datetime.fromisoformat(member["joined_at"]).date() if member.get("joined_at") else None,
                                      avatar=desc, palette=pal, hidden=blocked)
                        plain_d = build_dossier(**common, tz=DENVER)
                        rich_d = build_dossier(**common, tz=tz, baseline=baseline, profile=profile)
                        row = {"truth": chart.users[uid].alignment if uid in chart.users else None,
                               "total": total, "zone": "own" if zone_name else "MT"}
                        for variant, dossier, notes in (("A", plain_d, False), ("B", rich_d, False), ("C", rich_d, True)):
                                prompt = build_prompt(dossier, spec, BUDGET, notes=notes)
                                t3 = time.time()
                                try:
                                        g = await ask(plain, cfg, prompt, spec, notes=notes)
                                        row[variant] = g.alignment
                                except ModelError as e:
                                        row[variant] = f"error: {e}"
                                row[variant + "s"] = time.time() - t3
                                row[variant + "tok"] = sum(estimate_tokens(m["content"]) for m in prompt)
                        results.append(row)
                        p = f"P{len(results)}"
                        short = lambda a: abbreviation(a) if a and not a.startswith("error") else (a or "--")  # noqa: E731
                        log(f"{p}: msgs {total:>6,} zone {row['zone']:>3} chart {short(row['truth']):>2} | "
                            f"A {short(row['A']):>2}  B {short(row['B']):>2}  C {short(row['C']):>2} | "
                            f"fetch {fetched:.0f}s, ask {row['As']:.0f}/{row['Bs']:.0f}/{row['Cs']:.0f}s, "
                            f"~{row['Btok']:,} tok (A ~{row['Atok']:,})")

                # 3. Summary --------------------------------------------------
                log("\nSUMMARY")
                for v in "ABC":
                        counts = Counter(r[v] for r in results if not str(r[v]).startswith("error"))
                        cn = counts.get("Chaotic Neutral", 0)
                        hit = [r for r in results if r["truth"]]
                        exact = sum(1 for r in hit if r[v] == r["truth"])
                        log(f"{v}: {dict((abbreviation(k), c) for k, c in counts.most_common())}  "
                            f"distinct {len(counts)}, entropy {entropy(counts):.2f} bits, CN {cn}/{len(results)}, "
                            f"matches chart {exact}/{len(hit)}")
                log(f"total {time.time() - t0:.0f}s")


asyncio.run(main())
