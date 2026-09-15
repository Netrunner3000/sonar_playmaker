# Playmaker

_One of SONAR's tabs (`~/Documents/lab/active/sonar/sonar/playmaker/`). Its own
git repo, nested here — versioned separately from SONAR, but not a separate
app. Ported from Sentinel's NFL agent early on (that agent was never a
standalone project) and split out into its own project doc set on
2026-09-14 — see the parent SONAR README.md for how the Playmaker tab fits
into the rest of the app._

## What it does

Sports prop pricing. Pick a sport and prop, paste what each book is offering,
and it removes the margin, finds which book is out of line with its peers, and
sizes the result.

**Nine sports**: NFL, NBA, MLB, NHL, Premier League, Champions League, NCAA
basketball, UFC and ATP tennis — 81 prop types between them. Adding the eight
after NFL needed no new arithmetic, which is the point of the shape: `devig.py`
and `staking.py` are pure odds maths and know nothing about what they price. A
three-way soccer market works because `devig` takes N outcomes, not because
soccer was special-cased. What a `Sport` entry supplies is the prop vocabulary,
the context a reader should give, the league path for the coming results feed,
and how many sides the headline market has.

The split mirrors the rest of SONAR: the arithmetic is deterministic and
testable (odds conversion, implied probability, expected value, Kelly), and
the LLM is asked only for the narrative read on top of numbers it did not
invent. Nothing here places a bet — it evaluates one.

## Under the hood

The registry, odds arithmetic, prompt and parsing live in `__init__.py`; the
devigging and staking added in v2 are their own modules.

| Location | Role |
|---|---|
| `Sport` / `PropType` | Frozen dataclasses describing a sport's prop types and the game-context hint shown on its form. |
| `Sport.espn_path` / `Sport.outcomes` | League path for the results feed, and whether the headline market prices a draw. |
| `list_sports()` / `get_sport()` | The nine-sport registry. |
| `american_to_decimal()` / `implied_probability()` | Odds conversion. |
| `remove_vig()` | The proportional method, kept for reference. `devig.py` is what you want. |
| `devig.py` | Three devig methods (multiplicative, Clarke power, Shin), cross-book consensus, the outlier screen, and the price parser. |
| `staking.py` | `Estimate` (probability + interval + source), Kelly sized at the interval's low end, and the rule that a narrative source cannot size a bet. |
| `expected_value()` / `kelly_fraction()` / `edge_versus_market()` | The deterministic betting arithmetic. |
| `SYSTEM_PROMPT` / `build_prompt()` | The LLM system prompt and the per-prop user message it's paired with — numbers only, no invented data. |
| `parse_analysis()` / `Analysis` | Parses the model's response back into a structured result. |

## Where the numbers come from

Read `MODELS.md` before changing anything here. It surveys the sports models
with real track records — Elo with margin-of-victory, Dixon-Coles, Pythagorean
expectation, distributional prop models — and reaches a conclusion that sets the
build order: the accuracy ceiling is low, the sharp closing line sits at it, and
the return is therefore in devig quality and cross-book price shopping rather
than in a cleverer model. `TODO.md` carries the staged plan.

**A language model's percentage no longer sizes anything.** It used to: the UI
scraped a figure out of prose with a regex and computed a ¼-Kelly stake from it.
`staking.Estimate` now carries a source and an interval with every probability,
and `staking.kelly()` returns zero for a narrative source no matter how
confident the prose sounded. The LLM read is shown beside the numbers as
commentary.

**Both sides of a market are required.** A margin is how far a market's prices
sum past certainty, so a single price cannot reveal one — `-110` alone is
equally consistent with a juiced coin flip and with a genuine 52.4% favourite at
no margin. The form asked for one price for years, which is why nothing
downstream of it could have been right (`MODELS.md` §7a).

## The cross-book screen

The one approach here with a published track record. Kaunitz, Zhong & Kreiner
(2017) did not forecast anything — they devigged many books' prices for the same
market, took the consensus, and bet only where one book was an outlier against
it. Paste three or more books into the tab and `devig.screen()` runs that
arithmetic, computing each row's consensus with that row's own book left out so
a price cannot vote for itself.

Their other finding belongs next to it and is recorded in the module: the books
limited the winning accounts. The edge is real and the capacity is small.

## Requirements

A model provider for the narrative read (the arithmetic itself needs none).
Ported logic — the odds/EV/Kelly maths predates this repo's split and carries
its own test coverage from Sentinel's original NFL agent.
