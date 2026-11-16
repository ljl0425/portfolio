"""制程参数异常报告引擎 — 纯逻辑模块，支持按任务参数生成独立报告。

与 OVL 引擎的关键差异：
- 步骤3 paramName 来自用户选择的制程参数名（多选拼接）
- 跳过步骤5-6（CVD查询）和步骤7-8（量测查询）
- 规则判断基于用户自定义规则+阈值（而非 OVL spec 超标判断）
- 报告表格列按参数名动态生成
"""

import json, http.client, ssl, os, threading, time
from datetime import datetime

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
POSTING_API_URL = os.environ.get("POSTING_API_URL", "http://fab-api.invalid:3000/api/v2/chat/completions")

# 固定列（不随参数名变化）
FIXED_COLUMNS = ["glass_id", "oper_code", "start_time", "line_id", "product_id", "recipe_id",
                 "目前站点", "目前Lot_ID", "cst_id", "hold", "做账"]

# RESULT_COLUMNS 模板（与 OVL 相同）
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

# 根据 RESULT_COLUMNS 顺序构建 column_name → itemN 的映射
_COL_INDEX = {}
for _i, _col in enumerate(RESULT_COLUMNS):
    _COL_INDEX[_col["column_name"]] = f"item{_i + 1}"

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

# ===== 内存缓存 =====
_report_cache = {}

# ===== 版本控制 =====
_gen_version = {}
_gen_lock = threading.Lock()


class GenerationCancelled(Exception):
    """当前生成被新请求取代时抛出"""


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
    return os.path.join(OUTPUT_DIR, f"report_cache_{task_id}.json")


def _save_cache(task_id):
    entry = _report_cache.get(task_id)
    if not entry:
        return
    data = {
        "glass_ids": entry.get("glass_ids", []),
        "site": entry.get("site", ""),
        "data4": entry.get("data4", []),
        "state": entry.get("state", {}),
        "main_process": entry.get("main_process", "[]"),
        "machine": entry.get("machine", ""),
        "start_time": entry.get("start_time", ""),
        "end_time": entry.get("end_time", ""),
        "process_params": entry.get("process_params", "[]"),
        "judge_param": entry.get("judge_param", ""),
        "judge_rule": entry.get("judge_rule", ""),
        "threshold1": entry.get("threshold1", ""),
        "threshold2": entry.get("threshold2", ""),
    }
    with open(_cache_file(task_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_cache(task_id):
    cache_path = _cache_file(task_id)
    html_path = _report_file(task_id)
    if os.path.exists(cache_path) and os.path.exists(html_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        _report_cache[task_id] = {
            "html": html,
            "state": data.get("state", {}),
            "glass_ids": data.get("glass_ids", []),
            "site": data.get("site", ""),
            "data4": data.get("data4", []),
            "main_process": data.get("main_process", "[]"),
            "machine": data.get("machine", ""),
            "start_time": data.get("start_time", ""),
            "end_time": data.get("end_time", ""),
            "process_params": data.get("process_params", "[]"),
            "judge_param": data.get("judge_param", ""),
            "judge_rule": data.get("judge_rule", ""),
            "threshold1": data.get("threshold1", ""),
            "threshold2": data.get("threshold2", ""),
        }
        return True
    return False


# ===== 工具函数 =====
def _do_post(url, body, token=None, extra_headers=None):
    conn = http.client.HTTPSConnection("szporos-fab.csot.tcl.com", timeout=60, context=ctx)
    headers = {"Content-Type": "application/json;charset=UTF-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
    conn.request("POST", url, body_bytes, headers)
    resp = conn.getresponse()
    data = json.loads(resp.read().decode())
    conn.close()
    return data


def _do_patch(url, body, api_key):
    conn = http.client.HTTPSConnection("szporos-fab.csot.tcl.com", timeout=60, context=ctx)
    conn.request("PATCH", url, json.dumps(body),
                 {"Content-Type": "application/json;charset=UTF-8", "apiKey": api_key})
    resp = conn.getresponse()
    data = json.loads(resp.read().decode())
    conn.close()
    return data


def _fetch_all_pages(condition_id, token, page_size=500):
    all_data = []
    page = 1
    while True:
        resp = _do_post("/api/yms-analysis-service/showDataByPage/showDataByPage",
                        {"conditionId": condition_id, "pageIndex": page, "pageSize": page_size, "dataSource": 1}, token)
        batch = resp["data"]["data"]
        if len(batch) == 0:
            break
        all_data.extend(batch)
        page += 1
    print(f"  分页完成: 共 {len(all_data)} 条, 请求 {page} 页")
    return all_data


def _get_fab_token():
    conn = http.client.HTTPConnection(FAB_API_HOST, FAB_API_PORT, timeout=30)
    conn.request("POST", "/api/v1/chat/completions",
                 json.dumps({"messages": [{"role": "user", "content": "1"}], "stream": False}),
                 {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"})
    resp = conn.getresponse()
    token = json.loads(resp.read().decode())["choices"][0]["message"]["content"].strip()
    conn.close()
    return token


# ===== 持久化 =====
def _hold_file(task_id):
    return os.path.join(OUTPUT_DIR, f"hold_results_{task_id}.json")


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
    return os.path.join(OUTPUT_DIR, f"posting_results_{task_id}.json")


def _load_posting_data(task_id):
    path = _posting_file(task_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_posting_data(task_id, data):
    with open(_posting_file(task_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _report_file(task_id):
    return os.path.join(OUTPUT_DIR, f"report_process_task{task_id}.html")


def _notes_file(task_id):
    return os.path.join(OUTPUT_DIR, f"notes_{task_id}.json")


def _load_notes(task_id):
    path = _notes_file(task_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("notes", "")
    return ""


def _save_notes(task_id, notes):
    with open(_notes_file(task_id), "w", encoding="utf-8") as f:
        json.dump({"notes": notes}, f, ensure_ascii=False)


# ===== 阶段 A：参数提取 =====

def _derive_params(task):
    """从 task 对象提取参数，推导 EDC 请求体所需字段。"""
    site = task["site"]
    machine = task["machine"]
    start = task["start_time"]
    end = task["end_time"]

    def _to_edc_datetime(s):
        if not s: return "Invalid date"
        s = s.replace("T", " ").replace("-", "/")
        if s.count(":") == 1:
            s += ":00"
        return s

    start_full = _to_edc_datetime(start)
    end_full = _to_edc_datetime(end)

    # 主制程 → testOperCode
    main_process_raw = task.get("main_process", "[]")
    main_process = json.loads(main_process_raw) if isinstance(main_process_raw, str) else main_process_raw
    if not main_process:
        main_process = ["ALL"]
    if "ALL" in main_process:
        test_oper_code = "('1300','2300','3300','4300','5300')"
    else:
        test_oper_code = "(" + ",".join(f"'{p}'" for p in main_process) + ")"

    line_id = f"('{machine}')"

    # 制程参数名 → paramName 拼接
    process_params_raw = task.get("process_params", "[]")
    process_params = json.loads(process_params_raw) if isinstance(process_params_raw, str) else process_params_raw
    if not process_params:
        raise RuntimeError("制程参数名为空，无法生成报告")
    param_name = "(" + ",".join(f"'{p}'" for p in process_params) + ")"

    print(f"[参数推导] 主制程: {main_process} → testOperCode: {test_oper_code}")
    print(f"[参数推导] lineId: {line_id}")
    print(f"[参数推导] 制程参数名: {process_params} → paramName: {param_name}")

    return {
        "site": site,
        "start_full": start_full,
        "end_full": end_full,
        "test_oper_code": test_oper_code,
        "line_id": line_id,
        "param_name": param_name,
        "process_params": process_params,
    }


# ===== 阶段 B：数据获取（仅步骤3、4、9） =====

def _run_step_3(token, params):
    """步骤3：EDC 参数查询。"""
    print(f"[步骤3] EDC TIME 查询: paramName={params['param_name']}, "
          f"testOperCode={params['test_oper_code']}, lineId={params['line_id']}")

    edc3 = dict(EDC_COMMON)
    edc3.update({
        "selectType": "TIME",
        "endTime": f"'{params['end_full']}'",
        "startTime": f"'{params['start_full']}'",
        "testEqpType": "('DEMO-PNK','DEMO-PNK2')",
        "testOperCode": params["test_oper_code"],
        "productId": "('')",
        "lineId": params["line_id"],
        "paramName": params["param_name"],
    })

    r3 = _do_post("/api/yms-analysis-service/edcParam/edcParamQuery", edc3, token)
    cid3 = r3["data"]["conditionId"]
    print(f"  conditionId: {cid3}")
    return cid3


def _run_step_4(token, cid3):
    """步骤4：分页获取数据。"""
    print("[步骤4] 分页获取数据...")
    data4 = _fetch_all_pages(cid3, token)
    glass_ids = list(set(row[_COL_INDEX["glass_id"]] for row in data4))
    print(f"  共 {len(data4)} 行, {len(glass_ids)} 个唯一 glass_id")
    return data4, glass_ids


def _run_step_9(glass_ids, site):
    """步骤9：状态查询（分批，每批200）。"""
    BATCH_SIZE = 200
    all_data = []
    for i in range(0, len(glass_ids), BATCH_SIZE):
        batch = glass_ids[i:i + BATCH_SIZE]
        print(f"[步骤9] dbQuery 批次 {i // BATCH_SIZE + 1}/{(len(glass_ids) - 1) // BATCH_SIZE + 1}: {len(batch)} 个 glass...")
        body = {
            "params": {
                "site": site,
                "productNameList": batch,
                "lotName": "",
                "carrierName": ""
            },
            "site": site
        }
        resp = _do_patch("/api/ai-studio-service/dbQuery/executeQuery/fed8cb7ccd4a454880a3f8f96a0e6089",
                         body, DBQUERY_API_KEY)
        batch_data = resp.get("data", [])
        all_data.extend(batch_data)
        print(f"  返回 {len(batch_data)} 条")
    print(f"[步骤9] 完成: 共 {len(all_data)} 条")
    return {"data": all_data}


# ===== 阶段 C：数据组装与规则判断 =====

def _judge_value(value, rule, t1, t2):
    """根据规则判断 value 是否异常。返回 (is_risk, display_text)。"""
    try:
        v = float(value)
        th1 = float(t1)
    except (ValueError, TypeError):
        return False, f"无法比较（值={value}, 阈值1={t1}）"

    if rule == "大于等于阈值1":
        return v >= th1, f"{v} >= {th1}"
    elif rule == "小于等于阈值1":
        return v <= th1, f"{v} <= {th1}"
    elif rule == "在阈值1,2内":
        try:
            th2 = float(t2)
        except (ValueError, TypeError):
            return False, f"无法比较（阈值2无效={t2}）"
        return th1 <= v <= th2, f"{th1} <= {v} <= {th2}"
    elif rule == "不在阈值1,2内":
        try:
            th2 = float(t2)
        except (ValueError, TypeError):
            return False, f"无法比较（阈值2无效={t2}）"
        return v < th1 or v > th2, f"{v} < {th1} 或 {v} > {th2}"
    return False, f"未知规则: {rule}"


def _run_step_10(data4, data9, task):
    """步骤10：数据组装 + 规则判断。

    同一 glass_id 可能有多个 param_name 的记录（每条记录对应一个参数），
    需要按 glass_id 聚合，每个 param_name 的值作为独立列。
    同一 glass_id 也可能有多个 oper_code 的记录，全部保留（即每行 = glass_id + oper_code 唯一）。
    """
    process_params_raw = task.get("process_params", "[]")
    process_params = json.loads(process_params_raw) if isinstance(process_params_raw, str) else process_params_raw
    judge_param = task.get("judge_param", "")
    judge_rule = task.get("judge_rule", "")
    threshold1 = task.get("threshold1", "")
    threshold2 = task.get("threshold2", "")

    print(f"[步骤10] 数据组装: {len(data4)} 行原始数据, 参数: {process_params}")
    print(f"[步骤10] 判断规则: {judge_param} {judge_rule} (阈值1={threshold1}, 阈值2={threshold2})")

    # 构建 step9 映射: glass_id → {目前站点, 目前Lot_ID}
    step9_map = {}
    for row in data9.get("data", []):
        pn = row.get("PRODUCTNAME", "")
        if pn:
            step9_map[pn] = {
                "目前站点": row.get("PROCESSOPERATIONNAME", ""),
                "目前Lot_ID": row.get("LOTNAME", ""),
            }

    # 按 (glass_id, oper_code) 分组聚合 data4
    # 每组聚合所有 param_name 的值
    group_map = {}  # (glass_id, oper_code) → {param_name: param_value, ..., _meta: {...}}
    for row in data4:
        gid = row[_COL_INDEX["glass_id"]]
        op = row[_COL_INDEX["oper_code"]]
        pname = row[_COL_INDEX["param_name"]]
        pvalue = row[_COL_INDEX["param_value"]]
        key = (gid, op)
        if key not in group_map:
            group_map[key] = {
                "_meta": {
                    "glass_id": gid,
                    "oper_code": op,
                    "start_time": row[_COL_INDEX["start_time"]],
                    "line_id": row[_COL_INDEX["line_id"]],
                    "product_id": row[_COL_INDEX["product_id"]],
                    "recipe_id": row[_COL_INDEX["recipe_id"]],
                    "cst_id": row[_COL_INDEX["cst_id"]],
                }
            }
        group_map[key][pname] = pvalue

    # 构建 base_rows
    base_rows = []
    for (gid, op), grp in group_map.items():
        s9 = step9_map.get(gid, {})
        row = {
            "glass_id": gid,
            "oper_code": op,
            "start_time": grp["_meta"]["start_time"],
            "line_id": grp["_meta"]["line_id"],
            "product_id": grp["_meta"]["product_id"],
            "recipe_id": grp["_meta"]["recipe_id"],
            "目前站点": s9.get("目前站点", ""),
            "目前Lot_ID": s9.get("目前Lot_ID", ""),
            "cst_id": grp["_meta"]["cst_id"],
            "做账": "",
        }
        # 添加每个参数值列
        for pn in process_params:
            row[pn] = grp.get(pn, "")
        base_rows.append(row)

    # 排序
    base_rows.sort(key=lambda r: r["start_time"], reverse=True)

    # 规则判断
    risk_glass_set = set()
    for r in base_rows:
        judge_value_raw = r.get(judge_param, "")
        if judge_value_raw:
            is_risk, reason = _judge_value(judge_value_raw, judge_rule, threshold1, threshold2)
            r["is_risk"] = is_risk
            r["_risk_reason"] = reason
            if is_risk:
                risk_glass_set.add(r["glass_id"])
        else:
            r["is_risk"] = False
            r["_risk_reason"] = "无判断参数值"

    # 统计
    risk_glass = len(risk_glass_set)
    total_glass = len(set(r["glass_id"] for r in base_rows))
    total_rows = len(base_rows)

    print(f"[步骤10] 完成: {total_rows} 行, {total_glass} 玻璃, {risk_glass} 异常")

    # 柱状图数据：目前站点分布
    bar_data = {}
    for r in base_rows:
        site_label = r["目前站点"] or "(空)"
        bar_data[site_label] = bar_data.get(site_label, 0) + 1
    bar_labels = sorted(bar_data.keys())
    bar_values = [bar_data[k] for k in bar_labels]

    return {
        "base_rows": base_rows,
        "param_names": process_params,
        "judge_param": judge_param,
        "judge_rule": judge_rule,
        "threshold1": threshold1,
        "threshold2": threshold2,
        "total_glass": total_glass,
        "total_rows": total_rows,
        "risk_glass": risk_glass,
        "bar_labels": bar_labels,
        "bar_values": bar_values,
    }


# ===== 阶段 D：HTML 生成 =====

def _build_html(task, state):
    task_id = task["id"]
    site = task["site"]
    machine = task["machine"]
    start = task["start_time"]
    end = task["end_time"]
    base_rows = state["base_rows"]
    param_names = state["param_names"]
    judge_param = state["judge_param"]
    judge_rule = state["judge_rule"]
    threshold1 = state["threshold1"]
    threshold2 = state["threshold2"]
    total_glass = state["total_glass"]
    total_rows = state["total_rows"]
    risk_glass = state["risk_glass"]
    bar_labels = state.get("bar_labels", [])
    bar_values = state.get("bar_values", [])
    bar_labels_js = bar_labels
    bar_values_js = bar_values

    now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    hold_data = _load_hold_data(task_id)
    posting_data = _load_posting_data(task_id)

    # 构建动态列名：固定列 + 参数值列
    all_columns = list(FIXED_COLUMNS)
    # 把 hold 和 做账 移到最后，参数值列插在中间
    base_cols = ["glass_id", "oper_code", "start_time", "line_id", "product_id", "recipe_id"]
    mid_cols = param_names  # 参数值列
    tail_cols = ["目前站点", "目前Lot_ID", "cst_id", "hold", "做账"]
    all_columns = base_cols + mid_cols + tail_cols

    base_rows_js = []
    for r in base_rows:
        gid = r["glass_id"]
        hd = hold_data.get(gid, [])
        hold_display = " | ".join(
            [f"{h['operation']}{'✓' if h['status'] == 'ok' else '✗'}" for h in hd]
        ) if hd else ""
        row_js = {
            "glass_id": gid,
            "oper_code": r["oper_code"],
            "start_time": r["start_time"],
            "line_id": r["line_id"],
            "product_id": r["product_id"] or "",
            "recipe_id": r["recipe_id"] or "",
            "目前站点": r["目前站点"] or "",
            "目前Lot_ID": r["目前Lot_ID"] or "",
            "cst_id": r["cst_id"] or "",
            "hold": hold_display,
            "做账": ",".join(posting_data.get(gid, [])),
            "is_risk": r["is_risk"],
        }
        for pn in param_names:
            row_js[pn] = r.get(pn, "") or ""
        base_rows_js.append(row_js)

    # 构建筛选列名（用于前端 filter）
    filter_cols = base_cols + mid_cols + tail_cols

    # 生成表头和筛选行
    th_html = "\n".join(f"<th>{c}</th>" for c in all_columns)
    filter_th_html = "\n".join(
        f'<th class="filter-th"><input id="f-{c}" oninput="applyFilters()" placeholder="筛选..."></th>'
        for c in all_columns
    )

    # 生成 COLUMNS JS 数组
    columns_js = json.dumps(all_columns, ensure_ascii=False)

    # 生成 renderTable 中的列渲染
    # 需要特殊处理 hold 和 做账 列
    mid_cols_js = json.dumps(mid_cols, ensure_ascii=False)
    tail_cols_set = set(tail_cols)

    html = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>制程参数异常报告 — 任务 #""" + str(task_id) + r"""</title>
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
.tab-spacer { flex:1; }
.btn-save { background:#f59e0b; color:#fff; border:none; border-radius:4px; padding:6px 14px; font-size:12px; cursor:pointer; margin-right:4px; }
.btn-save:hover { background:#d97706; }
.btn-refresh { background:#10b981; color:#fff; border:none; border-radius:4px; padding:6px 14px; font-size:12px; cursor:pointer; margin-right:4px; }
.btn-refresh:hover { background:#059669; }
.btn-theme { background:transparent; color:var(--text-secondary); border:1px solid var(--text-secondary); border-radius:4px; padding:4px 10px; font-size:12px; cursor:pointer; margin-right:4px; }
.btn-theme:hover { color:var(--text-primary); border-color:var(--text-primary); }
.header { background:var(--bg-header); color:var(--text-on-dark); padding:12px 24px 16px; display:flex; gap:20px; align-items:flex-start; flex-shrink:0; }
body.light .header { color:var(--text-dark); }
.header-main { flex:1; }
.header h1 { font-size:18px; color:var(--text-title); }
.header .info { font-size:12px; color:var(--text-secondary); margin-top:4px; line-height:1.8; }
.header .info b { color:var(--text-primary); }
body.light .header .info b { color:var(--text-dark); }
.header-notes { width:680px; flex-shrink:0; background:var(--bg-card-light); color:var(--text-dark); border-radius:6px; padding:12px; }
.header-notes h3 { font-size:13px; margin-bottom:6px; color:var(--text-dark); display:flex; justify-content:space-between; align-items:center; }
.header-notes [contenteditable] { width:100%; height:200px; overflow-y:auto; border:1px solid var(--border-light); border-radius:4px; padding:8px; font-size:12px; outline:none; background:var(--bg-light); }
.stats { display:flex; gap:16px; padding:12px 24px; background:var(--bg-stats); flex-wrap:wrap; flex-shrink:0; }
.stat-card { background:var(--bg-card); border-radius:6px; padding:12px 20px; text-align:center; min-width:100px; }
.stat-card .num { font-size:22px; font-weight:bold; }
.stat-card .num.normal { color:var(--text-title); }
.stat-card .num.warn { color:#fbbf24; }
.stat-card .num.danger { color:#f87171; }
.stat-card .label { font-size:10px; color:var(--text-secondary); margin-top:2px; }
.main { display:flex; flex:1; overflow:hidden; }
.left { width:65%; background:var(--bg-card-light); border-right:1px solid var(--border-color); display:flex; flex-direction:column; overflow:hidden; }
.right { width:35%; padding:12px; overflow-y:auto; background:var(--bg-light); }
.bar-chart-box { background:var(--bg-card-light); border:1px solid var(--border-color); border-radius:6px; padding:12px; margin-bottom:12px; }
.bar-chart-box h3 { font-size:13px; margin-bottom:8px; color:var(--text-dark); }
.bar-chart-box canvas { width:100%; height:260px; }
.left-header { padding:8px 16px; border-bottom:1px solid var(--border-color); display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px; }
.left-header h2 { font-size:14px; }
.btn { background:var(--accent); color:#fff; border:none; border-radius:4px; padding:6px 14px; font-size:12px; cursor:pointer; }
.btn:hover { background:var(--accent-hover); }
.btn-outline { background:var(--bg-card-light); color:var(--accent); border:1px solid var(--accent); }
.btn-outline:hover { background:#e8faf8; }
.btn-danger { background:#ef4444; color:#fff; border:none; border-radius:4px; padding:6px 14px; font-size:12px; cursor:pointer; }
.btn-danger:hover { background:#dc2626; }
.table-wrap { flex:1; overflow:auto; position:relative; }
table { width:100%; border-collapse:collapse; font-size:11px; table-layout:auto; }
thead th { background:var(--bg-table-header); position:sticky; top:0; padding:4px 8px; text-align:left; border-bottom:2px solid #d0d0d0; white-space:nowrap; z-index:2; overflow:hidden; text-overflow:ellipsis; }
thead th.filter-th { top:26px; z-index:1; padding:2px 4px; background:var(--bg-light); border-bottom:1px solid var(--border-light); }
thead th.filter-th input { width:100%; border:1px solid var(--border-light); border-radius:2px; padding:2px 4px; font-size:10px; }
td { padding:3px 8px; border-bottom:1px solid var(--border-lighter); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; user-select:text !important; }
tr:hover td { background:#f0f8ff; }
tr.risk td { background:#fff3f3; }
tr.selected td { background:#c8e6ff !important; }
tr.selected:hover td { background:#bbdefb !important; }
tr.selected.risk td { background:#ffcdd2 !important; }
.selection-box { position:absolute; border:2px dashed var(--accent); background:rgba(79,209,197,0.1); pointer-events:none; z-index:99; display:none; }
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
th[draggable] { cursor:grab; user-select:none; }
th[draggable]:active { cursor:grabbing; }
th.drag-over { border-right:3px solid var(--accent) !important; }
</style>
</head>
<body>

<div class="loading" id="loading">处理中...</div>

<div class="navbar">
  <a href="/">任务列表</a>
  <a href="/config">配置管理</a>
  <a href="/tasks/""" + str(task_id) + r"""">← 返回任务详情</a>
</div>

<div class="tabs">
  <div class="tab-spacer"></div>
  <button class="btn-theme" onclick="toggleTheme()" id="btn-theme">☀️ 亮色</button>
  <button class="btn-refresh" onclick="doRefresh()">🔄 刷新</button>
  <button class="btn-save" onclick="doSave()">💾 保存页面</button>
</div>

<div class="header">
  <div class="header-main">
    <h1>异常处理报告 — 制程参数</h1>
    <div class="info">
      <b>任务编号:</b> #""" + str(task_id) + r""" &nbsp;|&nbsp; <b>站点:</b> """ + site + r""" &nbsp;|&nbsp; <b>机台:</b> """ + machine + r"""<br>
      <b>时间范围:</b> """ + start + r""" ~ """ + end + r"""<br>
      <b>制程参数:</b> """ + ", ".join(param_names) + r""" &nbsp;|&nbsp; <b>判断规则:</b> """ + judge_param + r""" """ + judge_rule + r""" (阈值1=""" + threshold1 + (""", 阈值2=""" + threshold2 if threshold2 else "") + r""")<br>
      <b>生成时间:</b> """ + now_ts + r"""
    </div>
  </div>
  <div class="header-notes">
    <h3>📝 交接 <button class="btn" onclick="expandNotes()" style="font-size:11px;padding:2px 10px;background:#4fd1c5;color:#fff;border:none;border-radius:3px;cursor:pointer;">展开</button></h3>
    <div id="report-notes" contenteditable="true" placeholder="在此输入交接内容...（支持粘贴图片）">""" + _load_notes(task_id) + r"""</div>
  </div>
</div>

<div class="stats">
  <div class="stat-card"><div class="num normal" id="stat-total">""" + str(total_rows) + r"""</div><div class="label">总记录数</div></div>
  <div class="stat-card"><div class="num warn" id="stat-glass">""" + str(total_glass) + r"""</div><div class="label">Glass 数</div></div>
  <div class="stat-card"><div class="num danger" id="stat-risk">""" + str(risk_glass) + r"""</div><div class="label">异常 Glass</div></div>
  <div class="stat-card"><div class="num" style="color:#a78bfa;" id="stat-selected">0</div><div class="label">已选 Glass</div></div>
</div>

<div class="main">
  <div class="left">
    <div class="left-header">
      <h2>异常事件清单（共 <span id="row-count">""" + str(total_rows) + r"""</span> 条）</h2>
      <div class="right-actions">
        <button class="btn btn-outline" onclick="selectAll()">全选</button>
        <button class="btn btn-outline" onclick="clearSelect()">取消选择</button>
        <button class="btn-danger" onclick="openHoldModal()">🛑 Hold</button>
        <button class="btn btn-outline" onclick="openCancelHoldModal()" style="color:#e94560;border-color:#e94560;">↩ 取消 Hold</button>
        <button class="btn-outline" onclick="openPostingModal()" style="background:#8b5cf6;color:#fff;border-color:#8b5cf6;">📋 做账</button>
        <button class="btn" onclick="downloadExcel()">📥 下载 Excel</button>
        <button class="btn btn-outline" onclick="resetColumnOrder()" style="font-size:11px;">📐 重置列</button>
      </div>
    </div>
    <div class="table-wrap" id="table-wrap">
      <div class="selection-box" id="selection-box"></div>
      <table id="main-table" style="min-width:""" + str(120 + len(all_columns) * 100) + r"""px;">
        <thead>
          <tr>
            """ + th_html + r"""
          </tr>
          <tr>
            """ + filter_th_html + r"""
          </tr>
        </thead>
        <tbody id="table-body">
        </tbody>
      </table>
    </div>
  </div>
  <div class="right" id="chart-area">
    <div class="bar-chart-box" id="bar-chart-box">
      <h3>目前站点分布 — Glass 数量柱状图</h3>
      <canvas id="bar-chart"></canvas>
    </div>
  </div>
</div>

<!-- Hold 模态框 -->
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

<!-- 取消 Hold 弹窗 -->
<div class="modal-overlay" id="cancel-hold-modal">
  <div class="modal" style="max-width:600px;">
    <h2>↩ 取消 Hold</h2>
    <div id="cancel-hold-glass-info"></div>
    <div style="margin:12px 0;">
      <label style="display:flex;align-items:center;margin-bottom:8px;cursor:pointer;">
        <input type="radio" name="cancel-mode" value="all" checked onchange="switchCancelMode()" style="margin-right:8px;">
        <span>取消全部 Hold — 撤销所选玻璃执行过的所有 Hold 操作</span>
      </label>
      <label style="display:flex;align-items:center;cursor:pointer;">
        <input type="radio" name="cancel-mode" value="partial" onchange="switchCancelMode()" style="margin-right:8px;">
        <span>取消指定 Hold — 只撤销选中的某几条 Hold 记录</span>
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

<!-- 做账弹窗 -->
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

<!-- 交接展开弹窗 -->
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
var ALL_COLUMNS = """ + columns_js + r""";
var COLUMN_STORE_KEY = 'proc_columns_' + TASK_ID;
var CURRENT_COLUMNS = (function(){
  try { var saved = JSON.parse(localStorage.getItem(COLUMN_STORE_KEY));
    if (saved && Array.isArray(saved) && saved.length === ALL_COLUMNS.length && saved.every(function(c){ return ALL_COLUMNS.indexOf(c) !== -1; })) return saved;
  } catch(e) {}
  return ALL_COLUMNS.slice();
})();
function saveColumnOrder() { try { localStorage.setItem(COLUMN_STORE_KEY, JSON.stringify(CURRENT_COLUMNS)); } catch(e) {} }
function resetColumnOrder() { CURRENT_COLUMNS = ALL_COLUMNS.slice(); saveColumnOrder(); applyColumnOrder(); renderTable(currentRows); }
function applyColumnOrder() {
  var headerRow = document.querySelector('#main-table thead tr:first-child');
  var filterRow = document.querySelector('#main-table thead tr:nth-child(2)');
  if (!headerRow || !filterRow) return;
  var headerMap = {}; headerRow.querySelectorAll('th').forEach(function(th, i) { headerMap[ALL_COLUMNS[i]] = th; });
  var filterMap = {}; filterRow.querySelectorAll('th.filter-th').forEach(function(th, i) { filterMap[ALL_COLUMNS[i]] = th; });
  CURRENT_COLUMNS.forEach(function(col) { if (headerMap[col]) headerRow.appendChild(headerMap[col]); if (filterMap[col]) filterRow.appendChild(filterMap[col]); });
}
function initColumnDrag() {
  var headerRow = document.querySelector('#main-table thead tr:first-child');
  if (!headerRow) return;
  var dragSrc = null;
  headerRow.querySelectorAll('th').forEach(function(th, i) {
    th.setAttribute('draggable', 'true'); th.setAttribute('data-col', ALL_COLUMNS[i]);
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
var MID_COLS = """ + mid_cols_js + r""";
var VALID_HOLD_OPS = """ + json.dumps(VALID_HOLD_OPS) + r""";
var VALID_POSTING_OPS = """ + json.dumps(VALID_POSTING_OPS) + r""";
var BAR_LABELS = """ + json.dumps(bar_labels_js) + r""";
var BAR_VALUES = """ + json.dumps(bar_values_js) + r""";
var selectedGids = {};
var currentRows = ALL_ROWS;
var isDragging = false, dragStartX = 0, dragStartY = 0;
var didDrag = false;

function renderTable(rows) {
  var tbody = document.getElementById('table-body');
  var html = '';
  rows.forEach(function(r) {
    var cls = '';
    if (r.is_risk) cls = 'risk';
    if (selectedGids[r.glass_id]) cls += ' selected';
    var vals = CURRENT_COLUMNS.map(function(c) {
      if (c === 'hold') return r.hold || '';
      if (c === '做账') return r['做账'] || '';
      return (r[c] !== undefined ? r[c] : '') || '<span class="empty">-</span>';
    });
    html += '<tr class="' + cls + '" data-gid="' + r.glass_id + '">' +
      vals.map(function(v) { return '<td>' + v + '</td>'; }).join('') + '</tr>';
  });
  tbody.innerHTML = html;
  document.getElementById('row-count').textContent = rows.length;
  updateStats();
}

function applyFilters() {
  var filters = {};
  CURRENT_COLUMNS.forEach(function(c) {
    var el = document.getElementById('f-' + c);
    filters[c] = el ? el.value.toLowerCase().trim() : '';
  });
  currentRows = ALL_ROWS.filter(function(r) {
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

function updateStats() {
  var rows = currentRows.length > 0 ? currentRows : ALL_ROWS;
  var risk = 0;
  var glassSet = {};
  rows.forEach(function(r) {
    if (r.is_risk) risk++;
    glassSet[r.glass_id] = true;
  });
  document.getElementById('stat-total').textContent = rows.length;
  document.getElementById('stat-glass').textContent = Object.keys(glassSet).length;
  document.getElementById('stat-risk').textContent = risk;
  document.getElementById('stat-selected').textContent = Object.keys(selectedGids).length;
}

// 框选
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
    if (boxRect.width < 5 && boxRect.height < 5) { didDrag = false; selBox.style.display = 'none'; return; }
    var boxTop = boxRect.top, boxBottom = boxRect.bottom;
    var boxLeft = boxRect.left, boxRight = boxRect.right;
    selBox.style.display = 'none';
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
    lotHtml += '<div class="lot-group"><b>' + lot + '</b>: ' + groups[lot].length + ' 片 → 独立请求 (' + groups[lot].join(', ') + ')</div>';
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
      alert('所选 Glass 没有任何 Hold 记录，无需取消');
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
    var okCount = 0, failCount = 0;
    Object.keys(results).forEach(function(gid) {
      if (results[gid].status === 'ok') okCount++; else failCount++;
    });
    var resultHtml = '<div class="result"><span class="ok">成功: ' + okCount + '</span> | <span class="fail">失败: ' + failCount + '</span></div>';
    Object.keys(results).forEach(function(gid) {
      var r = results[gid]; var cls = r.status === 'ok' ? 'ok' : 'fail';
      resultHtml += '<div class="' + cls + '">' + gid + ': ' + (r.status === 'ok' ? '取消Hold成功' : r.message || '取消失败') + '</div>';
    });
    document.getElementById('cancel-hold-result').innerHTML = resultHtml;
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
    if (okCount > 0) { closeCancelHoldModal(); }
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
  var ops = opsRaw.split(/[，,\s]+/).filter(function(o) { return o.length > 0; });
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
      document.getElementById('posting-result').innerHTML = '<div class="result"><span class="ok">做账成功</span><br><pre style="font-size:11px;white-space:pre-wrap;max-height:200px;overflow-y:auto;">' + (data.message || '') + '</pre></div>';
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
      document.getElementById('posting-result').innerHTML = '<div class="result"><span class="fail">做账失败</span><br><pre style="font-size:11px;white-space:pre-wrap;max-height:200px;overflow-y:auto;">' + (data.error || '未知错误') + '</pre></div>';
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
  var notes = document.getElementById('report-notes').innerHTML;
  fetch('/api/report/' + TASK_ID + '/save', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({notes: notes})
  })
  .then(function(r) { return r.json(); })
  .then(function(data) { hideLoading(); if (data.ok) alert('保存成功！'); else alert('保存失败: ' + (data.error || '')); })
  .catch(function(err) { hideLoading(); alert('保存失败: ' + err.message); });
}

function doRefresh() {
  if (!confirm('刷新将重新查询 YMS 获取最新数据（步骤9），确定继续？')) return;
  showLoading();
  fetch('/api/report/' + TASK_ID + '/refresh', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({})
  })
  .then(function(r) { return r.json(); })
  .then(function(data) { hideLoading(); if (data.ok) { location.reload(); } else { alert('刷新失败: ' + (data.error || '')); } })
  .catch(function(err) { hideLoading(); alert('刷新失败: ' + err.message); });
}

function downloadExcel() {
  var a = document.createElement('a');
  a.href = '/api/report/' + TASK_ID + '/excel';
  a.download = '制程参数异常报告_任务' + TASK_ID + '.xlsx';
  a.click();
}

function showLoading() { document.getElementById('loading').classList.add('show'); }
function hideLoading() { document.getElementById('loading').classList.remove('show'); }

(function() {
  var saved = localStorage.getItem('theme');
  if (saved === 'light') document.body.classList.add('light');
  updateThemeBtn();
})();
function toggleTheme() {
  document.body.classList.toggle('light');
  var isLight = document.body.classList.contains('light');
  localStorage.setItem('theme', isLight ? 'light' : 'dark');
  updateThemeBtn();
}
function updateThemeBtn() {
  var btn = document.getElementById('btn-theme');
  if (btn) btn.textContent = document.body.classList.contains('light') ? '🌙 暗色' : '☀️ 亮色';
}

function drawBarChart() {
  var canvas = document.getElementById('bar-chart');
  if (!canvas || !BAR_LABELS.length) return;
  var ctx = canvas.getContext('2d');
  var box = canvas.parentElement;
  var W = box.clientWidth - 24, H = 260;
  canvas.width = W; canvas.height = H;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  var pad = {top:20, right:20, bottom:80, left:50};
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

window.onload = function() { initColumnDrag(); applyColumnOrder(); renderTable(ALL_ROWS); drawBarChart(); };
</script>
</body>
</html>"""

    return html


# ===== 生成入口 =====

GENERATE_TIMEOUT = 180
GENERATE_MAX_RETRIES = 1


def _build_demo_state(task):
    """构造 demo 制程参数报告的 state（复用 _build_html 真实报告格式）。"""
    process_params_raw = task.get("process_params", "[]")
    process_params = json.loads(process_params_raw) if isinstance(process_params_raw, str) else process_params_raw
    if not process_params:
        raise RuntimeError("制程参数名为空，无法生成报告")
    judge_param = task.get("judge_param", "")
    judge_rule = task.get("judge_rule", "")
    threshold1 = task.get("threshold1", "")
    threshold2 = task.get("threshold2", "")

    machine = task.get("machine", "DEMO-M01")
    site = task.get("site", "DEMO-SITE-1")
    start = task.get("start_time", "2026-11-10 08:00")

    # 8 片玻璃：6 片在阈值内，2 片超出阈值以演示风险判定
    demo_values = [
        ("DEMO-G001", "0.80"), ("DEMO-G002", "1.00"), ("DEMO-G003", "1.20"),
        ("DEMO-G004", "0.90"), ("DEMO-G005", "1.10"), ("DEMO-G006", "1.35"),
        ("DEMO-G007", "2.20"), ("DEMO-G008", "0.30"),
    ]

    base_rows = []
    for i, (gid, pval) in enumerate(demo_values):
        row = {
            "glass_id": gid,
            "oper_code": "3400",
            "start_time": start,
            "line_id": machine,
            "product_id": "DEMO-PROD-01",
            "recipe_id": "DEMO-RCP-001",
            "目前站点": site,
            "目前Lot_ID": f"DEMO-LOT-{i + 1:03d}",
            "cst_id": f"DEMO-CST-{i + 1:03d}",
            "做账": "",
        }
        for pn in process_params:
            row[pn] = pval
        base_rows.append(row)

    # 规则判断（复用真实 _judge_value 逻辑）
    risk_glass_set = set()
    for r in base_rows:
        judge_value_raw = r.get(judge_param, "")
        if judge_value_raw:
            is_risk, reason = _judge_value(judge_value_raw, judge_rule, threshold1, threshold2)
            r["is_risk"] = is_risk
            r["_risk_reason"] = reason
            if is_risk:
                risk_glass_set.add(r["glass_id"])
        else:
            r["is_risk"] = False
            r["_risk_reason"] = "无判断参数值"

    total_glass = len(set(r["glass_id"] for r in base_rows))
    total_rows = len(base_rows)
    risk_glass = len(risk_glass_set)

    bar_data = {}
    for r in base_rows:
        site_label = r["目前站点"] or "(空)"
        bar_data[site_label] = bar_data.get(site_label, 0) + 1
    bar_labels = sorted(bar_data.keys())
    bar_values = [bar_data[k] for k in bar_labels]

    return {
        "base_rows": base_rows,
        "param_names": process_params,
        "judge_param": judge_param,
        "judge_rule": judge_rule,
        "threshold1": threshold1,
        "threshold2": threshold2,
        "total_glass": total_glass,
        "total_rows": total_rows,
        "risk_glass": risk_glass,
        "bar_labels": bar_labels,
        "bar_values": bar_values,
    }


def _do_generate(task, version):
    task_id = task["id"]

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    print(f"\n{'='*50}")
    print(f"生成任务 #{task_id} 的制程参数报告 (v{version})...")
    print(f"{'='*50}")

    # 阶段 A: 参数提取
    params = _derive_params(task)

    if os.environ.get("DEMO_MODE", "").strip() == "1":
        print("[DEMO] 制程参数报告：跳过外部数据获取，使用演示数据")
        time.sleep(2.5)
        state = _build_demo_state(task)
        html = _build_html(task, state)
        demo_glass_ids = list(set(r["glass_id"] for r in state["base_rows"]))
        _report_cache[task_id] = {
            "html": html, "state": state, "glass_ids": demo_glass_ids,
            "site": task.get("site", ""), "data4": [],
            "main_process": task.get("main_process", "[]"),
            "machine": task.get("machine", ""),
            "start_time": task.get("start_time", ""),
            "end_time": task.get("end_time", ""),
            "process_params": task.get("process_params", "[]"),
            "judge_param": task.get("judge_param", ""),
            "judge_rule": task.get("judge_rule", ""),
            "threshold1": task.get("threshold1", ""),
            "threshold2": task.get("threshold2", ""),
        }
        path = _report_file(task_id)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        _save_cache(task_id)
        print(f"[DEMO] 已写入磁盘: {path}")
        return html

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    # 阶段 B: 数据获取
    token = _get_fab_token()
    print(f"token: {token}")

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    cid3 = _run_step_3(token, params)
    data4, glass_ids = _run_step_4(token, cid3)
    print(f"步骤4: {len(data4)} 行, {len(glass_ids)} 个唯一 glass_id")

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    data9 = _run_step_9(glass_ids, params["site"])
    print(f"步骤9: {len(data9.get('data', []))} 条")

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    # 阶段 C: 数据组装
    state = _run_step_10(data4, data9, task)
    print(f"步骤10: {state['total_rows']} 行, {state['total_glass']} 玻璃, {state['risk_glass']} 异常")

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    # 阶段 D: HTML 生成
    html = _build_html(task, state)
    print(f"HTML 已生成 ({len(html)} 字符)")

    if not _is_current(task_id, version):
        print(f"任务 #{task_id} v{version} 已被取代，跳过缓存写入")
        raise GenerationCancelled("已被新请求取代")

    _report_cache[task_id] = {
        "html": html, "state": state, "glass_ids": glass_ids,
        "site": params["site"], "data4": data4,
        "main_process": task.get("main_process", "[]"),
        "machine": task.get("machine", ""),
        "start_time": task.get("start_time", ""),
        "end_time": task.get("end_time", ""),
        "process_params": task.get("process_params", "[]"),
        "judge_param": task.get("judge_param", ""),
        "judge_rule": task.get("judge_rule", ""),
        "threshold1": task.get("threshold1", ""),
        "threshold2": task.get("threshold2", ""),
    }

    path = _report_file(task_id)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    _save_cache(task_id)
    print(f"已写入磁盘: {path}")

    return html


def generate_report(task):
    task_id = task["id"]
    version = _start_generation(task_id)

    for attempt in range(GENERATE_MAX_RETRIES + 1):
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
            if attempt < GENERATE_MAX_RETRIES:
                print(f"任务 #{task_id} 第 {attempt + 1} 次生成超时（>{GENERATE_TIMEOUT}s），重试...")
                version = _start_generation(task_id)
                continue
            else:
                raise RuntimeError(f"报告生成超时（>{GENERATE_TIMEOUT}s）")

        if error[0] is not None:
            if isinstance(error[0], GenerationCancelled):
                raise error[0]
            raise error[0]

        return result[0]

    raise RuntimeError("报告生成失败")


def get_report(task_id):
    if task_id in _report_cache:
        return _report_cache[task_id].get("html")
    if _load_cache(task_id):
        return _report_cache[task_id].get("html")
    return None


def get_report_state(task_id):
    if task_id not in _report_cache:
        _load_cache(task_id)
    return _report_cache.get(task_id, {}).get("state")


def has_report(task_id):
    if task_id in _report_cache:
        return True
    return _load_cache(task_id)


# ===== 交互操作 =====

def do_hold(task_id, glass_ids, hold_op):
    """执行 Hold 操作。"""
    state = _report_cache.get(task_id, {}).get("state")
    if not state:
        return {"ok": False, "error": "报告未生成"}
    base_rows = state["base_rows"]
    row_map = {}
    for r in base_rows:
        gid = r["glass_id"]
        if gid not in row_map:
            row_map[gid] = r

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
    reason = f"制程参数异常 暂hold {hold_op} {now_ts}"

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
            print(f"Hold 响应: {json.dumps(resp, ensure_ascii=False)[:500]}")
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
            print(f"CancelHold 响应: {json.dumps(resp, ensure_ascii=False)[:500]}")
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
    print(f"做账内容:\n{content}")

    try:
        conn = http.client.HTTPConnection(FAB_API_HOST, FAB_API_PORT, timeout=60)
        conn.request("POST", "/api/v2/chat/completions",
                     json.dumps({"messages": [{"role": "user", "content": content}], "stream": False}),
                     {"Content-Type": "application/json", "Authorization": f"Bearer {POSTING_API_KEY}"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        conn.close()

        result_text = (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
        print(f"做账响应: {result_text[:500]}")

        if result_text.startswith("是"):
            posting_data = _load_posting_data(task_id)
            for gid in glass_ids:
                if gid not in posting_data:
                    posting_data[gid] = []
                for op in posting_ops:
                    if op not in posting_data[gid]:
                        posting_data[gid].append(op)
            _save_posting_data(task_id, posting_data)

            for r in state["base_rows"]:
                if r["glass_id"] in glass_ids:
                    existing = [s for s in (r.get("做账", "") or "").split(",") if s.strip()]
                    for op in posting_ops:
                        if op not in existing:
                            existing.append(op)
                    r["做账"] = ",".join(existing)

            return {"ok": True, "message": result_text}
        else:
            return {"ok": False, "error": result_text}
    except Exception as e:
        return {"ok": False, "error": f"请求异常: {str(e)}"}


def refresh_report(task_id):
    """刷新报告：重新执行步骤9（状态查询）+ 重新组装。"""
    state = _report_cache.get(task_id, {}).get("state")
    if not state:
        return {"ok": False, "error": "报告未生成"}

    glass_ids = _report_cache[task_id]["glass_ids"]
    data4 = _report_cache[task_id].get("data4", [])
    site = _report_cache[task_id].get("site", "DEMO-B")

    # 重建 task 对象（用于规则判断）
    task = {
        "id": task_id,
        "process_params": _report_cache[task_id].get("process_params", "[]"),
        "judge_param": _report_cache[task_id].get("judge_param", ""),
        "judge_rule": _report_cache[task_id].get("judge_rule", ""),
        "threshold1": _report_cache[task_id].get("threshold1", ""),
        "threshold2": _report_cache[task_id].get("threshold2", ""),
    }

    data9 = _run_step_9(glass_ids, site)
    new_state = _run_step_10(data4, data9, task)

    # 恢复做账数据
    posting_data = _load_posting_data(task_id)
    for r in new_state["base_rows"]:
        gid = r["glass_id"]
        if gid in posting_data:
            r["做账"] = ",".join(posting_data[gid])

    machine = _report_cache[task_id].get("machine", "")
    start_time = _report_cache[task_id].get("start_time", "")
    end_time = _report_cache[task_id].get("end_time", "")
    task_info = {"id": task_id, "site": site, "machine": machine, "start_time": start_time, "end_time": end_time}
    html = _build_html(task_info, new_state)
    _report_cache[task_id] = {
        "html": html, "state": new_state, "glass_ids": glass_ids, "site": site,
        "data4": data4, "main_process": _report_cache[task_id].get("main_process", "[]"),
        "machine": machine, "start_time": start_time, "end_time": end_time,
        "process_params": _report_cache[task_id].get("process_params", "[]"),
        "judge_param": _report_cache[task_id].get("judge_param", ""),
        "judge_rule": _report_cache[task_id].get("judge_rule", ""),
        "threshold1": _report_cache[task_id].get("threshold1", ""),
        "threshold2": _report_cache[task_id].get("threshold2", ""),
    }

    path = _report_file(task_id)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    _save_cache(task_id)

    print(f"刷新完成: {new_state['total_rows']} 行, {new_state['total_glass']} 玻璃, {new_state['risk_glass']} 异常")
    return {"ok": True}