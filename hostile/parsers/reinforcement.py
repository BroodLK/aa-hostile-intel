"""
Parser for the reinforcement info EVE lets you copy from a reinforced structure:

    Mercenary Den
    99 m
    Sec. -9.9
    Reinforced until 2026.11.03 19:13:30
"""

# Standard Library
import re
from datetime import UTC, datetime
from typing import Any

_UNTIL_RE = re.compile(
    r"^(?:(?P<stage>shield|armor|armour|hull|structure)\s+)?"
    r"(?P<kind>reinforced|anchoring|unanchoring)\s+until\s*:?\s+"
    r"(?P<date>\d{4}[./-]\d{1,2}[./-]\d{1,2})[\sT]+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?)",
    re.IGNORECASE,
)
_DISTANCE_RE = re.compile(r"^[\d.,\s]+\s*(m|km|au)$", re.IGNORECASE)
_SECURITY_RE = re.compile(
    r"^sec(?:urity)?(?:\s+status)?\.?\s*:?\s*(-?\d+(?:[.,]\d+)?)$", re.IGNORECASE
)

# "<stage> reinforced until" names the timer that is coming up, matching the
# registry's "Armor Reinforced" / "Hull Reinforced" states.
_STAGE_TO_TIMER = {
    "shield": "ARMOR",
    "armor": "ARMOR",
    "armour": "ARMOR",
    "hull": "HULL",
    "structure": "HULL",
}


def _parse_datetime(date_part: str, time_part: str) -> datetime | None:
    date_part = re.sub(r"[./]", "-", date_part)
    if time_part.count(":") == 1:
        time_part += ":00"
    try:
        return datetime.strptime(
            f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=UTC)
    except ValueError:
        return None


class ReinforcementTimerParser:
    """Parses a copied structure reinforcement / anchoring timer."""

    @classmethod
    def is_reinforcement_paste(cls, text: str) -> bool:
        if not text:
            return False
        return any(_UNTIL_RE.match(line.strip()) for line in text.splitlines())

    @classmethod
    def parse(cls, text: str) -> dict[str, Any] | None:
        """
        Returns ``{name, timer_datetime, timer_type, stage, distance, security}`` or
        None when the text isn't a reinforcement paste. ``timer_type`` is only set when
        the paste itself says which stage it is (anchoring / unanchoring / a stage
        word); otherwise it is left for the caller to infer.
        """
        if not text:
            return None
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        until = None
        until_index = -1
        for index, line in enumerate(lines):
            match = _UNTIL_RE.match(line)
            if match:
                until, until_index = match, index
                break
        if not until:
            return None

        when = _parse_datetime(until.group("date"), until.group("time"))
        if not when:
            return None

        name = ""
        distance = ""
        security = None
        for index, line in enumerate(lines):
            if index == until_index:
                continue
            if _DISTANCE_RE.match(line):
                distance = distance or line
                continue
            sec = _SECURITY_RE.match(line)
            if sec:
                try:
                    security = float(sec.group(1).replace(",", "."))
                except ValueError:
                    pass
                continue
            if not name and index < until_index:
                name = line

        kind = until.group("kind").lower()
        stage = (until.group("stage") or "").lower()
        if kind == "anchoring":
            timer_type = "ANCHORING"
        elif kind == "unanchoring":
            timer_type = "UNANCHORING"
        else:
            timer_type = _STAGE_TO_TIMER.get(stage, "")

        return {
            "name": name,
            "timer_datetime": when,
            "timer_type": timer_type,
            "stage": stage,
            "kind": kind,
            "distance": distance,
            "security": security,
        }
