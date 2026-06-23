"""
Caps concurrent Mistral API calls across the process. The OpenAI-compatible
Mistral client used in this codebase is synchronous, so call_with_limit runs
the blocking call in a thread (via asyncio.to_thread) instead of stalling the
event loop, while the semaphore bounds how many run at once.
"""
import asyncio
from typing import Callable, TypeVar

MISTRAL_CONCURRENCY_LIMIT = 3
mistral_semaphore = asyncio.Semaphore(MISTRAL_CONCURRENCY_LIMIT)

T = TypeVar("T")


async def call_with_limit(fn: Callable[[], T]) -> T:
    """fn must be a zero-arg callable, e.g. call_with_limit(lambda: client.chat.completions.create(...))"""
    async with mistral_semaphore:
        return await asyncio.to_thread(fn)
