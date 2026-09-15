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
sets the build order (§11). Stage 1 is done; Stage 2 is next and needs no key.

### Stage 1 — fix what is provably wrong (no data, no model) — **done**

Shipped 2026-09-15. `devig.py`, `staking.py`, 82 new tests, and the Playmaker
tab rebuilt around the cross-book screen.

- [x] `P1` `bug` `@ai` **Three devig methods, Shin as default.** Multiplicative
      (ranked last in every comparison), Clarke's power method, and Shin's
      iterative solve, all generalised to N-way markets. The UI shows the three
      side by side, because the disagreement is the argument.
- [x] `P1` `bug` `@ai` **Both sides of a market are now required.** Found while
      testing: a margin is how far prices sum past certainty, so a single price
      cannot reveal one. The form asked for exactly one for years — nothing
      downstream of it could have been right. `MODELS.md` §7a.
- [x] `P1` `bug` `@ai` **"Edge" and "EV" were the same number.** The stat row
      showed both; `edge_versus_market()` is exactly EV rescaled by the decimal
      odds. `staking.edge_versus_fair()` is the independent quantity. §7b.
- [x] `P1` `feature` `@ai` **Cross-book consensus and the outlier screen.** The
      Kaunitz arithmetic, with leave-one-out so a price cannot vote for itself,
      plus a parser for pasted book prices.
- [x] `P1` `design` `@ai` **A narrative estimate can no longer size a bet.**
      `staking.Estimate` carries a source with every probability and
      `staking.kelly()` returns zero for a narrative one.
- [x] `P2` `feature` `@ai` **Probabilities carry an interval and Kelly uses it.**
      Sized at the interval's low end rather than a fixed quarter, so an honest
      interval spanning break-even produces no bet.

### Found while building Stage 1

- [x] `P1` `testing` `@ai` **The test suite could hang forever, and did.**
      `MainWindow.shutdown()` falls through to `QThread.terminate()`, which never
      returns what the thread held — the GIL, or a pthread mutex. Both deadlock
      stacks were sampled out of hung runs. Lives in SONAR's `tests/conftest.py`
      rather than here: the network and the real data directory are closed off,
      `PollThread.run` is a no-op for the session, `terminate()` raises instead
      of wedging, and a faulthandler watchdog aborts with every thread's stack
      after 120s. 15 clean runs; the suite went from 31s to 3-9s.

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

### Stage 5 — more sports — **done, and earlier than planned**

- [x] `P1` `feature` `@ai` **Nine sports registered**: NFL, NBA, MLB, NHL,
      Premier League, Champions League, NCAA basketball, UFC, ATP tennis — 81
      prop types. The plan had this at Stage 5 on the assumption it needed the
      models first. It did not: everything Stage 1 shipped is pure odds
      arithmetic, so the screen works on a soccer 1X2 the same day it works on
      an NFL spread. Each entry carries its ESPN league path (all nine verified
      live, behind `-m network`) ready for Stage 2.
- [ ] `P2` `feature` `@ai` **Point each sport at a Stage 3 model** once those
      exist — Dixon-Coles suits soccer and hockey, Elo the rest.

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
