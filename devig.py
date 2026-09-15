"""Removing the bookmaker's margin, and finding where books disagree.

Two jobs, and the second is the one with the track record.

**Devigging.** A book's prices imply probabilities that sum to more than 1;
the excess is its margin. Turning those back into probabilities requires
choosing *how* the margin is distributed across outcomes, and the choice
matters: comparative studies (Clarke 2017) rank the obvious method —
proportional, "multiplicative" — last on every measure, because it assumes the
margin is spread evenly in proportion to price. Real books load more of it onto
longshots, which is the favourite-longshot bias. Three methods are implemented
here so the difference is visible rather than assumed.

**Consensus.** Kaunitz, Zhong & Kreiner (2017) did not forecast anything. They
devigged many books' prices for the same market, took the consensus, and bet
only where one book was a significant outlier against it — profitable across a
ten-year simulation and five months of real money. `screen()` is that
arithmetic. Their other finding belongs next to it: the books limited the
winning accounts, so the edge is real and the capacity is small.

Nothing here places a bet. It prices one.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from . import american_to_decimal


# Below this the solvers stop; well past the precision of any posted price.
TOLERANCE = 1e-12
MAX_ITERATIONS = 200

METHODS = ("multiplicative", "power", "shin")
DEFAULT_METHOD = "shin"


def raw_probabilities(odds: tuple[int, ...]) -> tuple[float, ...]:
    """Each outcome's implied probability before the margin is removed."""
    if len(odds) < 2:
        raise ValueError("a market needs at least two outcomes")
    return tuple(1.0 / american_to_decimal(o) for o in odds)


def booksum(odds: tuple[int, ...]) -> float:
    """Sum of raw implied probabilities. 1.05 means a 5% margin."""
    return sum(raw_probabilities(odds))


def overround(odds: tuple[int, ...]) -> float:
    """The margin itself, as a fraction. -110/-110 -> 0.0476."""
    return booksum(odds) - 1.0


# --------------------------------------------------------------------------- #
# The three methods
# --------------------------------------------------------------------------- #
def multiplicative(odds: tuple[int, ...]) -> tuple[float, ...]:
    """Scale every outcome by the same factor until they sum to 1.

    The naive method, and the worst performing one in every comparison I could
    find. It takes the margin from each outcome in proportion to its own price,
    which understates favourites and overstates longshots relative to the
    methods below. Kept because it is what the rest of the industry quotes, and
    because seeing the gap is the argument for not using it.
    """
    raw = raw_probabilities(odds)
    total = sum(raw)
    if total <= 0:
        raise ValueError("degenerate odds")
    return tuple(p / total for p in raw)


def power(odds: tuple[int, ...]) -> tuple[float, ...]:
    """Clarke's method: find k with sum(q_i ** k) == 1.

    Raising a probability in (0,1) to k > 1 shrinks a longshot proportionally
    far more than a favourite, which is the correction the favourite-longshot
    bias calls for. Clarke found this best or equal-best of the four he tested.
    """
    raw = raw_probabilities(odds)
    if any(q >= 1.0 for q in raw):
        # An outcome priced at or beyond certainty; no exponent can fix it.
        return multiplicative(odds)

    def total(k: float) -> float:
        return sum(q ** k for q in raw)

    if total(1.0) <= 1.0:
        return multiplicative(odds)      # no margin to remove

    lo, hi = 1.0, 2.0
    while total(hi) > 1.0 and hi < 512.0:
        hi *= 2.0
    for _ in range(MAX_ITERATIONS):
        mid = (lo + hi) / 2.0
        if total(mid) > 1.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < TOLERANCE:
            break
    k = (lo + hi) / 2.0
    out = tuple(q ** k for q in raw)
    scale = sum(out)
    return tuple(p / scale for p in out)          # tidy the last ulp


def shin(odds: tuple[int, ...]) -> tuple[float, ...]:
    """Shin's method: solve for the share of insider money, z.

    Shin (1992) models the margin as the book's protection against bettors who
    know something it does not. Backing out that share of informed money and
    removing it leaves probabilities that are generally the best calibrated of
    the three, and it corrects the favourite-longshot bias for the same reason
    the power method does — by construction rather than by fitting an exponent.

        p_i = (sqrt(z^2 + 4(1-z) q_i^2 / S) - z) / (2(1-z))

    with z chosen so the p_i sum to 1. p_i falls monotonically in z, from
    sqrt(S) at z=0 down to sum(q_i^2)/S, so a root exists whenever that floor
    is below 1 — and bisection finds it without a derivative.
    """
    raw = raw_probabilities(odds)
    total_raw = sum(raw)
    if total_raw <= 1.0:
        return multiplicative(odds)

    squares = [q * q / total_raw for q in raw]
    if sum(squares) >= 1.0:
        # No admissible z; the book is too lopsided for the model. Fall back
        # rather than return something the model does not actually support.
        return power(odds)

    def probs(z: float) -> list[float]:
        if z <= 0.0:
            return [math.sqrt(a) for a in squares]
        rest = 1.0 - z
        return [(math.sqrt(z * z + 4.0 * rest * a) - z) / (2.0 * rest)
                for a in squares]

    lo, hi = 0.0, 1.0 - 1e-12
    for _ in range(MAX_ITERATIONS):
        mid = (lo + hi) / 2.0
        if sum(probs(mid)) > 1.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < TOLERANCE:
            break
    out = probs((lo + hi) / 2.0)
    scale = sum(out)
    return tuple(p / scale for p in out)


_METHODS = {"multiplicative": multiplicative, "power": power, "shin": shin}


def devig(odds: tuple[int, ...], method: str = DEFAULT_METHOD) -> tuple[float, ...]:
    """Fair probabilities for one book's prices. Shin unless told otherwise."""
    try:
        fn = _METHODS[method]
    except KeyError:
        raise ValueError(f"unknown devig method: {method!r}; "
                         f"expected one of {', '.join(METHODS)}") from None
    return fn(odds)


def all_methods(odds: tuple[int, ...]) -> dict[str, tuple[float, ...]]:
    """Every method's answer for the same prices — the spread is the argument."""
    return {name: fn(odds) for name, fn in _METHODS.items()}


def method_spread(odds: tuple[int, ...], index: int) -> tuple[float, float]:
    """Lowest and highest probability the three methods give one outcome.

    Not a confidence interval in any formal sense — it is the disagreement
    between three defensible ways of removing the same margin. That
    disagreement is real uncertainty about the fair price, and it is the only
    uncertainty available from a single book.
    """
    values = [probs[index] for probs in all_methods(odds).values()]
    return min(values), max(values)


# --------------------------------------------------------------------------- #
# Consensus across books — the Kaunitz arithmetic
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Quote:
    """One book's prices for every outcome of one market, in a fixed order."""
    book: str
    odds: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(self.odds) < 2:
            raise ValueError(f"{self.book}: a market needs at least two outcomes")


@dataclass(frozen=True)
class Consensus:
    probability: tuple[float, ...]
    dispersion: tuple[float, ...]    # sd across books, per outcome
    books: int

    def standard_error(self, index: int) -> float:
        """Uncertainty of the consensus itself, not of the outcome."""
        if self.books < 2:
            return float("nan")
        return self.dispersion[index] / math.sqrt(self.books)


def consensus(quotes: list[Quote], method: str = DEFAULT_METHOD,
              exclude: str | None = None) -> Consensus:
    """Mean devigged probability per outcome, across books.

    `exclude` drops one book by name. Always exclude the book you are pricing
    against: leaving it in lets its own outlier price pull the consensus
    toward itself, which is exactly the number you were trying to test it
    against.
    """
    rows = [q for q in quotes if q.book != exclude]
    if not rows:
        raise ValueError("no books left after exclusion")
    width = len(rows[0].odds)
    if any(len(q.odds) != width for q in rows):
        raise ValueError("books disagree on how many outcomes this market has")

    fair = [devig(q.odds, method) for q in rows]
    means, sds = [], []
    for i in range(width):
        column = [f[i] for f in fair]
        mean = sum(column) / len(column)
        means.append(mean)
        if len(column) < 2:
            sds.append(0.0)
        else:
            var = sum((c - mean) ** 2 for c in column) / (len(column) - 1)
            sds.append(math.sqrt(var))
    return Consensus(tuple(means), tuple(sds), len(rows))


@dataclass(frozen=True)
class Opportunity:
    book: str
    outcome: int
    odds: int
    offered: float        # probability this price implies, margin included
    fair: float           # leave-one-out consensus probability
    edge: float           # fair - offered, in probability points
    expected_value: float  # per unit staked
    z: float              # how far out the price is, in consensus SEs

    @property
    def is_outlier(self) -> bool:
        return self.edge > 0.0 and (math.isnan(self.z) or abs(self.z) >= 2.0)


def screen(quotes: list[Quote], method: str = DEFAULT_METHOD,
           min_edge: float = 0.0) -> list[Opportunity]:
    """Every book/outcome whose price beats the other books' consensus.

    This is the Kaunitz strategy and not a forecast: it says nothing about who
    wins, only that one book is pricing an outcome differently from its peers.
    The consensus for each row is computed with that row's own book left out.

    Sorted by expected value, best first. An empty list is the common and
    correct answer.
    """
    if len(quotes) < 3:
        raise ValueError("a consensus needs at least three books "
                         "(two, minus the one being priced, is not a consensus)")
    found = []
    for quote in quotes:
        peers = consensus(quotes, method, exclude=quote.book)
        for i, price in enumerate(quote.odds):
            offered = 1.0 / american_to_decimal(price)
            fair = peers.probability[i]
            edge = fair - offered
            if edge < min_edge:
                continue
            se = peers.standard_error(i)
            z = (fair - offered) / se if se and se > 0 else float("nan")
            net = american_to_decimal(price) - 1.0
            found.append(Opportunity(
                book=quote.book, outcome=i, odds=price, offered=offered,
                fair=fair, edge=edge, expected_value=fair * net - (1.0 - fair),
                z=z,
            ))
    found.sort(key=lambda o: o.expected_value, reverse=True)
    return found


# --------------------------------------------------------------------------- #
# Reading prices the way a person writes them down
# --------------------------------------------------------------------------- #
_ODDS = re.compile(r'^[+-]?\d{2,5}$')


def parse_quotes(text: str) -> list[Quote]:
    """One book per line: a name, then that book's price for each outcome.

        DraftKings  -150  +130
        Pinnacle    -148   128
        BetMGM,     -150, +210

    Book names may contain spaces, so the odds are taken from the right — every
    trailing token that reads as a price — and whatever precedes them is the
    name. Blank lines and anything after a `#` are ignored.

    Raises on a line that cannot be read rather than skipping it: a price
    silently dropped from a consensus is a wrong answer that looks like a right
    one.
    """
    quotes: list[Quote] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip().rstrip(",")
        if not line:
            continue
        tokens = [t for t in line.replace(",", " ").split() if t]
        prices: list[int] = []
        while tokens and _ODDS.match(tokens[-1]):
            prices.insert(0, int(tokens.pop().lstrip("+")))
        name = " ".join(tokens).strip()
        if not name:
            raise ValueError(f"line {number}: no book name before the prices")
        if len(prices) < 2:
            raise ValueError(
                f"line {number}: {name!r} needs a price for every outcome "
                f"(found {len(prices)}) — a margin cannot be removed from one side")
        quotes.append(Quote(name, tuple(prices)))
    return quotes
