"""工事良率监控 — Matplotlib 图表渲染器。

从快照的 full_result_json / yield_data_json 生成高质量 PNG 图表。
输出到 output/yield_charts/task_{id}/ 目录，供 HTML 报告引用。
"""

import json
import os
import time
from datetime import datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
CHART_ROOT = os.path.join(_DIR, "output", "yield_charts")
os.makedirs(CHART_ROOT, exist_ok=True)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from matplotlib import font_manager

# ===================================================================
# 配色（与 engine.py 一致的高质量配色）
# ===================================================================
COLOR_BG = "#ffffff"
COLOR_CARD = "#f8f9fa"
COLOR_BORDER = "#dee2e6"
COLOR_TEXT = "#333333"
COLOR_TEXT_LIGHT = "#6c757d"
COLOR_ACCENT = "#0d6efd"
COLOR_BEFORE = "#198754"        # 绿 — 工事前
COLOR_AFTER = "#dc3545"         # 红 — 工事后
COLOR_DENSITY_LINE = "#0d6efd"  # 蓝 — Density 折线
COLOR_BAR = "#adb5bd"           # 灰 — 柱状图
COLOR_BAR_GLASS = "#6f9fd8"     # 玻璃片数
COLOR_EVENT_LINE = "#dc3545"    # 工事时间节点竖线
COLOR_GRID = "#f1f5f9"

# ===================================================================
# 字体
# ===================================================================
_font = None
for _fc in ["Microsoft YaHei", "SimHei", "WenQuanYi Micro Hei", "sans-serif"]:
    try:
        font_manager.findfont(_fc, fallback_to_default=False)
        _font = _fc
        break
    except Exception:
        continue
if _font:
    plt.rcParams["font.sans-serif"] = [_font, "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

FONT_TITLE = 15
FONT_SUBTITLE = 13
FONT_AXIS_LABEL = 12
FONT_TICK = 10
FONT_ANNOTATION = 9


def _parse_time(s):
    """解析时间字符串。"""
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt)
        except (ValueError, IndexError):
            continue
    return None


def _task_chart_dir(task_id):
    """获取任务的图表输出目录。"""
    d = os.path.join(CHART_ROOT, f"task_{task_id}")
    os.makedirs(d, exist_ok=True)
    return d


# ===================================================================
# 1. Defect Map 对比图
# ===================================================================

def draw_defect_map(chart_data, save_path):
    """绘制单个 Defect Map 对比图（工事前 vs 工事后散点图）。

    Args:
        chart_data: {"before": [(x,y,size),...], "after": [...],
                      "layer": "4300", "machine": "DEMO-M01",
                      "defect_code": "TPDPC",
                      "before_count": 8, "after_count": 10}
        save_path: PNG 文件路径
    """
    before_pts = chart_data.get("before", [])
    after_pts = chart_data.get("after", [])
    layer = chart_data.get("layer", "?")
    machine = chart_data.get("machine", "?")
    dc = chart_data.get("defect_code", "?")
    before_count = chart_data.get("before_count", len(before_pts))
    after_count = chart_data.get("after_count", len(after_pts))

    fig, (ax_l, ax_r) = plt.subplots(1, 2, figsize=(16, 7), facecolor=COLOR_BG)
    fig.suptitle(f"{layer} / {machine} / {dc}",
                 fontsize=FONT_TITLE, color=COLOR_ACCENT, fontweight="bold", y=0.98)

    for ax, pts, label, color, count in [
        (ax_l, before_pts, "工事前", COLOR_BEFORE, before_count),
        (ax_r, after_pts, "工事后", COLOR_AFTER, after_count),
    ]:
        ax.set_facecolor("#fdfdfd")
        ax.set_xlim(-1690, 1690)
        ax.set_ylim(-1495, 1495)
        ax.set_aspect("equal")
        ax.tick_params(colors=COLOR_TEXT_LIGHT, labelsize=FONT_TICK)
        ax.grid(True, alpha=0.3, color="#dee2e6")

        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            sizes = [max(3, min(25, float(p[2]) * 4)) if len(p) > 2 and p[2] else 8 for p in pts]
            ax.scatter(xs, ys, s=sizes, c=color, alpha=0.45, edgecolors="none")

        ax.axhline(0, color="#dee2e6", linewidth=0.5)
        ax.axvline(0, color="#dee2e6", linewidth=0.5)
        ax.set_title(f"{label}  (n={count})", fontsize=FONT_SUBTITLE, color=color, fontweight="bold")

    plt.tight_layout(rect=[0, 0.02, 1, 0.95])
    fig.savefig(save_path, dpi=120, facecolor=COLOR_BG, bbox_inches="tight")
    plt.close(fig)


# ===================================================================
# 2. Density 趋势图
# ===================================================================

def draw_density_trend(history, layer, dc, event_time_str, save_path):
    """绘制单个 Defect Code 的 Density 趋势图（柱状=颗数, 折线=Density）。

    Args:
        history: [(hour_str, density, count, glass_count), ...]
        layer: 层别
        dc: defect code
        event_time_str: ECPM 创建时间字符串
        save_path: PNG 路径
    """
    if not history:
        return

    hours = [h[0] for h in history]
    densities = [h[1] for h in history]
    counts = [h[2] for h in history]

    fig, ax1 = plt.subplots(figsize=(14, 5), facecolor=COLOR_BG)
    ax1.set_facecolor(COLOR_CARD)

    x = range(len(hours))

    # X 轴标签
    short_labels = []
    prev_date = ""
    for h in hours:
        parts = h.split()
        if len(parts) >= 2:
            d, t = parts[0], parts[1][:5]
            if d != prev_date:
                short_labels.append(f"{d}\n{t}")
                prev_date = d
            else:
                short_labels.append(t)
        else:
            short_labels.append(h)

    # 柱状图 — 颗数
    ax1.bar(x, counts, color=COLOR_BAR, alpha=0.7, label="Defect 颗数", width=0.7)
    ax1.set_ylabel("Defect 颗数", color=COLOR_TEXT, fontsize=FONT_AXIS_LABEL)
    ax1.tick_params(axis="y", colors=COLOR_TEXT_LIGHT, labelsize=FONT_TICK)
    ax1.set_xticks(x)
    ax1.set_xticklabels(short_labels, fontsize=9, color=COLOR_TEXT_LIGHT)

    # 柱状图顶部标签
    for xi, cnt in zip(x, counts):
        if cnt > 0:
            ax1.annotate(str(cnt), (xi, cnt), textcoords="offset points",
                         xytext=(0, 3), fontsize=FONT_ANNOTATION - 1, color=COLOR_TEXT_LIGHT, ha="center")

    # 折线 — Density
    ax2 = ax1.twinx()
    ax2.plot(x, densities, color=COLOR_DENSITY_LINE, marker="o", linewidth=2,
             markersize=8, label="Density (颗/glass)", zorder=5)
    ax2.set_ylabel("Density (颗/glass)", color=COLOR_DENSITY_LINE, fontsize=FONT_AXIS_LABEL)
    ax2.tick_params(axis="y", colors=COLOR_DENSITY_LINE, labelsize=FONT_TICK)

    # 折线数据标签（交错排列）
    for xi, den in zip(x, densities):
        offset = 10 if xi % 2 == 0 else -14
        ax2.annotate(f"{den:.1f}", (xi, den), textcoords="offset points",
                     xytext=(0, offset), fontsize=FONT_ANNOTATION, color=COLOR_DENSITY_LINE,
                     ha="center", fontweight="bold",
                     bbox=dict(boxstyle="round,pad=0.2", facecolor="#ffffffdd",
                               edgecolor=COLOR_DENSITY_LINE, alpha=0.8))

    # 工事时间节点竖线
    _draw_event_line(ax1, hours, event_time_str, max(counts) if counts else 1)

    ax1.set_title(f"{layer} / {dc} — Defect Density 趋势",
                  fontsize=FONT_TITLE, color=COLOR_ACCENT, fontweight="bold", pad=12)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left",
               fontsize=10, facecolor="white", edgecolor=COLOR_BORDER)
    ax1.grid(True, alpha=0.2, color=COLOR_BORDER, axis="y")

    plt.tight_layout()
    fig.savefig(save_path, dpi=120, facecolor=COLOR_BG, bbox_inches="tight")
    plt.close(fig)


def draw_glass_overview(density_data, event_time_str, save_path):
    """全局总览：每小时 Glass 片数 & Defect 总数，含工事竖线。"""
    if not density_data:
        return

    hours = [d.get("hour_str", "") if isinstance(d, dict) else (d[0] if len(d) > 0 else "") for d in density_data]
    glasses = [d.get("total_glass", 0) if isinstance(d, dict) else (d[3] if len(d) > 3 else 0) for d in density_data]
    defects = [d.get("total_defect", 0) if isinstance(d, dict) else (d[2] if len(d) > 2 else 0) for d in density_data]

    fig, ax1 = plt.subplots(figsize=(14, 4), facecolor=COLOR_BG)
    ax1.set_facecolor(COLOR_CARD)

    x = range(len(hours))
    short_labels = []
    prev_date = ""
    for h in hours:
        parts = h.split()
        if len(parts) >= 2:
            d, t = parts[0], parts[1][:5]
            if d != prev_date:
                short_labels.append(f"{d}\n{t}")
                prev_date = d
            else:
                short_labels.append(t)
        else:
            short_labels.append(h)

    ax1.bar(x, glasses, color=COLOR_BAR_GLASS, alpha=0.8, label="Glass 片数", width=0.6)
    ax1.set_ylabel("Glass 片数", color=COLOR_TEXT, fontsize=FONT_AXIS_LABEL)
    ax1.tick_params(axis="y", colors=COLOR_TEXT_LIGHT, labelsize=FONT_TICK)

    for xi, g in zip(x, glasses):
        if g > 0:
            ax1.annotate(str(g), (xi, g), textcoords="offset points",
                         xytext=(0, 3), fontsize=FONT_ANNOTATION - 1, color=COLOR_TEXT_LIGHT, ha="center")

    ax2 = ax1.twinx()
    ax2.plot(x, defects, color=COLOR_AFTER, marker=".", linewidth=1.5,
             markersize=6, label="Defect 总数")
    ax2.set_ylabel("Defect 总数", color=COLOR_AFTER, fontsize=FONT_AXIS_LABEL)
    ax2.tick_params(axis="y", colors=COLOR_AFTER, labelsize=FONT_TICK)

    for xi, df in zip(x, defects):
        if df > 0 and xi % 3 == 0:
            ax2.annotate(str(df), (xi, df), textcoords="offset points",
                         xytext=(0, -12), fontsize=FONT_ANNOTATION - 1, color=COLOR_AFTER, ha="center")

    ax1.set_xticks(x)
    ax1.set_xticklabels(short_labels, fontsize=9, color=COLOR_TEXT_LIGHT)

    _draw_event_line(ax1, hours, event_time_str, max(glasses) if glasses else 1)

    ax1.set_title("每小时 Glass 片数 & Defect 总数", fontsize=FONT_TITLE,
                  color=COLOR_ACCENT, fontweight="bold", pad=12)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left",
               fontsize=10, facecolor="white", edgecolor=COLOR_BORDER)
    ax1.grid(True, alpha=0.2, color=COLOR_BORDER, axis="y")

    plt.tight_layout()
    fig.savefig(save_path, dpi=120, facecolor=COLOR_BG, bbox_inches="tight")
    plt.close(fig)


def _draw_event_line(ax, hours, event_time_str, y_max):
    """在图表上画工事时间节点竖线。"""
    try:
        event_dt = datetime.strptime(event_time_str, "%Y-%m-%d %H:%M:%S")
        event_hour = event_dt.replace(minute=0, second=0, microsecond=0)

        hour_dt_list = []
        for h in hours:
            try:
                hour_dt_list.append(datetime.strptime(h, "%Y-%m-%d %H:%M"))
            except ValueError:
                hour_dt_list.append(None)

        line_x = None
        for i, hdt in enumerate(hour_dt_list):
            if hdt and hdt >= event_hour:
                line_x = i
                break
        if line_x is None and hour_dt_list:
            line_x = len(hour_dt_list) - 1

        if line_x is not None:
            ax.axvline(x=line_x, color=COLOR_EVENT_LINE, linewidth=2,
                       linestyle="--", alpha=0.8, zorder=4)
            ax.annotate("工事\n节点",
                        xy=(line_x, y_max * 0.9),
                        fontsize=FONT_ANNOTATION, color=COLOR_EVENT_LINE,
                        fontweight="bold", ha="center",
                        bbox=dict(boxstyle="round,pad=0.3", facecolor="#fff",
                                  edgecolor=COLOR_EVENT_LINE, alpha=0.9))
    except (ValueError, IndexError):
        pass


# ===================================================================
# 3. 良率对比图
# ===================================================================

def draw_yield_comparison(overall_summary, save_path):
    """绘制良率核心指标对比图（分组柱状图）。

    Args:
        overall_summary: {"1840 Yield": {"before": 94.6, "after": 95.8}, ...}
        save_path: PNG 路径
    """
    if not overall_summary:
        return

    names = list(overall_summary.keys())
    if not names:
        return

    before_vals = [overall_summary[n].get("before", 0) for n in names]
    after_vals = [overall_summary[n].get("after", 0) for n in names]

    fig, ax = plt.subplots(figsize=(max(8, len(names) * 1.8), 5.5), facecolor=COLOR_BG)
    ax.set_facecolor(COLOR_CARD)

    x = range(len(names))
    bar_w = 0.35
    bars1 = ax.bar([i - bar_w / 2 for i in x], before_vals, bar_w,
                   color=COLOR_BEFORE, alpha=0.85, label="工事前", edgecolor="white", linewidth=0.5)
    bars2 = ax.bar([i + bar_w / 2 for i in x], after_vals, bar_w,
                   color=COLOR_AFTER, alpha=0.85, label="工事后", edgecolor="white", linewidth=0.5)

    # 数据标签
    for bar in bars1:
        h = bar.get_height()
        ax.annotate(f"{h:.1f}", xy=(bar.get_x() + bar.get_width() / 2, h),
                    xytext=(0, 3), textcoords="offset points",
                    fontsize=FONT_ANNOTATION, color=COLOR_BEFORE, ha="center", fontweight="bold")
    for bar in bars2:
        h = bar.get_height()
        ax.annotate(f"{h:.1f}", xy=(bar.get_x() + bar.get_width() / 2, h),
                    xytext=(0, 3), textcoords="offset points",
                    fontsize=FONT_ANNOTATION, color=COLOR_AFTER, ha="center", fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=FONT_TICK, color=COLOR_TEXT)
    ax.set_ylabel("Yield (%)", fontsize=FONT_AXIS_LABEL, color=COLOR_TEXT)
    ax.tick_params(axis="y", labelsize=FONT_TICK, colors=COLOR_TEXT_LIGHT)
    ax.set_title("良率核心指标对比", fontsize=FONT_TITLE, color=COLOR_ACCENT, fontweight="bold", pad=12)
    ax.legend(fontsize=10, facecolor="white", edgecolor=COLOR_BORDER)
    ax.grid(True, alpha=0.2, color=COLOR_BORDER, axis="y")

    plt.tight_layout()
    fig.savefig(save_path, dpi=120, facecolor=COLOR_BG, bbox_inches="tight")
    plt.close(fig)


# ===================================================================
# 4. 批量渲染入口
# ===================================================================

def ensure_charts(task, fast_snaps, slow_snaps):
    """为一个任务生成所有图表 PNG。

    Args:
        task: 任务 dict（来自 monitor_yield_tasks）
        fast_snaps: 快速快照列表
        slow_snaps: 慢速快照列表

    Returns:
        {
            "map_charts": [{"filename": str, "layer": str, "machine": str, "dc": str,
                            "before_count": int, "after_count": int}, ...],
            "density_overview": str or None,
            "density_charts": [{"filename": str, "layer": str, "dc": str}, ...],
            "yield_charts": [{"filename": str}, ...],
        }
    """
    task_id = task["id"]
    chart_dir = _task_chart_dir(task_id)
    event_time = task.get("create_time", "")

    result = {
        "map_charts": [],
        "density_overview": None,
        "density_charts": [],
        "yield_charts": [],
    }

    # ---- 从最新 fast 快照提取 Defect Map 和 Density 数据 ----
    latest_full_result = None
    for snap in reversed(fast_snaps):
        frj = snap.get("full_result_json", "")
        if frj:
            try:
                latest_full_result = json.loads(frj)
                break
            except (json.JSONDecodeError, TypeError):
                continue

    if latest_full_result:
        # --- Defect Map 对比图 ---
        charts = latest_full_result.get("charts", {})
        for key, chart_data in charts.items():
            safe_name = key.replace("/", "_").replace(" ", "_")
            filename = f"map_{safe_name}.png"
            filepath = os.path.join(chart_dir, filename)

            if not os.path.exists(filepath):
                try:
                    draw_defect_map(chart_data, filepath)
                except Exception as e:
                    print(f"  [chart] Map {key} 绘制失败: {e}")
                    continue

            parts = key.split("/")
            result["map_charts"].append({
                "filename": filename,
                "layer": parts[0] if len(parts) > 0 else "?",
                "machine": parts[1] if len(parts) > 1 else "?",
                "dc": parts[2] if len(parts) > 2 else "?",
                "before_count": chart_data.get("before_count", 0),
                "after_count": chart_data.get("after_count", 0),
            })

        # --- Density 总览图 ---
        density_data = latest_full_result.get("density", [])
        if density_data:
            overview_fn = "density_overview.png"
            overview_path = os.path.join(chart_dir, overview_fn)
            if not os.path.exists(overview_path):
                try:
                    draw_glass_overview(density_data, event_time, overview_path)
                except Exception as e:
                    print(f"  [chart] Density overview 绘制失败: {e}")
            if os.path.exists(overview_path):
                result["density_overview"] = overview_fn

            # --- 每个 Top10 Defect Code 的 Density 趋势 ---
            top10 = latest_full_result.get("density_top10", [])
            for item in top10:
                if isinstance(item, dict):
                    layer, dc = item.get("layer", ""), item.get("code", "")
                elif isinstance(item, (list, tuple)):
                    layer, dc = item[0], item[1]
                else:
                    continue

                # 从 density_data 重建历史
                history = []
                for entry in density_data:
                    if isinstance(entry, dict):
                        h_str = entry.get("hour_str", "")
                        di = entry.get("layers", {}).get(layer, {}).get("defect_codes", {}).get(dc)
                    else:
                        # 兼容 tuple 格式
                        h_str = entry[0] if len(entry) > 0 else ""
                        di = None
                    if di and isinstance(di, dict):
                        history.append((h_str, di.get("density", 0), di.get("count", 0),
                                       di.get("glass_count", 0)))

                if history:
                    safe_name = f"density_{layer}_{dc}".replace(" ", "_")
                    filename = f"{safe_name}.png"
                    filepath = os.path.join(chart_dir, filename)
                    if not os.path.exists(filepath):
                        try:
                            draw_density_trend(history, layer, dc, event_time, filepath)
                        except Exception as e:
                            print(f"  [chart] Density {layer}/{dc} 绘制失败: {e}")
                            continue
                    result["density_charts"].append({
                        "filename": filename,
                        "layer": layer,
                        "dc": dc,
                    })

    # ---- 从最新 slow 快照提取良率数据 ----
    for snap in reversed(slow_snaps):
        yoj = snap.get("yield_overall_json", "")
        if yoj:
            try:
                overall = json.loads(yoj)
                if overall:
                    filename = "yield_comparison.png"
                    filepath = os.path.join(chart_dir, filename)
                    if not os.path.exists(filepath):
                        try:
                            draw_yield_comparison(overall, filepath)
                        except Exception as e:
                            print(f"  [chart] Yield 对比图绘制失败: {e}")
                    if os.path.exists(filepath):
                        result["yield_charts"].append({"filename": filename})
                break
            except (json.JSONDecodeError, TypeError):
                continue

    return result