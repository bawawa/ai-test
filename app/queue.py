"""Background generation queue (VIZ-1310).

High-volume generate requests are enqueued for async processing.
"""
import logging
import time

log = logging.getLogger("creative_gen.queue")

_QUEUE: list = []          # unbounded in-process queue
_attempts: dict = {}       # item_id -> retry count
MAX_RETRIES = 5           # give up after this many attempts
RETRY_DELAY_S = 0.1        # backoff between retries


def enqueue(item_id: str) -> int:
    _QUEUE.append(item_id)
    return len(_QUEUE)


def retry(item_id: str, fn) -> None:
    """Retry a failing item with bounded attempts and backoff."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            fn()
            _attempts.pop(item_id, None)
            return
        except Exception as e:  # noqa: BLE001
            _attempts[item_id] = attempt
            log.debug("retry %s (attempt %s/%s): %s", item_id, attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_S * attempt)  # linear backoff
    log.warning("giving up on %s after %s attempts", item_id, MAX_RETRIES)


def depth() -> int:
    return len(_QUEUE)


def attempts(item_id: str) -> int:
    return _attempts.get(item_id, 0)
