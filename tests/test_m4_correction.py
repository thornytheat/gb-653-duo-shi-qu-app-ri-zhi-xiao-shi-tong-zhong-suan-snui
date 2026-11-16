import pytest
from app.modules.m4_buckets import core

# ---- 单元：无时区日志的时区解释 ----

def test_gap_quarantined():
    utc, fold, reason = core.interpret_local("2026-03-08T02:30:00", "America/New_York")
    assert (utc, fold, reason) == (None, None, "DST_GAP")

def test_overlap_requires_fold():
    _, _, reason = core.interpret_local("2026-11-01T01:30:00", "America/New_York")
    assert reason == "FOLD_REQUIRED"

def test_overlap_with_fold_accepted():
    utc, fold, reason = core.interpret_local("2026-11-01T01:30:00", "America/New_York", 1)
    assert (utc, fold, reason) == ("2026-11-01T06:30:00Z", 1, None)

def test_fold_cannot_save_gap():
    _, _, reason = core.interpret_local("2026-03-08T02:30:00", "America/New_York", 1)
    assert reason == "DST_GAP"

def test_zone_for_effective_range():
    chain = [{"version": 1, "zone": "America/New_York", "effective_from": "2026-11-15T00:00:00"}]
    assert core.zone_for("pay", "2026-11-14T23:59:59", core.BASE_ZONES, chain) == ("Asia/Shanghai", 0)
    assert core.zone_for("pay", "2026-11-15T00:00:00", core.BASE_ZONES, chain) == ("America/New_York", 1)

def test_zone_for_latest_version_wins():
    chain = [{"version": 1, "zone": "America/New_York", "effective_from": "2026-01-01T00:00:00"},
             {"version": 2, "zone": "Asia/Shanghai", "effective_from": "2026-11-01T00:00:00"}]
    assert core.zone_for("pay", "2026-06-01T00:00:00", core.BASE_ZONES, chain) == ("America/New_York", 1)
    assert core.zone_for("pay", "2026-12-01T00:00:00", core.BASE_ZONES, chain) == ("Asia/Shanghai", 2)

# ---- API：更正登记与并发 ----

def _correct(client, **kw):
    body = {"service": "pay", "zone": "America/New_York",
            "effective_from": "2026-11-15T00:00:00",
            "reason": "服务搬迁，时区更正", "base_version": 0}
    body.update(kw)
    return client.post("/api/buckets/correct", json=body)

def test_correct_registers_and_versions(client):
    r1 = _correct(client)
    assert r1.status_code == 200 and r1.json()["version"] == 1
    r2 = _correct(client, zone="Asia/Shanghai", base_version=1, reason="回切上海")
    assert r2.status_code == 200 and r2.json()["version"] == 2

def test_stale_decision_rejected(client):
    assert _correct(client).status_code == 200
    stale = _correct(client, base_version=0)  # 并发下基于过期版本的决定
    assert stale.status_code == 422 and stale.json()["error"] == "STALE_VERSION"

def test_correct_requires_reason(client):
    r = _correct(client, reason="")
    assert r.status_code == 422 and r.json()["error"] == "EMPTY_VALUE"

def test_correct_rejects_bad_zone(client):
    r = _correct(client, zone="Mars/Olympus")
    assert r.status_code == 422 and r.json()["error"] == "INVALID_ZONE"

def test_correct_rejects_offset_effective_from(client):
    r = _correct(client, effective_from="2026-11-15T00:00:00+08:00")
    assert r.status_code == 422 and r.json()["error"] == "INVALID_VALUE"

# ---- API：重算、隔离、对照 ----

EVENTS = [
    {"request_id": "e1", "timestamp": "2026-11-15T09:10:00", "service": "pay", "status": 200},
    {"request_id": "e2", "timestamp": "2026-11-01T01:30:00", "service": "search", "status": 200},
    {"request_id": "e3", "timestamp": "2026-11-01T01:45:00", "service": "search", "status": 500, "fold": 1},
    {"request_id": "e4", "timestamp": "2026-03-08T02:30:00", "service": "search", "status": 200},
]

def test_rebuild_quarantine_reasons(client):
    data = client.post("/api/buckets/rebuild", json={"events": EVENTS}).json()
    assert data["status"] == "READY"
    assert data["input_count"] == 4 and data["valid_count"] == 2 and data["quarantine_count"] == 2
    reasons = {r["request_id"]: r["reason"] for r in data["evidence"] if r["decision"] == "quarantine"}
    assert reasons == {"e2": "FOLD_REQUIRED", "e4": "DST_GAP"}
    fold_row = [r for r in data["evidence"] if r["request_id"] == "e3"][0]
    assert fold_row["decision"] == "accepted" and fold_row["fold"] == 1

def test_correction_moves_hour_within_range_only(client):
    before = client.post("/api/buckets/rebuild", json={"events": EVENTS}).json()
    hour_before = {r["request_id"]: r.get("hour") for r in before["evidence"]}
    assert hour_before["e1"] == "2026-11-15T01:00:00Z"  # Asia/Shanghai
    assert _correct(client).status_code == 200
    events = EVENTS + [{"request_id": "e0", "timestamp": "2026-11-14T20:00:00",
                        "service": "pay", "status": 200}]
    after = client.post("/api/buckets/rebuild", json={"events": events}).json()
    hour_after = {r["request_id"]: r.get("hour") for r in after["evidence"]}
    assert hour_after["e1"] == "2026-11-15T14:00:00Z"  # 生效范围内改用 New_York
    assert hour_after["e0"] == "2026-11-14T12:00:00Z"  # 生效范围外仍用 Shanghai
    zone_used = {r["request_id"]: r.get("zone") for r in after["evidence"]}
    assert zone_used["e2"] == "America/New_York"  # 其他服务不受影响

def test_retry_does_not_duplicate_batch(client):
    first = client.post("/api/buckets/rebuild", json={"events": EVENTS}).json()
    second = client.post("/api/buckets/rebuild", json={"events": EVENTS}).json()
    assert first["run_id"] == second["run_id"] and second["deduplicated"] is True
    batches = client.get("/api/buckets/batches").json()["batches"]
    assert len(batches) == 1

def test_interrupt_then_resume(client):
    payload = {"events": EVENTS, "chunk_size": 2, "_interrupt_after": 2}
    cut = client.post("/api/buckets/rebuild", json=payload).json()
    assert cut["status"] == "INTERRUPTED" and cut["processed"] == 2
    done = client.post("/api/buckets/rebuild", json={"events": EVENTS}).json()
    assert done["status"] == "READY" and done["resumed"] is True and done["from_offset"] == 2
    assert done["input_count"] == 4
    assert len(client.get("/api/buckets/batches").json()["batches"]) == 1

def test_failed_switch_keeps_old_current(client):
    ok = client.post("/api/buckets/rebuild", json={"events": EVENTS}).json()
    cut = client.post("/api/buckets/rebuild",
                      json={"events": EVENTS + EVENTS, "_interrupt_after": 1}).json()
    assert cut["status"] == "INTERRUPTED"
    current = client.get("/api/buckets/batches").json()["current"]
    assert current == ok["run_id"]  # 切换失败沿用旧版

def test_history_and_retained_batch_reproduce(client):
    first = client.post("/api/buckets/rebuild", json={"events": EVENTS}).json()
    assert _correct(client).status_code == 200
    assert _correct(client, zone="Asia/Shanghai", base_version=1, reason="回切").status_code == 200
    history = client.get("/buckets").text
    assert "服务搬迁，时区更正" in history and "回切" in history  # 历次解释保留
    batches = client.get("/api/buckets/batches").json()["batches"]
    retained = [b for b in batches if b["run_id"] == first["run_id"]][0]
    zone_used = {r["request_id"]: r.get("zone") for r in retained["result"]["evidence"]}
    assert zone_used["e1"] == "Asia/Shanghai"  # 留存批次仍按原时区复现
    assert retained["result"]["corrections_used"] == {}  # 当时无更正决定

def test_page_renders_review_sections(client):
    _correct(client)
    client.post("/api/buckets/rebuild", json={"events": EVENTS})
    html = client.get("/buckets").text
    for text in ("更正前后对照", "隔离记录", "更正决定与历次解释", "留存批次",
                 "FOLD_REQUIRED", "DST_GAP", "服务搬迁，时区更正"):
        assert text in html
