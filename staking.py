"""What a probability is allowed to do, and how much to stake on it.

Playmaker's original failure was not in its arithmetic — the Kelly and EV
functions are correct — but in what it fed them. `parse_analysis()` scrapes a
percentage out of prose a language model wrote, and the UI sized a quarter-Kelly
stake from it. Nothing in the type system objected, because a float is a float.

`Estimate` is that objection. Every probability carries where it came from and
how uncertain it is, and only estimates from a source that can be checked
against reality are allowed to size a bet. A narrative read stays narrative.

The uncertainty is not decoration either. Full Kelly assumes the probability is
known exactly, and is brutally asymmetric when it is not: overstate the edge and
the growth rate goes negative well before the edge does. So the stake is sized
at the pessimistic end of the interval, which shrinks it as confidence falls and
zeroes it when the interval spans break-even — instead of the fixed quarter that
was applied regardless.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import american_to_decimal, kelly_fraction


#: Sources whose numbers can be checked against something. Only these may size.
SIZEABLE_SOURCES = ("market", "consensus", "model")

#: A narrative read. Displayed, never staked on.
NARRATIVE = "narrative"

#: Cap on any suggested fraction of bankroll, applied after everything else.
MAX_FRACTION = 0.05


@dataclass(frozen=True)
class Estimate:
    """A probability, where it came from, and how sure we are of it."""
    probability: float
    low: float
    high: float
    source: str
    basis: str = ""

    def __post_init__(self) -> None:
        for name, value in (("probability", self.probability),
                            ("low", self.low), ("high", self.high)):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1, got {value!r}")
        if not self.low <= self.probability <= self.high:
            raise ValueError("probability must lie inside [low, high]")

    @property
    def width(self) -> float:
        return self.high - self.low

    @property
    def can_size(self) -> bool:
        """Whether this estimate is allowed to determine a stake.

        The whole point of the class. A narrative source returns False no
        matter how confident its prose sounded.
        """
        return self.source in SIZEABLE_SOURCES


def from_market(odds: int, opposing: int, method: str = "shin") -> Estimate:
    """The market's own opinion about the first side, margin removed.

    **Both sides are required, and that is not an inconvenience — it is the
    arithmetic.** A margin is defined as the amount by which a market's prices
    sum past certainty, so a single price cannot reveal one: -110 on its own is
    equally consistent with a fair coin flip carrying 4.8% juice and with a
    genuine 52.4% favourite quoted at no margin. Playmaker's form asked for one
    price for years, which is why nothing downstream could ever have been right.

    The interval is the disagreement between the three devig methods — not a
    formal confidence interval, but a real measure of how much the answer
    depends on a modelling choice that one book's prices cannot settle.
    """
    from .devig import devig, method_spread

    pair = (odds, opposing)
    fair = devig(pair, method)[0]
    low, high = method_spread(pair, 0)
    return Estimate(fair, min(low, fair), max(high, fair), "market",
                    f"{method} devig of {odds:+d} / {opposing:+d}")


def from_consensus(cons, index: int, confidence: float = 1.96) -> Estimate:
    """Cross-book consensus, with an interval from how much the books disagree.

    This is the one estimate in Playmaker today whose uncertainty is measured
    rather than asserted: books quoting the same market are repeated samples of
    the same quantity, so their spread is informative.
    """
    p = cons.probability[index]
    se = cons.standard_error(index)
    if not se or math.isnan(se):
        return Estimate(p, p, p, "consensus", f"{cons.books} book(s), no spread")
    half = confidence * se
    return Estimate(p, max(0.0, p - half), min(1.0, p + half), "consensus",
                    f"{cons.books} books, +/-{half*100:.2f}pts")


def from_narrative(probability: float | None, confidence: str = "") -> Estimate | None:
    """The LLM's read. Returned so it can be shown, flagged so it cannot size.

    The interval is deliberately enormous — a language model's stated
    confidence is not a measurement, and pretending otherwise is the mistake
    this module exists to prevent.
    """
    if probability is None:
        return None
    width = {"high": 0.15, "medium": 0.25, "low": 0.35}.get(confidence.lower(), 0.30)
    return Estimate(probability,
                    max(0.0, probability - width), min(1.0, probability + width),
                    NARRATIVE, f"language model, stated confidence {confidence or 'none'}")


# --------------------------------------------------------------------------- #
# Sizing
# --------------------------------------------------------------------------- #
def edge_versus_fair(estimate: Estimate, fair_probability: float) -> float:
    """Do we disagree with the market, in probability points?

    Distinct from expected value, which answers a different question. EV asks
    whether the price pays enough; this asks whether we think the market is
    wrong. `edge_versus_market()` in the parent module looks like this one but
    is not — it compares against the vig-inclusive price, which makes it
    exactly EV rescaled by the decimal odds, and therefore not independent
    information.
    """
    return estimate.probability - fair_probability


def kelly(estimate: Estimate, odds: int, cap: float = MAX_FRACTION) -> float:
    """Fraction of bankroll to stake, sized at the pessimistic end.

    Zero whenever the estimate is not allowed to size, or when the bottom of
    its interval has no edge at this price. That second condition is the one
    that does the work: a wide interval around a tempting point estimate
    produces no bet, which is the correct answer and the one full Kelly on a
    point estimate never gives.
    """
    if not estimate.can_size:
        return 0.0
    return min(cap, kelly_fraction(estimate.low, odds))


@dataclass(frozen=True)
class Assessment:
    """Everything the UI needs for one priced wager, and nothing it invented."""
    estimate: Estimate
    odds: int
    fair: float
    expected_value: float
    edge: float
    fraction: float
    note: str = ""


def assess(estimate: Estimate, odds: int, fair: float) -> Assessment:
    """Price one wager against one estimate.

    `fair` is required. There is no defensible default: deriving it from
    `odds` alone would mean assuming a margin, and an assumed margin is the
    thing this module exists to stop being invented.
    """
    net = american_to_decimal(odds) - 1.0
    p = estimate.probability
    fraction = kelly(estimate, odds)
    if not estimate.can_size:
        note = ("narrative source — shown for context, not used for sizing")
    elif fraction <= 0.0:
        note = ("no stake: the low end of the interval has no edge at this price")
    else:
        note = f"sized at the low end of the interval ({estimate.low*100:.1f}%)"
    return Assessment(
        estimate=estimate, odds=odds, fair=fair,
        expected_value=p * net - (1.0 - p),
        edge=edge_versus_fair(estimate, fair),
        fraction=fraction, note=note,
    )
