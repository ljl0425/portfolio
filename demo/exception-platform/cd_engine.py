"""
CD 异常报告工作流引擎 v2.0 — 从头重建。

============================================================
步骤 1: 触发机制与关键信息拆解
============================================================
触发条件: exception_type == "量测异常" AND measure_dim == "CD"
分发位置: web_app.py → _get_engine(task) → 返回 cd_engine

操作:
  1. 从数据库查询任务，拆解所有关键字段为变量
  2. 时间格式转换: "2026-08-25T00:28" → "2026/08/25 00:28:00"

============================================================
步骤 2: 获取 Token（与 OVL 第一步完全相同）
============================================================
函数: _get_fab_token()
操作: POST {FAB_API_HOST}:{FAB_API_PORT} /api/v1/chat/completions
返回: Fab API Bearer Token

============================================================
步骤 3: 异步创建 15 个 EDC 查询条件
============================================================
- COT 参数 (1-9): testEqpType = ('DEMO-PTL','DEMO-PTL2')
- EXP 参数 (10-15): testEqpType = ('DEMO-PNK','DEMO-PNK2')
- 15 线程并发，每个返回一个 conditionId

============================================================
步骤 4: 异步分页捞取全部数据
============================================================
- 15 个 conditionId 并发翻页，直到取完所有数据

============================================================
步骤 5: 数据整合
============================================================
- 以 SCAN01_EXPENERGY 为母版
- 按 (item1, item2, item7±12h) 匹配
- 其他参数的 item4→列名, item6→值
- SCAN01 不存在的行跳过，重复匹配保留第一个
"""

import json, os, threading, ssl, http.client, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

# ===== SSL =====
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# ===== 常量 =====
API_KEY = os.environ.get("FAB_API_KEY", "")
HOLD_API_KEY = os.environ.get("HOLD_API_KEY", "")
DBQUERY_API_KEY = os.environ.get("DBQUERY_API_KEY", "")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflows")
os.makedirs(OUTPUT_DIR, exist_ok=True)
EVENT_USER = os.environ.get("EVENT_USER", "DEMO-USER")

# ===== CD 参数分类 =====
# COT 参数 (1-9): testEqpType = ('DEMO-PTL','DEMO-PTL2')
COT_PARAM_NAMES = [
    "DEV_ALKCNC", "DEV_CO2CNC", "QT_DEVIN_DEVOUT", "QT_EXPOUT_DEVIN",
    "DEV_RESCNC", "CT_MATERIALID", "QT_CTOUT_VCDIN", "QT_SBIN_SBOUT",
    "QT_SBOUT_EXPIN",
]

# EXP 参数 (10-15): testEqpType = ('DEMO-PNK','DEMO-PNK2') — 与 OVL 相同
EXP_PARAM_NAMES = [
    "SCAN01_EXPENERGY", "SCAN02_EXPENERGY", "SCAN03_EXPENERGY",
    "SCAN04_EXPENERGY", "SCAN05_EXPENERGY", "SCAN06_EXPENERGY",
]

# 全部 15 个参数（保持顺序）
ALL_PARAM_NAMES = COT_PARAM_NAMES + EXP_PARAM_NAMES

# 母版参数
MASTER_PARAM = "SCAN01_EXPENERGY"

# EDC 查询固定模板（与 OVL 共用）
RESULT_COLUMNS = [
    {"column_name": "glass_id", "column_desc": "GLASS_ID", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "oper_code", "column_desc": "OPER_CODE", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "end_time", "column_desc": "END_TIME", "column_define": "Date", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "param_name", "column_desc": "PARAM_NAME", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "site_no", "column_desc": "SITE_NO", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "param_value", "column_desc": "PARAM_VALUE", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "start_time", "column_desc": "START_TIME", "column_define": "Date", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "line_id", "column_desc": "LINE_ID", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "eqp_id", "column_desc": "EQP_ID", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "unit_id", "column_desc": "UNIT_ID", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "owner_type", "column_desc": "OWNER_TYPE", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "owner_code", "column_desc": "OWNER_CODE", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "product_kind", "column_desc": "PRODUCT_KIND", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "product_group", "column_desc": "PRODUCT_GROUP", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "product_id", "column_desc": "PRODUCT_ID", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "pp_id", "column_desc": "PP_ID", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "recipe_id", "column_desc": "RECIPE_ID", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "recipe_name", "column_desc": "RECIPE_NAME", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "recipe_ver", "column_desc": "RECIPE_VER", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "batch_id", "column_desc": "BATCH_ID", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "cst_id", "column_desc": "CST_ID", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "glass_judge", "column_desc": "GLASS_JUDGE", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "slot_id", "column_desc": "SLOT_NO", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "eqp_slot", "column_desc": "EQP_SLOT", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "type", "column_desc": "TYPE", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "operator_id", "column_desc": "OPERATOR_ID", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "run_times", "column_desc": "RUN_TIMES", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "item9", "column_desc": "TRACK_IN_TIME", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "last_run_flag", "column_desc": "LAST_RUN_FLAG", "column_define": "String", "table_name": "demo_edc_param_t", "default_flag": "Y"},
    {"column_name": "update_time", "column_desc": "UPDATE_TIME", "column_define": "Date", "table_name": "demo_edc_param_t", "default_flag": "Y"},
]

EDC_COMMON = {
    "associatedProcess": False, "dataLevel": None, "factory": "ARRAY",
    "ownerCode": "('')", "ownerType": "('')", "permissionId": "paramanalysis.edcparam",
    "processEqpType": None, "processFactory": None, "processOperCode": None,
    "productGroup": "('')", "site": [["DEMO-A", "DEMO-B"], ["DEMO-A", "DEMO-B"]],
    "userId": "DEMO-USER-ID", "resultColumns": RESULT_COLUMNS,
    "associatedResultColumns": [], "glassIdType": "TFT GLASS ID",
    "paramDesc": "", "paramType": "('')",
    "processLineId": "('')", "processEqpId": "('')", "processUnitId": "('')", "processSlotId": "('')",
    "productKind": "('')", "unitId": "('')", "eqpId": "('')",
    "keyParam": "all", "keyParamName": "('')", "rework": "3",
}

VALID_HOLD_OPS = ["3400", "4200", "4300", "4400", "5200", "5300", "5400", "5900", "T200", "T300", "9900"]

VALID_POSTING_OPS = [
    "1381", "1385", "1380", "1365", "1360", "1356", "1355", "1316", "1315", "1311", "1310",
    "2311", "2316", "2315", "2385", "238D", "2383", "2380", "2388", "2381", "2386",
    "3310", "3311", "3315", "3316", "3355", "3356", "3330", "3331", "3335", "3336", "3337",
    "3380", "3381", "3383", "3385", "338D", "3386",
    "4310", "4311", "4316", "4315", "4355", "4356", "4380", "4330", "4335", "4381", "4383", "4385", "438D", "4386",
    "5310", "5311", "5316", "5315", "5355", "5356", "5380", "5381", "5383", "5385", "538D", "5386",
    "1885", "1816", "1865", "2265", "3885", "3816", "3855", "3856", "5885", "5816",
]

POSTING_API_KEY = os.environ.get("POSTING_API_KEY", "")

# ===== 生成状态管理 =====
_gen_lock = threading.Lock()
_gen_version = {}
_report_cache = {}


class GenerationCancelled(Exception):
    pass


def _start_generation(task_id):
    with _gen_lock:
        v = _gen_version.get(task_id, 0) + 1
        _gen_version[task_id] = v
        return v


def _is_current(task_id, version):
    with _gen_lock:
        return _gen_version.get(task_id, 0) == version


# ===== 缓存持久化 =====
def _cache_file(task_id):
    return os.path.join(OUTPUT_DIR, f"cd_report_cache_{task_id}.json")


def _report_file(task_id):
    return os.path.join(OUTPUT_DIR, f"report_cd_task{task_id}.html")


def _save_cache(task_id):
    entry = _report_cache.get(task_id)
    if not entry:
        return
    cache_path = _cache_file(task_id)
    html_path = _report_file(task_id)

    # 将 state 中的 tuple key 转为 JSON 兼容的字符串 key
    import copy
    state = entry.get("state")
    json_state = None
    if state:
        json_state = copy.deepcopy(state)
        # spec_map: {(line, oper, pfcd, cd_param)} → spec_dict
        if "spec_map" in json_state:
            json_state["spec_map"] = {"|".join(k): v for k, v in json_state["spec_map"].items()}
        # line_chart_js: {pid: {cd_param: {dim: [...]}}} — 已经是 JSON 兼容
        # cd_contour_data: {(gid, cd_param)} → [...] — tuple key 需转换
        if "cd_contour_data" in json_state:
            json_state["cd_contour_data"] = {"|".join(k): v for k, v in json_state["cd_contour_data"].items()}

    cache_data = {
        "state": json_state,
        "glass_ids": entry.get("glass_ids"),
        "site": entry.get("site"),
        "main_process": entry.get("main_process"),
        "machine": entry.get("machine"),
        "start_time": entry.get("start_time"),
        "end_time": entry.get("end_time"),
    }
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)
    if entry.get("html"):
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(entry["html"])


def _load_cache(task_id):
    cache_path = _cache_file(task_id)
    html_path = _report_file(task_id)
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            cache_data = json.load(f)
        html = None
        if os.path.exists(html_path):
            with open(html_path, "r", encoding="utf-8") as f:
                html = f.read()
        _report_cache[task_id] = {
            "state": cache_data.get("state"),
            "html": html,
            "glass_ids": cache_data.get("glass_ids"),
            "site": cache_data.get("site"),
            "main_process": cache_data.get("main_process"),
            "machine": cache_data.get("machine"),
            "start_time": cache_data.get("start_time"),
            "end_time": cache_data.get("end_time"),
        }


# ===== Hold / 做账 / 备注持久化 =====
def _hold_file(task_id):
    return os.path.join(OUTPUT_DIR, f"cd_hold_results_{task_id}.json")


def _load_hold_data(task_id):
    path = _hold_file(task_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_hold_data(task_id, data):
    with open(_hold_file(task_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _posting_file(task_id):
    return os.path.join(OUTPUT_DIR, f"cd_posting_results_{task_id}.json")


def _load_posting_data(task_id):
    path = _posting_file(task_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_posting_data(task_id, data):
    with open(_posting_file(task_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _notes_file(task_id):
    return os.path.join(OUTPUT_DIR, f"cd_notes_{task_id}.json")


def _load_notes(task_id):
    path = _notes_file(task_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("notes", "")
    return ""


def _save_notes(task_id, content):
    path = _notes_file(task_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"notes": content}, f, ensure_ascii=False, indent=2)


def _color_file(task_id):
    return os.path.join(OUTPUT_DIR, f"cd_color_{task_id}.json")


def _load_color_data(task_id):
    path = _color_file(task_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_color_data(task_id, data):
    with open(_color_file(task_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ===== HTTP 工具函数 =====
def _do_post(url, body, token=None, extra_headers=None):
    conn = http.client.HTTPSConnection("szporos-fab.csot.tcl.com", timeout=60, context=ctx)
    headers = {"Content-Type": "application/json;charset=UTF-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    conn.request("POST", url, json.dumps(body, ensure_ascii=False).encode("utf-8"), headers)
    resp = conn.getresponse()
    data = json.loads(resp.read().decode())
    conn.close()
    return data


def _do_post_http(url, body, token=None):
    """HTTP 版本（非 HTTPS），用于规格 API"""
    conn = http.client.HTTPConnection("szporos-fab.csot.tcl.com", timeout=60)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn.request("POST", url, json.dumps(body, ensure_ascii=False).encode("utf-8"), headers)
    resp = conn.getresponse()
    data = json.loads(resp.read().decode())
    conn.close()
    return data


def _do_patch(url, body, api_key):
    conn = http.client.HTTPSConnection("szporos-fab.csot.tcl.com", timeout=60, context=ctx)
    headers = {"Content-Type": "application/json;charset=UTF-8", "apiKey": api_key}
    conn.request("PATCH", url, json.dumps(body, ensure_ascii=False).encode("utf-8"), headers)
    resp = conn.getresponse()
    data = json.loads(resp.read().decode())
    conn.close()
    return data


def _fetch_all_pages(condition_id, token, page_size=500):
    """循环翻页直到取完所有数据。

    注意：部分接口 countTotal 不准确（如 CVD 查询始终为 1），
    因此仅以空页和不足页判断终止，不信任 countTotal。
    """
    all_data = []
    page_index = 1
    max_pages = 200
    while page_index <= max_pages:
        body = {"conditionId": condition_id, "pageIndex": page_index, "pageSize": page_size, "dataSource": 1}
        resp = _do_post("/api/yms-analysis-service/showDataByPage/showDataByPage", body, token)
        rows = resp.get("data", {}).get("data", [])
        if len(rows) == 0:
            break
        all_data.extend(rows)
        if len(rows) < page_size:
            break
        page_index += 1
    return all_data, len(all_data)


# ============================================================
# 步骤 1: 触发机制与关键信息拆解
# ============================================================

def _format_time(s):
    """时间格式转换: 2026-08-25T00:28 → 2026/08/25 00:28:00"""
    if not s:
        return "Invalid date"
    s = s.replace("T", " ").replace("-", "/")
    if s.count(":") == 1:
        s += ":00"
    return s


# ============================================================
# 步骤 2: 获取 Token
# ============================================================

def _get_fab_token():
    """步骤 2: 获取 Fab API Token（与 OVL 第一步完全相同）"""
    conn = http.client.HTTPConnection(FAB_API_HOST, FAB_API_PORT, timeout=60)
    conn.request("POST", "/api/v1/chat/completions",
                 json.dumps({"messages": [{"role": "user", "content": "1"}], "stream": False}),
                 {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"})
    resp = conn.getresponse()
    data = json.loads(resp.read().decode())
    conn.close()
    return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()


# ============================================================
# 步骤 3: 异步创建 15 个 EDC 查询条件
# ============================================================

def _create_condition(param_name, test_eqp_type, start_full, end_full, test_oper_code, line_id, token):
    """为单个参数创建 EDC 查询条件，返回 (param_name, conditionId)"""
    edc_body = dict(EDC_COMMON)
    edc_body.update({
        "selectType": "TIME",
        "endTime": f"'{end_full}'",
        "startTime": f"'{start_full}'",
        "testEqpType": test_eqp_type,
        "testOperCode": test_oper_code,
        "productId": "('')",
        "lineId": line_id,
        "paramName": f"('{param_name}')",
    })
    try:
        r = _do_post("/api/yms-analysis-service/edcParam/edcParamQuery", edc_body, token)
        cid = r["data"]["conditionId"]
        result_data = r.get("data", {})
        total = result_data.get("countTotal", 0) or result_data.get("totalCount", 0) or result_data.get("total", 0) or "?"
        print(f"  [{param_name}] conditionId={cid[:20]}... total={total}")
        return param_name, cid, total
    except Exception as e:
        print(f"  [{param_name}] ❌ 创建失败: {e}")
        return param_name, None, 0


def _run_step_3(token, start_full, end_full, test_oper_code, line_id):
    """
    步骤 3: 异步创建 15 个 EDC 查询条件
    - COT 参数 (1-9): testEqpType = ('DEMO-PTL','DEMO-PTL2')
    - EXP 参数 (10-15): testEqpType = ('DEMO-PNK','DEMO-PNK2')
    返回: {param_name: conditionId}
    """
    print("=" * 70)
    print("步骤 3: 异步创建 15 个 EDC 查询条件")
    print("=" * 70)
    print(f"  start: {start_full}")
    print(f"  end:   {end_full}")
    print(f"  testOperCode: {test_oper_code}")
    print(f"  lineId: {line_id}")
    print()

    condition_ids = {}
    param_totals = {}

    with ThreadPoolExecutor(max_workers=15) as pool:
        futures = {}
        for pname in ALL_PARAM_NAMES:
            if pname in EXP_PARAM_NAMES:
                test_eqp_type = "('DEMO-PNK','DEMO-PNK2')"
            else:
                test_eqp_type = "('DEMO-PTL','DEMO-PTL2')"
            f = pool.submit(_create_condition, pname, test_eqp_type, start_full, end_full,
                           test_oper_code, line_id, token)
            futures[f] = pname

        for f in as_completed(futures):
            pname, cid, total = f.result()
            if cid:
                condition_ids[pname] = cid
            param_totals[pname] = total

    print()
    success = len(condition_ids)
    failed = len(ALL_PARAM_NAMES) - success
    print(f"  完成: {success} 成功, {failed} 失败")
    if failed:
        failed_names = [p for p in ALL_PARAM_NAMES if p not in condition_ids]
        print(f"  失败参数: {failed_names}")
    return condition_ids, param_totals


# ============================================================
# 步骤 4: 异步分页捞取全部数据
# ============================================================

def _fetch_param_data(param_name, condition_id, token):
    """为单个 conditionId 翻页捞取全部数据"""
    try:
        rows, total = _fetch_all_pages(condition_id, token)
        print(f"  [{param_name}] 捞取 {len(rows)} 条 (total={total})")
        return param_name, rows
    except Exception as e:
        print(f"  [{param_name}] ❌ 捞取失败: {e}")
        return param_name, []


def _run_step_4(token, condition_ids):
    """
    步骤 4: 异步分页捞取全部数据
    输入: {param_name: conditionId}
    输出: {param_name: [rows...]}
    """
    print("=" * 70)
    print("步骤 4: 异步分页捞取全部数据")
    print("=" * 70)

    all_data = {}

    with ThreadPoolExecutor(max_workers=15) as pool:
        futures = {}
        for pname, cid in condition_ids.items():
            f = pool.submit(_fetch_param_data, pname, cid, token)
            futures[f] = pname

        for f in as_completed(futures):
            pname, rows = f.result()
            all_data[pname] = rows

    print()
    total_rows = sum(len(v) for v in all_data.values())
    print(f"  完成: 共捞取 {total_rows} 条数据")
    for pname in ALL_PARAM_NAMES:
        count = len(all_data.get(pname, []))
        print(f"    {pname}: {count} 条")
    return all_data


# ============================================================
# 步骤 5: 数据整合
# ============================================================

def _parse_time(s):
    """尝试解析时间字符串，返回 datetime 对象"""
    if not s:
        return None
    for fmt in ["%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M"]:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _run_step_5(all_data):
    """
    步骤 5: 数据整合
    - 以 SCAN01_EXPENERGY 为母版（行骨架）
    - 按 (item1, item2, item7±12h) 匹配其他参数
    - 其他参数的 item4→列名, item6→值
    - 母版中不存在的行跳过，重复匹配保留第一个
    """
    print("=" * 70)
    print("步骤 5: 数据整合")
    print("=" * 70)

    master_rows = all_data.get(MASTER_PARAM, [])
    if not master_rows:
        print("  ❌ 母版参数 SCAN01_EXPENERGY 无数据，无法整合")
        return []

    print(f"  母版行数: {len(master_rows)}")

    # 为其他参数建索引: (item1, item2) → [(param_name, row), ...]
    # 不包括母版自身
    other_params = [p for p in ALL_PARAM_NAMES if p != MASTER_PARAM]
    other_index = {}
    for pname in other_params:
        rows = all_data.get(pname, [])
        for row in rows:
            key = (row.get("item1", ""), row.get("item2", ""))
            if key not in other_index:
                other_index[key] = []
            other_index[key].append((pname, row))

    print(f"  其他参数索引条目: {len(other_index)}")

    # 遍历母版行，整合
    merged_rows = []
    matched_count = 0
    skipped_no_match = 0

    for master_row in master_rows:
        item1 = master_row.get("item1", "")  # glass_id
        item2 = master_row.get("item2", "")  # oper_code
        item7 = master_row.get("item7", "")  # start_time

        key = (item1, item2)
        candidates = other_index.get(key, [])

        if not candidates:
            skipped_no_match += 1
            continue

        # 解析母版时间
        master_time = _parse_time(item7)

        # 构建整合行，以母版为基础
        merged = dict(master_row)
        # 母版自身的值：item6 → MASTER_PARAM 列
        merged[MASTER_PARAM] = master_row.get("item6", "")

        # 匹配其他参数
        matched_params = set()
        for pname, other_row in candidates:
            if pname in matched_params:
                continue  # 已匹配过，保留第一个

            # 时间差检查（12h内）
            if master_time:
                other_time = _parse_time(other_row.get("item7", ""))
                if other_time:
                    diff = abs((master_time - other_time).total_seconds())
                    if diff > 12 * 3600:
                        continue

            # 匹配成功：item4 → 列名, item6 → 值
            merged[pname] = other_row.get("item6", "")
            matched_params.add(pname)

        merged_rows.append(merged)
        matched_count += 1

    print(f"  整合完成: {len(merged_rows)} 行")
    print(f"  匹配成功: {matched_count}")
    print(f"  无匹配跳过: {skipped_no_match}")

    # 检查哪些参数没有被匹配到任何行
    all_matched_params = set()
    for row in merged_rows:
        for pname in ALL_PARAM_NAMES:
            if pname in row:
                all_matched_params.add(pname)
    unmatched_params = [p for p in ALL_PARAM_NAMES if p not in all_matched_params]
    if unmatched_params:
        print(f"  未匹配到任何行的参数: {unmatched_params}")

    # 统计每行匹配到的参数数
    param_counts = [sum(1 for p in ALL_PARAM_NAMES if p in row) for row in merged_rows]
    if param_counts:
        print(f"  每行参数数: min={min(param_counts)}, max={max(param_counts)}, avg={sum(param_counts)/len(param_counts):.1f}")

    return merged_rows


# ============================================================
# 步骤 9: 查询规格线
# ============================================================

def _fetch_spec(line, operation, pfcd, token):
    """查询单个 (line, operation, pfcd) 组合的规格线"""
    body = {
        "bizSite": "SiteDemoAB",
        "process": "DEMOARRAYEXPENG",
        "line": line,
        "mesSite": "",
        "operation": operation,
        "pfcd": pfcd,
        "itemName": "",
    }
    try:
        r = _do_post_http("/api/apc-service/spec/valueConfig/list", body, token)
        if r.get("success") and r.get("data"):
            return (line, operation, pfcd), r["data"]
        return (line, operation, pfcd), []
    except Exception as e:
        print(f"  [{line}/{operation}/{pfcd}] ❌ 规格查询失败: {e}")
        return (line, operation, pfcd), []


def _run_step_9(token, merged_rows):
    """
    步骤 9: 查询规格线
    - 从 merged_rows 提取唯一 (line, operation, pfcd) 组合
    - 异步查询每个组合的规格
    - 整理: itemName 去 _Avg → CD 参数名, column01=target, column02/03=均值规格...
    - 产出: spec_map + measured_cd_params
    """
    print("=" * 70)
    print("步骤 9: 查询规格线")
    print("=" * 70)

    # 提取唯一组合
    combos = set()
    for row in merged_rows:
        line = str(row.get("item8", ""))
        oper = str(row.get("item2", ""))
        pfcd = str(row.get("item15", ""))
        if line and oper and pfcd:
            combos.add((line, oper, pfcd))

    print(f"  唯一 (line, operation, pfcd) 组合: {len(combos)} 个")

    # 异步查询
    all_spec_data = {}
    with ThreadPoolExecutor(max_workers=min(len(combos), 10)) as pool:
        futures = {}
        for line, oper, pfcd in combos:
            f = pool.submit(_fetch_spec, line, oper, pfcd, token)
            futures[f] = (line, oper, pfcd)

        for f in as_completed(futures):
            key, data = f.result()
            if data:
                all_spec_data[key] = data

    print(f"  查询成功: {len(all_spec_data)}/{len(combos)} 个组合")

    # 整理规格数据
    spec_map = {}  # {(line, operation, pfcd, cd_param_name)} → spec_dict
    all_cd_params = set()

    for (line, oper, pfcd), items in all_spec_data.items():
        for item in items:
            item_name_raw = item.get("itemName", "")
            if not item_name_raw:
                continue
            # CD2_Avg → CD2
            cd_param = item_name_raw.replace("_Avg", "")
            all_cd_params.add(cd_param)

            spec_key = (line, oper, pfcd, cd_param)
            spec_map[spec_key] = {
                "target": item.get("column01", ""),
                "lsl_mean": item.get("column02", ""),
                "usl_mean": item.get("column03", ""),
                "lsl_mean_inner": item.get("column04", ""),
                "usl_mean_inner": item.get("column05", ""),
                "lsl_single": item.get("column08", ""),
                "usl_single": item.get("column09", ""),
                "lsl_single_inner": item.get("column10", ""),
                "usl_single_inner": item.get("column11", ""),
            }

    measured_cd_params = sorted(all_cd_params)
    print(f"  查到的 CD 参数: {measured_cd_params}")
    print(f"  规格条目总数: {len(spec_map)}")

    return spec_map, measured_cd_params


# ============================================================
# 量测参数常量
# ============================================================

CD_MEASURE_PARAM_NAMES = "('CD1','CD2','CD4','CD6','CD7','CD10','CD11','CD12')"

MEASURE_RESULT_COLUMNS = [
    {"column_name": "oper_code", "column_no": 5, "default_flag": "Y"},
    {"column_name": "glass_id", "column_no": 6, "default_flag": "Y"},
    {"column_name": "param_name", "column_no": 7, "default_flag": "Y"},
    {"column_name": "site_no", "column_no": 8, "default_flag": "Y"},
    {"column_name": "end_time", "column_no": 9, "default_flag": "Y"},
    {"column_name": "start_time", "column_no": 10, "default_flag": "Y"},
    {"column_name": "param_value", "column_no": 11, "default_flag": "Y"},
    {"column_name": "panel_id", "column_no": 12, "default_flag": "Y"},
    {"column_name": "panel_size", "column_no": 13, "default_flag": "Y"},
    {"column_name": "coordinate_x", "column_no": 14, "default_flag": "Y"},
    {"column_name": "coordinate_y", "column_no": 15, "default_flag": "Y"},
    {"column_name": "measure_x", "column_no": 16, "default_flag": "Y"},
    {"column_name": "measure_y", "column_no": 17, "default_flag": "Y"},
    {"column_name": "spec_high", "column_no": 18, "default_flag": "Y"},
    {"column_name": "spec_target", "column_no": 19, "default_flag": "Y"},
    {"column_name": "spec_low", "column_no": 20, "default_flag": "Y"},
    {"column_name": "shot", "column_no": 21, "default_flag": "Y"},
    {"column_name": "site_seq_in_shot", "column_no": 22, "default_flag": "Y"},
    {"column_name": "head_no", "column_no": 23, "default_flag": "Y"},
    {"column_name": "step_no", "column_no": 24, "default_flag": "Y"},
    {"column_name": "judge", "column_no": 25, "default_flag": "Y"},
    {"column_name": "camera_resolution", "column_no": 26, "default_flag": "Y"},
    {"column_name": "image_data", "column_no": 28, "default_flag": "Y"},
    {"column_name": "offset_a", "column_no": 29, "default_flag": "Y"},
    {"column_name": "offset_b", "column_no": 30, "default_flag": "Y"},
    {"column_name": "product_kind", "column_no": 31, "default_flag": "Y"},
    {"column_name": "product_group", "column_no": 32, "default_flag": "Y"},
    {"column_name": "product_id", "column_no": 33, "default_flag": "Y"},
    {"column_name": "line_id", "column_no": 34, "default_flag": "Y"},
    {"column_name": "eqp_id", "column_no": 35, "default_flag": "Y"},
    {"column_name": "unit_id", "column_no": 36, "default_flag": "Y"},
    {"column_name": "owner_type", "column_no": 37, "default_flag": "Y"},
    {"column_name": "owner_code", "column_no": 38, "default_flag": "Y"},
    {"column_name": "pp_id", "column_no": 39, "default_flag": "Y"},
    {"column_name": "recipe_name", "column_no": 40, "default_flag": "Y"},
    {"column_name": "recipe_id", "column_no": 41, "default_flag": "Y"},
    {"column_name": "cst_id", "column_no": 42, "default_flag": "Y"},
    {"column_name": "glass_judge", "column_no": 43, "default_flag": "Y"},
    {"column_name": "slot_id", "column_no": 44, "default_flag": "Y"},
    {"column_name": "group_id", "column_no": 45, "default_flag": "Y"},
    {"column_name": "run_times", "column_no": 49, "default_flag": "Y"},
    {"column_name": "last_run_flag", "column_no": 50, "default_flag": "Y"},
    {"column_name": "update_time", "column_no": 52, "default_flag": "Y"},
    {"column_name": "lot_id", "column_no": 53, "default_flag": "Y"},
]


# ============================================================
# 步骤 6: 创建量测查询条件（与 OVL 步骤 7 相同）
# ============================================================

def _build_measure_oper_code(main_process_list):
    """CD 量测站点推导规则：
    主制程 3xxx → 3380, 338D, 3385
    主制程 4xxx → 4380, 438D, 4385
    主制程 5xxx → 5380, 538D, 5385
    ALL → 全部 9 个
    """
    if not main_process_list or "ALL" in main_process_list:
        return "('338D','3380','3385','4380','438D','4385','5380','538D','5385')"
    codes = []
    for mp in main_process_list:
        prefix = mp[:2]
        for suffix in ("80", "8D", "85"):
            codes.append(f"'{prefix}{suffix}'")
    return "(" + ",".join(codes) + ")"


def _run_step_6(token, merged_rows, main_process, measured_cd_params):
    """
    步骤 6: 创建量测查询条件（与 OVL 步骤 7 相同）
    - URL: POST /api/yms-analysis-service/aSftpParam/newParamData
    - selectType: ID
    - measureEqpType: ('THCDO','TJCDO')
    - paramName: 动态，来自步骤 9 查到的 CD 参数
    - glassIds: 步骤5整合后的所有 item1
    """
    print("=" * 70)
    print("步骤 6: 创建量测查询条件（与 OVL 步骤 7 相同）")
    print("=" * 70)

    # 收集所有 glass_id
    glass_ids = list(set(row["item1"] for row in merged_rows if row.get("item1")))
    print(f"  glass_id 数量: {len(glass_ids)}")

    measure_oper_code = _build_measure_oper_code(main_process)
    dynamic_param_name = "('" + "','".join(measured_cd_params) + "')" if measured_cd_params else CD_MEASURE_PARAM_NAMES
    print(f"  measureOperCode: {measure_oper_code}")
    print(f"  paramName: {dynamic_param_name}")

    step6_body = {
        "associatedProcess": False,
        "permissionId": "paramanalysis.measureparam.dataqueryRowData",
        "userId": "DEMO-USER-ID",
        "site": [["DEMO-A", "DEMO-B"], ["DEMO-A", "DEMO-B"]],
        "factory": "ARRAY",
        "startTime": "'Invalid date'",
        "endTime": "'Invalid date'",
        "selectType": "ID",
        "timeType": "Inspect Time",
        "ownerType": "('')",
        "measureEqpType": "('THCDO','TJCDO')",
        "measureOperCode": measure_oper_code,
        "paramGroup": "('')",
        "paramName": dynamic_param_name,
        "eqpId": "('')",
        "processFactory": None,
        "dataLevel": None,
        "processEqpType": None,
        "processOperCode": None,
        "processLineId": None,
        "processEqpId": None,
        "processUnitId": None,
        "rework": 2,
        "associatedResultColumns": [],
        "resultColumns": MEASURE_RESULT_COLUMNS,
        "glassIdType": "TFT GLASS ID",
        "glassIds": glass_ids,
    }

    r = _do_post("/api/yms-analysis-service/aSftpParam/newParamData", step6_body, token)
    cid = r["data"]["conditionId"]
    total = r.get("data", {}).get("countTotal", 0) or r.get("data", {}).get("totalCount", 0) or "?"

    print(f"  conditionId: {cid[:40]}...")
    print(f"  预计总量: {total}")
    return cid, glass_ids


# ============================================================
# 步骤 7: 分页捞取量测数据（与 OVL 步骤 8 相同，不过滤 param_name）
# ============================================================

def _run_step_7(token, condition_id):
    """
    步骤 7: 分页捞取量测数据（与 OVL 步骤 8 相同）
    - 不过滤 param_name，全部保留
    - 返回 data8 原始列表
    """
    print("=" * 70)
    print("步骤 7: 分页捞取量测数据（与 OVL 步骤 8 相同）")
    print("=" * 70)

    rows, total = _fetch_all_pages(condition_id, token)
    print(f"  捞取 {len(rows)} 条 (total={total})")

    # 统计各 param_name 的数量
    param_counts = {}
    for row in rows:
        pname = str(row.get("item3", "") or row.get("item7", ""))
        param_counts[pname] = param_counts.get(pname, 0) + 1

    print(f"  量测参数分布:")
    for pname in sorted(param_counts.keys()):
        print(f"    {pname}: {param_counts[pname]} 条")

    return rows


# ============================================================
# 步骤 8: 查询当前状态信息（与 OVL 步骤 9 相同）
# ============================================================

def _run_step_8(merged_rows, site):
    """
    步骤 8: 查询 glass 当前状态信息（与 OVL 步骤 9 相同）
    - URL: PATCH /api/ai-studio-service/dbQuery/executeQuery/fed8cb7ccd4a454880a3f8f96a0e6089
    - 请求头: apiKey（非 Bearer Token）
    - 按 glass_id 分批查询，每批最多 200 个
    - 返回: PRODUCTNAME → {目前站点, 目前Lot_ID}
    """
    print("=" * 70)
    print("步骤 8: 查询当前状态信息（与 OVL 步骤 9 相同）")
    print("=" * 70)

    glass_ids = list(set(row["item1"] for row in merged_rows if row.get("item1")))
    print(f"  glass_id 数量: {len(glass_ids)}")
    print(f"  站点: {site}")

    BATCH_SIZE = 200
    all_data = []
    total_batches = (len(glass_ids) - 1) // BATCH_SIZE + 1

    for i in range(0, len(glass_ids), BATCH_SIZE):
        batch = glass_ids[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"  批次 {batch_num}/{total_batches}: {len(batch)} 个 glass...")

        body = {
            "params": {
                "site": site,
                "productNameList": batch,
                "lotName": "",
                "carrierName": ""
            },
            "site": site
        }
        resp = _do_patch(
            "/api/ai-studio-service/dbQuery/executeQuery/fed8cb7ccd4a454880a3f8f96a0e6089",
            body, DBQUERY_API_KEY
        )
        batch_data = resp.get("data", [])
        all_data.extend(batch_data)
        print(f"    返回 {len(batch_data)} 条")

    # 建索引: PRODUCTNAME → {目前站点, 目前Lot_ID}
    step8_map = {}
    for row in all_data:
        pn = row.get("PRODUCTNAME", "")
        if pn:
            step8_map[pn] = {
                "目前站点": row.get("PROCESSOPERATIONNAME", ""),
                "目前Lot_ID": row.get("LOTNAME", ""),
            }

    # 合并到 merged_rows
    for row in merged_rows:
        gid = row.get("item1", "")
        s8 = step8_map.get(gid, {})
        row["目前站点"] = s8.get("目前站点", "")
        row["目前Lot_ID"] = s8.get("目前Lot_ID", "")

    matched = sum(1 for row in merged_rows if row.get("目前站点"))
    print(f"  完成: 共 {len(all_data)} 条状态数据, {matched}/{len(merged_rows)} 行匹配到站点信息")
    return merged_rows


# ============================================================
# 步骤 10: 报告生成 — 数据组装
# ============================================================

def _run_step_10(merged_rows, measure_data, spec_map, measured_cd_params, task):
    """
    步骤 10: 数据组装
    - 从 merged_rows 构建 base_rows
    - 从 measure_data 计算 CD_AVG/MAX/MIN
    - 构建折线图数据（by product_id/oper_code/CD）
    - 构建等值线图数据（by glass_id/param_name）
    - 整合 spec_map
    """
    print("=" * 70)
    print("步骤 10: 数据组装")
    print("=" * 70)

    # ---- 1. 构建 base_rows ----
    base_rows = []
    for row in merged_rows:
        gid = row.get("item1", "")
        recipe = row.get("item17", "") or ""
        group = recipe[:4] if len(recipe) >= 4 else recipe
        base_rows.append({
            "glass_id": gid,
            "oper_code": row.get("item2", ""),
            "start_time": row.get("item7", ""),
            "line_id": row.get("item8", ""),
            "product_id": row.get("item15", ""),
            "recipe_id": recipe,
            "group": group,
            "目前站点": row.get("目前站点", ""),
            "目前Lot_ID": row.get("目前Lot_ID", ""),
            "量测站点": "",
            "cst_id": row.get("item21", ""),
            "hold": "",
            "做账": "",
            "is_risk": False,
            "has_measure": False,
        })

    # 按 start_time 降序
    base_rows.sort(key=lambda r: r["start_time"], reverse=True)

    # ---- 2. 从 measure_data 计算 CD_AVG/MAX/MIN ----
    # 实际 API 返回字段映射:
    # item2=glass_id, item3=param_name, item7=param_value
    # item12=coordinate_x, item13=coordinate_y, item10=measure_x, item11=measure_y
    # item1=oper_code, item4=site_no, item5=end_time, item6=start_time
    # item28=product_id, item29=line_id, item30=eqp_id, item37=cst_id
    # item36=recipe_id, item38=glass_judge, item24=offset_a(量测图片地址)
    cd_measure_map = {}  # {(glass_id, cd_param)} → [values]
    cd_contour_data = {}  # {(glass_id, cd_param)} → [{x, y, value}, ...]

    # 量测站点映射: glass_id → set of oper_codes
    glass_msite = {}

    for row in measure_data:
        gid = str(row.get("item2", ""))
        pname = str(row.get("item3", ""))
        oper = str(row.get("item1", ""))

        if gid and oper:
            if gid not in glass_msite:
                glass_msite[gid] = set()
            glass_msite[gid].add(oper)

        # 只统计 CD 参数
        if pname in measured_cd_params:
            key = (gid, pname)
            try:
                val = float(row.get("item7", 0))
            except (ValueError, TypeError):
                continue
            if key not in cd_measure_map:
                cd_measure_map[key] = []
            cd_measure_map[key].append(val)

            # 等值线数据
            if key not in cd_contour_data:
                cd_contour_data[key] = []
            try:
                cx = float(row.get("item12", 0))
                cy = float(row.get("item13", 0))
            except (ValueError, TypeError):
                cx = 0
                cy = 0
            cd_contour_data[key].append({"x": cx, "y": cy, "value": val})

    # 计算 AVG/MAX/MIN
    cd_stats = {}  # {(glass_id, cd_param)} → {"avg": ..., "max": ..., "min": ...}
    for key, vals in cd_measure_map.items():
        if vals:
            cd_stats[key] = {
                "avg": round(sum(vals) / len(vals), 4),
                "max": round(max(vals), 4),
                "min": round(min(vals), 4),
            }

    # 合并到 base_rows
    # 建 merged_rows 索引: glass_id → row
    merged_by_gid = {}
    for mrow in merged_rows:
        gid = mrow.get("item1", "")
        if gid:
            merged_by_gid[gid] = mrow

    for r in base_rows:
        gid = r["glass_id"]
        gid_ms = glass_msite.get(gid, set())
        # 量测站点：过滤掉主制程站点（取 3xxx/4xxx/5xxx 之外的）
        other_sites = []
        for s in sorted(gid_ms):
            if len(s) >= 4 and s[:2] in ("13", "23", "33", "43", "53"):
                pass  # 这是主制程站点，跳过
            else:
                other_sites.append(s)
        r["量测站点"] = ",".join(other_sites) if other_sites else (",".join(sorted(gid_ms)) if gid_ms else "")

        # 标记是否有量测数据
        has_any = False
        for cd_param in measured_cd_params:
            stats = cd_stats.get((gid, cd_param))
            if stats:
                r[f"{cd_param}_AVG"] = stats["avg"]
                r[f"{cd_param}_MAX"] = stats["max"]
                r[f"{cd_param}_MIN"] = stats["min"]
                has_any = True
            else:
                r[f"{cd_param}_AVG"] = ""
                r[f"{cd_param}_MAX"] = ""
                r[f"{cd_param}_MIN"] = ""
        r["has_measure"] = has_any

        # 填充 SCAN 参数
        mrow = merged_by_gid.get(gid, {})
        for pname in EXP_PARAM_NAMES:
            r[pname] = mrow.get(pname, "") if mrow.get(pname) is not None else ""
        for pname in COT_PARAM_NAMES:
            r[pname] = mrow.get(pname, "") if mrow.get(pname) is not None else ""

    # ---- 3. 确定 CD 列顺序（有数据的在前，无数据的在后） ----
    cd_params_with_data = []
    cd_params_empty = []
    for cd_param in measured_cd_params:
        has_data = any(
            cd_stats.get((r["glass_id"], cd_param))
            for r in base_rows
        )
        if has_data:
            cd_params_with_data.append(cd_param)
        else:
            cd_params_empty.append(cd_param)

    cd_params_ordered = cd_params_with_data + cd_params_empty

    # ---- 4. 构建折线图数据（by product_id, AVG/MAX/MIN 分别画图, 每个 glass_id 一条线） ----
    # 结构: {product_id: {cd_param: {"AVG": [{glass_id, value}], "MAX": [...], "MIN": [...]}}}
    line_chart_js = {}
    for r in base_rows:
        pid = r["product_id"]
        gid = r["glass_id"]
        for cd_param in cd_params_with_data:
            stats = cd_stats.get((gid, cd_param))
            if not stats:
                continue
            if pid not in line_chart_js:
                line_chart_js[pid] = {}
            if cd_param not in line_chart_js[pid]:
                line_chart_js[pid][cd_param] = {"AVG": [], "MAX": [], "MIN": []}
            line_chart_js[pid][cd_param]["AVG"].append({"glass_id": gid, "value": stats["avg"]})
            line_chart_js[pid][cd_param]["MAX"].append({"glass_id": gid, "value": stats["max"]})
            line_chart_js[pid][cd_param]["MIN"].append({"glass_id": gid, "value": stats["min"]})

    # 收集所有 product_id（排序）
    line_product_ids = sorted(line_chart_js.keys())

    # ---- 4b. 构建参数趋势图数据（按数值范围分组，不同画板） ----
    # 结构: {group_name: {param_name: [{glass_id, start_time, value}]}}
    # 分组：SCAN 曝光能量 (~34-36), QT 低值 (~20-72), QT 中高值 (~97-174), QT 极高值 (~216-790)
    TREND_GROUPS = [
        ("曝光能量", ["SCAN01_EXPENERGY", "SCAN02_EXPENERGY", "SCAN03_EXPENERGY",
                       "SCAN04_EXPENERGY", "SCAN05_EXPENERGY", "SCAN06_EXPENERGY"]),
        ("QT 低值", ["QT_CTOUT_VCDIN", "QT_DEVIN_DEVOUT"]),
        ("QT 中高值", ["QT_EXPOUT_DEVIN", "QT_SBIN_SBOUT"]),
        ("QT 极高值", ["QT_SBOUT_EXPIN"]),
    ]
    param_trend_groups = {}  # {group_name: {param_name: [{glass_id, start_time, value}]}}
    for group_name, param_names in TREND_GROUPS:
        group_data = {}
        for pname in param_names:
            series = []
            for r in base_rows:
                raw = r.get(pname, "")
                if raw == "" or raw is None:
                    continue
                try:
                    val = float(raw)
                except (ValueError, TypeError):
                    continue
                series.append({
                    "glass_id": r["glass_id"],
                    "start_time": r["start_time"],
                    "value": val,
                })
            if series:
                series.sort(key=lambda x: x["start_time"])
                group_data[pname] = series
        if group_data:
            param_trend_groups[group_name] = group_data

    # ---- 5. 构建等值线图数据 ----
    # 默认取第一个有数据的 glass_id + 第一个有数据的 cd_param
    contour_default_glass = ""
    contour_default_param = ""
    for r in base_rows:
        if r["has_measure"]:
            contour_default_glass = r["glass_id"]
            break
    if contour_default_glass:
        for cd_param in cd_params_with_data:
            if cd_contour_data.get((contour_default_glass, cd_param)):
                contour_default_param = cd_param
                break

    # ---- 6. 风险判断 ----
    # 对每个 base_row，检查是否有 CD 值超出规格
    for r in base_rows:
        gid = r["glass_id"]
        line = r["line_id"]
        oper = r["oper_code"]
        pfcd = r["product_id"]
        is_risk = False
        for cd_param in cd_params_with_data:
            stats = cd_stats.get((gid, cd_param))
            if not stats:
                continue
            spec = spec_map.get((line, oper, pfcd, cd_param))
            if spec:
                try:
                    usl = float(spec.get("usl_mean", 0) or 0)
                    lsl = float(spec.get("lsl_mean", 0) or 0)
                    if usl and lsl:
                        if stats["avg"] > usl or stats["avg"] < lsl:
                            is_risk = True
                            break
                except (ValueError, TypeError):
                    pass
        r["is_risk"] = is_risk

    # ---- 7. 统计 ----
    total_glass = len(base_rows)
    measured_glass = sum(1 for r in base_rows if r["has_measure"])
    risk_glass = sum(1 for r in base_rows if r["is_risk"])

    # ---- 8. 站点分布柱状图数据 ----
    bar_data = {}
    for r in base_rows:
        site_name = r["目前站点"] or "(空)"
        bar_data[site_name] = bar_data.get(site_name, 0) + 1
    bar_labels = sorted(bar_data.keys())
    bar_values = [bar_data[k] for k in bar_labels]

    # ---- 9. 收集 groups 和 recipe_ids ----
    groups = {}
    recipe_ids = set()
    for r in base_rows:
        g = r["group"]
        rid = r["recipe_id"]
        recipe_ids.add(rid)
        if g not in groups:
            groups[g] = set()
        groups[g].add(rid)
    groups_sorted = {k: sorted(v) for k, v in sorted(groups.items())}
    recipe_ids_sorted = sorted(recipe_ids)

    # ---- 10. 构建 measure_detail ----
    cd_detail = []
    for row in measure_data:
        cd_detail.append({
            "glass_id": str(row.get("item2", "")),
            "oper_code": str(row.get("item1", "")),
            "param_name": str(row.get("item3", "")),
            "param_value": str(row.get("item7", "")),
            "site_no": str(row.get("item4", "")),
            "measure_x": str(row.get("item10", "")),
            "measure_y": str(row.get("item11", "")),
            "coordinate_x": str(row.get("item12", "")),
            "coordinate_y": str(row.get("item13", "")),
            "eqp_id": str(row.get("item30", "")),
            "glass_judge": str(row.get("item38", "")),
            "start_time": str(row.get("item6", "")),
            "量测图片地址": str(row.get("item24", "")),
        })

    print(f"  base_rows: {total_glass} 行")
    print(f"  有量测数据: {measured_glass} 片")
    print(f"  风险 glass: {risk_glass} 片")
    print(f"  CD 参数列: {cd_params_ordered}")
    print(f"  折线图分组: {len(line_product_ids)} 个 product")
    print(f"  等值线默认: glass={contour_default_glass}, param={contour_default_param}")

    return {
        "base_rows": base_rows,
        "cd_detail": cd_detail,
        "cd_params_ordered": cd_params_ordered,
        "cd_params_with_data": cd_params_with_data,
        "cd_params_empty": cd_params_empty,
        "spec_map": spec_map,
        "measured_cd_params": measured_cd_params,
        "line_chart_js": line_chart_js,
        "line_product_ids": line_product_ids,
        "param_trend_groups": param_trend_groups,
        "cd_contour_data": cd_contour_data,
        "contour_default_glass": contour_default_glass,
        "contour_default_param": contour_default_param,
        "total_glass": total_glass,
        "measured_glass": measured_glass,
        "risk_glass": risk_glass,
        "bar_labels": bar_labels,
        "bar_values": bar_values,
        "groups": groups_sorted,
        "recipe_ids": recipe_ids_sorted,
    }


# ============================================================
# IDW 插值算法（等值线图）
# ============================================================

def _idw_interpolate(points, grid_x, grid_y, power=2):
    """IDW 插值：对指定网格点计算插值结果。"""
    if not points:
        return None
    numerator = 0.0
    denominator = 0.0
    for pt in points:
        dx = grid_x - pt["x"]
        dy = grid_y - pt["y"]
        dist = (dx * dx + dy * dy) ** 0.5
        if dist < 1e-6:
            return pt["value"]
        w = 1.0 / (dist ** power)
        numerator += w * pt["value"]
        denominator += w
    return numerator / denominator if denominator else None


# ============================================================
# HTML 报告生成
# ============================================================

def _build_html(task, state):
    """生成 CD 异常报告 HTML。"""
    task_id = task["id"]
    site = task["site"]
    machine = task["machine"]
    start = task["start_time"]
    end = task["end_time"]
    base_rows = state["base_rows"]
    cd_detail = state["cd_detail"]
    cd_params_ordered = state["cd_params_ordered"]
    cd_params_with_data = state["cd_params_with_data"]
    cd_params_empty = state["cd_params_empty"]
    spec_map = state["spec_map"]
    measured_cd_params = state["measured_cd_params"]
    line_chart_js = state["line_chart_js"]
    line_product_ids = state["line_product_ids"]
    cd_contour_data = state["cd_contour_data"]
    contour_default_glass = state["contour_default_glass"]
    contour_default_param = state["contour_default_param"]
    total_glass = state["total_glass"]
    measured_glass = state["measured_glass"]
    risk_glass = state["risk_glass"]
    bar_labels = state["bar_labels"]
    bar_values = state["bar_values"]

    now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    hold_data = _load_hold_data(task_id)
    posting_data = _load_posting_data(task_id)
    color_data = _load_color_data(task_id)

    # ---- 构建 COLUMNS 列表 ----
    columns = [
        "glass_id", "oper_code", "start_time", "line_id", "product_id",
        "recipe_id", "group", "目前站点", "目前Lot_ID", "量测站点",
    ]
    for cd_param in cd_params_ordered:
        columns.extend([f"{cd_param}_AVG", f"{cd_param}_MAX", f"{cd_param}_MIN"])
    columns.extend(EXP_PARAM_NAMES)
    columns.extend(COT_PARAM_NAMES)
    columns.extend(["hold", "做账"])

    # ---- 构建 base_rows JS ----
    base_rows_js = []
    for r in base_rows:
        gid = r["glass_id"]
        hd = hold_data.get(gid, [])
        hold_display = " | ".join(
            [f"{h['operation']}{'✓' if h['status'] == 'ok' else '✗'}" for h in hd]
        ) if hd else ""
        entry = {
            "glass_id": gid,
            "oper_code": r["oper_code"],
            "start_time": r["start_time"],
            "line_id": r["line_id"],
            "product_id": r["product_id"] or "",
            "recipe_id": r["recipe_id"] or "",
            "group": r["group"] or "",
            "目前站点": r["目前站点"] or "",
            "目前Lot_ID": r["目前Lot_ID"] or "",
            "量测站点": r["量测站点"] or "",
            "cst_id": r["cst_id"] or "",
            "hold": hold_display,
            "做账": ",".join(posting_data.get(gid, [])),
            "has_measure": r["has_measure"],
            "is_risk": r["is_risk"],
            "color_mark": color_data.get(gid, ""),
        }
        for cd_param in cd_params_ordered:
            for suffix in ["_AVG", "_MAX", "_MIN"]:
                entry[f"{cd_param}{suffix}"] = r.get(f"{cd_param}{suffix}", "")
        for pname in EXP_PARAM_NAMES + COT_PARAM_NAMES:
            entry[pname] = r.get(pname, "")
        base_rows_js.append(entry)

    # ---- 构建 cd_detail JS ----
    cd_detail_js = []
    for d in cd_detail:
        cd_detail_js.append({
            "glass_id": d["glass_id"],
            "oper_code": d["oper_code"],
            "param_name": d["param_name"],
            "param_value": d["param_value"],
            "site_no": d["site_no"],
            "measure_x": d["measure_x"] or "",
            "measure_y": d["measure_y"] or "",
            "coordinate_x": d["coordinate_x"] or "",
            "coordinate_y": d["coordinate_y"] or "",
            "eqp_id": d["eqp_id"] or "",
            "glass_judge": d["glass_judge"] or "",
            "start_time": d["start_time"] or "",
            "量测图片地址": d["量测图片地址"] or "",
        })

    # ---- 等值线图 glass/param 列表 ----
    contour_glass_list = sorted(set(gid for (gid, _) in cd_contour_data.keys()))

    # 转为 JS 友好格式: {glass_id: {cd_param: [{x, y, value}]}}
    contour_js = {}
    for (gid, cd_param), pts in cd_contour_data.items():
        if gid not in contour_js:
            contour_js[gid] = {}
        contour_js[gid][cd_param] = pts

    # ---- 规格线 JS ----
    spec_js = {}
    for (line, oper, pfcd, cd_param), spec in spec_map.items():
        if cd_param not in spec_js:
            spec_js[cd_param] = {}
        key = f"{line}/{oper}/{pfcd}"
        spec_js[cd_param][key] = spec

    # 各 CD 参数规格线汇总（用于折线图）
    spec_line_data = {}
    for cd_param in cd_params_with_data:
        targets = []
        usls = []
        lsls = []
        for (line, oper, pfcd, cp), spec in spec_map.items():
            if cp == cd_param:
                t = spec.get("target", "")
                u = spec.get("usl_mean", "") or spec.get("usl_single", "")
                l = spec.get("lsl_mean", "") or spec.get("lsl_single", "")
                if t:
                    try:
                        targets.append(float(t))
                    except:
                        pass
                if u:
                    try:
                        usls.append(float(u))
                    except:
                        pass
                if l:
                    try:
                        lsls.append(float(l))
                    except:
                        pass
        spec_line_data[cd_param] = {
            "target": round(sum(targets) / len(targets), 4) if targets else None,
            "usl": round(sum(usls) / len(usls), 4) if usls else None,
            "lsl": round(sum(lsls) / len(lsls), 4) if lsls else None,
        }

    # ---- 构建 HTML ----
    # 构建表头
    th_html = ""
    for col in columns:
        th_html += f'<th>{col}</th>\n'
    filter_th_html = ""
    for col in columns:
        fid = col.replace("_", "-")
        filter_th_html += f'<th class="filter-th"><input id="f-{fid}" oninput="applyFilters()" placeholder="筛选..."></th>\n'

    # 构建 option 列表
    group_options = "\n".join(f'<option value="{g}">{g}</option>' for g in sorted(state.get("groups", {}).keys()))
    recipe_options = "\n".join(f'<option value="{r}">{r}</option>' for r in state.get("recipe_ids", []))

    # 折线图 CD 参数 selector options
    line_cd_options = "\n".join(f'<option value="{p}">{p}</option>' for p in cd_params_with_data)

    # 等值线图 selector options
    contour_glass_options = "\n".join(f'<option value="{g}">{g}</option>' for g in contour_glass_list)
    contour_param_options = "\n".join(f'<option value="{p}">{p}</option>' for p in cd_params_with_data)
    # 折线图 product_id options
    line_product_options = "\n".join(f'<option value="{p}">{p}</option>' for p in line_product_ids)

    # 参数趋势图分组 HTML（含 checkbox 筛选）
    trend_groups_html = ""
    trend_groups_js = state.get("param_trend_groups", {})
    group_css_idx = 0
    TREND_COLORS = [
        '#4fd1c5','#f87171','#fbbf24','#60a5fa','#a78bfa',
        '#fb923c','#34d399','#f472b6','#818cf8','#eab308',
        '#06b6d4','#ef4444','#22c55e','#c084fc','#64748b'
    ]
    for group_name, group_data in trend_groups_js.items():
        param_names = sorted(group_data.keys())
        # 构建 checkbox HTML
        cb_html = ""
        for pi, pname in enumerate(param_names):
            color = TREND_COLORS[pi % len(TREND_COLORS)]
            cb_html += f'<label style="display:inline-flex;align-items:center;margin-right:10px;font-size:11px;color:#666;cursor:pointer;"><input type="checkbox" class="trend-cb" data-group="{group_css_idx}" data-param="{pname}" checked onchange="drawParamTrendCharts()" style="margin-right:3px;"><span style="display:inline-block;width:10px;height:3px;background:{color};margin-right:3px;border-radius:1px;"></span>{pname}</label>'
        group_css_idx += 1
        trend_groups_html += f'''
    <div class="bar-chart-box">
      <h3>📊 参数趋势 — {group_name} <span style="font-size:10px;color:#999;font-weight:normal;">（框选左边列表自动筛选）</span></h3>
      <div style="display:flex;align-items:center;gap:4px;margin-bottom:6px;flex-wrap:wrap;">{cb_html}</div>
      <canvas id="param-trend-{group_css_idx - 1}"></canvas>
    </div>'''

    html = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>CD异常报告 — 任务 #""" + str(task_id) + r"""</title>
<style>
:root {
  --bg: #16213e; --bg-nav: #0f3460; --bg-tabs: #1a1a2e; --bg-header: #1a1a2e;
  --bg-stats: #2a2a3e; --bg-card: #1a1a2e;
  --text-primary: #e2e2ee; --text-secondary: #9a9ab0; --text-title: #4fd1c5;
  --accent: #4fd1c5; --accent-hover: #3dbdb2;
  --text-on-dark: #fff; --text-dark: #333;
  --bg-light: #fafafa; --bg-card-light: #fff; --bg-table-header: #f0f0f0;
  --border-color: #e0e0e0; --border-light: #ddd; --border-lighter: #eee;
  --nav-link: #a0b4cc; --nav-hover: #e94560;
  --modal-bg: #fff; --modal-title: #1a1a2e;
}
body.light {
  --bg: #f0f2f5; --bg-nav: #1890ff; --bg-tabs: #fff; --bg-header: #fff;
  --bg-stats: #e8e8e8; --bg-card: #fff;
  --text-primary: #333; --text-secondary: #666; --text-title: #1890ff;
  --accent: #1890ff; --accent-hover: #40a9ff;
  --text-on-dark: #fff; --text-dark: #333;
  --bg-light: #fafafa; --bg-card-light: #fff; --bg-table-header: #f0f0f0;
  --border-color: #e0e0e0; --border-light: #ddd; --border-lighter: #eee;
  --nav-link: #fff; --nav-hover: #ffccc7;
  --modal-bg: #fff; --modal-title: #333;
}
* { box-sizing:border-box; margin:0; padding:0; }
html, body { height:100%; font-family:"Microsoft YaHei",sans-serif; background:var(--bg); user-select:none; display:flex; flex-direction:column; overflow:hidden; }
.left { user-select:none; }
.navbar { display:flex; align-items:center; background:var(--bg-nav); padding:0 20px; height:44px; flex-shrink:0; }
.navbar a { color:var(--nav-link); font-size:13px; text-decoration:none; padding:0 14px; line-height:44px; }
.navbar a:hover { color:var(--nav-hover); }
.tabs { display:flex; background:var(--bg-tabs); padding:0 24px; align-items:center; flex-shrink:0; }
.tab-btn { padding:10px 20px; color:var(--text-secondary); font-size:13px; cursor:pointer; border:none; background:none; border-bottom:2px solid transparent; }
.tab-btn:hover { color:var(--text-primary); }
.tab-btn.active { color:var(--text-title); border-bottom-color:var(--text-title); }
.tab-spacer { flex:1; }
.btn-save { background:#f59e0b; color:#fff; border:none; border-radius:4px; padding:6px 14px; font-size:12px; cursor:pointer; margin-right:4px; }
.btn-save:hover { background:#d97706; }
.btn-refresh { background:#10b981; color:#fff; border:none; border-radius:4px; padding:6px 14px; font-size:12px; cursor:pointer; margin-right:4px; }
.btn-refresh:hover { background:#059669; }
.btn-theme { background:transparent; color:var(--text-secondary); border:1px solid var(--text-secondary); border-radius:4px; padding:4px 10px; font-size:12px; cursor:pointer; margin-right:4px; }
.btn-theme:hover { color:var(--text-primary); border-color:var(--text-primary); }
.header { background:var(--bg-header); color:var(--text-on-dark); padding:8px 24px 10px; display:flex; gap:16px; align-items:flex-start; flex-shrink:0; }
body.light .header { color:var(--text-dark); }
.header-main { flex:1; }
.header h1 { font-size:16px; color:var(--text-title); }
.header .info { font-size:11px; color:var(--text-secondary); margin-top:2px; line-height:1.6; }
.header .info b { color:var(--text-primary); }
body.light .header .info b { color:var(--text-dark); }
.header-notes { width:520px; flex-shrink:0; background:var(--bg-card-light); color:var(--text-dark); border-radius:6px; padding:8px; }
.header-notes h3 { font-size:12px; margin-bottom:4px; color:var(--text-dark); display:flex; justify-content:space-between; align-items:center; }
.header-notes [contenteditable] { width:100%; height:120px; overflow-y:auto; border:1px solid var(--border-light); border-radius:4px; padding:6px; font-size:11px; outline:none; background:var(--bg-light); }
.stats { display:flex; gap:12px; padding:8px 24px; background:var(--bg-stats); flex-wrap:wrap; flex-shrink:0; }
.stat-card { background:var(--bg-card); border-radius:6px; padding:8px 16px; text-align:center; min-width:80px; }
.stat-card .num { font-size:20px; font-weight:bold; }
.stat-card .num.normal { color:var(--text-title); }
.stat-card .num.warn { color:#fbbf24; }
.stat-card .num.danger { color:#f87171; }
.stat-card .label { font-size:10px; color:var(--text-secondary); margin-top:1px; }
.main { display:flex; flex:1; overflow:hidden; }
.left { width:55%; background:var(--bg-card-light); border-right:1px solid var(--border-color); display:flex; flex-direction:column; overflow:hidden; }
.left-header { padding:6px 16px; border-bottom:1px solid var(--border-color); display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:4px; }
.left-header h2 { font-size:14px; }
.btn { background:var(--accent); color:#fff; border:none; border-radius:4px; padding:5px 12px; font-size:11px; cursor:pointer; }
.btn:hover { background:var(--accent-hover); }
.btn-outline { background:var(--bg-card-light); color:var(--accent); border:1px solid var(--accent); }
.btn-outline:hover { background:#e8faf8; }
.btn-danger { background:#ef4444; color:#fff; border:none; border-radius:4px; padding:5px 12px; font-size:11px; cursor:pointer; }
.btn-danger:hover { background:#dc2626; }
.table-wrap { flex:1; overflow:auto; position:relative; }
table { width:100%; border-collapse:collapse; font-size:11px; table-layout:auto; }
thead th { background:var(--bg-table-header); position:sticky; top:0; padding:3px 6px; text-align:left; border-bottom:2px solid #d0d0d0; white-space:nowrap; z-index:2; }
thead th.filter-th { top:24px; z-index:1; padding:2px 4px; background:var(--bg-light); border-bottom:1px solid var(--border-light); }
thead th.filter-th input { width:100%; border:1px solid var(--border-light); border-radius:2px; padding:2px 4px; font-size:10px; }
td { padding:2px 6px; border-bottom:1px solid var(--border-lighter); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; user-select:text !important; }
tr:hover td { background:#f0f8ff; }
tr.measure td { color:#2196F3; }
tr.risk td { background:#fff3f3; }
tr.risk.measure td { color:#2196F3; background:#fff3f3; }
tr.selected td { background:#c8e6ff !important; }
tr.selected:hover td { background:#bbdefb !important; }
tr.selected.risk td { background:#ffcdd2 !important; }
tr[data-color] td { transition:background 0.2s; }
tr[data-color]:not(.selected) td { background:var(--row-color) !important; }
tr[data-color].selected td { background:var(--row-color) !important; filter:brightness(0.92); }
.selection-box { position:absolute; border:2px dashed var(--accent); background:rgba(79,209,197,0.1); pointer-events:none; z-index:99; display:none; }
.right { width:45%; padding:10px; overflow-y:auto; background:var(--bg-light); }
.chart-box { background:var(--bg-card-light); border:1px solid var(--border-color); border-radius:6px; padding:10px; margin-bottom:10px; }
.chart-box h3 { font-size:13px; margin-bottom:6px; color:var(--text-dark); }
.chart-box canvas { width:100%; height:260px; }
.chart-row { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
.chart-row .chart-box canvas { height:220px; }
.chart-row .chart-box h3 { font-size:12px; }
.bar-chart-box { background:var(--bg-card-light); border:1px solid var(--border-color); border-radius:6px; padding:10px; margin-bottom:10px; }
.bar-chart-box canvas { width:100%; height:240px; }
.spec-legend { font-size:10px; color:#999; margin-top:4px; line-height:1.6; }
.tab-content { display:none; flex:1; overflow:hidden; }
.tab-content.active { display:flex; flex-direction:column; }
.tab-content#tab-detail { background:#f5f5f5; overflow-y:auto; }
body.light .tab-content#tab-detail { background:#fafafa; }
.detail-table { padding:16px; background:var(--bg-card-light); }
.detail-table h2 { font-size:15px; margin-bottom:12px; }
.empty { color:#ccc; }
.modal-overlay { display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.5); z-index:9999; justify-content:center; align-items:center; }
.modal-overlay.show { display:flex; }
.modal { background:var(--modal-bg); border-radius:8px; padding:24px; width:480px; max-height:80vh; overflow-y:auto; box-shadow:0 20px 60px rgba(0,0,0,0.3); }
.modal h2 { font-size:16px; margin-bottom:16px; color:var(--modal-title); }
.modal label { display:block; font-size:12px; color:#666; margin-bottom:4px; margin-top:12px; }
.modal input, .modal select { width:100%; border:1px solid var(--border-light); border-radius:4px; padding:8px 12px; font-size:13px; }
.modal input:focus { border-color:var(--accent); outline:none; }
.modal .hint { font-size:10px; color:#999; margin-top:4px; }
.modal .lot-group { background:#f5f5f5; border-radius:4px; padding:8px 12px; margin:8px 0; font-size:12px; }
.modal .lot-group b { color:var(--modal-title); }
.modal .btn-row { display:flex; gap:8px; justify-content:flex-end; margin-top:20px; }
.modal .error { color:#ef4444; font-size:11px; margin-top:4px; display:none; }
.modal .result { font-size:11px; margin-top:4px; }
.modal .result .ok { color:#10b981; }
.modal .result .fail { color:#ef4444; }
.loading { display:none; position:fixed; top:50%; left:50%; transform:translate(-50%,-50%); background:rgba(0,0,0,0.8); color:#fff; padding:16px 24px; border-radius:8px; z-index:99999; font-size:14px; }
.loading.show { display:block; }
.chart-tooltip { display:none; position:fixed; background:rgba(0,0,0,0.85); color:#fff; padding:6px 10px; border-radius:4px; font-size:11px; line-height:1.5; pointer-events:none; z-index:10000; white-space:nowrap; }
th[draggable] { cursor:grab; user-select:none; }
th[draggable]:active { cursor:grabbing; }
th.drag-over { border-right:3px solid var(--accent) !important; }
</style>
</head>
<body>

<div class="loading" id="loading">处理中...</div>
<div class="chart-tooltip" id="chart-tooltip"></div>

<div class="navbar">
  <a href="/">任务列表</a>
  <a href="/config">配置管理</a>
  <a href="/tasks/""" + str(task_id) + r"""">← 返回任务详情</a>
</div>

<div class="tabs">
  <button class="tab-btn active" onclick="switchTab('list',event)">异常清单</button>
  <button class="tab-btn" onclick="switchTab('detail',event)">量测详情</button>
  <div class="tab-spacer"></div>
  <button class="btn-theme" onclick="toggleTheme()" id="btn-theme">☀️ 亮色</button>
  <button class="btn-refresh" onclick="doRefresh()">🔄 刷新</button>
  <button class="btn-save" onclick="doSave()">💾 保存页面</button>
</div>

<div class="header">
  <div class="header-main">
    <h1>异常处理报告 — 量测异常 / CD</h1>
    <div class="info">
      <b>任务编号:</b> #""" + str(task_id) + r""" &nbsp;|&nbsp; <b>站点:</b> """ + site + r""" &nbsp;|&nbsp; <b>机台:</b> """ + machine + r"""<br>
      <b>时间范围:</b> """ + start + r""" ~ """ + end + r""" &nbsp;|&nbsp; <b>主流程:</b> ALL<br>
      <b>生成时间:</b> """ + now_ts + r"""
    </div>
  </div>
  <div class="header-notes">
    <h3>📝 交接 <button class="btn" onclick="expandNotes()" style="font-size:11px;padding:2px 10px;">展开</button></h3>
    <div id="report-notes" contenteditable="true" placeholder="在此输入交接内容...">""" + _load_notes(task_id) + r"""</div>
  </div>
</div>

<div class="stats">
  <div class="stat-card"><div class="num normal" id="stat-total">""" + str(total_glass) + r"""</div><div class="label">总 Glass 数</div></div>
  <div class="stat-card"><div class="num warn" id="stat-measured">""" + str(measured_glass) + r"""</div><div class="label">有量测数据</div></div>
  <div class="stat-card"><div class="num danger" id="stat-risk">""" + str(risk_glass) + r"""</div><div class="label">风险 Glass</div></div>
  <div class="stat-card"><div class="num" style="color:#a78bfa;" id="stat-selected">0</div><div class="label">已选 Glass</div></div>
  <div class="stat-card" style="flex:2; text-align:left; min-width:250px;">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
      <label style="font-size:10px;color:#9a9ab0;">Group:</label>
      <select id="filter-group" onchange="onFilterChange()" style="background:#1a1a2e;color:#e2e2ee;border:1px solid #444;border-radius:3px;padding:2px 6px;font-size:11px;">
        <option value="">全部</option>
""" + group_options + r"""
      </select>
      <label style="font-size:10px;color:#9a9ab0;">Recipe:</label>
      <select id="filter-recipe" onchange="onFilterChange()" style="background:#1a1a2e;color:#e2e2ee;border:1px solid #444;border-radius:3px;padding:2px 6px;font-size:11px;">
        <option value="">全部</option>
""" + recipe_options + r"""
      </select>
    </div>
  </div>
</div>

<!-- ===== 异常清单 Tab ===== -->
<div class="tab-content active" id="tab-list">
<div class="main">
  <div class="left">
    <div class="left-header">
      <h2>异常事件清单（共 <span id="row-count">""" + str(total_glass) + r"""</span> 条）</h2>
      <div class="right-actions">
        <button class="btn btn-outline" onclick="selectAll()">全选</button>
        <button class="btn btn-outline" onclick="clearSelect()">取消选择</button>
        <button class="btn btn-outline" onclick="openColorModal()" style="color:#f59e0b;border-color:#f59e0b;">🎨 标记颜色</button>
        <button class="btn-danger" onclick="openHoldModal()">🛑 Hold</button>
        <button class="btn btn-outline" onclick="openCancelHoldModal()" style="color:#e94560;border-color:#e94560;">↩ 取消 Hold</button>
        <button class="btn-outline" onclick="openPostingModal()" style="background:#8b5cf6;color:#fff;border-color:#8b5cf6;">📋 做账</button>
        <button class="btn" onclick="downloadExcel()">📥 下载 Excel</button>
        <button class="btn btn-outline" onclick="resetColumnOrder()" style="font-size:11px;">📐 重置列</button>
      </div>
    </div>
    <div class="table-wrap" id="table-wrap">
      <div class="selection-box" id="selection-box"></div>
      <table id="main-table" style="min-width:2000px;">
        <thead>
          <tr>""" + th_html + r"""</tr>
          <tr>""" + filter_th_html + r"""</tr>
        </thead>
        <tbody id="table-body">
        </tbody>
      </table>
    </div>
  </div>
  <div class="right" id="chart-area">
    <div class="bar-chart-box">
      <h3>目前站点分布 — Glass 数量柱状图</h3>
      <canvas id="bar-chart"></canvas>
    </div>
    <div class="chart-row">
      <div class="chart-box">
        <h3>📈 CD 折线图 — AVG</h3>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap;">
          <label style="font-size:11px;color:#666;">Product:</label>
          <select id="line-prod-avg" onchange="drawAllLineCharts()" style="padding:3px 8px;font-size:11px;border-radius:3px;border:1px solid #ccc;">
            """ + line_product_options + r"""
          </select>
          <label style="font-size:11px;color:#666;">CD 参数:</label>
          <select id="line-cd-avg" onchange="drawAllLineCharts()" style="padding:3px 8px;font-size:11px;border-radius:3px;border:1px solid #ccc;">
            """ + line_cd_options + r"""
          </select>
        </div>
        <canvas id="line-chart-avg"></canvas>
        <div class="spec-legend" id="line-spec-avg"></div>
      </div>
      <div class="chart-box">
        <h3>📈 CD 折线图 — MAX</h3>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap;">
          <label style="font-size:11px;color:#666;">Product:</label>
          <select id="line-prod-max" onchange="drawAllLineCharts()" style="padding:3px 8px;font-size:11px;border-radius:3px;border:1px solid #ccc;">
            """ + line_product_options + r"""
          </select>
          <label style="font-size:11px;color:#666;">CD 参数:</label>
          <select id="line-cd-max" onchange="drawAllLineCharts()" style="padding:3px 8px;font-size:11px;border-radius:3px;border:1px solid #ccc;">
            """ + line_cd_options + r"""
          </select>
        </div>
        <canvas id="line-chart-max"></canvas>
        <div class="spec-legend" id="line-spec-max"></div>
      </div>
    </div>
    <div class="chart-row">
      <div class="chart-box">
        <h3>📈 CD 折线图 — MIN</h3>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap;">
          <label style="font-size:11px;color:#666;">Product:</label>
          <select id="line-prod-min" onchange="drawAllLineCharts()" style="padding:3px 8px;font-size:11px;border-radius:3px;border:1px solid #ccc;">
            """ + line_product_options + r"""
          </select>
          <label style="font-size:11px;color:#666;">CD 参数:</label>
          <select id="line-cd-min" onchange="drawAllLineCharts()" style="padding:3px 8px;font-size:11px;border-radius:3px;border:1px solid #ccc;">
            """ + line_cd_options + r"""
          </select>
        </div>
        <canvas id="line-chart-min"></canvas>
        <div class="spec-legend" id="line-spec-min"></div>
      </div>
    </div>
    <div class="chart-row">
      <div class="chart-box">
        <h3>🗺️ CD 等值线图 ① — IDW 插值</h3>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap;">
          <label style="font-size:11px;color:#666;">Glass:</label>
          <select id="contour-glass-1" onchange="drawContourChart(1)" style="padding:3px 8px;font-size:11px;border-radius:3px;border:1px solid #ccc;">
            """ + contour_glass_options + r"""
          </select>
          <label style="font-size:11px;color:#666;">CD 参数:</label>
          <select id="contour-param-1" onchange="drawContourChart(1)" style="padding:3px 8px;font-size:11px;border-radius:3px;border:1px solid #ccc;">
            """ + contour_param_options + r"""
          </select>
        </div>
        <canvas id="contour-chart-1"></canvas>
      </div>
      <div class="chart-box">
        <h3>🗺️ CD 等值线图 ② — IDW 插值</h3>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap;">
          <label style="font-size:11px;color:#666;">Glass:</label>
          <select id="contour-glass-2" onchange="drawContourChart(2)" style="padding:3px 8px;font-size:11px;border-radius:3px;border:1px solid #ccc;">
            """ + contour_glass_options + r"""
          </select>
          <label style="font-size:11px;color:#666;">CD 参数:</label>
          <select id="contour-param-2" onchange="drawContourChart(2)" style="padding:3px 8px;font-size:11px;border-radius:3px;border:1px solid #ccc;">
            """ + contour_param_options + r"""
          </select>
        </div>
        <canvas id="contour-chart-2"></canvas>
      </div>
    </div>
    <div class="chart-row">
      <div class="chart-box">
        <h3>🗺️ CD 等值线图 ③ — IDW 插值</h3>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap;">
          <label style="font-size:11px;color:#666;">Glass:</label>
          <select id="contour-glass-3" onchange="drawContourChart(3)" style="padding:3px 8px;font-size:11px;border-radius:3px;border:1px solid #ccc;">
            """ + contour_glass_options + r"""
          </select>
          <label style="font-size:11px;color:#666;">CD 参数:</label>
          <select id="contour-param-3" onchange="drawContourChart(3)" style="padding:3px 8px;font-size:11px;border-radius:3px;border:1px solid #ccc;">
            """ + contour_param_options + r"""
          </select>
        </div>
        <canvas id="contour-chart-3"></canvas>
      </div>
      <div class="chart-box">
        <h3>🗺️ CD 等值线图 ④ — IDW 插值</h3>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap;">
          <label style="font-size:11px;color:#666;">Glass:</label>
          <select id="contour-glass-4" onchange="drawContourChart(4)" style="padding:3px 8px;font-size:11px;border-radius:3px;border:1px solid #ccc;">
            """ + contour_glass_options + r"""
          </select>
          <label style="font-size:11px;color:#666;">CD 参数:</label>
          <select id="contour-param-4" onchange="drawContourChart(4)" style="padding:3px 8px;font-size:11px;border-radius:3px;border:1px solid #ccc;">
            """ + contour_param_options + r"""
          </select>
        </div>
        <canvas id="contour-chart-4"></canvas>
      </div>
    </div>""" + trend_groups_html + r"""
  </div>
</div>
</div>

<!-- ===== 量测详情 Tab ===== -->
<div class="tab-content" id="tab-detail">
  <div class="detail-table">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
      <h2 style="margin:0;">量测数据详情（共 """ + str(len(cd_detail)) + r""" 条）</h2>
    </div>
    <div style="overflow:auto; max-height:70vh;">
      <table><thead><tr>
        <th>glass_id</th><th>oper_code</th><th>param_name</th><th>param_value</th>
        <th>site_no</th><th>measure_x</th><th>measure_y</th>
        <th>coordinate_x</th><th>coordinate_y</th><th>eqp_id</th>
        <th>glass_judge</th><th>start_time</th><th>量测图片地址</th>
      </tr></thead>
      <tbody id="detail-body"></tbody></table>
    </div>
  </div>
</div>

<!-- ===== Hold 模态框 ===== -->
<div class="modal-overlay" id="hold-modal">
  <div class="modal">
    <h2>🛑 Hold 货操作</h2>
    <div id="hold-glass-info"></div>
    <label>Hold 站点</label>
    <input type="text" id="hold-operation" placeholder="请输入 hold 站点，如 3400">
    <div class="hint">合法值: """ + ", ".join(VALID_HOLD_OPS) + r"""</div>
    <div class="error" id="hold-error"></div>
    <div id="hold-lot-groups"></div>
    <div id="hold-result"></div>
    <div class="btn-row">
      <button class="btn btn-outline" onclick="closeHoldModal()">取消</button>
      <button class="btn-danger" id="hold-confirm-btn" onclick="doHold()">确认 Hold</button>
    </div>
  </div>
</div>

<!-- ===== 取消 Hold 弹窗 ===== -->
<div class="modal-overlay" id="cancel-hold-modal">
  <div class="modal" style="max-width:600px;">
    <h2>↩ 取消 Hold</h2>
    <div id="cancel-hold-glass-info"></div>
    <div style="margin:12px 0;">
      <label style="display:flex;align-items:center;margin-bottom:8px;cursor:pointer;">
        <input type="radio" name="cancel-mode" value="all" checked onchange="switchCancelMode()" style="margin-right:8px;">
        <span>取消全部 Hold</span>
      </label>
      <label style="display:flex;align-items:center;cursor:pointer;">
        <input type="radio" name="cancel-mode" value="partial" onchange="switchCancelMode()" style="margin-right:8px;">
        <span>取消指定 Hold</span>
      </label>
    </div>
    <div id="cancel-hold-list" style="max-height:250px;overflow-y:auto;margin:8px 0;"></div>
    <div class="error" id="cancel-hold-error"></div>
    <div id="cancel-hold-result"></div>
    <div class="btn-row">
      <button class="btn btn-outline" onclick="closeCancelHoldModal()">取消</button>
      <button class="btn-danger" id="cancel-hold-confirm-btn" onclick="doCancelHold()">确认取消 Hold</button>
    </div>
  </div>
</div>

<!-- ===== 做账弹窗 ===== -->
<div class="modal-overlay" id="posting-modal">
  <div class="modal" style="max-width:550px;">
    <h2>📋 做账操作</h2>
    <div id="posting-glass-info"></div>
    <div style="margin:12px 0;">
      <label for="posting-operation" style="display:block;margin-bottom:4px;font-weight:bold;">做账站点</label>
      <input type="text" id="posting-operation" placeholder="请输入做账站点，多个用逗号/空格分隔" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:4px;font-size:13px;">
      <div class="hint" style="margin-top:4px;">可做账站点: """ + ", ".join(VALID_POSTING_OPS[:20]) + r""" ...</div>
    </div>
    <div style="margin:12px 0;">
      <label for="posting-reason" style="display:block;margin-bottom:4px;font-weight:bold;">做账原因</label>
      <input type="text" id="posting-reason" placeholder="请输入做账原因" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:4px;font-size:13px;">
    </div>
    <div class="error" id="posting-error"></div>
    <div id="posting-result"></div>
    <div class="btn-row">
      <button class="btn btn-outline" onclick="closePostingModal()">取消</button>
      <button class="btn-danger" id="posting-confirm-btn" onclick="doPosting()">确认做账</button>
    </div>
  </div>
</div>

<!-- ===== 颜色标记弹窗 ===== -->
<div class="modal-overlay" id="color-modal">
  <div class="modal" style="max-width:420px;">
    <h2>🎨 标记颜色</h2>
    <div style="font-size:12px;color:#999;margin-bottom:8px;">选中 <span id="color-selected-count">0</span> 行</div>
    <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;">
      <div class="color-swatch" data-color="#fef3c7" style="width:32px;height:32px;background:#fef3c7;border-radius:4px;cursor:pointer;border:2px solid #f59e0b;" onclick="pickColor('#fef3c7')"></div>
      <div class="color-swatch" data-color="#dbeafe" style="width:32px;height:32px;background:#dbeafe;border-radius:4px;cursor:pointer;border:2px solid transparent;" onclick="pickColor('#dbeafe')"></div>
      <div class="color-swatch" data-color="#fce7f3" style="width:32px;height:32px;background:#fce7f3;border-radius:4px;cursor:pointer;border:2px solid transparent;" onclick="pickColor('#fce7f3')"></div>
      <div class="color-swatch" data-color="#d1fae5" style="width:32px;height:32px;background:#d1fae5;border-radius:4px;cursor:pointer;border:2px solid transparent;" onclick="pickColor('#d1fae5')"></div>
      <div class="color-swatch" data-color="#ede9fe" style="width:32px;height:32px;background:#ede9fe;border-radius:4px;cursor:pointer;border:2px solid transparent;" onclick="pickColor('#ede9fe')"></div>
      <div class="color-swatch" data-color="#ffe4e6" style="width:32px;height:32px;background:#ffe4e6;border-radius:4px;cursor:pointer;border:2px solid transparent;" onclick="pickColor('#ffe4e6')"></div>
      <div class="color-swatch" data-color="#e0f2fe" style="width:32px;height:32px;background:#e0f2fe;border-radius:4px;cursor:pointer;border:2px solid transparent;" onclick="pickColor('#e0f2fe')"></div>
      <div class="color-swatch" data-color="#f3e8ff" style="width:32px;height:32px;background:#f3e8ff;border-radius:4px;cursor:pointer;border:2px solid transparent;" onclick="pickColor('#f3e8ff')"></div>
      <div class="color-swatch" data-color="#fef08a" style="width:32px;height:32px;background:#fef08a;border-radius:4px;cursor:pointer;border:2px solid transparent;" onclick="pickColor('#fef08a')"></div>
      <div class="color-swatch" data-color="#fed7aa" style="width:32px;height:32px;background:#fed7aa;border-radius:4px;cursor:pointer;border:2px solid transparent;" onclick="pickColor('#fed7aa')"></div>
    </div>
    <div class="btn-row">
      <button class="btn btn-outline" onclick="clearColorMark()" style="color:#e94560;border-color:#e94560;">🗑 清除颜色</button>
      <button class="btn btn-outline" onclick="closeColorModal()">取消</button>
      <button class="btn" id="color-apply-btn" onclick="applyColorMark()" style="background:#f59e0b;color:#fff;">应用</button>
    </div>
  </div>
</div>

<!-- ===== 交接展开弹窗 ===== -->
<div class="modal-overlay" id="notes-modal">
  <div class="modal" style="width:90%;max-width:900px;height:85vh;display:flex;flex-direction:column;">
    <h2>📝 交接</h2>
    <div id="notes-expanded" contenteditable="true" style="flex:1;overflow-y:auto;border:1px solid #ddd;border-radius:4px;padding:12px;font-size:13px;outline:none;min-height:0;"></div>
    <div class="btn-row">
      <button onclick="closeNotesModal()" class="btn">关闭</button>
    </div>
  </div>
</div>

<script>
var TASK_ID = """ + str(task_id) + r""";
var ALL_ROWS = """ + json.dumps(base_rows_js, ensure_ascii=False) + r""";
var ALL_DETAIL = """ + json.dumps(cd_detail_js, ensure_ascii=False) + r""";
var COLUMNS = """ + json.dumps(columns, ensure_ascii=False) + r""";
var COLUMN_STORE_KEY = 'cd_columns_' + TASK_ID;
var CURRENT_COLUMNS = (function(){
  try { var saved = JSON.parse(localStorage.getItem(COLUMN_STORE_KEY));
    if (saved && Array.isArray(saved) && saved.length === COLUMNS.length && saved.every(function(c){ return COLUMNS.indexOf(c) !== -1; })) return saved;
  } catch(e) {}
  return COLUMNS.slice();
})();
function saveColumnOrder() { try { localStorage.setItem(COLUMN_STORE_KEY, JSON.stringify(CURRENT_COLUMNS)); } catch(e) {} }
function resetColumnOrder() { CURRENT_COLUMNS = COLUMNS.slice(); saveColumnOrder(); applyColumnOrder(); renderTable(currentRows); }
function applyColumnOrder() {
  var headerRow = document.querySelector('#main-table thead tr:first-child');
  var filterRow = document.querySelector('#main-table thead tr:nth-child(2)');
  if (!headerRow || !filterRow) return;
  var headerMap = {}; headerRow.querySelectorAll('th').forEach(function(th, i) { headerMap[COLUMNS[i]] = th; });
  var filterMap = {}; filterRow.querySelectorAll('th.filter-th').forEach(function(th, i) { filterMap[COLUMNS[i]] = th; });
  CURRENT_COLUMNS.forEach(function(col) { if (headerMap[col]) headerRow.appendChild(headerMap[col]); if (filterMap[col]) filterRow.appendChild(filterMap[col]); });
}
function initColumnDrag() {
  var headerRow = document.querySelector('#main-table thead tr:first-child');
  if (!headerRow) return;
  var dragSrc = null;
  headerRow.querySelectorAll('th').forEach(function(th, i) {
    th.setAttribute('draggable', 'true'); th.setAttribute('data-col', COLUMNS[i]);
    th.addEventListener('dragstart', function(e) { dragSrc = this; this.style.opacity = '0.4'; e.dataTransfer.effectAllowed = 'move'; });
    th.addEventListener('dragend', function(e) { this.style.opacity = '1'; headerRow.querySelectorAll('th').forEach(function(t) { t.classList.remove('drag-over'); }); });
    th.addEventListener('dragover', function(e) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; this.classList.add('drag-over'); });
    th.addEventListener('dragleave', function(e) { this.classList.remove('drag-over'); });
    th.addEventListener('drop', function(e) {
      e.stopPropagation(); this.classList.remove('drag-over');
      var fromCol = dragSrc.getAttribute('data-col'), toCol = this.getAttribute('data-col');
      if (fromCol === toCol) return;
      var fromIdx = CURRENT_COLUMNS.indexOf(fromCol), toIdx = CURRENT_COLUMNS.indexOf(toCol);
      if (fromIdx === -1 || toIdx === -1) return;
      CURRENT_COLUMNS.splice(fromIdx, 1); CURRENT_COLUMNS.splice(toIdx, 0, fromCol);
      saveColumnOrder(); applyColumnOrder(); renderTable(currentRows); return false;
    });
  });
}
var LINE_CHART_DATA = """ + json.dumps(line_chart_js, ensure_ascii=False) + r""";
var LINE_PRODUCT_IDS = """ + json.dumps(line_product_ids, ensure_ascii=False) + r""";
var CONTOUR_DATA = """ + json.dumps(contour_js, ensure_ascii=False) + r""";
var CONTOUR_GLASS_LIST = """ + json.dumps(contour_glass_list, ensure_ascii=False) + r""";
var SPEC_LINE_DATA = """ + json.dumps(spec_line_data, ensure_ascii=False) + r""";
var CD_PARAMS_WITH_DATA = """ + json.dumps(cd_params_with_data, ensure_ascii=False) + r""";
var BAR_LABELS = """ + json.dumps(bar_labels, ensure_ascii=False) + r""";
var BAR_VALUES = """ + json.dumps(bar_values) + r""";
var VALID_HOLD_OPS = """ + json.dumps(VALID_HOLD_OPS) + r""";
var VALID_POSTING_OPS = """ + json.dumps(VALID_POSTING_OPS) + r""";
var GROUPS = """ + json.dumps(state.get("groups", {}), ensure_ascii=False) + r""";
var RECIPE_IDS = """ + json.dumps(state.get("recipe_ids", []), ensure_ascii=False) + r""";
var PARAM_TREND_GROUPS = """ + json.dumps(state.get("param_trend_groups", {}), ensure_ascii=False) + r""";
var selectedGids = {};
var currentRows = ALL_ROWS;
var isDragging = false, dragStartX = 0, dragStartY = 0;
var didDrag = false;

// ===== 渲染表格 =====
function renderTable(rows) {
  var tbody = document.getElementById('table-body');
  var html = '';
  rows.forEach(function(r) {
    var cls = '';
    if (r.is_risk && r.has_measure) cls = 'risk measure';
    else if (r.is_risk) cls = 'risk';
    else if (r.has_measure) cls = 'measure';
    if (selectedGids[r.glass_id]) cls += ' selected';
    var style = '';
    if (r.color_mark) style = ' data-color="1" style="--row-color:' + r.color_mark + '"';
    var vals = CURRENT_COLUMNS.map(function(c) {
      if (c === 'hold') return r.hold || '';
      if (c === '做账') return r['做账'] || '';
      return r[c] || '<span class="empty">-</span>';
    });
    html += '<tr class="' + cls + '" data-gid="' + r.glass_id + '"' + style + '>' +
      vals.map(function(v) { return '<td>' + v + '</td>'; }).join('') + '</tr>';
  });
  tbody.innerHTML = html;
  document.getElementById('row-count').textContent = rows.length;
  updateStats();
}

// ===== 筛选 =====
function applyFilters() {
  var filters = {};
  CURRENT_COLUMNS.forEach(function(c) {
    var fid = 'f-' + c.replace(/_/g, '-');
    var el = document.getElementById(fid);
    filters[c] = el ? el.value.toLowerCase().trim() : '';
  });
  var selGroup = document.getElementById('filter-group').value;
  var selRecipe = document.getElementById('filter-recipe').value;
  currentRows = ALL_ROWS.filter(function(r) {
    if (selGroup && r.group !== selGroup) return false;
    if (selRecipe && r.recipe_id !== selRecipe) return false;
    return CURRENT_COLUMNS.every(function(c) {
      var fv = filters[c];
      if (!fv) return true;
      var val;
      if (c === 'hold') val = r.hold;
      else if (c === '做账') val = r['做账'];
      else val = r[c];
      return (val || '').toLowerCase().indexOf(fv) !== -1;
    });
  });
  renderTable(currentRows);
  updateStats();
}

function onFilterChange() {
  var selGroup = document.getElementById('filter-group').value;
  var recipeSel = document.getElementById('filter-recipe');
  var prevVal = recipeSel.value;
  recipeSel.innerHTML = '<option value="">全部</option>';
  if (selGroup && GROUPS[selGroup]) {
    GROUPS[selGroup].forEach(function(r) {
      recipeSel.innerHTML += '<option value="' + r + '">' + r + '</option>';
    });
  } else {
    RECIPE_IDS.forEach(function(r) {
      recipeSel.innerHTML += '<option value="' + r + '">' + r + '</option>';
    });
  }
  var found = false;
  for (var i = 0; i < recipeSel.options.length; i++) {
    if (recipeSel.options[i].value === prevVal) { recipeSel.value = prevVal; found = true; break; }
  }
  if (!found) recipeSel.value = '';
  applyFilters();
}

function updateStats() {
  var rows = currentRows.length > 0 ? currentRows : ALL_ROWS;
  var measured = 0, risk = 0;
  rows.forEach(function(r) {
    if (r.has_measure) measured++;
    if (r.is_risk) risk++;
  });
  document.getElementById('stat-total').textContent = rows.length;
  document.getElementById('stat-measured').textContent = measured;
  document.getElementById('stat-risk').textContent = risk;
  document.getElementById('stat-selected').textContent = Object.keys(selectedGids).length;
  drawParamTrendCharts();
}

// ===== 框选 =====
(function(){
  var selBox = document.getElementById('selection-box');
  var tableWrap = document.getElementById('table-wrap');
  tableWrap.addEventListener('mousedown', function(e) {
    if (e.button !== 0) return;
    if (e.target.tagName === 'BUTTON' || e.target.tagName === 'INPUT') return;
    var sel = window.getSelection();
    if (sel && sel.toString().length > 0) return;
    var rect = tableWrap.getBoundingClientRect();
    isDragging = true;
    dragStartX = e.clientX - rect.left + tableWrap.scrollLeft;
    dragStartY = e.clientY - rect.top + tableWrap.scrollTop;
    selBox.style.display = 'block';
    selBox.style.left = dragStartX + 'px';
    selBox.style.top = dragStartY + 'px';
    selBox.style.width = '0px';
    selBox.style.height = '0px';
    if (!e.shiftKey) {
      selectedGids = {};
      document.querySelectorAll('#table-body tr.selected').forEach(function(tr) { tr.classList.remove('selected'); });
    }
    updateStats();
  });
  tableWrap.addEventListener('mousemove', function(e) {
    if (!isDragging) return;
    var rect = tableWrap.getBoundingClientRect();
    var curX = e.clientX - rect.left + tableWrap.scrollLeft;
    var curY = e.clientY - rect.top + tableWrap.scrollTop;
    selBox.style.left = Math.min(dragStartX, curX) + 'px';
    selBox.style.top = Math.min(dragStartY, curY) + 'px';
    selBox.style.width = Math.abs(curX - dragStartX) + 'px';
    selBox.style.height = Math.abs(curY - dragStartY) + 'px';
  });
  document.addEventListener('mouseup', function(e) {
    if (!isDragging) return;
    isDragging = false;
    didDrag = true;
    var boxRect = selBox.getBoundingClientRect();
    var boxTop = boxRect.top, boxBottom = boxRect.bottom;
    var boxLeft = boxRect.left, boxRight = boxRect.right;
    selBox.style.display = 'none';
    if (boxRect.width < 5 && boxRect.height < 5) {
      didDrag = false;
      return;
    }
    document.querySelectorAll('#table-body tr').forEach(function(tr) {
      var trRect = tr.getBoundingClientRect();
      if (trRect.bottom < boxTop || trRect.top > boxBottom ||
          trRect.right < boxLeft || trRect.left > boxRight) return;
      var gid = tr.getAttribute('data-gid');
      selectedGids[gid] = true;
      tr.classList.add('selected');
    });
    updateStats();
  });
})();

document.getElementById('table-body').addEventListener('click', function(e) {
  if (didDrag) { didDrag = false; return; }
  if (window.getSelection().toString().length > 0) return;
  var tr = e.target.closest('tr');
  if (!tr || !tr.getAttribute('data-gid')) return;
  var gid = tr.getAttribute('data-gid');
  if (selectedGids[gid]) { delete selectedGids[gid]; tr.classList.remove('selected'); }
  else { selectedGids[gid] = true; tr.classList.add('selected'); }
  updateStats();
});

function selectAll() { currentRows.forEach(function(r) { selectedGids[r.glass_id] = true; }); renderTable(currentRows); }
function clearSelect() { selectedGids = {}; renderTable(currentRows); }

// ===== 颜色标记 =====
var COLOR_STORE_KEY = 'cd_color_' + TASK_ID;
var pickedColor = '';

// 从 localStorage 加载已保存的颜色
function loadColorMarks() {
  try {
    var saved = JSON.parse(localStorage.getItem(COLOR_STORE_KEY) || '{}');
    ALL_ROWS.forEach(function(r) {
      if (saved[r.glass_id]) r.color_mark = saved[r.glass_id];
    });
  } catch(e) {}
}
function saveColorMarks() {
  var data = {};
  ALL_ROWS.forEach(function(r) {
    if (r.color_mark) data[r.glass_id] = r.color_mark;
  });
  try { localStorage.setItem(COLOR_STORE_KEY, JSON.stringify(data)); } catch(e) {}
}

function openColorModal() {
  var cnt = Object.keys(selectedGids).length;
  if (cnt === 0) { alert('请先框选 Glass'); return; }
  document.getElementById('color-selected-count').textContent = cnt;
  pickedColor = '';
  document.querySelectorAll('.color-swatch').forEach(function(el) { el.style.borderColor = 'transparent'; });
  document.getElementById('color-modal').classList.add('show');
}
function closeColorModal() { document.getElementById('color-modal').classList.remove('show'); }
function pickColor(color) {
  pickedColor = color;
  document.querySelectorAll('.color-swatch').forEach(function(el) {
    el.style.borderColor = el.getAttribute('data-color') === color ? '#333' : 'transparent';
  });
}
function applyColorMark() {
  if (!pickedColor) { alert('请选择一个颜色'); return; }
  var selList = Object.keys(selectedGids);
  selList.forEach(function(gid) {
    var row = ALL_ROWS.find(function(r) { return r.glass_id === gid; });
    if (row) row.color_mark = pickedColor;
  });
  saveColorMarks();
  renderTable(currentRows);
  closeColorModal();
}
function clearColorMark() {
  var selList = Object.keys(selectedGids);
  selList.forEach(function(gid) {
    var row = ALL_ROWS.find(function(r) { return r.glass_id === gid; });
    if (row) row.color_mark = '';
  });
  saveColorMarks();
  renderTable(currentRows);
  closeColorModal();
}

// ===== Hold =====
function openHoldModal() {
  var selList = Object.keys(selectedGids);
  if (selList.length === 0) { alert('请先在左侧框选要 Hold 的 Glass'); return; }
  var selRows = ALL_ROWS.filter(function(r) { return selectedGids[r.glass_id]; });
  var emptyLot = selRows.filter(function(r) { return !r['目前Lot_ID']; });
  if (emptyLot.length > 0) {
    alert('以下 Glass 的目前Lot_ID 为空，无法执行 Hold:\\n' + emptyLot.map(function(r){return r.glass_id;}).join(', '));
    return;
  }
  var groups = {};
  selRows.forEach(function(r) { var lot = r['目前Lot_ID']; if (!groups[lot]) groups[lot] = []; groups[lot].push(r.glass_id); });
  document.getElementById('hold-glass-info').innerHTML = '<b>已选 Glass:</b> ' + selList.length + ' 片 (' + selList.join(', ') + ')';
  document.getElementById('hold-operation').value = '';
  document.getElementById('hold-error').style.display = 'none';
  document.getElementById('hold-result').innerHTML = '';
  var lotHtml = '';
  Object.keys(groups).sort().forEach(function(lot) {
    lotHtml += '<div class="lot-group"><b>' + lot + '</b>: ' + groups[lot].length + ' 片 → (' + groups[lot].join(', ') + ')</div>';
  });
  document.getElementById('hold-lot-groups').innerHTML = lotHtml;
  document.getElementById('hold-confirm-btn').disabled = false;
  document.getElementById('hold-modal').classList.add('show');
}
function closeHoldModal() { document.getElementById('hold-modal').classList.remove('show'); }
function doHold() {
  var op = document.getElementById('hold-operation').value.trim();
  var errEl = document.getElementById('hold-error');
  if (VALID_HOLD_OPS.indexOf(op) === -1) { errEl.textContent = '无效的 Hold 站点，合法值: ' + VALID_HOLD_OPS.join(', '); errEl.style.display = 'block'; return; }
  errEl.style.display = 'none';
  document.getElementById('hold-confirm-btn').disabled = true;
  showLoading();
  fetch('/api/report/' + TASK_ID + '/hold', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({glass_ids: Object.keys(selectedGids), holdOperation: op})
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    hideLoading();
    var results = data.results || {};
    Object.keys(results).forEach(function(gid) {
      var row = ALL_ROWS.find(function(r) { return r.glass_id === gid; });
      if (row) {
        var display = (results[gid].status === 'ok' ? '✓' : '✗');
        var newHold = op + display;
        if (row.hold) {
          var existing = row.hold.split(' | '), replaced = false;
          for (var i = 0; i < existing.length; i++) { if (existing[i].indexOf(op) === 0) { existing[i] = newHold; replaced = true; break; } }
          if (!replaced) existing.push(newHold);
          row.hold = existing.join(' | ');
        } else { row.hold = newHold; }
      }
    });
    var okCount = 0, failCount = 0;
    Object.keys(results).forEach(function(gid) { if (results[gid].status === 'ok') okCount++; else failCount++; });
    var resultHtml = '<div class="result"><span class="ok">成功: ' + okCount + '</span> | <span class="fail">失败: ' + failCount + '</span></div>';
    Object.keys(results).forEach(function(gid) {
      var r = results[gid]; var cls = r.status === 'ok' ? 'ok' : 'fail';
      resultHtml += '<div class="' + cls + '">' + gid + ': ' + (r.status === 'ok' ? 'Hold成功' : r.message || 'Hold失败') + '</div>';
    });
    document.getElementById('hold-result').innerHTML = resultHtml;
    renderTable(currentRows);
  })
  .catch(function(err) { hideLoading(); alert('请求失败: ' + err.message); document.getElementById('hold-confirm-btn').disabled = false; });
}

// ===== 取消 Hold =====
var cancelHoldData = [];
function openCancelHoldModal() {
  var selList = Object.keys(selectedGids);
  if (selList.length === 0) { alert('请先在左侧框选要取消 Hold 的 Glass'); return; }
  showLoading();
  fetch('/api/report/' + TASK_ID + '/holdSummary', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({glass_ids: selList})
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    hideLoading();
    cancelHoldData = data.records || [];
    if (cancelHoldData.length === 0) {
      alert('所选 Glass 没有任何 Hold 记录');
      return;
    }
    document.getElementById('cancel-hold-glass-info').innerHTML =
      '<b>已选 Glass:</b> ' + selList.length + ' 片，共涉及 <b>' + cancelHoldData.length + '</b> 条 Hold 记录';
    document.getElementById('cancel-hold-error').style.display = 'none';
    document.getElementById('cancel-hold-result').innerHTML = '';
    document.querySelector('input[name="cancel-mode"][value="all"]').checked = true;
    switchCancelMode();
    document.getElementById('cancel-hold-modal').classList.add('show');
  })
  .catch(function(err) { hideLoading(); alert('请求失败: ' + err.message); });
}
function closeCancelHoldModal() { document.getElementById('cancel-hold-modal').classList.remove('show'); }
function switchCancelMode() {
  var mode = document.querySelector('input[name="cancel-mode"]:checked').value;
  var html = '';
  cancelHoldData.forEach(function(rec, idx) {
    var checked = mode === 'all' ? 'checked disabled' : 'checked';
    html += '<label style="display:flex;align-items:flex-start;margin:6px 0;padding:6px;background:#1a1a2e;border-radius:4px;cursor:pointer;">'
      + '<input type="checkbox" class="cancel-hold-check" data-idx="' + idx + '" ' + checked + ' style="margin-right:8px;margin-top:3px;">'
      + '<span><b>' + rec.operation + '</b> — <span style="color:#9a9ab0;">' + (rec.reasonCode || '') + '</span>'
      + '<br><small style="color:#a0b4cc;">涉及 ' + rec.count + ' 片玻璃</small></span></label>';
  });
  document.getElementById('cancel-hold-list').innerHTML = html;
}
function doCancelHold() {
  var mode = document.querySelector('input[name="cancel-mode"]:checked').value;
  var checks = document.querySelectorAll('.cancel-hold-check');
  var selected = [];
  checks.forEach(function(cb) {
    if (cb.checked) selected.push(cancelHoldData[parseInt(cb.dataset.idx)]);
  });
  if (selected.length === 0) {
    document.getElementById('cancel-hold-error').textContent = '请至少选择一条 Hold 记录';
    document.getElementById('cancel-hold-error').style.display = 'block';
    return;
  }
  document.getElementById('cancel-hold-error').style.display = 'none';
  document.getElementById('cancel-hold-confirm-btn').disabled = true;
  showLoading();
  fetch('/api/report/' + TASK_ID + '/cancelHold', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      glass_ids: Object.keys(selectedGids),
      cancel_records: selected.map(function(r) { return {reasonCode: r.reasonCode, operation: r.operation, gids: r.gids}; })
    })
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    hideLoading();
    document.getElementById('cancel-hold-confirm-btn').disabled = false;
    var results = data.results || {};
    Object.keys(results).forEach(function(gid) {
      if (results[gid].status === 'ok') {
        var row = ALL_ROWS.find(function(r) { return r.glass_id === gid; });
        if (row && row.hold) {
          var op = results[gid].operation;
          var existing = row.hold.split(' | ').filter(function(h) { return h.indexOf(op) !== 0; });
          row.hold = existing.join(' | ');
        }
      }
    });
    renderTable(currentRows);
    closeCancelHoldModal();
  })
  .catch(function(err) { hideLoading(); alert('请求失败: ' + err.message); document.getElementById('cancel-hold-confirm-btn').disabled = false; });
}

// ===== 做账 =====
function openPostingModal() {
  var selList = Object.keys(selectedGids);
  if (selList.length === 0) { alert('请先在左侧框选要做账的 Glass'); return; }
  document.getElementById('posting-glass-info').innerHTML = '<b>已选 Glass:</b> ' + selList.length + ' 片 (' + selList.slice(0, 10).join(', ') + (selList.length > 10 ? ' ...' : '') + ')';
  document.getElementById('posting-operation').value = '';
  document.getElementById('posting-reason').value = '';
  document.getElementById('posting-error').style.display = 'none';
  document.getElementById('posting-result').innerHTML = '';
  document.getElementById('posting-confirm-btn').disabled = false;
  document.getElementById('posting-modal').classList.add('show');
}
function closePostingModal() { document.getElementById('posting-modal').classList.remove('show'); }
function doPosting() {
  var opsRaw = document.getElementById('posting-operation').value.trim();
  var reason = document.getElementById('posting-reason').value.trim();
  var errEl = document.getElementById('posting-error');
  if (!opsRaw) { errEl.textContent = '请输入做账站点'; errEl.style.display = 'block'; return; }
  if (!reason) { errEl.textContent = '请输入做账原因'; errEl.style.display = 'block'; return; }
  var ops = opsRaw.split(/[，,\\s]+/).filter(function(o) { return o.length > 0; });
  var invalid = ops.filter(function(o) { return VALID_POSTING_OPS.indexOf(o) === -1; });
  if (invalid.length > 0) {
    errEl.textContent = '以下站点不可在此页面做账: ' + invalid.join(', ');
    errEl.style.display = 'block';
    return;
  }
  errEl.style.display = 'none';
  document.getElementById('posting-confirm-btn').disabled = true;
  showLoading();
  fetch('/api/report/' + TASK_ID + '/posting', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({glass_ids: Object.keys(selectedGids), posting_ops: ops, reason: reason})
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    hideLoading();
    document.getElementById('posting-confirm-btn').disabled = false;
    if (data.ok) {
      document.getElementById('posting-result').innerHTML = '<div class="result"><span class="ok">做账成功</span></div>';
      var selGids = Object.keys(selectedGids);
      ALL_ROWS.forEach(function(r) {
        if (selGids.indexOf(r.glass_id) !== -1) {
          var existing = (r['做账'] || '').split(',').filter(function(s) { return s.trim(); });
          ops.forEach(function(op) { if (existing.indexOf(op) === -1) existing.push(op); });
          r['做账'] = existing.join(',');
        }
      });
      renderTable(currentRows);
    } else {
      document.getElementById('posting-result').innerHTML = '<div class="result"><span class="fail">做账失败: ' + (data.error || '') + '</span></div>';
    }
  })
  .catch(function(err) { hideLoading(); alert('请求失败: ' + err.message); document.getElementById('posting-confirm-btn').disabled = false; });
}

// ===== 交接 =====
function expandNotes() {
  document.getElementById('notes-expanded').innerHTML = document.getElementById('report-notes').innerHTML;
  document.getElementById('notes-modal').classList.add('show');
  document.getElementById('notes-expanded').oninput = function() {
    document.getElementById('report-notes').innerHTML = this.innerHTML;
  };
}
function closeNotesModal() {
  document.getElementById('report-notes').innerHTML = document.getElementById('notes-expanded').innerHTML;
  document.getElementById('notes-modal').classList.remove('show');
}

function doSave() {
  showLoading();
  var html = '<!DOCTYPE html>\\n' + document.documentElement.outerHTML;
  var notes = document.getElementById('report-notes').innerHTML;
  fetch('/api/report/' + TASK_ID + '/save', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({html: html, notes: notes})
  })
  .then(function(r) { return r.json(); })
  .then(function(data) { hideLoading(); if (data.ok) alert('保存成功！'); else alert('保存失败: ' + (data.error || '')); })
  .catch(function(err) { hideLoading(); alert('保存失败: ' + err.message); });
}

function doRefresh() {
  if (!confirm('刷新将重新查询 YMS 获取最新数据，确定继续？')) return;
  showLoading();
  var allGids = currentRows.map(function(r) { return r.glass_id; });
  fetch('/api/report/' + TASK_ID + '/refresh', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({glass_ids: allGids})
  })
  .then(function(r) { return r.json(); })
  .then(function(data) { hideLoading(); if (data.ok) { location.reload(); } else { alert('刷新失败: ' + (data.error || '')); } })
  .catch(function(err) { hideLoading(); alert('刷新失败: ' + err.message); });
}

function showLoading() { document.getElementById('loading').classList.add('show'); }
function hideLoading() { document.getElementById('loading').classList.remove('show'); }

// ===== 柱状图 =====
function drawBarChart() {
  var canvas = document.getElementById('bar-chart');
  if (!canvas || !BAR_LABELS.length) return;
  var ctx = canvas.getContext('2d');
  var box = canvas.parentElement;
  var W = box.clientWidth - 24, H = 240;
  canvas.width = W; canvas.height = H;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  var pad = {top:20, right:20, bottom:70, left:50};
  var pw = W - pad.left - pad.right, ph = H - pad.top - pad.bottom;
  var n = BAR_LABELS.length;
  var maxV = Math.max.apply(null, BAR_VALUES) || 1;
  var barW = Math.max(8, Math.min(40, pw / n * 0.7));
  var gap = pw / n;
  ctx.fillStyle = '#fafafa'; ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = '#eee'; ctx.lineWidth = 0.5;
  for (var i = 0; i <= 4; i++) {
    var y = pad.top + ph * i / 4;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(W - pad.right, y); ctx.stroke();
    ctx.fillStyle = '#999'; ctx.font = '10px Microsoft YaHei';
    ctx.fillText(Math.round(maxV - maxV * i / 4), 2, y + 3);
  }
  var colors = ['#4fd1c5','#60a5fa','#fbbf24','#f87171','#a78bfa','#34d399','#f472b6','#818cf8'];
  for (var i = 0; i < n; i++) {
    var x = pad.left + gap * i + (gap - barW) / 2;
    var barH = ph * BAR_VALUES[i] / maxV;
    var y = pad.top + ph - barH;
    ctx.fillStyle = colors[i % colors.length];
    ctx.fillRect(x, y, barW, barH);
    ctx.fillStyle = '#333'; ctx.font = 'bold 10px Microsoft YaHei';
    ctx.fillText(BAR_VALUES[i], x + barW/2 - 8, y - 4);
    ctx.save(); ctx.fillStyle = '#666'; ctx.font = '9px Microsoft YaHei';
    ctx.translate(x + barW/2, H - pad.bottom + 14);
    ctx.rotate(-0.5); ctx.fillText(BAR_LABELS[i], 0, 0); ctx.restore();
  }
}

// ===== 折线图（AVG/MAX/MIN 分别画，每个 glass_id 一条线） =====
function drawLineChartDim(dim) {
  var prodId = document.getElementById('line-prod-' + dim.toLowerCase()).value;
  var cdParam = document.getElementById('line-cd-' + dim.toLowerCase()).value;
  var canvas = document.getElementById('line-chart-' + dim.toLowerCase());
  var legendEl = document.getElementById('line-spec-' + dim.toLowerCase());
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  var box = canvas.parentElement;
  var W = box.clientWidth - 24, H = 260;
  canvas.width = W; canvas.height = H;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  var pad = {top:30, right:20, bottom:60, left:60};
  var pw = W - pad.left - pad.right, ph = H - pad.top - pad.bottom;

  var productData = LINE_CHART_DATA[prodId] || {};
  var cdData = productData[cdParam] || {};
  var pts = cdData[dim] || [];
  if (pts.length === 0) { ctx.fillStyle='#999'; ctx.fillText('无数据', 10, 20); return; }

  var allVals = pts.map(function(p) { return p.value; });
  var minV = Math.min.apply(null, allVals), maxV = Math.max.apply(null, allVals);
  var spec = SPEC_LINE_DATA[cdParam] || {};
  if (spec.usl) maxV = Math.max(maxV, spec.usl * 1.05);
  if (spec.lsl) minV = Math.min(minV, spec.lsl * 0.95);
  var range = maxV - minV || 1;
  minV -= range * 0.05; maxV += range * 0.05; range = maxV - minV;

  ctx.fillStyle = '#fafafa'; ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = '#eee'; ctx.lineWidth = 0.5;
  for (var i = 0; i <= 5; i++) {
    var y = pad.top + ph * i / 5;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(W - pad.right, y); ctx.stroke();
    ctx.fillStyle = '#999'; ctx.font = '10px Microsoft YaHei';
    ctx.fillText((maxV - range * i / 5).toFixed(4), 2, y + 3);
  }

  if (spec.target) {
    var ty = pad.top + ph - ph * (spec.target - minV) / range;
    ctx.strokeStyle = '#4fd1c5'; ctx.lineWidth = 1; ctx.setLineDash([4,4]);
    ctx.beginPath(); ctx.moveTo(pad.left, ty); ctx.lineTo(W - pad.right, ty); ctx.stroke();
    ctx.setLineDash([]); ctx.fillStyle = '#4fd1c5'; ctx.font = '9px Microsoft YaHei';
    ctx.fillText('target ' + spec.target, W - pad.right - 70, ty - 3);
  }
  if (spec.usl) {
    var uy = pad.top + ph - ph * (spec.usl - minV) / range;
    ctx.strokeStyle = '#f87171'; ctx.lineWidth = 1; ctx.setLineDash([4,4]);
    ctx.beginPath(); ctx.moveTo(pad.left, uy); ctx.lineTo(W - pad.right, uy); ctx.stroke();
    ctx.setLineDash([]); ctx.fillStyle = '#f87171'; ctx.font = '9px Microsoft YaHei';
    ctx.fillText('USL ' + spec.usl, W - pad.right - 70, uy - 3);
  }
  if (spec.lsl) {
    var ly = pad.top + ph - ph * (spec.lsl - minV) / range;
    ctx.strokeStyle = '#f87171'; ctx.lineWidth = 1; ctx.setLineDash([4,4]);
    ctx.beginPath(); ctx.moveTo(pad.left, ly); ctx.lineTo(W - pad.right, ly); ctx.stroke();
    ctx.setLineDash([]); ctx.fillStyle = '#f87171'; ctx.font = '9px Microsoft YaHei';
    ctx.fillText('LSL ' + spec.lsl, W - pad.right - 70, ly - 3);
  }
  legendEl.innerHTML = 'Target: ' + (spec.target || '—') + ' | USL: ' + (spec.usl || '—') + ' | LSL: ' + (spec.lsl || '—');

  // 每个 glass_id 一条线
  ctx.strokeStyle = '#4fd1c5'; ctx.lineWidth = 2;
  ctx.beginPath();
  pts.forEach(function(pt, i) {
    var x = pad.left + pw * i / Math.max(1, pts.length - 1);
    var y = pad.top + ph - ph * (pt.value - minV) / range;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.fillStyle = '#4fd1c5';
  pts.forEach(function(pt) {
    var x = pad.left + pw * pts.indexOf(pt) / Math.max(1, pts.length - 1);
    var y = pad.top + ph - ph * (pt.value - minV) / range;
    ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI*2); ctx.fill();
  });

  // X 轴 glass_id 标签
  var step = Math.max(1, Math.floor(pts.length / 15));
  ctx.fillStyle = '#666'; ctx.font = '8px Microsoft YaHei';
  for (var i = 0; i < pts.length; i += step) {
    var x = pad.left + pw * i / Math.max(1, pts.length - 1);
    ctx.save(); ctx.translate(x, H - pad.bottom + 10); ctx.rotate(-0.4);
    ctx.fillText(pts[i].glass_id, 0, 0); ctx.restore();
  }
}

function drawAllLineCharts() {
  drawLineChartDim('AVG');
  drawLineChartDim('MAX');
  drawLineChartDim('MIN');
}

// ===== 等值线图 (IDW) — 支持 4 个独立图 =====
function drawContourChart(num) {
  var canvas = document.getElementById('contour-chart-' + num);
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  var box = canvas.parentElement;
  var W = box.clientWidth - 24, H = 280;
  canvas.width = W; canvas.height = H;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';

  var glass = document.getElementById('contour-glass-' + num).value;
  var param = document.getElementById('contour-param-' + num).value;
  var data = CONTOUR_DATA[glass] && CONTOUR_DATA[glass][param];
  if (!data || data.length === 0) { ctx.fillStyle='#999'; ctx.fillText('无数据', 10, 20); return; }

  var pad = {top:30, right:20, bottom:40, left:50};
  var pw = W - pad.left - pad.right, ph = H - pad.top - pad.bottom;

  var xs = data.map(function(d) { return d.x; });
  var ys = data.map(function(d) { return d.y; });
  var vs = data.map(function(d) { return d.value; });
  var minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs);
  var minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
  var minV = Math.min.apply(null, vs), maxV = Math.max.apply(null, vs);
  var rangeX = maxX - minX || 1, rangeY = maxY - minY || 1;

  var gridN = 60;
  var grid = [];
  for (var i = 0; i < gridN; i++) {
    grid[i] = [];
    for (var j = 0; j < gridN; j++) {
      var gx = minX + rangeX * (i + 0.5) / gridN;
      var gy = minY + rangeY * (j + 0.5) / gridN;
      var numerator = 0, denominator = 0;
      for (var k = 0; k < data.length; k++) {
        var dx = gx - data[k].x, dy = gy - data[k].y;
        var dist = Math.sqrt(dx*dx + dy*dy);
        if (dist < 1e-6) { numerator = data[k].value; denominator = 1; break; }
        var w = 1.0 / (dist * dist);
        numerator += w * data[k].value;
        denominator += w;
      }
      grid[i][j] = numerator / denominator;
    }
  }

  var cellW = pw / gridN, cellH = ph / gridN;
  for (var i = 0; i < gridN; i++) {
    for (var j = 0; j < gridN; j++) {
      var v = grid[i][j];
      var t = (v - minV) / (maxV - minV || 1);
      var r, g, b;
      if (t < 0.33) {
        r = 0; g = Math.floor(255 * t / 0.33); b = 255;
      } else if (t < 0.66) {
        r = Math.floor(255 * (t - 0.33) / 0.33); g = 255; b = Math.floor(255 * (1 - (t - 0.33) / 0.33));
      } else {
        r = 255; g = Math.floor(255 * (1 - (t - 0.66) / 0.34)); b = 0;
      }
      ctx.fillStyle = 'rgb(' + r + ',' + g + ',' + b + ')';
      ctx.fillRect(pad.left + i * cellW, pad.top + j * cellH, cellW + 1, cellH + 1);
    }
  }

  data.forEach(function(pt) {
    var x = pad.left + pw * (pt.x - minX) / rangeX;
    var y = pad.top + ph * (pt.y - minY) / rangeY;
    ctx.fillStyle = '#000';
    ctx.beginPath(); ctx.arc(x, y, 2, 0, Math.PI*2); ctx.fill();
  });

  ctx.strokeStyle = '#333'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(pad.left, pad.top); ctx.lineTo(pad.left, pad.top + ph); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(pad.left, pad.top + ph); ctx.lineTo(pad.left + pw, pad.top + ph); ctx.stroke();
  ctx.fillStyle = '#666'; ctx.font = '9px Microsoft YaHei';
  ctx.fillText('X: ' + minX.toFixed(0) + ' ~ ' + maxX.toFixed(0), pad.left, H - pad.bottom + 15);
  ctx.fillText('Y: ' + minY.toFixed(0) + ' ~ ' + maxY.toFixed(0), pad.left + pw/2 - 40, H - pad.bottom + 15);
  ctx.fillText('值: ' + minV.toFixed(3) + ' ~ ' + maxV.toFixed(3), pad.left + pw/2 - 40, pad.top - 10);
}

// ===== 量测详情 =====
function renderDetail() {
  var tbody = document.getElementById('detail-body');
  if (!tbody) return;
  var html = '';
  ALL_DETAIL.forEach(function(d) {
    html += '<tr>' +
      '<td>' + d.glass_id + '</td><td>' + d.oper_code + '</td><td>' + d.param_name + '</td>' +
      '<td>' + d.param_value + '</td><td>' + d.site_no + '</td>' +
      '<td>' + (d.measure_x || '<span class="empty">-</span>') + '</td>' +
      '<td>' + (d.measure_y || '<span class="empty">-</span>') + '</td>' +
      '<td>' + (d.coordinate_x || '<span class="empty">-</span>') + '</td>' +
      '<td>' + (d.coordinate_y || '<span class="empty">-</span>') + '</td>' +
      '<td>' + (d.eqp_id || '<span class="empty">-</span>') + '</td>' +
      '<td>' + (d.glass_judge || '<span class="empty">-</span>') + '</td>' +
      '<td>' + (d.start_time || '<span class="empty">-</span>') + '</td>' +
      '<td>' + (d['量测图片地址'] || '<span class="empty">-</span>') + '</td>' +
      '</tr>';
  });
  tbody.innerHTML = html;
}

function switchTab(name, evt) {
  document.querySelectorAll('.tab-content').forEach(function(t){ t.classList.remove('active'); });
  document.querySelectorAll('.tab-btn').forEach(function(b){ b.classList.remove('active'); });
  document.getElementById('tab-'+name).classList.add('active');
  (evt || event).target.classList.add('active');
  if (name === 'list') setTimeout(function() { drawBarChart(); drawAllLineCharts(); drawAllContourCharts(); drawParamTrendCharts(); }, 100);
}

function downloadExcel() {
  var a = document.createElement('a');
  a.href = '/api/report/' + TASK_ID + '/excel';
  a.download = 'CD异常报告_任务' + TASK_ID + '.xlsx';
  a.click();
}

// ===== 明暗模式 =====
(function() {
  var saved = localStorage.getItem('cd-theme');
  if (saved === 'light') document.body.classList.add('light');
  updateThemeBtn();
})();
function toggleTheme() {
  document.body.classList.toggle('light');
  var isLight = document.body.classList.contains('light');
  localStorage.setItem('cd-theme', isLight ? 'light' : 'dark');
  updateThemeBtn();
  drawBarChart(); drawAllLineCharts(); drawAllContourCharts(); drawParamTrendCharts();
}
function updateThemeBtn() {
  var btn = document.getElementById('btn-theme');
  if (btn) btn.textContent = document.body.classList.contains('light') ? '🌙 暗色' : '☀️ 亮色';
}

function drawAllContourCharts() {
  for (var i = 1; i <= 4; i++) drawContourChart(i);
}

// ===== 参数趋势折线图（按数值范围分组） =====
var PARAM_COLORS = [
  '#4fd1c5','#f87171','#fbbf24','#60a5fa','#a78bfa',
  '#fb923c','#34d399','#f472b6','#818cf8','#eab308',
  '#06b6d4','#ef4444','#22c55e','#c084fc','#64748b'
];
var TREND_TOOLTIP = document.getElementById('chart-tooltip');

function drawParamTrendCharts() {
  var groupNames = Object.keys(PARAM_TREND_GROUPS);
  groupNames.forEach(function(groupName, gi) {
    drawParamTrendPanel(gi, groupName);
  });
}

function drawParamTrendPanel(gi, groupName) {
  var canvas = document.getElementById('param-trend-' + gi);
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  var dpr = window.devicePixelRatio || 1;
  var W = canvas.parentElement.clientWidth - 20;
  var H = 280;
  canvas.width = W * dpr; canvas.height = H * dpr;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  var selGids = Object.keys(selectedGids);
  var isDark = !document.body.classList.contains('light');
  var bg = isDark ? '#1a1a2e' : '#fff';
  var textColor = isDark ? '#9a9ab0' : '#666';
  var gridColor = isDark ? '#333' : '#e0e0e0';

  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, W, H);

  if (selGids.length === 0) {
    selGids = ALL_ROWS.map(function(r) { return r.glass_id; });
  }

  var groupData = PARAM_TREND_GROUPS[groupName];
  if (!groupData) return;

  // 按 checkbox 筛选
  var paramNames = Object.keys(groupData).sort();
  var activeParams = [];
  paramNames.forEach(function(pname) {
    var cb = document.querySelector('.trend-cb[data-group="' + gi + '"][data-param="' + pname + '"]');
    if (cb && cb.checked) activeParams.push(pname);
  });
  if (activeParams.length === 0) {
    ctx.fillStyle = textColor;
    ctx.font = '14px Microsoft YaHei';
    ctx.textAlign = 'center';
    ctx.fillText('未选中任何参数', W/2, H/2);
    return;
  }

  // 收集时间序列
  var allTimes = [];
  var paramSeries = {};
  activeParams.forEach(function(pname) {
    var raw = groupData[pname];
    var filtered = raw.filter(function(d) { return selGids.indexOf(d.glass_id) >= 0; });
    if (filtered.length === 0) return;
    var timeMap = {};
    filtered.forEach(function(d) {
      if (!timeMap[d.start_time]) timeMap[d.start_time] = [];
      timeMap[d.start_time].push(d.value);
    });
    var times = Object.keys(timeMap).sort();
    var series = [];
    times.forEach(function(t) {
      var vals = timeMap[t];
      var avg = vals.reduce(function(a,b){return a+b;},0) / vals.length;
      series.push({time: t, value: avg});
      if (allTimes.indexOf(t) < 0) allTimes.push(t);
    });
    paramSeries[pname] = series;
  });
  allTimes.sort();

  var drawnParams = Object.keys(paramSeries);
  if (drawnParams.length === 0 || allTimes.length === 0) {
    ctx.fillStyle = textColor;
    ctx.font = '14px Microsoft YaHei';
    ctx.textAlign = 'center';
    ctx.fillText('所选 Glass 无参数数据', W/2, H/2);
    return;
  }

  // Y 范围
  var allVals = [];
  drawnParams.forEach(function(p) {
    paramSeries[p].forEach(function(d) { allVals.push(d.value); });
  });
  var minV = Math.min.apply(null, allVals);
  var maxV = Math.max.apply(null, allVals);
  var range = maxV - minV || 1;
  minV -= range * 0.05; maxV += range * 0.05;
  range = maxV - minV;

  var pad = {top: 20, right: 30, bottom: 45, left: 60};
  var pw = W - pad.left - pad.right;
  var ph = H - pad.top - pad.bottom;

  // 网格 + Y 刻度
  ctx.strokeStyle = gridColor; ctx.lineWidth = 0.5;
  var ySteps = 5;
  for (var i = 0; i <= ySteps; i++) {
    var y = pad.top + ph * i / ySteps;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(W - pad.right, y); ctx.stroke();
    var lbl = (maxV - (maxV - minV) * i / ySteps).toFixed(2);
    ctx.fillStyle = textColor; ctx.font = '10px Microsoft YaHei';
    ctx.textAlign = 'right';
    ctx.fillText(lbl, pad.left - 5, y + 3);
  }

  // X 轴刻度
  var maxXTicks = Math.min(allTimes.length, 12);
  var xStep = Math.max(1, Math.floor(allTimes.length / maxXTicks));
  for (var xi = 0; xi < allTimes.length; xi += xStep) {
    var x = pad.left + pw * xi / (allTimes.length - 1 || 1);
    ctx.strokeStyle = gridColor; ctx.lineWidth = 0.5;
    ctx.beginPath(); ctx.moveTo(x, pad.top); ctx.lineTo(x, pad.top + ph); ctx.stroke();
    var tLabel = allTimes[xi].substring(5, 16);
    ctx.fillStyle = textColor; ctx.font = '9px Microsoft YaHei';
    ctx.textAlign = 'center';
    ctx.save();
    ctx.translate(x, pad.top + ph + 8);
    ctx.rotate(-0.5);
    ctx.fillText(tLabel, 0, 0);
    ctx.restore();
  }

  // 画线 + 存储坐标用于 hover
  canvas._trendLines = [];
  drawnParams.forEach(function(pname) {
    var series = paramSeries[pname];
    var color = PARAM_COLORS[drawnParams.indexOf(pname) % PARAM_COLORS.length];
    ctx.strokeStyle = color; ctx.lineWidth = 1.8;
    ctx.beginPath();
    var first = true;
    var linePoints = [];
    series.forEach(function(d) {
      var xi = allTimes.indexOf(d.time);
      var x = pad.left + pw * xi / (allTimes.length - 1 || 1);
      var y = pad.top + ph - ph * (d.value - minV) / range;
      if (first) { ctx.moveTo(x, y); first = false; }
      else ctx.lineTo(x, y);
      linePoints.push({x: x, y: y, time: d.time, value: d.value, param: pname, color: color});
    });
    ctx.stroke();
    canvas._trendLines.push(linePoints);
  });

  // 图例
  var legendY = pad.top + ph + 33;
  var legendX = pad.left;
  drawnParams.forEach(function(pname) {
    var color = PARAM_COLORS[drawnParams.indexOf(pname) % PARAM_COLORS.length];
    if (legendX > W - pad.right - 60) { legendX = pad.left; legendY += 14; }
    ctx.fillStyle = color;
    ctx.fillRect(legendX, legendY - 4, 12, 4);
    ctx.fillStyle = textColor;
    ctx.font = '10px Microsoft YaHei';
    ctx.textAlign = 'left';
    ctx.fillText(pname, legendX + 15, legendY + 1);
    legendX += ctx.measureText(pname).width + 25;
  });

  // hover 事件
  canvas.onmousemove = function(e) {
    var rect = canvas.getBoundingClientRect();
    var mx = (e.clientX - rect.left) * (W / rect.width);
    var my = (e.clientY - rect.top) * (H / rect.height);
    var minDist = 20, best = null;
    (canvas._trendLines || []).forEach(function(pts) {
      pts.forEach(function(p) {
        var dx = Math.abs(mx - p.x), dy = Math.abs(my - p.y);
        if (dx < minDist && dy < minDist) { minDist = Math.max(dx, dy); best = p; }
      });
    });
    if (best) {
      TREND_TOOLTIP.style.display = 'block';
      TREND_TOOLTIP.style.left = (e.clientX + 18) + 'px';
      TREND_TOOLTIP.style.top = (e.clientY - 50) + 'px';
      TREND_TOOLTIP.innerHTML = '<b style="color:' + best.color + ';font-size:13px;">' + best.param + '</b><br>时间: ' + best.time + '<br>值: ' + best.value.toFixed(4);
    } else {
      TREND_TOOLTIP.style.display = 'none';
    }
  };
  canvas.onmouseleave = function() { TREND_TOOLTIP.style.display = 'none'; };
}

window.onload = function() {
  loadColorMarks();
  initColumnDrag(); applyColumnOrder();
  renderTable(ALL_ROWS);
  renderDetail();
  drawBarChart();
  drawAllLineCharts();
  drawAllContourCharts();
  drawParamTrendCharts();
};
</script>
</body>
</html>"""

    return html


# ============================================================
# 公开 API — 报告生成
# ============================================================

GENERATE_TIMEOUT = 180

DEMO_NOTICE = (
    "演示版未包含 CD 维度的演示数据。CD 报告依赖产线 EDC 接口，"
    "公开版本不提供该链路，请改用 OVL 或 Defect 两个演示任务。"
)


def _demo_mode() -> bool:
    return os.environ.get("DEMO_MODE", "").strip() == "1"


def _do_generate(task, version):
    """实际执行 CD 报告生成流程。"""
    task_id = task["id"]

    if _demo_mode():
        raise RuntimeError(DEMO_NOTICE)


    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    print(f"\n{'='*50}")
    print(f"生成任务 #{task_id} 的 CD 报告 (v{version})...")
    print(f"{'='*50}")

    # 步骤 1: 拆解
    start_full = _format_time(task["start_time"])
    end_full = _format_time(task["end_time"])

    main_process_raw = task.get("main_process", "[]")
    main_process = json.loads(main_process_raw) if isinstance(main_process_raw, str) else main_process_raw
    if not main_process:
        main_process = ["ALL"]
    if "ALL" in main_process:
        test_oper_code = "('1300','2300','3300','4300','5300')"
    else:
        test_oper_code = "(" + ",".join(f"'{p}'" for p in main_process) + ")"
    line_id = f"('{task['machine']}')"
    site = task["site"]

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    # 步骤 2: Token
    token = _get_fab_token()
    if not token:
        raise RuntimeError("Token 获取失败")

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    # 步骤 3: EDC 查询条件
    condition_ids, param_totals = _run_step_3(token, start_full, end_full, test_oper_code, line_id)

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    # 步骤 4: 捞取 EDC 数据
    all_data = _run_step_4(token, condition_ids)

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    # 步骤 5: 数据整合
    merged_rows = _run_step_5(all_data)

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    # 步骤 9: 查询规格线
    spec_map, measured_cd_params = _run_step_9(token, merged_rows)

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    # 步骤 6: 量测查询条件
    measure_cid, glass_ids = _run_step_6(token, merged_rows, main_process, measured_cd_params)

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    # 步骤 7: 捞取量测数据
    measure_data = _run_step_7(token, measure_cid)

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    # 步骤 8: 查询当前状态
    merged_rows = _run_step_8(merged_rows, site)

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    # 步骤 10: 数据组装
    state = _run_step_10(merged_rows, measure_data, spec_map, measured_cd_params, task)

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    # 生成 HTML
    html = _build_html(task, state)
    print(f"HTML 已生成 ({len(html)} 字符)")

    # 缓存
    if not _is_current(task_id, version):
        print(f"任务 #{task_id} v{version} 已被取代，跳过缓存写入")
        raise GenerationCancelled("已被新请求取代")

    _report_cache[task_id] = {
        "html": html, "state": state, "glass_ids": glass_ids, "site": site,
        "main_process": task.get("main_process", "[]"),
        "machine": task.get("machine", ""), "start_time": task.get("start_time", ""),
        "end_time": task.get("end_time", ""),
    }

    path = _report_file(task_id)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    _save_cache(task_id)
    print(f"已写入磁盘: {path}")

    return html


def generate_report(task):
    """生成 CD 报告，带超时。返回 HTML 字符串。"""
    task_id = task["id"]
    version = _start_generation(task_id)

    result = [None]
    error = [None]

    def runner():
        try:
            result[0] = _do_generate(task, version)
        except GenerationCancelled:
            error[0] = GenerationCancelled("已被新请求取代")
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout=GENERATE_TIMEOUT)

    if t.is_alive():
        raise RuntimeError(f"报告生成超时（>{GENERATE_TIMEOUT}s）")

    if error[0] is not None:
        if isinstance(error[0], GenerationCancelled):
            raise error[0]
        raise error[0]

    return result[0]


def get_report(task_id):
    """获取已缓存的报告 HTML。"""
    if task_id in _report_cache:
        return _report_cache[task_id].get("html")
    if _load_cache(task_id):
        return _report_cache[task_id].get("html")
    return None


def get_report_state(task_id):
    """获取已缓存的报告数据状态。"""
    if task_id not in _report_cache:
        _load_cache(task_id)
    return _report_cache.get(task_id, {}).get("state")


def get_report_glass_ids(task_id):
    """获取已缓存的 glass_ids。"""
    if task_id not in _report_cache:
        _load_cache(task_id)
    return _report_cache.get(task_id, {}).get("glass_ids", [])


def has_report(task_id):
    """检查是否已有缓存报告。"""
    if task_id in _report_cache:
        return True
    return _load_cache(task_id)


# ============================================================
# Hold / 做账操作
# ============================================================

def do_hold(task_id, glass_ids, hold_op):
    """执行 Hold 操作。"""
    state = _report_cache.get(task_id, {}).get("state")
    if not state:
        return {"ok": False, "error": "报告未生成"}
    base_rows = state["base_rows"]
    row_map = {r["glass_id"]: r for r in base_rows}

    empty_lot = [g for g in glass_ids if not row_map.get(g, {}).get("目前Lot_ID")]
    if empty_lot:
        return {"ok": False, "error": f"以下 Glass 的目前Lot_ID 为空: {', '.join(empty_lot)}"}

    lot_groups = {}
    for gid in glass_ids:
        lot = row_map[gid]["目前Lot_ID"]
        if lot not in lot_groups:
            lot_groups[lot] = []
        lot_groups[lot].append(gid)

    now_ts = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    reason = f"CD NG 暂hold {hold_op} {now_ts}"

    results = {}
    for lot, gids in lot_groups.items():
        print(f"Hold: lot={lot}, gids={gids}, op={hold_op}")
        body = {
            "site": _report_cache[task_id].get("site", "DEMO-B"),
            "factory": "ARRAY",
            "department": "EXP",
            "holdOperation": hold_op,
            "reasonCode": reason,
            "eventComment": reason,
            "eventUser": EVENT_USER,
            "glassIds": gids,
        }
        try:
            resp = _do_post("/api/ai-studio-service/mesOperation/hold", body,
                            extra_headers={"apiKey": HOLD_API_KEY})
            code = resp.get("code", -1)
            success = resp.get("success", False)
            if code == 0 or success:
                for g in gids: results[g] = {"status": "ok"}
            else:
                msg = resp.get("message") or resp.get("msg") or "未知错误"
                for g in gids: results[g] = {"status": "fail", "message": msg}
        except Exception as e:
            for g in gids: results[g] = {"status": "fail", "message": str(e)}

    hold_data = _load_hold_data(task_id)
    for gid, result in results.items():
        if gid not in hold_data: hold_data[gid] = []
        replaced = False
        for h in hold_data[gid]:
            if h["operation"] == hold_op:
                h["status"] = result["status"]
                h["time"] = now_ts
                h["reasonCode"] = reason
                replaced = True
                break
        if not replaced:
            hold_data[gid].append({"operation": hold_op, "status": result["status"],
                                   "time": now_ts, "reasonCode": reason})
    _save_hold_data(task_id, hold_data)

    return {"ok": True, "results": results}


def do_cancel_hold(task_id, glass_ids, cancel_records):
    """取消 Hold 操作。"""
    site = _report_cache.get(task_id, {}).get("site", "DEMO-B")
    hold_data = _load_hold_data(task_id)

    results = {}
    for rec in cancel_records:
        reason_code = rec["reasonCode"]
        operation = rec["operation"]
        involved_gids = rec["gids"]
        target_gids = [g for g in involved_gids if g in glass_ids]
        if not target_gids:
            continue

        print(f"CancelHold: op={operation}, reason={reason_code}, gids={target_gids}")
        body = {
            "site": site,
            "factory": "ARRAY",
            "department": "EXP",
            "cancelholdOperation": operation,
            "reasonCode": reason_code,
            "eventComment": reason_code,
            "eventUser": EVENT_USER,
            "glassIds": target_gids,
        }
        try:
            resp = _do_post("/api/ai-studio-service/mesOperation/cancelHold", body,
                            extra_headers={"apiKey": HOLD_API_KEY})
            code = resp.get("code", -1)
            success = resp.get("success", False)
            status = "ok" if (code == 0 or success) else "fail"
            msg = "" if status == "ok" else (resp.get("message") or resp.get("msg") or "未知错误")
        except Exception as e:
            status = "fail"
            msg = str(e)

        for g in target_gids:
            results[g] = {"status": status, "operation": operation, "message": msg}

    for gid, result in results.items():
        if result["status"] == "ok" and gid in hold_data:
            hold_data[gid] = [h for h in hold_data[gid]
                              if h["operation"] != result["operation"]]
            if not hold_data[gid]:
                del hold_data[gid]
    _save_hold_data(task_id, hold_data)

    if task_id in _report_cache:
        _report_cache[task_id]["html"] = None
    path = _report_file(task_id)
    if os.path.exists(path):
        os.remove(path)

    return {"ok": True, "results": results}


def get_hold_summary(task_id, glass_ids):
    """获取选中 glass 的 hold 记录汇总。"""
    hold_data = _load_hold_data(task_id)
    groups = {}
    for gid in glass_ids:
        if gid not in hold_data:
            continue
        for h in hold_data[gid]:
            key = (h.get("reasonCode", ""), h["operation"])
            if key not in groups:
                groups[key] = set()
            groups[key].add(gid)

    return [{"reasonCode": k[0], "operation": k[1],
             "gids": sorted(v), "count": len(v)} for k, v in groups.items()]


def do_posting(task_id, glass_ids, posting_ops, reason):
    """执行做账操作。"""
    state = _report_cache.get(task_id, {}).get("state")
    if not state:
        return {"ok": False, "error": "报告未生成"}

    site = _report_cache[task_id].get("site", "DEMO-B")

    invalid = [op for op in posting_ops if op not in VALID_POSTING_OPS]
    if invalid:
        return {"ok": False, "error": f"以下站点不可在此页面做账: {', '.join(invalid)}"}

    lines = [site]
    lines.extend(glass_ids)
    lines.append(" " + " ".join(posting_ops) + " ")
    lines.append(reason)
    content = "\n".join(lines)

    print(f"做账请求: site={site}, glass={len(glass_ids)}片, 站点={posting_ops}, 原因={reason}")

    try:
        conn = http.client.HTTPConnection(FAB_API_HOST, FAB_API_PORT, timeout=60)
        body = json.dumps({
            "model": "deepseek-v4-pro",
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.1,
            "max_tokens": 512,
            "stream": False,
        })
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + POSTING_API_KEY,
        }
        conn.request("POST", "/api/v1/chat/completions", body, headers)
        resp = conn.getresponse()
        resp_data = json.loads(resp.read().decode("utf-8"))
        conn.close()

        posting_data = _load_posting_data(task_id)
        for gid in glass_ids:
            if gid not in posting_data:
                posting_data[gid] = []
            for op in posting_ops:
                if op not in posting_data[gid]:
                    posting_data[gid].append(op)
        _save_posting_data(task_id, posting_data)

        return {"ok": True, "message": json.dumps(resp_data, ensure_ascii=False, indent=2)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def set_color_mark(task_id, glass_ids, color):
    """为指定行设置颜色标记。"""
    data = _load_color_data(task_id)
    for gid in glass_ids:
        data[gid] = color
    _save_color_data(task_id, data)
    return {"ok": True}


def clear_color_mark(task_id, glass_ids):
    """清除指定行的颜色标记。"""
    data = _load_color_data(task_id)
    for gid in glass_ids:
        if gid in data:
            del data[gid]
    _save_color_data(task_id, data)
    return {"ok": True}


def refresh_report(task_id):
    """刷新报告（重新生成 HTML）。"""
    if task_id not in _report_cache:
        return {"ok": False, "error": "报告未生成"}
    entry = _report_cache[task_id]
    state = entry.get("state")
    if not state:
        return {"ok": False, "error": "报告状态为空"}
    task = {
        "id": task_id,
        "site": entry.get("site", ""),
        "machine": entry.get("machine", ""),
        "start_time": entry.get("start_time", ""),
        "end_time": entry.get("end_time", ""),
        "main_process": entry.get("main_process", "[]"),
    }
    html = _build_html(task, state)
    entry["html"] = html
    with open(_report_file(task_id), "w", encoding="utf-8") as f:
        f.write(html)
    return {"ok": True}


# ============================================================
# 测试入口
# ============================================================

def run_test():
    """运行任务52的完整步骤1-5，打印每一步的输入、操作、返回"""
    from database import get_task

    # ================================================================
    # 步骤 1: 触发机制与关键信息拆解
    # ================================================================
    print("=" * 70)
    print("步骤 1: 触发机制与关键信息拆解")
    print("=" * 70)
    print()
    print("【触发条件】")
    print("  exception_type == '量测异常' AND measure_dim == 'CD'")
    print("  web_app.py → _get_engine(task) → 返回 cd_engine")
    print()

    task = get_task(52)
    if not task:
        print("  ❌ 任务52不存在")
        return

    print("【操作 1.1】从数据库查询任务52 ✅")
    print("【操作 1.2】拆解关键字段为变量")
    print(f"  task_id={task['id']}, site={task['site']}, machine={task['machine']}")
    print(f"  main_process={task['main_process']}, pfcd={task['pfcd']}")
    print(f"  start_time={task['start_time']}, end_time={task['end_time']}")
    print(f"  exception_type={task['exception_type']}, measure_dim={task['measure_dim']}")

    start_full = _format_time(task["start_time"])
    end_full = _format_time(task["end_time"])

    print(f"【操作 1.3】时间格式转换")
    print(f"  '{task['start_time']}' → '{start_full}'")
    print(f"  '{task['end_time']}'   → '{end_full}'")

    # 推导 testOperCode 和 lineId
    main_process_raw = task.get("main_process", "[]")
    main_process = json.loads(main_process_raw) if isinstance(main_process_raw, str) else main_process_raw
    if not main_process:
        main_process = ["ALL"]
    if "ALL" in main_process:
        test_oper_code = "('1300','2300','3300','4300','5300')"
    else:
        test_oper_code = "(" + ",".join(f"'{p}'" for p in main_process) + ")"
    line_id = f"('{task['machine']}')"

    print(f"  testOperCode: {test_oper_code}")
    print(f"  lineId: {line_id}")
    print()

    # ================================================================
    # 步骤 2: 获取 Token
    # ================================================================
    print("=" * 70)
    print("步骤 2: 获取 Token（与 OVL 第一步完全相同）")
    print("=" * 70)
    print("  POST {FAB_API_HOST}:{FAB_API_PORT} /api/v1/chat/completions")

    token = _get_fab_token()

    if token:
        print(f"  ✅ Token: {token[:30]}... (长度 {len(token)})")
    else:
        print("  ❌ Token 为空，无法继续")
        return
    print()

    # ================================================================
    # 步骤 3: 异步创建 15 个 EDC 查询条件
    # ================================================================
    condition_ids, param_totals = _run_step_3(token, start_full, end_full, test_oper_code, line_id)

    if not condition_ids:
        print("❌ 没有任何 conditionId，无法继续")
        return
    print()

    # ================================================================
    # 步骤 4: 异步分页捞取全部数据
    # ================================================================
    all_data = _run_step_4(token, condition_ids)
    print()

    # ================================================================
    # 步骤 5: 数据整合
    # ================================================================
    merged_rows = _run_step_5(all_data)
    print()

    # ================================================================
    # 汇总
    # ================================================================
    print("=" * 70)
    print("步骤 1-5 完成 — 汇总")
    print("=" * 70)
    print(f"  步骤1: 任务52 拆解完成")
    print(f"  步骤2: Token 获取成功")
    print(f"  步骤3: {len(condition_ids)}/{len(ALL_PARAM_NAMES)} 个 conditionId")
    print(f"  步骤4: 共捞取 {sum(len(v) for v in all_data.values())} 条原始数据")
    print(f"  步骤5: 整合后 {len(merged_rows)} 行")

    # 输出整合后的数据样例
    if merged_rows:
        print()
        print("【整合后第一行样例】")
        sample = merged_rows[0]
        # 先显示 EDC 原始字段
        for key in ["item1", "item2", "item3", "item7", "item8", "item9", "item15", "item17", "item21"]:
            print(f"  {key}: {sample.get(key, '')}")
        # 再显示 15 个参数
        for pname in ALL_PARAM_NAMES:
            print(f"  {pname}: {sample.get(pname, '(缺失)')}")

    return {
        "task": task,
        "start_full": start_full,
        "end_full": end_full,
        "token": token,
        "condition_ids": condition_ids,
        "all_data": all_data,
        "merged_rows": merged_rows,
    }


if __name__ == "__main__":
    run_test()