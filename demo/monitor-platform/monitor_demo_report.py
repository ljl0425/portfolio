# -*- coding: utf-8 -*-
"""日常监控 — DEMO 报告生成器（演示用）

生成结构化的静态 HTML 监控报告，视觉风格与真实报告一致
（浅色主题 + 蓝色渐变页头 + 统计卡片 + 表格），
全部使用演示数据（DEMO 机台 / 批次 / 产品），不含公司名与真实数据。
"""

import os
import time


def _now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


_BADGE = {
    "OK": "b-ok", "PASS": "b-ok", "合格": "b-ok", "正常": "b-ok",
    "NG": "b-ng", "FAIL": "b-ng", "超规": "b-ng", "异常": "b-ng",
    "监控中": "b-run", "进行中": "b-run", "待处理": "b-run", "Hold": "b-run",
    "已完成": "b-done", "已闭环": "b-done", "已解除": "b-done",
}


def _cell(v) -> str:
    s = "" if v is None else str(v)
    cls = _BADGE.get(s)
    if cls:
        return f'<span class="{cls}">{s}</span>'
    return s


_CSS = """
:root { --bg:#f0f2f5; --card:#fff; --text:#1a1a2e; --sub:#6b7280; --border:#e5e7eb;
        --blue:#2563eb; --green:#22c55e; --red:#ef4444; --orange:#f59e0b; }
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:'Microsoft YaHei','PingFang SC',sans-serif; background:var(--bg);
       color:var(--text); padding:28px 24px; }
.container { max-width:1100px; margin:0 auto; }
.header { background:linear-gradient(135deg,#1e3a5f 0%,#2563eb 100%); color:#fff;
          border-radius:12px; padding:26px 30px; margin-bottom:22px;
          box-shadow:0 2px 8px rgba(0,0,0,.10); }
.header h1 { font-size:21px; font-weight:700; margin-bottom:6px; }
.header .meta { font-size:13px; opacity:.85; display:flex; gap:16px; flex-wrap:wrap; margin-top:6px; }
.header .badge { background:rgba(255,255,255,.2); padding:2px 10px; border-radius:99px; font-size:11px; }
.stats-row { display:flex; gap:14px; margin-bottom:20px; }
.stat-card { flex:1; background:var(--card); border:1px solid var(--border); border-radius:10px;
             padding:18px 22px; text-align:center; box-shadow:0 1px 3px rgba(0,0,0,.04); }
.stat-card .num { font-size:28px; font-weight:800; color:var(--blue); }
.stat-card .num.green { color:var(--green); }
.stat-card .num.orange { color:var(--orange); }
.stat-card .num.red { color:var(--red); }
.stat-card .label { font-size:12px; color:var(--sub); margin-top:2px; }
.hint-bar { background:#eff6ff; border:1px solid #93c5fd; border-radius:8px;
            padding:10px 16px; margin-bottom:18px; font-size:13px; color:#1d4ed8; }
.table-wrap { background:var(--card); border:1px solid var(--border); border-radius:10px;
              overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,.04); }
table { width:100%; border-collapse:collapse; font-size:13px; }
thead th { background:#f8fafc; text-align:left; padding:12px 16px; border-bottom:2px solid var(--border);
           color:#475569; font-weight:600; font-size:12px; letter-spacing:.3px; }
tbody td { padding:11px 16px; border-bottom:1px solid #f1f5f9; vertical-align:middle; }
tbody tr:last-child td { border-bottom:none; }
tbody tr:hover { background:#f8fafc; }
.empty-row { text-align:center; color:#94a3b8; padding:30px; font-size:14px; }
.b-ok { background:#d1fae5; color:#065f46; padding:2px 10px; border-radius:99px; font-size:12px; font-weight:600; white-space:nowrap; }
.b-ng { background:#fee2e2; color:#991b1b; padding:2px 10px; border-radius:99px; font-size:12px; font-weight:600; white-space:nowrap; }
.b-run { background:#fef3c7; color:#d97706; padding:2px 10px; border-radius:99px; font-size:12px; font-weight:600; white-space:nowrap; }
.b-done { background:#d1fae5; color:#065f46; padding:2px 10px; border-radius:99px; font-size:12px; font-weight:600; white-space:nowrap; }
.footer { text-align:center; color:#94a3b8; font-size:12px; margin-top:28px; padding:12px; }
"""


def _spec_item(item_name: str, date_str: str) -> dict:
    """按监控事项返回演示数据（标题 / 统计卡 / 表头 / 行）。"""
    n = (item_name or "").strip()

    if "Dose" in n:
        return {
            "title": "📊 Dose 监控",
            "subtitle": "每日 Dose 数据增量同步与趋势监控",
            "stats": [("批次总数", "48", ""), ("超规批次", "2", "orange"),
                      ("平均 Dose", "24.6 μC/cm²", ""), ("合格率", "95.8%", "green")],
            "cols": ["批次", "机台", "产品", "Dose (μC/cm²)", "规格", "判定"],
            "rows": [
                ["DEMO-LOT-001", "DEMO-M01", "DEMO-PROD-01", "24.8", "24.0±1.0", "OK"],
                ["DEMO-LOT-002", "DEMO-M01", "DEMO-PROD-01", "24.3", "24.0±1.0", "OK"],
                ["DEMO-LOT-003", "DEMO-M02", "DEMO-PROD-02", "25.1", "24.0±1.0", "OK"],
                ["DEMO-LOT-004", "DEMO-M02", "DEMO-PROD-02", "23.6", "24.0±1.0", "OK"],
                ["DEMO-LOT-005", "DEMO-M03", "DEMO-PROD-03", "26.4", "24.0±1.0", "NG"],
                ["DEMO-LOT-006", "DEMO-M03", "DEMO-PROD-03", "24.0", "24.0±1.0", "OK"],
            ],
        }

    if "OVL" in n:
        return {
            "title": "📐 OVL 量测监控",
            "subtitle": "OVL 量测异常自动分析与报告",
            "stats": [("量测片数", "40", ""), ("超规片数", "3", "orange"),
                      ("最大 X 偏移", "0.142 μm", "red"), ("最大 Y 偏移", "0.118 μm", "")],
            "cols": ["Glass", "机台", "OVL_X (μm)", "OVL_Y (μm)", "规格 (μm)", "判定"],
            "rows": [
                ["DEMO-G001", "DEMO-M01", "0.032", "0.021", "≤0.100", "OK"],
                ["DEMO-G002", "DEMO-M01", "0.045", "0.038", "≤0.100", "OK"],
                ["DEMO-G003", "DEMO-M02", "0.061", "0.052", "≤0.100", "OK"],
                ["DEMO-G007", "DEMO-M02", "0.118", "0.096", "≤0.100", "NG"],
                ["DEMO-G008", "DEMO-M03", "0.142", "0.118", "≤0.100", "NG"],
                ["DEMO-G009", "DEMO-M03", "0.058", "0.041", "≤0.100", "OK"],
            ],
        }

    if "ECPM" in n:
        return {
            "title": "📋 ECPM 昨日整理",
            "subtitle": "ECPM 数据定时拉取与工事汇总",
            "stats": [("工事总数", "12", ""), ("已闭环", "9", "green"),
                      ("进行中", "3", "orange"), ("平均闭环时长", "2.4 天", "")],
            "cols": ["工事编号", "工事标题", "机台", "状态", "最后更新"],
            "rows": [
                ["DEMO-ECPM-001", "DEMO-M01 刻蚀机台维护", "DEMO-M01", "已闭环", "2026-11-11 18:20"],
                ["DEMO-ECPM-002", "DEMO-M02 沉积腔体压力波动", "DEMO-M02", "已闭环", "2026-11-11 16:45"],
                ["DEMO-ECPM-003", "DEMO-M03 光刻对位偏移排查", "DEMO-M03", "进行中", "2026-11-12 09:10"],
                ["DEMO-ECPM-004", "DEMO-M01 参数监控报警复核", "DEMO-M01", "进行中", "2026-11-12 10:35"],
            ],
        }

    if "工事" in n or "后续" in n:
        return {
            "title": "📈 工事后续监控",
            "subtitle": "重大工事后续良率跟进，快照对比与报告",
            "stats": [("监控任务", "6", ""), ("监控中", "2", "orange"),
                      ("已完成", "4", "green"), ("快照总数", "128", "")],
            "cols": ["监控任务", "机台", "状态", "快照数", "更新时间"],
            "rows": [
                ["DEMO-M01 刻蚀机台维护跟进", "DEMO-M01", "监控中", "48", "2026-11-12 07:30"],
                ["DEMO-M02 沉积腔体压力跟进", "DEMO-M02", "监控中", "36", "2026-11-12 07:30"],
                ["DEMO-M03 光刻对位偏移跟进", "DEMO-M03", "已完成", "24", "2026-11-11 07:30"],
                ["DEMO-M01 参数监控报警跟进", "DEMO-M01", "已完成", "20", "2026-11-10 07:30"],
            ],
        }

    if "Hold" in n:
        return {
            "title": "📦 Hold 货报告",
            "subtitle": "Hold 货批次数据汇总与推送",
            "stats": [("Hold 批次", "15", ""), ("已解除", "11", "green"),
                      ("当前 Hold", "4", "orange"), ("平均时长", "6.2 h", "")],
            "cols": ["批次", "机台", "Hold 原因", "时长", "状态"],
            "rows": [
                ["DEMO-LOT-011", "DEMO-M01", "参数超规待复核", "3.5 h", "已解除"],
                ["DEMO-LOT-012", "DEMO-M02", "量测异常待复测", "5.1 h", "已解除"],
                ["DEMO-LOT-013", "DEMO-M03", "Defect 异常待分析", "8.2 h", "Hold"],
                ["DEMO-LOT-014", "DEMO-M01", "工程变更验证中", "2.0 h", "Hold"],
            ],
        }

    # 兜底：通用报告
    return {
        "title": "📊 日常监控报告",
        "subtitle": n or "监控事项",
        "stats": [("监控项", "1", ""), ("状态", "已生成", "green"),
                  ("记录数", "0", ""), ("异常", "0", "")],
        "cols": ["项目", "数值", "状态"],
        "rows": [["演示数据", "-", "OK"]],
    }


def build_demo_report(item_name: str, date_str: str) -> str:
    spec = _spec_item(item_name, date_str)

    stats_html = "".join(
        f'<div class="stat-card"><div class="num {color}">{val}</div>'
        f'<div class="label">{label}</div></div>'
        for label, val, color in spec["stats"]
    )

    if spec["rows"]:
        head = "".join(f"<th>{c}</th>" for c in spec["cols"])
        body = "".join(
            "<tr>" + "".join(f"<td>{_cell(v)}</td>" for v in row) + "</tr>"
            for row in spec["rows"]
        )
        table_html = (f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
                      f"<tbody>{body}</tbody></table></div>")
    else:
        table_html = ('<div class="table-wrap"><table><tbody>'
                      '<tr><td class="empty-row">暂无数据</td></tr></tbody></table></div>')

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{spec['title']} — {date_str}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{spec['title']}</h1>
    <div class="meta">
      <span>日期: {date_str}</span>
      <span>生成: {_now_str()}</span>
      <span class="badge">DEMO 演示数据</span>
    </div>
  </div>

  <div class="stats-row">{stats_html}</div>

  <div class="hint-bar">📋 {spec['subtitle']}（本报告为演示数据，仅用于功能展示）</div>

  {table_html}

  <div class="footer">
    <p>日常监控平台 · {spec['title']}</p>
  </div>
</div>
</body>
</html>"""


def write_demo_report(item_id: int, item_name: str, date_str: str, output_dir: str) -> str:
    """生成演示报告 HTML 并写入 output_dir，返回文件名。"""
    html = build_demo_report(item_name, date_str)
    safe = (item_name or "report").replace(" ", "_")
    filename = f"monitor_{item_id}_{date_str}_{safe}.html"
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, filename), "w", encoding="utf-8") as f:
        f.write(html)
    return filename
