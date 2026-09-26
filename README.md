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

**Seven sports**: NFL, College Football, NBA, MMA (UFC), International
Football, Golf and Cycling — 67 prop types between them. Football means
**international team games only** — World Cup, Euros, Copa América, Gold Cup,
AFCON, Nations League, every confederation's qualifiers, and friendlies. No club
competitions.

The **pricing** half is identical for all seven: `devig.py` and `staking.py` are
pure odds maths and never learn what they are pricing. The **rating** half is
not, because a result does not mean the same thing in each — `Sport.shape` says
which:

| Shape | Sports | Fitted by |
|---|---|---|
| `scores` — two sides, a scoreline | NFL, College Football, NBA, International Football | Elo with margin of victory; football also Dixon-Coles |
| `winners` — two sides, no scoreline | MMA | Elo on wins and losses; no margin to learn from, so it degenerates to plain Elo, which is correct |
| `field` — a finishing order | Golf, Cycling | **nothing** — a hundred competitors is not a head-to-head contest |

**Cycling has no results feed.** ESPN serves no cycling endpoint (400) and the
free alternatives are unofficial scrapers of one site, so `espn_paths` is empty
and `has_results` is False. That is a real state, not an oversight: cycling is
priced and screened like anything else, and nothing here rates a rider. The tab
says so when you pick it.

**International football pools fourteen competitions** into one set of
national-team ratings. Sides play a handful of times a year, so splitting by
tournament would leave none with enough matches to fit on — and form in a
qualifier is evidence about the same side at the Euros.

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
| `Sport.espn_paths` / `Sport.shape` / `Sport.model` | Which competitions feed it, what a result looks like, and which model fits — or `""` where none does. |
| `Sport.outcomes` | Whether the headline market prices a draw. |
| `list_sports()` / `get_sport()` | The seven-sport registry. |
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

## The prediction models

Three, chosen because they have track records rather than because they are
clever — `MODELS.md` §1 found that a tuned Elo lands within ~1.65pp of deep
models and that the market sits above both.

| Module | Model |
|---|---|
| `ratings.py` | **Elo** in FiveThirtyEight's published form — margin-of-victory scaling, the autocorrelation correction that stops strong teams' ratings running away, and between-season regression. Plus **Pythagorean expectation** as an independent cross-check, with published exponents only (a guessed one would make the check worse than useless). |
| `poisson.py` | **Dixon-Coles (1997)** for football: attack and defence per team, the `tau` correction for the four lowest scorelines, and time decay at their own fitted `xi = 0.0065` per half-week. Fitted stdlib-only — with team indicators and a log link each strength's MLE given the others is closed form, so the fixed point *is* the maximum. |
| `results.py` | The feed. One ESPN adapter over each sport's league codes, no key. `parse_scoreboard` reads a scoreline, `parse_bouts` reads a fight card (no home/away, no score — only a winner). |
| `scoring.py` | The gate. Brier, log loss, calibration and a KEEP/WEAK/DROP verdict, all walk-forward. `measure(sport_key, seasons, games=None)` fetches and scores a sport in one call, so a UI tab can re-run it after a change instead of the verdict living only in a script's scrollback. |

Measured out of sample on real results:

| Sport | Results | Brier | Baseline | Skill | Verdict |
|---|---|---|---|---|---|
| International Football | 5,842 | 0.1525 | 0.1834 | **+0.168** | KEEP |
| College Football | 1,823 | 0.2057 | 0.2380 | **+0.136** | KEEP |
| NBA | 2,793 | 0.2177 | 0.2474 | **+0.120** | KEEP |
| NFL | 1,338 | 0.2286 | 0.2459 | **+0.071** | KEEP |
| MMA | 3,369 | 0.2455 | 0.2463 | +0.003 | **WEAK** |

Sanity check on who the ratings put at the top — international football gives
ESP, ARG, ENG, FRA, JPN, MAR; college football OSU, MIZ, MICH, FSU; MMA
Makhachev, Ulberg, Van, Du Plessis. The ratings are sensible everywhere.

**MMA barely predicts, and that is the point of having a gate.** Three
thousandths of skill over 3,369 fights is nothing — fighters compete twice a
year, style beats general strength, and one punch ends it. `scoring` returns
WEAK rather than KEEP, which doubles the interval on an MMA estimate and shrinks
the stake to match. The ratings are still worth showing; they are just not worth
betting the way an NBA rating is.

**The KEEP bar is a measured interval, not a shape-of-an-error-bar guess.** The
verdict used to compare skill against `1/sqrt(games)` — an approximation that
never actually measured the sample's real variance. It now compares against
`skill_floor`, the lower bound of a 95% moving-block bootstrap over the
per-game Brier differences (blocked, not single-game, because consecutive
predictions share the fitted table that priced them): KEEP only when the whole
interval clears zero, WEAK when skill is positive but the interval still
touches zero. **Accuracy excludes draws** rather than counting them as free
hits — Brier and log loss already score a draw properly at 0.5, and accuracy
is display-only, so a draw-heavy league can no longer inflate it. And
`ratings.observed_draw_rate()` now genuinely measures a league's own draw rate
once it has fit at least 50 games (`MIN_DRAW_SAMPLE`) — previously the function
claimed to measure it but always returned the default, since nothing ever set
the attribute it read.

Dixon-Coles, checked separately against club-league matches (1,125 fitted, 375
held out): home advantage 1.155, RPS **0.2149** against a published bar around
0.20.

Beating the base rate is real and modest. It is **not** the same as beating a
bookmaker, who starts from a better price and charges the margin on top.

**Nothing predicts until it has been measured.** `scoring.calibrated_estimate()`
is the only route from a model to a stake, and it refuses for a model that is
unmeasured or lost to the base rate — the same rule `calibration.py` holds on the
markets side.

### Two things not to undo

`results.py` sends **no User-Agent**. ESPN's edge returns 403 for a custom or
browser-shaped one and accepts urllib's honest default — measured 3/3 versus
0/3. Do not "fix" a 403 by pasting in a Chrome string: that is impersonation,
and it is also the thing being blocked. And long windows come back chunked and
are sometimes truncated, raising `http.client.IncompleteRead` — an
`HTTPException`, *not* an `OSError`, so it slips past the obvious except clause
and silently costs a month of results.

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
