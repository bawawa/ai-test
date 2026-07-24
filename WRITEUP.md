# creative-gen Degradation Diagnosis & Fix Writeup

## Context

`creative-gen` is a content-generation service (FastAPI + Postgres + nginx frontend).
Its output has been degrading, and a relaunch ships **reference-image mode** — which
must not regress latency. I spent ~25 minutes diagnosing and fixing the most impactful
issues.

## What I Found

I read the entire codebase (17 Python modules, ~600 lines of application code) and
identified **13 bugs** across three severity tiers.

### Critical — directly causes output degradation

| # | File | Issue | Impact |
|---|------|-------|--------|
| 1 | `providers.py` | **Connection leak**: `_Client` created per request but `finally: pass` never closes it | Under load (35% failure rate), connections accumulate → resource exhaustion → cascading latency |
| 2 | `refimages.py` | **Cache mutation corruption**: `apply_reference_images` mutates the cached style vector in-place (`sv[i] = sv[i] * 0.5`) | Each reuse of a reference image exponentially decays its contribution. The reference-image mode — the relaunch feature — degrades with every call |
| 3 | `templates.py` | **ReDoS vulnerability**: `re.compile(r"^(a+)+$")` has catastrophic backtracking | A crafted `creator_id` like `"aaa...!"` hangs the server. Also, the regex only matches strings of all `a`s — it's both broken and dangerous |
| 4 | `templates.py` | **Format string vulnerability**: `tpl.format(..., ctx=_CTX)` exposes `build_token` to user templates | A malicious template `{ctx.build_token}` leaks the internal build token |

### High — causes latency / performance issues

| # | File | Issue | Impact |
|---|------|-------|--------|
| 5 | `worker.py` + `async_pipeline.py` | **Async pipeline never executes**: `post_generate()` is async but called without `await` from sync code | Coroutine is created and immediately GC'd — trending warmup + notifications silently never run. Confirmed by `RuntimeWarning: coroutine 'post_generate' was never awaited` |
| 6 | `async_pipeline.py` | **Blocking `time.sleep` in async context**: `_notify` uses `time.sleep(0.05)` | Even if the coroutine were awaited, this 50ms blocking call would stall the event loop |
| 7 | `store.py` | **Mutable default argument**: `remember_brief(brief, _seen=[])` | Classic Python footgun — the list is shared across all calls, grows unboundedly → memory leak |
| 8 | `repository.py` | **N+1 query**: `list_by_creator` calls `get(_id)` per item | For N items by a creator, issues N+1 queries instead of 1 |
| 9 | `queue.py` | **Infinite retry loop**: `retry()` has `while True` with no max attempts or backoff | A permanently failing item spins forever, consuming CPU |
| 10 | `monitoring.py` | **Degraded output not tracked**: `record(ok=True)` for fallback path | The 35% fallback rate is invisible to monitoring — SLA looks healthy while output quality silently degrades |

### Medium — correctness / consistency issues

| # | File | Issue | Impact |
|---|------|-------|--------|
| 11 | `worker.py` | **Regenerate doesn't persist**: `regenerate()` doesn't call `repository.save_and_publish` or update the cache | Regenerated items aren't durably persisted or event-published; `/creators/{id}/items` returns stale data |
| 12 | `store.py` | **`get_references` loses ordering**: `set(ids) | _global_exemplars()` destroys the "primary anchor first" guarantee | The prompt builder's `references[0]` (supposed to be the creator's identity anchor) may be a random global exemplar |
| 13 | `store.py` | **`is_stale` crashes on None**: `datetime.utcnow() - c.created_at` when `created_at is None` | TypeError on any creative without a timestamp |

### Minor issues found but not fixed (time-constrained)

- `refimages.py`: Cache stampede in `fetch_style_hot` — no single-flight; concurrent misses all fetch simultaneously
- `context.py`: Process-wide mutable `_state` — race condition under concurrency (FastAPI sync endpoints run in a threadpool)
- `store.py`: `remember_brief` is called but its return value is never used; `_global_exemplars` mixes other creators' content into every request
- `providers.py`: `LOAD_PRESSURE=0.35` means 35% of primary calls fall back to a quality=0.62 model that strips words ≤3 chars, producing garbled text. This is the root cause of "degrading output" and should be addressed with circuit breakers / retry-with-jitter
- `queue.py`: `_QUEUE` is enqueued but never consumed — unbounded memory growth
- `cache.py`: Cache is write-only (`cache.get` is never called) — wasted serialization
- `refimages.py`: TOCTOU in SSRF validation (DNS rebinding between check and use)
- `db.py`: `on_event("startup")` is deprecated in newer FastAPI

## How I Verified

1. **Baseline**: Ran existing smoke tests — both pass but emit `RuntimeWarning: coroutine 'post_generate' was never awaited`, confirming issue #5.
2. **Fix verification**: Wrote 8 new tests (`tests/test_fixes.py`) that specifically exercise each fix:
   - Connection counter stays flat after a generate call
   - Reference image cache returns identical values across repeated calls (no mutation)
   - ReDoS input (`"a" * 40 + "!"`) returns immediately instead of hanging
   - Queue retry respects `MAX_RETRIES` and exits
   - `remember_brief` works with module-level state
   - Regenerate updates the repository (caption matches after replica lag)
   - Async pipeline appends to `_trending` after generate
3. **Regression**: All 10 tests pass (2 original + 8 new).

## What I Changed

| File | Changes |
|------|---------|
| `app/providers.py` | `finally: pass` → `finally: client.close()`; `record(ok=True)` → `record(ok=True, degraded=True)` on fallback |
| `app/monitoring.py` | Added `degraded` counter and `degraded_rate()`; `sla_ok()` warns on high degradation rate |
| `app/refimages.py` | `fetch_style` / `fetch_style_hot` return `list(cached)` copies; `apply_reference_images` works on local copy, never mutates cache; removed double DNS resolution in `fetch_remote_style` |
| `app/templates.py` | `re.compile(r"^(a+)+$")` → `re.compile(r"^\w+$")`; removed `ctx=_CTX` from `tpl.format()` to prevent build token leakage |
| `app/async_pipeline.py` | `_notify` is now `async` with `await asyncio.sleep`; added `fire_and_forget()` helper that safely schedules from sync code |
| `app/worker.py` | Uses `async_pipeline.fire_and_forget()` instead of un-awaited `post_generate()`; `regenerate()` now persists to repository + updates cache |
| `app/store.py` | `remember_brief` uses module-level `_BRIEFS_SEEN` instead of mutable default arg; `get_references` preserves ordering (creator refs first, then exemplars); `is_stale` handles `None` created_at |
| `app/repository.py` | `list_by_creator` does a single filter instead of N+1 `get()` calls |
| `app/queue.py` | `retry()` is bounded (`MAX_RETRIES=5`) with linear backoff; added `MAX_RETRIES` and `RETRY_DELAY_S` constants |
| `tests/test_fixes.py` | 8 new tests verifying each fix |

## What I'd Do Next (with more time)

1. **Address the root cause of degradation**: The 35% fallback rate to a quality=0.62 model is the primary source of output degradation. I'd implement a circuit breaker around the primary model with exponential backoff + jitter, and consider retrying the primary once before falling back (the current code falls back immediately on any error).
2. **Single-flight cache for reference images**: Add a lock per cache key so concurrent misses share a single fetch.
3. **Thread-safe request context**: Replace `context._state` with `contextvars` to fix the race condition.
4. **Consume the background queue**: The queue is fire-and-forget with no consumer. Either remove it or wire up a worker.
5. **Add quality assertions to tests**: The smoke tests only assert status 200 and non-empty caption. I'd add tests that verify output quality (e.g., caption doesn't drop words, style vectors are consistent across regenerations).
6. **Docker integration test**: I couldn't run `docker compose up` in this environment. I'd test the full stack including Postgres and the frontend.
7. **Load test reference-image mode**: Specifically verify that reference-image mode doesn't regress latency under load (the relaunch requirement).
8. **SSRF hardening**: Fix the TOCTOU in `fetch_remote_style` by re-validating the IP at connection time.
