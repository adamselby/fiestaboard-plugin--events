"""Events plugin for FiestaBoard.
"""

# ---------------------------------------------------------------------------
# Shared core (generated into each plugin from src/_shared_core.py; keep in sync)
# Pure standard library: no network, no third-party packages.
# ---------------------------------------------------------------------------

from __future__ import annotations

import logging
import random
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.plugins.base import PluginBase, PluginResult, TriggerResult

logger = logging.getLogger(__name__)

def wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap; '|' forces a line break; long words are hard-cut."""
    lines: list[str] = []
    for chunk in str(text).split("|"):
        current = ""
        for word in chunk.split():
            word = word[:width]
            if not current:
                current = word
            elif len(current) + 1 + len(word) <= width:
                current += " " + word
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def info_lines(rows: list[str], width: int, height: int) -> list[str]:
    """Plain centered rows; on short boards drop blank spacer rows first."""
    if height < len(rows):
        rows = [r for r in rows if r] or rows
    rows = [r[:width].center(width) for r in rows[:height]]
    return (rows + [""] * height)[:height]


# ---------------------------------------------------------------------------
# Date-plugin helpers (from src/_shared.py)
# ---------------------------------------------------------------------------

DEFAULT_TZ = "America/Chicago"
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
COLORS = {
    "red": "{63}",
    "orange": "{64}",
    "yellow": "{65}",
    "green": "{66}",
    "blue": "{67}",
    "violet": "{68}",
    "purple": "{68}",
    "white": "{69}",
}
RAINBOW = ["red", "orange", "yellow", "green", "blue", "violet"]
CONFETTI_TILES = 22  # widest single board; a Note uses the first 15
_YMD_RE = re.compile(r"^\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*$")


def parse_ymd(raw: Any) -> date:
    """Parse YYYY-MM-DD (slashes allowed). Raises ValueError."""
    m = _YMD_RE.match(str(raw or ""))
    if not m:
        raise ValueError(f"'{raw}' is not YYYY-MM-DD")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def fmt_date(d: date) -> str:
    return f"{MONTHS[d.month - 1]} {d.day}"


def parse_palette(raw: Any) -> list[str]:
    """'red, white, blue' → tile markers. Unknown names are ignored."""
    names = [p.strip().lower() for p in str(raw or "").replace("/", ",").split(",")]
    return [COLORS[n] for n in names if n in COLORS]


def random_confetti(tiles: int, palette: list[str] | None = None, rng: random.Random | None = None) -> str:
    """Random color tiles; never the same color twice in a row when avoidable."""
    rng = rng or random
    pal = palette or [COLORS[c] for c in RAINBOW]
    out: list[str] = []
    for _ in range(tiles):
        choices = [c for c in pal if not out or c != out[-1]] or pal
        out.append(rng.choice(choices))
    return "".join(out)


def days_phrase(days: int) -> str:
    if days == 0:
        return "TODAY"
    if days == 1:
        return "TOMORROW"
    return f"IN {days} DAYS"


def celebration_lines(message: str, width: int, height: int, palette: list[str] | None = None) -> list[str]:
    """Message centered, with confetti rows above (and below when there's room)."""
    body = wrap(message, width)[:height]
    top = [random_confetti(width, palette)] if height >= len(body) + 1 else []
    bottom = [random_confetti(width, palette)] if height > 3 and height >= len(body) + len(top) + 1 else []
    free = height - len(top) - len(bottom) - len(body)
    middle = [""] * max(free // 2, 0) + [line.center(width) for line in body]
    middle += [""] * (height - len(top) - len(bottom) - len(middle))
    return (top + middle + bottom + [""] * height)[:height]


def flanked(text: str, width: int, palette: list[str] | None = None) -> str:
    """Message centered with confetti filling both sides."""
    text = text[: max(width - 2, 0)]
    spare = width - len(text) - 2
    left = spare // 2
    return random_confetti(left, palette) + " " + text + " " + random_confetti(spare - left, palette)


def announce_settings(default_priority: int, what: str) -> dict[str, Any]:
    """Settings-schema fragment for the takeover ("announce") feature."""
    return {
        "announce": {
            "type": "boolean",
            "title": "Take Over the Board",
            "description": f"Interrupt your normal pages to show {what}, then hand the board back.",
            "default": False,
        },
        "announce_mode": {
            "type": "string",
            "title": "Takeover Style",
            "description": "Once a day for a set length, or the first few minutes of every hour.",
            "enum": ["daily", "hourly"],
            "enumNames": ["Once a day", "Start of every hour"],
            "default": "daily",
        },
        "announce_hour": {
            "type": "integer",
            "title": "Start Hour",
            "description": "0-23, local time. Once a day: when it starts. Every hour: the first hour it runs.",
            "default": 8,
            "minimum": 0,
            "maximum": 23,
        },
        "announce_end_hour": {
            "type": "integer",
            "title": "End Hour (every-hour style)",
            "description": "0-24. The takeover doesn't start in this hour or later.",
            "default": 22,
            "minimum": 1,
            "maximum": 24,
        },
        "announce_minutes": {
            "type": "integer",
            "title": "Takeover Length (minutes)",
            "description": "Once a day: up to 1440. Every hour: capped at 60.",
            "default": 30,
            "minimum": 1,
            "maximum": 1440,
        },
        "takeover_priority": {
            "type": "integer",
            "title": "Takeover Priority",
            "description": "When two takeovers overlap, the higher number wins.",
            "default": default_priority,
            "minimum": 1,
            "maximum": 99,
        },
    }


class Takeover:
    """Mixin (deliberately NOT a PluginBase subclass, so the loader finds exactly
    one plugin class per module) providing timezone handling and trigger logic.

    Subclasses set ACTIVE_FLAG (the data key that must be "true" to take over)
    and DEFAULT_PRIORITY.
    """

    ACTIVE_FLAG = "active"
    DEFAULT_PRIORITY = 50

    def _tz(self) -> ZoneInfo:
        name = self.config.get("timezone") or DEFAULT_TZ
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo(DEFAULT_TZ)

    def _now(self) -> datetime:
        return datetime.now(self._tz())

    def _validate_common(self, config: dict[str, Any]) -> list[str]:
        errors = list(self._validate_refresh_seconds(config))
        tz = config.get("timezone") or DEFAULT_TZ
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            errors.append(f"Invalid timezone: {tz}")
        if config.get("announce_mode", "daily") not in ("daily", "hourly"):
            errors.append("Takeover style must be 'daily' or 'hourly'")
        if config.get("announce_mode") == "hourly" and int(config.get("announce_end_hour", 22)) <= int(
            config.get("announce_hour", 8)
        ):
            errors.append("End hour must be after the start hour")
        return errors

    def announce_window(self, now: datetime) -> tuple[datetime, str] | None:
        """(window end, stable window key) if a takeover window is open now."""
        cfg = self.config
        if not cfg.get("announce"):
            return None
        start_hour = int(cfg.get("announce_hour", 8))
        minutes = int(cfg.get("announce_minutes", 30))
        if cfg.get("announce_mode") == "hourly":
            end_hour = int(cfg.get("announce_end_hour", 22))
            if not (start_hour <= now.hour < end_hour):
                return None
            start = now.replace(minute=0, second=0, microsecond=0)
            end = start + timedelta(minutes=min(minutes, 60))
            key = now.strftime("%Y-%m-%d-%H")
        else:
            start = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
            end = start + timedelta(minutes=minutes)
            key = now.strftime("%Y-%m-%d")
        if start <= now < end:
            return end, key
        return None

    def check_triggers(self) -> list[TriggerResult]:
        now = self._now()
        window = self.announce_window(now)
        if window is None:
            return []
        # Use the cached fetch (refreshed every refresh_seconds). Recomputing
        # here would reshuffle confetti on every engine tick and re-flap the board.
        result = self.get_data()
        if not result.available or not result.data or result.data.get(self.ACTIVE_FLAG) != "true":
            return []
        end, key = window
        return [
            TriggerResult(
                triggered=True,
                # One id per window: re-returning it each tick just refreshes it,
                # and a manual page change suppresses it for the rest of the window.
                trigger_id=f"{self.plugin_id}_{key}",
                priority=int(self.config.get("takeover_priority", self.DEFAULT_PRIORITY)),
                duration_seconds=max(int((end - now).total_seconds()), 30),
                data=result.data,
                formatted_lines=result.formatted_lines,
                message=result.data.get("message") or None,
            )
        ]

    def _size(self) -> tuple[int, int]:
        board = self.board
        return (board.width if board else 22), (board.height if board else 6)

    def get_formatted_display(self) -> list[str] | None:
        result = self.fetch_data()
        if not result.available or not result.data:
            return None
        return result.formatted_lines


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class EventsPlugin(Takeover, PluginBase):
    """Event countdown."""

    ACTIVE_FLAG = "is_event_day"
    DEFAULT_PRIORITY = 35

    @property
    def plugin_id(self) -> str:
        return "events"

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors = self._validate_common(config)
        entries = config.get("events") or []
        if not isinstance(entries, list):
            return errors + ["Events must be a list"]
        for i, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                errors.append(f"Row {i}: expected a name and a date")
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                errors.append(f"Row {i}: name is required")
            try:
                start = parse_ymd(entry.get("date"))
            except ValueError as e:
                errors.append(f"Row {i} ({name or 'unnamed'}): {e}")
                continue
            if entry.get("end"):
                try:
                    if parse_ymd(entry.get("end")) < start:
                        errors.append(f"Row {i} ({name}): end is before start")
                except ValueError as e:
                    errors.append(f"Row {i} ({name}) end: {e}")
        return errors

    def _events(self) -> list[tuple[date, date, str, str, list[str]]]:
        out = []
        for entry in self.config.get("events") or []:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip().upper()
            if not name:
                continue
            try:
                start = parse_ymd(entry.get("date"))
                end = parse_ymd(entry["end"]) if entry.get("end") else start
            except ValueError as e:
                logger.warning("events: skipping %s: %s", name, e)
                continue
            if end < start:
                continue
            message = str(entry.get("message") or "").strip().upper() or f"TODAY: {name}"
            out.append((start, end, name, message, parse_palette(entry.get("colors"))))
        out.sort(key=lambda t: (t[0], t[2]))
        return out

    def compute(self, today: date) -> dict[str, Any]:
        events = self._events()
        lookahead = int(self.config.get("lookahead_days") or 60)
        current = [e for e in events if e[0] <= today <= e[1]]
        future = [e for e in events if e[0] > today]
        palette = current[0][4] if current else []
        self._palette = palette
        data: dict[str, Any] = {
            "is_event_day": "true" if current else "false",
            "name": current[0][2] if current else "",
            "message": current[0][3] if current else "",
            "count": str(len(current)),
            "confetti": random_confetti(CONFETTI_TILES, palette),
            "today": [{"name": e[2], "message": e[3]} for e in current],
            "upcoming": [
                {"name": e[2], "date": fmt_date(e[0]), "days": str((e[0] - today).days)}
                for e in future
                if (e[0] - today).days <= lookahead
            ],
            "next_name": "",
            "next_date": "",
            "next_days": "",
            "next_formatted": "",
        }
        if future:
            e = future[0]
            days = (e[0] - today).days
            data.update(next_name=e[2], next_date=fmt_date(e[0]), next_days=str(days),
                        next_formatted=f"{e[2]} {days_phrase(days)}")
        return data

    def fetch_data(self) -> PluginResult:
        try:
            data = self.compute(self._now().date())
            if not (data["name"] or data["next_name"]):
                return PluginResult(available=False, error="No upcoming events")
            return PluginResult(available=True, data=data, formatted_lines=self._format_display(data))
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("Error computing events")
            return PluginResult(available=False, error=str(e))

    def _format_display(self, data: dict[str, Any]) -> list[str]:
        width, height = self._size()
        if data["is_event_day"] == "true":
            return celebration_lines(data["message"], width, height, getattr(self, "_palette", None))
        days = int(data["next_days"])
        count = "TOMORROW" if days == 1 else f"{days} DAYS"
        rows = ["COUNTDOWN", "", data["next_name"], data["next_date"], count, ""]
        if height <= 3:
            rows = [data["next_name"], data["next_date"], count]
        return info_lines(rows, width, height)


Plugin = EventsPlugin
