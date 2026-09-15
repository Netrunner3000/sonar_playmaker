"""Dixon-Coles: the scoreline model, and the strongest result in this field.

Elo gives one number — how likely the home side is to win. A market wants more
than that: the draw, the total, both-teams-to-score, the handicap. All of those
fall out of a *distribution over scorelines*, which is what this builds.

Dixon & Coles (1997) is the landmark, still the reference implementation for
football thirty years on, and it is three ideas (`MODELS.md` §4):

1. **Attack and defence per team.** Home goals are Poisson with
   `lambda = gamma * attack[home] * defence[away]`, away goals Poisson with
   `mu = attack[away] * defence[home]`. `gamma` is the home advantage.
2. **A low-score correction.** Independent Poissons systematically underpredict
   0-0 and 1-1. The `tau` term reweights the four lowest scorelines, and `rho`
   is how much. This is the part everyone leaves out and then wonders why their
   draw prices are wrong.
3. **Time decay.** Matches are weighted `exp(-xi * t)`, with **xi = 0.0065 per
   half-week** — a half-life of about a year — the value Dixon and Coles found
   by maximising out-of-sample predictive log-likelihood. Last season counts,
   but less.

Fitted with no numeric libraries, because SONAR ships stdlib-only. That is not a
compromise here: with only team indicators and a log link, the maximum-likelihood
attack and defence strengths have a closed-form update given the others, so
iterating them to convergence *is* the MLE rather than an approximation of it.
Only `rho` needs a search, and it is one bounded parameter.

Like everything else in Playmaker, a probability from here reaches `staking`
only through `scoring.calibrated_estimate()`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .results import Game

#: Dixon & Coles' fitted decay, per half-week. Half-life about one year.
XI_PER_HALF_WEEK = 0.0065
HALF_WEEK_DAYS = 3.5

#: Scorelines are summed up to this many goals per side. Beyond it the Poisson
#: tail is worth less than the rounding.
MAX_GOALS = 12

MAX_ITERATIONS = 200
TOLERANCE = 1e-10
#: Dixon and Coles constrain rho so tau stays positive; this is comfortably
#: inside the admissible region for realistic scoring rates.
RHO_BOUNDS = (-0.2, 0.2)


def decay_weight(days_ago: float, xi: float = XI_PER_HALF_WEEK) -> float:
    """`exp(-xi * t)`, with t in half-weeks — the paper's own units."""
    return math.exp(-xi * (days_ago / HALF_WEEK_DAYS))


def tau(home_goals: int, away_goals: int, lam: float, mu: float,
        rho: float) -> float:
    """Dixon and Coles' dependence correction for the four lowest scorelines.

    Independent Poissons get 0-0 and 1-1 wrong in a way that matters for exactly
    the market people bet most — the draw. Everything above 1-1 is left alone.
    """
    if home_goals == 0 and away_goals == 0:
        return 1.0 - lam * mu * rho
    if home_goals == 0 and away_goals == 1:
        return 1.0 + lam * rho
    if home_goals == 1 and away_goals == 0:
        return 1.0 + mu * rho
    if home_goals == 1 and away_goals == 1:
        return 1.0 - rho
    return 1.0


def _poisson(k: int, rate: float) -> float:
    if rate <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-rate + k * math.log(rate) - math.lgamma(k + 1))


@dataclass
class DixonColes:
    """Fitted attack and defence strengths, a home effect and the correction."""
    attack: dict[str, float] = field(default_factory=dict)
    defence: dict[str, float] = field(default_factory=dict)
    home_advantage: float = 1.35
    rho: float = 0.0
    games: int = 0

    def teams(self) -> list[str]:
        return sorted(self.attack)

    def rates(self, home: str, away: str) -> tuple[float, float]:
        """Expected goals for each side. The whole model, in two numbers."""
        lam = self.home_advantage * self.attack.get(home, 1.0) * self.defence.get(away, 1.0)
        mu = self.attack.get(away, 1.0) * self.defence.get(home, 1.0)
        return max(lam, 1e-9), max(mu, 1e-9)

    def scoreline_matrix(self, home: str, away: str,
                         max_goals: int = MAX_GOALS) -> list[list[float]]:
        """P(home scores x, away scores y), normalised over the grid.

        The tau correction is not a probability distribution on its own, so the
        grid is renormalised. Truncating at `max_goals` loses a little tail mass
        and the same renormalisation absorbs it.
        """
        lam, mu = self.rates(home, away)
        grid = [[tau(x, y, lam, mu, self.rho) * _poisson(x, lam) * _poisson(y, mu)
                 for y in range(max_goals + 1)]
                for x in range(max_goals + 1)]
        total = sum(sum(row) for row in grid)
        if total <= 0:
            return grid
        return [[cell / total for cell in row] for row in grid]

    # -- the markets a scoreline distribution answers --------------------- #
    def outcome_probabilities(self, home: str, away: str) -> tuple[float, float, float]:
        """Home win, draw, away win."""
        grid = self.scoreline_matrix(home, away)
        home_win = sum(grid[x][y] for x in range(len(grid)) for y in range(x))
        draw = sum(grid[i][i] for i in range(len(grid)))
        return home_win, draw, max(0.0, 1.0 - home_win - draw)

    def over_probability(self, home: str, away: str, line: float) -> float:
        """P(total goals > line). A line of 2.5 cannot be pushed; 2.0 can."""
        grid = self.scoreline_matrix(home, away)
        return sum(grid[x][y] for x in range(len(grid)) for y in range(len(grid))
                   if x + y > line)

    def both_score_probability(self, home: str, away: str) -> float:
        grid = self.scoreline_matrix(home, away)
        return sum(grid[x][y] for x in range(1, len(grid))
                   for y in range(1, len(grid)))

    def most_likely_score(self, home: str, away: str) -> tuple[int, int, float]:
        grid = self.scoreline_matrix(home, away)
        best = max(((x, y, grid[x][y])
                    for x in range(len(grid)) for y in range(len(grid))),
                   key=lambda t: t[2])
        return best


def _weights(games: list[Game], xi: float) -> list[float]:
    if not games:
        return []
    latest = max(g.date for g in games)
    return [decay_weight((latest - g.date).days, xi) for g in games]


def fit(games: list[Game], xi: float = XI_PER_HALF_WEEK,
        fit_rho: bool = True) -> DixonColes:
    """Maximum-likelihood attack, defence, home effect and rho.

    Attack and defence are iterated to a fixed point. With a log link and team
    indicators only, each parameter's MLE given the others is a ratio of
    weighted goals to weighted opportunity — so the fixed point *is* the
    maximum, not a heuristic that lands near it. `rho` is then found by a
    bounded search on the corrected log-likelihood, which is where the
    dependence between the two scores actually lives.
    """
    model = DixonColes(games=len(games))
    if not games:
        return model

    weights = _weights(games, xi)
    teams = sorted({g.home for g in games} | {g.away for g in games})
    model.attack = {t: 1.0 for t in teams}
    model.defence = {t: 1.0 for t in teams}

    weighted_games = list(zip(games, weights))
    total_weight = sum(weights) or 1.0
    mean_goals = sum(w * g.total for g, w in weighted_games) / (2.0 * total_weight)
    mean_goals = max(mean_goals, 1e-6)

    # Work in goals-per-game units so the strengths stay near 1.0.
    scored: dict[str, float] = {t: 0.0 for t in teams}
    conceded: dict[str, float] = {t: 0.0 for t in teams}
    for g, w in weighted_games:
        scored[g.home] += w * g.home_score
        scored[g.away] += w * g.away_score
        conceded[g.home] += w * g.away_score
        conceded[g.away] += w * g.home_score

    home_goals = sum(w * g.home_score for g, w in weighted_games)
    away_goals = sum(w * g.away_score for g, w in weighted_games)
    model.home_advantage = (home_goals / away_goals) if away_goals > 0 else 1.0

    for _ in range(MAX_ITERATIONS):
        shift = 0.0
        for team in teams:
            opportunity = 0.0
            for g, w in weighted_games:
                if g.home == team:
                    opportunity += w * model.home_advantage * model.defence[g.away] * mean_goals
                elif g.away == team:
                    opportunity += w * model.defence[g.home] * mean_goals
            if opportunity > 0:
                new = scored[team] / opportunity
                shift = max(shift, abs(new - model.attack[team]))
                model.attack[team] = new
        for team in teams:
            opportunity = 0.0
            for g, w in weighted_games:
                if g.home == team:
                    opportunity += w * model.attack[g.away] * mean_goals
                elif g.away == team:
                    opportunity += w * model.home_advantage * model.attack[g.home] * mean_goals
            if opportunity > 0:
                new = conceded[team] / opportunity
                shift = max(shift, abs(new - model.defence[team]))
                model.defence[team] = new
        if shift < TOLERANCE:
            break

    # Fold the league's scoring level into the strengths so `rates()` returns
    # goals rather than multiples of an average nobody carries around.
    scale = math.sqrt(mean_goals)
    for team in teams:
        model.attack[team] *= scale
        model.defence[team] *= scale

    if fit_rho:
        model.rho = _fit_rho(model, weighted_games)
    return model


def _log_likelihood(model: DixonColes, weighted_games, rho: float) -> float:
    total = 0.0
    for g, w in weighted_games:
        lam, mu = model.rates(g.home, g.away)
        correction = tau(g.home_score, g.away_score, lam, mu, rho)
        if correction <= 0:
            return -math.inf
        total += w * (math.log(correction)
                      + math.log(max(_poisson(g.home_score, lam), 1e-300))
                      + math.log(max(_poisson(g.away_score, mu), 1e-300)))
    return total


def _fit_rho(model: DixonColes, weighted_games) -> float:
    """Golden-section search over one bounded parameter."""
    low, high = RHO_BOUNDS
    phi = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = low, high
    c, d = b - phi * (b - a), a + phi * (b - a)
    fc, fd = (_log_likelihood(model, weighted_games, c),
              _log_likelihood(model, weighted_games, d))
    for _ in range(60):
        if fc > fd:
            b, d, fd = d, c, fc
            c = b - phi * (b - a)
            fc = _log_likelihood(model, weighted_games, c)
        else:
            a, c, fc = c, d, fd
            d = a + phi * (b - a)
            fd = _log_likelihood(model, weighted_games, d)
        if abs(b - a) < 1e-6:
            break
    return (a + b) / 2.0
