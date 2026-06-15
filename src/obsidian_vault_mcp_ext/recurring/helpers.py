"""Pure anchor/interval logic and frontmatter I/O for the recurring extension.

The anchor / interval logic (``parse_interval``, ``compute_relative_period``,
``compute_pending_periods`` and the descending trigger generator) is ported
verbatim from the fork's tools/recurring.py: it is pure, has no vault or
filesystem dependency, and is unit-testable without time mocking.

Frontmatter (de)serialization is done with the ``frontmatter`` library (PyYAML
under the hood) instead of the fork's ruamel-based ``frontmatter_io`` module,
which is a host-core internal not available to extensions. ISO date values are
stored as strings, so the ``(recurrence_template, recurrence_period)`` pair
round-trips as strings -- exactly what the disk-based idempotency lookup needs.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterator

import frontmatter

# --------------------------------------------------------------------------
# Pure anchor / interval logic (ported verbatim from the fork)
# --------------------------------------------------------------------------

_QUARTER_END_PLUS_RE = re.compile(r"^quarter_end_plus_(\d+)d$")
_FIXED_RE = re.compile(r"^fixed-(\d{2})-(\d{2})$")
_T_BEFORE_RE = re.compile(r"^T-(\d+)-before-(\d{2})-(\d{2})$")
_INTERVAL_RE = re.compile(r"^(\d+)([dm])$")


class AnchorError(ValueError):
    """Raised when an anchor or interval expression is malformed."""


@dataclass(frozen=True)
class TriggeredPeriod:
    """One period whose trigger date has fired.

    ``trigger_date`` is the calendar date when the instance becomes "due"
    in the anchor sense (before applying ``due_offset_days``).
    ``period_key`` is a deterministic string used together with the template
    id to enforce idempotency (e.g. ``q3-2026`` or ``2026-07-31``).
    """

    trigger_date: date
    period_key: str


@dataclass(frozen=True)
class Interval:
    """A non-zero positive interval of days or months."""

    n: int
    unit: str  # 'd' or 'm'

    def add_to(self, base: date) -> date:
        if self.unit == "d":
            return base + timedelta(days=self.n)
        if self.unit == "m":
            month_zero_based = base.month - 1 + self.n
            year = base.year + month_zero_based // 12
            month = (month_zero_based % 12) + 1
            day = min(base.day, calendar.monthrange(year, month)[1])
            return date(year, month, day)
        raise AnchorError(f"Unsupported interval unit: {self.unit!r}")


def parse_interval(spec: str) -> Interval:
    """Parse ``'7d'`` or ``'3m'`` into an :class:`Interval`."""
    if not isinstance(spec, str):
        raise AnchorError(f"Interval must be a string, got {type(spec).__name__}")
    match = _INTERVAL_RE.match(spec.strip())
    if not match:
        raise AnchorError(
            f"Invalid interval format: {spec!r}; expected 'Nd' (days) or 'Nm' (months)"
        )
    n = int(match.group(1))
    unit = match.group(2)
    if n < 1:
        raise AnchorError(f"Interval must be >= 1: {spec!r}")
    return Interval(n=n, unit=unit)


def compute_relative_period(interval_spec: str, last_done_date: date) -> TriggeredPeriod:
    """Compute the next trigger for a relative-mode template."""
    interval = parse_interval(interval_spec)
    trigger = interval.add_to(last_done_date)
    return TriggeredPeriod(trigger_date=trigger, period_key=trigger.isoformat())


def _quarter_of(month: int) -> int:
    return (month - 1) // 3 + 1


def _quarter_end_date(year: int, quarter: int) -> date:
    last_month = quarter * 3
    last_day = calendar.monthrange(year, last_month)[1]
    return date(year, last_month, last_day)


_MAX_DAY_PER_MONTH = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def _validate_month_day(mm: int, dd: int) -> None:
    if not (1 <= mm <= 12):
        raise AnchorError(f"Invalid month: {mm}")
    max_day = _MAX_DAY_PER_MONTH[mm - 1]
    if not (1 <= dd <= max_day):
        raise AnchorError(f"Invalid day for month {mm:02d}: {dd}")


def _resolve_fixed_in_year(year: int, mm: int, dd: int) -> date | None:
    """Return ``date(year, mm, dd)`` or None if not a real calendar date."""
    try:
        return date(year, mm, dd)
    except ValueError:
        return None


_MAX_DESCENDING_STEPS = 4000  # ~333 years of monthly walks, hard hang preventer


def _iter_triggers_descending(anchor: str, as_of: date) -> Iterator[TriggeredPeriod]:
    """Yield triggers ``<= as_of`` in descending trigger-date order."""
    if anchor == "month_end":
        year, month = as_of.year, as_of.month
        for _ in range(_MAX_DESCENDING_STEPS):
            last_day = calendar.monthrange(year, month)[1]
            trigger = date(year, month, last_day)
            if trigger <= as_of:
                yield TriggeredPeriod(trigger, f"{year:04d}-{month:02d}")
            month -= 1
            if month < 1:
                month = 12
                year -= 1
        return

    if anchor == "month_start":
        year, month = as_of.year, as_of.month
        for _ in range(_MAX_DESCENDING_STEPS):
            trigger = date(year, month, 1)
            if trigger <= as_of:
                yield TriggeredPeriod(trigger, f"{year:04d}-{month:02d}")
            month -= 1
            if month < 1:
                month = 12
                year -= 1
        return

    match = _QUARTER_END_PLUS_RE.match(anchor)
    if match:
        n_days = int(match.group(1))
        year = as_of.year
        quarter = _quarter_of(as_of.month)
        for _ in range(_MAX_DESCENDING_STEPS):
            qe = _quarter_end_date(year, quarter)
            trigger = qe + timedelta(days=n_days)
            if trigger <= as_of:
                yield TriggeredPeriod(trigger, f"q{quarter}-{year:04d}")
            quarter -= 1
            if quarter < 1:
                quarter = 4
                year -= 1
        return

    match = _FIXED_RE.match(anchor)
    if match:
        mm = int(match.group(1))
        dd = int(match.group(2))
        _validate_month_day(mm, dd)
        year = as_of.year
        for _ in range(_MAX_DESCENDING_STEPS):
            d = _resolve_fixed_in_year(year, mm, dd)
            if d is not None and d <= as_of:
                yield TriggeredPeriod(d, f"fixed-{mm:02d}-{dd:02d}-{year:04d}")
            year -= 1
        return

    match = _T_BEFORE_RE.match(anchor)
    if match:
        n = int(match.group(1))
        mm = int(match.group(2))
        dd = int(match.group(3))
        _validate_month_day(mm, dd)
        year = as_of.year
        for _ in range(_MAX_DESCENDING_STEPS):
            anchor_d = _resolve_fixed_in_year(year, mm, dd)
            if anchor_d is not None:
                trigger = anchor_d - timedelta(days=n)
                if trigger <= as_of:
                    yield TriggeredPeriod(trigger, f"fixed-{mm:02d}-{dd:02d}-{year:04d}")
            year -= 1
        return

    raise AnchorError(f"Unsupported anchor expression: {anchor!r}")


def compute_pending_periods(
    anchor: str,
    as_of: date,
    since: date | None,
    *,
    catchup: str = "next",
    safety_limit: int = 50,
) -> list[TriggeredPeriod]:
    """Return absolute-anchor periods that should fire now, ascending by trigger date."""
    if catchup not in {"next", "all"}:
        raise AnchorError(f"Invalid catchup mode: {catchup!r}")

    if since is None:
        # No baseline -> never backfill (bootstrap-conservative branch).
        return []

    collected: list[TriggeredPeriod] = []
    gen = _iter_triggers_descending(anchor, as_of)
    for _ in range(safety_limit):
        try:
            period = next(gen)
        except StopIteration:
            break
        if period.trigger_date <= since:
            break
        collected.append(period)

    collected.reverse()
    if catchup == "next" and len(collected) > 1:
        return collected[-1:]
    return collected


# --------------------------------------------------------------------------
# Date coercion + frontmatter (de)serialization helpers
# --------------------------------------------------------------------------


def coerce_date(value: Any) -> date | None:
    """Coerce a frontmatter value to a date if possible, else None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def format_iso(d: date) -> str:
    return d.isoformat()


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse markdown into (metadata, body) using the frontmatter library."""
    post = frontmatter.loads(content)
    return dict(post.metadata or {}), post.content


def dump_frontmatter(metadata: dict[str, Any], body: str) -> str:
    """Serialize (metadata, body) back to markdown using the frontmatter library."""
    post = frontmatter.Post(body, **metadata)
    text = frontmatter.dumps(post)
    if not text.endswith("\n"):
        text += "\n"
    return text
