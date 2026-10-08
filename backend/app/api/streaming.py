import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress


async def keepalive(source: AsyncIterator[str], interval: float = 15.0) -> AsyncIterator[str]:
    """Keep reverse proxies alive while model loading or generation is pending."""
    pending: asyncio.Task | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(anext(source))
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield ": heartbeat\n\n"
                continue
            try:
                yield pending.result()
            except StopAsyncIteration:
                break
            pending = None
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        await source.aclose()
