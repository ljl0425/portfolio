"""监控平台定时调度器。

后台线程每 30 秒检查一次数据库中的调度配置，匹配当前时间执行任务。

公司日逻辑（以 07:30 为界）：
  - cron_time >= 07:30 → 当天公司日，数据范围：当天 07:30 ~ cron_time
  - cron_time <  07:30 → 前一天公司日，数据范围：前一天 07:30 ~ 当天 cron_time

调度类型：
  - generate: 生成报告（运行对应 workflow）
  - push: 推送已有报告到会议佐证（不重新生成，只读 output/ 下已有文件）

集成方式：在 worker 启动时调用 scheduler.start() 即可。
"""

import os
import sys
import json
import time
import threading
import traceback

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)

import database

CHECK_INTERVAL = 30  # 检查间隔（秒）
_LAST_RUN_DATE = {}  # schedule_id -> "YYYY-MM-DD HH:MM"，防止同一分钟重复执行


def _resolve_company_date(cron_time: str) -> str:
    """根据 cron_time 判定本次运行的报告日期。

    cron_time: "HH:MM" 格式，如 "07:25"、"10:00"、"20:03"

    规则：
      cron_time >= 07:30 → 今天日历日就是公司日，报告日期 = 今天
      cron_time <  07:30 → 还没过公司日边界，报告日期 = 昨天
    """
    try:
        parts = cron_time.strip().split(":")
        cron_h, cron_m = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        # 解析失败，回退到旧逻辑（前一天公司日）
        return _get_previous_company_date()

    cron_total = cron_h * 60 + cron_m
    boundary = 7 * 60 + 30  # 07:30

    if cron_total >= boundary:
        # 过了 07:30，今天就是公司日
        return time.strftime("%Y-%m-%d")
    else:
        # 还没到 07:30，公司日还是昨天
        yesterday = time.time() - 86400
        return time.strftime("%Y-%m-%d", time.localtime(yesterday))


def _get_previous_company_date() -> str:
    """获取前一个公司日（始终是昨天）。仅用于回退。"""
    now = time.localtime()
    total_minutes = now.tm_hour * 60 + now.tm_min
    if total_minutes >= 7 * 60 + 30:
        yesterday = time.time() - 86400
    else:
        yesterday = time.time() - 2 * 86400
    return time.strftime("%Y-%m-%d", time.localtime(yesterday))


# ===== 生成任务 =====

def _execute_generate(schedule: dict):
    """执行生成任务。"""
    item_id = schedule["item_id"]
    cron_time = schedule["cron_time"]
    date_str = _resolve_company_date(cron_time)

    print(f"[调度器] 执行生成: {schedule['name']} (item={item_id}, cron={cron_time}, date={date_str})")

    if item_id == 1:
        _run_defect_map(date_str)
    elif item_id == 3:
        _run_ecpm(date_str)
    elif item_id == 4:
        _run_feedback(date_str)
    elif item_id == 5:
        _run_yield_monitor(date_str)
    elif item_id == 6:
        _run_hold_report(date_str)
    elif item_id == 7:
        _run_rw_report(date_str)
    else:
        print(f"[调度器] 未知事项类型: item_id={item_id}，跳过")
        database.mark_schedule_run(schedule["id"], f"未知事项: item_id={item_id}")
        return

    database.mark_schedule_run(schedule["id"], f"ok: {date_str}")


def _run_ecpm(date_str: str):
    """运行 ECPM 工作流并保存。"""
    import ecpm_workflow
    html = ecpm_workflow.run("昨日ECPM整理", date_str)
    stats = ecpm_workflow.get_last_stats()

    filename = f"monitor_3_{date_str}_昨日ECPM整理.html"
    filepath = os.path.join(_DIR, "output", filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    # 更新数据库
    conn = database.get_connection()
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            "SELECT id FROM monitor_data WHERE item_id = 3 AND date = ?",
            (date_str,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE monitor_data SET status = 'success', html_path = ? WHERE id = ?",
                (filename, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO monitor_data (item_id, date, status, html_path, created_at) "
                "VALUES (3, ?, 'success', ?, ?)",
                (date_str, filename, now),
            )
        conn.commit()
    finally:
        conn.close()

    print(f"[调度器] ECPM 完成: {date_str}, {stats.get('total', 0)} 条")


def _run_defect_map(date_str: str):
    """运行 Defect Map 工作流并保存。"""
    defect_dir = os.path.join(os.path.dirname(_DIR), "defect-map-monitor")
    sys.path.insert(0, defect_dir)
    import importlib
    import defect_map_engine
    importlib.reload(defect_map_engine)
    from defect_map_engine import run, generate_html

    result = run(date_str=date_str)
    charts = result["_charts"]
    s7 = result["step7"]
    anomaly_stats = s7.get("anomaly_stats", {})

    # 保存 HTML
    html = generate_html(result)
    filename = f"monitor_defect_map_{date_str}_Defect_Map对比.html"
    filepath = os.path.join(_DIR, "output", filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    # 更新数据库
    conn = database.get_connection()
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            "SELECT id FROM monitor_data WHERE item_id = 1 AND date = ?",
            (date_str,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE monitor_data SET status = 'success', html_path = ? WHERE id = ?",
                (filename, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO monitor_data (item_id, date, status, html_path, created_at) "
                "VALUES (1, ?, 'success', ?, ?)",
                (date_str, filename, now),
            )
        conn.commit()
    finally:
        conn.close()

    # 保存异常元数据（供推送使用）
    anomaly_data = {
        "date": date_str,
        "chart_count": s7["chart_count"],
        "anomaly_stats": anomaly_stats,
        "anomaly_list": [],
    }
    for key, chart in sorted(charts.items()):
        anomaly = chart.get("anomaly", {})
        level = anomaly.get("level", "normal")
        if level in ("severe", "monitor", "warning"):
            anomaly_data["anomaly_list"].append({
                "key": key,
                "layer": chart["layer"],
                "machine": chart["machine"],
                "defect_code": chart["defect_code"],
                "anomaly": {
                    "level": level,
                    "level_label": anomaly.get("level_label", "?"),
                    "reasons": anomaly.get("reasons", []),
                    "volume_ratio": anomaly.get("volume_ratio", 1.0),
                    "total_points_y": anomaly.get("total_points_y", 0),
                    "total_points_t": anomaly.get("total_points_t", 0),
                    "disappeared": [{
                        "center": d["center"], "bbox": d["bbox"],
                        "shape": d.get("shape", "blob"),
                        "expected_today": d.get("expected_today", 0),
                        "actual_today": d.get("actual_today", 0),
                    } for d in anomaly.get("disappeared", [])],
                    "new_clusters": [{
                        "center": n["center"], "bbox": n["bbox"],
                        "shape": n.get("shape", "blob"),
                        "expected_yesterday": n.get("expected_yesterday", 0),
                        "actual_yesterday": n.get("actual_yesterday", 0),
                    } for n in anomaly.get("new_clusters", [])],
                    "matched_anomaly": [{
                        "y_center": m["y_center"], "y_bbox": m["y_bbox"],
                        "shape": m.get("shape", "blob"),
                        "density_ratio": m.get("density_ratio", 1.0),
                        "y_point_count": m.get("y_point_count", 0),
                        "t_point_count": m.get("t_point_count", 0),
                    } for m in anomaly.get("matched_anomaly", [])],
                },
            })

    meta_filename = f"monitor_defect_map_{date_str}_meta.json"
    meta_path = os.path.join(_DIR, "output", meta_filename)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(anomaly_data, f, ensure_ascii=False, indent=2)

    print(f"[调度器] Defect Map 完成: {date_str}, {s7['chart_count']} 图表, "
          f"严重: {anomaly_stats.get('severe', 0)}, 关注: {anomaly_stats.get('warning', 0)}")


def _run_feedback(date_str: str):
    """运行外部门反馈工作流并保存。"""
    import feishu_feedback_workflow
    html = feishu_feedback_workflow.run("外部门反馈", date_str)
    stats = feishu_feedback_workflow.get_last_stats()

    filename = f"monitor_4_{date_str}_外部门反馈.html"
    filepath = os.path.join(_DIR, "output", filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    # 更新数据库
    conn = database.get_connection()
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            "SELECT id FROM monitor_data WHERE item_id = 4 AND date = ?",
            (date_str,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE monitor_data SET status = 'success', html_path = ? WHERE id = ?",
                (filename, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO monitor_data (item_id, date, status, html_path, created_at) "
                "VALUES (4, ?, 'success', ?, ?)",
                (date_str, filename, now),
            )
        conn.commit()
    finally:
        conn.close()

    print(f"[调度器] 外部门反馈完成: {date_str}, {stats.get('total', 0)} 条记录")


def _run_yield_monitor(date_str: str):
    """运行工事后续监控工作流并保存。"""
    import yield_monitor_workflow
    html = yield_monitor_workflow.run("工事后续监控", date_str)

    filename = f"monitor_5_{date_str}_工事后续监控.html"
    filepath = os.path.join(_DIR, "output", filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    # 更新数据库
    conn = database.get_connection()
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            "SELECT id FROM monitor_data WHERE item_id = 5 AND date = ?",
            (date_str,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE monitor_data SET status = 'success', html_path = ? WHERE id = ?",
                (filename, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO monitor_data (item_id, date, status, html_path, created_at) "
                "VALUES (5, ?, 'success', ?, ?)",
                (date_str, filename, now),
            )
        conn.commit()
    finally:
        conn.close()

    print(f"[调度器] 工事后续监控完成: {date_str}")


def _run_hold_report(date_str: str):
    """运行加捡/Hold货报告工作流并保存。"""
    import hold_report_workflow
    html = hold_report_workflow.run("加捡Hold货报告", date_str)
    stats = hold_report_workflow.get_last_stats()

    company_date = hold_report_workflow._company_date()
    filename = f"sample_hold_{company_date}.html"
    filepath = os.path.join(_DIR, "output", filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    # 更新数据库
    conn = database.get_connection()
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            "SELECT id FROM monitor_data WHERE item_id = 6 AND date = ?",
            (date_str,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE monitor_data SET status = 'success', html_path = ? WHERE id = ?",
                (filename, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO monitor_data (item_id, date, status, html_path, created_at) "
                "VALUES (6, ?, 'success', ?, ?)",
                (date_str, filename, now),
            )
        conn.commit()
    finally:
        conn.close()

    print(f"[调度器] 加捡/Hold货报告完成: {company_date}, "
          f"T6={stats.get('t6_count', 0)}, T7={stats.get('t7_count', 0)}")


def _run_rw_report(date_str: str):
    """运行 RW 监控报告工作流并保存。"""
    import generate_rw_report

    filename = f"monitor_7_{date_str}_RW监控.html"
    filepath = os.path.join(_DIR, "output", filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    html, _ = generate_rw_report.run(date_str=date_str, output_path=filepath)

    # 更新数据库
    conn = database.get_connection()
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            "SELECT id FROM monitor_data WHERE item_id = 7 AND date = ?",
            (date_str,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE monitor_data SET status = 'success', html_path = ? WHERE id = ?",
                (filename, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO monitor_data (item_id, date, status, html_path, created_at) "
                "VALUES (7, ?, 'success', ?, ?)",
                (date_str, filename, now),
            )
        conn.commit()
    finally:
        conn.close()

    print(f"[调度器] RW监控完成: {date_str}, {len(html):,} 字节")


# ===== 推送任务 =====

def _execute_push(schedule: dict):
    """执行推送任务。只推送已有报告，不重新生成。

    从 output/ 目录读取已有文件，推送到会议佐证平台。
    - 日期判定：先按 cron_time 解析公司日，若文件不存在则再试前一天。
    - 按 schedule.item_id 过滤：只推送该事项，不推送所有。
    """
    cron_time = schedule["cron_time"]
    date_str = _resolve_company_date(cron_time)
    meeting_code = schedule.get("meeting_code", "")
    schedule_item_id = schedule.get("item_id")

    print(f"[调度器] 执行推送: {schedule['name']} (cron={cron_time}, date={date_str}, item_id={schedule_item_id})")
    import monitor_push

    # 辅助：查找报告文件，先试首选日期，再试前一天
    def _find_output(filename_pattern, preferred_date):
        """在 output/ 下找文件，先试首选日期，再试前一天。返回 (date, filepath)。"""
        candidates = [preferred_date]
        prev_date = time.strftime("%Y-%m-%d", time.localtime(
            time.mktime(time.strptime(preferred_date, "%Y-%m-%d")) - 86400
        ))
        if prev_date != preferred_date:
            candidates.append(prev_date)

        for d in candidates:
            fn = filename_pattern.format(date=d)
            fp = os.path.join(_DIR, "output", fn)
            if os.path.exists(fp):
                return d, fp
        return preferred_date, None

    results = []

    def _push_ecpm():
        ecpm_date, ecpm_path = _find_output("monitor_3_{date}_昨日ECPM整理.html", date_str)
        if not ecpm_path:
            print(f"[调度器] ECPM 报告不存在，跳过")
            return False

        import ecpm_workflow
        stats = ecpm_workflow.get_last_stats()
        if not stats:
            ecpm_workflow.run("昨日ECPM整理", ecpm_date)
            stats = ecpm_workflow.get_last_stats()

        chart_images = []
        line_counts = stats.get("line_counts", {}) if stats else {}
        if line_counts:
            chart_images.append(monitor_push.render_machine_heatmap(
                line_counts, f"各机台 ECPM 数量（{ecpm_date}）"))
        if stats and stats.get("eval_item_labels") and stats.get("eval_item_values"):
            chart_images.append(monitor_push.render_bar_chart(
                stats["eval_item_labels"], stats["eval_item_values"],
                f"评价项目分类（前10）", "#e67e22"))

        summary = monitor_push.build_ecpm_summary(stats) if stats else f"ECPM 报告（{ecpm_date}）"
        return monitor_push.push_report("昨日ECPM整理", ecpm_date, summary,
                                        f"monitor_3_{ecpm_date}_昨日ECPM整理.html",
                                        chart_images, meeting_code)

    def _push_defect_map():
        dm_date, meta_path_fn = _find_output("monitor_defect_map_{date}_meta.json", date_str)
        if not meta_path_fn:
            print(f"[调度器] Defect Map 元数据不存在，跳过")
            return False

        with open(meta_path_fn, "r", encoding="utf-8") as f:
            anomaly_data = json.load(f)
        anomaly_stats = anomaly_data.get("anomaly_stats", {})
        chart_images = []
        if anomaly_stats:
            labels = ["严重", "监控", "关注", "正常"]
            values = [anomaly_stats.get("severe", 0),
                      anomaly_stats.get("monitor", 0),
                      anomaly_stats.get("warning", 0),
                      anomaly_stats.get("normal", 0)]
            chart_images.append(monitor_push.render_bar_chart_custom(
                labels, values, f"Defect Map 异常统计（{dm_date}）",
                ["#e74c3c", "#e67e22", "#f39c12", "#27ae60"]))
        summary = monitor_push.build_defect_map_summary_from_meta(anomaly_data)
        return monitor_push.push_report("Defect Map对比", dm_date, summary,
                                        f"monitor_defect_map_{dm_date}_Defect_Map对比.html",
                                        chart_images, meeting_code)

    def _push_feedback():
        fb_date, fb_path = _find_output("monitor_4_{date}_外部门反馈.html", date_str)
        if not fb_path:
            print(f"[调度器] 外部门反馈报告不存在，跳过")
            return False

        import feishu_feedback_workflow
        stats = feishu_feedback_workflow.get_last_stats()
        summary = monitor_push.build_feedback_push_summary(stats)
        return monitor_push.push_report("外部门反馈", fb_date, summary,
                                        f"monitor_4_{fb_date}_外部门反馈.html",
                                        [], meeting_code)

    push_items = {
        1: ("Defect Map", _push_defect_map),
        3: ("ECPM", _push_ecpm),
        4: ("外部门反馈", _push_feedback),
    }

    for item_id, (label, pusher) in push_items.items():
        if schedule_item_id is not None and schedule_item_id != item_id:
            continue
        try:
            ok = pusher()
            results.append((label, ok))
        except Exception as e:
            traceback.print_exc()
            results.append((label, False))

    status = "; ".join(f"{n}:{'OK' if ok else 'FAIL'}" for n, ok in results)
    database.mark_schedule_run(schedule["id"], status)
    print(f"[调度器] 推送完成: {status}")


# ===== 主循环 =====

from concurrent.futures import ThreadPoolExecutor

_MAX_SCHEDULE_WORKERS = 4  # 最多同时执行的任务数

def _check_and_run():
    """检查所有启用调度，匹配当前时间并发执行。

    所有匹配当前时间的任务通过线程池并发执行，避免慢任务阻塞快任务。
    """
    try:
        now = time.localtime()
        current_time = f"{now.tm_hour:02d}:{now.tm_min:02d}"
        current_date = time.strftime("%Y-%m-%d")

        schedules = database.get_enabled_schedules()
        matched = []
        for s in schedules:
            if s["cron_time"] != current_time:
                continue

            # 防止同一分钟内重复执行
            run_key = f"{s['id']}"
            if _LAST_RUN_DATE.get(run_key) == f"{current_date} {current_time}":
                continue
            _LAST_RUN_DATE[run_key] = f"{current_date} {current_time}"
            matched.append(s)

        if not matched:
            return

        # 并发执行匹配的任务
        def _run_one(s):
            try:
                if s["schedule_type"] == "generate":
                    _execute_generate(s)
                elif s["schedule_type"] == "push":
                    _execute_push(s)
            except Exception as e:
                traceback.print_exc()
                database.mark_schedule_run(s["id"], f"error: {str(e)[:200]}")

        with ThreadPoolExecutor(max_workers=_MAX_SCHEDULE_WORKERS) as executor:
            list(executor.map(_run_one, matched))
    except Exception as e:
        traceback.print_exc()


def _loop():
    """调度器主循环。"""
    print(f"[调度器] 启动，检查间隔: {CHECK_INTERVAL}s")
    while True:
        try:
            _check_and_run()
        except Exception:
            pass
        time.sleep(CHECK_INTERVAL)


# ===== RWK 数据同步后台线程 =====

RWK_SYNC_INTERVAL = 2 * 3600  # 每 2 小时同步一次

def _rwk_sync_loop():
    """RWK 增量同步循环：每 2 小时从 API 拉取最新数据。"""
    import rwk_sync
    print(f"[RWK同步] 启动，间隔: {RWK_SYNC_INTERVAL // 3600} 小时")
    while True:
        try:
            print(f"[RWK同步] {time.strftime('%Y-%m-%d %H:%M:%S')} 开始拉取...")
            result = rwk_sync.sync_once()
            if result.get("error"):
                print(f"[RWK同步] 失败: {result['error']}")
            elif result.get("inserted", 0) > 0:
                print(f"[RWK同步] 新增 {result['inserted']} 条, 总量约 {result.get('api_total', 0)}")
            else:
                print(f"[RWK同步] 无新数据")
        except Exception as e:
            print(f"[RWK同步] 异常: {e}")
        time.sleep(RWK_SYNC_INTERVAL)


_scheduler_thread = None
_rwk_sync_thread = None


def start():
    """启动调度器和 RWK 同步后台线程。"""
    global _scheduler_thread, _rwk_sync_thread

    if _scheduler_thread and _scheduler_thread.is_alive():
        print("[调度器] 已在运行中")
    else:
        _scheduler_thread = threading.Thread(target=_loop, daemon=True, name="monitor-scheduler")
        _scheduler_thread.start()
        print("[调度器] 已启动")

    if _rwk_sync_thread and _rwk_sync_thread.is_alive():
        print("[RWK同步] 已在运行中")
    else:
        _rwk_sync_thread = threading.Thread(target=_rwk_sync_loop, daemon=True, name="rwk-sync")
        _rwk_sync_thread.start()
        print("[RWK同步] 已启动")


def seed_defaults():
    """初始化默认调度配置（如果不存在则创建）。"""
    existing = database.list_schedules()
    existing_names = {s["name"] for s in existing}

    defaults = [
        ("ECPM-生成", "generate", 3, "07:25", ""),
        ("DefectMap-生成-早班", "generate", 1, "07:29", ""),
        ("外部门反馈-生成-早班", "generate", 4, "07:00", ""),
        ("推送-早会", "push", None, "08:00", ""),
        ("DefectMap-生成-晚班", "generate", 1, "20:03", ""),
        ("外部门反馈-生成-晚班", "generate", 4, "20:00", ""),
        # 加捡/Hold货报告：每2小时生成一次（07:30/09:30/11:30/13:30/...）
        ("加捡Hold货-生成-0830", "generate", 6, "08:30", ""),
        ("加捡Hold货-生成-1030", "generate", 6, "10:30", ""),
        ("加捡Hold货-生成-1230", "generate", 6, "12:30", ""),
        ("加捡Hold货-生成-1430", "generate", 6, "14:30", ""),
        ("加捡Hold货-生成-1630", "generate", 6, "16:30", ""),
        ("加捡Hold货-生成-1830", "generate", 6, "18:30", ""),
        ("加捡Hold货-生成-2030", "generate", 6, "20:30", ""),
        ("加捡Hold货-生成-2230", "generate", 6, "22:30", ""),
        ("加捡Hold货-生成-0030", "generate", 6, "00:30", ""),
        ("加捡Hold货-生成-0230", "generate", 6, "02:30", ""),
        ("加捡Hold货-生成-0430", "generate", 6, "04:30", ""),
        ("加捡Hold货-生成-0630", "generate", 6, "06:30", ""),
        # RW 监控报告：每天早上 07:26 生成一次
        ("RW监控-生成", "generate", 7, "07:26", ""),
        # 工事后续监控：每天早上 07:25 生成汇总清单页
        ("工事后续监控-生成", "generate", 5, "07:25", ""),
    ]

    for name, stype, item_id, cron_time, meeting_code in defaults:
        if name not in existing_names:
            try:
                database.add_schedule(name, stype, item_id, cron_time, meeting_code)
                print(f"[调度器] 默认配置已创建: {name} @ {cron_time}")
            except Exception as e:
                print(f"[调度器] 默认配置创建失败({name}): {e}")


if __name__ == "__main__":
    database.init_db()
    seed_defaults()
    start()
    # 保持主线程运行
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("[调度器] 已停止")