from __future__ import annotations


class ScheduleError(ValueError):
    """调度配置或状态非法。"""


class SchedulePermissionError(PermissionError):
    """调用方无权访问或修改目标定时任务。"""


class ScheduleConflictError(RuntimeError):
    """乐观并发控制冲突。"""
