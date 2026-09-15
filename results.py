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

#: ESPN pages long ranges; ask for plenty and walk the range in chunks anyway.
PAGE_LIMIT = 400
CHUNK_DAYS = 30
TIMEOUT_S = 20.0
#: Be a considerate guest on somebody else's free endpoint.
PAUSE_S = 0.25
RETRIES = 3
RETRY_PAUSE_S = 0.5


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
    if not sport.espn_path:
        raise ValueError(f"{sport.key} has no results league configured")
    if start > end:
        raise ValueError("start must not be after end")

    seen: set[tuple] = set()
    games: list[Game] = []
    url = BASE.format(path=sport.espn_path)
    for first, last in _chunks(start, end):
        query = urllib.parse.urlencode({
            "dates": f"{first:%Y%m%d}-{last:%Y%m%d}", "limit": PAGE_LIMIT})
        request = urllib.request.Request(f"{url}?{query}")
        payload = None
        for attempt in range(RETRIES):
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                    payload = json.load(response)
                break
            except (urllib.error.URLError, http.client.HTTPException,
                    OSError, ValueError, TimeoutError):
                # A long window comes back chunked and is sometimes truncated
                # mid-stream (http.client.IncompleteRead, which is an
                # HTTPException and not an OSError — catching OSError alone
                # lets it through). Retrying costs a second; losing the window
                # costs a month of results.
                if attempt + 1 < RETRIES:
                    time.sleep(RETRY_PAUSE_S * (attempt + 1))
        if payload is None:
            continue
        for game in parse_scoreboard(payload):
            key = (game.date, game.home, game.away)
            if key in seen:
                continue
            seen.add(key)
            games.append(game)
        if pause:
            time.sleep(pause)
    games.sort(key=lambda g: (g.date, g.home))
    return games


def fetch_seasons(sport: Sport | str, seasons: int = 3,
                  today: dt.date | None = None) -> list[Game]:
    """The last few years of results — enough history to fit a rating on."""
    today = today or dt.date.today()
    return fetch_range(sport, today - dt.timedelta(days=365 * seasons), today)
