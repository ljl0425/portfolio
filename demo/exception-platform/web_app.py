"""Flask Web 服务：任务提交表单 + 任务列表/详情 + 配置管理页。

- 模板全部内联（render_template_string，内网无外网依赖）；
- 列表 5 秒 / 详情 3 秒局部刷新（/partial 路由返回 HTML 片段）；
- 校验全部在服务端白名单内完成，失败回填已填内容并内联报错；
- XSS 守则：用户数据只经 {{ }} 转义或 DOM 属性赋值输出，绝不拼进 HTML 字符串。
"""

import ctypes
import io
import json
import math
import os
import re
import shutil
import sys
import time

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_file,
    session,
    url_for,
)

import database
import report_engine
import cd_engine
import process_engine
import defect_engine
import monitor_routes

# 导入 THK 监控平台
import thk.routes

# 导入自动复机监控仪表盘
_AUTO_RECOVERY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "auto_recovery")
if _AUTO_RECOVERY_DIR not in sys.path:
    sys.path.insert(0, _AUTO_RECOVERY_DIR)
try:
    import monitor_ui  # 自动复机看板（可选模块）
except ImportError:  # 开源版未包含该模块时跳过，不影响主服务
    monitor_ui = None


# ===== 报告引擎分发 =====
def _has_valid_workflow(task):
    """判断该任务是否有对应的工作流引擎（非兜底）。
    制程参数 → process_engine
    defect异常 → defect_engine
    量测异常 CD → cd_engine
    量测异常 OVL → report_engine（OVL 专用）
    量测异常 HT/MAC/TP → 无对应工作流
    """
    exception_type = task.get("exception_type", "")
    measure_dim = task.get("measure_dim", "")
    if exception_type == "制程参数":
        return True
    if exception_type == "defect异常":
        return True
    if exception_type == "量测异常" and measure_dim in ("CD", "OVL"):
        return True
    return False


def _get_engine(task):
    """根据任务类型返回对应的报告引擎模块。
    - 制程参数：process_engine
    - Defect 异常：defect_engine
    - CD（量测异常）：cd_engine
    - OVL（默认）：report_engine
    """
    exception_type = task.get("exception_type", "")
    measure_dim = task.get("measure_dim", "")
    if exception_type == "制程参数":
        return process_engine
    if exception_type == "defect异常":
        return defect_engine
    if exception_type == "量测异常" and measure_dim == "CD":
        return cd_engine
    return report_engine


def _disable_console_quick_edit():
    """禁用 Windows 控制台"快速编辑模式"，防止误点鼠标导致进程假死。

    在快速编辑模式下，控制台会阻塞所有 stdout 写入，导致 Flask 请求线程全部卡住。
    此函数在程序启动时调用，通过 Windows API 关闭该模式。
    """
    if sys.platform != "win32":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        STD_INPUT_HANDLE = -10
        ENABLE_EXTENDED_FLAGS = 0x0080
        ENABLE_QUICK_EDIT_MODE = 0x0040
        handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        mode = ctypes.c_uint32()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        new_mode = mode.value & ~ENABLE_QUICK_EDIT_MODE
        new_mode |= ENABLE_EXTENDED_FLAGS
        kernel32.SetConsoleMode(handle, new_mode)
        print("已禁用控制台快速编辑模式（防止误触导致进程假死）")
    except Exception:
        pass  # 非控制台环境下忽略


# ============================================================
# 环境标识 —— 基于文件校验，防止漏设环境变量导致危险操作
# ============================================================
IS_STAGING = False
_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ENVIRONMENT.txt")
try:
    with open(_ENV_FILE, "r", encoding="utf-8") as _f:
        _ENV_FILE_CONTENT = _f.read().strip()
except FileNotFoundError:
    _ENV_FILE_CONTENT = ""

_ENV_VAR = os.environ.get("ENVIRONMENT", "").strip()

# 环境变量和文件必须一致，任一缺失/未知/不一致时按 staging 处理（默认安全）
if _ENV_VAR == "production" and _ENV_FILE_CONTENT == "production":
    IS_STAGING = False
elif _ENV_VAR == "staging" and _ENV_FILE_CONTENT == "staging":
    IS_STAGING = True
else:
    # 缺失、未知或不一致 → 默认拒绝危险操作
    IS_STAGING = True
    if not _ENV_FILE_CONTENT:
        print("[WARNING] ENVIRONMENT.txt 缺失或为空，默认按 staging 处理（危险操作被拦截）")
    elif not _ENV_VAR:
        print("[WARNING] ENVIRONMENT 环境变量未设置，默认按 staging 处理（危险操作被拦截）")
    else:
        print(f"[WARNING] 环境不一致：ENVIRONMENT={_ENV_VAR}, ENVIRONMENT.txt={_ENV_FILE_CONTENT}，默认按 staging 处理")


def _staging_blocked(op_name="此操作"):
    """staging 环境下拒绝危险操作。"""
    return jsonify({
        "ok": False,
        "error": f"[STAGING] {op_name} 在测试环境中不可用",
        "staging": True,
    })


def _require_production():
    """非正式环境抛出异常，用于外部调用边界（非 HTTP 路由路径）。"""
    if IS_STAGING:
        raise RuntimeError("[STAGING] 此操作在测试环境中被拦截")


# 向后兼容：保留 DEV_MODE 供路由层的 IF 使用
DEV_MODE = IS_STAGING


app = Flask(__name__)
# JSON 响应中文不转义（\uXXXX），前端提示与测试断言直接可读
app.json.ensure_ascii = False

# ===== 安全基础：SECRET_KEY（从环境变量读取） =====
_FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "").strip()
if not _FLASK_SECRET_KEY:
    if not IS_STAGING:
        print("[FATAL] FLASK_SECRET_KEY 环境变量未设置，正式环境拒绝启动")
        sys.exit(1)
    # staging 使用独立测试密钥
    _FLASK_SECRET_KEY = "staging-test-key-20261014"
    print("[WARNING] FLASK_SECRET_KEY 未设置，使用 staging 测试密钥")
app.secret_key = _FLASK_SECRET_KEY

# ===== 会话配置 =====
app.config["PERMANENT_SESSION_LIFETIME"] = __import__("datetime").timedelta(hours=8)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# 局域网 HTTP 不使用 SESSION_COOKIE_SECURE

# ===== 操作员密码（从环境变量读取） =====
_OPERATOR_PASSWORD = os.environ.get("OPERATOR_PASSWORD", "").strip()
if not _OPERATOR_PASSWORD:
    if not IS_STAGING:
        print("[FATAL] OPERATOR_PASSWORD 环境变量未设置，正式环境拒绝启动")
        sys.exit(1)
    _OPERATOR_PASSWORD = "staging-test-password-20261014"
    print("[WARNING] OPERATOR_PASSWORD 未设置，使用 staging 测试密码")

# ===== 密码校验（hashlib + hmac，零外部依赖） =====
import hashlib
import hmac as _hmac_module
import secrets

_OPERATOR_PASSWORD_HASH = hashlib.sha256(_OPERATOR_PASSWORD.encode("utf-8")).digest()


def _check_password(plain: str) -> bool:
    """常量时间比较密码哈希，防止时序攻击。"""
    submitted = hashlib.sha256(plain.encode("utf-8")).digest()
    return _hmac_module.compare_digest(_OPERATOR_PASSWORD_HASH, submitted)


# ===== 登录限速（内存，重启清零） =====
_rate_limit_store = {}  # ip -> {"failures": int, "last_fail": float}


def _rate_limit_check(ip: str) -> tuple[bool, str]:
    """检查 IP 是否超出限速。返回 (ok, reason)。"""
    import time as _time
    now = _time.time()
    entry = _rate_limit_store.get(ip)
    if entry is None:
        return True, ""
    # 超过 1 分钟窗口，重置
    if now - entry["last_fail"] > 60:
        _rate_limit_store.pop(ip, None)
        return True, ""
    if entry["failures"] >= 5:
        return False, "尝试次数过多，请稍后再试"
    if entry["failures"] >= 3:
        # 渐进延迟
        _time.sleep(2)
    return True, ""


# ===== 只读 GET 判断 =====
_READONLY_GET_WHITELIST = {
    # 前缀匹配：以这些路径开头的 GET 为只读
    "/api/report/",
    "/partial/",
    "/api/tasks",
    "/api/config/",
    "/api/yield-monitor/",
    "/api/hold/query",  # 副作用 GET，需要认证
    "/api/report/",  # excel 下载含副作用，但路径前缀匹配时以列表顺序为准
}
# 注：/api/hold/query 和 /api/report/*/excel 虽然为 GET 但有副作用，需排除
_EXEMPT_PATHS = {"/login", "/api/fastgpt/webhook"}

# CSRF 豁免路径：仍需认证，但免 CSRF 检查（从静态 HTML 页面发起的请求无 csrf_token）
_CSRF_EXEMPT_PATHS = {"/api/yield-monitor/start", "/api/yield-monitor/status"}

_SIDEEFFECT_GET_PATTERNS = [
    # (prefix, suffix) — GET 路由匹配此模式时需要认证
    ("/api/report/", "/excel"),       # Excel 下载有副作用
    ("/api/hold/query", ""),          # Hold 查询调用外部 API
]


def _is_exempt(path: str) -> bool:
    """某些路径完全跳过 before_request 处理。"""
    return path in _EXEMPT_PATHS


def _is_sideeffect_get(path: str, method: str) -> bool:
    """判断是否为有副作用的 GET 请求（需要认证）。"""
    if method != "GET":
        return False
    for prefix, suffix in _SIDEEFFECT_GET_PATTERNS:
        if path.startswith(prefix) and (not suffix or path.endswith(suffix)):
            return True
    return False


# ===== 全局认证守卫（覆盖所有 Blueprint 路由） =====
@app.before_request
def _auth_guard():
    """对所有请求进行认证与 CSRF 检查。

    - /login、/logout、/api/fastgpt/webhook 由各自路由自行处理
    - 只读 GET 放行
    - 其余所有请求（POST + 带副作用的 GET）需要 session + CSRF
    """
    path = request.path
    method = request.method

    # 豁免路径：登录/登出/webhook 由各自路由自行处理
    if _is_exempt(path):
        return None

    # 只读 GET（不含副作用 GET）直接放行
    if method == "GET" and not _is_sideeffect_get(path, method):
        return None

    # 所有写操作 + 副作用 GET：先检查身份
    if not session.get("user"):
        return jsonify({"ok": False, "error": "请先登录"}), 401

    # CSRF token 检查（POST/DELETE/PUT/PATCH 等写操作 + 副作用 GET）
    # 静态 HTML 报告（如 ECPM 报告）中的 fetch 请求无法携带 CSRF token，走豁免
    if path not in _CSRF_EXEMPT_PATHS and method in ("GET", "POST", "DELETE", "PUT", "PATCH"):
        csrf_header = request.headers.get("X-CSRF-Token", "")
        csrf_form = (request.form.get("csrf_token") or "" if request.form else "")
        csrf_json = ""
        if request.is_json:
            try:
                csrf_json = (request.get_json(silent=True) or {}).get("csrf_token", "")
            except Exception:
                pass
        submitted = csrf_header or csrf_form or csrf_json
        expected = session.get("csrf_token", "")
        if not submitted or not _hmac_module.compare_digest(submitted, expected):
            return jsonify({"ok": False, "error": "CSRF 校验失败，请刷新页面重试"}), 403

    return None


app.register_blueprint(monitor_routes.bp)
if monitor_ui is not None:  # 开源版未包含自动复机看板模块
    app.register_blueprint(monitor_ui.bp)
app.register_blueprint(thk.routes.bp)


@app.context_processor
def inject_auth_context():
    """向所有模板注入 csrf_token 和登录状态。"""
    return {
        "csrf_token": session.get("csrf_token", ""),
        "user": session.get("user"),
    }


EXCEPTION_TYPES = ["制程参数", "量测异常", "defect异常"]
STATUS_ORDER = ["pending", "running", "success", "failed"]
STATUS_LABELS = {
    "pending": "待处理",
    "running": "处理中",
    "success": "成功",
    "failed": "失败",
}
# 配置页 tab：kind -> 显示名（顺序即 tab 顺序）
CONFIG_TAB_INFO = [
    ("pfcd", "PFCD"),
    ("process_param", "制程参数"),
    ("defect_code", "defect code"),
]

# ---------- 深色科技风样式（各页共用） ----------

_PAGE_STYLE = """
:root {
  --bg: #1e1e2e; --card: #2a2a3c; --border: #3a3a4f;
  --text: #e2e2ee; --text-2: #9a9ab0;
  --primary: #4fd1c5; --primary-dark: #3ab8ad;
  --control-h: 37px;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
       background: var(--bg); color: var(--text); }
a { color: var(--primary); text-decoration: none; }
a:hover { text-decoration: underline; }
.hidden { display: none !important; }
.container { max-width: 1200px; margin: 0 auto; padding: 0 20px 40px; }
.page-header { background: #232334; border-bottom: 1px solid var(--border);
               padding: 16px 0; margin-bottom: 20px; }
.page-header .container { padding-bottom: 0; display: flex; align-items: baseline;
                          gap: 16px; flex-wrap: wrap; }
.page-header h1 { font-size: 20px; margin: 0; color: var(--primary); }
.sub { color: var(--text-2); font-size: 13px; }
.card { background: var(--card); border: 1px solid var(--border);
        border-radius: 8px; padding: 18px; margin-bottom: 16px; }
.card-title { margin: 0 0 14px; font-size: 16px; padding-left: 10px;
              border-left: 4px solid var(--primary); }
.card-title .sub { margin-left: 10px; }

input, select, textarea, button { font-family: inherit; }
.field input, .field select, .field .ss-box,
.filter-bar input, .filter-bar select,
.cfg-toolbar input, .cfg-toolbar textarea,
.row-edit {
  background: #1e1e2e; border: 1px solid var(--border); color: var(--text);
  border-radius: 6px; padding: 7px 9px; font-size: 14px; width: 100%;
}
.field input:focus, .field select:focus,
.filter-bar input:focus, .filter-bar select:focus,
.cfg-toolbar input:focus, .cfg-toolbar textarea:focus,
.row-edit:focus { outline: none; border-color: var(--primary); }

/* 统一单行控件高度：文本/日期输入框默认比 select 矮 2px，这里对齐到同一高度 */
.field input:not([type="checkbox"]):not([type="radio"]):not([type="hidden"]):not(.ms-search),
.field select,
.filter-bar input:not([type="checkbox"]):not([type="hidden"]),
.filter-bar select,
.cfg-toolbar input:not([type="checkbox"]):not([type="hidden"]),
.row-edit { height: var(--control-h); }

.form-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px 16px; }
.field { display: flex; flex-direction: column; min-width: 0; }
.field label { font-size: 13px; color: var(--text-2); margin-bottom: 4px; }
.field.invalid input, .field.invalid select, .field.invalid .ms-box,
.field.invalid .ss-box {
  border-color: #f87171;
}
.field-error { color: #f87171; font-size: 12px; margin: 4px 0 0; }
.form-branch { border: 1px solid var(--border); border-radius: 8px;
               padding: 14px; margin-top: 12px; background: #262639; }
.branch-title { margin: 0 0 10px; font-size: 13px; color: var(--primary); font-weight: bold; }
.form-actions { display: flex; justify-content: flex-end; align-items: flex-end;
                gap: 12px; margin-top: 14px; }
.form-actions .field { width: 240px; }
.form-summary { background: rgba(248,113,113,.12); border: 1px solid rgba(248,113,113,.4);
                color: #f87171; border-radius: 6px; padding: 10px 14px;
                margin-bottom: 12px; font-size: 13px; }

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
.btn:disabled:hover { border-color: var(--border); color: var(--text); background: transparent; }
.btn-primary:disabled:hover { border-color: var(--primary); color: #0f2e2b; background: var(--primary); }

.multi-select { position: relative; }
.ms-box { display: flex; flex-wrap: wrap; align-items: center; gap: 4px;
          background: #1e1e2e; border: 1px solid var(--border); border-radius: 6px;
          padding: 7px 9px; cursor: pointer; min-height: var(--control-h); }
.ms-box:focus-within { border-color: var(--primary); }
.ms-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.ms-chip { background: rgba(79,209,197,.15); border: 1px solid rgba(79,209,197,.4);
           color: var(--primary); border-radius: 4px; padding: 1px 6px;
           font-size: 12px; display: inline-flex; align-items: center; gap: 4px; }
.ms-chip-x { cursor: pointer; font-weight: bold; }
.multi-select .ms-search { flex: 1 1 60px; width: auto; min-width: 60px;
             background: transparent; border: none;
             color: var(--text); font-size: 14px; padding: 0; line-height: normal; }
.multi-select .ms-search:focus { outline: none; }
.ms-dropdown { position: absolute; top: calc(100% + 4px); left: 0; right: 0; z-index: 20;
               background: #161b22; border: 1px solid var(--border); border-radius: 6px;
               max-height: 220px; overflow-y: auto; padding: 4px; }
.ms-option { display: flex; align-items: center; gap: 6px; padding: 5px 8px;
             border-radius: 4px; font-size: 13px; cursor: pointer; }
.ms-option:hover { background: #30304a; }
.ms-option input { accent-color: var(--primary); }
.ms-empty { padding: 10px; color: var(--text-2); font-size: 13px; text-align: center; }

/* 可搜索单选下拉：点击展开全部选项，输入关键字过滤 */
.search-select { position: relative; }
.field .search-select { width: 100%; }
.ss-box { display: flex; align-items: center; gap: 4px; width: 100%;
          background: #1e1e2e; border: 1px solid var(--border); border-radius: 6px;
          padding: 7px 9px; cursor: pointer; min-height: var(--control-h); }
.ss-box:focus-within { border-color: var(--primary); }
.ss-value { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
            font-size: 14px; }
.ss-value.ss-ph { color: var(--text-2); }
.ss-clear { cursor: pointer; color: var(--text-2); font-weight: bold; padding: 0 4px; }
.ss-clear:hover { color: #f87171; }
.ss-dropdown { position: absolute; top: calc(100% + 4px); left: 0; right: 0; z-index: 20;
               background: #161b22; border: 1px solid var(--border); border-radius: 6px;
               max-height: 260px; overflow-y: auto; padding: 4px; }
.ss-dropdown .ss-search { background: transparent; border: 1px solid var(--border);
                          color: var(--text); font-size: 13px; padding: 6px 8px;
                          border-radius: 4px; width: 100%; }
.ss-dropdown .ss-search:focus { outline: none; border-color: var(--primary); }
.ss-option { padding: 6px 8px; border-radius: 4px; font-size: 13px; cursor: pointer; }
.ss-option:hover { background: #30304a; }
.ss-option.selected { color: var(--primary); background: rgba(79,209,197,.12); }
.f-item .search-select { width: 200px; }

.filter-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end; margin-bottom: 12px; }
.f-item { display: flex; flex-direction: column; gap: 3px; }
.f-item label { font-size: 12px; color: var(--text-2); }
.f-item input, .f-item select { width: 150px; }
.f-item.wide input { width: 220px; }
.table-wrap { overflow-x: auto; }
.task-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.task-table th { background: #232334; color: var(--text-2); text-align: left;
                 padding: 8px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
.task-table td { padding: 8px 10px; border-bottom: 1px solid var(--border); }
.task-table tr:hover td { background: #30304a; }
.task-row { cursor: pointer; }
.empty { text-align: center; color: var(--text-2); padding: 36px 0; }
.hint { color: var(--text-2); font-size: 12px; margin-top: 10px; }

.badge { display: inline-block; padding: 2px 10px; border-radius: 4px; font-size: 12px; border: 1px solid; }
.badge-pending { color: #fbbf24; background: rgba(251,191,36,.15); border-color: rgba(251,191,36,.4); }
.badge-running { color: #60a5fa; background: rgba(96,165,250,.15); border-color: rgba(96,165,250,.4); }
.badge-success { color: #34d399; background: rgba(52,211,153,.15); border-color: rgba(52,211,153,.4); }
.badge-failed  { color: #f87171; background: rgba(248,113,113,.15); border-color: rgba(248,113,113,.4); }

/* 事件状态 open/close */
.row-open td { background: rgba(255,255,200,.08); }
.event-status-badge { display: inline-block; padding: 2px 10px; border-radius: 4px; font-size: 12px; border: 1px solid; cursor: pointer; user-select: none; }
.event-status-badge:hover { opacity: 0.8; }
.badge-open  { color: #fbbf24; background: rgba(251,191,36,.2); border-color: rgba(251,191,36,.5); }
.badge-close { color: #34d399; background: rgba(52,211,153,.15); border-color: rgba(52,211,153,.4); }

.info-grid { display: grid; grid-template-columns: 120px 1fr 120px 1fr; gap: 8px 16px;
             font-size: 14px; margin: 0; }
.info-grid dt { color: var(--text-2); font-size: 12px; }
.info-grid dd { margin: 0; word-break: break-all; }
.log { background: #1e1e2e; border: 1px solid var(--border); border-radius: 6px;
       padding: 10px; font-family: Consolas, "Courier New", monospace; font-size: 12px;
       max-height: 320px; overflow: auto; margin: 0; white-space: pre-wrap;
       word-break: break-all; }
.log.error { color: #f87171; }
.detail-actions { display: flex; gap: 10px; margin-top: 16px; }

.tab-header { display: flex; gap: 8px; border-bottom: 1px solid var(--border); margin-bottom: 14px; }
.tab-btn { background: transparent; border: none; color: var(--text-2); padding: 8px 16px;
           font-size: 14px; cursor: pointer; border-bottom: 2px solid transparent; }
.tab-btn:hover { color: var(--text); }
.tab-btn.active { color: var(--primary); border-bottom-color: var(--primary); }
.cfg-toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
.cfg-toolbar .cfg-add-input { width: 220px; }
details.import-box { margin-left: 4px; }
details.import-box summary { cursor: pointer; color: var(--text-2); font-size: 13px; }
details.import-box textarea { width: 100%; min-height: 120px; margin: 8px 0; }
.cfg-import-result { color: var(--text-2); font-size: 12px; margin-left: 8px; }
.cfg-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.cfg-table th, .cfg-table td { border-bottom: 1px solid var(--border); padding: 8px 10px;
                               text-align: left; }
.cfg-table th { background: #232334; color: var(--text-2); }
.cfg-table tr:hover td { background: #30304a; }
.cfg-table .row-edit { width: 240px; margin-right: 8px; }
.cfg-count { color: var(--text-2); font-size: 12px; margin: 8px 0 0; }

/* 生成弹窗 */
.toast-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 9999;
                 display: flex; align-items: center; justify-content: center; }
.toast-box { background: var(--bg-card); border-radius: 12px; padding: 32px 40px;
             text-align: center; box-shadow: 0 8px 32px rgba(0,0,0,.4); min-width: 320px; }
.toast-spinner { width: 48px; height: 48px; border: 4px solid var(--border);
                 border-top-color: var(--primary); border-radius: 50%;
                 animation: spin .8s linear infinite; margin: 0 auto 16px; }
@keyframes spin { to { transform: rotate(360deg); } }
.toast-title { font-size: 16px; color: var(--text); margin-bottom: 8px; }
.toast-desc { font-size: 13px; color: var(--text-2); }
.toast-done { color: #4ade80; font-size: 48px; margin-bottom: 16px; }
.toast-error { color: #f87171; font-size: 48px; margin-bottom: 16px; }
.toast-btn { margin-top: 16px; }
"""

# ---------- 模板片段 ----------

# 任务列表片段（列表页初始渲染 + /partial/tasks 轮询共用）
LIST_FRAGMENT = """
{% if not tasks %}
<div class="empty">暂无符合条件的任务</div>
{% else %}
<table class="task-table">
  <tr>
    <th>编号</th><th>Site</th><th>异常事件说明</th><th>事件状态</th>
    <th>机台</th><th>主制程</th><th>PFCD</th>
    <th>异常类型</th><th>提交时间</th><th>操作</th>
  </tr>
  {% for t in tasks %}
  <tr class="task-row{% if t.event_status == 'open' %} row-open{% endif %}" data-id="{{ t.id }}">
    <td><a href="/tasks/{{ t.id }}">#{{ t.id }}</a></td>
    <td>{{ t.site }}</td>
    <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="{{ t.event_description or '' }}">{{ t.event_description or '-' }}</td>
    <td>
      <span class="event-status-badge badge-{{ t.event_status }}" data-action="toggle-status" data-id="{{ t.id }}" data-status="{{ t.event_status }}" title="点击切换状态">
        {{ 'Open' if t.event_status == 'open' else 'Close' }}
      </span>
    </td>
    <td>{{ t.machine }}</td>
    <td>{{ t.main_process }}</td>
    <td>{{ t.pfcd }}</td>
    <td>{{ t.exception_type }}</td>
    <td>{{ t.created_at }}</td>
    <td style="white-space: nowrap">
      <button class="btn btn-sm btn-primary" data-action="report" data-id="{{ t.id }}">报告</button>
      <button class="btn btn-sm" data-action="regen-report" data-id="{{ t.id }}">重新生成</button>
      <button class="btn btn-sm btn-danger" data-action="delete" data-id="{{ t.id }}"
              {% if t.status == 'running' %}disabled title="处理中禁止删除"{% endif %}>删除</button>
      <button class="btn btn-sm" data-action="retry" data-id="{{ t.id }}"
              {% if t.status != 'failed' %}disabled title="仅失败任务可重新入队"{% endif %}>重试</button>
    </td>
  </tr>
  {% endfor %}
</table>
{% endif %}
"""

# 详情片段（详情页初始渲染 + /partial/tasks/<id> 轮询共用；data-status 供前端停轮询）
DETAIL_FRAGMENT = """
<div data-status="{{ task.status }}">
  <div class="card">
    <h2 class="card-title">基本信息
      <span class="badge badge-{{ task.status }}">{{ status_labels[task.status] }}</span>
      <span class="sub">每 3 秒自动刷新</span>
    </h2>
    <dl class="info-grid">
      <dt>Site</dt><dd>{{ task.site }}</dd>
      <dt>机台</dt><dd>{{ task.machine }}</dd>
      <dt>主制程</dt><dd>{{ task.main_process }}</dd>
      <dt>PFCD</dt><dd>{{ task.pfcd }}</dd>
      <dt>起始时间</dt><dd>{{ task.start_time }}</dd>
      <dt>结束时间</dt><dd>{{ task.end_time }}</dd>
      <dt>异常类型</dt><dd>{{ task.exception_type }}</dd>
      <dt>异常事件说明</dt><dd>{{ task.event_description or '—' }}</dd>
      <dt>事件状态</dt><dd><span class="event-status-badge badge-{{ task.event_status }}">{{ 'Open' if task.event_status == 'open' else 'Close' }}</span></dd>
      {% if task.process_params %}
      <dt>制程参数名</dt><dd>{{ task.process_params }}</dd>
      {% endif %}
      {% if task.judge_param %}
      <dt>判断参数名</dt><dd>{{ task.judge_param }}</dd>
      {% endif %}
      {% if task.judge_rule %}
      <dt>异常判断规则</dt><dd>{{ task.judge_rule }}</dd>
      {% endif %}
      {% if task.threshold1 %}
      <dt>阈值1</dt><dd>{{ task.threshold1 }}</dd>
      {% endif %}
      {% if task.threshold2 %}
      <dt>阈值2</dt><dd>{{ task.threshold2 }}</dd>
      {% endif %}
      {% if task.measure_dim %}
      <dt>量测维度</dt><dd>{{ task.measure_dim }}</dd>
      {% endif %}
      {% if task.defect_code %}
      <dt>defect code</dt><dd>{{ task.defect_code }}</dd>
      {% endif %}
      {% if task.defect_size %}
      <dt>defect size</dt><dd>{{ task.defect_size }}</dd>
      {% endif %}
      <dt>提交时间</dt><dd>{{ task.created_at }}</dd>
      <dt>开始处理</dt><dd>{{ task.started_at or '—' }}</dd>
      <dt>完成时间</dt><dd>{{ task.finished_at or '—' }}</dd>
    </dl>
  </div>
  {% if task.status == 'failed' %}
  <div class="card">
    <h2 class="card-title">失败原因</h2>
    <pre class="log error">{{ task.error }}</pre>
  </div>
  {% endif %}
  {% if task.status == 'success' %}
  <div class="card">
    <h2 class="card-title">处理结果（result.json）</h2>
    <pre class="log">{{ result_pretty }}</pre>
  </div>
  {% endif %}
  <div class="card">
    <h2 class="card-title">提交数据（input.json）</h2>
    <pre class="log">{{ input_text or '（文件不存在）' }}</pre>
  </div>
  <div class="card">
    <h2 class="card-title">运行日志（run.log）</h2>
    <pre class="log">{{ run_log_text or '（暂无日志）' }}</pre>
  </div>
  <div class="detail-actions">
    <button class="btn btn-danger" data-action="delete" data-id="{{ task.id }}"
            {% if task.status == 'running' %}disabled title="处理中禁止删除"{% endif %}>删除任务</button>
    <button class="btn btn-primary" data-action="retry" data-id="{{ task.id }}"
            {% if task.status != 'failed' %}disabled title="仅失败任务可重新入队"{% endif %}>重新入队处理</button>
  </div>
</div>
"""

# 配置表 tbody 片段（配置页初始渲染 + 操作后局部刷新共用）
CONFIG_FRAGMENT = """
<tbody class="cfg-tbody">
{% if not rows %}
<tr><td colspan="4" class="empty">暂无配置，可通过「添加」或「批量粘贴导入」录入</td></tr>
{% else %}
{% for r in rows %}
<tr data-id="{{ r.id }}">
  <td>{{ r.id }}</td>
  <td class="cfg-value"><span class="cfg-value-text">{{ r.value }}</span></td>
  <td>{{ r.created_at }}</td>
  <td style="white-space: nowrap">
    <button class="btn btn-sm cfg-edit">编辑</button>
    <button class="btn btn-sm btn-danger cfg-del">删除</button>
  </td>
</tr>
{% endfor %}
{% endif %}
</tbody>
"""

# ---------- 整页模板 ----------

INDEX_PAGE = (
    """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>异常处理平台</title>
{% if user %}<meta name="csrf-token" content="{{ csrf_token }}">{% endif %}
<style>"""
    + _PAGE_STYLE
    + """</style>
</head>
<body>
<header class="page-header">
  <div class="container">
    <h1>异常处理平台</h1>
    <span class="sub">任务列表每 5 秒自动刷新 · <a href="/config">配置管理</a> · <a href="/monitor">日常监控</a> · <a href="/monitor/thk">THK监控</a>
    {% if user %} · <span style="color:#81c784">已登录</span> · <a href="javascript:void(0)" onclick="fetch('/logout',{method:'POST'}).then(()=>location.reload())">登出</a>{% else %} · <a href="/login">登录</a>{% endif %}</span>
  </div>
</header>
<main class="container">
  {% if errors %}
  <div class="form-summary">提交失败：{{ errors|length }} 处填写有误，请修正后重试。</div>
  {% endif %}
  <section class="card">
    <h2 class="card-title">提交异常</h2>
    <form id="create-form" action="/create" method="post" autocomplete="off">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <div class="form-grid">
        <div class="field {{ 'invalid' if errors.site }}">
          <label for="site">Site *</label>
          <select id="site" name="site" required>
            <option value="">请选择…</option>
            {% for s in machines_by_site %}
            <option value="{{ s }}" {{ 'selected' if form.get('site') == s }}>{{ s }}</option>
            {% endfor %}
          </select>
          {% if errors.site %}<p class="field-error">{{ errors.site }}</p>{% endif %}
        </div>
        <div class="field {{ 'invalid' if errors.machine }}">
          <label>机台 *</label>
          <div class="search-select" id="machine-select">
            <div class="ss-box" tabindex="0">
              <span class="ss-value ss-ph">请选择…</span>
              <span class="ss-clear hidden">×</span>
            </div>
            <div class="ss-dropdown hidden">
              <input class="ss-search" placeholder="输入搜索…">
              <div class="ss-options"></div>
              <div class="ms-empty hidden">无匹配选项</div>
            </div>
            <input type="hidden" name="machine" value="{{ form.get('machine', '') }}">
          </div>
          {% if errors.machine %}<p class="field-error">{{ errors.machine }}</p>{% endif %}
        </div>
        <div class="field {{ 'invalid' if errors.main_process }}">
          <label>主制程 *</label>
          <div class="multi-select">
            <div class="ms-box" tabindex="0">
              <span class="ms-chips"></span>
              <input class="ms-search" placeholder="输入搜索…">
            </div>
            <div class="ms-dropdown hidden">
              {% for v in main_processes %}
              <label class="ms-option"><input type="checkbox" value="{{ v }}"><span>{{ v }}</span></label>
              {% endfor %}
              <div class="ms-empty hidden">无匹配选项</div>
            </div>
            <input type="hidden" name="main_process" value="{{ form.get('main_process', '[]') }}">
          </div>
          {% if errors.main_process %}<p class="field-error">{{ errors.main_process }}</p>{% endif %}
        </div>
        <div class="field {{ 'invalid' if errors.pfcd }}">
          <label>PFCD *</label>
          <div class="multi-select">
            <div class="ms-box" tabindex="0">
              <span class="ms-chips"></span>
              <input class="ms-search" placeholder="输入搜索…">
            </div>
            <div class="ms-dropdown hidden">
              {% for v in pfcd_values %}
              <label class="ms-option"><input type="checkbox" value="{{ v }}"><span>{{ v }}</span></label>
              {% endfor %}
              <div class="ms-empty hidden">无匹配选项</div>
            </div>
            <input type="hidden" name="pfcd" value="{{ form.get('pfcd', '[]') }}">
          </div>
          {% if errors.pfcd %}<p class="field-error">{{ errors.pfcd }}</p>{% endif %}
        </div>
        <div class="field {{ 'invalid' if errors.start_time }}">
          <label for="start_time">起始时间 *</label>
          <input id="start_time" type="datetime-local" name="start_time"
                 value="{{ form.get('start_time', '') }}">
          {% if errors.start_time %}<p class="field-error">{{ errors.start_time }}</p>{% endif %}
        </div>
        <div class="field {{ 'invalid' if errors.end_time }}">
          <label for="end_time">结束时间 *</label>
          <input id="end_time" type="datetime-local" name="end_time"
                 value="{{ form.get('end_time', '') }}">
          {% if errors.end_time %}<p class="field-error">{{ errors.end_time }}</p>{% endif %}
        </div>
        <div class="field {{ 'invalid' if errors.exception_type }}">
          <label for="exception_type">异常类型 *</label>
          <select id="exception_type" name="exception_type" required>
            <option value="">请选择…</option>
            {% for t in exception_types %}
            <option value="{{ t }}" {{ 'selected' if form.get('exception_type') == t }}>{{ t }}</option>
            {% endfor %}
          </select>
          {% if errors.exception_type %}<p class="field-error">{{ errors.exception_type }}</p>{% endif %}
        </div>
      </div>

      <div class="field" style="margin-top:12px;">
        <label for="event_description">异常事件说明</label>
        <textarea id="event_description" name="event_description" placeholder="请描述本次异常事件的大致情况…" rows="2" style="width:100%;background:#1e1e2e;border:1px solid var(--border);color:var(--text);border-radius:6px;padding:7px 9px;font-size:14px;resize:vertical;">{{ form.get('event_description', '') }}</textarea>
      </div>

      <div class="form-branch hidden" id="branch-process">
        <p class="branch-title">制程参数分支</p>
        <div class="form-grid">
          <div class="field {{ 'invalid' if errors.process_params }}">
            <label>制程参数名 *</label>
            <div class="multi-select">
              <div class="ms-box" tabindex="0">
                <span class="ms-chips"></span>
                <input class="ms-search" placeholder="输入搜索…">
              </div>
              <div class="ms-dropdown hidden">
                {% for v in param_values %}
                <label class="ms-option"><input type="checkbox" value="{{ v }}"><span>{{ v }}</span></label>
                {% endfor %}
                <div class="ms-empty hidden">无匹配选项</div>
              </div>
              <input type="hidden" name="process_params" value="{{ form.get('process_params', '[]') }}">
            </div>
            {% if errors.process_params %}<p class="field-error">{{ errors.process_params }}</p>{% endif %}
          </div>
          <div class="field {{ 'invalid' if errors.judge_param }}">
            <label>判断参数名 *</label>
            <div class="search-select" id="judge-param-select">
              <div class="ss-box" tabindex="0">
                <span class="ss-value ss-ph">请选择…</span>
                <span class="ss-clear hidden">×</span>
              </div>
              <div class="ss-dropdown hidden">
                <input class="ss-search" placeholder="输入搜索…">
                <div class="ss-options">
                  {% for v in param_values %}<div class="ss-option" data-value="{{ v }}">{{ v }}</div>{% endfor %}
                </div>
                <div class="ms-empty hidden">无匹配选项</div>
              </div>
              <input type="hidden" name="judge_param" value="{{ form.get('judge_param', '') }}">
            </div>
            {% if errors.judge_param %}<p class="field-error">{{ errors.judge_param }}</p>{% endif %}
          </div>
          <div class="field {{ 'invalid' if errors.judge_rule }}">
            <label for="judge_rule">异常判断规则 *</label>
            <select id="judge_rule" name="judge_rule">
              <option value="">请选择…</option>
              {% for r in judge_rules %}
              <option value="{{ r }}" {{ 'selected' if form.get('judge_rule') == r }}>{{ r }}</option>
              {% endfor %}
            </select>
            {% if errors.judge_rule %}<p class="field-error">{{ errors.judge_rule }}</p>{% endif %}
          </div>
          <div class="field {{ 'invalid' if errors.threshold1 }}">
            <label for="threshold1">阈值1 *</label>
            <input id="threshold1" name="threshold1" placeholder="数字" value="{{ form.get('threshold1', '') }}">
            {% if errors.threshold1 %}<p class="field-error">{{ errors.threshold1 }}</p>{% endif %}
          </div>
          <div class="field hidden {{ 'invalid' if errors.threshold2 }}" id="threshold2-field">
            <label for="threshold2">阈值2 *</label>
            <input id="threshold2" name="threshold2" placeholder="数字" value="{{ form.get('threshold2', '') }}">
            {% if errors.threshold2 %}<p class="field-error">{{ errors.threshold2 }}</p>{% endif %}
          </div>
        </div>
      </div>

      <div class="form-branch hidden" id="branch-measure">
        <p class="branch-title">量测异常分支</p>
        <div class="form-grid">
          <div class="field {{ 'invalid' if errors.measure_dim }}">
            <label for="measure_dim">量测维度 *</label>
            <select id="measure_dim" name="measure_dim">
              <option value="">请选择…</option>
              {% for d in measure_dims %}
              <option value="{{ d }}" {{ 'selected' if form.get('measure_dim') == d }}>{{ d }}</option>
              {% endfor %}
            </select>
            {% if errors.measure_dim %}<p class="field-error">{{ errors.measure_dim }}</p>{% endif %}
          </div>
        </div>
      </div>

      <div class="form-branch hidden" id="branch-defect">
        <p class="branch-title">defect 异常分支</p>
        <div class="form-grid">
          <div class="field {{ 'invalid' if errors.defect_code }}">
            <label>defect code *</label>
            <div class="search-select" id="defect-code-select">
              <div class="ss-box" tabindex="0">
                <span class="ss-value ss-ph">请选择…</span>
                <span class="ss-clear hidden">×</span>
              </div>
              <div class="ss-dropdown hidden">
                <input class="ss-search" placeholder="输入搜索…">
                <div class="ss-options">
                  {% for v in defect_values %}<div class="ss-option" data-value="{{ v }}">{{ v }}</div>{% endfor %}
                </div>
                <div class="ms-empty hidden">无匹配选项</div>
              </div>
              <input type="hidden" name="defect_code" value="{{ form.get('defect_code', '') }}">
            </div>
            {% if errors.defect_code %}<p class="field-error">{{ errors.defect_code }}</p>{% endif %}
          </div>
          <div class="field">
            <label for="defect_size">defect size（选填）</label>
            <input id="defect_size" name="defect_size" placeholder="defect 大小" value="{{ form.get('defect_size', '') }}">
          </div>
        </div>
      </div>

      <div class="form-actions">
        <button type="submit" class="btn btn-primary">提交异常</button>
      </div>
    </form>
  </section>

  <section class="card">
    <h2 class="card-title">任务列表</h2>
    <form class="filter-bar" method="get" action="/">
      <div class="f-item">
        <label for="f_status">状态</label>
        <select id="f_status" name="status">
          <option value="">全部状态</option>
          {% for s in statuses %}
          <option value="{{ s }}" {{ 'selected' if filters.get('status') == s }}>{{ status_labels[s] }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="f-item">
        <label for="f_event_status">事件状态</label>
        <select id="f_event_status" name="event_status">
          <option value="">全部</option>
          <option value="open" {{ 'selected' if filters.get('event_status') == 'open' }}>Open</option>
          <option value="close" {{ 'selected' if filters.get('event_status') == 'close' }}>Close</option>
        </select>
      </div>
      <div class="f-item">
        <label for="f_site">Site</label>
        <select id="f_site" name="site">
          <option value="">全部</option>
          {% for s in machines_by_site %}
          <option value="{{ s }}" {{ 'selected' if filters.get('site') == s }}>{{ s }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="f-item">
        <label>机台</label>
        <div class="search-select" id="filter-machine-select" data-placeholder="全部机台" data-autosubmit="1">
          <div class="ss-box" tabindex="0">
            <span class="ss-value ss-ph">全部机台</span>
            <span class="ss-clear hidden">×</span>
          </div>
          <div class="ss-dropdown hidden">
            <input class="ss-search" placeholder="输入搜索…">
            <div class="ss-options">
              {% for m in distinct_machines %}<div class="ss-option" data-value="{{ m }}">{{ m }}</div>{% endfor %}
            </div>
            <div class="ms-empty hidden">无匹配选项</div>
          </div>
          <input type="hidden" name="machine" value="{{ filters.get('machine', '') }}">
        </div>
      </div>
      <div class="f-item">
        <label for="f_type">异常类型</label>
        <select id="f_type" name="exception_type">
          <option value="">全部</option>
          {% for t in exception_types %}
          <option value="{{ t }}" {{ 'selected' if filters.get('exception_type') == t }}>{{ t }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="f-item">
        <label for="f_date_from">提交日期从</label>
        <input id="f_date_from" type="date" name="date_from" value="{{ filters.get('date_from', '') }}">
      </div>
      <div class="f-item">
        <label for="f_date_to">至</label>
        <input id="f_date_to" type="date" name="date_to" value="{{ filters.get('date_to', '') }}">
      </div>
      <div class="f-item wide">
        <label for="f_keyword">关键字</label>
        <input id="f_keyword" name="keyword" placeholder="机台 / PFCD / 参数…" value="{{ filters.get('keyword', '') }}">
      </div>
      <button type="submit" class="btn">筛选</button>
      <a href="/" class="btn">重置</a>
    </form>
    <div id="task-table" class="table-wrap">{{ tasks_html | safe }}</div>
    <p class="hint">仅显示最近 50 条，请用筛选缩小范围。</p>
  </section>
</main>

<!-- 报告生成弹窗 -->
<div id="toast-overlay" class="toast-overlay hidden">
  <div class="toast-box" id="toast-box"></div>
</div>
<script>
"""
    + """// ---------- CSRF 防护：自动为同源写请求注入 token ----------
(function() {
  var meta = document.querySelector('meta[name="csrf-token"]');
  var csrf = meta ? meta.content : '';
  if (!csrf) return;
  var _fetch = window.fetch;
  window.fetch = function(input, init) {
    // 解析 URL：支持字符串和 Request 对象
    var url;
    if (typeof input === 'string') {
      url = input;
    } else if (input && input.url) {
      url = input.url;
    } else {
      return _fetch.call(this, input, init);
    }
    // 只对同源请求附加 CSRF（相对路径或以当前 origin 开头的绝对路径）
    if (url.indexOf('://') !== -1) {
      var origin = location.origin;
      if (url.substring(0, origin.length) !== origin) {
        return _fetch.call(this, input, init);
      }
    }
    // 确定 HTTP method
    var method = (init && init.method) || (input && input.method) || 'GET';
    if (/^(GET|HEAD|OPTIONS)$/i.test(method)) {
      return _fetch.call(this, input, init);
    }
    // 构建 Headers 对象（正确处理 Headers 实例和普通对象）
    init = init || {};
    var headers = new Headers(init.headers || {});
    if (!headers.has('X-CSRF-Token')) {
      headers.set('X-CSRF-Token', csrf);
    }
    init.headers = headers;
    return _fetch.call(this, input, init);
  };
})();

// ---------- 可搜索单选组件（替代 datalist） ----------
function initSearchSelect(el) {
  var box = el.querySelector('.ss-box');
  var valueSpan = el.querySelector('.ss-value');
  var clearBtn = el.querySelector('.ss-clear');
  var dropdown = el.querySelector('.ss-dropdown');
  var search = el.querySelector('.ss-search');
  var optionsContainer = el.querySelector('.ss-options');
  var emptyMsg = el.querySelector('.ms-empty');
  var hidden = el.querySelector('input[type=hidden]');
  var allOptions = Array.prototype.slice.call(optionsContainer.querySelectorAll('.ss-option'));
  var placeholder = el.dataset.placeholder || '请选择…';
  var autosubmit = el.dataset.autosubmit === '1';

  function getOptions() {
    return Array.prototype.slice.call(optionsContainer.querySelectorAll('.ss-option'));
  }

  function sync() {
    var val = hidden.value;
    var found = null;
    if (val) {
      getOptions().forEach(function (opt) {
        if (opt.dataset.value === val) { found = opt; }
      });
    }
    if (found) {
      valueSpan.textContent = found.textContent;
      valueSpan.classList.remove('ss-ph');
      clearBtn.classList.remove('hidden');
    } else {
      valueSpan.textContent = placeholder;
      valueSpan.classList.add('ss-ph');
      if (!val) { clearBtn.classList.add('hidden'); } else { clearBtn.classList.remove('hidden'); }
    }
  }

  function filterOptions() {
    var q = search.value.toLowerCase();
    var any = false;
    var opts = getOptions();
    opts.forEach(function (opt) {
      var show = opt.textContent.toLowerCase().indexOf(q) !== -1;
      opt.classList.toggle('hidden', !show);
      if (show) { any = true; }
    });
    emptyMsg.classList.toggle('hidden', any);
  }

  function open() {
    dropdown.classList.remove('hidden');
    search.value = '';
    filterOptions();
    search.focus();
  }

  function close() {
    dropdown.classList.add('hidden');
  }

  // 筛选栏提交：AJAX 刷新列表 + 更新 URL
  function submitFilter() {
    var form = el.closest('form');
    if (!form) { return; }
    var qs = '';
    var fields = form.querySelectorAll('input[name], select[name]');
    Array.prototype.forEach.call(fields, function (f) {
      if (f.name && f.value) {
        qs += (qs ? '&' : '') + encodeURIComponent(f.name) + '=' + encodeURIComponent(f.value);
      }
    });
    fetch('/partial/tasks?' + qs, { cache: 'no-store' })
      .then(function (res) { if (res.ok) { return res.text(); } throw new Error('fail'); })
      .then(function (html) { document.getElementById('task-table').innerHTML = html; })
      .catch(function () { /* 忽略 */ });
    if (history.replaceState) {
      history.replaceState(null, '', '/?' + qs);
    }
  }

  function selectOption(opt) {
    var val = opt.dataset.value;
    hidden.value = val;
    sync();
    close();
    if (autosubmit) { submitFilter(); }
  }

  // box 点击展开/收起
  box.addEventListener('click', function (e) {
    if (e.target.closest('.ss-clear')) {
      hidden.value = '';
      sync();
      close();
      if (autosubmit) { submitFilter(); }
      return;
    }
    if (dropdown.classList.contains('hidden')) { open(); } else { close(); }
  });

  // 搜索过滤
  search.addEventListener('input', filterOptions);

  // 选项点击
  optionsContainer.addEventListener('click', function (e) {
    var opt = e.target.closest('.ss-option');
    if (opt && !opt.classList.contains('hidden')) { selectOption(opt); }
  });

  // 键盘操作
  box.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { close(); box.blur(); }
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
  });

  // 点击外部关闭
  document.addEventListener('click', function (e) {
    if (!el.contains(e.target)) { close(); }
  });

  sync();

  // 返回公共方法
  return {
    setOptions: function (values, clearInvalid) {
      // 重建选项列表
      optionsContainer.innerHTML = '';
      var currentVal = hidden.value;
      var found = false;
      values.forEach(function (v) {
        var div = document.createElement('div');
        div.className = 'ss-option';
        div.dataset.value = v;
        div.textContent = v;
        optionsContainer.appendChild(div);
        if (v === currentVal) { found = true; }
      });
      allOptions = getOptions();
      if (clearInvalid && !found) {
        hidden.value = '';
      }
      sync();
    },
    sync: sync
  };
}

// ---------- 初始化各组件 ----------
var MACHINES = {{ machines_by_site | tojson }};
var siteSel = document.getElementById('site');
var machineSelect = initSearchSelect(document.getElementById('machine-select'));
var judgeParamSelect = initSearchSelect(document.getElementById('judge-param-select'));
var defectCodeSelect = initSearchSelect(document.getElementById('defect-code-select'));
var filterMachineSelect = initSearchSelect(document.getElementById('filter-machine-select'));
var typeSel = document.getElementById('exception_type');
var ruleSel = document.getElementById('judge_rule');
var threshold2Field = document.getElementById('threshold2-field');
var threshold2Input = document.getElementById('threshold2');

function fillMachines(clearInvalid) {
  machineSelect.setOptions(MACHINES[siteSel.value] || [], !!clearInvalid);
}
function applyBranches() {
  var t = typeSel.value;
  document.getElementById('branch-process').classList.toggle('hidden', t !== '制程参数');
  document.getElementById('branch-measure').classList.toggle('hidden', t !== '量测异常');
  document.getElementById('branch-defect').classList.toggle('hidden', t !== 'defect异常');
}
function applyThreshold2() {
  var need2 = (ruleSel.value || '').indexOf('阈值1,2') !== -1;
  threshold2Field.classList.toggle('hidden', !need2);
  threshold2Input.required = need2;
  if (!need2) { threshold2Input.value = ''; }
}
siteSel.addEventListener('change', function () { fillMachines(true); });
typeSel.addEventListener('change', applyBranches);
ruleSel.addEventListener('change', applyThreshold2);
// 校验失败回填后按当前值恢复联动状态
fillMachines(false); applyBranches(); applyThreshold2();

// ---------- 可搜索多选组件 ----------
function initMultiSelect(el) {
  var box = el.querySelector('.ms-box');
  var chips = el.querySelector('.ms-chips');
  var search = el.querySelector('.ms-search');
  var dropdown = el.querySelector('.ms-dropdown');
  var hidden = el.querySelector('input[type=hidden]');
  var empty = el.querySelector('.ms-empty');
  var checkboxes = Array.prototype.slice.call(el.querySelectorAll('.ms-option input'));
  var selected = [];
  try { selected = JSON.parse(hidden.value || '[]'); } catch (e) { selected = []; }

  function sync() {
    chips.innerHTML = '';
    selected.forEach(function (v) {
      var chip = document.createElement('span');
      chip.className = 'ms-chip';
      chip.textContent = v;
      var x = document.createElement('span');
      x.className = 'ms-chip-x';
      x.textContent = '×';
      x.dataset.v = v;
      chip.appendChild(x);
      chips.appendChild(chip);
    });
    checkboxes.forEach(function (cb) { cb.checked = selected.indexOf(cb.value) !== -1; });
    hidden.value = JSON.stringify(selected);
    search.placeholder = selected.length ? '' : '输入搜索…';
  }
  function open() { dropdown.classList.remove('hidden'); search.focus(); }
  function close() { dropdown.classList.add('hidden'); }
  box.addEventListener('click', function (e) {
    if (e.target.closest('.ms-chip-x')) {
      var v = e.target.closest('.ms-chip-x').dataset.v;
      selected = selected.filter(function (x) { return x !== v; });
      sync();
      return;
    }
    if (dropdown.classList.contains('hidden')) {
      open();
    } else if (!e.target.classList.contains('ms-search')) {
      close();
    }
  });
  search.addEventListener('focus', open);
  search.addEventListener('input', function () {
    var q = search.value.toLowerCase();
    var any = false;
    checkboxes.forEach(function (cb) {
      var show = cb.value.toLowerCase().indexOf(q) !== -1;
      cb.closest('.ms-option').classList.toggle('hidden', !show);
      if (show) { any = true; }
    });
    empty.classList.toggle('hidden', any);
  });
  dropdown.addEventListener('change', function (e) {
    var cb = e.target.closest('.ms-option input');
    if (!cb) { return; }
    if (cb.checked && selected.indexOf(cb.value) === -1) { selected.push(cb.value); }
    if (!cb.checked) { selected = selected.filter(function (x) { return x !== cb.value; }); }
    sync();
  });
  box.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { close(); }
  });
  document.addEventListener('click', function (e) {
    if (!el.contains(e.target)) { close(); }
  });
  sync();
}
Array.prototype.forEach.call(document.querySelectorAll('.multi-select'), initMultiSelect);

// ---------- 日期输入：点击框任意位置弹出选择器 ----------
Array.prototype.forEach.call(document.querySelectorAll('input[type="date"], input[type="datetime-local"]'), function (el) {
  el.addEventListener('click', function () {
    if (typeof el.showPicker === 'function') {
      try { el.showPicker(); } catch (e) { /* 某些浏览器/状态下可能抛异常 */ }
    }
  });
});

// ---------- 任务列表局部刷新（复用当前筛选条件） ----------
var listFetching = false;
function refreshList() {
  if (listFetching) { return; }
  listFetching = true;
  fetch('/partial/tasks' + location.search, { cache: 'no-store' })
    .then(function (res) {
      if (res.ok) { return res.text(); }
      throw new Error('not ok');
    })
    .then(function (html) {
      document.getElementById('task-table').innerHTML = html;
    })
    .catch(function () { /* 网络瞬断，下一轮重试 */ })
    .then(function () { listFetching = false; });
}
setInterval(refreshList, 5000);

// 筛选栏表单：拦截 submit 改为 AJAX + 更新 URL
var filterForm = document.querySelector('.filter-bar');
if (filterForm) {
  filterForm.addEventListener('submit', function (e) {
    e.preventDefault();
    var qs = '';
    var fields = filterForm.querySelectorAll('input[name], select[name]');
    Array.prototype.forEach.call(fields, function (f) {
      if (f.name && f.value) {
        qs += (qs ? '&' : '') + encodeURIComponent(f.name) + '=' + encodeURIComponent(f.value);
      }
    });
    fetch('/partial/tasks?' + qs, { cache: 'no-store' })
      .then(function (res) { if (res.ok) { return res.text(); } throw new Error('fail'); })
      .then(function (html) { document.getElementById('task-table').innerHTML = html; })
      .catch(function () { /* 忽略 */ });
    if (history.replaceState) {
      history.replaceState(null, '', '/?' + qs);
    }
  });
}

// ---------- 任务行点击进入详情 ----------
document.addEventListener('click', function (e) {
  // 跳过按钮、链接、可操作元素上的点击
  if (e.target.closest('a') || e.target.closest('button') || e.target.closest('[data-action]')) { return; }
  var row = e.target.closest('.task-row');
  if (row && row.dataset.id) {
    location.href = '/tasks/' + row.dataset.id;
  }
});

// ---------- 删除 / 重试（document 级事件委托，轮询换 DOM 后依然有效） ----------
document.addEventListener('click', function (e) {
  var btn = e.target.closest('[data-action]');
  if (!btn || btn.disabled) { return; }
  var id = btn.dataset.id, action = btn.dataset.action;

  // --- 报告 / 重新生成 ---
  if (action === 'report') {
    // 先检查是否已有报告
    fetch('/api/report/' + id + '/check', { cache: 'no-store' })
      .then(function(res) { return res.json(); })
      .then(function(data) {
        if (data.exists) {
          // 已有报告，直接打开
          window.open('/tasks/' + id + '/report', '_blank');
        } else {
          // 没有报告，生成
          showToast('⏳ 正在生成报告...', '请稍候，数据量较大时可能需要几分钟', 'loading');
          return fetch('/api/report/' + id + '/generate', { method: 'POST' }).then(function(r) { return r.json(); });
        }
      })
      .then(function(data) {
        if (data && data.ok) {
          showToast('✅ 报告生成完成', '正在打开报告...', 'done');
          setTimeout(function() { window.open('/tasks/' + id + '/report', '_blank'); }, 800);
        } else if (data && !data.ok) {
          showToast('❌ 生成失败', data.error || '未知错误', 'error');
        }
      })
      .catch(function(err) {
        showToast('❌ 请求失败', err.message, 'error');
      });
    return;
  }
  if (action === 'regen-report') {
    if (!confirm('重新生成将覆盖现有报告，确定继续？')) return;
    showToast('⏳ 正在重新生成报告...', '请稍候', 'loading');
    fetch('/api/report/' + id + '/regenerate', { method: 'POST' })
      .then(function(res) { return res.json(); })
      .then(function(data) {
        if (data.ok) {
          showToast('✅ 报告生成完成', '正在打开报告...', 'done');
          setTimeout(function() { window.open('/tasks/' + id + '/report', '_blank'); }, 800);
        } else {
          showToast('❌ 生成失败', data.error || '未知错误', 'error');
        }
      })
      .catch(function(err) {
        showToast('❌ 请求失败', err.message, 'error');
      });
    return;
  }

  // --- 切换事件状态 ---
  if (action === 'toggle-status') {
    fetch('/tasks/' + id + '/toggle-status', { method: 'POST' })
      .then(function(res) { return res.json(); })
      .then(function(data) {
        if (data.ok) {
          refreshList();
        } else {
          alert(data.message || '切换失败');
        }
      })
      .catch(function() { alert('网络错误，切换失败'); });
    return;
  }

  // --- 删除 / 重试 ---
  var msg = action === 'delete'
    ? '确定删除任务 #' + id + ' 吗？将同时删除其文件目录，不可恢复。'
    : '确定将任务 #' + id + ' 重新入队吗？原结果将被清空，日志续写。';
  if (!confirm(msg)) { return; }
  fetch('/tasks/' + id + '/' + action, { method: 'POST' })
    .then(function (res) {
      return res.json().then(function (data) {
        return { ok: res.ok, data: data };
      }).catch(function () { return { ok: res.ok, data: {} }; });
    })
    .then(function (result) {
      if (!result.ok || !result.data.ok) { alert(result.data.message || '操作失败'); return; }
      refreshList();
    })
    .catch(function () { alert('网络错误，操作失败'); });
});

// ---------- 报告生成弹窗 ----------
function showToast(title, desc, type) {
  var overlay = document.getElementById('toast-overlay');
  var box = document.getElementById('toast-box');
  var html = '';
  if (type === 'loading') {
    html = '<div class="toast-spinner"></div>';
  } else if (type === 'done') {
    html = '<div class="toast-done">✓</div>';
  } else if (type === 'error') {
    html = '<div class="toast-error">✗</div>';
  }
  html += '<div class="toast-title">' + title + '</div>';
  html += '<div class="toast-desc">' + desc + '</div>';
  if (type === 'done' || type === 'error') {
    html += '<button class="btn toast-btn" onclick="closeToast()">关闭</button>';
  }
  box.innerHTML = html;
  overlay.classList.remove('hidden');
  if (type === 'done' || type === 'error') {
    setTimeout(closeToast, 5000);
  }
}
function closeToast() {
  document.getElementById('toast-overlay').classList.add('hidden');
}
"""
    + """
</script>
</body>
</html>
"""
)

DETAIL_PAGE = (
    """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="csrf-token" content="{{ csrf_token }}">
<title>任务 #{{ task.id }} - 异常处理平台</title>
<style>"""
    + _PAGE_STYLE
    + """</style>
</head>
<body>
<header class="page-header">
  <div class="container">
    <h1>任务 #{{ task.id }}</h1>
    <span class="sub"><a href="/">← 返回列表</a> · <a href="/config">配置管理</a> · <a href="/tasks/{{ task.id }}/report" target="_blank">📊 报告生成</a></span>
  </div>
</header>
<main class="container">
  <div id="detail-body">{{ detail_html | safe }}</div>
</main>
<script>
"""
    + """// ---------- CSRF 防护：自动为同源写请求注入 token ----------
(function() {
  var meta = document.querySelector('meta[name="csrf-token"]');
  var csrf = meta ? meta.content : '';
  if (!csrf) return;
  var _fetch = window.fetch;
  window.fetch = function(input, init) {
    var url = typeof input === 'string' ? input : (input && input.url ? input.url : '');
    if (!url) return _fetch.call(this, input, init);
    if (url.indexOf('://') !== -1) {
      var origin = location.origin;
      if (url.substring(0, origin.length) !== origin) return _fetch.call(this, input, init);
    }
    var method = (init && init.method) || (input && input.method) || 'GET';
    if (/^(GET|HEAD|OPTIONS)$/i.test(method)) return _fetch.call(this, input, init);
    init = init || {};
    var headers = new Headers(init.headers || {});
    if (!headers.has('X-CSRF-Token')) { headers.set('X-CSRF-Token', csrf); }
    init.headers = headers;
    return _fetch.call(this, input, init);
  };
})();

// ---------- 详情局部刷新（终态停轮询） ----------
var detailFetching = false;
var detailStopped = false;
function checkStopped() {
  var root = document.querySelector('#detail-body [data-status]');
  if (root && (root.dataset.status === 'success' || root.dataset.status === 'failed')) {
    detailStopped = true;
    var hint = document.querySelector('#detail-body .card-title .sub');
    if (hint) { hint.textContent = '已停止刷新'; }
  }
}
function refreshDetail() {
  if (detailFetching || detailStopped) { return; }
  detailFetching = true;
  fetch('/partial/tasks/{{ task.id }}', { cache: 'no-store' })
    .then(function (res) {
      if (res.ok) { return res.text(); }
      // 404 等：任务不存在或已删除
      document.getElementById('detail-body').innerHTML =
        '<div class="empty">任务不存在或已删除</div>';
      detailStopped = true;
      throw new Error('stopped');
    })
    .then(function (html) {
      document.getElementById('detail-body').innerHTML = html;
      checkStopped();
    })
    .catch(function (e) {
      if (e && e.message === 'stopped') { return; }
      /* 网络瞬断，下一轮重试 */
    })
    .then(function () { detailFetching = false; });
}
checkStopped();
setInterval(refreshDetail, 3000);

// ---------- 删除 / 重试 ----------
document.addEventListener('click', function (e) {
  var btn = e.target.closest('[data-action]');
  if (!btn || btn.disabled) { return; }
  var id = btn.dataset.id, action = btn.dataset.action;
  var msg = action === 'delete'
    ? '确定删除任务 #' + id + ' 吗？将同时删除其文件目录，不可恢复。'
    : '确定将任务 #' + id + ' 重新入队吗？原结果将被清空，日志续写。';
  if (!confirm(msg)) { return; }
  fetch('/tasks/' + id + '/' + action, { method: 'POST' })
    .then(function (res) {
      return res.json().then(function (data) {
        return { ok: res.ok, data: data };
      }).catch(function () { return { ok: res.ok, data: {} }; });
    })
    .then(function (result) {
      if (!result.ok || !result.data.ok) { alert(result.data.message || '操作失败'); return; }
      if (action === 'delete') { location.href = '/'; return; }
      detailStopped = false;
      refreshDetail();
    })
    .catch(function () { alert('网络错误，操作失败'); });
});
"""
    + """
</script>
</body>
</html>
"""
)

CONFIG_PAGE = (
    """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="csrf-token" content="{{ csrf_token }}">
<title>配置管理 - 异常处理平台</title>
<style>"""
    + _PAGE_STYLE
    + """</style>
</head>
<body>
<header class="page-header">
  <div class="container">
    <h1>配置管理</h1>
    <span class="sub"><a href="/">← 返回列表</a></span>
  </div>
</header>
<main class="container">
  <div class="card">
    <div class="tab-header">
      {% for k, label, _ in panels %}
      <button class="tab-btn {{ 'active' if loop.first }}" data-kind="{{ k }}">{{ label }}</button>
      {% endfor %}
    </div>
    {% for k, label, tbody in panels %}
    <div class="config-panel {{ 'hidden' if not loop.first }}" data-panel="{{ k }}">
      <div class="cfg-toolbar">
        <input type="text" class="cfg-add-input" placeholder="新增{{ label }}值…">
        <button class="btn btn-primary cfg-add" data-kind="{{ k }}">添加</button>
        <details class="import-box">
          <summary>批量粘贴导入</summary>
          <div>
            <textarea class="cfg-import-text" placeholder="每行一个值，可整列从 Excel 粘贴"></textarea>
            <button class="btn cfg-import" data-kind="{{ k }}">导入</button>
            <span class="cfg-import-result"></span>
          </div>
        </details>
      </div>
      <div class="table-wrap">
        <table class="cfg-table">
          <thead>
            <tr><th style="width:60px">ID</th><th>值</th><th>创建时间</th><th style="width:150px">操作</th></tr>
          </thead>
          {{ tbody | safe }}
        </table>
      </div>
      <p class="cfg-count"></p>
    </div>
    {% endfor %}
  </div>
</main>
<script>
"""
    + """// ---------- CSRF 防护：自动为同源写请求注入 token ----------
(function() {
  var meta = document.querySelector('meta[name="csrf-token"]');
  var csrf = meta ? meta.content : '';
  if (!csrf) return;
  var _fetch = window.fetch;
  window.fetch = function(input, init) {
    var url = typeof input === 'string' ? input : (input && input.url ? input.url : '');
    if (!url) return _fetch.call(this, input, init);
    if (url.indexOf('://') !== -1) {
      var origin = location.origin;
      if (url.substring(0, origin.length) !== origin) return _fetch.call(this, input, init);
    }
    var method = (init && init.method) || (input && input.method) || 'GET';
    if (/^(GET|HEAD|OPTIONS)$/i.test(method)) return _fetch.call(this, input, init);
    init = init || {};
    var headers = new Headers(init.headers || {});
    if (!headers.has('X-CSRF-Token')) { headers.set('X-CSRF-Token', csrf); }
    init.headers = headers;
    return _fetch.call(this, input, init);
  };
})();

// ---------- tab 切换 ----------
var tabBtns = Array.prototype.slice.call(document.querySelectorAll('.tab-btn'));
tabBtns.forEach(function (t) {
  t.addEventListener('click', function () {
    tabBtns.forEach(function (x) { x.classList.toggle('active', x === t); });
    Array.prototype.forEach.call(document.querySelectorAll('.config-panel'), function (p) {
      p.classList.toggle('hidden', p.dataset.panel !== t.dataset.kind);
    });
  });
});

function updateCount(kind) {
  var panel = document.querySelector('.config-panel[data-panel="' + kind + '"]');
  if (!panel) { return; }
  var n = panel.querySelectorAll('.cfg-tbody tr').length;
  panel.querySelector('.cfg-count').textContent = '共 ' + n + ' 条';
}
Array.prototype.forEach.call(document.querySelectorAll('.config-panel'), function (p) {
  updateCount(p.dataset.panel);
});

function postJson(url, payload) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }).then(function (res) {
    return res.json().catch(function () { return { ok: false, message: '解析失败' }; });
  }).catch(function () { return { ok: false, message: '网络错误' }; });
}

function loadTbody(kind) {
  var panel = document.querySelector('.config-panel[data-panel="' + kind + '"]');
  if (!panel) { return; }
  fetch('/partial/config/' + kind, { cache: 'no-store' })
    .then(function (res) {
      if (!res.ok) { throw new Error('load failed'); }
      return res.text();
    })
    .then(function (html) {
      panel.querySelector('.cfg-tbody').outerHTML = html;
      updateCount(kind);
    })
    .catch(function (e) {
      if (e && e.message === 'load failed') { alert('加载失败'); }
      else { alert('网络错误'); }
    });
}

// ---------- 添加 / 导入 / 编辑 / 删除（事件委托） ----------
document.addEventListener('click', function (e) {
  // 添加
  var addBtn = e.target.closest('.cfg-add');
  if (addBtn) {
    var kind = addBtn.dataset.kind;
    var input = addBtn.closest('.config-panel').querySelector('.cfg-add-input');
    var v = input.value.trim();
    if (!v) { alert('请输入值'); return; }
    postJson('/config/' + kind + '/add', { value: v }).then(function (data) {
      if (!data.ok) { alert(data.message || '添加失败'); return; }
      input.value = '';
      loadTbody(kind);
    });
    return;
  }
  // 批量导入
  var impBtn = e.target.closest('.cfg-import');
  if (impBtn) {
    var impKind = impBtn.dataset.kind;
    var box = impBtn.closest('.config-panel');
    var text = box.querySelector('.cfg-import-text').value;
    if (!text.trim()) { alert('请粘贴内容'); return; }
    postJson('/config/' + impKind + '/import', { text: text }).then(function (impData) {
      if (!impData.ok) { alert(impData.message || '导入失败'); return; }
      box.querySelector('.cfg-import-result').textContent = impData.message;
      box.querySelector('.cfg-import-text').value = '';
      loadTbody(impKind);
    });
    return;
  }
  // 删除
  var delBtn = e.target.closest('.cfg-del');
  if (delBtn) {
    var delKind = delBtn.closest('.config-panel').dataset.panel;
    var row = delBtn.closest('tr');
    if (!confirm('确定删除该配置值吗？历史任务不受影响。')) { return; }
    postJson('/config/' + delKind + '/delete', { id: parseInt(row.dataset.id, 10) }).then(function (delData) {
      if (!delData.ok) { alert(delData.message || '删除失败'); return; }
      loadTbody(delKind);
    });
    return;
  }
  // 进入编辑：value 单元格换成输入框 + 保存/取消（DOM 属性赋值，防 XSS）
  var editBtn = e.target.closest('.cfg-edit');
  if (editBtn) {
    var row2 = editBtn.closest('tr');
    var cell = row2.querySelector('.cfg-value');
    var oldValue = row2.querySelector('.cfg-value-text').textContent;
    cell.innerHTML = '';
    var editInput = document.createElement('input');
    editInput.type = 'text';
    editInput.className = 'row-edit';
    editInput.value = oldValue;
    var saveBtn = document.createElement('button');
    saveBtn.className = 'btn btn-sm cfg-save';
    saveBtn.textContent = '保存';
    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-sm cfg-cancel';
    cancelBtn.textContent = '取消';
    cell.append(editInput, saveBtn, cancelBtn);
    editInput.focus();
    editInput.select();
    return;
  }
  // 保存编辑
  var saveBtn = e.target.closest('.cfg-save');
  if (saveBtn) {
    var saveKind = saveBtn.closest('.config-panel').dataset.panel;
    var saveRow = saveBtn.closest('tr');
    var newValue = saveRow.querySelector('.row-edit').value.trim();
    if (!newValue) { alert('值不能为空'); return; }
    postJson('/config/' + saveKind + '/edit', {
      id: parseInt(saveRow.dataset.id, 10), value: newValue
    }).then(function (saveData) {
      if (!saveData.ok) { alert(saveData.message || '编辑失败'); return; }
      loadTbody(saveKind);
    });
    return;
  }
  // 取消编辑
  var cancelBtn = e.target.closest('.cfg-cancel');
  if (cancelBtn) {
    loadTbody(cancelBtn.closest('.config-panel').dataset.panel);
    return;
  }
});

// 行内编辑快捷键：Enter 保存 / Esc 取消
document.addEventListener('keydown', function (e) {
  var input = e.target.closest('.row-edit');
  if (!input) { return; }
  if (e.key === 'Enter') {
    input.closest('tr').querySelector('.cfg-save').click();
  } else if (e.key === 'Escape') {
    input.closest('tr').querySelector('.cfg-cancel').click();
  }
});
"""
    + """
</script>
</body>
</html>
"""
)

ERROR_PAGE = (
    """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>提示 - 异常处理平台</title>
<style>"""
    + _PAGE_STYLE
    + """</style>
</head>
<body>
<header class="page-header">
  <div class="container">
    <h1>异常处理平台</h1>
  </div>
</header>
<main class="container">
  <div class="card">
    <h2 class="card-title">提示</h2>
    <p>{{ message }}</p>
    <p><a href="{{ back_url }}">返回</a></p>
  </div>
</main>
</body>
</html>
"""
)


# ---------- 工具函数 ----------


def _save_input(task_id: int, data: dict) -> None:
    """提交时把完整表单数据落盘 input.json（web 创建任务时写入）。"""
    task_dir = os.path.join(database.TASKS_DIR, str(task_id))
    os.makedirs(task_dir, exist_ok=True)
    with open(os.path.join(task_dir, "input.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _read_file(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _read_tail(path: str, max_lines: int = 200) -> str | None:
    """读取文本文件末尾 max_lines 行（run.log 随重试增长，截尾控制体积）。"""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        return "".join(lines[-max_lines:])
    except OSError:
        return None


def _append_line(path: str, line: str) -> None:
    """向 run.log 追加一行（web 侧用于重试标记，格式与 worker 一致）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")


def _parse_multi(raw: str) -> list:
    """解析多选隐藏字段的 JSON 数组；非法输入返回空列表。"""
    try:
        value = json.loads(raw or "[]")
        return value if isinstance(value, list) else []
    except (TypeError, ValueError):
        return []


def _is_number(s: str) -> bool:
    """判断字符串能否解析为有限数值（阈值只展示不运算，仅校验格式）。"""
    try:
        return math.isfinite(float(s))
    except (TypeError, ValueError):
        return False


_TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?")


def _normalize_time(s: str) -> str | None:
    """校验并规范化 datetime-local 输入为 'YYYY-MM-DDTHH:MM'（去秒）。"""
    s = (s or "").strip()
    if not _TIME_RE.fullmatch(s):
        return None
    return s[:16]


def _query_from_args(args) -> list[dict]:
    """从请求参数提取筛选条件并查询任务列表（列表页与片段路由共用）。"""
    return database.query_tasks(
        status=args.get("status") or None,
        site=args.get("site") or None,
        machine=(args.get("machine") or "").strip() or None,
        exception_type=args.get("exception_type") or None,
        date_from=args.get("date_from") or None,
        date_to=args.get("date_to") or None,
        keyword=(args.get("keyword") or "").strip() or None,
        event_status=args.get("event_status") or None,
    )


def _index_context(filters, form, errors):
    """列表页（正常 / 校验失败回填）共用的渲染上下文。"""
    return {
        "tasks_html": render_template_string(
            LIST_FRAGMENT,
            tasks=_query_from_args(filters),
            status_labels=STATUS_LABELS,
        ),
        "filters": filters,
        "form": form,
        "errors": errors,
        "exception_types": EXCEPTION_TYPES,
        "main_processes": database.MAIN_PROCESSES,
        "judge_rules": database.JUDGE_RULES,
        "measure_dims": database.MEASURE_DIMS,
        "pfcd_values": [r["value"] for r in database.list_configs("pfcd")],
        "param_values": [r["value"] for r in database.list_configs("process_param")],
        "defect_values": [r["value"] for r in database.list_configs("defect_code")],
        "machines_by_site": database.MACHINES_BY_SITE,
        "statuses": STATUS_ORDER,
        "status_labels": STATUS_LABELS,
        "distinct_machines": database.get_distinct_machines(),
    }


def _detail_context(task: dict) -> dict:
    """详情页与详情片段共用的渲染上下文。"""
    result_pretty = ""
    if task["status"] == "success" and task["result"]:
        try:
            result_pretty = json.dumps(
                json.loads(task["result"]), ensure_ascii=False, indent=2
            )
        except ValueError:
            result_pretty = task["result"]
    task_dir = os.path.join(database.TASKS_DIR, str(task["id"]))
    return {
        "task": task,
        "status_labels": STATUS_LABELS,
        "result_pretty": result_pretty,
        "input_text": _read_file(os.path.join(task_dir, "input.json")),
        "run_log_text": _read_tail(os.path.join(task_dir, "run.log")),
    }


# ---------- 任务路由 ----------


# ===== 登录 / 登出 =====

@app.route("/login", methods=["GET", "POST"])
def login():
    """登录页面 + 登录处理。

    GET: 显示登录表单
    POST: 校验密码 → 签发 session → 重定向首页
    """
    if request.method == "GET":
        # 已登录直接跳首页
        if session.get("user"):
            return redirect("/")
        return render_template_string(
            """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>登录 - 异常处理平台</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0a0f1e;color:#e0e0e0;
display:flex;justify-content:center;align-items:center;min-height:100vh}
.login-card{background:#111827;border:1px solid #1e3a5f;border-radius:12px;
padding:40px;width:380px;max-width:90vw}
.login-card h2{color:#64b5f6;margin-bottom:24px;text-align:center;font-size:20px}
.field{margin-bottom:16px}
.field label{display:block;margin-bottom:6px;font-size:13px;color:#90a4ae}
.field input{width:100%;padding:10px 12px;background:#0d1117;border:1px solid #30363d;
border-radius:6px;color:#e0e0e0;font-size:14px;outline:none}
.field input:focus{border-color:#1e3a5f}
.btn{width:100%;padding:10px;background:#1e3a5f;color:#e0e0e0;border:none;
border-radius:6px;font-size:14px;cursor:pointer;margin-top:8px}
.btn:hover{background:#243b6e}
.error{color:#ef5350;font-size:13px;margin-bottom:12px;text-align:center}
{% if error %}<p class="error">{{ error }}</p>{% endif %}
</style>
</head>
<body>
<form class="login-card" method="post">
<h2>操作员登录</h2>
{% if error %}<p class="error">{{ error }}</p>{% endif %}
<div class="field">
<label for="password">操作密码</label>
<input id="password" name="password" type="password" required autofocus>
</div>
<button class="btn" type="submit">登 录</button>
</form>
</body>
</html>""", error=request.args.get("error", "")
        )

    # POST: 密码校验
    password = (request.form.get("password") or "").strip()
    ip = request.remote_addr or "127.0.0.1"

    # 限速检查
    ok, reason = _rate_limit_check(ip)
    if not ok:
        return render_template_string(
            """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>登录受限</title></head><body style="font-family:system-ui;text-align:center;padding-top:100px">
<h2 style="color:#ef5350">{{ reason }}</h2><p><a href="/login">返回</a></p>
</body></html>""", reason=reason), 429

    if not password:
        return redirect("/login?error=请输入密码")

    if _check_password(password):
        # 成功：清计数器，签发 session
        _rate_limit_store.pop(ip, None)
        session.clear()
        session.permanent = True
        session["user"] = ip
        session["csrf_token"] = secrets.token_hex(32)
        return redirect("/")

    # 失败：记录
    entry = _rate_limit_store.get(ip)
    import time as _time2
    if entry is None:
        _rate_limit_store[ip] = {"failures": 1, "last_fail": _time2.time()}
    else:
        entry["failures"] += 1
        entry["last_fail"] = _time2.time()
    return redirect("/login?error=密码错误")


@app.post("/logout")
def logout():
    """登出：清除 session 并返回首页。"""
    session.clear()
    return redirect("/")


@app.get("/")
def index():
    return render_template_string(INDEX_PAGE, **_index_context(request.args, {}, {}), user=session.get("user"))


@app.post("/create")
def create():
    """提交任务：服务端白名单校验 → 失败回填内联报错，成功落库并跳详情（PRG）。"""
    form = request.form
    errors = {}

    site = (form.get("site") or "").strip()
    machine = (form.get("machine") or "").strip()
    main_process = _parse_multi(form.get("main_process"))
    pfcd = _parse_multi(form.get("pfcd"))
    start_time = _normalize_time(form.get("start_time"))
    end_time = _normalize_time(form.get("end_time"))
    exception_type = (form.get("exception_type") or "").strip()
    if site not in database.MACHINES_BY_SITE:
        errors["site"] = "请选择 Site"
    if machine not in database.MACHINES_BY_SITE.get(site, []):
        errors["machine"] = "机台不在所选 Site 的列表中"
    if not main_process or not set(main_process) <= set(database.MAIN_PROCESSES):
        errors["main_process"] = "请选择主制程"
    if not pfcd or not set(pfcd) <= {r["value"] for r in database.list_configs("pfcd")}:
        errors["pfcd"] = "请选择 PFCD"
    if start_time is None:
        errors["start_time"] = "请填写起始时间"
    if end_time is None:
        errors["end_time"] = "请填写结束时间"
    elif start_time is not None and start_time >= end_time:
        errors["end_time"] = "结束时间必须晚于起始时间"
    if exception_type not in EXCEPTION_TYPES:
        errors["exception_type"] = "请选择异常类型"

    branch = {}
    if exception_type == "制程参数":
        process_params = _parse_multi(form.get("process_params"))
        judge_param = (form.get("judge_param") or "").strip()
        judge_rule = (form.get("judge_rule") or "").strip()
        threshold1 = (form.get("threshold1") or "").strip()
        threshold2 = (form.get("threshold2") or "").strip()
        param_values = {r["value"] for r in database.list_configs("process_param")}
        if not process_params or not set(process_params) <= param_values:
            errors["process_params"] = "请选择制程参数名"
        if judge_param not in param_values:
            errors["judge_param"] = "请选择判断参数名"
        if judge_rule not in database.JUDGE_RULES:
            errors["judge_rule"] = "请选择异常判断规则"
        if not threshold1 or not _is_number(threshold1):
            errors["threshold1"] = "请填写数字"
        if "阈值1,2" in judge_rule and (not threshold2 or not _is_number(threshold2)):
            errors["threshold2"] = "请填写数字"
        branch = {
            "process_params": json.dumps(process_params, ensure_ascii=False) or None,
            "judge_param": judge_param or None,
            "judge_rule": judge_rule or None,
            "threshold1": threshold1 or None,
            "threshold2": threshold2 or None,
        }
    elif exception_type == "量测异常":
        measure_dim = (form.get("measure_dim") or "").strip()
        if measure_dim not in database.MEASURE_DIMS:
            errors["measure_dim"] = "请选择量测维度"
        branch = {"measure_dim": measure_dim or None}
    elif exception_type == "defect异常":
        defect_code = (form.get("defect_code") or "").strip()
        defect_size = (form.get("defect_size") or "").strip()
        defect_values = {r["value"] for r in database.list_configs("defect_code")}
        if defect_code not in defect_values:
            errors["defect_code"] = "请选择 defect code"
        branch = {
            "defect_code": defect_code or None,
            "defect_size": defect_size or None,
        }

    if errors:
        return (
            render_template_string(
                INDEX_PAGE, **_index_context(request.args, form, errors)
            ),
            400,
        )

    data = {
        "site": site,
        "machine": machine,
        "main_process": json.dumps(main_process, ensure_ascii=False),
        "pfcd": json.dumps(pfcd, ensure_ascii=False),
        "start_time": start_time,
        "end_time": end_time,
        "exception_type": exception_type,
        "hold_flag": "否",
        "event_description": (form.get("event_description") or "").strip(),
        "event_status": "open",
        **branch,
    }
    task_id = database.create_task(data)
    _save_input(task_id, data)

    # 自动生成报告（有对应工作流才触发）
    task = database.get_task(task_id)
    if _has_valid_workflow(task):
        engine = _get_engine(task)
        try:
            engine.generate_report(task)
        except engine.GenerationCancelled:
            pass  # 被取消，不影响跳转
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[create] 任务 #{task_id} 自动生成报告失败: {e}")
    else:
        print(f"[create] 任务 #{task_id} ({task.get('exception_type')}/{task.get('measure_dim')}) 无对应工作流，跳过报告生成")

    return redirect(url_for("task_detail", task_id=task_id))


# ---------- 外部 API：提交异常 + 自动生成报告 ----------

@app.post("/api/submit")
def api_submit():
    """外部调用 API：提交异常任务并自动生成报告，返回报告地址。

    接收 JSON，校验逻辑与表单 /create 完全一致。
    落库后同步生成报告（阻塞等待），成功后返回报告 URL。
    """
    try:
        body = request.get_json(force=True, silent=True) or {}
    except Exception:
        return jsonify(ok=False, error="请求体必须是有效 JSON"), 400

    errors = {}

    site = (body.get("site") or "").strip()
    machine = (body.get("machine") or "").strip()
    main_process_raw = body.get("main_process", [])
    main_process = list(main_process_raw) if isinstance(main_process_raw, list) else []
    pfcd_raw = body.get("pfcd", [])
    pfcd = list(pfcd_raw) if isinstance(pfcd_raw, list) else []
    start_time = _normalize_time(body.get("start_time", ""))
    end_time = _normalize_time(body.get("end_time", ""))
    exception_type = (body.get("exception_type") or "").strip()

    if site not in database.MACHINES_BY_SITE:
        errors["site"] = "请选择 Site"
    if machine not in database.MACHINES_BY_SITE.get(site, []):
        errors["machine"] = "机台不在所选 Site 的列表中"
    if not main_process or not set(main_process).issubset(set(database.MAIN_PROCESSES)):
        errors["main_process"] = "请选择主制程" + str(database.MAIN_PROCESSES)
    if not pfcd or not set(pfcd).issubset({r["value"] for r in database.list_configs("pfcd")}):
        errors["pfcd"] = "请选择 PFCD"
    if start_time is None:
        errors["start_time"] = "请填写起始时间（格式 YYYY-MM-DDTHH:MM）"
    if end_time is None:
        errors["end_time"] = "请填写结束时间（格式 YYYY-MM-DDTHH:MM）"
    elif start_time is not None and start_time >= end_time:
        errors["end_time"] = "结束时间必须晚于起始时间"
    if exception_type not in EXCEPTION_TYPES:
        errors["exception_type"] = "请选择异常类型" + str(EXCEPTION_TYPES)

    branch = {}
    if exception_type == "制程参数":
        process_params_raw = body.get("process_params", [])
        process_params = list(process_params_raw) if isinstance(process_params_raw, list) else []
        judge_param = (body.get("judge_param") or "").strip()
        judge_rule = (body.get("judge_rule") or "").strip()
        threshold1 = (body.get("threshold1") or "").strip()
        threshold2 = (body.get("threshold2") or "").strip()
        param_values = {r["value"] for r in database.list_configs("process_param")}
        if not process_params or not set(process_params).issubset(param_values):
            errors["process_params"] = "请选择制程参数名"
        if judge_param not in param_values:
            errors["judge_param"] = "请选择判断参数名"
        if judge_rule not in database.JUDGE_RULES:
            errors["judge_rule"] = "请选择异常判断规则" + str(database.JUDGE_RULES)
        if not threshold1 or not _is_number(threshold1):
            errors["threshold1"] = "阈值1须为数字"
        if "阈值1,2" in judge_rule and (not threshold2 or not _is_number(threshold2)):
            errors["threshold2"] = "阈值2须为数字"
        branch = {
            "process_params": json.dumps(process_params, ensure_ascii=False) or None,
            "judge_param": judge_param or None,
            "judge_rule": judge_rule or None,
            "threshold1": threshold1 or None,
            "threshold2": threshold2 or None,
        }
    elif exception_type == "量测异常":
        measure_dim = (body.get("measure_dim") or "").strip()
        if measure_dim not in database.MEASURE_DIMS:
            errors["measure_dim"] = "请选择量测维度" + str(database.MEASURE_DIMS)
        branch = {"measure_dim": measure_dim or None}
    elif exception_type == "defect异常":
        defect_code = (body.get("defect_code") or "").strip()
        defect_size = (body.get("defect_size") or "").strip()
        defect_values = {r["value"] for r in database.list_configs("defect_code")}
        if defect_code not in defect_values:
            errors["defect_code"] = "请选择 defect code"
        branch = {
            "defect_code": defect_code or None,
            "defect_size": defect_size or None,
        }

    if errors:
        return jsonify(ok=False, errors=errors), 400

    data = {
        "site": site,
        "machine": machine,
        "main_process": json.dumps(main_process, ensure_ascii=False),
        "pfcd": json.dumps(pfcd, ensure_ascii=False),
        "start_time": start_time,
        "end_time": end_time,
        "exception_type": exception_type,
        "hold_flag": "否",
        "event_description": (body.get("event_description") or "").strip(),
        "event_status": "open",
        **branch,
    }
    task_id = database.create_task(data)
    _save_input(task_id, data)

    # 自动生成报告（有对应工作流才触发）
    task = database.get_task(task_id)
    if not _has_valid_workflow(task):
        print(f"[api/submit] 任务 #{task_id} ({task.get('exception_type')}/{task.get('measure_dim')}) 无对应工作流，跳过报告生成")
        return jsonify(
            ok=True,
            task_id=task_id,
            report_url=None,
            message=f"任务 #{task_id} 已创建（{task.get('exception_type')}/{task.get('measure_dim')} 暂无对应工作流，未生成报告）",
        ), 201

    engine = _get_engine(task)
    try:
        engine.generate_report(task)
    except engine.GenerationCancelled:
        return jsonify(ok=False, error="报告生成被取消"), 409
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify(ok=False, error=f"报告生成失败: {e}", task_id=task_id, report_url=f"/tasks/{task_id}/report"), 500

    report_url = f"/tasks/{task_id}/report"
    return jsonify(
        ok=True,
        task_id=task_id,
        report_url=report_url,
        message=f"任务 #{task_id} 已创建，报告已生成",
    ), 201


# ---------- 工具 API ----------

@app.get("/api/config/<kind>")
def api_config(kind):
    """查询配置表。

    支持三种: pfcd / process_param / defect_code
    """
    if kind not in database.CONFIG_TABLES:
        return jsonify(ok=False, error=f"不支持配置类型: {kind}, 支持: {list(database.CONFIG_TABLES.keys())}"), 400
    rows = database.list_configs(kind)
    return jsonify(ok=True, kind=kind, count=len(rows), data=[r["value"] for r in rows])


@app.get("/api/tasks")
def api_tasks():
    """查询任务列表（带筛选）。

    支持字段: status, site, machine, exception_type, event_status,
              date_from, date_to, keyword, limit
    返回不含大量 data4/data3 等缓存字段的轻量列表。
    """
    tasks = _query_from_args(request.args)
    # 精简字段，去掉不需要的
    result = []
    for t in tasks:
        report_url = f"/tasks/{t['id']}/report"
        result.append({
            "id": t["id"],
            "site": t["site"],
            "machine": t["machine"],
            "main_process": t.get("main_process", ""),
            "pfcd": t.get("pfcd", ""),
            "start_time": t.get("start_time", ""),
            "end_time": t.get("end_time", ""),
            "exception_type": t.get("exception_type", ""),
            "event_description": t.get("event_description", ""),
            "event_status": t.get("event_status", "open"),
            "status": t.get("status", ""),
            "created_at": t.get("created_at", ""),
            "report_url": report_url,
        })
    return jsonify(ok=True, count=len(result), tasks=result)


@app.get("/api/tasks/<int:task_id>")
def api_task_detail(task_id):
    """查询单个任务详情。"""
    t = database.get_task(task_id)
    if t is None:
        return jsonify(ok=False, error="任务不存在"), 404
    result = {
        "id": t["id"],
        "site": t["site"],
        "machine": t["machine"],
        "main_process": t.get("main_process", ""),
        "pfcd": t.get("pfcd", ""),
        "start_time": t.get("start_time", ""),
        "end_time": t.get("end_time", ""),
        "exception_type": t.get("exception_type", ""),
        "event_description": t.get("event_description", ""),
        "event_status": t.get("event_status", "open"),
        "status": t.get("status", ""),
        "created_at": t.get("created_at", ""),
        "started_at": t.get("started_at", ""),
        "finished_at": t.get("finished_at", ""),
        "process_params": t.get("process_params", ""),
        "judge_param": t.get("judge_param", ""),
        "judge_rule": t.get("judge_rule", ""),
        "threshold1": t.get("threshold1", ""),
        "threshold2": t.get("threshold2", ""),
        "measure_dim": t.get("measure_dim", ""),
        "defect_code": t.get("defect_code", ""),
        "defect_size": t.get("defect_size", ""),
        "error": t.get("error", ""),
        "report_url": f"/tasks/{task_id}/report",
    }
    return jsonify(ok=True, task=result)


@app.get("/api/hold/query")
def api_hold_query():
    """查询 Hold 货数据（透明代理 fab API）。

    本接口固定请求体和 URL，调用方无需传参。
    内部流程: 获取 token → POST fab Hold 查询 API → 透传原始结果。
    """
    try:
        token = report_engine._get_fab_token()
    except Exception as e:
        return jsonify(ok=False, error=f"获取 token 失败: {e}"), 502

    try:
        body = {
            "site": "DEMO-A,DEMO-B",
            "department": "DEMO-DEPT",
        }
        resp = report_engine._do_post(
            "/api/ims-engboard-service/statisticalData/queryProductHold",
            body, token=token,
        )
        return jsonify(ok=True, data=resp)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify(ok=False, error=f"查询 Hold 数据失败: {e}"), 502


@app.get("/tasks/<int:task_id>")
def task_detail(task_id):
    task = database.get_task(task_id)
    if task is None:
        return (
            render_template_string(
                ERROR_PAGE, message="任务不存在或已删除。", back_url="/"
            ),
            404,
        )
    detail_html = render_template_string(DETAIL_FRAGMENT, **_detail_context(task))
    return render_template_string(DETAIL_PAGE, task=task, detail_html=detail_html)


@app.get("/partial/tasks")
def partial_tasks():
    """列表片段（5 秒轮询）：只替换任务表容器，筛选条件复用 URL 查询参数。"""
    return render_template_string(
        LIST_FRAGMENT,
        tasks=_query_from_args(request.args),
        status_labels=STATUS_LABELS,
    )


@app.get("/partial/tasks/<int:task_id>")
def partial_task_detail(task_id):
    """详情片段（3 秒轮询）：任务不存在返回 404（前端据此停轮询）。"""
    task = database.get_task(task_id)
    if task is None:
        return "", 404
    return render_template_string(DETAIL_FRAGMENT, **_detail_context(task))


@app.post("/tasks/<int:task_id>/delete")
def task_delete(task_id):
    """删除任务：先删 DB 行（running 拒绝），再删文件目录（DB 事务外，失败容忍）。"""
    task = database.get_task(task_id)
    if task is None:
        return jsonify(ok=False, message="任务不存在"), 404
    if not database.delete_task(task_id):
        return jsonify(ok=False, message="任务正在处理中，禁止删除"), 409
    try:
        shutil.rmtree(os.path.join(database.TASKS_DIR, str(task_id)))
    except OSError as e:
        print(f"删除任务 #{task_id} 目录失败（忽略）: {e}")
    return jsonify(ok=True, message="已删除")


@app.post("/tasks/<int:task_id>/retry")
def task_retry(task_id):
    """失败任务重新入队：failed→pending；run.log 续写标记；删除旧 result.json。"""
    if not database.retry_task(task_id):
        task = database.get_task(task_id)
        if task is None:
            return jsonify(ok=False, message="任务不存在"), 404
        return jsonify(ok=False, message="仅失败状态的任务可重新入队"), 409
    task_dir = os.path.join(database.TASKS_DIR, str(task_id))
    _append_line(os.path.join(task_dir, "run.log"), "===== 用户触发重新入队 =====")
    try:
        os.remove(os.path.join(task_dir, "result.json"))
    except OSError:
        pass
    return jsonify(ok=True, message="已重新入队")


@app.post("/tasks/<int:task_id>/toggle-status")
def task_toggle_status(task_id):
    """切换事件状态：open ↔ close"""
    conn = database.get_connection()
    try:
        task = database.get_task(task_id)
        if task is None:
            return jsonify(ok=False, message="任务不存在"), 404
        new_status = "close" if task.get("event_status") == "open" else "open"
        conn.execute(
            "UPDATE tasks SET event_status = ? WHERE id = ?",
            (new_status, task_id),
        )
        conn.commit()
        return jsonify(ok=True, event_status=new_status)
    except Exception as e:
        return jsonify(ok=False, message=str(e)), 500
    finally:
        conn.close()


# ---------- 配置管理路由 ----------


@app.get("/config")
def config_page():
    """配置页：三 tab 一次渲染（PFCD / 制程参数 / defect code）。"""
    panels = []
    for kind, label in CONFIG_TAB_INFO:
        rows = database.list_configs(kind)
        tbody = render_template_string(CONFIG_FRAGMENT, rows=rows)
        panels.append((kind, label, tbody))
    return render_template_string(CONFIG_PAGE, panels=panels)


@app.get("/partial/config/<kind>")
def partial_config(kind):
    """某配置表的 tbody 片段（操作成功后局部刷新）。"""
    if kind not in database.CONFIG_TABLES:
        return "", 404
    rows = database.list_configs(kind)
    return render_template_string(CONFIG_FRAGMENT, rows=rows)


def _config_payload() -> tuple:
    """解析配置接口的 JSON 请求体，返回 (value, value_id, ok)。"""
    payload = request.get_json(silent=True) or {}
    try:
        value_id = int(payload.get("id"))
    except (TypeError, ValueError):
        value_id = None
    return payload.get("value") or "", value_id


@app.post("/config/<kind>/add")
def config_add(kind):
    if kind not in database.CONFIG_TABLES:
        return jsonify(ok=False, message="未知配置类型"), 404
    value, _ = _config_payload()
    value = value.strip()
    if not value:
        return jsonify(ok=False, message="值不能为空"), 400
    if len(value) > 100:
        return jsonify(ok=False, message="值过长（最多 100 字符）"), 400
    if not database.add_config(kind, value):
        return jsonify(ok=False, message="该值已存在"), 409
    return jsonify(ok=True, message="已添加")


@app.post("/config/<kind>/edit")
def config_edit(kind):
    if kind not in database.CONFIG_TABLES:
        return jsonify(ok=False, message="未知配置类型"), 404
    value, value_id = _config_payload()
    value = value.strip()
    if value_id is None:
        return jsonify(ok=False, message="参数错误"), 400
    if not value:
        return jsonify(ok=False, message="值不能为空"), 400
    if len(value) > 100:
        return jsonify(ok=False, message="值过长（最多 100 字符）"), 400
    if not database.update_config(kind, value_id, value):
        return jsonify(ok=False, message="记录不存在或该值已存在"), 409
    return jsonify(ok=True, message="已保存")


@app.post("/config/<kind>/delete")
def config_delete(kind):
    if kind not in database.CONFIG_TABLES:
        return jsonify(ok=False, message="未知配置类型"), 404
    _, value_id = _config_payload()
    if value_id is None:
        return jsonify(ok=False, message="参数错误"), 400
    if not database.delete_config(kind, value_id):
        return jsonify(ok=False, message="记录不存在"), 404
    return jsonify(ok=True, message="已删除")


@app.post("/config/<kind>/import")
def config_import(kind):
    """批量粘贴导入：逐行解析 → 去空行。返回新增/跳过数量。

    文本内重复行不去重——交给 INSERT OR IGNORE 计数，保证「跳过重复」统计准确
    （同批内先出现的算新增，后出现的算跳过）。
    """
    if kind not in database.CONFIG_TABLES:
        return jsonify(ok=False, message="未知配置类型"), 404
    payload = request.get_json(silent=True) or {}
    text = payload.get("text") or ""
    values = []
    for line in text.splitlines():
        v = line.strip()
        if v and len(v) <= 100:
            values.append(v)
    if not values:
        return jsonify(ok=False, message="未解析到有效内容（每行一个值）"), 400
    added, skipped = database.import_configs(kind, values)
    return jsonify(
        ok=True, added=added, skipped=skipped, message=f"新增 {added} 条，跳过重复 {skipped} 条"
    )


# ===== 报告路由（按任务） =====

def _inject_report_csrf(html: str) -> str:
    """向 engine 生成的报告 HTML 注入 CSRF meta 和 fetch 包装器。"""
    csrf = session.get("csrf_token", "")
    if not csrf:
        return html
    meta_tag = f'<meta name="csrf-token" content="{csrf}">'
    # 在 <head> 之后注入 meta（如果有 <head>）
    if "<head>" in html:
        html = html.replace("<head>", "<head>\n" + meta_tag)
    elif "<html" in html:
        html = html.replace("<html", "<html>\n" + meta_tag, 1)
    else:
        html = meta_tag + "\n" + html

    # 在 </head> 前注入独立的 fetch 包装器 <script> 块
    # 注意：不能注入到已有 <script> 内部，否则嵌套 script 会导致浏览器解析失败
    csrf_js = """<script>
(function(){
  var meta=document.querySelector('meta[name="csrf-token"]');
  var csrf=meta?meta.content:'';
  if(!csrf)return;
  var _fetch=window.fetch;
  window.fetch=function(input,init){
    var url=typeof input==='string'?input:(input&&input.url?input.url:'');
    if(!url)return _fetch.call(this,input,init);
    if(url.indexOf('://')!==-1){
      var origin=location.origin;
      if(url.substring(0,origin.length)!==origin)return _fetch.call(this,input,init);
    }
    var method=(init&&init.method)||(input&&input.method)||'GET';
    if(/^(GET|HEAD|OPTIONS)$/i.test(method))return _fetch.call(this,input,init);
    init=init||{};
    var headers=new Headers(init.headers||{});
    if(!headers.has('X-CSRF-Token')){headers.set('X-CSRF-Token',csrf);}
    init.headers=headers;
    return _fetch.call(this,input,init);
  };
})();
</script>"""
    if "</head>" in html:
        html = html.replace("</head>", csrf_js + "\n</head>")
    elif "</body>" in html:
        html = html.replace("</body>", csrf_js + "\n</body>")
    else:
        html = html + "\n" + csrf_js
    return html


@app.get("/tasks/<int:task_id>/report")
def task_report(task_id):
    """查看任务的异常报告（只读，不自动触发生成）。"""
    task = database.get_task(task_id)
    if not task:
        return "任务不存在", 404

    engine = _get_engine(task)

    # 如果已有 state，用最新 state 重新渲染 HTML（确保 hold/做账/交接数据最新）
    state = engine.get_report_state(task_id)
    if state is not None:
        cache = engine._report_cache.get(task_id, {})
        task_info = {"id": task_id, "site": cache.get("site", ""), "machine": cache.get("machine", ""),
                     "start_time": cache.get("start_time", ""), "end_time": cache.get("end_time", "")}
        html = engine._build_html(task_info, state)
        engine._report_cache[task_id]["html"] = html
        return _inject_report_csrf(html)

    # 无缓存：返回提示页（不自动触发生成，需用户主动点击"生成报告"按钮）
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="csrf-token" content=""" + repr(session.get('csrf_token', '')) + """ >
<title>报告尚未生成</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0a0f1e;color:#e0e0e0;
display:flex;justify-content:center;align-items:center;min-height:100vh}
.card{background:#111827;border:1px solid #1e3a5f;border-radius:12px;
padding:40px;width:420px;max-width:90vw;text-align:center}
.card h2{color:#64b5f6;margin-bottom:16px;font-size:20px}
.card p{color:#90a4ae;margin-bottom:24px;font-size:14px;line-height:1.6}
.card .btn{display:inline-block;padding:10px 24px;background:#1e3a5f;color:#e0e0e0;
border:none;border-radius:6px;font-size:14px;cursor:pointer;text-decoration:none}
.card .btn:hover{background:#243b6e}
.card .back{margin-top:16px;font-size:13px}
.card .back a{color:#64b5f6;text-decoration:none}
</style>
</head>
<body>
<div class="card">
<h2>报告尚未生成</h2>
<p>任务 #""" + str(task_id) + """ 的报告数据尚未生成。<br>
请点击下方按钮触发报告生成。</p>
<button class="btn" onclick="generateReport()">生成报告</button>
<div class="back"><a href="/">← 返回任务列表</a></div>
</div>
<script>
async function generateReport() {
    const btn = event.target;
    btn.disabled = true;
    btn.textContent = '生成中…';
    try {
        const csrfMeta = document.querySelector('meta[name="csrf-token"]');
        const csrfVal = csrfMeta ? csrfMeta.content : '';
        const hdrs = {'Content-Type': 'application/json'};
        if (csrfVal) hdrs['X-CSRF-Token'] = csrfVal;
        const resp = await fetch('/api/report/""" + str(task_id) + """/generate', {method: 'POST', headers: hdrs});
        const data = await resp.json();
        if (data.ok) {
            location.reload();
        } else {
            alert('生成失败: ' + (data.error || '未知错误'));
            btn.disabled = false;
            btn.textContent = '生成报告';
        }
    } catch(e) {
        alert('请求失败: ' + e.message);
        btn.disabled = false;
        btn.textContent = '生成报告';
    }
}
</script>
</body>
</html>"""


@app.post("/api/report/<int:task_id>/generate")
def report_generate(task_id):
    """异步生成报告（从列表点击"报告"按钮调用）"""
    try:
        task = database.get_task(task_id)
        if not task:
            return jsonify({"ok": False, "error": "任务不存在"})
        engine = _get_engine(task)
        # 已有报告则直接返回
        existing = engine.get_report(task_id)
        if existing is not None:
            return jsonify({"ok": True, "len": len(existing), "cached": True})
        html = engine.generate_report(task)
        return jsonify({"ok": True, "len": len(html)})
    except cd_engine.GenerationCancelled:
        return jsonify({"ok": False, "error": "生成已被新请求取代，请重试"})
    except report_engine.GenerationCancelled:
        return jsonify({"ok": False, "error": "生成已被新请求取代，请重试"})
    except process_engine.GenerationCancelled:
        return jsonify({"ok": False, "error": "生成已被新请求取代，请重试"})
    except defect_engine.GenerationCancelled:
        return jsonify({"ok": False, "error": "生成已被新请求取代，请重试"})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/report/<int:task_id>/check")
def report_check(task_id):
    """检查报告是否已存在"""
    task = database.get_task(task_id)
    if not task:
        return jsonify({"exists": False})
    engine = _get_engine(task)
    return jsonify({"exists": engine.has_report(task_id)})


@app.post("/api/report/<int:task_id>/save")
def report_save(task_id):
    """保存报告：用缓存中的最新 state（含 hold/做账）重新生成 HTML"""
    try:
        task = database.get_task(task_id)
        engine = _get_engine(task) if task else report_engine

        data = request.get_json()
        notes = data.get("notes", "")

        # 先保存交接内容
        engine._save_notes(task_id, notes)

        # 检查缓存中是否有 state
        state = engine.get_report_state(task_id)
        if not state:
            return jsonify({"ok": False, "error": "报告数据不存在，请先生成报告"})

        # 从缓存获取 task 信息
        cache = engine._report_cache.get(task_id, {})
        task_info = {"id": task_id, "site": cache.get("site", ""), "machine": cache.get("machine", ""),
                     "start_time": cache.get("start_time", ""), "end_time": cache.get("end_time", "")}

        # 用最新 state 重新生成 HTML（_build_html 会自动加载 hold_data/posting_data）
        html_content = engine._build_html(task_info, state)

        # 写入磁盘
        path = engine._report_file(task_id)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_content)
        engine._report_cache[task_id]["html"] = html_content

        print(f"报告已保存（含最新 hold/做账/交接）: {path}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/report/<int:task_id>/regenerate")
def report_regenerate(task_id):
    """重新生成报告（覆盖现有，自动顶掉正在生成的旧任务）"""
    try:
        task = database.get_task(task_id)
        if not task:
            return jsonify({"ok": False, "error": "任务不存在"})
        engine = _get_engine(task)
        html = engine.generate_report(task)
        return jsonify({"ok": True, "len": len(html)})
    except cd_engine.GenerationCancelled:
        return jsonify({"ok": False, "error": "生成已被新请求取代，请重试"})
    except report_engine.GenerationCancelled:
        return jsonify({"ok": False, "error": "生成已被新请求取代，请重试"})
    except process_engine.GenerationCancelled:
        return jsonify({"ok": False, "error": "生成已被新请求取代，请重试"})
    except defect_engine.GenerationCancelled:
        return jsonify({"ok": False, "error": "生成已被新请求取代，请重试"})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/report/<int:task_id>/hold")
def report_hold(task_id):
    """执行 Hold 操作"""
    if IS_STAGING:
        return _staging_blocked("hold")
    try:
        task = database.get_task(task_id)
        engine = _get_engine(task) if task else report_engine
        data = request.get_json()
        glass_ids = data.get("glass_ids", [])
        hold_op = data.get("holdOperation", "")
        if not glass_ids:
            return jsonify({"ok": False, "error": "glass_ids 为空"})
        if hold_op not in engine.VALID_HOLD_OPS:
            return jsonify({"ok": False, "error": f"无效的 hold 站点: {hold_op}"})
        result = engine.do_hold(task_id, glass_ids, hold_op)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/report/<int:task_id>/cancelHold")
def report_cancel_hold(task_id):
    """取消 Hold 操作"""
    if IS_STAGING:
        return _staging_blocked("cancelHold")
    try:
        task = database.get_task(task_id)
        engine = _get_engine(task) if task else report_engine
        data = request.get_json()
        glass_ids = data.get("glass_ids", [])
        cancel_records = data.get("cancel_records", [])
        if not glass_ids:
            return jsonify({"ok": False, "error": "glass_ids 为空"})
        if not cancel_records:
            return jsonify({"ok": False, "error": "cancel_records 为空"})
        result = engine.do_cancel_hold(task_id, glass_ids, cancel_records)
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/report/<int:task_id>/holdSummary")
def report_hold_summary(task_id):
    """获取选中 glass 的 hold 记录汇总"""
    try:
        task = database.get_task(task_id)
        engine = _get_engine(task) if task else report_engine
        data = request.get_json()
        glass_ids = data.get("glass_ids", [])
        records = engine.get_hold_summary(task_id, glass_ids)
        return jsonify({"ok": True, "records": records})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/report/<int:task_id>/posting")
def report_posting(task_id):
    """执行做账操作"""
    if IS_STAGING:
        return _staging_blocked("posting")
    try:
        task = database.get_task(task_id)
        engine = _get_engine(task) if task else report_engine
        data = request.get_json()
        glass_ids = data.get("glass_ids", [])
        posting_ops = data.get("posting_ops", [])
        reason = data.get("reason", "")
        if not glass_ids or not posting_ops or not reason:
            return jsonify({"ok": False, "error": "参数不完整"})
        result = engine.do_posting(task_id, glass_ids, posting_ops, reason)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/report/<int:task_id>/color-mark")
def report_color_mark(task_id):
    """标记行颜色 / 清除颜色"""
    try:
        task = database.get_task(task_id)
        engine = _get_engine(task) if task else report_engine
        data = request.get_json()
        glass_ids = data.get("glass_ids", [])
        if not glass_ids:
            return jsonify({"ok": False, "error": "未提供 glass_ids"})
        if data.get("clear"):
            result = engine.clear_color_mark(task_id, glass_ids)
        else:
            color = data.get("color", "")
            if not color:
                return jsonify({"ok": False, "error": "未指定颜色"})
            result = engine.set_color_mark(task_id, glass_ids, color)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/report/<int:task_id>/analyze")
def report_analyze(task_id):
    """生成分析建议"""
    try:
        task = database.get_task(task_id)
        engine = _get_engine(task) if task else report_engine
        data = request.get_json() or {}
        filter_group = data.get("group") or None
        filter_recipe = data.get("recipe") or None
        state = engine.get_report_state(task_id)
        if not state:
            return jsonify({"ok": False, "error": "报告未生成"})
        if engine is cd_engine:
            return jsonify({"ok": False, "error": "CD 报告暂不支持 AI 分析建议"})
        html = engine._analyze_ovl(state, filter_group, filter_recipe)
        return jsonify({"ok": True, "html": html})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/report/<int:task_id>/refresh")
def report_refresh(task_id):
    """刷新报告数据"""
    try:
        task = database.get_task(task_id)
        engine = _get_engine(task) if task else report_engine
        result = engine.refresh_report(task_id)
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/report/<int:task_id>/excel")
def report_excel(task_id):
    """下载 Excel 报告：Sheet1=异常事件清单, Sheet2=量测详情（仅 OVL）"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    task = database.get_task(task_id)
    engine = _get_engine(task) if task else report_engine

    state = engine.get_report_state(task_id)
    if not state:
        return jsonify({"ok": False, "error": "报告未生成"}), 400

    base_rows = state.get("base_rows", [])

    wb = Workbook()

    # ---- Sheet1: 异常事件清单 ----
    ws1 = wb.active
    ws1.title = "异常事件清单"
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="0F3460", end_color="0F3460", fill_type="solid")
    risk_fill = PatternFill(start_color="FFE0E0", end_color="FFE0E0", fill_type="solid")

    if engine is cd_engine:
        param_names = state.get("param_names", [])
        headers1 = ["glass_id", "oper_code", "start_time", "line_id", "product_id",
                    "recipe_id", "group", "目前站点", "目前Lot_ID", "量测站点", "cst_id"] + param_names + ["Hold", "做账", "风险"]
    else:
        headers1 = ["glass_id", "oper_code", "start_time", "line_id", "product_id",
                    "recipe_id", "group", "目前站点", "目前Lot_ID", "EGAEXEC_MODE", "CVD_chamber", "量测站点", "cst_id", "Hold", "做账", "风险"]

    for col, h in enumerate(headers1, 1):
        cell = ws1.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for i, r in enumerate(base_rows, 2):
        is_risk = r.get("is_risk", False)
        vals = [str(r.get(h, "")) for h in headers1]
        # risk flag
        vals[-1] = "是" if is_risk else "否"
        for col, v in enumerate(vals, 1):
            cell = ws1.cell(row=i, column=col, value=v)
            if is_risk:
                cell.fill = risk_fill

    # 自适应列宽
    for col in range(1, len(headers1) + 1):
        max_len = len(str(ws1.cell(row=1, column=col).value or ""))
        for row in range(2, len(base_rows) + 2):
            val = str(ws1.cell(row=row, column=col).value or "")
            max_len = max(max_len, len(val))
        ws1.column_dimensions[ws1.cell(row=1, column=col).column_letter].width = min(max_len + 4, 40)

    # ---- Sheet2: 量测详情 ----
    ws2 = wb.create_sheet(title="量测详情")
    if engine is cd_engine:
        measure_by_gid = state.get("measure_by_gid", {})
        detail_rows = []
        for gid, records in measure_by_gid.items():
            for rec in records:
                detail_rows.append({
                    "glass_id": gid,
                    "param_name": rec.get("item3", ""),
                    "param_value": rec.get("item4", ""),
                    "spec": rec.get("item5", ""),
                    "site_no": rec.get("item6", ""),
                    "measure_time": rec.get("item7", ""),
                })
        headers2 = ["glass_id", "param_name", "param_value", "spec", "site_no", "measure_time"]
    else:
        detail_rows = state.get("ovl_detail", [])
        headers2 = ["glass_id", "oper_code", "site_no", "param_name", "param_value",
                    "measure_x", "measure_y", "eqp_id", "glass_judge", "start_time"]

    for col, h in enumerate(headers2, 1):
        cell = ws2.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for i, d in enumerate(detail_rows, 2):
        for col, h in enumerate(headers2, 1):
            ws2.cell(row=i, column=col, value=str(d.get(h, "")))

    for col in range(1, len(headers2) + 1):
        max_len = len(str(ws2.cell(row=1, column=col).value or ""))
        for row in range(2, len(detail_rows) + 2):
            val = str(ws2.cell(row=row, column=col).value or "")
            max_len = max(max_len, len(val))
        ws2.column_dimensions[ws2.cell(row=1, column=col).column_letter].width = min(max_len + 4, 40)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(output,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True,
                     download_name=f"异常报告_任务{task_id}.xlsx")


# ============================================================
# FastGPT / 飞书 Webhook 接口
# ============================================================

@app.post("/api/fastgpt/webhook")
def fastgpt_webhook():
    """
    FastGPT HTTP 插件入口。
    飞书 → FastGPT → 本机（此接口）

    认证：Bearer token，从 WEBHOOK_TOKEN 环境变量读取。
    未配置时返回 503（服务不可用），token 不匹配返回 403。

    接收 JSON:
      {"action": "ping" | "create_task" | "generate_report" | "list_tasks" | "get_report", ...}
    """
    # --- Bearer 认证 ---
    _webhook_token = os.environ.get("WEBHOOK_TOKEN", "").strip()
    if not _webhook_token:
        return jsonify({"ok": False, "error": "webhook 未配置（WEBHOOK_TOKEN 环境变量缺失）"}), 503

    auth_header = (request.headers.get("Authorization") or "").strip()
    if not auth_header.startswith("Bearer "):
        return jsonify({"ok": False, "error": "未授权：缺少 Bearer token"}), 403

    provided_token = auth_header[len("Bearer "):].strip()
    if not _hmac_module.compare_digest(provided_token, _webhook_token):
        return jsonify({"ok": False, "error": "未授权：token 不匹配"}), 403

    # --- staging 环境拒绝危险 action ---
    if IS_STAGING:
        # staging 仍允许只读 action（ping / list_tasks / get_report）
        body = request.get_json(force=True, silent=True) or {}
        action = body.get("action", "").strip()
        if action not in ("ping", "list_tasks", "get_report"):
            return _staging_blocked("FastGPT webhook " + action)
        return _fastgpt_dispatch(body)
    # --- 正式环境正常处理 ---
    try:
        data = request.get_json(force=True, silent=True) or {}
        return _fastgpt_dispatch(data)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)})


def _fastgpt_dispatch(data: dict):
    """处理 FastGPT webhook 请求（认证已通过）。"""
    action = data.get("action", "").strip()

    if action == "ping":
        return jsonify({"ok": True, "message": "pong", "data": {"status": "running"}})

    if action == "list_tasks":
        tasks = database.get_all_tasks()
        recent = [{"id": t["id"], "site": t.get("site",""), "measure_dim": t.get("measure_dim","OVL"),
                    "start_time": t.get("start_time",""), "end_time": t.get("end_time",""),
                    "status": t.get("status","")} for t in tasks[:20]]
        return jsonify({"ok": True, "message": f"共 {len(tasks)} 个任务，返回最近 20 条", "data": {"tasks": recent}})

    if action == "create_task":
        task_id = database.create_task(
            site=data.get("site", "DEMO-SITE-1"), machine=data.get("machine", "DEMO-M01"),
            start_time=data.get("start_time", ""), end_time=data.get("end_time", ""),
            measure_dim=data.get("measure_dim", "OVL"), main_process=data.get("main_process", ["3400"]),
            line_id=data.get("line_id", "DEMO-M01"), test_oper_code=data.get("test_oper_code", "3400"),
            test_eqp_type=data.get("test_eqp_type", "DEMO-PNK"), hold_site=data.get("hold_site", "DEMO-A"),
            cvd_chamber=data.get("cvd_chamber", 7),
        )
        return jsonify({"ok": True, "message": f"任务 #{task_id} 创建成功", "data": {"task_id": task_id}})

    if action == "generate_report":
        task_id = data.get("task_id")
        if not task_id: return jsonify({"ok": False, "error": "缺少 task_id"})
        task = database.get_task(task_id)
        if not task: return jsonify({"ok": False, "error": f"任务 #{task_id} 不存在"})
        engine = _get_engine(task)
        result = engine.generate_report(task)
        if result.get("ok"):
            return jsonify({"ok": True, "message": f"任务 #{task_id} 报告生成成功", "data": {"task_id": task_id}})
        return jsonify({"ok": False, "error": result.get("error", "生成失败")})

    if action == "get_report":
        task_id = data.get("task_id")
        if not task_id: return jsonify({"ok": False, "error": "缺少 task_id"})
        task = database.get_task(task_id)
        if not task: return jsonify({"ok": False, "error": f"任务 #{task_id} 不存在"})
        engine = _get_engine(task)
        state = engine.get_report_state(task_id)
        summary = {}
        if state:
            summary = {"total_glass": state.get("total_glass", 0),
                       "measured_glass": state.get("measured_glass", 0),
                       "risk_glass": state.get("risk_glass", 0)}
        return jsonify({"ok": True, "message": "报告状态", "data": {"has_report": engine.has_report(task_id), "summary": summary}})

    return jsonify({"ok": False, "error": f"未知 action: {action}"})


# ===================================================================
# 良率监控 API（ECPM 表格中触发）
# ===================================================================

_yield_rb = None  # yield_report_builder 模块缓存

def _get_yield_db():
    """延迟导入 monitor-platform 的 database 模块。"""
    import importlib.util
    _m_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "monitor-platform")
    _spec = importlib.util.spec_from_file_location(
        "monitor_database_yield", os.path.join(_m_dir, "database.py")
    )
    _mdb = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mdb)
    return _mdb


def _get_yield_report_builder():
    """延迟导入 monitor-platform 的 yield_report_builder 模块。"""
    global _yield_rb
    if _yield_rb is not None:
        return _yield_rb

    import importlib.util, sys
    _m_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "monitor-platform")

    # 确保 monitor-platform 目录在 sys.path 中（供 yield_chart_renderer 等子模块导入）
    if _m_dir not in sys.path:
        sys.path.insert(0, _m_dir)

    # yield_report_builder 内部 import database as db，但当前进程已加载
    # exception-platform/database.py 占用了 "database" 名字。
    # 策略：保存原 module → 替换为 monitor 版 → 加载 → 恢复原 module
    _saved_db = sys.modules.get("database")
    try:
        _db_spec = importlib.util.spec_from_file_location(
            "database", os.path.join(_m_dir, "database.py")
        )
        _db_mod = importlib.util.module_from_spec(_db_spec)
        sys.modules["database"] = _db_mod
        _db_spec.loader.exec_module(_db_mod)

        # 也确保 yield_chart_renderer 可导入
        _cr_spec = importlib.util.spec_from_file_location(
            "yield_chart_renderer", os.path.join(_m_dir, "yield_chart_renderer.py")
        )
        _cr = importlib.util.module_from_spec(_cr_spec)
        sys.modules["yield_chart_renderer"] = _cr
        _cr_spec.loader.exec_module(_cr)

        _rb_spec = importlib.util.spec_from_file_location(
            "yield_report_builder_live", os.path.join(_m_dir, "yield_report_builder.py")
        )
        _rb = importlib.util.module_from_spec(_rb_spec)
        _rb_spec.loader.exec_module(_rb)
        _yield_rb = _rb
        return _rb
    finally:
        if _saved_db is not None:
            sys.modules["database"] = _saved_db
        else:
            sys.modules.pop("database", None)


@app.post("/api/yield-monitor/start")
def yield_monitor_start():
    """启动良率监控任务。"""
    try:
        data = request.get_json(force=True) or {}
        ecpm_id = (data.get("ecpm_id") or "").strip()
        if not ecpm_id:
            return jsonify({"ok": False, "error": "缺少 ecpm_id"})

        ydb = _get_yield_db()

        # 检查是否已存在
        existing = ydb.get_yield_task(ecpm_id=ecpm_id)
        if existing:
            return jsonify({"ok": False, "error": "该 ECPM 已有监控任务（状态: %s）" % existing.get("status", "unknown")})

        # 获取 ECPM 信息 — 需要从 ecpm_workflow 或请求中获取
        machine = (data.get("machine") or data.get("line") or "").strip()
        title = (data.get("title") or "").strip()[:100]
        create_time = (data.get("create_time") or data.get("createTime") or "").strip()

        if not machine or not create_time:
            return jsonify({"ok": False, "error": "缺少 machine/line 或 create_time"})

        from datetime import datetime, timedelta
        now = datetime.now()
        create_dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                create_dt = datetime.strptime(create_time[:19], fmt)
                break
            except (ValueError, IndexError):
                continue
        if create_dt is None:
            create_dt = now

        fast_end = (create_dt + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        slow_end = (create_dt + timedelta(days=15)).strftime("%Y-%m-%d %H:%M:%S")

        task = ydb.create_yield_task(
            ecpm_id=ecpm_id,
            ecpm_title=title,
            machine=machine,
            create_time=create_time,
            fast_end_time=fast_end,
            slow_end_time=slow_end,
        )

        return jsonify({"ok": True, "task_id": task["id"]})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/yield-monitor/status")
def yield_monitor_status():
    """查询良率监控任务状态。"""
    try:
        ecpm_id = request.args.get("ecpm_id", "")
        task_id_str = request.args.get("task_id", "")

        ydb = _get_yield_db()

        if task_id_str:
            task = ydb.get_yield_task(task_id=int(task_id_str))
        elif ecpm_id:
            task = ydb.get_yield_task(ecpm_id=ecpm_id)
        else:
            return jsonify({"ok": False, "error": "需要 ecpm_id 或 task_id"})

        if not task:
            return jsonify({"ok": False, "error": "任务不存在"})

        snaps = ydb.get_snapshots(task["id"])
        fast_snaps = [s for s in snaps if s["snapshot_type"] == "fast"]
        slow_snaps = [s for s in snaps if s["snapshot_type"] == "slow"]

        return jsonify({
            "ok": True,
            "task": {
                "id": task["id"],
                "ecpm_id": task["ecpm_id"],
                "status": task["status"],
                "machine": task["machine"],
                "started_at": task["started_at"],
                "completed_at": task.get("completed_at"),
                "fast_count": len(fast_snaps),
                "slow_count": len(slow_snaps),
                "report_path": task.get("report_path"),
            },
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/yield-monitor/list")
def yield_monitor_list():
    """返回所有良率监控任务列表（含快照数），供前端轮询。"""
    try:
        ydb = _get_yield_db()
        tasks = ydb.get_all_yield_tasks(200)
        result = []
        for t in tasks:
            fast_snaps = ydb.get_snapshots(t["id"], "fast")
            slow_snaps = ydb.get_snapshots(t["id"], "slow")
            fast_ok = sum(1 for s in fast_snaps if s.get("status") == "success")
            slow_ok = sum(1 for s in slow_snaps if s.get("status") == "success")
            report_path = t.get("report_path", "")
            fn = os.path.basename(report_path) if report_path else ""
            result.append({
                "id": t["id"],
                "ecpm_title": t.get("ecpm_title", ""),
                "machine": t["machine"],
                "status": t["status"],
                "started_at": t.get("started_at", ""),
                "fast_count": len(fast_snaps),
                "fast_ok": fast_ok,
                "slow_count": len(slow_snaps),
                "slow_ok": slow_ok,
                "report_file": fn,
                "has_report": bool(report_path and os.path.exists(report_path)),
                "fast_snaps": [{
                    "run_at": s["run_at"],
                    "run_seq": s["run_seq"],
                    "before_matched": s.get("before_matched", 0),
                    "after_matched": s.get("after_matched", 0),
                    "density_hours": s.get("density_hours", 0),
                    "density_top10": s.get("density_top10", ""),
                    "status": s.get("status", ""),
                } for s in fast_snaps],
                "slow_snaps": [{
                    "run_at": s["run_at"],
                    "run_seq": s["run_seq"],
                    "yield_task_count": s.get("yield_task_count", 0),
                    "yield_result_count": s.get("yield_result_count", 0),
                    "yield_overall_json": s.get("yield_overall_json", ""),
                    "status": s.get("status", ""),
                } for s in slow_snaps],
            })
        return jsonify({"ok": True, "tasks": result})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/yield-monitor/snapshot/<int:task_id>/latest")
def yield_monitor_snapshot_latest(task_id):
    """返回任务最新 fast/slow 快照的完整数据，供前端渲染图表。"""
    try:
        ydb = _get_yield_db()
        task = ydb.get_yield_task(task_id=task_id)
        if not task:
            return jsonify({"ok": False, "error": "任务不存在"})

        fast_snaps = ydb.get_snapshots(task_id, "fast")
        slow_snaps = ydb.get_snapshots(task_id, "slow")

        import json as _json

        # 最新 fast 快照 — 取 full_result_json（含 charts + density）
        latest_fast = None
        fast_times = []
        fast_defect_history = []
        if fast_snaps:
            latest_fast = fast_snaps[-1]
            for s in fast_snaps:
                fast_times.append(s["run_at"])
                fast_defect_history.append({
                    "run_at": s["run_at"],
                    "before_matched": s.get("before_matched", 0),
                    "after_matched": s.get("after_matched", 0),
                    "density_top10": s.get("density_top10", ""),
                })

        result = {"ok": True, "task_id": task_id, "machine": task["machine"],
                  "ecpm_title": task.get("ecpm_title", ""), "status": task["status"]}

        # Fast 数据
        if latest_fast:
            full_json = latest_fast.get("full_result_json", "")
            if full_json:
                full = _json.loads(full_json)
                charts = full.get("charts", {})
                # charts 的 before/after 是坐标点列表 (x, y)，太大
                # 只传 before_count/after_count + layer/machine/code/level
                chart_summary = {}
                for key, ch in charts.items():
                    chart_summary[key] = {
                        "layer": ch.get("layer", ""),
                        "machine": ch.get("machine", ""),
                        "defect_code": ch.get("defect_code", ""),
                        "before_count": ch.get("before_count", 0),
                        "after_count": ch.get("after_count", 0),
                    }
                result["fast"] = {
                    "run_at": latest_fast["run_at"],
                    "before_matched": latest_fast.get("before_matched", 0),
                    "after_matched": latest_fast.get("after_matched", 0),
                    "density_hours": latest_fast.get("density_hours", 0),
                    "density_top10": _json.loads(latest_fast.get("density_top10", "[]")),
                    "chart_count": len(charts),
                    "chart_max_10": dict(list(chart_summary.items())[:30]),
                }
            result["fast_history"] = fast_defect_history
            result["fast_times"] = fast_times

        # Slow 数据
        if slow_snaps:
            ls = slow_snaps[-1]
            yd_json = ls.get("yield_data_json", "")
            result["slow"] = {
                "run_at": ls["run_at"],
                "yield_task_count": ls.get("yield_task_count", 0),
                "yield_result_count": ls.get("yield_result_count", 0),
                "yield_overall": _json.loads(ls.get("yield_overall_json", "{}")),
            }
            if yd_json:
                try:
                    yd = _json.loads(yd_json)
                    agg = yd.get("aggregated", {})
                    result["slow"]["sections"] = agg.get("overall", [])
                    result["slow"]["by_size"] = {k: v for k, v in agg.get("by_size", {}).items()}
                    result["slow"]["same_product"] = agg.get("same_product", [])
                    result["slow"]["overall_summary"] = agg.get("overall_summary", {})
                except Exception:
                    pass

            # 慢速历史（用于趋势图）
            slow_history = []
            for s in slow_snaps:
                slow_history.append({
                    "run_at": s["run_at"],
                    "yield_overall": _json.loads(s.get("yield_overall_json", "{}")),
                })
            result["slow_history"] = slow_history

        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/yield-monitor/report")
def yield_monitor_report():
    """获取良率监控报告。

    优先返回预生成的终期报告文件；若不存在则实时渲染。
    """
    try:
        task_id_str = request.args.get("task_id", "")

        ydb = _get_yield_db()

        if task_id_str:
            task_id = int(task_id_str)
            task = ydb.get_yield_task(task_id=task_id)
        else:
            return jsonify({"ok": False, "error": "需要 task_id"})

        if not task:
            return jsonify({"ok": False, "error": "任务不存在"})

        # 优先：已生成的终期报告文件
        rp = task.get("report_path", "")
        if rp and os.path.exists(rp):
            return send_file(rp, mimetype="text/html; charset=utf-8")

        # 兜底：实时渲染
        rb = _get_yield_report_builder()
        html = rb.render_live_report_html(task_id)
        if html is None:
            return jsonify({"ok": False, "error": "渲染失败"})
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)})


@app.get("/api/yield-monitor/report/<int:task_id>")
def yield_monitor_report_live(task_id):
    """实时生成单任务完整详情报告（HTML）。

    从快照数据实时渲染，返回完整 HTML 页面，浏览器可直接打开。
    与 /api/yield-monitor/report?task_id=X 的区别：
      - 后者：读取预生成的 report_path 文件（仅 completed 任务有）
      - 本接口：从快照表实时生成（running 任务也能查看最新数据）
    """
    try:
        rb = _get_yield_report_builder()
        html = rb.render_live_report_html(task_id)
        if html is None:
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/yield-monitor/chart/<int:task_id>/<path:filename>")
def yield_monitor_chart_image(task_id, filename):
    """服务 matplotlib 生成的良率监控图表 PNG 图片。

    图表文件位于 monitor-platform/output/yield_charts/task_{task_id}/ 目录。
    """
    import os as _os
    from flask import send_file

    _monitor_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "monitor-platform")
    _chart_dir = _os.path.join(_monitor_dir, "output", "yield_charts", f"task_{task_id}")
    _filepath = _os.path.join(_chart_dir, filename)

    # 安全检查：禁止路径穿越
    _filepath = _os.path.normpath(_filepath)
    _chart_dir_norm = _os.path.normpath(_chart_dir)
    if not _filepath.startswith(_chart_dir_norm):
        return jsonify({"ok": False, "error": "禁止的文件路径"}), 403

    if not _os.path.exists(_filepath):
        # 文件不存在时尝试动态生成图表
        try:
            rb = _get_yield_report_builder()
            task = _get_yield_db().get_yield_task(task_id=task_id)
            if task:
                fast_snaps = _get_yield_db().get_snapshots(task_id, "fast")
                slow_snaps = _get_yield_db().get_snapshots(task_id, "slow")
                # 触发图表生成
                from yield_chart_renderer import ensure_charts
                ensure_charts(task, fast_snaps, slow_snaps)
        except Exception as e:
            import traceback
            traceback.print_exc()

    if not _os.path.exists(_filepath):
        return jsonify({"ok": False, "error": "图表文件不存在"}), 404

    return send_file(_filepath, mimetype="image/png")


def init_and_run(port=15000, host="127.0.0.1", start_services=True):
    """初始化数据库与后台服务，然后启动 Flask。

    port/host: 监听地址（正式 15000 / 开发 15001，均仅本机）
    start_services: 是否启动告警轮询器 + 日常监控调度器（开发环境为 False）
    """
    _disable_console_quick_edit()
    database.init_db()

    # 初始化监控平台数据库
    import monitor_routes
    import importlib.util, os as _os
    _monitor_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "monitor-platform")
    _spec = importlib.util.spec_from_file_location("monitor_database", _os.path.join(_monitor_dir, "database.py"))
    _monitor_db = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_monitor_db)
    _monitor_db.init_db()
    _os.makedirs(monitor_routes.OUTPUT_DIR, exist_ok=True)

    if start_services:
        # 启动告警轮询器
        import alarm_poller as _alarm_poller_module
        _alarm_poller_module.init_poller()
        print("[web_app] 告警轮询器已启动")

        # 启动日常监控调度器（内嵌到 web 进程，避免独立 worker 进程挂掉无人知）
        # monitor_scheduler import database 时需要指向 monitor-platform 的 database，
        # 但 sys.modules['database'] 已被 web_app 的 import database 占用，所以临时替换
        _orig_db_module = sys.modules.get("database")
        sys.modules["database"] = _monitor_db
        try:
            _scheduler_spec = importlib.util.spec_from_file_location(
                "_monitor_scheduler", _os.path.join(_monitor_dir, "monitor_scheduler.py")
            )
            _monitor_scheduler = importlib.util.module_from_spec(_scheduler_spec)
            _scheduler_spec.loader.exec_module(_monitor_scheduler)
            _monitor_scheduler.seed_defaults()
            _monitor_scheduler.start()
        finally:
            if _orig_db_module is not None:
                sys.modules["database"] = _orig_db_module
            else:
                sys.modules.pop("database", None)
        print("[web_app] 日常监控调度器已启动（内嵌模式）")
    else:
        print("[web_app] DEV_MODE: 未启动后台服务（调度器/告警轮询器）")

    app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    init_and_run()
