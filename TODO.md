# Playmaker — TODO

> **Legend** — priority `P0` critical · `P1` high · `P2` normal · `P3` low
> categories `security` `bug` `feature` `performance` `design` `docs` `testing` `infra` `research`
> owner `@me` (needs you — accounts, keys, money, judgement) · `@ai` (Claude can do this)

---

## Needs you

- [ ] `P1` `infra` `@me` **Multi-book odds feed.** The Kaunitz consensus-outlier
      strategy (`MODELS.md` §8) is the highest-value item in the whole plan and
      the only one that needs an account. The Odds API's free tier is quoted as
      25 requests/day by one source and 500/month by another — worth checking
      which is real before paying. Everything else in the plan runs on keyless
      data (§10).

---

## v2 — rebuilding the prediction

Planned from `MODELS.md`, which surveys the models with real track records and
sets the build order (§11). Nothing below is started.

### Stage 1 — fix what is provably wrong (no data, no model)

- [ ] `P1` `bug` `@ai` **Replace multiplicative vig removal.** `remove_vig()`
      uses the method that comparative studies rank *last*; it understates
      favourites and overstates longshots, so every longshot edge Playmaker
      reports today is inflated. Add Shin's iterative solve and Clarke's power
      method, default to the better one. `MODELS.md` §7.
- [ ] `P1` `feature` `@ai` **Consensus devig across books.** The §8 arithmetic —
      consensus probability from N books' prices, outlier detection against it.
      Buildable and testable before the feed above exists.
- [ ] `P1` `design` `@ai` **Stop feeding the LLM's percentage to EV and Kelly.**
      `parse_analysis()` regex-scrapes a number out of prose and `ui/app.py`
      sizes a stake from it. Keep the narrative as commentary beside the
      numbers; it must stop being the source of them. `MODELS.md` §0.
- [ ] `P2` `feature` `@ai` **Probabilities carry an interval.** Kelly should
      shrink with estimate uncertainty rather than by a fixed ¼. `MODELS.md` §9.

### Stage 2 — data

- [ ] `P1` `feature` `@ai` **ESPN results adapter.** One adapter plus a league
      code covers NFL, NBA, MLB, NHL, EPL, UCL, NCAAB, UFC and ATP with full
      history and no key — verified live 2026-09-15. Local cache. `MODELS.md` §10.

### Stage 3 — game-level models

- [ ] `P1` `feature` `@ai` **Elo with margin-of-victory and autocorrelation
      correction**, plus between-season regression. §3. The reviewed literature
      says this captures most of the available pre-match signal.
- [ ] `P2` `feature` `@ai` **Dixon-Coles** for soccer and hockey — attack/defence
      Poisson, low-score dependence term, time decay ξ=0.0065/half-week. §4.
- [ ] `P3` `feature` `@ai` **Pythagorean expectation** as an independent
      cross-check on Elo (exponents 1.83 MLB / 2.37 NFL / 13.91 NBA / 2.15 NHL). §5.

### Stage 4 — measurement, before any of it is shown

- [ ] `P1` `testing` `@ai` **Score the models before trusting them.** Brier, log
      loss, RPS and a calibration curve over held-out history, with a
      KEEP/WEAK/DROP verdict in the style of `backtest._verdict_for`. A model
      that does not beat the market baseline gets labelled, not shipped quietly.

### Stage 5 — more sports

- [ ] `P1` `feature` `@ai` **Registry entries beyond NFL** — NBA, MLB, NHL, EPL,
      UCL, NCAAB to start. Each needs its prop types, a league code and a
      pointer to whichever Stage 3 model suits its scoring shape.

### Stage 6 — player props

- [ ] `P2` `feature` `@ai` **Distributional prop model.** A prop is a tail
      probability, not a projected mean: model opportunity (negative binomial)
      × efficiency, integrate past the line. NFL first — nflverse is the only
      free play-by-play source verified. `MODELS.md` §6.

### Stage 7 — the real scoreboard

- [ ] `P2` `feature` `@ai` **Closing Line Value tracking.** Record the price at
      pick time, compare against the close, report the distribution. Measurable
      within days on every pick, unlike P&L. `MODELS.md` §2.

---

## v1 — shipped

Odds arithmetic (american/decimal conversion, implied probability, vig removal,
EV, Kelly, edge vs market) with 28 tests; the NFL sport registry; the LLM prompt
and response parser; the Playmaker tab in SONAR.
