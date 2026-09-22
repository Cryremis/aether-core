from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.services.scheduling.errors import ScheduleError


_CRON_FIELD_RANGES = (
    (0, 59),
    (0, 23),
    (1, 31),
    (1, 12),
    (0, 6),
)


def parse_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleError(f"无效时区: {value}") from exc


def _parse_time(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, TypeError) as exc:
        raise ScheduleError(f"无效时间: {value}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleError(f"无效时间: {value}")
    return hour, minute


def _parse_cron_field(field: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        if not part:
            raise ScheduleError("cron 字段不能为空")
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) <= 0:
                raise ScheduleError("cron step 必须是正整数")
            step = int(step_text)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            start_text, end_text = part.split("-", 1)
            if not start_text.isdigit() or not end_text.isdigit():
                raise ScheduleError("cron range 必须是数字")
            start, end = int(start_text), int(end_text)
        elif part.isdigit():
            start = end = int(part)
        else:
            raise ScheduleError(f"无效 cron 片段: {part}")
        if start < low or end > high or start > end:
            raise ScheduleError(f"cron 值超出范围: {part}")
        values.update(range(start, end + 1, step))
    return values


def _parse_cron(expression: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    fields = expression.split()
    if len(fields) != 5:
        raise ScheduleError("cron 必须是标准 5 字段: minute hour day month weekday")
    minute, hour, day, month, weekday = (
        _parse_cron_field(field, low, high)
        for field, (low, high) in zip(fields, _CRON_FIELD_RANGES, strict=True)
    )
    return minute, hour, day, month, weekday


def _next_local_time(schedule: dict[str, Any], after: datetime, timezone_info: ZoneInfo) -> datetime:
    schedule_type = str(schedule.get("type"))
    if schedule_type == "interval":
        every_seconds = int(schedule["every_seconds"])
        anchor = schedule.get("anchor_at")
        if anchor is None:
            return after.astimezone(timezone.utc) + timedelta(seconds=every_seconds)
        anchor_dt = anchor if isinstance(anchor, datetime) else datetime.fromisoformat(str(anchor))
        if anchor_dt.tzinfo is None:
            anchor_dt = anchor_dt.replace(tzinfo=timezone.utc)
        elapsed = (after.astimezone(timezone.utc) - anchor_dt).total_seconds()
        periods = max(1, int(elapsed // every_seconds) + 1) if elapsed > 0 else 1
        return anchor_dt.astimezone(timezone.utc) + timedelta(seconds=periods * every_seconds)

    hour, minute = _parse_time(str(schedule["time"]))
    if schedule_type == "weekly":
        weekdays = set(schedule.get("weekdays") or [])
        if not weekdays or any(day not in range(1, 8) for day in weekdays):
            raise ScheduleError("weekdays 必须是 1-7 的非空列表")
    elif schedule_type == "workday":
        weekdays = {1, 2, 3, 4, 5}
    else:
        weekdays = set(range(1, 8))

    local = after.astimezone(timezone_info)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    for _ in range(3661):
        if candidate.isoweekday() in weekdays:
            return candidate.astimezone(timezone.utc)
        candidate += timedelta(days=1)
    raise ScheduleError("超过 10 年仍未找到下一次运行时间")


def _next_cron(expression: str, after: datetime, timezone_info: ZoneInfo) -> datetime:
    fields = expression.split()
    minutes, hours, days, months, weekdays = _parse_cron(expression)
    day_restricted = fields[2] != "*"
    weekday_restricted = fields[4] != "*"
    local = after.astimezone(timezone_info)
    candidate = (local + timedelta(minutes=1)).replace(second=0, microsecond=0)
    for _ in range(5_270_400):
        minute_match = candidate.minute in minutes
        hour_match = candidate.hour in hours
        month_match = candidate.month in months
        # 标准 cron 语义：日期和星期同时受限时取 OR，仅一方受限时取 AND。
        if day_restricted and weekday_restricted:
            day_match = candidate.day in days or candidate.weekday() in weekdays
        elif day_restricted:
            day_match = candidate.day in days
        elif weekday_restricted:
            day_match = candidate.weekday() in weekdays
        else:
            day_match = True
        if minute_match and hour_match and day_match and month_match:
            return candidate.astimezone(timezone.utc)
        candidate += timedelta(minutes=1)
    raise ScheduleError("超过 10 年仍未找到下一次运行时间")


def next_occurrence(
    schedule: dict[str, Any],
    *,
    after: datetime,
    timezone_name: str,
) -> datetime:
    """计算严格晚于 after 的下一次 UTC 触发时间。"""
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    timezone_info = parse_timezone(timezone_name)
    if str(schedule.get("type")) == "cron":
        return _next_cron(str(schedule["expression"]), after, timezone_info)
    return _next_local_time(schedule, after, timezone_info)


def preview_next_runs(
    schedule: dict[str, Any],
    *,
    after: datetime,
    timezone_name: str,
    count: int = 3,
) -> list[datetime]:
    result: list[datetime] = []
    cursor = after
    for _ in range(count):
        cursor = next_occurrence(schedule, after=cursor, timezone_name=timezone_name)
        result.append(cursor)
    return result


def describe_schedule(schedule: dict[str, Any], timezone_name: str) -> str:
    """生成用户可读频率描述，供 Agent 工具回执和 UI 复用。"""
    schedule_type = str(schedule.get("type"))
    if schedule_type == "interval":
        seconds = int(schedule.get("every_seconds", 0))
        if seconds % 86400 == 0:
            return f"每 {seconds // 86400} 天"
        if seconds % 3600 == 0:
            return f"每 {seconds // 3600} 小时"
        if seconds % 60 == 0:
            return f"每 {seconds // 60} 分钟"
        return f"每 {seconds} 秒"
    if schedule_type == "cron":
        return f"Cron {schedule.get('expression', '')} ({timezone_name})"
    label = {"daily": "每天", "workday": "工作日", "weekly": "每周"}.get(schedule_type, schedule_type)
    if schedule_type == "weekly":
        names = ["一", "二", "三", "四", "五", "六", "日"]
        days = "".join(names[int(day) - 1] for day in schedule.get("weekdays", []))
        label = f"每周{days}"
    return f"{label} {schedule.get('time', '')} ({timezone_name})"
