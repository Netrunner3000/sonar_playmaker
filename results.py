"""Match results, for the models that need a history to fit against.

One adapter and a league code covers every registered sport: ESPN publishes a
public scoreboard for each, needs no key, and answers for arbitrary date ranges.
Every path in the registry was checked against it live rather than read off
documentation (`MODELS.md` §10).

Split so the parsing can be tested without a network: `parse_scoreboard()` is
pure and takes a decoded payload, `fetch_range()` is the only function here that
opens a socket. The suite never calls the second one.

Scores only. Nothing here knows what a rating is — `ratings.py` and `poisson.py`
consume `Game` and this module has no opinion about either.
"""

from __future__ import annotations

import datetime as dt
import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from . import Sport, get_sport

BASE = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"

# No User-Agent override, deliberately. ESPN's edge rejects a custom or
# browser-shaped one with 403 and accepts urllib's honest default — measured,
# not guessed: "Mozilla/5.0 (Macintosh) sonar/0.5" and a full Chrome string both
# failed 3/3 where the default passed 3/3. Which is the right way round. Do not
# "fix" a 403 here by pasting in a browser string; that is impersonation, and it
# is also what gets blocked.

#: ESPN caps a response, so a window that returns exactly this many events is
#: assumed truncated and split — see `_fetch_window`.
PAGE_LIMIT = 400
#: A quarter-year per request. Thirty days was twelve times more requests than
#: necessary: a full year of one competition comes back in half a second, and
#: the extra traffic was enough to get rate-limited while fetching a sport that
#: pools fourteen leagues. Density varies hugely between them, so the splitting
#: below is what actually guarantees nothing is lost.
CHUNK_DAYS = 90
TIMEOUT_S = 20.0
#: Be a considerate guest on somebody else's free endpoint.
PAUSE_S = 0.25
RETRIES = 3
RETRY_PAUSE_S = 0.5
#: What to wait after a 403/429 — the endpoint has said slow down.
THROTTLED_PAUSE_S = 2.0
#: How many times a capped window may be halved before giving up.
MAX_SPLITS = 6


@dataclass(frozen=True)
class Game:
    """One finished match. Neutral about sport: goals, points and runs are all
    just numbers, which is why a single rating engine can fit any of them."""
    date: dt.date
    home: str
    away: str
    home_score: int
    away_score: int

    @property
    def margin(self) -> int:
        """Positive when the home side won."""
        return self.home_score - self.away_score

    @property
    def total(self) -> int:
        return self.home_score + self.away_score

    @property
    def result(self) -> float:
        """1.0 home win, 0.5 draw, 0.0 away win — the Elo convention."""
        if self.margin > 0:
            return 1.0
        return 0.0 if self.margin < 0 else 0.5


def parse_scoreboard(payload: dict) -> list[Game]:
    """Every completed game in one scoreboard response.

    Tolerant by design. ESPN's payload carries scheduled, in-progress,
    postponed and cancelled fixtures alongside finished ones, and a malformed
    entry should cost that entry rather than the whole fetch — a rating fitted
    on a short history is recoverable, a crashed fetch loop is not. Unfinished
    games are skipped rather than counted as draws, which is the mistake that
    would quietly bias every rating toward the mean.
    """
    games: list[Game] = []
    for event in payload.get("events") or []:
        for competition in event.get("competitions") or []:
            status = (competition.get("status") or {}).get("type") or {}
            if not status.get("completed"):
                continue
            home = away = None
            for side in competition.get("competitors") or []:
                team = (side.get("team") or {})
                name = (team.get("abbreviation") or team.get("displayName")
                        or team.get("name"))
                try:
                    score = int(side.get("score"))
                except (TypeError, ValueError):
                    name = None
                if not name:
                    continue
                if side.get("homeAway") == "home":
                    home = (name, score)
                elif side.get("homeAway") == "away":
                    away = (name, score)
            if not home or not away:
                continue
            stamp = competition.get("date") or event.get("date") or ""
            try:
                when = dt.datetime.fromisoformat(
                    stamp.replace("Z", "+00:00")).date()
            except ValueError:
                continue
            games.append(Game(when, home[0], away[0], home[1], away[1]))
    return games


def parse_bouts(payload: dict) -> list[Game]:
    """Every finished fight in one scoreboard response.

    MMA is head-to-head but shaped nothing like a league fixture: one *event*
    is a fight card carrying several competitions, there is no home and away,
    and there is no scoreline — only a `winner` flag. So a bout becomes a
    `Game` with 1-0 as its score and the corner ESPN lists first as "home",
    which is arbitrary and harmless: `EloConfig.home_advantage` is zero for a
    sport with no venue effect, and a constant margin of one makes the
    margin-of-victory multiplier a constant too. The model degenerates to plain
    Elo, which is exactly right when there is no margin to learn from.

    A draw or no-contest is skipped rather than recorded as a half-win. They
    are rare, they are usually the result of a foul or an injury, and neither
    tells you anything about who is better.
    """
    games: list[Game] = []
    for event in payload.get("events") or []:
        for bout in event.get("competitions") or []:
            status = (bout.get("status") or {}).get("type") or {}
            if not status.get("completed"):
                continue
            sides = []
            for side in bout.get("competitors") or []:
                athlete = side.get("athlete") or side.get("team") or {}
                name = (athlete.get("displayName") or athlete.get("shortName")
                        or athlete.get("name"))
                if not name:
                    continue
                try:
                    order = int(side.get("order", len(sides) + 1))
                except (TypeError, ValueError):
                    order = len(sides) + 1
                sides.append((order, name, bool(side.get("winner"))))
            if len(sides) != 2:
                continue
            sides.sort()
            (_, first, first_won), (_, second, second_won) = sides
            if first_won == second_won:
                continue                      # draw, no-contest, or unrecorded
            stamp = bout.get("date") or event.get("date") or ""
            try:
                when = dt.datetime.fromisoformat(
                    stamp.replace("Z", "+00:00")).date()
            except ValueError:
                continue
            games.append(Game(when, first, second,
                              1 if first_won else 0, 1 if second_won else 0))
    return games


#: How a sport's results are read off the wire.
PARSERS = {"scores": parse_scoreboard, "winners": parse_bouts}


def _chunks(start: dt.date, end: dt.date, days: int = CHUNK_DAYS):
    cursor = start
    while cursor <= end:
        stop = min(cursor + dt.timedelta(days=days - 1), end)
        yield cursor, stop
        cursor = stop + dt.timedelta(days=1)


def fetch_range(sport: Sport | str, start: dt.date, end: dt.date,
                pause: float = PAUSE_S) -> list[Game]:
    """Completed games between two dates, oldest first.

    The only networked function in Playmaker. A chunk that fails is skipped —
    one bad window should not cost a season — and duplicates across chunk
    boundaries are dropped, because ESPN includes a fixture in every range that
    touches it.
    """
    if isinstance(sport, str):
        sport = get_sport(sport)
    if not sport.has_results:
        raise ValueError(
            f"{sport.key} has no results feed — it is priced and screened like "
            "any other sport, but nothing here rates its competitors")
    if sport.shape == "field":
        raise ValueError(
            f"{sport.key} is a field event: a finishing order across a hundred "
            "competitors, not a result between two sides. `Game` cannot hold it "
            "and no head-to-head model fits it.")
    if start > end:
        raise ValueError("start must not be after end")

    parse = PARSERS[sport.shape]
    seen: set[tuple] = set()
    games: list[Game] = []
    # A sport can be several competitions — international football is fourteen,
    # all feeding one pool of national-team ratings.
    for path in sport.espn_paths:
        for first, last in _chunks(start, end):
            for game in _fetch_window(path, first, last, parse, pause):
                key = (game.date, game.home, game.away)
                if key in seen:
                    continue
                seen.add(key)
                games.append(game)
    games.sort(key=lambda g: (g.date, g.home))
    return games


def _fetch_window(path: str, first: dt.date, last: dt.date, parse,
                  pause: float, depth: int = 0) -> list[Game]:
    """One request, split in half if the response looks capped.

    ESPN truncates a response rather than paginating it, and it does not say
    so. A window that comes back with exactly `PAGE_LIMIT` events has almost
    certainly lost some, and silently short history is worse than slow history:
    it produces a rating that looks fine and is fitted on half the season.
    """
    url = BASE.format(path=path)
    query = urllib.parse.urlencode({
        "dates": f"{first:%Y%m%d}-{last:%Y%m%d}", "limit": PAGE_LIMIT})
    request = urllib.request.Request(f"{url}?{query}")

    payload = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            # 429 and 403 both mean "slow down" here; back off hard rather than
            # hammering a free endpoint that has already said no.
            if exc.code in (403, 429) and attempt + 1 < RETRIES:
                time.sleep(THROTTLED_PAUSE_S * (attempt + 1))
            elif attempt + 1 < RETRIES:
                time.sleep(RETRY_PAUSE_S * (attempt + 1))
        except (urllib.error.URLError, http.client.HTTPException,
                OSError, ValueError, TimeoutError):
            # A long window comes back chunked and is sometimes truncated
            # mid-stream (http.client.IncompleteRead, which is an
            # HTTPException and not an OSError — catching OSError alone lets it
            # through). Retrying costs a second; losing the window costs a
            # season of results.
            if attempt + 1 < RETRIES:
                time.sleep(RETRY_PAUSE_S * (attempt + 1))
    if pause:
        time.sleep(pause)
    if payload is None:
        return []

    events = len(payload.get("events") or [])
    span = (last - first).days
    if events >= PAGE_LIMIT and span >= 1 and depth < MAX_SPLITS:
        middle = first + dt.timedelta(days=span // 2)
        return (_fetch_window(path, first, middle, parse, pause, depth + 1)
                + _fetch_window(path, middle + dt.timedelta(days=1), last,
                                parse, pause, depth + 1))
    return parse(payload)


def fetch_seasons(sport: Sport | str, seasons: int = 3,
                  today: dt.date | None = None) -> list[Game]:
    """The last few years of results — enough history to fit a rating on."""
    today = today or dt.date.today()
    return fetch_range(sport, today - dt.timedelta(days=365 * seasons), today)
