"""Measuring a model before it is allowed to say anything.

This module is the gate. `ratings.py` can produce a probability for any fixture
the moment it has been fitted, and that probability is worth nothing until it
has been scored against games the model did not see. SONAR already holds this
line on the markets side — `P(profit)` stays pinned at its driftless baseline
until `calibration.py` measures drift from positions that actually closed — and
Playmaker now holds it too.

Everything here is walk-forward: predict the next game from a table that has
only seen the previous ones, then update. Scoring a model on data it was fitted
on is the easiest way in the world to invent an edge that is not there, and it
is the mistake `MODELS.md` was written to avoid repeating.

Three numbers, because each answers a different question:

* **Brier score** — mean squared error of the probability. Proper, bounded,
  and the one to compare against a baseline.
* **Log loss** — punishes confident mistakes far harder. A model that is right
  on average but occasionally certain and wrong looks fine on Brier and bad
  here, which is exactly the failure that ruins a staked bankroll.
* **Calibration** — of the fixtures called 60%, did 60% happen? A model can be
  sharp and miscalibrated, and only the second matters for sizing a bet.

The verdict borrows `backtest._verdict_for`'s vocabulary deliberately: KEEP,
WEAK, DROP. A model that does not beat the base rate is labelled, not shipped
quietly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import staking
from .ratings import Table, config_for, expected, update
from .results import Game

#: Below this there is nothing to conclude, whatever the numbers say.
MIN_GAMES = 200
#: Buckets for the calibration curve.
BUCKETS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
#: A probability is never allowed to be 0 or 1 — log loss would be infinite,
#: and no model here has earned certainty.
CLAMP = 1e-6

#: Source name for a model that has not passed. Deliberately not one of
#: `staking.SIZEABLE_SOURCES`, so such an estimate cannot size a bet.
UNVALIDATED = "model (unvalidated)"


@dataclass(frozen=True)
class Bucket:
    low: float
    high: float
    predicted: float
    realised: float
    count: int

    @property
    def gap(self) -> float:
        return self.realised - self.predicted


@dataclass(frozen=True)
class Score:
    games: int
    brier: float
    log_loss: float
    accuracy: float
    base_rate: float
    baseline_brier: float
    buckets: tuple[Bucket, ...] = field(default_factory=tuple)

    @property
    def skill(self) -> float:
        """Improvement on always predicting the base rate. Negative is worse."""
        if self.baseline_brier <= 0:
            return 0.0
        return 1.0 - self.brier / self.baseline_brier

    @property
    def calibration_error(self) -> float:
        """Weighted mean |predicted - realised| across populated buckets.

        This is the number that sets how wide a model estimate's interval is.
        It is measured, which is the whole point — nothing here asserts its own
        confidence.
        """
        total = sum(b.count for b in self.buckets)
        if not total:
            return 1.0
        return sum(abs(b.gap) * b.count for b in self.buckets) / total

    @property
    def verdict(self) -> str:
        if self.games < MIN_GAMES:
            return "INSUFFICIENT"
        if self.skill <= 0.0:
            return "DROP"
        # Standard error of the Brier difference, approximated from the sample.
        margin = 1.0 / math.sqrt(self.games)
        return "KEEP" if self.skill > margin else "WEAK"

    @property
    def summary(self) -> str:
        if self.games < MIN_GAMES:
            return (f"{self.games} games scored; {MIN_GAMES} needed before "
                    "any of this means anything.")
        beat = "beats" if self.skill > 0 else "loses to"
        return (f"{self.verdict}: Brier {self.brier:.4f} {beat} the base-rate "
                f"baseline {self.baseline_brier:.4f} "
                f"(skill {self.skill:+.3f}), log loss {self.log_loss:.4f}, "
                f"calibration error {self.calibration_error*100:.1f}pts "
                f"over {self.games} games.")


def _clamp(p: float) -> float:
    return min(1.0 - CLAMP, max(CLAMP, p))


def score_predictions(pairs: list[tuple[float, float]]) -> Score:
    """Score (predicted probability, realised outcome) pairs.

    Outcomes are 1.0, 0.0 or 0.5 for a draw — a draw is genuinely half a home
    win under the Elo convention, and Brier handles a fractional outcome
    without complaint.
    """
    if not pairs:
        return Score(0, 0.0, 0.0, 0.0, 0.0, 0.0)

    n = len(pairs)
    base_rate = sum(actual for _, actual in pairs) / n
    brier = sum((p - actual) ** 2 for p, actual in pairs) / n
    baseline = sum((base_rate - actual) ** 2 for _, actual in pairs) / n
    logloss = -sum(
        actual * math.log(_clamp(p)) + (1.0 - actual) * math.log(1.0 - _clamp(p))
        for p, actual in pairs) / n
    hits = sum(1 for p, actual in pairs
               if (p > 0.5) == (actual > 0.5) or actual == 0.5)
    accuracy = hits / n

    buckets = []
    for low, high in zip(BUCKETS, BUCKETS[1:]):
        inside = [(p, a) for p, a in pairs
                  if low <= p < high or (high == 1.0 and p == 1.0)]
        if not inside:
            continue
        buckets.append(Bucket(
            low=low, high=high,
            predicted=sum(p for p, _ in inside) / len(inside),
            realised=sum(a for _, a in inside) / len(inside),
            count=len(inside)))
    return Score(n, brier, logloss, accuracy, base_rate, baseline, tuple(buckets))


def walk_forward(games: list[Game], sport_key: str = "",
                 burn_in: int = 100, season_gap_days: int = 60) -> Score:
    """Fit and score a rating in one chronological pass.

    Every prediction is made by a table that has seen only earlier games. The
    first `burn_in` games are fitted but not scored: every team starts on the
    same rating, so the earliest predictions measure how long the model takes
    to learn a league rather than how well it knows one.
    """
    from .ratings import regress_to_mean

    table = Table(config_for(sport_key))
    pairs: list[tuple[float, float]] = []
    previous = None
    for index, game in enumerate(sorted(games, key=lambda g: g.date)):
        if previous and (game.date - previous).days >= season_gap_days:
            regress_to_mean(table)
        if index >= burn_in:
            pairs.append((expected(table.value(game.home),
                                   table.value(game.away), table.config),
                          game.result))
        update(table, game)
        previous = game.date
    return score_predictions(pairs)


def calibrated_estimate(probability: float, score: Score,
                        sport_key: str = "") -> staking.Estimate:
    """Wrap a model probability in what measurement says it is worth.

    A model that has not been scored, has too little history, or lost to the
    base rate comes back with a source that `staking.kelly()` refuses to size
    from. One that passed gets an interval set by its *measured* calibration
    error rather than by anything it claims about itself.
    """
    verdict = score.verdict
    if verdict in ("DROP", "INSUFFICIENT"):
        width = 0.5
        source = UNVALIDATED
        basis = f"{verdict.lower()} — {score.summary}"
    else:
        # Widen by the measured miss, and again for a WEAK result, since
        # "better than the baseline but inside its own error bar" is a reason
        # to stake less rather than a reason to stake.
        width = score.calibration_error * (2.0 if verdict == "WEAK" else 1.0)
        source = "model"
        basis = (f"{verdict.lower()} — elo over {score.games} games, "
                 f"calibration error {score.calibration_error*100:.1f}pts")
    if sport_key:
        basis = f"{sport_key}: {basis}"
    return staking.Estimate(
        probability,
        max(0.0, probability - width), min(1.0, probability + width),
        source, basis)
