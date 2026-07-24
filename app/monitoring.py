"""Availability monitoring.

On-call is paged when the rolling 5xx rate crosses the error budget. We run to a
four-nines SLA, so the budget is tiny.
"""
import logging

log = logging.getLogger("creative_gen.monitoring")

ERROR_BUDGET = 0.001  # 0.1% — four nines
_calls = {"total": 0, "errors": 0, "degraded": 0}


def record(ok: bool, *, degraded: bool = False) -> None:
    _calls["total"] += 1
    if not ok:
        _calls["errors"] += 1
    if degraded:
        _calls["degraded"] += 1


def error_rate() -> float:
    return _calls["errors"] / max(1, _calls["total"])


def degraded_rate() -> float:
    """Fraction of calls served by the fallback (degraded quality)."""
    return _calls["degraded"] / max(1, _calls["total"])


def sla_ok() -> bool:
    breached = error_rate() > ERROR_BUDGET
    if breached:
        log.critical("SLA BREACH: 5xx rate %.4f > budget %.4f — paging on-call",
                     error_rate(), ERROR_BUDGET)
    deg = degraded_rate()
    if deg > 0.2:  # more than 20% of calls are degraded-quality
        log.warning("High degraded rate: %.4f — output quality is regressing", deg)
    return not breached
