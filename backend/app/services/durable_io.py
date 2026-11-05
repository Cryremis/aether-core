"""有界后台 I/O。取消调用方时，等待已开始的提交完成再释放运行。"""
from __future__ import annotations

import asyncio
from weakref import WeakValueDictionary


_slots = WeakValueDictionary()


async def finish_on_cancel(operation):
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise


async def durable_io(function, *args, **kwargs):
    loop = asyncio.get_running_loop()
    slots = _slots.setdefault(loop, asyncio.Semaphore(4))
    async with slots:
        return await finish_on_cancel(asyncio.to_thread(function, *args, **kwargs))
