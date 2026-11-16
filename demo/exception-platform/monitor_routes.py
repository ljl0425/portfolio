"""日常监控模块 — Blueprint，挂载在 /monitor/ 下，内嵌到异常处理中台。

- 左侧固定侧边栏 + 右侧主内容区；
- 总览页：日期选择器 + 监控事项列表（点击跳转查看 HTML 报告）；
- 配置页：监控事项的增删改查；
- 模板全部内联，纯标准库，无 CDN 依赖。
"""

import os
import sys
import time

from flask import (
    Blueprint,
    jsonify,
    render_template_string,
    request,
    send_from_directory,
)

# 导入监控平台的 database（与异常中台 database.py 区分）
_MONITOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "monitor-platform")
if _MONITOR_DIR not in sys.path:
    sys.path.insert(0, _MONITOR_DIR)

# 显式从 monitor-platform 目录导入，避免与同目录的 database.py 冲突
import importlib.util
_spec = importlib.util.spec_from_file_location("monitor_database", os.path.join(_MONITOR_DIR, "database.py"))
monitor_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(monitor_db)

bp = Blueprint("monitor", __name__, url_prefix="/monitor")

OUTPUT_DIR = os.path.join(_MONITOR_DIR, "output")
STATIC_DIR = os.path.join(_MONITOR_DIR, "static")

# ===== 样式（复用异常中台 CSS 变量，只补充监控独有的样式） =====

_MONITOR_STYLE = """
/* 监控模块独有样式 */
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

.monitor-body { display: flex; height: 100vh; overflow: hidden; }
.monitor-main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.monitor-header { background: #232334; border-bottom: 1px solid var(--border);
                  padding: 12px 24px; flex-shrink: 0;
                  display: flex; align-items: center; justify-content: space-between; }
.monitor-header h1 { font-size: 18px; }
.monitor-content { flex: 1; overflow-y: auto; padding: 20px 24px; }

.date-picker { display: flex; align-items: center; gap: 8px; margin-bottom: 16px; }
.date-picker .arrow { background: transparent; border: 1px solid var(--border);
                      color: var(--text); border-radius: 6px; padding: 4px 10px;
                      cursor: pointer; font-size: 16px; }
.date-picker .arrow:hover { border-color: var(--primary); color: var(--primary); }
.date-picker .date-val { font-size: 16px; font-weight: bold; color: var(--primary);
                         min-width: 100px; text-align: center; }
.date-picker .date-input { background: #1e1e2e; border: 1px solid var(--border);
                           color: var(--text); border-radius: 6px; padding: 5px 8px;
                           font-size: 14px; width: 140px; }
.date-picker .date-input:focus { border-color: var(--primary); outline: none; }

.item-row { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
            margin-bottom: 8px; overflow: hidden; }
.item-header { display: flex; align-items: center; padding: 12px 16px; }
.item-clickable { cursor: pointer; user-select: none; }
.item-clickable:hover { background: rgba(79,209,197,.06); }
.item-name { flex: 1; font-size: 14px; font-weight: bold; }
.item-status { flex-shrink: 0; margin-left: 12px; }
.item-action { flex-shrink: 0; margin-left: 12px; }
.item-action-hint { flex-shrink: 0; margin-left: 12px; color: var(--primary); font-size: 13px;
                    opacity: 0; transition: opacity .15s; }
.item-clickable:hover .item-action-hint { opacity: 1; }
.no-data { text-align: center; padding: 40px 20px; color: var(--text-2); }
.no-data .btn { margin-top: 12px; }

.config-toolbar { display: flex; gap: 8px; margin-bottom: 16px; align-items: center; flex-wrap: wrap; }
.config-toolbar input { width: 200px; }
.config-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.config-table th { background: #232334; color: var(--text-2); text-align: left;
                   padding: 8px 10px; border-bottom: 1px solid var(--border); }
.config-table td { padding: 8px 10px; border-bottom: 1px solid var(--border); }
.config-table tr:hover td { background: #30304a; }
.config-table .row-edit { width: 200px; margin-right: 8px; }
.config-table .row-edit-desc { width: 300px; margin-right: 8px; }

.toast { position: fixed; top: 20px; right: 20px; z-index: 9999; padding: 12px 20px;
         border-radius: 6px; font-size: 14px; color: #fff; display: none; }
.toast.success { background: #059669; }
.toast.error { background: #dc2626; }
.toast.show { display: block; animation: fadeIn .3s; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(-8px); } to { opacity: 1; transform: translateY(0); } }
"""

# ===== 模板片段 =====

# 侧边栏
SIDEBAR = """
<div class="sidebar">
  <div class="sidebar-header">
    <h2>日常监控</h2>
  </div>
  <div class="sidebar-nav">
    <a class="nav-item {{ 'active' if active_page == 'overview' }}" href="/monitor/">
      📊 监控总览
    </a>
    <a class="nav-item {{ 'active' if active_page == 'config' }}" href="/monitor/config">
      ⚙ 事项配置
    </a>
    <a class="nav-item {{ 'active' if active_page == 'schedules' }}" href="/monitor/schedules">
      ⏰ 调度配置
    </a>
    <a class="nav-item {{ 'active' if active_page == 'custom_yield' }}" href="/monitor/custom-yield">
      📈 自定义良率监控
    </a>
    <div style="margin:8px 16px;border-top:1px solid var(--border)"></div>
    <div style="padding:6px 20px;font-size:11px;color:var(--text-2);text-transform:uppercase;letter-spacing:1px">其他模块</div>
    <a class="nav-item" href="/monitor/thk/">
      🔬 THK 监控
    </a>
  </div>
</div>
"""

# 页面框架
PAGE_FRAME = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>日常监控 — 异常处理平台</title>
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
input, textarea, button { font-family: inherit; }
input, textarea {
  background: #1e1e2e; border: 1px solid var(--border); color: var(--text);
  border-radius: 6px; padding: 7px 9px; font-size: 14px;
}
input:focus, textarea:focus { outline: none; border-color: var(--primary); }
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
.badge-generating { color: #60a5fa; background: rgba(96,165,250,.15); border-color: rgba(96,165,250,.4); }
.badge-success { color: #34d399; background: rgba(52,211,153,.15); border-color: rgba(52,211,153,.4); }
.badge-failed  { color: #f87171; background: rgba(248,113,113,.15); border-color: rgba(248,113,113,.4); }
.badge-no_data { color: #9a9ab0; background: rgba(154,154,176,.15); border-color: rgba(154,154,176,.4); }
.empty { text-align: center; color: var(--text-2); padding: 36px 0; }
.hint { color: var(--text-2); font-size: 12px; margin-top: 8px; }
""" + _MONITOR_STYLE + """</style>
<meta name="csrf-token" content="{{ csrf_token }}">
</head>
<body class="monitor-body">
""" + SIDEBAR + """
<div class="monitor-main">
  <div class="monitor-header">
    <h1>{{ page_title }}</h1>
    <span class="hint" style="margin:0"><a href="/">← 返回异常处理平台</a> &nbsp; {{ page_subtitle or '' }}</span>
  </div>
  <div class="monitor-content">
    {{ content | safe }}
  </div>
</div>
<div class="toast" id="toast"></div>
	<script>
	// CSRF 防护：自动为同源 XMLHttpRequest 写请求注入 token
	(function(){
	  var meta = document.querySelector('meta[name="csrf-token"]');
	  var csrf = meta ? meta.content : '';
	  if (!csrf) return;
	  var _open = XMLHttpRequest.prototype.open;
	  XMLHttpRequest.prototype.open = function(method, url) {
	    this._csrf_method = method;
	    this._csrf_url = url;
	    return _open.apply(this, arguments);
	  };
	  var _send = XMLHttpRequest.prototype.send;
	  XMLHttpRequest.prototype.send = function(body) {
	    var method = (this._csrf_method || '').toUpperCase();
	    if (method === 'GET' || method === 'HEAD' || method === 'OPTIONS') {
	      return _send.apply(this, arguments);
	    }
	    var url = this._csrf_url || '';
	    if (url.indexOf('://') !== -1) {
	      if (url.substring(0, location.origin.length) !== location.origin) {
	        return _send.apply(this, arguments);
	      }
	    }
	    try { this.setRequestHeader('X-CSRF-Token', csrf); } catch(e) {}
	    return _send.apply(this, arguments);
	  };
	})();
	</script>
</body>
</html>
"""

# 总览页 — 事项列表片段
OVERVIEW_ITEMS_FRAGMENT = """
{% if not data_list %}
<div class="card">
  <div class="empty">暂无监控事项，请先到 <a href="/monitor/config">事项配置</a> 页面添加事项</div>
</div>
{% else %}
<div id="items-container">
  {% for d in data_list %}
  <div class="item-row" data-item-id="{{ d.item_id }}">
    {% if d.status == 'success' and d.html_path %}
    <div class="item-header item-clickable" onclick="openReport('/monitor/output/{{ d.html_path }}')">
      <span class="item-name">{{ d.name }}</span>
      <span class="item-status">
        <span class="badge badge-success">已生成</span>
      </span>
      <span class="item-action-hint">查看详情 →</span>
    </div>
    {% elif d.status == 'no_data' %}
    <div class="item-header">
      <span class="item-name">{{ d.name }}</span>
      <span class="item-status">
        <span class="badge badge-no_data">未生成</span>
      </span>
      <span class="item-action">
        <button class="btn btn-primary btn-sm" onclick="event.stopPropagation(); generateData({{ d.item_id }}, '{{ date }}')">生成报告</button>
      </span>
    </div>
    {% elif d.status == 'pending' %}
    <div class="item-header">
      <span class="item-name">{{ d.name }}</span>
      <span class="item-status">
        <span class="badge badge-pending">待生成</span>
      </span>
      <span class="item-action">
        <span class="hint" style="margin:0">准备中，请稍后刷新...</span>
      </span>
    </div>
    {% elif d.status == 'generating' %}
    <div class="item-header">
      <span class="item-name">{{ d.name }}</span>
      <span class="item-status">
        <span class="badge badge-generating">生成中</span>
      </span>
      <span class="item-action">
        <span class="hint" style="margin:0">生成中，请稍后刷新...</span>
      </span>
    </div>
    {% elif d.status == 'failed' %}
    <div class="item-header">
      <span class="item-name">{{ d.name }}</span>
      <span class="item-status">
        <span class="badge badge-failed">失败</span>
      </span>
      <span class="item-action">
        <button class="btn btn-primary btn-sm" onclick="event.stopPropagation(); generateData({{ d.item_id }}, '{{ date }}')">重新生成</button>
      </span>
    </div>
    {% endif %}
  </div>
  {% endfor %}
</div>
{% endif %}
"""

# 总览页 — 完整内容
OVERVIEW_CONTENT = """<div class="date-picker">
  <button class="arrow" onclick="changeDate(-1)" title="前一天">◀</button>
  <input type="date" class="date-input" id="dateInput" value="{{ date }}" onchange="onDateChange()">
  <button class="arrow" onclick="changeDate(1)" title="后一天">▶</button>
  <span class="date-val" id="dateDisplay">{{ date }}</span>
  <button class="btn btn-sm" onclick="goToday()">今天</button>
</div>
<div id="overview-items">
  {{ items_html | safe }}
</div>

<script>
var currentDate = "{{ date }}";
var today = "{{ today }}";
var pollingTimer = null;

function changeDate(delta) {
  var d = new Date(currentDate + "T00:00:00");
  d.setDate(d.getDate() + delta);
  currentDate = d.toISOString().slice(0, 10);
  document.getElementById("dateInput").value = currentDate;
  document.getElementById("dateDisplay").textContent = currentDate;
  fullReloadOverview();
}

function onDateChange() {
  currentDate = document.getElementById("dateInput").value;
  document.getElementById("dateDisplay").textContent = currentDate;
  fullReloadOverview();
}

function goToday() {
  currentDate = today;
  document.getElementById("dateInput").value = currentDate;
  document.getElementById("dateDisplay").textContent = currentDate;
  triggerGenerateToday();
  fullReloadOverview();
}

function fullReloadOverview() {
  /* 日期切换：全量替换整个事项区域 */
  var xhr = new XMLHttpRequest();
  xhr.open("GET", "/monitor/partial/overview?date=" + encodeURIComponent(currentDate));
  xhr.onload = function() {
    if (xhr.status === 200) {
      document.getElementById("overview-items").innerHTML = xhr.responseText;
      startPolling();
    }
  };
  xhr.send();
}

function triggerGenerateToday() {
  /* 只在用户主动点击"今天"按钮时触发当天生成 */
  var xhr = new XMLHttpRequest();
  xhr.open("POST", "/monitor/generate-all/" + currentDate);
  xhr.send();
}

function fetchOverview() {
  /* 轮询：增量 patch，只更新状态变化 */
  var xhr = new XMLHttpRequest();
  xhr.open("GET", "/monitor/partial/overview?date=" + encodeURIComponent(currentDate));
  xhr.onload = function() {
    if (xhr.status === 200) {
      var tmp = document.createElement("div");
      tmp.innerHTML = xhr.responseText;
      var hasPending = false;
      tmp.querySelectorAll(".item-row").forEach(function(newRow) {
        var id = newRow.getAttribute("data-item-id");
        var oldRow = document.querySelector("#overview-items .item-row[data-item-id='" + id + "']");
        if (!oldRow) return;

        // 更新整个 item-header（状态变化时替换内容）
        var oldStatus = oldRow.querySelector(".item-status");
        var newStatus = newRow.querySelector(".item-status");
        var oldBadge = oldStatus ? oldStatus.textContent.trim() : "";
        var newBadge = newStatus ? newStatus.textContent.trim() : "";

        var needsRefresh = oldBadge.indexOf("待生成") !== -1 || oldBadge.indexOf("生成中") !== -1 || oldBadge.indexOf("未生成") !== -1;
        if (needsRefresh && newBadge.indexOf("已生成") !== -1) {
          // 状态变为成功：替换整行为可点击链接
          oldRow.innerHTML = newRow.innerHTML;
          // 新行不再需要展开，直接替换完成
        } else if (oldBadge !== newBadge) {
          // 普通状态变化：直接替换整个 item-header
          var oldHeader = oldRow.querySelector(".item-header");
          var newHeader = newRow.querySelector(".item-header");
          if (oldHeader && newHeader) {
            oldHeader.innerHTML = newHeader.innerHTML;
            oldHeader.className = newHeader.className;
          }
        }

        if (newBadge.indexOf("待生成") !== -1 || newBadge.indexOf("生成中") !== -1) {
          hasPending = true;
        }
      });
      if (!hasPending) {
        stopPolling();
      }
    }
  };
  xhr.send();
}

function startPolling() {
  stopPolling();
  pollingTimer = setInterval(fetchOverview, 3000);
}

function stopPolling() {
  if (pollingTimer) {
    clearInterval(pollingTimer);
    pollingTimer = null;
  }
}

// 首次加载：完整渲染
fullReloadOverview();

function openReport(url) {
  window.open(url, "_blank");
}

function generateData(itemId, date) {
  var xhr = new XMLHttpRequest();
  xhr.open("POST", "/monitor/generate/" + itemId + "/" + date);
  xhr.setRequestHeader("Content-Type", "application/json");
  xhr.onload = function() {
    if (xhr.status === 200) {
      showToast("已提交生成任务，请稍后刷新查看", "success");
      setTimeout(fullReloadOverview, 2000);
    } else {
      showToast("提交失败", "error");
    }
  };
  xhr.send();
}

function showToast(msg, type) {
  var t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast " + type + " show";
  setTimeout(function() { t.className = "toast"; }, 3000);
}
</script>
"""

# 配置页 — 配置表片段
CONFIG_TABLE_FRAGMENT = """
{% if not items %}
<div class="empty">暂无监控事项，请使用上方表单添加</div>
{% else %}
<table class="config-table">
  <thead>
    <tr>
      <th style="width:60px">编号</th>
      <th>事项名称</th>
      <th>描述</th>
      <th style="width:160px">创建时间</th>
      <th style="width:120px">操作</th>
    </tr>
  </thead>
  <tbody>
    {% for item in items %}
    <tr data-id="{{ item.id }}">
      <td>{{ item.id }}</td>
      <td class="cfg-name"><span class="cfg-name-text">{{ item.name }}</span></td>
      <td class="cfg-desc"><span class="cfg-desc-text">{{ item.description or '-' }}</span></td>
      <td>{{ item.created_at }}</td>
      <td class="cfg-actions" style="white-space:nowrap">
        <button class="btn btn-sm" onclick="editItem({{ item.id }})">编辑</button>
        <button class="btn btn-sm btn-danger" onclick="deleteItem({{ item.id }})">删除</button>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}
"""

# 配置页 — 完整内容
CONFIG_CONTENT = """<div class="card">
  <h2 class="card-title">监控事项管理</h2>
  <div class="config-toolbar">
    <input type="text" id="newItemName" placeholder="事项名称" maxlength="100">
    <input type="text" id="newItemDesc" placeholder="事项描述（选填）" maxlength="200" style="width:280px">
    <button class="btn btn-primary" onclick="addItem()">添加</button>
  </div>
  <div id="config-table-wrap">
    {{ table_html | safe }}
  </div>
</div>

<script>
function addItem() {
  var name = document.getElementById("newItemName").value.trim();
  var desc = document.getElementById("newItemDesc").value.trim();
  if (!name) { showToast("请输入事项名称", "error"); return; }
  var xhr = new XMLHttpRequest();
  xhr.open("POST", "/monitor/config/add");
  xhr.setRequestHeader("Content-Type", "application/x-www-form-urlencoded");
  xhr.onload = function() {
    if (xhr.status === 200) {
      document.getElementById("config-table-wrap").innerHTML = xhr.responseText;
      document.getElementById("newItemName").value = "";
      document.getElementById("newItemDesc").value = "";
      showToast("添加成功", "success");
    } else {
      showToast("添加失败：" + xhr.responseText, "error");
    }
  };
  xhr.send("name=" + encodeURIComponent(name) + "&description=" + encodeURIComponent(desc));
}

function editItem(id) {
  var row = document.querySelector("tr[data-id='" + id + "']");
  if (!row) return;
  var nameCell = row.querySelector(".cfg-name");
  var descCell = row.querySelector(".cfg-desc");
  var nameVal = nameCell.querySelector(".cfg-name-text").textContent;
  var descVal = descCell.querySelector(".cfg-desc-text").textContent;
  nameCell.innerHTML = '<input class="row-edit" id="editName_' + id + '" value="' + escapeHtml(nameVal) + '" maxlength="100">';
  descCell.innerHTML = '<input class="row-edit-desc" id="editDesc_' + id + '" value="' + escapeHtml(descVal) + '" maxlength="200">';
  var actions = row.querySelector(".cfg-actions");
  actions.innerHTML = '<button class="btn btn-sm btn-primary" onclick="saveEdit(' + id + ')">保存</button>'
    + '<button class="btn btn-sm" onclick="cancelEdit(' + id + ')">取消</button>';
}

function saveEdit(id) {
  var name = document.getElementById("editName_" + id).value.trim();
  var desc = document.getElementById("editDesc_" + id).value.trim();
  if (!name) { showToast("名称不能为空", "error"); return; }
  var xhr = new XMLHttpRequest();
  xhr.open("POST", "/monitor/config/edit/" + id);
  xhr.setRequestHeader("Content-Type", "application/x-www-form-urlencoded");
  xhr.onload = function() {
    if (xhr.status === 200) {
      document.getElementById("config-table-wrap").innerHTML = xhr.responseText;
      showToast("保存成功", "success");
    } else {
      showToast("保存失败：" + xhr.responseText, "error");
    }
  };
  xhr.send("name=" + encodeURIComponent(name) + "&description=" + encodeURIComponent(desc));
}

function cancelEdit(id) {
  var xhr = new XMLHttpRequest();
  xhr.open("GET", "/monitor/partial/config-table");
  xhr.onload = function() {
    if (xhr.status === 200) {
      document.getElementById("config-table-wrap").innerHTML = xhr.responseText;
    }
  };
  xhr.send();
}

function deleteItem(id) {
  if (!confirm("确定要删除该事项吗？关联的监控数据也会一并删除。")) return;
  var xhr = new XMLHttpRequest();
  xhr.open("POST", "/monitor/config/delete/" + id);
  xhr.setRequestHeader("Content-Type", "application/x-www-form-urlencoded");
  xhr.onload = function() {
    if (xhr.status === 200) {
      document.getElementById("config-table-wrap").innerHTML = xhr.responseText;
      showToast("删除成功", "success");
    } else {
      showToast("删除失败：" + xhr.responseText, "error");
    }
  };
  xhr.send();
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function showToast(msg, type) {
  var t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast " + type + " show";
  setTimeout(function() { t.className = "toast"; }, 3000);
}
</script>
"""


# 调度配置页 — 调度表片段
SCHEDULE_TABLE_FRAGMENT = """
{% if not schedules %}
<div class="empty">暂无调度任务，请使用上方表单添加</div>
{% else %}
<table class="config-table">
  <thead>
    <tr>
      <th style="width:50px">编号</th>
      <th>任务名称</th>
      <th style="width:70px">类型</th>
      <th>关联事项</th>
      <th style="width:80px">执行时间</th>
      <th style="width:80px">会议编号</th>
      <th style="width:60px">状态</th>
      <th style="width:140px">上次执行</th>
      <th style="width:100px">操作</th>
    </tr>
  </thead>
  <tbody>
    {% for s in schedules %}
    <tr data-id="{{ s.id }}">
      <td>{{ s.id }}</td>
      <td class="sch-name"><span class="sch-name-text">{{ s.name }}</span></td>
      <td><span class="badge badge-{{ 'success' if s.schedule_type == 'generate' else 'generating' }}">{{ '生成' if s.schedule_type == 'generate' else '推送' }}</span></td>
      <td class="sch-item"><span class="sch-item-text">{{ s.item_name or '-' }}</span></td>
      <td class="sch-time"><span class="sch-time-text">{{ s.cron_time }}</span></td>
      <td class="sch-code"><span class="sch-code-text">{{ s.meeting_code or '-' }}</span></td>
      <td>
        <button class="btn btn-sm {{ 'btn-primary' if s.enabled else '' }}" onclick="toggleSchedule({{ s.id }})" style="min-width:50px">
          {{ '启用' if s.enabled else '停用' }}
        </button>
      </td>
      <td style="font-size:11px;color:var(--text-2)">
        {% if s.last_run_at %}
        {{ s.last_run_at }}<br><span style="font-size:10px">{{ s.last_run_result or '' }}</span>
        {% else %}
        -
        {% endif %}
      </td>
      <td class="sch-actions" style="white-space:nowrap">
        <button class="btn btn-sm" onclick="editSchedule({{ s.id }})">编辑</button>
        <button class="btn btn-sm btn-danger" onclick="deleteSchedule({{ s.id }})">删除</button>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}
"""

# 调度配置页 — 完整内容
SCHEDULE_CONTENT = """<div class="card">
  <h2 class="card-title">定时调度配置</h2>
  <p class="hint" style="margin-bottom:12px">配置报告的自动生成和推送时间。调度器每 30 秒检查一次，匹配当前时间（HH:MM）即执行。</p>
  <div class="config-toolbar" style="flex-wrap:wrap;gap:6px">
    <input type="text" id="newSchName" placeholder="任务名称" maxlength="50" style="width:140px">
    <select id="newSchType" style="width:90px;background:#1e1e2e;border:1px solid var(--border);color:var(--text);border-radius:6px;padding:7px 6px;font-size:13px">
      <option value="generate">生成</option>
      <option value="push">推送</option>
    </select>
    <select id="newSchItem" style="width:160px;background:#1e1e2e;border:1px solid var(--border);color:var(--text);border-radius:6px;padding:7px 6px;font-size:13px">
      <option value="">推送不关联事项</option>
      {% for item in items %}
      <option value="{{ item.id }}">{{ item.name }}</option>
      {% endfor %}
    </select>
    <input type="text" id="newSchTime" placeholder="HH:MM" maxlength="5" style="width:80px" value="07:00">
    <input type="text" id="newSchCode" placeholder="会议编号（推送用）" maxlength="20" style="width:150px">
    <button class="btn btn-primary" onclick="addSchedule()">添加</button>
  </div>
  <div id="schedule-table-wrap">
    {{ table_html | safe }}
  </div>
</div>

<script>
function addSchedule() {
  var name = document.getElementById("newSchName").value.trim();
  var type = document.getElementById("newSchType").value;
  var itemId = document.getElementById("newSchItem").value;
  var time = document.getElementById("newSchTime").value.trim();
  var code = document.getElementById("newSchCode").value.trim();
  if (!name || !time) { showToast("名称和时间为必填", "error"); return; }
  if (!/^\\d{2}:\\d{2}$/.test(time)) { showToast("时间格式应为 HH:MM", "error"); return; }
  var xhr = new XMLHttpRequest();
  xhr.open("POST", "/monitor/schedules/add");
  xhr.setRequestHeader("Content-Type", "application/x-www-form-urlencoded");
  xhr.onload = function() {
    if (xhr.status === 200) {
      document.getElementById("schedule-table-wrap").innerHTML = xhr.responseText;
      document.getElementById("newSchName").value = "";
      document.getElementById("newSchCode").value = "";
      showToast("添加成功", "success");
    } else {
      showToast("添加失败：" + xhr.responseText, "error");
    }
  };
  xhr.send("name=" + encodeURIComponent(name) + "&type=" + type + "&item_id=" + itemId + "&cron_time=" + time + "&meeting_code=" + encodeURIComponent(code));
}

function editSchedule(id) {
  var row = document.querySelector("tr[data-id='" + id + "']");
  if (!row) return;
  var nameVal = row.querySelector(".sch-name-text").textContent;
  var itemVal = row.querySelector(".sch-item-text").textContent;
  var timeVal = row.querySelector(".sch-time-text").textContent;
  var codeVal = row.querySelector(".sch-code-text").textContent;
  // 获取 type（从 badge 文字判断）
  var badge = row.querySelector(".badge");
  var typeVal = badge.textContent.trim() === "生成" ? "generate" : "push";

  row.querySelector(".sch-name").innerHTML = '<input class="row-edit" id="editSchName_' + id + '" value="' + escapeHtml(nameVal) + '" maxlength="50">';
  row.querySelector(".sch-item").innerHTML = '<input class="row-edit" id="editSchItem_' + id + '" value="' + escapeHtml(itemVal) + '" maxlength="50">';
  row.querySelector(".sch-time").innerHTML = '<input class="row-edit" id="editSchTime_' + id + '" value="' + timeVal + '" maxlength="5" style="width:60px">';
  row.querySelector(".sch-code").innerHTML = '<input class="row-edit" id="editSchCode_' + id + '" value="' + escapeHtml(codeVal) + '" maxlength="20" style="width:80px">';
  var actions = row.querySelector(".sch-actions");
  actions.innerHTML = '<button class="btn btn-sm btn-primary" onclick="saveSchedule(' + id + ')">保存</button>'
    + '<button class="btn btn-sm" onclick="cancelSchedule()">取消</button>';
}

function saveSchedule(id) {
  var name = document.getElementById("editSchName_" + id).value.trim();
  var item = document.getElementById("editSchItem_" + id).value.trim();
  var time = document.getElementById("editSchTime_" + id).value.trim();
  var code = document.getElementById("editSchCode_" + id).value.trim();
  if (!name || !time) { showToast("名称和时间为必填", "error"); return; }
  if (!/^\\d{2}:\\d{2}$/.test(time)) { showToast("时间格式应为 HH:MM", "error"); return; }
  var xhr = new XMLHttpRequest();
  xhr.open("POST", "/monitor/schedules/edit/" + id);
  xhr.setRequestHeader("Content-Type", "application/x-www-form-urlencoded");
  xhr.onload = function() {
    if (xhr.status === 200) {
      document.getElementById("schedule-table-wrap").innerHTML = xhr.responseText;
      showToast("保存成功", "success");
    } else {
      showToast("保存失败：" + xhr.responseText, "error");
    }
  };
  xhr.send("name=" + encodeURIComponent(name) + "&item_name=" + encodeURIComponent(item) + "&cron_time=" + time + "&meeting_code=" + encodeURIComponent(code));
}

function cancelSchedule() {
  var xhr = new XMLHttpRequest();
  xhr.open("GET", "/monitor/partial/schedule-table");
  xhr.onload = function() {
    if (xhr.status === 200) {
      document.getElementById("schedule-table-wrap").innerHTML = xhr.responseText;
    }
  };
  xhr.send();
}

function toggleSchedule(id) {
  var xhr = new XMLHttpRequest();
  xhr.open("POST", "/monitor/schedules/toggle/" + id);
  xhr.setRequestHeader("Content-Type", "application/x-www-form-urlencoded");
  xhr.onload = function() {
    if (xhr.status === 200) {
      document.getElementById("schedule-table-wrap").innerHTML = xhr.responseText;
      showToast("状态已切换", "success");
    }
  };
  xhr.send();
}

function deleteSchedule(id) {
  if (!confirm("确定要删除该调度任务吗？")) return;
  var xhr = new XMLHttpRequest();
  xhr.open("POST", "/monitor/schedules/delete/" + id);
  xhr.setRequestHeader("Content-Type", "application/x-www-form-urlencoded");
  xhr.onload = function() {
    if (xhr.status === 200) {
      document.getElementById("schedule-table-wrap").innerHTML = xhr.responseText;
      showToast("删除成功", "success");
    } else {
      showToast("删除失败：" + xhr.responseText, "error");
    }
  };
  xhr.send();
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function showToast(msg, type) {
  var t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast " + type + " show";
  setTimeout(function() { t.className = "toast"; }, 3000);
}
</script>
"""

# ===== 辅助函数 =====

def _build_data_list(date_str: str, expanded_set=None) -> list:
    if expanded_set is None:
        expanded_set = set()
    data_list = monitor_db.list_data_for_date_readonly(date_str)
    for d in data_list:
        d["_expanded"] = d["item_id"] in expanded_set
    return data_list


# ===== 路由 =====

@bp.get("/")
def overview():
    """总览页：默认当天日期，展示所有事项的监控状态。"""
    date_str = request.args.get("date", "") or time.strftime("%Y-%m-%d")
    today = time.strftime("%Y-%m-%d")
    data_list = _build_data_list(date_str)
    items_html = render_template_string(
        OVERVIEW_ITEMS_FRAGMENT, date=date_str, data_list=data_list
    )
    content = render_template_string(
        OVERVIEW_CONTENT, date=date_str, today=today, items_html=items_html
    )
    return render_template_string(
        PAGE_FRAME,
        page_title="监控总览",
        page_subtitle="",
        active_page="overview",
        content=content,
    )


@bp.get("/partial/overview")
def partial_overview():
    """总览页局部刷新。"""
    date_str = request.args.get("date", "") or time.strftime("%Y-%m-%d")
    expanded_raw = request.args.get("expanded", "")
    expanded_set = set()
    if expanded_raw:
        for v in expanded_raw.split(","):
            try:
                expanded_set.add(int(v))
            except ValueError:
                pass
    data_list = _build_data_list(date_str, expanded_set)
    return render_template_string(
        OVERVIEW_ITEMS_FRAGMENT, date=date_str, data_list=data_list
    )


@bp.get("/config")
def config_page():
    """配置页：管理监控事项。"""
    items = monitor_db.list_items()
    table_html = render_template_string(CONFIG_TABLE_FRAGMENT, items=items)
    content = render_template_string(CONFIG_CONTENT, table_html=table_html)
    return render_template_string(
        PAGE_FRAME,
        page_title="事项配置",
        page_subtitle="添加、编辑、删除监控事项",
        active_page="config",
        content=content,
    )


@bp.get("/partial/config-table")
def partial_config_table():
    """配置表局部刷新。"""
    items = monitor_db.list_items()
    return render_template_string(CONFIG_TABLE_FRAGMENT, items=items)


@bp.post("/config/add")
def config_add():
    """添加事项。"""
    name = (request.form.get("name") or "").strip()
    if not name:
        return "名称不能为空", 400
    description = (request.form.get("description") or "").strip()
    monitor_db.add_item(name, description)
    return partial_config_table()


@bp.post("/config/edit/<int:item_id>")
def config_edit(item_id):
    """编辑事项。"""
    name = (request.form.get("name") or "").strip()
    if not name:
        return "名称不能为空", 400
    description = (request.form.get("description") or "").strip()
    result = monitor_db.update_item(item_id, name, description)
    if not result:
        return "事项不存在", 404
    return partial_config_table()


@bp.post("/config/delete/<int:item_id>")
def config_delete(item_id):
    """删除事项。"""
    ok = monitor_db.delete_item(item_id)
    if not ok:
        return "事项不存在", 404
    return partial_config_table()


# ===== 调度配置路由 =====

@bp.get("/schedules")
def schedules_page():
    """调度配置页面。"""
    items = monitor_db.list_items()
    tbl = render_template_string(
        SCHEDULE_TABLE_FRAGMENT,
        schedules=monitor_db.list_schedules(),
    )
    return render_template_string(
        PAGE_FRAME,
        page_title="调度配置",
        page_subtitle="",
        active_page="schedules",
        content=render_template_string(
            SCHEDULE_CONTENT,
            items=items,
            table_html=tbl,
        ),
    )


@bp.get("/partial/schedule-table")
def partial_schedule_table():
    """返回调度表片段（HTMX 局部刷新）。"""
    return render_template_string(
        SCHEDULE_TABLE_FRAGMENT,
        schedules=monitor_db.list_schedules(),
    )


# ===== 自定义良率监控 =====

CUSTOM_YIELD_CONTENT = """<div class="card">
  <h2 class="card-title">📈 自定义良率监控</h2>
  <div class="empty">
    <p style="font-size:16px;margin-bottom:8px;">🚧 功能开发中</p>
    <p>此页面将支持自定义机台、时间段和监控指标的良率监控任务创建与管理。</p>
    <p style="margin-top:12px;font-size:13px;">敬请期待...</p>
  </div>
</div>"""


@bp.get("/custom-yield")
def custom_yield_page():
    """自定义良率监控页面（骨架）。"""
    content = render_template_string(CUSTOM_YIELD_CONTENT)
    return render_template_string(
        PAGE_FRAME,
        page_title="自定义良率监控",
        page_subtitle="自定义机台与指标的良率监控任务",
        active_page="custom_yield",
        content=content,
    )


@bp.post("/schedules/add")
def schedules_add():
    """添加调度任务。"""
    name = request.form.get("name", "").strip()
    stype = request.form.get("type", "generate").strip()
    cron_time = request.form.get("cron_time", "").strip()
    meeting_code = request.form.get("meeting_code", "").strip()
    item_id_str = request.form.get("item_id", "").strip()

    if not name or not cron_time:
        return "名称和时间为必填", 400

    item_id = int(item_id_str) if item_id_str else None
    try:
        monitor_db.add_schedule(
            name=name,
            schedule_type=stype,
            item_id=item_id,
            cron_time=cron_time,
            meeting_code=meeting_code,
        )
    except Exception as e:
        return str(e), 400

    return partial_schedule_table()


@bp.post("/schedules/edit/<int:schedule_id>")
def schedules_edit(schedule_id):
    """编辑调度任务。"""
    name = request.form.get("name", "").strip()
    cron_time = request.form.get("cron_time", "").strip()
    meeting_code = request.form.get("meeting_code", "").strip()

    if not name or not cron_time:
        return "名称和时间为必填", 400

    monitor_db.update_schedule(schedule_id, name=name, cron_time=cron_time, meeting_code=meeting_code)
    return partial_schedule_table()


@bp.post("/schedules/delete/<int:schedule_id>")
def schedules_delete(schedule_id):
    """删除调度任务。"""
    ok = monitor_db.delete_schedule(schedule_id)
    if not ok:
        return "调度不存在", 404
    return partial_schedule_table()


@bp.post("/schedules/toggle/<int:schedule_id>")
def schedules_toggle(schedule_id):
    """切换调度启用/停用。"""
    monitor_db.toggle_schedule(schedule_id)
    return partial_schedule_table()


def _execute_single_generate(schedule_info: dict, item_id: int, date_str: str):
    """后台线程中执行单个事项的数据生成。
    从 on-demand "生成报告" 按钮触发，使用用户选择的日期而非调度器日期逻辑。
    """
    import importlib, importlib.util, os as _os, sys as _sys

    _m_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "monitor-platform")

    # 临时替换 sys.modules["database"] 指向 monitor-platform 的 database
    _orig_db = _sys.modules.get("database")
    _spec = importlib.util.spec_from_file_location("monitor_database_on_demand", _os.path.join(_m_dir, "database.py"))
    _db_module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_db_module)
    _sys.modules["database"] = _db_module

    try:
        # 动态导入 monitor_scheduler 中的具体生成函数
        _sched_spec = importlib.util.spec_from_file_location(
            "_monitor_scheduler_on_demand", _os.path.join(_m_dir, "monitor_scheduler.py"))
        _sched = importlib.util.module_from_spec(_sched_spec)
        _sched_spec.loader.exec_module(_sched)

        # 直接调度到具体生成函数，传入用户选择的日期（绕过 _resolve_company_date）
        _name = schedule_info.get("name", "")
        print(f"[on-demand] 执行生成: {_name} (item={item_id}, date={date_str})")

        # ---- DEMO 模式：生成纯演示报告，不拉取外部真实数据 ----
        if _os.environ.get("DEMO_MODE", "").strip() == "1":
            import monitor_demo_report
            time.sleep(3.0)  # 让前端"生成中"状态可见（演示用延时）
            _fname = monitor_demo_report.write_demo_report(
                item_id, _name, date_str, OUTPUT_DIR)
            _conn = monitor_db.get_connection()
            try:
                _conn.execute(
                    "UPDATE monitor_data SET status = 'success', html_path = ? "
                    "WHERE item_id = ? AND date = ?",
                    (_fname, item_id, date_str),
                )
                _conn.commit()
            finally:
                _conn.close()
            print(f"[DEMO] 已生成演示报告: {_name} -> {_fname}")
            return

        if item_id == 1:
            _sched._run_defect_map(date_str)
        elif item_id == 3:
            _sched._run_ecpm(date_str)
        elif item_id == 4:
            _sched._run_feedback(date_str)
        elif item_id == 5:
            _sched._run_yield_monitor(date_str)
        elif item_id == 6:
            _sched._run_hold_report(date_str)
        elif item_id == 7:
            _sched._run_rw_report(date_str)
        else:
            print(f"[on-demand] 未知事项类型: item_id={item_id}，跳过")
    finally:
        if _orig_db is not None:
            _sys.modules["database"] = _orig_db
        else:
            _sys.modules.pop("database", None)


@bp.post("/generate/<int:item_id>/<date>")
def generate(item_id, date):
    """触发单个事项的数据生成（不覆盖已有的 success 状态）。"""
    item = monitor_db.get_item(item_id)
    if not item:
        return jsonify({"error": "事项不存在"}), 404
    conn = monitor_db.get_connection()
    try:
        row = conn.execute(
            "SELECT id, status FROM monitor_data WHERE item_id = ? AND date = ?",
            (item_id, date),
        ).fetchone()
        if row:
            # 已有 success 不覆盖；failed/pending/generating 可重试
            if row["status"] == "success":
                conn.close()
                return jsonify({"ok": True, "skipped": True})
            conn.execute(
                "UPDATE monitor_data SET status = 'generating', html_path = NULL WHERE id = ?",
                (row["id"],),
            )
        else:
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO monitor_data (item_id, date, status, html_path, created_at) "
                "VALUES (?, ?, 'generating', NULL, ?)",
                (item_id, date, now),
            )
        conn.commit()
    finally:
        conn.close()

    # 在后台线程中触发实际生成（不阻塞 Web 响应）
    import threading as _threading
    schedule_info = {"item_id": item_id, "cron_time": time.strftime("%H:%M"), "name": item["name"],
                     "id": 0, "schedule_type": "generate", "enabled": True}
    _threading.Thread(
        target=_execute_single_generate, args=(schedule_info, item_id, date),
        daemon=True
    ).start()
    return jsonify({"ok": True})


@bp.post("/generate-all/<date>")
def generate_all(date):
    """触发全部事项的数据生成（已有 success 的不覆盖）。"""
    items = monitor_db.list_items()
    conn = monitor_db.get_connection()
    total = 0
    skipped = 0
    triggered = []
    try:
        for item in items:
            row = conn.execute(
                "SELECT id, status FROM monitor_data WHERE item_id = ? AND date = ?",
                (item["id"], date),
            ).fetchone()
            if row:
                if row["status"] == "success":
                    skipped += 1
                    continue
                conn.execute(
                    "UPDATE monitor_data SET status = 'generating', html_path = NULL WHERE id = ?",
                    (row["id"],),
                )
            else:
                now = time.strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "INSERT INTO monitor_data (item_id, date, status, html_path, created_at) "
                    "VALUES (?, ?, 'generating', NULL, ?)",
                    (item["id"], date, now),
                )
            total += 1
            triggered.append(item)
        conn.commit()
    finally:
        conn.close()

    # 后台线程逐个生成
    import threading as _threading
    for item in triggered:
        schedule_info = {"item_id": item["id"], "cron_time": time.strftime("%H:%M"),
                         "name": item["name"], "id": 0, "schedule_type": "generate", "enabled": True}
        _threading.Thread(
            target=_execute_single_generate, args=(schedule_info, item["id"], date),
            daemon=True
        ).start()
    return jsonify({"ok": True, "count": total, "skipped": skipped})


@bp.get("/output/<path:filename>")
def serve_output(filename):
    """提供 output 目录下的 HTML 报告。"""
    return send_from_directory(OUTPUT_DIR, filename)


@bp.get("/static/<path:filename>")
def serve_static(filename):
    """提供 static 目录下的静态文件（Chart.js 等）。"""
    return send_from_directory(STATIC_DIR, filename)