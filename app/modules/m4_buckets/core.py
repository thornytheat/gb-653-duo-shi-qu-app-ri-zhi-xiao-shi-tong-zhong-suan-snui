from datetime import datetime, timezone
from zoneinfo import ZoneInfo

SAMPLE={"events":[{"timestamp":"2026-11-15T01:00:00Z","service":"pay","status":200},{"timestamp":"2026-11-15T01:59:59Z","service":"pay","status":500},{"timestamp":"2026-11-15T02:00:00Z","service":"pay","status":200}]}
def hour_key(value):
    dt=datetime.fromisoformat(value.replace("Z","+00:00")); return dt.strftime("%Y-%m-%dT%H:00:00Z")
def bucket(events):
    out={}
    for row in events:
        key=(hour_key(row["timestamp"]),row["service"]); item=out.setdefault(key,{"hour":key[0],"service":key[1],"count":0,"errors":0}); item["count"]+=1; item["errors"]+=int(int(row["status"])>=500)
    return sorted(out.values(),key=lambda x:(x["hour"],x["service"]))
def run(payload):
    events=payload.get("events",SAMPLE["events"]); out=bucket(events); return {"status":"READY","input_count":len(events),"valid_count":sum(x["count"] for x in out),"quarantine_count":0,"buckets":len(out),"errors":sum(x["errors"] for x in out),"reason":"OK"}


# ---------------------------------------------------------------------------
# 时区更正引擎：无时区日志按服务生效中的更正决定解释；历史批次凭留存证据复现。
# ---------------------------------------------------------------------------

# 服务基础时区（未登记任何更正时采用），与 sample_data/zones.csv 一致。
BASE_ZONES = {"pay": "Asia/Shanghai", "search": "America/New_York"}

# 演示样例：覆盖带偏移、无时区、DST 重叠（缺 fold / 带 fold）与跳时空档。
DEMO_EVENTS = [
    {"request_id": "d1", "timestamp": "2026-11-15T01:05:00Z", "service": "pay", "status": 200},
    {"request_id": "d2", "timestamp": "2026-11-01T01:30:00", "service": "search", "status": 200},
    {"request_id": "d3", "timestamp": "2026-11-01T01:45:00", "service": "search", "status": 500, "fold": 1},
    {"request_id": "d4", "timestamp": "2026-03-08T02:30:00", "service": "search", "status": 200},
    {"request_id": "d5", "timestamp": "2026-11-15T09:10:00", "service": "pay", "status": 200},
    {"request_id": "d6", "timestamp": "2026-11-15T17:30:00", "service": "pay", "status": 500},
]


class RecomputeInterrupted(Exception):
    """重算中断：携带已处理进度，调用方落断点后可凭同一令牌续跑。"""

    def __init__(self, state: dict):
        self.state = state
        super().__init__(f"recompute interrupted at offset {state['offset']}")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def interpret_local(value: str, zone: str, fold=None):
    """无时区本地时间按指定时区解释。

    返回 (utc_iso, fold_used, reason)：
    - 落在跳时空档 -> (None, None, "DST_GAP")，fold 无法挽救；
    - 重叠时段缺 fold -> (None, None, "FOLD_REQUIRED")；
    - 其余 -> (UTC ISO, 实际采用的 fold 或 None, None)。
    """
    naive = datetime.fromisoformat(value)
    z = ZoneInfo(zone)
    back = naive.replace(tzinfo=z, fold=0).astimezone(timezone.utc).astimezone(z).replace(tzinfo=None)
    if back != naive:
        return None, None, "DST_GAP"
    ambiguous = (naive.replace(tzinfo=z, fold=0).utcoffset()
                 != naive.replace(tzinfo=z, fold=1).utcoffset())
    if ambiguous:
        if fold is None:
            return None, None, "FOLD_REQUIRED"
        if str(fold) not in ("0", "1"):
            return None, None, "INVALID_VALUE"
        chosen = int(fold)
        dt = naive.replace(tzinfo=z, fold=chosen)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), chosen, None
    dt = naive.replace(tzinfo=z, fold=0)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), None, None


def zone_for(service: str, local_value: str, zones: dict, chain: list):
    """按生效范围解析服务时区：effective_from <= 本地时间 的决定中版本最高者。

    返回 (zone, correction_version)；无更正决定时回退基础时区，版本记 0。
    """
    naive = datetime.fromisoformat(local_value)
    hit = None
    for decision in chain:
        if datetime.fromisoformat(decision["effective_from"]) <= naive:
            if hit is None or decision["version"] > hit["version"]:
                hit = decision
    if hit:
        return hit["zone"], hit["version"]
    if service in zones:
        return zones[service], 0
    return None, 0


def _status_of(event: dict) -> int:
    try:
        return int(event.get("status", 200))
    except (TypeError, ValueError):
        return 200


def resolve_event(event: dict, zones: dict, chains: dict) -> dict:
    """单条日志的归属判定，产出可留存的证据行（全部为 JSON 可序列化值）。"""
    raw = str(event.get("timestamp", ""))
    service = str(event.get("service", ""))
    ev = {"request_id": str(event.get("request_id", "")), "service": service,
          "raw": raw, "status": _status_of(event)}
    if event.get("fold") is not None:
        ev["fold_given"] = event.get("fold")
    try:
        aware = _parse(raw).tzinfo is not None
    except ValueError:
        return ev | {"decision": "quarantine", "reason": "INVALID_VALUE",
                     "detail": "时间格式无法解析"}
    if aware:
        utc = _parse(raw).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return ev | {"decision": "accepted", "zone": "(offset)", "correction_version": 0,
                     "fold": None, "utc": utc, "hour": hour_key(utc)}
    zone, version = zone_for(service, raw, zones, chains.get(service, []))
    if zone is None:
        return ev | {"decision": "quarantine", "reason": "ZONE_REQUIRED",
                     "detail": "无时区日志且服务未登记时区"}
    utc, fold_used, reason = interpret_local(raw, zone, event.get("fold"))
    if reason:
        return ev | {"decision": "quarantine", "reason": reason,
                     "zone": zone, "correction_version": version}
    return ev | {"decision": "accepted", "zone": zone, "correction_version": version,
                 "fold": fold_used, "utc": utc, "hour": hour_key(utc)}


def empty_state() -> dict:
    return {"offset": 0, "accepted": [], "quarantine": []}


def rebuild(events, zones, chains, state=None, chunk_size=500,
            interrupt_after=None, on_checkpoint=None):
    """逐条判定小时归属；支持断点续跑（state 续传）与中断注入（interrupt_after）。"""
    state = state or empty_state()
    total = len(events)
    while state["offset"] < total:
        res = resolve_event(events[state["offset"]], zones, chains)
        res["seq"] = state["offset"]
        state["accepted" if res["decision"] == "accepted" else "quarantine"].append(res)
        state["offset"] += 1
        if interrupt_after is not None and state["offset"] >= interrupt_after:
            raise RecomputeInterrupted(state)
        if on_checkpoint and state["offset"] % chunk_size == 0 and state["offset"] < total:
            on_checkpoint(state)
    return state


def result_from(state, events, zones, chains, rule_version) -> dict:
    """汇总重算结果：桶、逐条证据、原日志与所用决定版本一并留存，供历史复现。"""
    rows = [{"timestamp": r["utc"], "service": r["service"], "status": r["status"]}
            for r in state["accepted"]]
    buckets = bucket(rows)
    evidence = sorted(state["accepted"] + state["quarantine"], key=lambda r: r["seq"])
    return {
        "rule_version": rule_version,
        "input_count": len(evidence),
        "valid_count": len(state["accepted"]),
        "quarantine_count": len(state["quarantine"]),
        "buckets": buckets,
        "bucket_count": len(buckets),
        "errors": sum(b["errors"] for b in buckets),
        "evidence": evidence,
        "events": events,
        "zones": dict(zones),
        "corrections_used": {svc: [c["version"] for c in chain]
                             for svc, chain in chains.items()},
        "reason": "OK" if not state["quarantine"] else state["quarantine"][0]["reason"],
    }


def _label(res: dict) -> str:
    if res["decision"] != "accepted":
        return f"隔离·{res['reason']}"
    return f"{res['hour']} · {res['zone']}"


def compare(events, zones, chains) -> list:
    """对照最近一次更正前后的小时归属；before 为去掉各服务最新版本后的决定链。"""
    before_chains = {svc: chain[:-1] for svc, chain in chains.items() if chain}
    out = []
    for seq, event in enumerate(events):
        before = resolve_event(event, zones, before_chains)
        after = resolve_event(event, zones, chains)
        b, a = _label(before), _label(after)
        out.append({"seq": seq, "request_id": str(event.get("request_id", "")),
                    "service": str(event.get("service", "")),
                    "raw": str(event.get("timestamp", "")),
                    "before": b, "after": a, "moved": b != a})
    return out
