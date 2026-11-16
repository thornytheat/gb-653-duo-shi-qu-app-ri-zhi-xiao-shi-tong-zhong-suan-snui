from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .errors import DomainError

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS runs (digest TEXT PRIMARY KEY, run_id TEXT NOT NULL, result TEXT NOT NULL)",
    # M4 时区更正决定：同一服务的历次版本全部保留（历次解释可查），status 仅标记是否最新。
    """CREATE TABLE IF NOT EXISTS corrections (
        service TEXT NOT NULL, version INTEGER NOT NULL,
        zone TEXT NOT NULL, effective_from TEXT NOT NULL,
        reason TEXT NOT NULL, decided_at TEXT NOT NULL, status TEXT NOT NULL,
        PRIMARY KEY (service, version))""",
    # M4 留存批次：digest 幂等，重试不重复生成；结果含原日志与逐条证据，永不改写。
    """CREATE TABLE IF NOT EXISTS bucket_batches (
        digest TEXT PRIMARY KEY, run_id TEXT NOT NULL,
        result TEXT NOT NULL, created_at TEXT NOT NULL)""",
    # 当前批次指针：只在重算成功提交时同事务切换，失败沿用旧版。
    """CREATE TABLE IF NOT EXISTS bucket_current (
        scope TEXT PRIMARY KEY, digest TEXT NOT NULL,
        run_id TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    # 重算断点：按输入摘要令牌保存进度，中断后凭同一令牌续跑。
    """CREATE TABLE IF NOT EXISTS bucket_checkpoints (
        token TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at TEXT NOT NULL)""",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            for stmt in SCHEMA:
                db.execute(stmt)

    def connect(self):
        return sqlite3.connect(self.path)

    # ---- 通用运行记录（M6 对账沿用，行为不变） ----
    def record(self, digest: str, run_id: str, result: dict) -> dict:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO runs VALUES (?, ?, ?)",
                       (digest, run_id, json.dumps(result, ensure_ascii=False, sort_keys=True)))
            row = db.execute("SELECT run_id, result FROM runs WHERE digest=?", (digest,)).fetchone()
        return {"run_id": row[0], "result": json.loads(row[1])}

    def count(self) -> int:
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    def all(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT run_id, digest, result FROM runs ORDER BY run_id").fetchall()
        return [{"run_id": r[0], "digest": r[1], "result": json.loads(r[2])} for r in rows]

    # ---- M4 更正决定 ----
    def add_correction(self, service: str, zone: str, effective_from: str,
                       reason: str, base_version: int, decided_at: str) -> dict:
        """登记一条更正决定。base_version 与当前最新版本不符即拒绝（过期决定）。"""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute(
                "SELECT COALESCE(MAX(version), 0) FROM corrections WHERE service=?",
                (service,)).fetchone()[0]
            if latest != base_version:
                raise DomainError("STALE_VERSION",
                                  f"服务 {service} 当前版本为 v{latest}，提交的 base_version=v{base_version} 已过期")
            version = latest + 1
            db.execute("UPDATE corrections SET status='SUPERSEDED' WHERE service=?", (service,))
            db.execute("INSERT INTO corrections VALUES (?,?,?,?,?,?,?)",
                       (service, version, zone, effective_from, reason, decided_at, "ACTIVE"))
        return {"service": service, "version": version, "zone": zone,
                "effective_from": effective_from, "reason": reason,
                "decided_at": decided_at, "status": "ACTIVE"}

    def corrections(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT service, version, zone, effective_from, reason, decided_at, status "
                "FROM corrections ORDER BY service, version").fetchall()
        keys = ("service", "version", "zone", "effective_from", "reason", "decided_at", "status")
        return [dict(zip(keys, r)) for r in rows]

    def correction_chains(self) -> dict[str, list[dict]]:
        """每个服务的全部决定（版本升序），供生效范围解析使用；历史版本不删除。"""
        chains: dict[str, list[dict]] = {}
        for row in self.corrections():
            chains.setdefault(row["service"], []).append(row)
        return chains

    # ---- M4 批次与切换 ----
    def get_batch(self, digest: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT run_id, result, created_at FROM bucket_batches WHERE digest=?",
                (digest,)).fetchone()
        if not row:
            return None
        return {"run_id": row[0], "result": json.loads(row[1]), "created_at": row[2]}

    def commit_batch(self, digest: str, run_id: str, result: dict,
                     scope: str = "buckets:current") -> dict:
        """批次落库 + 当前指针切换 + 断点清理，同一事务；任何一步失败整体回滚，沿用旧版。"""
        now = _now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO bucket_batches VALUES (?,?,?,?)",
                       (digest, run_id, json.dumps(result, ensure_ascii=False, sort_keys=True), now))
            row = db.execute("SELECT run_id, result FROM bucket_batches WHERE digest=?",
                             (digest,)).fetchone()
            db.execute(
                "INSERT INTO bucket_current VALUES (?,?,?,?) "
                "ON CONFLICT(scope) DO UPDATE SET digest=excluded.digest, "
                "run_id=excluded.run_id, updated_at=excluded.updated_at",
                (scope, digest, row[0], now))
            db.execute("DELETE FROM bucket_checkpoints WHERE token=?", (digest,))
        return {"run_id": row[0], "result": json.loads(row[1])}

    def current_batch(self, scope: str = "buckets:current") -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT c.digest, c.run_id, c.updated_at, b.result "
                "FROM bucket_current c JOIN bucket_batches b ON b.digest = c.digest "
                "WHERE c.scope=?", (scope,)).fetchone()
        if not row:
            return None
        return {"digest": row[0], "run_id": row[1], "updated_at": row[2],
                "result": json.loads(row[3])}

    def batches(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT run_id, digest, result, created_at FROM bucket_batches "
                "ORDER BY created_at, run_id").fetchall()
        return [{"run_id": r[0], "digest": r[1], "result": json.loads(r[2]),
                 "created_at": r[3]} for r in rows]

    # ---- M4 断点续跑 ----
    def save_checkpoint(self, token: str, state: dict) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO bucket_checkpoints VALUES (?,?,?) "
                "ON CONFLICT(token) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
                (token, json.dumps(state, ensure_ascii=False, sort_keys=True), _now()))

    def get_checkpoint(self, token: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT state FROM bucket_checkpoints WHERE token=?",
                             (token,)).fetchone()
        return json.loads(row[0]) if row else None
