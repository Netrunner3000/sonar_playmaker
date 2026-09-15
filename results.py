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

#: The most ESPN will serve in one response. Measured, because none of it is
#: documented: limit=400 returns 400, limit=1000 returns 1000, and limit=2000
#: returns *twenty-five* — past some threshold the endpoint quietly gives up
#: rather than erroring, which is the worst way for a cap to behave.
PAGE_LIMIT = 1000
TIMEOUT_S = 20.0
#: Be a considerate guest on somebody else's free endpoint.
PAUSE_S = 0.25
RETRIES = 3
RETRY_PAUSE_S = 0.5
#: What to wait after a 403/429 — the endpoint has said slow down.
THROTTLED_PAUSE_S = 2.0


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


def _year_token(year: int) -> str:
    return f"{year:04d}"


def _month_tokens(year: int) -> list[str]:
    return [f"{year:04d}{month:02d}" for month in range(1, 13)]


def _truncated(events: int) -> bool:
    """Whether a response looks capped rather than complete.

    ESPN truncates and says nothing — no next-page cursor, no total count, no
    error. A response holding exactly the limit has almost certainly lost
    fixtures, and silently short history is worse than slow history: it fits a
    rating on half a season and looks perfectly healthy doing it.
    """
    return events >= PAGE_LIMIT


def fetch_range(sport: Sport | str, start: dt.date, end: dt.date,
                pause: float = PAUSE_S) -> list[Game]:
    """Completed results between two dates, oldest first.

    Asks for a whole year at a time and drops to months only when the year
    comes back capped. That matters more than it sounds: a sport here can be
    fourteen competitions, and thirty-day windows made six years of
    international football over a thousand requests. A year per league is
    eighty-four, and the sparse competitions — which is most of them — never
    need the month fallback at all.

    Whole years are fetched and then filtered to the window asked for, because
    ESPN's arbitrary `from-to` ranges are unreliable: identical requests that
    worked earlier in a session have come back 400. The year and month forms
    have not.
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
    for path in sport.espn_paths:
        for year in range(start.year, end.year + 1):
            found, events = _fetch_token(path, _year_token(year), parse, pause)
            if _truncated(events):
                # A dense league — the NBA runs past a thousand fixtures a
                # calendar year. Months are always well inside the cap.
                found = []
                for token in _month_tokens(year):
                    part, _ = _fetch_token(path, token, parse, pause)
                    found.extend(part)
            for game in found:
                if not (start <= game.date <= end):
                    continue
                key = (game.date, game.home, game.away)
                if key in seen:
                    continue
                seen.add(key)
                games.append(game)
    games.sort(key=lambda g: (g.date, g.home))
    return games


def _fetch_token(path: str, token: str, parse, pause: float) -> tuple[list[Game], int]:
    """One request. Returns what parsed, and how many events came back.

    The raw event count is handed back separately because it is what says
    whether the answer was capped — the parsed list is shorter, since scheduled
    and postponed fixtures are dropped, and using it would miss truncation.
    """
    url = BASE.format(path=path)
    query = urllib.parse.urlencode({"dates": token, "limit": PAGE_LIMIT})
    request = urllib.request.Request(f"{url}?{query}")

    payload = None
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            # 429 and 403 mean slow down; back off hard rather than hammering a
            # free endpoint that has already said no. A 400 is this league
            # having nothing for that window, which is normal and not worth
            # retrying.
            if exc.code == 400:
                return [], 0
            if exc.code in (403, 429) and attempt + 1 < RETRIES:
                time.sleep(THROTTLED_PAUSE_S * (attempt + 1))
            elif attempt + 1 < RETRIES:
                time.sleep(RETRY_PAUSE_S * (attempt + 1))
        except (urllib.error.URLError, http.client.HTTPException,
                OSError, ValueError, TimeoutError):
            # A long window comes back chunked and is sometimes truncated
            # mid-stream (http.client.IncompleteRead, which is an
            # HTTPException and *not* an OSError — catching OSError alone lets
            # it through). Retrying costs a second; losing the window costs a
            # season.
            if attempt + 1 < RETRIES:
                time.sleep(RETRY_PAUSE_S * (attempt + 1))
    if pause:
        time.sleep(pause)
    if payload is None:
        return [], 0
    return parse(payload), len(payload.get("events") or [])


def fetch_seasons(sport: Sport | str, seasons: int = 3,
                  today: dt.date | None = None) -> list[Game]:
    """The last few years of results — enough history to fit a rating on."""
    today = today or dt.date.today()
    return fetch_range(sport, today - dt.timedelta(days=365 * seasons), today)
