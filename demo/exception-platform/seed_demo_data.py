"""演示数据种子脚本 — 仅写入 demo 数据库，不影响正式库。

使用方式：
    $env:DEMO_MODE = "1"
    python seed_demo_data.py

生成三个独立 demo 库：
    exception_platform_demo.db  (异常中台)
    monitor_platform_demo.db    (日常监控)
    thk_platform_demo.db        (THK 监控)
"""

import os
import sys
import time

# ===== 必须在 import 任何业务模块前设置 =====
os.environ["DEMO_MODE"] = "1"

_BASE = os.path.dirname(os.path.abspath(__file__))
_MONITOR_BASE = os.path.join(_BASE, "..", "monitor-platform")


def _delete_demo_dbs():
    """清理旧的 demo 数据库，确保从零开始。"""
    files = [
        os.path.join(_BASE, "exception_platform_demo.db"),
        os.path.join(_MONITOR_BASE, "monitor_platform_demo.db"),
        os.path.join(_BASE, "thk", "thk_platform_demo.db"),
    ]
    for f in files:
        abs_f = os.path.normpath(f)
        try:
            os.remove(abs_f)
            print(f"[CLEAN] 已删除 {abs_f}")
        except FileNotFoundError:
            pass
        # 同时删除 WAL/SHM 文件
        for suffix in ("-wal", "-shm"):
            try:
                os.remove(abs_f + suffix)
            except FileNotFoundError:
                pass


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ===================================================================
# 1. 初始化数据库（建表）
# ===================================================================

print("=" * 50)
print("  演示数据种子脚本")
print("=" * 50)

_delete_demo_dbs()

# 异常中台数据库
sys.path.insert(0, _BASE)
import database as ex_db
sys.modules.pop("database", None)

# 监控平台数据库
sys.path.insert(0, _MONITOR_BASE)
import database as mon_db
sys.modules.pop("database", None)

# 日常监控演示报告生成器
import monitor_demo_report

# THK 数据库
sys.path.insert(0, os.path.join(_BASE, "thk"))
import database as thk_db

print("[INIT] 初始化三个 demo 数据库...")
ex_db.init_db()
mon_db.init_db()
thk_db.init_db()
print("[INIT] 数据库初始化完成\n")


# ===================================================================
# 2. 异常中台 — 配置数据
# ===================================================================
print("[SEED] 写入配置数据...")

configs = {
    "pfcd": ["DEMO-PFCD-01 (刻蚀)", "DEMO-PFCD-02 (沉积)", "DEMO-PFCD-03 (光刻)"],
    "process_param": ["DEMO-Param-A (压力)", "DEMO-Param-B (温度)", "DEMO-Param-C (流量)"],
    "defect_code": ["DEMO-DEFECT-001 (颗粒)", "DEMO-DEFECT-002 (划伤)", "DEMO-DEFECT-003 (残留)"],
}
for kind, values in configs.items():
    for v in values:
        try:
            ex_db.add_config(kind, v)
        except Exception:
            pass  # 忽略重复

print("[SEED] 配置数据完成\n")


# ===================================================================
# 3. 异常中台 — 演示任务（不同状态、不同类型）
# ===================================================================
print("[SEED] 创建演示异常任务...")

demo_tasks = [
    # (site, machine, exception_type, status, event_status, hold_flag)
    {
        "site": "DEMO-SITE-1",
        "machine": "DEMO-M01",
        "main_process": '["DEMO-PFCD-01 (刻蚀)"]',
        "pfcd": '["DEMO-PFCD-01 (刻蚀)"]',
        "start_time": "2026-11-10 08:00",
        "end_time": "2026-11-10 16:00",
        "exception_type": "制程参数",
        "process_params": '["DEMO-Param-A (压力)"]',
        "judge_param": "DEMO-Param-A (压力)",
        "judge_rule": "不在阈值1,2内",
        "threshold1": "0.5",
        "threshold2": "1.5",
        "measure_dim": "压力 (mTorr)",
        "hold_flag": "是",
        "event_description": "演示任务：制程参数异常，压力超出阈值范围，触发自动 Hold。",
        "event_status": "open",
    },
    {
        "site": "DEMO-SITE-1",
        "machine": "DEMO-M02",
        "main_process": '["DEMO-PFCD-02 (沉积)"]',
        "pfcd": '["DEMO-PFCD-02 (沉积)"]',
        "start_time": "2026-11-11 06:00",
        "end_time": "2026-11-11 18:00",
        "exception_type": "量测异常",
        "process_params": '["DEMO-Param-B (温度)"]',
        "judge_param": "OVL_X",
        "judge_rule": "超规",
        "threshold1": "0.08",
        "threshold2": "0.15",
        "measure_dim": "OVL (μm)",
        "hold_flag": "否",
        "event_description": "演示任务：OVL 量测异常，X 方向偏移超规。",
        "event_status": "open",
    },
    {
        "site": "DEMO-SITE-2",
        "machine": "DEMO-M03",
        "main_process": '["DEMO-PFCD-03 (光刻)"]',
        "pfcd": '["DEMO-PFCD-03 (光刻)"]',
        "start_time": "2026-11-09 12:00",
        "end_time": "2026-11-09 20:00",
        "exception_type": "defect异常",
        "process_params": "",
        "judge_param": "",
        "judge_rule": "",
        "threshold1": "",
        "threshold2": "",
        "measure_dim": "",
        "defect_code": "DEMO-DEFECT-001 (颗粒)",
        "defect_size": "0.5",
        "hold_flag": "是",
        "event_description": "演示任务：Defect 扫描发现颗粒异常，已 Hold 相关批次。已生成报告并推送。",
        "event_status": "close",
    },
]

task_ids = []
for i, td in enumerate(demo_tasks):
    tid = ex_db.create_task(td)
    task_ids.append(tid)
    print(f"  [{i+1}] 任务 #{tid} — {td['machine']} ({td['exception_type']})")

# 手动更新任务状态（让演示有不同状态的展示）
conn = ex_db.get_connection()
try:
    now = _now()
    # 任务1: running（正在处理中）
    conn.execute(
        "UPDATE tasks SET status='running', started_at=? WHERE id=?",
        (now, task_ids[0]),
    )
    # 任务2: success（已完成）
    conn.execute(
        "UPDATE tasks SET status='success', started_at=?, finished_at=? WHERE id=?",
        (now, now, task_ids[1]),
    )
    # 任务3: success + close（已完成且关闭）
    conn.execute(
        "UPDATE tasks SET status='success', started_at=?, finished_at=? WHERE id=?",
        (now, now, task_ids[2]),
    )
    conn.commit()
finally:
    conn.close()

print("[SEED] 异常任务完成（pending/running/success 各一条）\n")


# ===================================================================
# 3.5 异常中台 — 写演示任务目录文件（input.json / run.log）
# ===================================================================
print("[SEED] 写入演示任务 input.json / run.log ...")

import json as _json

# run.log 结尾按任务状态区分：running 停在"处理中"，success 到"处理成功"
_run_log_tail = {
    "running": ["处理中…"],
    "success": ["处理成功"],
}

for tid, td in zip(task_ids, demo_tasks):
    task_dir = os.path.join(ex_db.TASKS_DIR, str(tid))
    os.makedirs(task_dir, exist_ok=True)

    # input.json：与 web 提交落盘格式一致（全部演示数据）
    input_data = {
        "site": td["site"],
        "machine": td["machine"],
        "main_process": td["main_process"],
        "pfcd": td["pfcd"],
        "start_time": td["start_time"],
        "end_time": td["end_time"],
        "exception_type": td["exception_type"],
        "hold_flag": td["hold_flag"],
        "event_description": td["event_description"],
        "event_status": td["event_status"],
    }
    with open(os.path.join(task_dir, "input.json"), "w", encoding="utf-8") as f:
        _json.dump(input_data, f, ensure_ascii=False, indent=2)

    # run.log：演示处理日志（不含任何真实机台/厂区代号）
    status = "running" if tid == task_ids[0] else "success"
    log_lines = [
        f"[{td['start_time']}:00] 开始处理任务 #{tid}（{td['exception_type']} / {td['site']} / 机台 {td['machine']}）",
        f"[{td['start_time']}:00] 读取量测数据完成",
    ]
    log_lines.extend(f"[{td['start_time']}:00] {tail}" for tail in _run_log_tail[status])
    with open(os.path.join(task_dir, "run.log"), "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")

print("[SEED] 演示任务文件完成\n")


# ===================================================================
# 4. 监控平台 — 监控事项
# ===================================================================
print("[SEED] 创建监控事项...")

monitor_items = [
    ("Dose 监控", "每日 Dose 数据增量同步与趋势监控"),
    ("OVL 量测监控", "OVL 量测异常自动分析与报告"),
    ("ECPM 昨日整理", "ECPM 数据定时拉取与工事汇总"),
    ("工事后续监控", "重大工事后续良率跟进，快照对比与报告"),
    ("Hold 货报告", "Hold 货批次数据汇总与推送"),
]

item_map = {}
for name, desc in monitor_items:
    item = mon_db.add_item(name, desc)
    item_map[name] = item["id"]
    print(f"  [{item['id']}] {name}")

print("[SEED] 监控事项完成\n")


# ===================================================================
# 5. 监控平台 — 调度配置
# ===================================================================
print("[SEED] 创建调度配置...")

schedules = [
    ("Dose 监控-生成", "generate", item_map["Dose 监控"], "07:00", ""),
    ("OVL 量测监控-生成", "generate", item_map["OVL 量测监控"], "07:15", ""),
    ("ECPM 昨日整理-生成", "generate", item_map["ECPM 昨日整理"], "07:25", ""),
    ("工事后续监控-生成", "generate", item_map["工事后续监控"], "07:25", ""),
    ("Hold 货报告-生成", "generate", item_map["Hold 货报告"], "08:00", ""),
]

for name, stype, item_id, cron_time, extra in schedules:
    try:
        mon_db.add_schedule(name, stype, item_id, cron_time, extra)
        print(f"  {name} @ {cron_time}")
    except Exception as e:
        print(f"  [WARN] {name} 创建失败: {e}")

print("[SEED] 调度配置完成\n")


# ===================================================================
# 6. 监控平台 — 演示监控数据（总览页展示）
# ===================================================================
print("[SEED] 创建演示监控数据...")

today = time.strftime("%Y-%m-%d")
yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))

conn = mon_db.get_connection()
try:
    now = _now()
    for name, item_id in item_map.items():
        # 今天：不建记录 → 总览页显示"未生成"+"生成报告"按钮，可演示生成过程
        # 昨天：success + 已生成的演示报告文件（可点击查看）
        html_path = monitor_demo_report.write_demo_report(
            item_id, name, yesterday, mon_db.OUTPUT_DIR)
        conn.execute(
            "INSERT INTO monitor_data (item_id, date, status, html_path, created_at) "
            "VALUES (?, ?, 'success', ?, ?)",
            (item_id, yesterday, html_path, now),
        )
    conn.commit()
finally:
    conn.close()

print("[SEED] 监控数据完成\n")


# ===================================================================
# 7. 监控平台 — 良率监控任务（工事后续监控）
# ===================================================================
print("[SEED] 创建良率监控任务...")

yield_tasks = [
    {
        "ecpm_id": "DEMO-ECPM-20261111-001",
        "ecpm_title": "重大工事 — DEMO-M01 刻蚀机台维护",
        "machine": "DEMO-M01",
        "create_time": "2026-11-11 07:30:00",
        "fast_end_time": "2026-11-14 07:30:00",  # 3 天
        "slow_end_time": "2026-11-26 07:30:00",  # 15 天
    },
    {
        "ecpm_id": "DEMO-ECPM-20261110-002",
        "ecpm_title": "重大异常 — DEMO-M02 沉积腔体压力波动",
        "machine": "DEMO-M02",
        "create_time": "2026-11-10 14:00:00",
        "fast_end_time": "2026-11-13 14:00:00",
        "slow_end_time": "2026-11-25 14:00:00",
    },
]

yield_task_ids = []
for yt in yield_tasks:
    task_record = mon_db.create_yield_task(
        ecpm_id=yt["ecpm_id"],
        ecpm_title=yt["ecpm_title"],
        machine=yt["machine"],
        create_time=yt["create_time"],
        fast_end_time=yt["fast_end_time"],
        slow_end_time=yt["slow_end_time"],
    )
    tid = task_record["id"]
    yield_task_ids.append(tid)
    print(f"  [YIELD #{tid}] {yt['machine']} — {yt['ecpm_title'][:40]}")

# 为 yield_task 1 添加一些快照（模拟已经跑了几轮）
conn = mon_db.get_connection()
try:
    snap_seq = 0
    # fast 快照：每 2 小时一轮的 Defect/Density 数据
    for hour_offset in [2, 4, 6, 8]:
        snap_seq += 1
        run_at = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(time.time() - hour_offset * 3600),
        )
        mon_db.add_yield_snapshot(
            task_id=yield_task_ids[0],
            snapshot_type="fast",
            run_seq=snap_seq,
            run_at=run_at,
            before_matched=95 + snap_seq,
            after_matched=94 + snap_seq,
            density_hours=2 * snap_seq,
            density_top10="DEMO-DEFECT-001(颗粒):+1, DEMO-DEFECT-002(划伤):0",
            full_result_json='{"defect_before": %d, "defect_after": %d}' % (
                100 - (95 + snap_seq), 100 - (94 + snap_seq)),
        )

    # slow 快照：每 24 小时一轮的良率对比
    snap_seq += 1
    run_at = time.strftime(
        "%Y-%m-%d %H:%M:%S",
        time.localtime(time.time() - 24 * 3600),
    )
    mon_db.add_yield_snapshot(
        task_id=yield_task_ids[0],
        snapshot_type="slow",
        run_seq=snap_seq,
        run_at=run_at,
        before_matched=1890,
        after_matched=1875,
        yield_task_count=1,
        yield_result_count=2,
        yield_overall_json='{"before_yield": "94.5%", "after_yield": "93.75%", "diff": "-0.75%"}',
        status="success",
    )
    conn.commit()
finally:
    conn.close()

print("[SEED] 良率监控任务 + 快照完成\n")


# ===================================================================
# 8. THK 监控 — 演示数据
# ===================================================================
print("[SEED] 创建 THK 演示数据...")

conn = thk_db.get_connection()
try:
    now = _now()
    # CSV 源
    conn.execute(
        "INSERT INTO thk_csv_sources (name, unc_path, file_pattern, enabled, created_at) "
        "VALUES (?, ?, ?, 1, ?)",
        ("DEMO-THK-Source-01", "\\\\DEMO-SERVER\\THK\\csv", "*.csv", now),
    )
    # 分析任务
    for i in range(3):
        status = ["done", "pending", "failed"][i]
        conn.execute(
            "INSERT INTO thk_tasks (source_id, csv_file, csv_path, status, "
            "need_image, image_count, image_done, summary, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                f"DEMO_THK_Data_{i+1:03d}.csv",
                f"\\\\DEMO-SERVER\\THK\\csv\\DEMO_THK_Data_{i+1:03d}.csv",
                status,
                1 if i == 0 else 0,
                3 if i == 0 else 0,
                3 if i == 0 else 0,
                "演示 THK 分析摘要：正常波动范围内，无需额外处理。"
                if i == 0
                else None,
                now,
            ),
        )
    conn.commit()
finally:
    conn.close()

print("[SEED] THK 演示数据完成\n")


# ===================================================================
# 完成
# ===================================================================
print("=" * 50)
print("  演示数据种子完成！")
print("=" * 50)
print()
print("  Demo 数据库文件：")
print(f"    {os.path.join(_BASE, 'exception_platform_demo.db')}")
print(f"    {os.path.join(_MONITOR_BASE, 'monitor_platform_demo.db')}")
print(f"    {os.path.join(_BASE, 'thk', 'thk_platform_demo.db')}")
print()
print("  启动演示环境：")
print("    PowerShell:")
print('    cd exception-platform')
print('    python -c "import os; os.environ[\'DEMO_MODE\']=\'1\'; from web_app import init_and_run; init_and_run(port=15001, start_services=False)"')
print('    （DEMO_MODE 在 Python 进程内设置，避免 PowerShell 子进程不继承的问题）')
print()
print("  访问：")
print("    直连: http://127.0.0.1:15001/")
print("    网关: http://127.0.0.1:5000/dev/")