# Improving Playmaker's prediction

Research notes on the sports-forecasting models with real track records, what
of each transfers here, and what the evidence actually supports. Nothing in
§1–§9 is implemented. The build order is §11 and the refusals are §12.

---

## 0. What Playmaker does today, and why it is the wrong shape

The module splits cleanly in two, and only one half is sound.

**The arithmetic is correct and well covered.** `american_to_decimal`,
`implied_probability`, `expected_value`, `kelly_fraction`,
`edge_versus_market` — 33 tests, no notes. Keep all of it.

**There is no model.** The probability those functions consume comes from
`parse_analysis()`, which regex-scrapes a percentage out of prose an LLM wrote:

```python
prob = re.search(r'(\d{1,3}(?:\.\d+)?)\s*%', verdict)
```

`ui/app.py` then feeds that number to `expected_value()` and prints a ¼-Kelly
stake from it. So a language model's guess — with no data behind it, no
uncertainty attached, and no way to be wrong on the record — is dressed in
correct arithmetic and displayed as a bet size. That is the most dangerous
shape a tool like this can take: rigorous machinery wrapped around an
unfounded input reads as far more credible than either part deserves.

This is the same mistake SONAR already corrected on the markets side. There,
`P(profit)` stays pinned at the driftless `1/(1+R:R)` baseline until
`calibration.py` measures drift from positions that actually closed, because
five pre-registered studies found no directional edge. Playmaker never had
that discipline applied to it.

There is also **one outright defect**, independent of all the above: the vig
removal is the worst-performing method in the literature. §7.

---

## 1. The ceiling: how good can any of this get

Worth establishing before choosing a model, because it determines how much
model complexity is worth buying.

Pre-match prediction has a low and well-mapped ceiling. In a unified tennis
benchmark, a tuned **Elo baseline reached 65.87%** accuracy and all four
approaches tested — including deep networks — sat inside a **1.65 percentage
point band**; ML methods in tennis generally level off around 70%. The
reviewers' summary is the line to remember: *Elo alone captures most of what
can be captured from universally available pre-match features.*

In soccer, the 2017 Soccer Prediction Challenge's best entry was CatBoost over
pi-ratings at **0.1925 RPS / 55.82% accuracy**. Other studies report bookmaker
odds at ~0.2020 RPS — not directly comparable, different samples — but the
qualitative finding repeats across the literature: **even a plain
odds-derived baseline is hard to outperform.**

So the honest expectation is that a careful Elo gets us ~95% of the way to
what a tuned gradient-boosted ensemble would, and that both land near or
below the market. **Model sophistication is not where the return is.** §7 and
§8 are.

---

## 2. The market is the benchmark, not the opponent

Across 397,935 football matches, Pinnacle's **closing** odds correlate with
observed outcome frequencies at **r² ≈ 0.997**. The closing line at a sharp,
high-limit, low-margin book is the most information-dense probability estimate
that exists for a sporting event — it has absorbed injuries, lineups, weather
and every sharp bettor's opinion.

Two consequences, and they set Playmaker's whole posture:

1. **The devigged sharp closing price is the best available probability.** It
   should be what Playmaker shows by default, and what any in-house model is
   scored against — not a number the model competes with for the user's
   attention.
2. **Closing Line Value is the scorekeeper.** Whether a pick beat the closing
   line is measurable within days, on every pick, long before P&L means
   anything. CLV is the standard professional metric for exactly this reason.
   It is the Playmaker equivalent of `calibration.py`.

---

## 3. Rating models — Elo, Glicko-2, Bradley-Terry

The family that needs nothing but results, which is precisely what we can get
for free (§10).

**Bradley-Terry** is the statistical foundation: `P(i beats j) = p_i/(p_i+p_j)`.
Elo is its online, recursive approximation.

**Elo** with the FiveThirtyEight refinements is the workhorse. Three details
separate a working implementation from a naive one:

- **Margin-of-victory multiplier** — a 30-point win is more evidence than a
  1-point win. FiveThirtyEight's NFL form is
  `log(margin + 1) × 2.2 / (0.001 × elo_diff + 2.2)`.
- **Autocorrelation correction** — the `elo_diff` term in that denominator.
  Favourites run up the score, so without it strong teams' ratings inflate
  without bound. NHL uses `2.05 / (0.001 × elo_diff + 2.05)`; NBA runs K=20.
- **Between-season regression to the mean** — carry over roughly two-thirds
  of a team's rating, or last season's champion starts the new one overrated.

**Glicko-2** is Elo plus a **rating deviation** and a volatility term — it
knows how uncertain it is about each team. That matters more here than the
accuracy difference does, because §9 needs an uncertainty to shrink the stake
with, and Elo has none to give.

**Verdict:** Elo with MOV first, because it is small, testable, sport-agnostic
and provably most of the available signal. Glicko-2 second, for the deviation.

---

## 4. Scoreline models — Poisson and Dixon-Coles

Ratings give a win probability. A **scoreline distribution** gives win, draw,
totals, both-teams-to-score and handicaps from one fit — which is what a prop
tool actually needs.

**Dixon & Coles (1997)** is the landmark and still the reference implementation
for soccer. Each team carries an attack and a defence parameter; goals are
Poisson; two corrections make it work:

- a **low-score dependence term** `τ`, because independent Poissons
  systematically underpredict 0-0 and 1-1 draws;
- **exponential time decay** on the match weights, `φ(t) = exp(-ξt)`, with
  **ξ = 0.0065 per half-week** — a half-life of about a year — fitted by
  maximising out-of-sample predictive log-likelihood.

Dixon and Coles reported returns against the market of the day, which is the
strongest single result in this literature. Modern extensions (bivariate
Poisson, Karlis-Ntzoufras diagonal inflation) add little for the complexity.

**Verdict:** the right model for the soccer leagues, and reusable for hockey.
Wrong shape for NFL/NBA scoring, which wants a normal margin model instead.

---

## 5. Pythagorean expectation — the cheap second opinion

`win% ≈ PF^x / (PF^x + PA^x)`. Sport-specific exponents: **1.83** MLB,
**2.37** NFL, **13.91** NBA (Morey), **2.15** NHL. Tighter-scoring sports take
higher exponents.

Its famous property is that it predicts a team's *future* win rate better than
its *current* win rate does — it strips the luck out of close-game records.
Typical error is around 2% per team. Modern ML beats it (one NFL study reports
a neural net at MAE 0.052 vs the Pythagorean method), but nothing else this
cheap comes close.

**Verdict:** worth having as an independent cross-check on Elo — two methods
disagreeing is information — not as the primary estimate.

---

## 6. Player props need a distribution, not a mean

This is the structural reason the current design cannot work, separate from
the LLM objection.

A prop is a **tail probability**: `P(receiving yards > 52.5)`. You cannot get
there from a projected mean. Two receivers both projected at 55 yards — one a
high-volume possession target, one a deep threat — have very different
P(over), because they have different *variance*.

The right shape:

1. Model the **opportunity** (targets, carries, snaps) — usually
   **negative binomial**, which fits overdispersed counting stats better than
   Poisson.
2. Model **efficiency per opportunity** (yards per target).
3. Compound the two into a distribution, integrate the tail past the line.

Anytime-touchdown props are the clean case: a per-play scoring rate compounded
over an expected play count gives P(≥1 TD) directly.

**Verdict:** this, not a better prompt, is what turns Playmaker's prop tab into
something with a number behind it. It is also the most work, and it is
NFL-specific per prop type — which is why §11 puts it after the game-level
models.

---

## 7. The one provable defect: how the vig is removed

`remove_vig()` normalises both sides' implied probabilities to sum to 1:

```python
return p_a / total, p_b / total
```

This is the **multiplicative** method, and comparative studies rank it **last**
of the standard options. Clarke's comparison found the multiplicative model
worst on every measure, with the **power** method best or equal-best; **Shin's**
method — which models the margin as protection against insider bettors and
therefore corrects the favourite-longshot bias — is the other standard
improvement and generally the best calibrated.

Multiplicative devig spreads the margin evenly in proportion to price, which
systematically **understates the favourite and overstates the longshot**. Every
edge Playmaker reports on a longshot is inflated by this, today.

**Verdict:** fix this first. It needs no data, no model and no new dependency —
just Shin's iterative solve and Clarke's power solve alongside the existing
method, and the better one as default. This is a bug, not a feature.

---

## 8. Kaunitz (2017): don't out-predict, out-shop

The most directly applicable result in the whole field, and it inverts the
usual approach.

Kaunitz, Zhong and Kreiner did not build a forecasting model. They took the
odds **many bookmakers were already publishing**, formed a consensus
probability from them, and bet only where a single book's price was a
significant outlier against that consensus. Profitable in a **10-year**
historical simulation on closing odds, a **6-month** minute-by-minute
simulation, and **5 months of real staked money** — ROI from 3.5% in
simulation to 9.9% live.

Two things must be said together, because the second is in the same paper:
**the bookmakers limited the winning accounts.** The edge is real and the
capacity is tiny. It also biased their own sample, since the games they were
allowed to stake on stopped being randomly selected.

**Verdict:** this is the model Playmaker should be built around. It matches
SONAR's existing philosophy exactly — don't assert a direction you cannot
measure; find where the market disagrees *with itself*. It needs a multi-book
odds feed (§10) and nothing else.

---

## 9. Staking under an uncertain estimate

Full Kelly assumes the probability is **known**. It never is here, and Kelly is
brutally asymmetric about it: overestimate your edge and you overbet, and the
geometric growth rate goes negative well before the edge does.

`kelly_fraction()` computes full Kelly correctly and `ui/app.py` quarters it,
which is the right convention. What is missing is that the fraction should
shrink with the **uncertainty of the estimate**, not by a fixed divisor. A
probability carrying a wide interval should size smaller than a tight one at
the same point estimate — which is the practical argument for Glicko-2's
rating deviation (§3) over plain Elo.

**Verdict:** carry an interval alongside every probability, end to end, and let
it set the stake. A point estimate with no error bar is what created the
current problem.

---

## 10. What data we can actually get

Checked live on 2026-09-15, not taken from documentation.

| Source | Key? | Covers | Verified |
|---|---|---|---|
| **ESPN public API** | none | NFL, NBA, MLB, NHL, EPL, UCL, NCAAB, UFC, ATP | ✅ all 9 return 200 |
| ESPN historical | none | date ranges + NFL week/seasontype | ✅ 82 NBA games for a 10-day range; EPL, NFL week 5 |
| **nflverse** | none | play-by-play, rosters, 554 player-stat assets | ✅ GitHub releases readable |
| The Odds API | **key** | multi-book prices, the §8 input | ⛔ free tier disputed — 25/day vs 500/mo by source |
| football-data.org | **key** | 12 competitions, free tier | not tested |

The important finding: **one ESPN adapter and a league code gets every sport
in the first row**, with full result history. "More sports" is a registry entry
and a string, exactly as the module was designed for — no per-sport scraper.

The gap is odds. §8 is the highest-value work and it is the one thing that
needs an account. That makes it an `@me` item, like Finnhub.

---

## 11. Build order

Sequenced so that each stage is verifiable on its own and nothing depends on a
key we do not have.

**Stage 1 — fix what is provably wrong.** No data, no model.
Shin + Clarke power devig beside the multiplicative one, better as default.
Multi-book consensus devig (the §8 arithmetic, ready before the feed is).
Probabilities carry an interval; Kelly shrinks with it (§9).

**Stage 2 — results feed.** One ESPN adapter, league codes, local cache.
This is the foundation for every model below and needs no key.

**Stage 3 — game-level models.** Elo with MOV and autocorrelation (§3),
Pythagorean as cross-check (§5), Dixon-Coles for soccer and hockey (§4).

**Stage 4 — measurement, before any of it is shown.** Brier score, log loss,
RPS and a calibration curve over held-out history, with a KEEP/WEAK/DROP
verdict in the style of `backtest._verdict_for`. A model that does not beat
the market baseline gets labelled as such in the UI rather than quietly
shipped.

**Stage 5 — more sports.** Registry entries with prop types per sport, each
pointing at whichever Stage 3 model suits its scoring.

**Stage 6 — player props (§6).** Opportunity × efficiency distributions.
NFL first, because nflverse is the only free play-by-play source we verified.

**Stage 7 — CLV tracking (§2).** Record the price at pick time, compare to
close, report the distribution. The real scoreboard.

---

## 12. What this note does not recommend

- **A deep model.** §1 — the ceiling is low and Elo is already near it. The
  complexity would buy a point or two and cost all the interpretability.
- **Keeping the LLM's percentage as an input to EV or Kelly.** §0. The
  narrative read is worth keeping as *commentary beside* the numbers; it must
  stop being the source of them.
- **Live bet placement.** Same line as `execution.py` holds for markets:
  Playmaker evaluates a wager, it does not place one.

---

## Sources

- Kaunitz, Zhong & Kreiner (2017), *Beating the bookies with their own numbers* — [arXiv:1710.02824](https://arxiv.org/abs/1710.02824)
- Dixon & Coles (1997), via [dashee87](https://dashee87.github.io/football/python/predicting-football-results-with-statistical-modelling-dixon-coles-and-time-weighting/) and [opisthokonta](https://opisthokonta.net/?cat=48)
- Clarke (2017), *Adjusting Bookmaker's Odds to Allow for Overround* — [PDF](https://outlier.bet/wp-content/uploads/2023/08/2017-clarke-adjusting_bookmakers_odds.pdf)
- [Devigging methods compared](https://betherosports.com/blog/devigging-methods-explained)
- [Pinnacle closing-line efficiency](https://www.football-data.co.uk/blog/pinnacle_efficiency.php)
- Bunker et al. (2024), *Elo vs ML for tennis* — [DOI](https://doi.org/10.1177/17543371231212235); [unified tennis benchmark](https://doi.org/10.3390/analytics5030022)
- [Evaluating soccer match prediction models](https://link.springer.com/article/10.1007/s10994-024-06608-w)
- [A Systematic Review of ML in Sports Betting](https://arxiv.org/pdf/2410.21484)
- [FiveThirtyEight nfl-elo-game](https://github.com/fivethirtyeight/nfl-elo-game/blob/master/forecast.py); [NBA methodology](https://fivethirtyeight.com/methodology/how-our-nba-predictions-work/)
- [Pythagorean expectation](https://en.wikipedia.org/wiki/Pythagorean_expectation); [NFL win prediction](https://pmc.ncbi.nlm.nih.gov/articles/PMC12463883/)
- [Closing line value](https://www.pinnacleoddsdropper.com/blog/closing-line-value)
