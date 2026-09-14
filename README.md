# Playmaker

_One of SONAR's tabs (`~/Documents/lab/active/sonar/sonar/playmaker/`). Its own
git repo, nested here — versioned separately from SONAR, but not a separate
app. Ported from Sentinel's NFL agent early on (that agent was never a
standalone project) and split out into its own project doc set on
2026-09-14 — see the parent SONAR README.md for how the Playmaker tab fits
into the rest of the app._

## What it does

Sports prop-bet analysis: pick a sport and prop, enter the line, price and any
supporting stats, and an LLM read scores it against Playmaker's own odds/EV/
Kelly arithmetic. NFL is the only sport implemented today; the module is
generalised so a second sport is a registry entry (`Sport`/`PropType`) rather
than a rewrite.

The split mirrors the rest of SONAR: the arithmetic is deterministic and
testable (odds conversion, implied probability, expected value, Kelly), and
the LLM is asked only for the narrative read on top of numbers it did not
invent. Nothing here places a bet — it evaluates one.

## Under the hood

| Location | Role |
|---|---|
| `list_sports()` / `get_sport()` | The sport/prop-type registry. |
| `american_to_decimal()` / `implied_probability()` / `remove_vig()` | Odds conversion and vig removal. |
| `expected_value()` / `kelly_fraction()` / `edge_versus_market()` | The deterministic betting arithmetic. |
| `build_prompt()` | Builds the LLM prompt for the narrative read — numbers only, no invented data. |
| `parse_analysis()` / `Analysis` | Parses the model's response back into a structured result. |

## Requirements

A model provider for the narrative read (the arithmetic itself needs none).
Ported logic — the odds/EV/Kelly maths predates this repo's split and carries
its own test coverage from Sentinel's original NFL agent.
