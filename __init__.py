"""Sports prop-bet analysis — the Playmaker tool.

Ported from Sentinel's NFL agent (which was never a standalone project — it is
part of SONAR, the betting tool), generalised so a second sport is a registry
entry rather than a rewrite. NFL is the only sport implemented today; the shape
below is what an NBA or soccer entry has to fill in.

The split matches the rest of SONAR: the arithmetic here is deterministic and
testable (odds conversion, implied probability, expected value, Kelly), and the
LLM is asked only for the narrative read on top of numbers it did not invent.
Nothing in this module places a bet — it evaluates one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# --------------------------------------------------------------------------- #
# The sport registry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PropType:
    key: str
    label: str
    unit: str


@dataclass(frozen=True)
class Sport:
    key: str
    name: str
    prop_types: tuple[PropType, ...]
    # What "game context" means here — shown as the placeholder on the form and
    # named in the prompt so the model asks for the right missing inputs.
    context_hint: str
    period_label: str = "game"


NFL = Sport(
    key="nfl",
    name="NFL",
    period_label="game",
    context_hint="Opponent, week, home/away, weather, injuries, pace",
    prop_types=(
        PropType("pass_yds", "Passing yards", "yards"),
        PropType("rush_yds", "Rushing yards", "yards"),
        PropType("rec_yds", "Receiving yards", "yards"),
        PropType("receptions", "Receptions", "catches"),
        PropType("pass_tds", "Passing touchdowns", "TDs"),
        PropType("anytime_td", "Anytime touchdown", "TDs"),
        PropType("team_total", "Team total points", "points"),
        PropType("spread", "Spread", "points"),
        PropType("game_total", "Game total", "points"),
    ),
)

SPORTS: dict[str, Sport] = {NFL.key: NFL}


def list_sports() -> list[Sport]:
    return list(SPORTS.values())


def get_sport(key: str) -> Sport:
    try:
        return SPORTS[key]
    except KeyError:
        raise ValueError(f"unknown sport: {key!r}") from None


# --------------------------------------------------------------------------- #
# Odds arithmetic — deterministic, and the part worth testing
# --------------------------------------------------------------------------- #
def american_to_decimal(odds: int) -> float:
    """-110 -> 1.909, +150 -> 2.5. Total return per unit staked, stake included."""
    if odds == 0:
        raise ValueError("american odds cannot be 0")
    return 1.0 + (odds / 100.0 if odds > 0 else 100.0 / abs(odds))


def implied_probability(odds: int) -> float:
    """The break-even win rate the price demands, before removing the vig.

    -110 -> 0.5238: two sides at -110 sum to 1.0476, and that 4.76% excess is
    the book's margin, not a probability.
    """
    return 1.0 / american_to_decimal(odds)


def remove_vig(odds_a: int, odds_b: int) -> tuple[float, float]:
    """Both sides' implied probabilities, normalised to sum to 1.

    The naive implied probability of each side overstates it; comparing a model
    against the raw number credits you with an edge the vig invented.
    """
    p_a, p_b = implied_probability(odds_a), implied_probability(odds_b)
    total = p_a + p_b
    if total <= 0:
        raise ValueError("degenerate odds pair")
    return p_a / total, p_b / total


def expected_value(win_prob: float, odds: int, stake: float = 1.0) -> float:
    """EV in units staked. Positive means the price is better than the estimate."""
    if not 0.0 <= win_prob <= 1.0:
        raise ValueError("win_prob must be between 0 and 1")
    net_profit = (american_to_decimal(odds) - 1.0) * stake
    return win_prob * net_profit - (1.0 - win_prob) * stake


def kelly_fraction(win_prob: float, odds: int) -> float:
    """Full-Kelly stake as a fraction of bankroll; 0 when there is no edge.

    Full Kelly is famously aggressive — the UI quotes a quarter of this — but
    the unscaled number is the honest one to compute here.
    """
    if not 0.0 <= win_prob <= 1.0:
        raise ValueError("win_prob must be between 0 and 1")
    b = american_to_decimal(odds) - 1.0
    if b <= 0:
        return 0.0
    edge = win_prob * b - (1.0 - win_prob)
    return max(0.0, edge / b)


def edge_versus_market(model_prob: float, odds: int) -> float:
    """Model probability minus the price's implied probability, in points."""
    return model_prob - implied_probability(odds)


# --------------------------------------------------------------------------- #
# The prompt
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """You are a sports prop-bet analyst. You evaluate one wager at a time using only the data supplied to you.

Rules you never break:
- You do not invent statistics. If a number you need is missing, say which one and reason without it.
- You do not express certainty you cannot support. "No edge" is a valid and common conclusion.
- Everything you produce is analysis for informational purposes, not betting advice.

Structure every response with exactly these headings:

PROP OVERVIEW
Subject, prop type and line, price, and the game context you were given.

OVER CASE
The factors supporting the over, drawn from the supplied data. Name the split or trend you are leaning on.

UNDER CASE
The factors supporting the under, and the scenarios in which the line holds.

EDGE ASSESSMENT
Lean: OVER, UNDER or NO EDGE.
Confidence: low, medium or high.
Estimated win probability as a percentage — this is the number the app converts into expected value, so give a single figure.

MISSING DATA
Anything you would want before treating this as actionable. Say "none" if the inputs were complete.
"""


def build_prompt(sport: Sport, subject: str, prop_label: str, line: str,
                 odds: str, context: str, data: str) -> str:
    """The user message for one prop. Values only — the model adds the prose."""
    parts = [
        f"Sport: {sport.name}",
        f"Subject: {subject or '(not given)'}",
        f"Prop: {prop_label} {line}".strip(),
        f"Price: {odds or '(not given)'}",
        f"{sport.period_label.capitalize()} context: {context or '(not given)'}",
    ]
    body = "\n".join(parts)
    supplied = data.strip() or "(no supporting data supplied)"
    return f"{body}\n\nSupporting data:\n{supplied}"


# --------------------------------------------------------------------------- #
# Parsing the response back into sections
# --------------------------------------------------------------------------- #
SECTIONS = ("PROP OVERVIEW", "OVER CASE", "UNDER CASE",
            "EDGE ASSESSMENT", "MISSING DATA")


@dataclass
class Analysis:
    sections: dict[str, str] = field(default_factory=dict)
    lean: str = ""
    confidence: str = ""
    win_probability: float | None = None
    raw: str = ""


def parse_analysis(text: str) -> Analysis:
    """Split the model's answer into its sections and pull the numbers out.

    Tolerant on purpose: a heading may arrive with or without markdown, and a
    missing section is simply absent rather than an error.
    """
    result = Analysis(raw=text)
    positions = []
    for name in SECTIONS:
        match = re.search(rf'^\s*#*\s*\**{name}\**\s*:?\s*$', text,
                          re.IGNORECASE | re.MULTILINE)
        if match:
            positions.append((match.end(), name))
    positions.sort()
    for index, (start, name) in enumerate(positions):
        end = positions[index + 1][0] if index + 1 < len(positions) else len(text)
        end = text.rfind("\n", start, end) if index + 1 < len(positions) else end
        chunk = text[start:end]
        # drop the next heading line that rfind may have left attached
        result.sections[name] = re.sub(r'\n\s*#*\s*\**[A-Z ]+\**\s*:?\s*$', '',
                                       chunk).strip()

    verdict = result.sections.get("EDGE ASSESSMENT", text)
    lean = re.search(r'\bLean\b\s*:?\s*\**\s*(OVER|UNDER|NO EDGE)', verdict, re.IGNORECASE)
    if lean:
        result.lean = lean.group(1).upper()
    conf = re.search(r'\bConfidence\b\s*:?\s*\**\s*(low|medium|high)', verdict, re.IGNORECASE)
    if conf:
        result.confidence = conf.group(1).lower()
    prob = re.search(r'(\d{1,3}(?:\.\d+)?)\s*%', verdict)
    if prob:
        value = float(prob.group(1))
        if 0.0 <= value <= 100.0:
            result.win_probability = value / 100.0
    return result
