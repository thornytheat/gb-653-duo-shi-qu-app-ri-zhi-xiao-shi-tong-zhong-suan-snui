from datetime import datetime
from zoneinfo import ZoneInfo

from app.errors import DomainError
from app.manifest import canonical_digest
from app.reasons import reason as reason_text
from . import core

DEFAULT_RULE_VERSION = "2026.11"


def _now_z() -> str:
    from datetime import timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _zones(payload: dict) -> dict:
    zones = dict(core.BASE_ZONES)
    zones.update(payload.get("zones") or {})
    for service, zone in zones.items():
        try:
            ZoneInfo(zone)
        except Exception:
            raise DomainError("INVALID_ZONE", f"服务 {service} 的时区无法识别: {zone}")
    return zones


def _events(payload: dict) -> list:
    return payload["events"] if "events" in payload else list(core.DEMO_EVENTS)


def rebuild(store, payload: dict | None, rule_version: str = DEFAULT_RULE_VERSION) -> dict:
    """重算小时桶。同一输入摘要重试复用原批次；中断凭断点续跑；成功才切换当前指针。"""
    payload = payload or {}
    events = _events(payload)
    zones = _zones(payload)
    chains = store.correction_chains()
    manifest = {"events": events, "zones": zones, "chains": chains,
                "rule_version": rule_version}
    token = canonical_digest(manifest)

    existing = store.get_batch(token)
    if existing:
        return {"status": "READY", "run_id": existing["run_id"],
                "deduplicated": True, "resumed": False, **existing["result"]}

    state = store.get_checkpoint(token)
    resumed = bool(state and state["offset"] > 0)
    from_offset = state["offset"] if state else 0
    interrupt_after = payload.get("_interrupt_after")
    chunk_size = int(payload.get("chunk_size") or 500)
    try:
        state = core.rebuild(events, zones, chains, state=state, chunk_size=chunk_size,
                             interrupt_after=interrupt_after,
                             on_checkpoint=lambda s: store.save_checkpoint(token, s))
    except core.RecomputeInterrupted as exc:
        store.save_checkpoint(token, exc.state)
        return {"status": "INTERRUPTED", "resume_token": token,
                "processed": exc.state["offset"], "total": len(events),
                "message": "重算中断，使用同一请求重试即可从断点续跑；当前批次沿用旧版"}

    result = core.result_from(state, events, zones, chains, rule_version)
    run_id = "run-" + token[:12]
    committed = store.commit_batch(token, run_id, result)
    return {"status": "READY", "run_id": committed["run_id"], "resumed": resumed,
            "from_offset": from_offset, "deduplicated": False, **committed["result"]}


def correct(store, payload: dict, rule_version: str = DEFAULT_RULE_VERSION) -> dict:
    """登记服务时区更正：注明生效范围与理由；base_version 过期即拒绝。"""
    payload = payload or {}
    service = str(payload.get("service", "")).strip()
    zone = str(payload.get("zone", "")).strip()
    effective_from = str(payload.get("effective_from", "")).strip()
    reason = str(payload.get("reason", "")).strip()
    if not service:
        raise DomainError("EMPTY_VALUE", "服务名不能为空")
    if not reason:
        raise DomainError("EMPTY_VALUE", "必须注明更正理由")
    try:
        ZoneInfo(zone)
    except Exception:
        raise DomainError("INVALID_ZONE", f"时区名称无法识别: {zone}")
    try:
        eff = datetime.fromisoformat(effective_from)
    except ValueError:
        raise DomainError("INVALID_VALUE", "effective_from 需为 ISO 本地时间，如 2026-11-15T00:00:00")
    if eff.tzinfo is not None:
        raise DomainError("INVALID_VALUE", "effective_from 按服务本地时间登记，不带偏移")
    if payload.get("base_version") is None:
        raise DomainError("EMPTY_VALUE", "缺少 base_version（并发控制所需）")
    try:
        base_version = int(payload["base_version"])
    except (TypeError, ValueError):
        raise DomainError("INVALID_VALUE", "base_version 需为整数")
    row = store.add_correction(service, zone, eff.isoformat(), reason,
                               base_version, _now_z())
    return {"status": "READY", "message": "更正已登记，重算后对生效范围内的日志生效", **row}


def page_data(store, rule_version: str = DEFAULT_RULE_VERSION) -> dict:
    """组装分桶页：当前批次、更正前后对照、隔离原因、历次更正与留存批次。"""
    chains = store.correction_chains()
    zones = _zones({})
    current = store.current_batch()
    if current:
        batch = current["result"]
        status, run_id = "READY", current["run_id"]
        events = batch.get("events") or list(core.DEMO_EVENTS)
    else:
        preview = core.rebuild(list(core.DEMO_EVENTS), zones, chains)
        batch = core.result_from(preview, list(core.DEMO_EVENTS), zones, chains, rule_version)
        status, run_id = "PREVIEW", "（未提交）"
        events = list(core.DEMO_EVENTS)
    compare_rows = core.compare(events, zones, chains)
    quarantine = [dict(r, reason_text=reason_text(r["reason"]))
                  for r in batch["evidence"] if r["decision"] == "quarantine"]
    history = store.corrections()
    current_run = current["run_id"] if current else None
    return {
        "status": status,
        "run_id": run_id,
        "input_count": batch["input_count"],
        "valid_count": batch["valid_count"],
        "quarantine_count": batch["quarantine_count"],
        "reason": batch["reason"],
        "buckets": batch["buckets"],
        "evidence": batch["evidence"],
        "compare": compare_rows,
        "moved_count": sum(1 for r in compare_rows if r["moved"]),
        "quarantine": quarantine,
        "corrections": [c for c in history if c["status"] == "ACTIVE"],
        "history": sorted(history, key=lambda c: (c["service"], -c["version"])),
        "latest_versions": {svc: chain[-1]["version"] for svc, chain in chains.items()},
        "zones": zones,
        "batches": [{"run_id": b["run_id"], "created_at": b["created_at"],
                     "input_count": b["result"]["input_count"],
                     "valid_count": b["result"]["valid_count"],
                     "quarantine_count": b["result"]["quarantine_count"],
                     "current": b["run_id"] == current_run}
                    for b in store.batches()],
    }
