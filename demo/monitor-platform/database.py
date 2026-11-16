"""SQLite 数据访问层：web_app 与 monitor_worker 两进程共用。

- 所有路径基于本文件位置计算，不依赖启动目录（cwd）；
- 每次操作新建连接、用完即关（try/finally），不跨请求/跨轮询持有连接；
- WAL 模式 + timeout=5.0：支撑 web 与 worker 两进程并发读写；
- 表结构用 PRAGMA user_version 版本化，升级时在 BEGIN IMMEDIATE 事务内重建。
"""

import os
import sqlite3
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_MODE = os.environ.get("DEMO_MODE", "").strip() == "1"
_DB_NAME = "monitor_platform_demo.db" if _DEMO_MODE else "monitor_platform.db"
DB_PATH = os.path.join(BASE_DIR, _DB_NAME)
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

SCHEMA_VERSION = 4

_MONITOR_ITEMS_DDL = """
CREATE TABLE IF NOT EXISTS monitor_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at  TEXT NOT NULL
)
"""

_MONITOR_DATA_DDL = """
CREATE TABLE IF NOT EXISTS monitor_data (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES monitor_items(id),
    date        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','generating','success','failed')),
    html_path   TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE(item_id, date)
)
"""

_MONITOR_SCHEDULES_DDL = """
CREATE TABLE IF NOT EXISTS monitor_schedules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    schedule_type   TEXT NOT NULL CHECK (schedule_type IN ('generate','push')),
    item_id         INTEGER REFERENCES monitor_items(id),
    cron_time       TEXT NOT NULL,
    meeting_code    TEXT DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_run_at     TEXT,
    last_run_result TEXT,
    created_at      TEXT NOT NULL
)
"""

# ---- 工事良率监控 ----

_YIELD_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS monitor_yield_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ecpm_id         TEXT NOT NULL UNIQUE,
    ecpm_title      TEXT DEFAULT '',
    machine         TEXT NOT NULL,
    create_time     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running','completed','cancelled')),
    fast_end_time   TEXT NOT NULL,
    slow_end_time   TEXT NOT NULL,
    fast_last_at    TEXT,
    slow_last_at    TEXT,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    report_path     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
)
"""

_YIELD_SNAPS_DDL = """
CREATE TABLE IF NOT EXISTS monitor_yield_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         INTEGER NOT NULL REFERENCES monitor_yield_tasks(id),
    snapshot_type   TEXT NOT NULL CHECK (snapshot_type IN ('fast','slow')),
    run_at          TEXT NOT NULL,
    run_seq         INTEGER NOT NULL,
    before_matched  INTEGER DEFAULT 0,
    after_matched   INTEGER DEFAULT 0,
    density_hours   INTEGER DEFAULT 0,
    density_top10   TEXT,
    yield_task_count   INTEGER DEFAULT 0,
    yield_result_count INTEGER DEFAULT 0,
    yield_overall_json TEXT,
    full_result_json   TEXT,
    yield_data_json    TEXT,
    status          TEXT DEFAULT 'success',
    error_msg       TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
)
"""

CREATE_YIELD_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_my_tasks_status ON monitor_yield_tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_my_snap_task ON monitor_yield_snapshots(task_id, snapshot_type, run_seq)",
]

# ---- 加捡/Hold货报告 ----

_HOLD_REPORT_DATA_DDL = """
CREATE TABLE IF NOT EXISTS hold_report_data (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_date    TEXT NOT NULL,
    site            TEXT NOT NULL CHECK (site IN ('T6', 'T7')),
    data_json       TEXT,
    record_count    INTEGER DEFAULT 0,
    glass_count     INTEGER DEFAULT 0,
    generated_at    TEXT NOT NULL,
    UNIQUE(company_date, site)
)
"""


def _now() -> str:
    """当前本地时间字符串，统一格式写入数据库。"""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _parse_db_time(s):
    """解析数据库时间字符串为 datetime（供 worker 使用）。"""
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt)
        except (ValueError, IndexError):
            continue
    return None


def get_connection() -> sqlite3.Connection:
    """新建一个 SQLite 连接。"""
    conn = sqlite3.connect(DB_PATH, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """幂等初始化数据库：创建表，检查版本升级。"""
    conn = get_connection()
    try:
        # 检查当前版本
        cur = conn.execute("PRAGMA user_version")
        current_version = cur.fetchone()[0]

        if current_version < SCHEMA_VERSION:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(_MONITOR_ITEMS_DDL)
                conn.execute(_MONITOR_DATA_DDL)
                conn.execute(_MONITOR_SCHEDULES_DDL)
                conn.execute(_YIELD_TASKS_DDL)
                conn.execute(_YIELD_SNAPS_DDL)
                for idx_sql in CREATE_YIELD_INDEXES:
                    conn.execute(idx_sql)
                conn.execute(_HOLD_REPORT_DATA_DDL)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.execute("COMMIT")
                conn.execute("PRAGMA foreign_keys = ON")
                print(f"[数据库] 初始化完成，版本 {SCHEMA_VERSION}")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        else:
            # 确保表存在（已初始化过的环境）
            conn.execute(_MONITOR_ITEMS_DDL)
            conn.execute(_MONITOR_DATA_DDL)
            conn.execute(_MONITOR_SCHEDULES_DDL)
            conn.execute(_YIELD_TASKS_DDL)
            conn.execute(_YIELD_SNAPS_DDL)
            for idx_sql in CREATE_YIELD_INDEXES:
                conn.execute(idx_sql)
            conn.execute(_HOLD_REPORT_DATA_DDL)
    finally:
        conn.close()


# ---------- 监控事项 CRUD ----------

def list_items() -> list[dict]:
    """返回所有监控事项，按 id 排序。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, description, created_at FROM monitor_items ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_item(name: str, description: str = "") -> dict:
    """添加一条监控事项，返回完整记录。"""
    conn = get_connection()
    try:
        now = _now()
        cur = conn.execute(
            "INSERT INTO monitor_items (name, description, created_at) VALUES (?, ?, ?)",
            (name.strip(), description.strip(), now),
        )
        conn.commit()
        return dict(
            conn.execute(
                "SELECT id, name, description, created_at FROM monitor_items WHERE id = ?",
                (cur.lastrowid,),
            ).fetchone()
        )
    finally:
        conn.close()


def update_item(item_id: int, name: str, description: str = "") -> dict | None:
    """编辑一条监控事项，返回更新后的记录；不存在返回 None。"""
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM monitor_items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE monitor_items SET name = ?, description = ? WHERE id = ?",
            (name.strip(), description.strip(), item_id),
        )
        conn.commit()
        return dict(
            conn.execute(
                "SELECT id, name, description, created_at FROM monitor_items WHERE id = ?",
                (item_id,),
            ).fetchone()
        )
    finally:
        conn.close()


def delete_item(item_id: int) -> bool:
    """删除监控事项及其关联的监控数据；返回是否成功删除。"""
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM monitor_items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM monitor_data WHERE item_id = ?", (item_id,))
        conn.execute("DELETE FROM monitor_items WHERE id = ?", (item_id,))
        conn.commit()
        return True
    finally:
        conn.close()


# ---------- 监控数据操作 ----------

def get_or_create_monitor_data(item_id: int, date: str) -> dict:
    """获取指定事项在指定日期的监控数据记录；不存在则自动创建（status=pending）。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, item_id, date, status, html_path, created_at FROM monitor_data "
            "WHERE item_id = ? AND date = ?",
            (item_id, date),
        ).fetchone()
        if row:
            return dict(row)
        now = _now()
        cur = conn.execute(
            "INSERT INTO monitor_data (item_id, date, status, html_path, created_at) "
            "VALUES (?, ?, 'pending', NULL, ?)",
            (item_id, date, now),
        )
        conn.commit()
        return dict(
            conn.execute(
                "SELECT id, item_id, date, status, html_path, created_at FROM monitor_data WHERE id = ?",
                (cur.lastrowid,),
            ).fetchone()
        )
    finally:
        conn.close()


def list_monitor_data_by_date(date: str) -> list[dict]:
    """返回指定日期的所有监控数据（含事项名称），按事项 id 排序。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT d.id, d.item_id, d.date, d.status, d.html_path, d.created_at, i.name "
            "FROM monitor_data d JOIN monitor_items i ON d.item_id = i.id "
            "WHERE d.date = ? ORDER BY i.id",
            (date,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def claim_pending_task() -> dict | None:
    """取一条 status='pending' 的监控数据，将其状态改为 generating 并返回；
    无待处理任务时返回 None。"""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT d.id, d.item_id, d.date, d.status, d.html_path, d.created_at, i.name "
                "FROM monitor_data d JOIN monitor_items i ON d.item_id = i.id "
                "WHERE d.status = 'pending' ORDER BY d.id LIMIT 1"
            ).fetchone()
            if not row:
                conn.execute("COMMIT")
                return None
            conn.execute(
                "UPDATE monitor_data SET status = 'generating' WHERE id = ?",
                (row["id"],),
            )
            conn.execute("COMMIT")
            return dict(row)
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def mark_task_result(task_id: int, status: str, html_path: str = "") -> None:
    """将监控数据标记为 success 或 failed，可选记录 html 路径。"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE monitor_data SET status = ?, html_path = ? WHERE id = ?",
            (status, html_path or None, task_id),
        )
        conn.commit()
    finally:
        conn.close()


def reset_stale_generating() -> None:
    """启时将卡在 generating 超过 10 分钟的任务重置为 pending。"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE monitor_data SET status = 'pending' "
            "WHERE status = 'generating' AND created_at <= datetime('now', 'localtime', '-10 minutes')"
        )
        conn.commit()
    finally:
        conn.close()


def ensure_data_pending(item_id: int, date: str) -> None:
    """确保指定事项+日期在 monitor_data 中有记录（如无则创建，状态为 pending）。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, status FROM monitor_data WHERE item_id = ? AND date = ?",
            (item_id, date),
        ).fetchone()
        if not row:
            # 不存在，创建新记录
            now = _now()
            conn.execute(
                "INSERT INTO monitor_data (item_id, date, status, html_path, created_at) "
                "VALUES (?, ?, 'pending', NULL, ?)",
                (item_id, date, now),
            )
        # 已存在的不做任何操作，保持原状态
        conn.commit()
    finally:
        conn.close()


def reset_all_to_pending(date: str) -> None:
    """将指定日期所有事项重置为 pending（用于每小时强制重新生成）。"""
    conn = get_connection()
    try:
        # 确保所有事项都有该日期的记录
        items = conn.execute("SELECT id FROM monitor_items").fetchall()
        for item in items:
            row = conn.execute(
                "SELECT id FROM monitor_data WHERE item_id = ? AND date = ?",
                (item["id"], date),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE monitor_data SET status = 'pending', html_path = NULL WHERE id = ?",
                    (row["id"],),
                )
            else:
                now = _now()
                conn.execute(
                    "INSERT INTO monitor_data (item_id, date, status, html_path, created_at) "
                    "VALUES (?, ?, 'pending', NULL, ?)",
                    (item["id"], date, now),
                )
        conn.commit()
    finally:
        conn.close()


def claim_pending_by_item(item_id: int, date: str) -> dict | None:
    """认领指定事项+日期的 pending 任务；返回任务或 None（非 pending 时）。"""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT d.id, d.item_id, d.date, d.status, d.html_path, d.created_at, i.name "
                "FROM monitor_data d JOIN monitor_items i ON d.item_id = i.id "
                "WHERE d.item_id = ? AND d.date = ? AND d.status = 'pending'",
                (item_id, date),
            ).fetchone()
            if not row:
                conn.execute("COMMIT")
                return None
            conn.execute(
                "UPDATE monitor_data SET status = 'generating' WHERE id = ?",
                (row["id"],),
            )
            conn.execute("COMMIT")
            return dict(row)
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def get_item(item_id: int) -> dict | None:
    """获取单个事项信息。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, name, description, created_at FROM monitor_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_data_for_date_readonly(date: str) -> list[dict]:
    """只读查询：返回所有事项在指定日期的数据状态（不自动创建记录）。

    没有记录的项返回 status='no_data', html_path=None。
    """
    conn = get_connection()
    try:
        items = conn.execute("SELECT id, name, description FROM monitor_items ORDER BY id").fetchall()
        data_rows = conn.execute(
            "SELECT id, item_id, date, status, html_path, created_at FROM monitor_data WHERE date = ?",
            (date,),
        ).fetchall()
        data_by_item = {r["item_id"]: dict(r) for r in data_rows}

        result = []
        for item in items:
            item_dict = dict(item)
            if item["id"] in data_by_item:
                d = data_by_item[item["id"]]
                d["name"] = item_dict["name"]
                d["description"] = item_dict.get("description", "")
                result.append(d)
            else:
                result.append({
                    "id": None,
                    "item_id": item["id"],
                    "date": date,
                    "status": "no_data",
                    "html_path": None,
                    "created_at": "",
                    "name": item_dict["name"],
                    "description": item_dict.get("description", ""),
                })
        return result
    finally:
        conn.close()


# ---------- 调度配置 ----------

def list_schedules() -> list[dict]:
    """返回所有调度任务，按 id 排序。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT s.id, s.name, s.schedule_type, s.item_id, s.cron_time,
                      s.meeting_code, s.enabled, s.last_run_at, s.last_run_result, s.created_at,
                      i.name as item_name
               FROM monitor_schedules s
               LEFT JOIN monitor_items i ON s.item_id = i.id
               ORDER BY s.id"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_schedule(name: str, schedule_type: str, item_id: int, cron_time: str,
                 meeting_code: str = "") -> dict:
    """添加一条调度配置。"""
    conn = get_connection()
    try:
        now = _now()
        cur = conn.execute(
            """INSERT INTO monitor_schedules (name, schedule_type, item_id, cron_time,
                       meeting_code, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (name.strip(), schedule_type, item_id, cron_time.strip(),
             meeting_code.strip(), now),
        )
        conn.commit()
        return dict(
            conn.execute(
                """SELECT s.id, s.name, s.schedule_type, s.item_id, s.cron_time,
                          s.meeting_code, s.enabled, s.last_run_at, s.last_run_result, s.created_at,
                          i.name as item_name
                   FROM monitor_schedules s
                   LEFT JOIN monitor_items i ON s.item_id = i.id
                   WHERE s.id = ?""",
                (cur.lastrowid,),
            ).fetchone()
        )
    finally:
        conn.close()


def update_schedule(schedule_id: int, **kwargs) -> dict | None:
    """编辑一条调度配置。支持部分更新，只传需要修改的字段。"""
    allowed = {"name", "schedule_type", "item_id", "cron_time", "meeting_code", "enabled"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return None

    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM monitor_schedules WHERE id = ?", (schedule_id,)).fetchone()
        if not row:
            return None

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values())
        values.append(schedule_id)
        conn.execute(f"UPDATE monitor_schedules SET {set_clause} WHERE id = ?", values)
        conn.commit()
        return dict(
            conn.execute(
                """SELECT s.id, s.name, s.schedule_type, s.item_id, s.cron_time,
                          s.meeting_code, s.enabled, s.last_run_at, s.last_run_result, s.created_at,
                          i.name as item_name
                   FROM monitor_schedules s
                   LEFT JOIN monitor_items i ON s.item_id = i.id
                   WHERE s.id = ?""",
                (schedule_id,),
            ).fetchone()
        )
    finally:
        conn.close()


def delete_schedule(schedule_id: int) -> bool:
    """删除一条调度配置。"""
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM monitor_schedules WHERE id = ?", (schedule_id,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM monitor_schedules WHERE id = ?", (schedule_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def toggle_schedule(schedule_id: int) -> bool:
    """切换调度启用/禁用状态。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, enabled FROM monitor_schedules WHERE id = ?", (schedule_id,)
        ).fetchone()
        if not row:
            return False
        new_enabled = 0 if row["enabled"] else 1
        conn.execute(
            "UPDATE monitor_schedules SET enabled = ? WHERE id = ?",
            (new_enabled, schedule_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def mark_schedule_run(schedule_id: int, result: str) -> None:
    """记录调度执行结果。"""
    conn = get_connection()
    try:
        now = _now()
        conn.execute(
            "UPDATE monitor_schedules SET last_run_at = ?, last_run_result = ? WHERE id = ?",
            (now, result[:500], schedule_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_enabled_schedules() -> list[dict]:
    """获取所有启用的调度任务。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT s.id, s.name, s.schedule_type, s.item_id, s.cron_time,
                      s.meeting_code, s.enabled, s.last_run_at, s.last_run_result, s.created_at,
                      i.name as item_name
               FROM monitor_schedules s
               LEFT JOIN monitor_items i ON s.item_id = i.id
               WHERE s.enabled = 1
               ORDER BY s.id"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------- 工事良率监控 CRUD ----------

def create_yield_task(ecpm_id: str, ecpm_title: str, machine: str,
                      create_time: str, fast_end_time: str, slow_end_time: str) -> dict:
    """创建良率监控任务。"""
    conn = get_connection()
    try:
        now = _now()
        conn.execute(
            """INSERT INTO monitor_yield_tasks
               (ecpm_id, ecpm_title, machine, create_time, fast_end_time, slow_end_time, started_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ecpm_id, ecpm_title, machine, create_time, fast_end_time, slow_end_time, now),
        )
        conn.commit()
        return dict(
            conn.execute(
                "SELECT * FROM monitor_yield_tasks WHERE id = ?",
                (conn.execute("SELECT last_insert_rowid()").fetchone()[0],),
            ).fetchone()
        )
    finally:
        conn.close()


def get_yield_task(ecpm_id: str = None, task_id: int = None) -> dict | None:
    """按 ecpm_id 或 task_id 获取任务。"""
    conn = get_connection()
    try:
        if task_id is not None:
            row = conn.execute(
                "SELECT * FROM monitor_yield_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        elif ecpm_id is not None:
            row = conn.execute(
                "SELECT * FROM monitor_yield_tasks WHERE ecpm_id = ?", (ecpm_id,)
            ).fetchone()
        else:
            return None
        return dict(row) if row else None
    finally:
        conn.close()


def get_running_tasks() -> list[dict]:
    """获取所有正在执行的任务。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM monitor_yield_tasks WHERE status = 'running' ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_yield_task_field(task_id: int, **kwargs) -> bool:
    """更新任务字段（部分更新）。"""
    allowed = {
        "status", "fast_last_at", "slow_last_at",
        "completed_at", "report_path",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    conn = get_connection()
    try:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [task_id]
        conn.execute(
            f"UPDATE monitor_yield_tasks SET {set_clause} WHERE id = ?", values
        )
        conn.commit()
        return True
    finally:
        conn.close()


def cancel_yield_task(task_id: int) -> bool:
    """取消任务。"""
    return update_yield_task_field(task_id, status="cancelled")


def complete_yield_task(task_id: int, report_path: str = "") -> bool:
    """标记任务完成。"""
    return update_yield_task_field(
        task_id,
        status="completed",
        completed_at=_now(),
        report_path=report_path,
    )


def add_yield_snapshot(task_id: int, snapshot_type: str, run_seq: int,
                       run_at: str = None,
                       before_matched: int = 0, after_matched: int = 0,
                       density_hours: int = 0, density_top10: str = "",
                       yield_task_count: int = 0, yield_result_count: int = 0,
                       yield_overall_json: str = "",
                       full_result_json: str = "",
                       yield_data_json: str = "",
                       status: str = "success", error_msg: str = "") -> dict:
    """添加一条快照记录。"""
    conn = get_connection()
    try:
        now = run_at or _now()
        conn.execute(
            """INSERT INTO monitor_yield_snapshots
               (task_id, snapshot_type, run_at, run_seq,
                before_matched, after_matched, density_hours, density_top10,
                yield_task_count, yield_result_count,
                yield_overall_json, full_result_json, yield_data_json,
                status, error_msg)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, snapshot_type, now, run_seq,
             before_matched, after_matched, density_hours, density_top10,
             yield_task_count, yield_result_count,
             yield_overall_json, full_result_json, yield_data_json,
             status, error_msg),
        )
        conn.commit()
        return dict(
            conn.execute(
                "SELECT * FROM monitor_yield_snapshots WHERE id = ?",
                (conn.execute("SELECT last_insert_rowid()").fetchone()[0],),
            ).fetchone()
        )
    finally:
        conn.close()


def get_snapshots(task_id: int, snapshot_type: str = None) -> list[dict]:
    """获取某任务的所有快照，可按类型过滤。"""
    conn = get_connection()
    try:
        if snapshot_type:
            rows = conn.execute(
                """SELECT * FROM monitor_yield_snapshots
                   WHERE task_id = ? AND snapshot_type = ?
                   ORDER BY run_seq""",
                (task_id, snapshot_type),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM monitor_yield_snapshots WHERE task_id = ? ORDER BY run_seq",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_yield_tasks(limit: int = 50) -> list[dict]:
    """获取所有良率监控任务，按创建时间倒序。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM monitor_yield_tasks ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------- 加捡/Hold货报告 CRUD ----------

def save_hold_report(company_date: str, site: str, data_json: str,
                     record_count: int = 0, glass_count: int = 0) -> dict:
    """保存或更新加捡/Hold货报告的结构化数据。"""
    conn = get_connection()
    try:
        now = _now()
        row = conn.execute(
            "SELECT id FROM hold_report_data WHERE company_date = ? AND site = ?",
            (company_date, site),
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE hold_report_data
                   SET data_json = ?, record_count = ?, glass_count = ?, generated_at = ?
                   WHERE id = ?""",
                (data_json, record_count, glass_count, now, row["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO hold_report_data (company_date, site, data_json, record_count, glass_count, generated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (company_date, site, data_json, record_count, glass_count, now),
            )
        conn.commit()
        return dict(
            conn.execute(
                "SELECT * FROM hold_report_data WHERE company_date = ? AND site = ?",
                (company_date, site),
            ).fetchone()
        )
    finally:
        conn.close()


def get_hold_report(company_date: str, site: str = None) -> list[dict]:
    """获取加捡/Hold货报告数据。"""
    conn = get_connection()
    try:
        if site:
            rows = conn.execute(
                "SELECT * FROM hold_report_data WHERE company_date = ? AND site = ? ORDER BY id",
                (company_date, site),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM hold_report_data WHERE company_date = ? ORDER BY site",
                (company_date,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def seed_hold_item():
    """确保加捡/Hold货报告事项存在于 monitor_items 表中。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM monitor_items WHERE name LIKE '%加捡%' OR name LIKE '%Hold%'"
        ).fetchone()
        if not row:
            now = _now()
            conn.execute(
                "INSERT INTO monitor_items (name, description, created_at) VALUES (?, ?, ?)",
                ("加捡Hold货报告", "加捡/Hold货Sample报告，每2小时刷新", now),
            )
            conn.commit()
            print("[数据库] 已创建事项: 加捡Hold货报告")
    finally:
        conn.close()


def seed_rw_item():
    """确保 RW 监控报告事项存在于 monitor_items 表中。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM monitor_items WHERE name LIKE '%RW%'"
        ).fetchone()
        if not row:
            now = _now()
            conn.execute(
                "INSERT INTO monitor_items (name, description, created_at) VALUES (?, ?, ?)",
                ("RW监控报告", "RW返工监控报告，每天生成一次", now),
            )
            conn.commit()
            print("[数据库] 已创建事项: RW监控报告")
    finally:
        conn.close()