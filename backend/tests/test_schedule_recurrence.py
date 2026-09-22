from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.scheduling.errors import ScheduleError
from app.services.scheduling.recurrence import next_occurrence, preview_next_runs


def test_daily_schedule_respects_timezone() -> None:
    after = datetime(2026, 9, 22, 1, 0, tzinfo=timezone.utc)

    result = next_occurrence(
        {"type": "daily", "time": "09:30"},
        after=after,
        timezone_name="Asia/Shanghai",
    )

    assert result == datetime(2026, 9, 22, 1, 30, tzinfo=timezone.utc)


def test_workday_schedule_skips_weekend() -> None:
    after = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)

    result = next_occurrence(
        {"type": "workday", "time": "09:00"},
        after=after,
        timezone_name="UTC",
    )

    assert result.weekday() == 0
    assert result == datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)


def test_weekly_schedule_selects_requested_days() -> None:
    after = datetime(2026, 9, 22, tzinfo=timezone.utc)

    result = preview_next_runs(
        {"type": "weekly", "weekdays": [1, 5], "time": "10:00"},
        after=after,
        timezone_name="UTC",
        count=2,
    )

    assert [item.isoweekday() for item in result] == [5, 1]


def test_interval_schedule_uses_anchor_without_repeating_missed_periods() -> None:
    anchor = datetime(2026, 9, 22, tzinfo=timezone.utc)
    after = datetime(2026, 9, 22, 0, 7, 30, tzinfo=timezone.utc)

    result = next_occurrence(
        {"type": "interval", "every_seconds": 300, "anchor_at": anchor.isoformat()},
        after=after,
        timezone_name="UTC",
    )

    assert result == datetime(2026, 9, 22, 0, 10, tzinfo=timezone.utc)


def test_cron_schedule_supports_ranges_and_steps() -> None:
    after = datetime(2026, 9, 22, 8, 50, tzinfo=timezone.utc)

    result = next_occurrence(
        {"type": "cron", "expression": "*/15 9-17 * * 1-5"},
        after=after,
        timezone_name="UTC",
    )

    assert result == datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("expression",),
    [
        ("60 * * * *",),
        ("* 24 * * *",),
        ("* * * * 8",),
        ("*/0 * * * *",),
        ("not-a-cron",),
    ],
)
def test_invalid_cron_expressions_are_rejected(expression: str) -> None:
    with pytest.raises(ScheduleError):
        next_occurrence(
            {"type": "cron", "expression": expression},
            after=datetime(2026, 9, 22, tzinfo=timezone.utc),
            timezone_name="UTC",
        )
