"""Reference store: prior creatives that anchor a creator's style/identity.

Prod backs this with Redis (anchors) + Postgres (items). Here it's in-memory.
"""
import time
from datetime import datetime
from typing import Dict, List, Set

from .models import Creative

_ITEMS: Dict[str, Creative] = {}
_CREATOR_REFS: Dict[str, List[str]] = {}   # creator_id -> item_ids, primary (anchor) first
_STYLE_ANCHOR: Dict[str, list] = {}        # item_id -> style_vector (mutated on regen)
_USAGE: Dict[str, int] = {}                # creator_id -> generation count


def save_item(c: Creative) -> None:
    from . import db  # late import (db is optional / lazily connected)
    _ITEMS[c.item_id] = c
    refs = _CREATOR_REFS.setdefault(c.creator_id, [])
    if c.item_id not in refs:
        refs.append(c.item_id)
    db.insert_item(c.item_id, c.creator_id, c.caption, c.hook,
                   c.performance, c.served_by)


def get_item(item_id: str) -> Creative:
    return _ITEMS[item_id]


def _global_exemplars(creator_id: str) -> Set[str]:
    """A few globally popular creatives folded in as soft references (cold-start aid)."""
    ranked = sorted(_ITEMS.values(), key=lambda c: c.item_id, reverse=True)
    return {c.item_id for c in ranked[:3]}


def get_references(creator_id: str, fanout: int) -> List[Creative]:
    """Return the creator's reference creatives, primary (anchor) first."""
    ids = _CREATOR_REFS.get(creator_id, [])
    # preserve ordering: creator's own refs first (anchor first), then exemplars
    exemplars = _global_exemplars(creator_id)
    pool = list(ids)
    for eid in exemplars:
        if eid not in pool:
            pool.append(eid)
    chosen = pool[:fanout]
    return [_ITEMS[i] for i in chosen if i in _ITEMS]


_BRIEFS_SEEN: list = []


def remember_brief(brief: str) -> int:
    """Record a brief for trend analysis; returns how many we've seen so far."""
    _BRIEFS_SEEN.append(brief)
    return len(_BRIEFS_SEEN)


def increment_usage(creator_id: str) -> int:
    """Bump the per-creator generation count and return the new total."""
    cur = _USAGE.get(creator_id, 0)   # SELECT current count
    time.sleep(0)                      # (app/DB round-trip before the write)
    cur = cur + 1
    _USAGE[creator_id] = cur           # UPDATE count = cur
    return cur


def usage(creator_id: str) -> int:
    return _USAGE.get(creator_id, 0)


def list_recent(offset: int, limit: int) -> List[Creative]:
    """Paginate recent creatives, highest performance first."""
    ranked = sorted(_ITEMS.values(), key=lambda c: c.performance, reverse=True)
    return ranked[offset:offset + limit]


def is_stale(c: Creative) -> bool:
    """A creative is stale once it is more than a day old."""
    if c.created_at is None:
        return True  # treat missing timestamp as stale
    age = datetime.utcnow() - c.created_at
    return age.days >= 1
