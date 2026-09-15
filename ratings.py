"""Elo, and the Pythagorean cross-check.

`MODELS.md` §1 is why this is Elo and not something fancier: across the
benchmarks surveyed there, a tuned Elo lands within about 1.65 percentage points
of deep models, and the reviewers' own summary is that *Elo alone captures most
of what can be captured from universally available pre-match features*. The
market still sits above all of them. So the job here is to implement the
well-tested thing carefully, not to invent.

Three refinements separate a working Elo from the textbook one, all from
FiveThirtyEight's published models (§3):

* **margin of victory** — a thirty-point win is more evidence than a one-point
  win, so the update scales with `log(margin + 1)`;
* **autocorrelation correction** — the `elo_diff` term in that same expression.
  Favourites run up the score, so without it strong teams' ratings inflate
  without bound;
* **between-season regression** — carry roughly two thirds of a rating into the
  next season, or last year's champion starts this one overrated.

`pythagorean()` is the independent second opinion (§5): a team's future win rate
is predicted better by its points scored and allowed than by its actual record,
because that strips out luck in close games.

Nothing here is allowed to size a bet on its own. A probability from this module
reaches `staking` only through `scoring.calibrated_estimate()`, which refuses
until the model has been measured out of sample — the same rule the rest of
SONAR follows for its own score.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field, replace

from .results import Game


@dataclass(frozen=True)
class EloConfig:
    """Tuning for one sport. Defaults are FiveThirtyEight's NFL settings."""
    k: float = 20.0
    #: Home advantage in rating points. 65 is roughly 2.5 NFL points.
    home_advantage: float = 65.0
    #: Denominator constant in the margin multiplier — 2.2 for NFL, 2.05 NHL.
    autocorrelation: float = 2.2
    #: Fraction of a rating pulled back to base between seasons.
    season_regression: float = 1.0 / 3.0
    base: float = 1500.0
    scale: float = 400.0
    #: Draws are possible. Where they are not, a 0.5 result is a data error.
    draws: bool = False


#: Per-sport settings. Where a published value exists it is used and named;
#: where one does not, the NFL default stands in and is marked as such so the
#: next person does not mistake a guess for a finding.
CONFIGS: dict[str, EloConfig] = {
    # FiveThirtyEight NFL: K=20, 2.2 autocorrelation, a third regressed.
    "nfl": EloConfig(k=20.0, home_advantage=65.0, autocorrelation=2.2),
    # FiveThirtyEight NBA: K=20, ~100 points of home court.
    "nba": EloConfig(k=20.0, home_advantage=100.0, autocorrelation=2.2),
    # FiveThirtyEight NHL: 2.05 autocorrelation; ties are possible in regulation.
    "nhl": EloConfig(k=6.0, home_advantage=50.0, autocorrelation=2.05, draws=True),
    # Baseball moves ratings slowly — one game is a small sample of a long season.
    "mlb": EloConfig(k=4.0, home_advantage=24.0, autocorrelation=2.2),
    # Soccer: draws are priced, and club Elo conventionally uses a low K.
    "epl": EloConfig(k=20.0, home_advantage=65.0, autocorrelation=2.2, draws=True),
    "ucl": EloConfig(k=20.0, home_advantage=55.0, autocorrelation=2.2, draws=True),
    # Unpublished defaults below — NFL settings standing in, not measured.
    "ncaab": EloConfig(k=20.0, home_advantage=100.0, autocorrelation=2.2),
    "ufc": EloConfig(k=24.0, home_advantage=0.0, autocorrelation=2.2),
    "atp": EloConfig(k=24.0, home_advantage=0.0, autocorrelation=2.2),
}

#: Sports whose Elo settings are a stand-in rather than a published figure.
UNTUNED = frozenset({"ncaab", "ufc", "atp"})


def config_for(sport_key: str) -> EloConfig:
    return CONFIGS.get(sport_key, EloConfig())


@dataclass
class Rating:
    value: float
    games: int = 0
    scored: int = 0
    allowed: int = 0


@dataclass
class Table:
    """Ratings for one league, and enough bookkeeping to say how sure it is."""
    config: EloConfig
    ratings: dict[str, Rating] = field(default_factory=dict)
    games: int = 0

    def get(self, team: str) -> Rating:
        return self.ratings.setdefault(team, Rating(self.config.base))

    def value(self, team: str) -> float:
        return self.get(team).value

    def ranked(self) -> list[tuple[str, Rating]]:
        return sorted(self.ratings.items(), key=lambda kv: -kv[1].value)


def expected(home_rating: float, away_rating: float,
             config: EloConfig) -> float:
    """Probability the home side wins, before any draw is carved out.

    The logistic Elo curve, which is Bradley-Terry written recursively.
    """
    diff = home_rating + config.home_advantage - away_rating
    return 1.0 / (1.0 + 10.0 ** (-diff / config.scale))


def margin_multiplier(margin: int, winner_diff: float,
                      config: EloConfig) -> float:
    """FiveThirtyEight's margin-of-victory scaling, with the autocorrelation fix.

        log(|margin| + 1) * (C / (0.001 * winner_elo_diff + C))

    `winner_diff` is the winner's rating advantage *including* home field. It
    sits in the denominator so that a favourite thrashing an underdog moves its
    rating less than the same scoreline the other way round — without which
    good teams' ratings run away, since they are the ones who get to run up
    scores.

    A draw gives `|margin| = 0`, and `max(margin, 1) + 1` keeps the update
    alive at `log(2)` rather than silently discarding the game.
    """
    c = config.autocorrelation
    return math.log(max(abs(margin), 1) + 1.0) * (c / (0.001 * winner_diff + c))


def update(table: Table, game: Game) -> tuple[float, float]:
    """Apply one game. Returns the pre-game ratings, for out-of-sample scoring.

    Read the ratings *before* updating and hand them back, so a caller walking a
    season chronologically can score each prediction against a table that has
    not yet seen the result. Scoring a model on data it has already fitted is
    the single easiest way to invent an edge that is not there.
    """
    home, away = table.get(game.home), table.get(game.away)
    before = (home.value, away.value)

    prediction = expected(home.value, away.value, table.config)
    diff = home.value + table.config.home_advantage - away.value
    winner_diff = diff if game.margin > 0 else -diff
    if game.margin == 0:
        winner_diff = abs(diff)
    shift = (table.config.k
             * margin_multiplier(game.margin, winner_diff, table.config)
             * (game.result - prediction))

    home.value += shift
    away.value -= shift
    for side, own, other in ((home, game.home_score, game.away_score),
                             (away, game.away_score, game.home_score)):
        side.games += 1
        side.scored += own
        side.allowed += other
    table.games += 1
    return before


def regress_to_mean(table: Table) -> None:
    """Pull every rating back toward the base between seasons."""
    pull = table.config.season_regression
    for rating in table.ratings.values():
        rating.value = table.config.base + (rating.value - table.config.base) * (1.0 - pull)


def fit(games: list[Game], sport_key: str = "",
        season_gap_days: int = 60) -> Table:
    """Walk a history in order, regressing whenever there is a long gap.

    The gap heuristic stands in for a season calendar the results feed does not
    publish: two months without a fixture is an off-season in every league here.
    It is a heuristic and is named as one — a league that takes a long
    mid-season break would be regressed when it should not be.
    """
    table = Table(config_for(sport_key))
    previous = None
    for game in sorted(games, key=lambda g: g.date):
        if previous and (game.date - previous).days >= season_gap_days:
            regress_to_mean(table)
        update(table, game)
        previous = game.date
    return table


# --------------------------------------------------------------------------- #
# Turning a rating difference into the probabilities a market prices
# --------------------------------------------------------------------------- #
def outcome_probabilities(table: Table, home: str, away: str,
                          draw_rate: float | None = None) -> tuple[float, ...]:
    """Home win / draw / away win, or home / away where a draw cannot happen.

    The draw is carved out of the middle of the distribution rather than
    modelled: Elo produces one number, and one number cannot say how often two
    evenly matched sides finish level. Where draws matter, `poisson.py` models
    the scoreline properly and should be preferred — this exists so that a
    three-way market is not simply unsupported.
    """
    home_win = expected(table.value(home), table.value(away), table.config)
    if not table.config.draws:
        return (home_win, 1.0 - home_win)
    if draw_rate is None:
        draw_rate = observed_draw_rate(table)
    # Take the draw proportionally from both sides, so the favourite stays
    # favoured and the pair still sums to one.
    keep = 1.0 - draw_rate
    return (home_win * keep, draw_rate, (1.0 - home_win) * keep)


def observed_draw_rate(table: Table, default: float = 0.25) -> float:
    """How often this league actually draws — measured, not assumed."""
    return getattr(table, "draw_rate", default)


def pythagorean(scored: float, allowed: float, exponent: float) -> float:
    """Bill James' expected win rate from points for and against.

    Predicts a team's *future* win rate better than its current record does,
    because a record includes luck in close games and this does not.
    """
    if scored <= 0 and allowed <= 0:
        return 0.5
    numerator = scored ** exponent
    denominator = numerator + allowed ** exponent
    return numerator / denominator if denominator else 0.5


#: Published exponents only. A sport absent here has no measured value that I
#: could source, and guessing one would make the cross-check worse than useless.
PYTHAGOREAN_EXPONENTS: dict[str, float] = {
    "mlb": 1.83,     # modern baseball
    "nfl": 2.37,     # Football Outsiders
    "nba": 13.91,    # Morey
    "nhl": 2.15,
}


def pythagorean_for(table: Table, team: str, sport_key: str) -> float | None:
    """The cross-check, or None when this sport has no sourced exponent."""
    exponent = PYTHAGOREAN_EXPONENTS.get(sport_key)
    if exponent is None:
        return None
    rating = table.ratings.get(team)
    if rating is None or rating.games == 0:
        return None
    return pythagorean(rating.scored, rating.allowed, exponent)
