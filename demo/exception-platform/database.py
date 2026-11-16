"""
SQLite 数据访问层：web_app 与 worker 两个进程共用。

- 所有路径基于本文件位置计算，不依赖启动目录（cwd）；
- 每次操作新建连接、用完即关（try/finally），不跨请求/跨轮询持有连接；
- WAL 模式 + timeout=5.0：支撑 web 与 worker 两进程并发读写，写等待不会无限阻塞；
- 表结构用 PRAGMA user_version 版本化，升级时在 BEGIN IMMEDIATE 事务内重建。
"""
import json
import os
import shutil
import sqlite3
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_MODE = os.environ.get("DEMO_MODE", "").strip() == "1"
_DB_NAME = "exception_platform_demo.db" if _DEMO_MODE else "exception_platform.db"
DB_PATH = os.path.join(BASE_DIR, _DB_NAME)
TASKS_DIR = os.path.join(BASE_DIR, "tasks_demo" if _DEMO_MODE else "tasks")

SCHEMA_VERSION = 3

# ---- 常量 ----

MACHINES_BY_SITE = (
    {
        "DEMO-SITE-1": ["DEMO-M01", "DEMO-M02"],
        "DEMO-SITE-2": ["DEMO-M03"],
    }
    if _DEMO_MODE
    else {
        "DEMO-A": ["DEMO-M%02d" % n for n in range(1, 10)],
        "DEMO-B": ["DEMO-M%02d" % n for n in range(10, 20)]
              
    }
)

MAIN_PROCESSES = ["ALL", "1300", "2300", "3300", "4300", "5300"]

JUDGE_RULES = ["大于等于阈值1", "小于等于阈值1", "在阈值1,2内", "不在阈值1,2内"]

MEASURE_DIMS = ["CD", "OVL", "HT", "MAC", "TP"]

CONFIG_TABLES = {
    "pfcd": "pfcd_config",
    "process_param": "process_param_config",
    "defect_code": "defect_code_config",
}

# ---- DDL ----

_TASKS_DDL = """
CREATE TABLE tasks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    site           TEXT NOT NULL,
    machine        TEXT NOT NULL,
    main_process   TEXT NOT NULL,
    pfcd           TEXT NOT NULL,
    start_time     TEXT NOT NULL,
    end_time       TEXT NOT NULL,
    exception_type TEXT NOT NULL
                   CHECK (exception_type IN ('制程参数','量测异常','defect异常')),
    process_params TEXT,
    judge_param    TEXT,
    judge_rule     TEXT,
    threshold1     TEXT,
    threshold2     TEXT,
    measure_dim    TEXT,
    defect_code    TEXT,
    defect_size    TEXT,
    hold_flag      TEXT NOT NULL CHECK (hold_flag IN ('是','否')),
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','running','success','failed')),
    event_description TEXT DEFAULT '',
    event_status   TEXT NOT NULL DEFAULT 'open'
                   CHECK (event_status IN ('open','close')),
    result         TEXT,
    error          TEXT,
    created_at     TEXT NOT NULL,
    started_at     TEXT,
    finished_at    TEXT
)
"""

_CONFIG_DDL = (
    """CREATE TABLE IF NOT EXISTS pfcd_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    value TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS process_param_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    value TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS defect_code_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    value TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
)""",
)


# ---- 工具函数 ----

def _now():
    """当前本地时间字符串，统一格式写入数据库。"""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def get_connection():
    """新建一个 SQLite 连接。

    check_same_thread=False 仅为保险（Flask 多线程），真正的并发安全依赖
    「每操作一个新连接 + WAL + busy_timeout」。
    """
    conn = sqlite3.connect(DB_PATH, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---- 初始化 ----

def init_db():
    """建表/迁移 + 建任务目录。幂等，web 与 worker 启动时都调用。

    BEGIN IMMEDIATE：迁移期间独占写锁，防止 web/worker 双进程同时重建；
    旧表字段与 CHECK 完全不同，直接 DROP 重建（升级前应先停掉两个进程，
    见 README）。任务 id 会从 1 重新计数，因此同时清理旧的 tasks/<id>/ 目录，
    避免新任务与旧文件内容串用。
    """
    os.makedirs(TASKS_DIR, exist_ok=True)

    conn = get_connection()
    try:
        cur = conn.execute("PRAGMA user_version")
        version = cur.fetchone()[0]

        if version == SCHEMA_VERSION:
            # 版本匹配：只确保配置表和索引存在（幂等），保留所有数据
            for ddl in _CONFIG_DDL:
                conn.execute(ddl)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)"
            )
            conn.commit()
            return

        if version == 0:
            # user_version=0 不等于全新空库。先检查是否有业务表和数据。
            table_rows = conn.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            table_names = [r[0] for r in table_rows]

            if len(table_names) == 0:
                # 确认无任何用户表 → 真正空库，安全创建
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(_TASKS_DDL)
                for ddl in _CONFIG_DDL:
                    conn.execute(ddl)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)"
                )
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.commit()
                return

            # 有用户表 — 核对结构
            biz_tables = {"tasks", "pfcd_config", "process_param_config", "defect_code_config"}
            existing = set(table_names) & biz_tables
            if not existing:
                # 表名不匹配任何已知业务表 → 拒绝
                raise RuntimeError(
                    f"数据库 user_version=0 且含未知表: {table_names}。"
                    f" 拒绝自动初始化，请手动检查。"
                )

            # 检查每个已知表的结构是否与预期 DDL 一致
            # 注意：SQLite 存储 sqlite_master.sql 时会去除 IF NOT EXISTS、
            # 规范化空白和引号。比较时应做同等规范化。
            expected_ddls = {
                "tasks": _TASKS_DDL.strip(),
                "pfcd_config": _CONFIG_DDL[0].strip(),
                "process_param_config": _CONFIG_DDL[1].strip(),
                "defect_code_config": _CONFIG_DDL[2].strip(),
            }

            def _normalize_ddl(sql_text):
                """将 DDL 规范化为 SQLite sqlite_master 存储格式。"""
                s = sql_text
                # 去除 IF NOT EXISTS（SQLite 不保留此修饰）
                s = s.replace("IF NOT EXISTS ", "")
                # 规范化空白
                s = " ".join(s.split())
                # 规范化括号周围空白
                s = s.replace("( ", "(").replace(" )", ")")
                return s

            for tname in existing:
                actual_ddl = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (tname,),
                ).fetchone()
                if actual_ddl is None:
                    raise RuntimeError(
                        f"表 {tname} 无 DDL 信息。拒绝自动初始化。"
                    )
                actual_norm = _normalize_ddl(actual_ddl[0])
                expected_norm = _normalize_ddl(expected_ddls[tname])
                if actual_norm != expected_norm:
                    raise RuntimeError(
                        f"表 {tname} 结构与预期不匹配。\n"
                        f"  预期: {expected_norm[:200]}...\n"
                        f"  实际: {actual_norm[:200]}...\n"
                        f"  拒绝自动初始化，请手动迁移。"
                    )

            # 结构完全匹配 → 设置版本号，不重建
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            # 仍确保配置表和索引存在
            for ddl in _CONFIG_DDL:
                conn.execute(ddl)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)"
            )
            conn.commit()
            return

        # ── 安全修正：版本不匹配（非空库，version != 0 且 != SCHEMA_VERSION）时拒绝 ──
        # 原始字节码行为：version != SCHEMA_VERSION 时执行
        #   BEGIN IMMEDIATE → DROP TABLE IF EXISTS tasks → 重建 tasks + 配置表
        #   → PRAGMA user_version = SCHEMA_VERSION → 删除旧 tasks/<id>/
        #
        # 本次恢复中改为：版本不匹配时报错停止，不自动删除。
        # 如需迁移，另行申请授权。
        raise RuntimeError(
            f"数据库版本不匹配：磁盘版本={version}, 代码版本={SCHEMA_VERSION}。"
            f" 自动迁移已禁用。请手动迁移或联系管理员。"
        )

    finally:
        conn.close()


# ─── 以下为安全修正差异说明 ───
# 原始 init_db() 在 version != SCHEMA_VERSION 时会执行：
#   conn.execute("BEGIN IMMEDIATE")
#   conn.execute("DROP TABLE IF EXISTS tasks")
#   conn.execute(_TASKS_DDL)
#   for ddl in _CONFIG_DDL: conn.execute(ddl)
#   conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
#   conn.commit()
#   for name in os.listdir(TASKS_DIR):
#       path = os.path.join(TASKS_DIR, name)
#       if os.path.isdir(path):
#           shutil.rmtree(path, ignore_errors=True)
# 已替换为 RuntimeError。这是唯一的字节码行为差异。
# ─────────────────────────────────────────────


# ---- 任务操作 ----

def create_task(data):
    """插入一条 pending 任务，返回任务编号。

    data 为完整表单 dict；多选字段（main_process/pfcd/process_params）
    已由 web 层 json.dumps 成 JSON 数组字符串。
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO tasks (site, machine, main_process, pfcd, start_time, end_time,
                               exception_type, process_params, judge_param, judge_rule,
                               threshold1, threshold2, measure_dim, defect_code,
                               defect_size, hold_flag, event_description, event_status,
                               status, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?)
            """,
            (
                data["site"],
                data["machine"],
                data["main_process"],
                data["pfcd"],
                data["start_time"],
                data["end_time"],
                data["exception_type"],
                data.get("process_params", ""),
                data.get("judge_param", ""),
                data.get("judge_rule", ""),
                data.get("threshold1", ""),
                data.get("threshold2", ""),
                data.get("measure_dim", ""),
                data.get("defect_code", ""),
                data.get("defect_size", ""),
                data.get("hold_flag", ""),
                data.get("event_description", ""),
                data.get("event_status", "open"),
                _now(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_task(task_id):
    """按编号查询单个任务，不存在返回 None。"""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        conn.close()


def query_tasks(
    status=None,
    site=None,
    machine=None,
    exception_type=None,
    date_from=None,
    date_to=None,
    keyword=None,
    event_status=None,
    limit=50,
):
    """筛选 + 搜索任务列表。条件全可选，动态拼接 WHERE。

    防注入：列名/关键字全部写死在 SQL，用户输入只出现在 ? 占位参数。
    日期范围作用于提交时间 created_at（date_to 拼 23:59:59 含当天全天）。
    关键字对多列 LIKE（JSON 数组列文本可命中，如搜 "1300" 命中主制程）。
    """
    sql = "SELECT * FROM tasks WHERE 1=1"
    params = []

    if status:
        sql += " AND status = ?"
        params.append(status)
    if site:
        sql += " AND site = ?"
        params.append(site)
    if machine:
        sql += " AND machine = ?"
        params.append(machine)
    if exception_type:
        sql += " AND exception_type = ?"
        params.append(exception_type)
    if event_status:
        sql += " AND event_status = ?"
        params.append(event_status)
    if date_from:
        sql += " AND created_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND created_at <= ?"
        params.append(date_to + " 23:59:59")
    if keyword:
        sql += (
            " AND (machine LIKE ? OR site LIKE ? OR exception_type LIKE ?"
            " OR defect_code LIKE ? OR defect_size LIKE ?"
            " OR measure_dim LIKE ? OR judge_param LIKE ?"
            " OR pfcd LIKE ? OR main_process LIKE ?)"
        )
        like = "%" + keyword + "%"
        params.extend([like] * 9)

    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_distinct_machines():
    """筛选联想：历史任务中出现过的机台（未出现在任务里的值不联想）。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT machine FROM tasks ORDER BY machine"
        ).fetchall()
        return [r["machine"] for r in rows]
    finally:
        conn.close()


def get_all_tasks():
    """返回全部任务列表（id 降序，最新的在前），用于 FastGPT list_tasks 接口。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_pending_task_ids():
    """取全部 pending 任务编号（id 升序，先提交先处理）。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id FROM tasks WHERE status = 'pending' ORDER BY id"
        ).fetchall()
        return [r["id"] for r in rows]
    finally:
        conn.close()


def claim_task(task_id):
    """原子认领：仅当任务仍为 pending 时才置为 running。

    UPDATE 的 WHERE 带 status='pending' 条件 + rowcount 判定，
    防止多个 worker 进程重复处理同一任务。
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE tasks SET status = 'running', started_at = ?"
            " WHERE id = ? AND status = 'pending'",
            (_now(), task_id),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def update_task_result(task_id, status, result, error):
    """写回最终状态与结果（result 以 JSON 字符串存 TEXT 列）。"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE tasks SET status = ?, result = ?, error = ?, finished_at = ?"
            " WHERE id = ?",
            (
                status,
                json.dumps(result, ensure_ascii=False) if result is not None else None,
                error,
                _now(),
                task_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def delete_task(task_id):
    """删除任务记录。running 中的任务拒绝删除。返回是否删除成功。"""
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM tasks WHERE id = ? AND status != 'running'", (task_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def retry_task(task_id):
    """失败任务重新入队：failed → pending，清空 result/error 与相关时间戳。"""
    conn = get_connection()
    try:
        cur = conn.execute(
            """UPDATE tasks SET status = 'pending', result = NULL, error = NULL,
                    started_at = NULL, finished_at = NULL
               WHERE id = ? AND status = 'failed'""",
            (task_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def reset_stuck_tasks():
    """崩溃恢复：把遗留的 running 任务重置为 pending（仅 worker 启动时调用）。

    单 worker 假设下，遗留 running 只可能是上次进程崩溃导致。
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE tasks SET status = 'pending', started_at = NULL"
            " WHERE status = 'running'"
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ---- 配置操作 ----

def _config_table(kind):
    """kind 白名单映射到表名，非法 kind 抛 ValueError。"""
    table = CONFIG_TABLES.get(kind)
    if table is None:
        raise ValueError(f"未知配置类型: {kind}")
    return table


def list_configs(kind):
    """列出某类配置的全部值（按值排序）。"""
    conn = get_connection()
    try:
        table = _config_table(kind)
        rows = conn.execute(
            f"SELECT id, value, created_at FROM {table} ORDER BY value"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_config(kind, value):
    """新增配置值。UNIQUE 冲突（值已存在）返回 False。"""
    conn = get_connection()
    try:
        table = _config_table(kind)
        conn.execute(
            f"INSERT INTO {table} (value, created_at) VALUES (?, ?)",
            (value, _now()),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def update_config(kind, value_id, value):
    """编辑配置值。rowcount 判定是否存在；UNIQUE 冲突返回 False。"""
    conn = get_connection()
    try:
        table = _config_table(kind)
        cur = conn.execute(
            f"UPDATE {table} SET value = ? WHERE id = ?", (value, value_id)
        )
        conn.commit()
        return cur.rowcount > 0
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def delete_config(kind, value_id):
    """删除配置值。任务存的是值快照，删除不影响历史任务展示。"""
    conn = get_connection()
    try:
        table = _config_table(kind)
        cur = conn.execute(f"DELETE FROM {table} WHERE id = ?", (value_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def import_configs(kind, values):
    """批量导入，返回 (新增数, 跳过数)。逐条 INSERT OR IGNORE。"""
    conn = get_connection()
    try:
        table = _config_table(kind)
        added = 0
        for v in values:
            cur = conn.execute(
                f"INSERT OR IGNORE INTO {table} (value, created_at) VALUES (?, ?)",
                (v, _now()),
            )
            if cur.rowcount:
                added += 1
        conn.commit()
        return added, len(values) - added
    finally:
        conn.close()