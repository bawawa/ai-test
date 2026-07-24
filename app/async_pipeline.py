"""Async post-generation pipeline — runs after each generate (warm trending, notify)."""
import asyncio
import time

_trending: list = []


async def _warm_trending(item_id: str) -> None:
    await asyncio.sleep(0)
    _trending.append(item_id)


async def _notify(item_id: str) -> None:
    # post to the notifications service (async client) — ~50ms round trip
    await asyncio.sleep(0.05)


async def post_generate(item_id: str) -> None:
    await _warm_trending(item_id)
    await _notify(item_id)


def fire_and_forget(item_id: str) -> None:
    """Schedule post_generate without blocking the request thread.

    Safe to call from sync code (e.g. a FastAPI sync endpoint).  The event
    loop is created per-call; in prod this would dispatch to a shared
    thread-pool executor.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(post_generate(item_id))
        else:
            loop.run_until_complete(post_generate(item_id))
    except RuntimeError:
        # No event loop in this thread — create one for the background task
        asyncio.run(post_generate(item_id))
