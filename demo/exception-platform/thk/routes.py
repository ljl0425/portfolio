"""THK 监控平台 — Blueprint 路由。

挂在 /monitor/thk/ 下。
页面：总览页、CSV 源配置页、任务详情页。
API：扫描、分析、报告预览。
全部内联模板，复用异常中台 CSS 变量（深色科技风）。
"""

import json
import os
import time

from flask import (
    Blueprint,
    jsonify,
    render_template_string,
    request,
    send_file,
)

from . import database, csv_reader, analyzer, image_analyzer

bp = Blueprint("thk", __name__, url_prefix="/monitor/thk")

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

STATUS_ORDER = [
    "pending", "scanning", "csv_parsed",
    "image_needed", "image_analysis", "done", "failed",
]
STATUS_LABELS = {
    "pending": "待扫描",
    "scanning": "扫描中",
    "csv_parsed": "已解析",
    "image_needed": "需图像分析",
    "image_analysis": "图像分析中",
    "done": "已完成",
    "failed": "失败",
}

# ===== 样式（扩展异常中台 CSS 变量） =====

_THK_STYLE = """
/* THK 监控模块独有样式 */
.sidebar { width: 200px; min-width: 200px;
           background: #1a1a2e; border-right: 1px solid var(--border);
           display: flex; flex-direction: column; flex-shrink: 0; }
.sidebar-header { padding: 16px; border-bottom: 1px solid var(--border); }
.sidebar-header h2 { font-size: 16px; color: var(--primary); }
.sidebar-nav { flex: 1; overflow-y: auto; padding: 8px 0; }
.nav-item { display: block; padding: 10px 20px; color: var(--text-2); font-size: 14px;
            cursor: pointer; border-left: 3px solid transparent; transition: .15s; text-decoration: none; }
.nav-item:hover { color: var(--text); background: rgba(79,209,197,.06); text-decoration: none; }
.nav-item.active { color: var(--primary); border-left-color: var(--primary);
                   background: rgba(79,209,197,.1); }
.nav-divider { margin: 8px 16px; border-top: 1px solid var(--border); }
.nav-section-label { padding: 6px 20px; font-size: 11px; color: var(--text-2);
                     text-transform: uppercase; letter-spacing: 1px; }

.thk-body { display: flex; height: 100vh; overflow: hidden; }
.thk-main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.thk-header { background: #232334; border-bottom: 1px solid var(--border);
              padding: 12px 24px; flex-shrink: 0;
              display: flex; align-items: center; justify-content: space-between; }
.thk-header h1 { font-size: 18px; }
.thk-content { flex: 1; overflow-y: auto; padding: 20px 24px; }

.toolbar { display: flex; gap: 8px; margin-bottom: 16px; align-items: center; flex-wrap: wrap; }
.toolbar input { width: 200px; }
.toolbar select { width: 140px; }

.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th { background: #232334; color: var(--text-2); text-align: left;
                 padding: 8px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
.data-table td { padding: 8px 10px; border-bottom: 1px solid var(--border); }
.data-table tr:hover td { background: #30304a; }
.data-table a { color: var(--primary); text-decoration: none; }
.data-table a:hover { text-decoration: underline; }

.stats-bar { display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.stat-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
             padding: 14px 20px; min-width: 120px; text-align: center; }
.stat-card .stat-num { font-size: 28px; font-weight: bold; color: var(--primary); }
.stat-card .stat-label { font-size: 12px; color: var(--text-2); margin-top: 4px; }

.config-toolbar { display: flex; gap: 8px; margin-bottom: 16px; align-items: center; flex-wrap: wrap; }
.config-toolbar input { width: 180px; }
.row-edit { width: 180px; margin-right: 8px; }

.detail-grid { display: grid; grid-template-columns: 120px 1fr 120px 1fr; gap: 8px 16px;
               font-size: 14px; margin: 0; }
.detail-grid dt { color: var(--text-2); font-size: 12px; }
.detail-grid dd { margin: 0; word-break: break-all; }

.pre-box { background: #1e1e2e; border: 1px solid var(--border); border-radius: 6px;
           padding: 10px; font-family: Consolas, monospace; font-size: 12px;
           max-height: 400px; overflow: auto; margin: 0; white-space: pre-wrap;
           word-break: break-all; }

.image-gallery { display: flex; flex-wrap: wrap; gap: 12px; }
.image-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
              padding: 10px; max-width: 400px; }
.image-card img { max-width: 100%; border-radius: 4px; display: block; }
.image-card .img-info { font-size: 12px; color: var(--text-2); margin-top: 8px; }

.toast { position: fixed; top: 20px; right: 20px; z-index: 9999; padding: 12px 20px;
         border-radius: 6px; font-size: 14px; color: #fff; display: none; }
.toast.success { background: #059669; }
.toast.error { background: #dc2626; }
.toast.show { display: block; animation: fadeIn .3s; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(-8px); } to { opacity: 1; transform: translateY(0); } }

/* 弹窗 */
.overlay { position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 9999;
           display: flex; align-items: center; justify-content: center; }
.dialog { background: var(--card); border: 1px solid var(--border); border-radius: 12px;
          padding: 24px; min-width: 400px; max-width: 600px; }
.dialog h3 { margin: 0 0 16px; font-size: 16px; color: var(--primary); }
.dialog .form-row { display: flex; gap: 8px; margin-bottom: 12px; align-items: center; }
.dialog .form-row label { width: 80px; font-size: 13px; color: var(--text-2); flex-shrink: 0; }
.dialog .form-row input, .dialog .form-row select { flex: 1; }
.dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }

/* CSV 预览表格 */
.csv-table-wrap { max-height: 300px; overflow: auto; margin-top: 12px; }
.csv-table { border-collapse: collapse; font-size: 12px; width: 100%; }
.csv-table th, .csv-table td { border: 1px solid var(--border); padding: 4px 8px; text-align: left; white-space: nowrap; }
.csv-table th { background: #232334; position: sticky; top: 0; }
.csv-table td { font-family: Consolas, monospace; font-size: 11px; }
"""

# ===== 模板片段 =====

# 侧边栏
SIDEBAR = """
<div class="sidebar">
  <div class="sidebar-header">
    <h2>THK 监控</h2>
  </div>
  <div class="sidebar-nav">
    <a class="nav-item {{ 'active' if active_page == 'overview' }}" href="/monitor/thk/">
      📊 监控总览
    </a>
    <a class="nav-item {{ 'active' if active_page == 'config' }}" href="/monitor/thk/config">
      ⚙ CSV 源配置
    </a>
    <div class="nav-divider"></div>
    <div class="nav-section-label">其他模块</div>
    <a class="nav-item" href="/monitor/">📋 日常监控</a>
    <a class="nav-item" href="/">🔧 异常处理平台</a>
  </div>
</div>
"""

# 页面框架
PAGE_FRAME = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>THK 监控 — 异常处理平台</title>
<style>
:root {
  --bg: #1e1e2e; --card: #2a2a3c; --border: #3a3a4f;
  --text: #e2e2ee; --text-2: #9a9ab0;
  --primary: #4fd1c5; --primary-dark: #3ab8ad;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
       background: var(--bg); color: var(--text); }
a { color: var(--primary); text-decoration: none; }
a:hover { text-decoration: underline; }
.card { background: var(--card); border: 1px solid var(--border);
        border-radius: 8px; padding: 18px; margin-bottom: 16px; }
.card-title { margin: 0 0 14px; font-size: 16px; padding-left: 10px;
              border-left: 4px solid var(--primary); }
input, textarea, button, select { font-family: inherit; }
input, textarea, select {
  background: #1e1e2e; border: 1px solid var(--border); color: var(--text);
  border-radius: 6px; padding: 7px 9px; font-size: 14px;
}
input:focus, textarea:focus, select:focus { outline: none; border-color: var(--primary); }
.btn { display: inline-block; background: transparent; color: var(--text);
       border: 1px solid var(--border); border-radius: 6px; padding: 6px 14px;
       font-size: 13px; cursor: pointer; }
.btn:hover { border-color: var(--primary); color: var(--primary); }
.btn-primary { background: var(--primary); border-color: var(--primary);
               color: #0f2e2b; font-weight: bold; }
.btn-primary:hover { background: var(--primary-dark); border-color: var(--primary-dark);
                     color: #0f2e2b; }
.btn-danger { color: #f87171; border-color: rgba(248,113,113,.4); }
.btn-danger:hover { border-color: #f87171; background: rgba(248,113,113,.1); color: #f87171; }
.btn-sm { padding: 3px 10px; font-size: 12px; }
.btn:disabled { opacity: .4; cursor: not-allowed; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 4px; font-size: 12px;
         border: 1px solid; }
.badge-pending { color: #fbbf24; background: rgba(251,191,36,.15); border-color: rgba(251,191,36,.4); }
.badge-scanning { color: #818cf8; background: rgba(129,140,248,.15); border-color: rgba(129,140,248,.4); }
.badge-csv_parsed { color: #a78bfa; background: rgba(167,139,250,.15); border-color: rgba(167,139,250,.4); }
.badge-image_needed { color: #fb923c; background: rgba(251,146,60,.15); border-color: rgba(251,146,60,.4); }
.badge-image_analysis { color: #60a5fa; background: rgba(96,165,250,.15); border-color: rgba(96,165,250,.4); }
.badge-done { color: #34d399; background: rgba(52,211,153,.15); border-color: rgba(52,211,153,.4); }
.badge-failed { color: #f87171; background: rgba(248,113,113,.15); border-color: rgba(248,113,113,.4); }
.empty { text-align: center; color: var(--text-2); padding: 36px 0; }
.hint { color: var(--text-2); font-size: 12px; margin-top: 8px; }
""" + _THK_STYLE + """</style>
</head>
<body class="thk-body">
""" + SIDEBAR + """
<div class="thk-main">
  <div class="thk-header">
    <h1>{{ page_title }}</h1>
    <span class="hint" style="margin:0">{{ page_subtitle or '' }}</span>
  </div>
  <div class="thk-content">
    {{ content | safe }}
  </div>
</div>
<div class="toast" id="toast"></div>
</body>
</html>
"""

# 总览页 — 统计栏
STATS_BAR = """
<div class="stats-bar" id="stats-bar">
  <div class="stat-card">
    <div class="stat-num">{{ stats.total_tasks }}</div>
    <div class="stat-label">总任务数</div>
  </div>
  <div class="stat-card">
    <div class="stat-num">{{ stats.by_status.get('done', 0) }}</div>
    <div class="stat-label">已完成</div>
  </div>
  <div class="stat-card">
    <div class="stat-num">{{ stats.by_status.get('pending', 0) + stats.by_status.get('scanning', 0) }}</div>
    <div class="stat-label">处理中</div>
  </div>
  <div class="stat-card">
    <div class="stat-num">{{ stats.by_status.get('failed', 0) }}</div>
    <div class="stat-label">失败</div>
  </div>
  <div class="stat-card">
    <div class="stat-num">{{ stats.total_images }}</div>
    <div class="stat-label">图像分析</div>
  </div>
</div>
"""

# 总览页 — 任务列表片段
TASK_LIST_FRAGMENT = """
{% if not tasks %}
<div class="empty">暂无任务，请先 <button class="btn btn-primary" onclick="scanAndCreate()">扫描 CSV 文件</button></div>
{% else %}
<table class="data-table">
  <thead>
    <tr>
      <th style="width:50px">编号</th>
      <th>CSV 文件</th>
      <th>来源</th>
      <th style="width:80px">状态</th>
      <th style="width:80px">需图像</th>
      <th style="width:140px">创建时间</th>
      <th style="width:140px">操作</th>
    </tr>
  </thead>
  <tbody>
    {% for t in tasks %}
    <tr>
      <td><a href="/monitor/thk/tasks/{{ t.id }}">#{{ t.id }}</a></td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="{{ t.csv_file }}">{{ t.csv_file }}</td>
      <td>{{ t.source_name or '-' }}</td>
      <td><span class="badge badge-{{ t.status }}">{{ status_labels[t.status] }}</span></td>
      <td>{% if t.need_image %}<span style="color:#fb923c">是 ({{ t.image_count }})</span>{% else %}否{% endif %}</td>
      <td>{{ t.created_at }}</td>
      <td style="white-space:nowrap">
        <a class="btn btn-sm" href="/monitor/thk/tasks/{{ t.id }}">详情</a>
        <button class="btn btn-sm btn-primary" onclick="runTask({{ t.id }})" {% if t.status == 'done' %}disabled{% endif %}>分析</button>
        <button class="btn btn-sm btn-danger" onclick="deleteTask({{ t.id }})">删除</button>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}
"""

# 总览页 — 完整内容
OVERVIEW_CONTENT = """<div class="toolbar">
  <button class="btn btn-primary" onclick="scanAndCreate()">🔍 扫描 CSV 文件</button>
  <button class="btn" onclick="refreshList()">刷新列表</button>
  <button class="btn" onclick="processAll()">▶ 处理全部待处理</button>
  <span class="hint" style="margin:0">{{ scan_info }}</span>
</div>
<div id="stats-bar-container">
  {{ stats_html | safe }}
</div>
<div class="card">
  <h2 class="card-title">分析任务列表</h2>
  <div id="task-table-wrap">
    {{ tasks_html | safe }}
  </div>
</div>

<script>
function scanAndCreate() {
  showToast("正在扫描 CSV 文件...", "success");
  fetch("/monitor/thk/api/scan", { method: "POST" })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.ok) {
        showToast("扫描完成：发现 " + d.total + " 个文件，新建 " + d.new_tasks + " 个任务", "success");
        refreshList();
        refreshStats();
      } else {
        showToast("扫描失败：" + (d.error || "未知错误"), "error");
      }
    })
    .catch(function() { showToast("网络错误", "error"); });
}

function runTask(id) {
  showToast("正在分析任务 #" + id + "...", "success");
  fetch("/monitor/thk/api/analyze/" + id, { method: "POST" })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.ok) {
        showToast("任务 #" + id + " 分析完成", "success");
        refreshList();
        refreshStats();
      } else {
        showToast("分析失败：" + (d.error || "未知错误"), "error");
      }
    })
    .catch(function() { showToast("网络错误", "error"); });
}

function processAll() {
  showToast("正在批量处理...", "success");
  fetch("/monitor/thk/api/process-all", { method: "POST" })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.ok) {
        showToast("批量处理完成：" + d.processed + " 个任务", "success");
        refreshList();
        refreshStats();
      } else {
        showToast("处理失败", "error");
      }
    })
    .catch(function() { showToast("网络错误", "error"); });
}

function deleteTask(id) {
  if (!confirm("确定删除任务 #" + id + " 吗？关联的图像结果也将删除。")) return;
  fetch("/monitor/thk/api/tasks/" + id + "/delete", { method: "POST" })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.ok) {
        showToast("已删除", "success");
        refreshList();
        refreshStats();
      } else {
        showToast("删除失败", "error");
      }
    })
    .catch(function() { showToast("网络错误", "error"); });
}

function refreshList() {
  var filters = "";
  var statusEl = document.getElementById("filterStatus");
  if (statusEl && statusEl.value) { filters = "?status=" + encodeURIComponent(statusEl.value); }
  fetch("/monitor/thk/partial/tasks" + filters)
    .then(function(r) { return r.text(); })
    .then(function(h) { document.getElementById("task-table-wrap").innerHTML = h; });
}

function refreshStats() {
  fetch("/monitor/thk/partial/stats")
    .then(function(r) { return r.text(); })
    .then(function(h) { document.getElementById("stats-bar-container").innerHTML = h; });
}

function showToast(msg, type) {
  var t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast " + type + " show";
  setTimeout(function() { t.className = "toast"; }, 3000);
}

// 每 10 秒自动刷新列表和统计
setInterval(function() { refreshList(); refreshStats(); }, 10000);
</script>
"""

# 任务详情页
TASK_DETAIL_CONTENT = """<div class="card">
  <h2 class="card-title">基本信息
    <span class="badge badge-{{ task.status }}">{{ status_labels[task.status] }}</span>
  </h2>
  <dl class="detail-grid">
    <dt>CSV 文件</dt><dd>{{ task.csv_file }}</dd>
    <dt>来源</dt><dd>{{ task.source_name or '-' }}</dd>
    <dt>文件路径</dt><dd style="grid-column: span 3">{{ task.csv_path }}</dd>
    <dt>状态</dt><dd>{{ status_labels[task.status] }}</dd>
    <dt>需图像分析</dt><dd>{{ '是' if task.need_image else '否' }}</dd>
    <dt>图片数量</dt><dd>{{ task.image_count }} / {{ task.image_done }} 已完成</dd>
    <dt>创建时间</dt><dd>{{ task.created_at }}</dd>
    <dt>开始时间</dt><dd>{{ task.started_at or '-' }}</dd>
    <dt>完成时间</dt><dd>{{ task.finished_at or '-' }}</dd>
  </dl>
</div>

{% if task.summary %}
<div class="card">
  <h2 class="card-title">分析摘要</h2>
  <p style="font-size:14px">{{ task.summary }}</p>
</div>
{% endif %}

{% if task.error %}
<div class="card">
  <h2 class="card-title">错误信息</h2>
  <pre class="pre-box" style="color:#f87171">{{ task.error }}</pre>
</div>
{% endif %}

{% if csv_preview %}
<div class="card">
  <h2 class="card-title">CSV 数据预览
    <span class="hint">显示前 {{ csv_preview.rows|length }} / {{ csv_preview.total_rows }} 行</span>
  </h2>
  <div class="csv-table-wrap">
    <table class="csv-table">
      <thead>
        <tr>
          {% for h in csv_preview.headers %}
          <th>{{ h }}</th>
          {% endfor %}
        </tr>
      </thead>
      <tbody>
        {% for row in csv_preview.rows %}
        <tr>
          {% for cell in row %}
          <td>{{ cell }}</td>
          {% endfor %}
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endif %}

{% if image_results %}
<div class="card">
  <h2 class="card-title">图像分析结果 ({{ image_results|length }})</h2>
  <div class="image-gallery">
    {% for img in image_results %}
    <div class="image-card">
      {% if img.thumbnail %}
      <img src="data:image/jpeg;base64,{{ img.thumbnail }}" alt="{{ img.image_name }}" loading="lazy">
      {% else %}
      <div style="background:#1e1e2e;height:200px;display:flex;align-items:center;justify-content:center;color:var(--text-2)">
        {{ img.status == 'pending' and '待分析…' or '无图像' }}
      </div>
      {% endif %}
      <div class="img-info">
        <div><strong>{{ img.image_name or img.image_path }}</strong></div>
        <div>状态: <span class="badge badge-{{ 'done' if img.status == 'done' else ('failed' if img.status == 'failed' else 'pending') }}">{{ img.status }}</span></div>
        {% if img.result_json %}
        <div style="margin-top:6px;font-size:11px;max-height:120px;overflow:auto">
          <pre style="margin:0;white-space:pre-wrap">{% for line in img.result_json_lines %}
{{ line }}{% endfor %}</pre>
        </div>
        {% endif %}
        {% if img.error %}
        <div style="color:#f87171;font-size:11px;margin-top:4px">{{ img.error }}</div>
        {% endif %}
      </div>
    </div>
    {% endfor %}
  </div>
</div>
{% endif %}

{% if analysis_result %}
<div class="card">
  <h2 class="card-title">分析决策详情</h2>
  <pre class="pre-box">{{ analysis_result }}</pre>
</div>
{% endif %}

<div class="toolbar">
  <button class="btn btn-primary" onclick="runAnalysis()" {% if task.status == 'done' %}disabled{% endif %}>▶ 执行分析</button>
  <button class="btn btn-danger" onclick="deleteThis()">删除任务</button>
  <a class="btn" href="/monitor/thk/">← 返回列表</a>
</div>

<script>
function runAnalysis() {
  fetch("/monitor/thk/api/analyze/{{ task.id }}", { method: "POST" })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.ok) {
        location.reload();
      } else {
        alert("分析失败：" + (d.error || "未知错误"));
      }
    })
    .catch(function() { alert("网络错误"); });
}

function deleteThis() {
  if (!confirm("确定删除任务 #{{ task.id }} 吗？")) return;
  fetch("/monitor/thk/api/tasks/{{ task.id }}/delete", { method: "POST" })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.ok) { location.href = "/monitor/thk/"; }
      else { alert("删除失败"); }
    })
    .catch(function() { alert("网络错误"); });
}
</script>
"""

# 配置页内容
CONFIG_CONTENT = """<div class="card">
  <h2 class="card-title">CSV 文件源管理
    <span class="hint">配置 UNC 网络路径，系统会定期扫描其中的 CSV 文件</span>
  </h2>
  <div class="config-toolbar">
    <input type="text" id="newName" placeholder="来源名称" maxlength="100">
    <input type="text" id="newPath" placeholder="UNC 路径，如 \\\\server\\share\\folder" maxlength="300" style="width:300px">
    <input type="text" id="newPattern" placeholder="文件匹配，如 *.csv" maxlength="50" style="width:120px" value="*.csv">
    <button class="btn btn-primary" onclick="addSource()">添加</button>
  </div>
  <p class="hint" style="margin-bottom:12px">
    UNC 路径示例：<code>\\\\demo-host\\demo_share\\csv</code>
    请确保运行 Flask 的账户有该路径的读取权限。
  </p>
  <div id="config-table-wrap">
    {{ sources_html | safe }}
  </div>
</div>

<script>
function addSource() {
  var name = document.getElementById("newName").value.trim();
  var path = document.getElementById("newPath").value.trim();
  var pattern = document.getElementById("newPattern").value.trim() || "*.csv";
  if (!name || !path) { showToast("名称和路径为必填", "error"); return; }
  fetch("/monitor/thk/api/sources/add", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name, unc_path: path, file_pattern: pattern })
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.ok) {
      showToast("添加成功", "success");
      document.getElementById("newName").value = "";
      document.getElementById("newPath").value = "";
      reloadSources();
    } else { showToast("添加失败：" + (d.error || ""), "error"); }
  })
  .catch(function() { showToast("网络错误", "error"); });
}

function deleteSource(id) {
  if (!confirm("确定删除该文件源吗？")) return;
  fetch("/monitor/thk/api/sources/" + id + "/delete", { method: "POST" })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.ok) { showToast("已删除", "success"); reloadSources(); }
      else { showToast("删除失败", "error"); }
    })
    .catch(function() { showToast("网络错误", "error"); });
}

function reloadSources() {
  fetch("/monitor/thk/partial/sources")
    .then(function(r) { return r.text(); })
    .then(function(h) { document.getElementById("config-table-wrap").innerHTML = h; });
}

function showToast(msg, type) {
  var t = document.getElementById("toast");
  t.textContent = msg; t.className = "toast " + type + " show";
  setTimeout(function() { t.className = "toast"; }, 3000);
}
</script>
"""

# 配置页 — 来源列表片段
SOURCES_FRAGMENT = """
{% if not sources %}
<div class="empty">暂无 CSV 文件源，请使用上方表单添加</div>
{% else %}
<table class="data-table">
  <thead>
    <tr>
      <th style="width:50px">编号</th>
      <th>名称</th>
      <th>UNC 路径</th>
      <th>文件匹配</th>
      <th>状态</th>
      <th style="width:140px">创建时间</th>
      <th style="width:80px">操作</th>
    </tr>
  </thead>
  <tbody>
    {% for s in sources %}
    <tr>
      <td>{{ s.id }}</td>
      <td>{{ s.name }}</td>
      <td style="font-family:Consolas,monospace;font-size:12px;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{{ s.unc_path }}">{{ s.unc_path }}</td>
      <td>{{ s.file_pattern }}</td>
      <td>
        <span class="badge {{ 'badge-done' if s.enabled else 'badge-failed' }}">
          {{ '启用' if s.enabled else '停用' }}
        </span>
      </td>
      <td>{{ s.created_at }}</td>
      <td>
        <button class="btn btn-sm btn-danger" onclick="deleteSource({{ s.id }})">删除</button>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}
"""


# ===== 辅助函数 =====

def _task_with_source(task: dict) -> dict:
    """给 task 附加 source_name。"""
    if task.get("source_id"):
        sources = {s["id"]: s for s in database.list_csv_sources()}
        src = sources.get(task["source_id"])
        task["source_name"] = src["name"] if src else None
    else:
        task["source_name"] = None
    return task


# ===== 页面路由 =====

@bp.get("/")
def overview():
    """THK 监控总览页。"""
    tasks = database.list_tasks(limit=50)
    for t in tasks:
        _task_with_source(t)
    stats = database.get_stats()
    stats_html = render_template_string(STATS_BAR, stats=stats)
    tasks_html = render_template_string(
        TASK_LIST_FRAGMENT,
        tasks=tasks,
        status_labels=STATUS_LABELS,
    )
    content = render_template_string(
        OVERVIEW_CONTENT,
        stats_html=stats_html,
        tasks_html=tasks_html,
        scan_info="",
    )
    return render_template_string(
        PAGE_FRAME,
        page_title="THK 监控总览",
        page_subtitle="CSV 文件扫描 · 数据分析 · 图像识别",
        active_page="overview",
        content=content,
    )


@bp.get("/partial/tasks")
def partial_tasks():
    """任务列表局部刷新。"""
    status = request.args.get("status")
    tasks = database.list_tasks(status=status, limit=50)
    for t in tasks:
        _task_with_source(t)
    return render_template_string(
        TASK_LIST_FRAGMENT,
        tasks=tasks,
        status_labels=STATUS_LABELS,
    )


@bp.get("/partial/stats")
def partial_stats():
    """统计栏局部刷新。"""
    stats = database.get_stats()
    return render_template_string(STATS_BAR, stats=stats)


@bp.get("/config")
def config_page():
    """CSV 源配置页。"""
    sources = database.list_csv_sources()
    sources_html = render_template_string(SOURCES_FRAGMENT, sources=sources)
    content = render_template_string(CONFIG_CONTENT, sources_html=sources_html)
    return render_template_string(
        PAGE_FRAME,
        page_title="CSV 源配置",
        page_subtitle="管理 CSV 文件来源",
        active_page="config",
        content=content,
    )


@bp.get("/partial/sources")
def partial_sources():
    """来源列表局部刷新。"""
    sources = database.list_csv_sources()
    return render_template_string(SOURCES_FRAGMENT, sources=sources)


@bp.get("/tasks/<int:task_id>")
def task_detail(task_id):
    """任务详情页。"""
    task = database.get_task(task_id)
    if not task:
        return render_template_string(
            PAGE_FRAME,
            page_title="任务不存在",
            page_subtitle="",
            active_page="overview",
            content='<div class="empty">任务 #%d 不存在，<a href="/monitor/thk/">返回列表</a></div>' % task_id,
        )

    _task_with_source(task)

    # 读取 CSV 预览
    csv_preview = None
    try:
        csv_preview = csv_reader.read_csv(task["csv_path"])
    except Exception:
        pass

    # 读取图像结果
    image_results = database.get_image_results(task_id)
    for img in image_results:
        # 格式化为行
        if img.get("result_json"):
            try:
                parsed = json.loads(img["result_json"])
                img["result_json_lines"] = json.dumps(
                    parsed, ensure_ascii=False, indent=2
                ).splitlines()
            except (ValueError, TypeError):
                img["result_json_lines"] = [img["result_json"]]
        else:
            img["result_json_lines"] = []

    # 分析决策
    analysis_result = None
    if task.get("result_json"):
        try:
            parsed = json.loads(task["result_json"])
            analysis_result = json.dumps(parsed, ensure_ascii=False, indent=2)
        except (ValueError, TypeError):
            analysis_result = task["result_json"]

    content = render_template_string(
        TASK_DETAIL_CONTENT,
        task=task,
        status_labels=STATUS_LABELS,
        csv_preview=csv_preview,
        image_results=image_results,
        analysis_result=analysis_result,
    )
    return render_template_string(
        PAGE_FRAME,
        page_title="任务 #%d" % task_id,
        page_subtitle="",
        active_page="overview",
        content=content,
    )


# ===== API 路由 =====

@bp.post("/api/scan")
def api_scan():
    """扫描所有启用的 CSV 源，发现新文件时创建分析任务。"""
    sources = database.list_csv_sources()
    total_files = 0
    new_tasks = 0
    errors = []

    for src in sources:
        if not src["enabled"]:
            continue
        try:
            files = csv_reader.scan_csv_files(
                src["unc_path"], src["file_pattern"]
            )
            total_files += len(files)

            # 检查已存在（通过 csv_path 去重）
            existing = set()
            for t in database.list_tasks(source_id=src["id"], limit=1000):
                existing.add(t["csv_path"])

            for f in files:
                if f["path"] in existing:
                    continue
                try:
                    database.create_task(
                        source_id=src["id"],
                        csv_file=f["name"],
                        csv_path=f["path"],
                    )
                    new_tasks += 1
                except Exception as e:
                    errors.append(f"{f['name']}: {e}")
        except Exception as e:
            errors.append(f"{src['name']}: {e}")

    return jsonify(
        ok=True,
        total=total_files,
        new_tasks=new_tasks,
        errors=errors[:5] if errors else [],
    )


@bp.post("/api/analyze/<int:task_id>")
def api_analyze(task_id):
    """执行单个任务的完整分析 pipeline：CSV解析 → 分析决策 → 图像识别。"""
    task = database.get_task(task_id)
    if not task:
        return jsonify(ok=False, error="任务不存在"), 404

    try:
        # Step 1: CSV 解析
        database.update_task_status(task_id, "scanning", started_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        csv_data = csv_reader.read_csv(task["csv_path"])

        database.update_task_status(task_id, "csv_parsed")

        # Step 2: 分析决策
        analysis = analyzer.analyze_csv(csv_data)

        if analysis["need_image"] and analysis["image_paths"]:
            database.update_task_status(
                task_id, "image_needed",
                need_image=1,
                image_count=len(analysis["image_paths"]),
                summary=analysis["summary"],
                result_json=json.dumps(analysis, ensure_ascii=False),
            )

            # Step 3: 图像分析
            database.update_task_status(task_id, "image_analysis")
            image_done = 0
            for img_path in analysis["image_paths"]:
                img_id = database.add_image_result(
                    task_id, img_path,
                    image_name=os.path.basename(img_path),
                )
                try:
                    result = image_analyzer.full_analysis(img_path, analysis)
                    thumbnail = result.get("defect_result", {}).get(
                        "annotated_base64", ""
                    )
                    database.update_image_result(
                        img_id, "done",
                        result_json=json.dumps(
                            {
                                "has_anomaly": result["has_anomaly"],
                                "anomaly_summary": result["anomaly_summary"],
                                "mura_count": len(result.get("mura_result", {}).get("regions", [])),
                                "defect_count": result.get("defect_result", {}).get("defect_count", 0),
                            },
                            ensure_ascii=False,
                        ),
                        thumbnail=thumbnail,
                    )
                    image_done += 1
                except Exception as e:
                    database.update_image_result(img_id, "failed", error=str(e))

            database.update_task_status(
                task_id, "done",
                image_done=image_done,
                finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
        else:
            # 不需要图像分析，直接完成
            database.update_task_status(
                task_id, "done",
                summary=analysis["summary"],
                result_json=json.dumps(analysis, ensure_ascii=False),
                finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )

        return jsonify(ok=True, analysis=analysis)

    except Exception as e:
        import traceback
        traceback.print_exc()
        database.update_task_status(task_id, "failed", error=str(e))
        return jsonify(ok=False, error=str(e)), 500


@bp.post("/api/process-all")
def api_process_all():
    """批量处理所有待处理任务。"""
    pending = database.list_tasks(
        status="pending", limit=50
    ) + database.list_tasks(status="image_needed", limit=50)

    processed = 0
    errors = []
    for task in pending:
        try:
            # 递归调用 analyze 逻辑（简化版：直接内联）
            csv_data = csv_reader.read_csv(task["csv_path"])
            analysis = analyzer.analyze_csv(csv_data)
            database.update_task_status(
                task["id"], "done",
                summary=analysis["summary"],
                result_json=json.dumps(analysis, ensure_ascii=False),
                finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            processed += 1
        except Exception as e:
            errors.append(f"#{task['id']}: {e}")
            try:
                database.update_task_status(task["id"], "failed", error=str(e))
            except Exception:
                pass

    return jsonify(ok=True, processed=processed, errors=errors[:5])


@bp.post("/api/tasks/<int:task_id>/delete")
def api_delete_task(task_id):
    """删除任务。"""
    ok = database.delete_task(task_id)
    return jsonify(ok=ok)


# ===== CSV 源配置 API =====

@bp.post("/api/sources/add")
def api_add_source():
    """添加 CSV 源。"""
    try:
        body = request.get_json(force=True, silent=True) or {}
    except Exception:
        body = {}
    name = (body.get("name") or "").strip()
    unc_path = (body.get("unc_path") or "").strip()
    file_pattern = (body.get("file_pattern") or "*.csv").strip()
    if not name or not unc_path:
        return jsonify(ok=False, error="名称和路径为必填"), 400
    try:
        sid = database.add_csv_source(name, unc_path, file_pattern)
        return jsonify(ok=True, id=sid)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500


@bp.post("/api/sources/<int:source_id>/delete")
def api_delete_source(source_id):
    """删除 CSV 源。"""
    ok = database.delete_csv_source(source_id)
    return jsonify(ok=ok)


# ===== 输出文件服务 =====

@bp.get("/output/<path:filename>")
def serve_output(filename):
    """提供 output 目录下的文件。"""
    from flask import send_from_directory
    return send_from_directory(OUTPUT_DIR, filename)