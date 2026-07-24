"""Additional tests — verify the fixes for degradation and reference-image mode."""
import os
import time

# Ensure offline mode (no DB)
os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("LOAD_PRESSURE", "0.0")  # disable provider failures for deterministic tests
os.environ.setdefault("GEN_SLOW_S", "0.0")

from fastapi.testclient import TestClient
from app.api import app

client = TestClient(app)
KEY = {"x-api-key": "sk_live_demo"}


# ---- Fix: connection leak in providers.py ----
def test_provider_connections_are_closed():
    """Each generate call must close its _Client connection."""
    from app import providers
    before = providers._open_clients
    client.post("/generate", json={"creator_id": "c_fix", "brief": "test"}, headers=KEY)
    after = providers._open_clients
    assert before == after, f"connection leak: {before} -> {after}"


# ---- Fix: reference-image cache corruption ----
def test_reference_image_cache_not_mutated():
    """Calling apply_reference_images twice with the same ID must yield the
    same contribution (cache entries must not be corrupted by mutation)."""
    from app import refimages
    refimages._HOT_CACHE.clear()
    refimages._IMG_CACHE.clear()

    base = [0.5] * 8
    r1 = refimages.apply_reference_images(list(base), ["img_001"])
    refimages._HOT_CACHE.clear()  # force re-fetch to prove the cached value wasn't mutated
    r2 = refimages.apply_reference_images(list(base), ["img_001"])
    assert r1 == r2, f"cache corrupted: {r1} != {r2}"


def test_reference_image_idempotent_within_ttl():
    """Within TTL, repeated calls should hit the cache and return identical vectors."""
    from app import refimages
    refimages._HOT_CACHE.clear()
    v1 = refimages.fetch_style_hot("img_002")
    v2 = refimages.fetch_style_hot("img_002")
    assert v1 == v2, "TTL cache returned different values for same key"


# ---- Fix: ReDoS in templates.py ----
def test_template_name_validation_no_redos():
    """A long string of 'a' followed by a non-matching char must not hang."""
    from app import templates
    # This would hang for seconds with the old ^(a+)+$ regex
    result = templates._valid_name("a" * 40 + "!")
    assert result is False, "should reject non-identifier"


# ---- Fix: queue.retry is bounded ----
def test_queue_retry_is_bounded():
    """retry must give up after MAX_RETRIES, not loop forever."""
    from app import queue
    calls = []
    def always_fail():
        calls.append(1)
        raise RuntimeError("boom")
    queue.retry("item_bounded", always_fail)
    assert len(calls) == queue.MAX_RETRIES, f"expected {queue.MAX_RETRIES} attempts, got {len(calls)}"


# ---- Fix: remember_brief no longer uses mutable default ----
def test_remember_brief_module_level():
    """remember_brief should work and return increasing counts."""
    from app import store
    store._BRIEFS_SEEN.clear()
    assert store.remember_brief("a") == 1
    assert store.remember_brief("b") == 2


# ---- Fix: regenerate persists to repository ----
def test_regenerate_persists_to_repository():
    """After regenerate, the updated creative should be in the repository."""
    from app import repository
    repository._PRIMARY.clear()
    repository._REPLICA.clear()
    repository._pending_replica.clear()

    g = client.post("/generate", json={"creator_id": "c_repo", "brief": "test"}, headers=KEY).json()
    # wait for replica lag
    time.sleep(0.6)
    repo_item = repository.get(g["item_id"])
    assert repo_item is not None, "generate should persist to repository"

    # regenerate
    r = client.post("/regenerate", json={"creator_id": "c_repo", "item_id": g["item_id"]}, headers=KEY)
    assert r.status_code == 200
    # wait for replica lag
    time.sleep(0.6)
    repo_item2 = repository.get(g["item_id"])
    assert repo_item2 is not None, "regenerate should update repository"
    assert repo_item2.caption == r.json()["caption"], "repository should reflect regenerated caption"


# ---- Fix: async pipeline executes (no more "coroutine never awaited") ----
def test_async_pipeline_executes():
    """After generate, the trending list should contain the item."""
    from app import async_pipeline
    async_pipeline._trending.clear()
    client.post("/generate", json={"creator_id": "c_async", "brief": "test"}, headers=KEY)
    # fire_and_forget should have appended to _trending
    assert len(async_pipeline._trending) > 0, "async pipeline did not execute"
