# Guess experiments

REST-only harnesses that run the guess pipeline on a real server, inside the
`dogebot-red` container (they need `RED_TOKEN`, Red's Config and the model on
yveltal). They print counts and alignment labels only: no names, ids or
message content. People who blocked guesses are skipped.

```sh
tar czf - --exclude=__pycache__ doge_alignment -C scripts/experiments . \
  | ssh yveltal_local 'rm -rf /tmp/align2 && mkdir /tmp/align2 && tar xzf - -C /tmp/align2 &&
      podman exec dogebot-red rm -rf /tmp/align && podman cp /tmp/align2 dogebot-red:/tmp/align &&
      podman exec dogebot-red python /tmp/align/baseline_experiment.py 10 32000 6'
```

Arguments: members, token budget, guild index (default: the guild with the
most people on its chart). Each member takes 1-4 minutes.

## Results, 2026-11-06 (Qwen3.5-9B, thinking off, 32k, one server: 14k messages, 47 people, 10 members)

`baseline_experiment.py`: the same fetched history asked three ways.

| Variant | Cells used | Chaotic Neutral | Matches chart (7) |
|---|---|---|---|
| A: the old prompt | 2 | 9/10 | 5/7 |
| B: + server baseline, comparison with regulars, profile, own time zone | 3 | 8/10 | 5/7 |
| C: B + "notes" before the answer | 3 | 8/10 | 5/7 |

`score_experiment.py`: 0-100 scores per axis (50 = a typical regular here),
asked twice with the same prompt (D, D2) and once without the baseline (E),
then cells from terciles among the members.

| | order | ethics |
|---|---|---|
| range (D) | 58-88 | 12-85 |
| mean change on an identical re-ask | 9 | 27 |
| rank agreement D vs D2 (Spearman) | -0.33 | 0.24 |
| rank agreement D vs E | 0.12 | 0.05 |

The scores don't repeat: ethics flips between about 12 and 85 for the same
person on a re-ask. Ranking them would give a spread of random cells, so that
approach was dropped. The baseline stays in (a small gain, and it
costs one extra minute a week per server). The bottleneck looks like the 9B
model telling these people apart at all, not the framing.
