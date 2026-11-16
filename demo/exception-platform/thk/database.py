"""THK 监控平台 SQLite 数据访问层。

独立数据库 thk_platform.db，与异常平台、监控平台 DB 隔离。
表结构：csv_sources（CSV文件源）、tasks（分析任务）、image_results（图像分析子任务）。
"""

import json
import os
import sqlite3
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_MODE = os.environ.get("DEMO_MODE", "").strip() == "1"
_DB_NAME = "thk_platform_demo.db" if _DEMO_MODE else "thk_platform.db"
DB_PATH = os.path.join(BASE_DIR, _DB_NAME)
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

SCHEMA_VERSION = 1

# ---- DDL ----

_CSV_SOURCES_DDL = """
CREATE TABLE IF NOT EXISTS thk_csv_sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    unc_path     TEXT NOT NULL,
    file_pattern TEXT DEFAULT '*.csv',
    enabled      INTEGER DEFAULT 1,
    created_at   TEXT NOT NULL
)
"""

_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS thk_tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    INTEGER REFERENCES thk_csv_sources(id),
    csv_file     TEXT NOT NULL,
    csv_path     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','scanning','csv_parsed',
                      'image_needed','image_analysis','done','failed')),
    need_image   INTEGER DEFAULT 0,
    image_count  INTEGER DEFAULT 0,
    image_done   INTEGER DEFAULT 0,
    result_json  TEXT,
    summary      TEXT,
    error        TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    created_at   TEXT NOT NULL
)
"""

_IMAGE_RESULTS_DDL = """
CREATE TABLE IF NOT EXISTS thk_image_results (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER REFERENCES thk_tasks(id),
    image_path   TEXT NOT NULL,
    image_name   TEXT,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','done','failed')),
    result_json  TEXT,
    thumbnail    TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL
)
"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """幂等初始化数据库。"""
    conn = get_connection()
    try:
        cur = conn.execute("PRAGMA user_version")
        current_version = cur.fetchone()[0]

        if current_version < SCHEMA_VERSION:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(_CSV_SOURCES_DDL)
                conn.execute(_TASKS_DDL)
                conn.execute(_IMAGE_RESULTS_DDL)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.execute("COMMIT")
                print(f"[THK数据库] 初始化完成，版本 {SCHEMA_VERSION}")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        else:
            conn.execute(_CSV_SOURCES_DDL)
            conn.execute(_TASKS_DDL)
            conn.execute(_IMAGE_RESULTS_DDL)
    finally:
        conn.close()


# ===== CSV 文件源 CRUD =====

def list_csv_sources() -> list[dict]:
    """列出所有 CSV 文件源。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM thk_csv_sources ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_csv_source(name: str, unc_path: str, file_pattern: str = "*.csv") -> int:
    """添加 CSV 文件源，返回新 ID。"""
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO thk_csv_sources (name, unc_path, file_pattern, created_at) "
            "VALUES (?, ?, ?, ?)",
            (name, unc_path, file_pattern, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_csv_source(source_id: int, **kwargs) -> bool:
    """更新 CSV 文件源。"""
    allowed = {"name", "unc_path", "file_pattern", "enabled"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    conn = get_connection()
    try:
        sets = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [source_id]
        cur = conn.execute(
            f"UPDATE thk_csv_sources SET {sets} WHERE id = ?", values
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_csv_source(source_id: int) -> bool:
    """删除 CSV 文件源。"""
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM thk_csv_sources WHERE id = ?", (source_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ===== 分析任务 CRUD =====

def create_task(source_id: int, csv_file: str, csv_path: str) -> int:
    """创建分析任务，返回新 ID。"""
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO thk_tasks (source_id, csv_file, csv_path, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (source_id, csv_file, csv_path, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_task(task_id: int) -> dict | None:
    """获取单个任务。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM thk_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_tasks(source_id: int = None, status: str = None,
               date_from: str = None, date_to: str = None,
               limit: int = 50) -> list[dict]:
    """查询任务列表，带筛选。"""
    conn = get_connection()
    try:
        where = []
        params = []
        if source_id:
            where.append("source_id = ?")
            params.append(source_id)
        if status:
            where.append("status = ?")
            params.append(status)
        if date_from:
            where.append("created_at >= ?")
            params.append(date_from + " 00:00:00")
        if date_to:
            where.append("created_at <= ?")
            params.append(date_to + " 23:59:59")

        sql = "SELECT * FROM thk_tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_task_status(task_id: int, status: str, **kwargs) -> bool:
    """更新任务状态和可选字段。"""
    updates = {"status": status}
    updates.update(kwargs)
    conn = get_connection()
    try:
        sets = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [task_id]
        cur = conn.execute(
            f"UPDATE thk_tasks SET {sets} WHERE id = ?", values
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_task(task_id: int) -> bool:
    """删除任务及关联的图像结果。"""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM thk_image_results WHERE task_id = ?", (task_id,))
        cur = conn.execute("DELETE FROM thk_tasks WHERE id = ?", (task_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ===== 图像分析结果 =====

def add_image_result(task_id: int, image_path: str,
                     image_name: str = None) -> int:
    """添加图像分析子任务。"""
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO thk_image_results (task_id, image_path, image_name, "
            "status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            (task_id, image_path, image_name, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_image_result(img_id: int, status: str,
                        result_json: str = None,
                        thumbnail: str = None,
                        error: str = None) -> bool:
    """更新图像分析结果。"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE thk_image_results SET status = ?, result_json = ?, "
            "thumbnail = ?, error = ? WHERE id = ?",
            (status, result_json, thumbnail, error, img_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_image_results(task_id: int) -> list[dict]:
    """获取某个任务的所有图像分析结果。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM thk_image_results WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_pending_image_tasks(limit: int = 10) -> list[dict]:
    """获取待处理的图像子任务。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM thk_image_results WHERE status = 'pending' "
            "ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_stats() -> dict:
    """获取统计信息。"""
    conn = get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(*) as c FROM thk_tasks"
        ).fetchone()["c"]
        by_status = {}
        for row in conn.execute(
            "SELECT status, COUNT(*) as c FROM thk_tasks GROUP BY status"
        ).fetchall():
            by_status[row["status"]] = row["c"]
        image_total = conn.execute(
            "SELECT COUNT(*) as c FROM thk_image_results"
        ).fetchone()["c"]
        image_by_status = {}
        for row in conn.execute(
            "SELECT status, COUNT(*) as c FROM thk_image_results GROUP BY status"
        ).fetchall():
            image_by_status[row["status"]] = row["c"]
        return {
            "total_tasks": total,
            "by_status": by_status,
            "total_images": image_total,
            "image_by_status": image_by_status,
        }
    finally:
        conn.close()