import asyncio
from app.api.streaming import keepalive


async def test_heartbeat_does_not_cancel_slow_source() -> None:
    async def source():
        await asyncio.sleep(0.02)
        yield 'data: {"type":"done"}\n\n'

    events = [event async for event in keepalive(source(), interval=0.005)]
    assert events[0] == ": heartbeat\n\n"
    assert events[-1] == 'data: {"type":"done"}\n\n'
