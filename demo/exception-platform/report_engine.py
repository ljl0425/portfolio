"""OVL 异常报告引擎 — 纯逻辑模块，支持按任务参数生成独立报告。"""
import json, http.client, ssl, os, threading, time
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

COLUMNS = ["glass_id", "oper_code", "start_time", "line_id", "product_id", "recipe_id",
           "group", "目前站点", "目前Lot_ID", "egaexec_mode", "CVD_chamber", "msite", "cst_id", "hold", "做账"]

# ===== 固定模板 =====
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

# ===== 内存缓存：task_id → {html, state, glass_ids, ...} =====
_report_cache = {}

# ===== 生成状态追踪（用于超时取消和顶掉旧任务） =====
_gen_version = {}  # task_id → int, 每次新生成时递增
_gen_lock = threading.Lock()


class GenerationCancelled(Exception):
    """当前生成被新请求取代时抛出"""


def _start_generation(task_id):
    """开始新一次生成，递增版本号并返回（旧生成自动失效）"""
    with _gen_lock:
        v = _gen_version.get(task_id, 0) + 1
        _gen_version[task_id] = v
        return v


def _is_current(task_id, version):
    """检查当前生成是否仍然是最新版本"""
    with _gen_lock:
        return _gen_version.get(task_id, 0) == version

# ===== 缓存持久化 =====
def _cache_file(task_id):
    return os.path.join(OUTPUT_DIR, f"report_cache_{task_id}.json")


def _save_cache(task_id):
    """将内存缓存持久化到磁盘 JSON"""
    entry = _report_cache.get(task_id)
    if not entry:
        return
    data = {
        "glass_ids": entry.get("glass_ids", []),
        "site": entry.get("site", ""),
        "data4": entry.get("data4", []),
        "cvd_map": entry.get("cvd_map", {}),
        "state": entry.get("state", {}),
        "main_process": entry.get("main_process", "[]"),
        "machine": entry.get("machine", ""),
        "start_time": entry.get("start_time", ""),
        "end_time": entry.get("end_time", ""),
    }
    with open(_cache_file(task_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_cache(task_id):
    """从磁盘恢复缓存（如果 HTML 文件也存在）"""
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
            "cvd_map": data.get("cvd_map", {}),
            "main_process": data.get("main_process", "[]"),
            "machine": data.get("machine", ""),
            "start_time": data.get("start_time", ""),
            "end_time": data.get("end_time", ""),
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
    conn.request("POST", url, json.dumps(body, ensure_ascii=False).encode("utf-8"), headers)
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
    """循环调用 showDataByPage 直到取完所有数据，返回合并后的列表。

    注意：部分接口 countTotal 不准确（如 CVD 查询始终为 1），
    因此仅以空页判断终止，不信任 countTotal。
    """
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


def _get_spec(oper_code):
    if not oper_code:
        return None
    first = oper_code[0]
    if first == '3': return (1.4, None)
    if first == '4': return (2.0, 2.5)
    if first == '5': return (2.0, None)
    return None


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
    return os.path.join(OUTPUT_DIR, f"report_ovl_task{task_id}.html")


# ===== 步骤函数 =====

def _run_steps_3_6(token, task):
    """步骤3-6：EDC 查询 + CVD 映射"""
    site = task["site"]           # 厂别，如 "DEMO-B"
    machine = task["machine"]     # 机台，如 "DEMO-M01"
    start = task["start_time"]
    end = task["end_time"]

    # 时间格式转换：任务存储的是 "2026-08-18T08:00" → EDC 需要 "2026/08/18 08:00:00"
    def _to_edc_datetime(s):
        if not s: return "Invalid date"
        s = s.replace("T", " ").replace("-", "/")
        if s.count(":") == 1:
            s += ":00"
        return s

    start_full = _to_edc_datetime(start)
    end_full = _to_edc_datetime(end)

    # 主制程 → testOperCode：ALL 用全部，否则用勾选的拼接
    main_process_raw = task.get("main_process", "[]")
    main_process = json.loads(main_process_raw) if isinstance(main_process_raw, str) else main_process_raw
    if not main_process:
        main_process = ["ALL"]
    if "ALL" in main_process:
        test_oper_code = "('1300','2300','3300','4300','5300')"
    else:
        test_oper_code = "(" + ",".join(f"'{p}'" for p in main_process) + ")"

    # 机台 → lineId
    line_id = f"('{machine}')"

    # testEqpType 固定
    test_eqp_type = "('DEMO-PNK','DEMO-PNK2')"

    print(f"[步骤2] 主制程: {main_process} → testOperCode: {test_oper_code}, lineId: {line_id}")
    print(f"[步骤3] EDC TIME 查询: 厂别={site}, {start}~{end}...")
    edc3 = dict(EDC_COMMON)
    edc3.update({
        "selectType": "TIME",
        "endTime": f"'{end_full}'", "startTime": f"'{start_full}'",
        "testEqpType": test_eqp_type,
        "testOperCode": test_oper_code,
        "productId": "('')", "lineId": line_id,
        "paramName": "('EGAEXEC_MODE')",
    })
    r3 = _do_post("/api/yms-analysis-service/edcParam/edcParamQuery", edc3, token)
    cid3 = r3["data"]["conditionId"]

    print("[步骤4] 分页获取数据...")
    data4 = _fetch_all_pages(cid3, token)
    glass_ids = [row["item1"] for row in data4]

    print("[步骤5-6] 分批查询 CVD 数据...")
    # 去重后分批查询，每批最多 500 个 glass_id，确保覆盖率接近 100%
    unique_gids = list(set(glass_ids))
    batch_size = 500
    cvd_map = {}
    cvd_total = 0
    for batch_idx in range(0, len(unique_gids), batch_size):
        batch_gids = unique_gids[batch_idx:batch_idx + batch_size]
        edc5 = dict(EDC_COMMON)
        edc5.update({
            "selectType": "ID",
            "endTime": "'Invalid date'", "startTime": "'Invalid date'",
            "testEqpType": "('THCVD','TJCVD')", "testOperCode": "('2200')",
            "productId": "('')", "lineId": "('')",
            "paramName": "('11GAS6_N2_AVG')",
            "glassIds": batch_gids,
        })
        r5 = _do_post("/api/yms-analysis-service/edcParam/edcParamQuery", edc5, token)
        cid5 = r5["data"]["conditionId"]
        cvd_data = _fetch_all_pages(cid5, token)
        cvd_total += len(cvd_data)
        for row in cvd_data:
            gid = row["item1"]
            uid = row["item10"]
            if gid and gid not in cvd_map:
                cvd_map[gid] = uid
        batch_num = batch_idx // batch_size + 1
        total_batches = (len(unique_gids) - 1) // batch_size + 1
        print(f"  批次 {batch_num}/{total_batches}: {len(batch_gids)} 个 glass → {len(cvd_data)} 条 CVD, 映射累计 {len(cvd_map)}")
    print(f"  CVD 总计: {cvd_total} 条, 映射 {len(cvd_map)} 个 glass")
    return data4, glass_ids, cvd_map


def _build_measure_oper_code(main_process_list):
    """根据主制程列表推导量测站点：3xxx→3380/3383/3385, 4xxx→4380/4383/4385, 5xxx→5380/5383/5385"""
    if not main_process_list or "ALL" in main_process_list:
        return "('3383','3380','3385','4380','4383','4385','5380','5383','5385')"
    codes = []
    for mp in main_process_list:
        prefix = mp[:2]  # 前两位数字 33/43/53
        for suffix in ("80", "83", "85"):
            codes.append(f"'{prefix}{suffix}'")
    return "(" + ",".join(codes) + ")"


def _run_steps_7_8(token, glass_ids, main_process=None):
    """步骤7-8：量测数据查询"""
    # 解析主制程列表
    if isinstance(main_process, str):
        main_process = json.loads(main_process) if main_process else []
    if not main_process:
        main_process = ["ALL"]

    measure_oper_code = _build_measure_oper_code(main_process)

    print(f"[步骤7] newParamData... measureOperCode={measure_oper_code}")
    step7_body = {
        "associatedProcess": False, "permissionId": "paramanalysis.measureparam.dataqueryRowData",
        "userId": "DEMO-USER-ID", "site": [["DEMO-A", "DEMO-B"], ["DEMO-A", "DEMO-B"]], "factory": "ARRAY",
        "startTime": "'Invalid date'", "endTime": "'Invalid date'",
        "selectType": "ID", "timeType": "Inspect Time", "ownerType": "('')",
        "measureEqpType": "('THCDO','TJCDO')",
        "measureOperCode": measure_oper_code,
        "paramGroup": "('')", "paramName": "('')", "eqpId": "('')",
        "processFactory": None, "dataLevel": None, "processEqpType": None,
        "processOperCode": None, "processLineId": None, "processEqpId": None, "processUnitId": None,
        "rework": 2, "associatedResultColumns": [], "resultColumns": MEASURE_RESULT_COLUMNS,
        "glassIdType": "TFT GLASS ID", "glassIds": glass_ids,
    }
    r7 = _do_post("/api/yms-analysis-service/aSftpParam/newParamData", step7_body, token)
    cid7 = r7["data"]["conditionId"]

    print("[步骤8] 分页获取量测数据...")
    data8 = _fetch_all_pages(cid7, token)
    # 筛选 OVL-X1 或 OVL-Y1
    data8_ovl = [r for r in data8 if "OVL-X1" in str(r["item3"]).upper() or "OVL-Y1" in str(r["item3"]).upper()]
    return data8_ovl


def _run_step_9(glass_ids, site):
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


def _run_step_10(data4, cvd_map, data8_ovl, data9):
    step9_map = {}
    for row in data9.get("data", []):
        pn = row.get("PRODUCTNAME", "")
        if pn:
            step9_map[pn] = {
                "目前站点": row.get("PROCESSOPERATIONNAME", ""),
                "目前Lot_ID": row.get("LOTNAME", ""),
            }

    base_rows = []
    for row in data4:
        gid = row["item1"]
        s9 = step9_map.get(gid, {})
        base_rows.append({
            "glass_id": gid,
            "oper_code": row["item2"],
            "egaexec_mode": row["item6"],
            "start_time": row["item7"],
            "line_id": row["item8"],
            "product_id": row["item15"],
            "recipe_id": row["item17"],
            "目前站点": s9.get("目前站点", ""),
            "目前Lot_ID": s9.get("目前Lot_ID", ""),
            "cst_id": row["item21"],
            "做账": "",
        })

    for r in base_rows:
        r["CVD_chamber"] = cvd_map.get(r["glass_id"], "")

    ovl_glass_set = set()
    ovl_oper_map = {}
    ovl_detail = []
    for row in data8_ovl:
        gid = row["item2"]
        ovl_glass_set.add(gid)
        if gid not in ovl_oper_map:
            ovl_oper_map[gid] = row["item1"]
        ovl_detail.append({
            "glass_id": gid,
            "oper_code": row["item1"],
            "site_no": row["item4"],
            "param_name": row["item3"],
            "param_value": row["item7"],
            "measure_x": row["item12"],
            "measure_y": row["item13"],
            "eqp_id": row["item30"],
            "glass_judge": row["item38"],
            "start_time": row["item6"],
            "end_time": row["item5"],
        })

    for r in base_rows:
        if r["glass_id"] in ovl_glass_set:
            r["has_ovl"] = True
            r["量测站点"] = ovl_oper_map.get(r["glass_id"], "")
        else:
            r["has_ovl"] = False
            r["量测站点"] = ""

    base_rows.sort(key=lambda r: r["start_time"], reverse=True)

    spec_info = _get_spec(ovl_detail[0]["oper_code"]) if ovl_detail else None
    spec_line = spec_info[0] if spec_info else None
    db_line = spec_info[1] if spec_info else None
    has_db_line = db_line is not None

    # 按 recipe_id 分组：给每个 base_row 加上 group 字段
    for r in base_rows:
        recipe = r.get("recipe_id", "") or ""
        r["group"] = recipe[:4] if len(recipe) >= 4 else recipe

    # 收集所有 group 和 recipe_id
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

    # 超标 glass（按 spec 判断）
    exceeded_gids = set()
    for d in ovl_detail:
        spec = _get_spec(d["oper_code"])
        if spec:
            try:
                if abs(float(d["param_value"])) > spec[0]:
                    exceeded_gids.add(d["glass_id"])
            except:
                pass

    # 风险区间：按 recipe_id 分组独立计算
    # 构建每个 recipe_id 的时间排序列表
    recipe_gid_order = {}  # recipe_id -> [gid1, gid2, ...] 按 start_time 升序
    for r in sorted(base_rows, key=lambda x: x["start_time"]):
        rid = r["recipe_id"]
        if rid not in recipe_gid_order:
            recipe_gid_order[rid] = []
        recipe_gid_order[rid].append(r["glass_id"])

    recipe_gid_to_idx = {}
    for rid, gids in recipe_gid_order.items():
        recipe_gid_to_idx[rid] = {g: i for i, g in enumerate(gids)}

    risk_gids = set()
    for rid, ordered_gids in recipe_gid_order.items():
        gid_to_idx_local = recipe_gid_to_idx[rid]
        # 该 recipe 下的超标 glass
        rid_exceeded = [g for g in exceeded_gids if g in gid_to_idx_local]
        for gid in rid_exceeded:
            idx = gid_to_idx_local.get(gid, -1)
            if idx < 0:
                continue
            prev_ok = 0
            for i in range(idx - 1, -1, -1):
                pg = ordered_gids[i]
                if any(d["glass_judge"] == "OK" for d in ovl_detail if d["glass_id"] == pg):
                    prev_ok = i + 1
                    break
            next_ok = len(ordered_gids) - 1
            for i in range(idx + 1, len(ordered_gids)):
                ng = ordered_gids[i]
                if any(d["glass_judge"] == "OK" for d in ovl_detail if d["glass_id"] == ng):
                    next_ok = i - 1
                    break
            for i in range(prev_ok, next_ok + 1):
                risk_gids.add(ordered_gids[i])

    for r in base_rows:
        r["is_risk"] = r["glass_id"] in risk_gids

    chart_param = {}
    chart_glass_list = set()
    for d in ovl_detail:
        gid = d["glass_id"]
        chart_glass_list.add(gid)
        pn = d["param_name"]
        if pn not in chart_param:
            chart_param[pn] = {}
        if gid not in chart_param[pn]:
            chart_param[pn][gid] = []
        try:
            chart_param[pn][gid].append({"site": int(d["site_no"]), "value": float(d["param_value"])})
        except:
            pass
    for pn in chart_param:
        for gid in chart_param[pn]:
            chart_param[pn][gid].sort(key=lambda x: x["site"])

    chart_glass_list = sorted(chart_glass_list)

    # 记录每个 glass 是否有绝对值 >5 的量测值（用于图表默认不勾选）
    chart_has_overshoot = {}
    for pn in chart_param:
        chart_has_overshoot[pn] = {}
        for gid in chart_param[pn]:
            chart_has_overshoot[pn][gid] = any(abs(pt["value"]) > 5 for pt in chart_param[pn][gid])

    chart_data_js = {}
    for pn in sorted(chart_param.keys()):
        chart_data_js[pn] = {}
        for gid in sorted(chart_param[pn].keys()):
            chart_data_js[pn][gid] = chart_param[pn][gid]

    bar_data = {}
    for r in base_rows:
        site = r["目前站点"] or "(空)"
        bar_data[site] = bar_data.get(site, 0) + 1
    bar_labels = sorted(bar_data.keys())
    bar_values = [bar_data[k] for k in bar_labels]

    return {
        "base_rows": base_rows,
        "ovl_detail": ovl_detail,
        "chart_data_js": chart_data_js,
        "chart_glass_list": chart_glass_list,
        "bar_labels": bar_labels,
        "bar_values": bar_values,
        "spec_line": spec_line,
        "db_line": db_line,
        "has_db_line": has_db_line,
        "total_glass": len(base_rows),
        "measured_glass": len(ovl_glass_set),
        "risk_glass": len(risk_gids),
        "groups": groups_sorted,
        "recipe_ids": recipe_ids_sorted,
        "chart_has_overshoot": chart_has_overshoot,
    }


# ===== 备注持久化 =====

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


def _color_file(task_id):
    return os.path.join(OUTPUT_DIR, f"ovl_color_{task_id}.json")


def _load_color_data(task_id):
    path = _color_file(task_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_color_data(task_id, data):
    with open(_color_file(task_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ===== 分析建议引擎 =====

def _analyze_ovl(state, filter_group=None, filter_recipe=None):
    """根据筛选后的数据生成分析建议。返回 HTML 字符串。"""
    base_rows = state["base_rows"]
    ovl_detail = state["ovl_detail"]
    spec_line = state["spec_line"]

    # 筛选 base_rows
    filtered_rows = base_rows
    if filter_group:
        filtered_rows = [r for r in filtered_rows if r.get("group", "") == filter_group]
    if filter_recipe:
        filtered_rows = [r for r in filtered_rows if r.get("recipe_id", "") == filter_recipe]

    filtered_gids = {r["glass_id"] for r in filtered_rows}

    # 构建辅助数据结构
    # glass_id -> {param_name: [{site, value}, ...]}
    ovl_by_gid = {}
    for d in ovl_detail:
        gid = d["glass_id"]
        if gid not in filtered_gids:
            continue
        if gid not in ovl_by_gid:
            ovl_by_gid[gid] = {}
        pn = d["param_name"]
        if pn not in ovl_by_gid[gid]:
            ovl_by_gid[gid][pn] = []
        try:
            ovl_by_gid[gid][pn].append({"site": int(d["site_no"]), "value": float(d["param_value"])})
        except:
            pass

    # 每个 glass 的 judge 状态
    gid_judge = {}
    for d in ovl_detail:
        gid = d["glass_id"]
        if d.get("glass_judge") == "OK":
            gid_judge[gid] = "OK"

    # 按 start_time 排序（升序）
    ordered_rows = sorted(filtered_rows, key=lambda r: r["start_time"])

    # spec
    spec = spec_line or 1.4

    results = []

    for i, r in enumerate(ordered_rows):
        gid = r["glass_id"]
        if gid not in ovl_by_gid:
            continue

        # 判断是否 NG
        is_ng = False
        for pn, sites in ovl_by_gid[gid].items():
            for s in sites:
                if abs(s["value"]) > spec:
                    is_ng = True
                    break
            if is_ng:
                break
        if not is_ng:
            continue

        # 找前后有量测的片
        prev_gid = None
        for j in range(i - 1, -1, -1):
            if ordered_rows[j]["glass_id"] in ovl_by_gid:
                prev_gid = ordered_rows[j]["glass_id"]
                break
        next_gid = None
        for j in range(i + 1, len(ordered_rows)):
            if ordered_rows[j]["glass_id"] in ovl_by_gid:
                next_gid = ordered_rows[j]["glass_id"]
                break

        # 趋势分析：对比 NG 片与前后片的 OVL 值
        trend_divergent = False
        if prev_gid and next_gid:
            # 取第一个 param_name 的所有 site 值对比
            ng_pn = list(ovl_by_gid[gid].keys())[0]
            ng_sites = {s["site"]: s["value"] for s in ovl_by_gid[gid].get(ng_pn, [])}
            prev_sites = {s["site"]: s["value"] for s in ovl_by_gid.get(prev_gid, {}).get(ng_pn, [])}
            next_sites = {s["site"]: s["value"] for s in ovl_by_gid.get(next_gid, {}).get(ng_pn, [])}

            prev_diff = 0
            next_diff = 0
            common_sites = set(ng_sites.keys()) & set(prev_sites.keys()) & set(next_sites.keys())
            if common_sites:
                prev_diffs = [abs(ng_sites[s] - prev_sites[s]) for s in common_sites]
                next_diffs = [abs(ng_sites[s] - next_sites[s]) for s in common_sites]
                prev_diff = sum(prev_diffs) / len(prev_diffs) if prev_diffs else 0
                next_diff = sum(next_diffs) / len(next_diffs) if next_diffs else 0

            # 趋势离异判断：差值 > 0.6 或 NG 与前后片方向相反
            if prev_diff > 0.6 or next_diff > 0.6:
                trend_divergent = True
            else:
                # 检查方向：NG 往上，前后片往下（或反过来）
                ng_avg = sum(ng_sites.values()) / len(ng_sites) if ng_sites else 0
                prev_avg = sum(prev_sites.values()) / len(prev_sites) if prev_sites else 0
                next_avg = sum(next_sites.values()) / len(next_sites) if next_sites else 0
                ng_dir = ng_avg - prev_avg
                next_dir = next_avg - ng_avg
                if (ng_dir > 0.1 and next_dir < -0.1) or (ng_dir < -0.1 and next_dir > 0.1):
                    trend_divergent = True

        ega_mode = (r.get("egaexec_mode") or "").strip()
        cvd_chamber = r.get("CVD_chamber") or ""
        recipe_id = r.get("recipe_id") or ""

        if not trend_divergent:
            results.append(f"<b>{gid}</b> (recipe={recipe_id}): 离异趋势不大，制程正常波动")
            continue

        # 趋势离异，分 sample 和 Object
        if ega_mode.upper() == "SAMPLE":
            # 查同 recipe + 同 chamber 的量测数据
            same_chamber_gids = [rr["glass_id"] for rr in filtered_rows
                                 if rr.get("CVD_chamber") == cvd_chamber
                                 and rr["glass_id"] != gid
                                 and rr.get("recipe_id") == recipe_id]
            same_chamber_measured = [sg for sg in same_chamber_gids if sg in ovl_by_gid]

            if not same_chamber_measured:
                # 推荐同 chamber 的 glass
                recommended = _recommend_glass(ordered_rows, ovl_by_gid, gid, cvd_chamber, "SAMPLE")
                if not recommended:
                    recommended = _recommend_glass(ordered_rows, ovl_by_gid, gid, cvd_chamber, "OBJECT")
                rec_str = ", ".join(recommended) if recommended else "暂无"
                results.append(
                    f"<b>{gid}</b> (recipe={recipe_id}): sample片NG，离异趋势大，"
                    f"暂无同chamber量测片，建议加检同chamber glass: {rec_str}")
            else:
                # 有同 chamber 量测，看趋势是否一致
                consistent = _check_chamber_consistency(ovl_by_gid, gid, same_chamber_measured)
                if consistent:
                    results.append(
                        f"<b>{gid}</b> (recipe={recipe_id}): 该 <b>{cvd_chamber}</b> CVD chamber "
                        f"与其他chamber差异较大导致NG")
                else:
                    recommended = _recommend_glass(ordered_rows, ovl_by_gid, gid, cvd_chamber, "SAMPLE")
                    rec_str = ", ".join(recommended) if recommended else "暂无"
                    results.append(
                        f"<b>{gid}</b> (recipe={recipe_id}): 该 <b>{cvd_chamber}</b> chamber "
                        f"与其他chamber差异较大，建议加检同chamber sample片: {rec_str}")

        else:  # Object 或其他
            same_chamber_gids = [rr["glass_id"] for rr in filtered_rows
                                 if rr.get("CVD_chamber") == cvd_chamber
                                 and rr["glass_id"] != gid
                                 and rr.get("recipe_id") == recipe_id]
            same_chamber_measured = [sg for sg in same_chamber_gids if sg in ovl_by_gid]

            if same_chamber_measured:
                consistent = _check_chamber_consistency(ovl_by_gid, gid, same_chamber_measured)
                if consistent:
                    results.append(
                        f"<b>{gid}</b> (recipe={recipe_id}): <b>{cvd_chamber}</b> CVD chamber "
                        f"与其他chamber差异较大")
                else:
                    results.append(
                        f"<b>{gid}</b> (recipe={recipe_id}): NG片 <b>{cvd_chamber}</b> chamber "
                        f"可能受sample片影响，建议加检同chamber sample片: "
                        f"{', '.join(_recommend_glass(ordered_rows, ovl_by_gid, gid, cvd_chamber, 'SAMPLE')) or '暂无'}")
            else:
                recommended = _recommend_glass(ordered_rows, ovl_by_gid, gid, cvd_chamber, "SAMPLE")
                if not recommended:
                    recommended = _recommend_glass(ordered_rows, ovl_by_gid, gid, cvd_chamber, "OBJECT")
                rec_str = ", ".join(recommended) if recommended else "暂无"
                results.append(
                    f"<b>{gid}</b> (recipe={recipe_id}): 离异趋势大，"
                    f"暂无同chamber量测片，建议加检同chamber glass: {rec_str}")

    if not results:
        return '<div style="color:#9a9ab0;">当前筛选条件下无 NG glass，无需分析</div>'

    return "<br>".join(results)


def _recommend_glass(ordered_rows, ovl_by_gid, ng_gid, chamber, target_mode):
    """推荐离 NG glass 最近的同 chamber、指定 EGAEXEC_MODE 的 glass（最多 2 片）。"""
    ng_idx = None
    for i, r in enumerate(ordered_rows):
        if r["glass_id"] == ng_gid:
            ng_idx = i
            break
    if ng_idx is None:
        return []

    candidates = []
    for i, r in enumerate(ordered_rows):
        if i == ng_idx:
            continue
        mode = (r.get("egaexec_mode") or "").strip().upper()
        if mode == target_mode.upper() and r.get("CVD_chamber") == chamber:
            dist = abs(i - ng_idx)
            candidates.append((dist, r["glass_id"]))

    candidates.sort(key=lambda x: x[0])
    return [c[1] for c in candidates[:2]]


def _check_chamber_consistency(ovl_by_gid, ng_gid, same_chamber_gids):
    """检查 NG 片与同 chamber 其他片的趋势是否一致。"""
    if not same_chamber_gids:
        return False
    ng_data = ovl_by_gid.get(ng_gid, {})
    if not ng_data:
        return False
    first_pn = list(ng_data.keys())[0]
    ng_values = {s["site"]: s["value"] for s in ng_data[first_pn]}

    consistent_count = 0
    for sg in same_chamber_gids:
        sg_data = ovl_by_gid.get(sg, {}).get(first_pn, [])
        if not sg_data:
            continue
        sg_values = {s["site"]: s["value"] for s in sg_data}
        common = set(ng_values.keys()) & set(sg_values.keys())
        if not common:
            continue
        diffs = [abs(ng_values[s] - sg_values[s]) for s in common]
        avg_diff = sum(diffs) / len(diffs)
        if avg_diff < 0.6:
            consistent_count += 1

    return consistent_count >= len(same_chamber_gids) / 2


# ===== HTML 生成 =====

def _build_html(task, state):
    task_id = task["id"]
    site = task["site"]
    machine = task["machine"]
    start = task["start_time"]
    end = task["end_time"]
    base_rows = state["base_rows"]
    ovl_detail = state["ovl_detail"]
    chart_data_js = state["chart_data_js"]
    chart_glass_list = state["chart_glass_list"]
    bar_labels = state["bar_labels"]
    bar_values = state["bar_values"]
    spec_line = state["spec_line"]
    db_line = state["db_line"]
    has_db_line = state["has_db_line"]
    total_glass = state["total_glass"]
    measured_glass = state["measured_glass"]
    risk_glass = state["risk_glass"]

    now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    hold_data = _load_hold_data(task_id)
    posting_data = _load_posting_data(task_id)
    color_data = _load_color_data(task_id)

    base_rows_js = []
    for r in base_rows:
        gid = r["glass_id"]
        hd = hold_data.get(gid, [])
        hold_display = " | ".join(
            [f"{h['operation']}{'✓' if h['status'] == 'ok' else '✗'}" for h in hd]
        ) if hd else ""
        base_rows_js.append({
            "glass_id": gid,
            "oper_code": r["oper_code"],
            "egaexec_mode": r["egaexec_mode"] or "",
            "start_time": r["start_time"],
            "line_id": r["line_id"],
            "product_id": r["product_id"] or "",
            "recipe_id": r["recipe_id"] or "",
            "group": r["group"] or "",
            "目前站点": r["目前站点"] or "",
            "目前Lot_ID": r["目前Lot_ID"] or "",
            "CVD_chamber": r["CVD_chamber"] or "",
            "msite": r["量测站点"] or "",
            "cst_id": r["cst_id"] or "",
            "hold": hold_display,
            "做账": ",".join(posting_data.get(gid, [])),
            "has_ovl": r["has_ovl"],
            "is_risk": r["is_risk"],
            "color_mark": color_data.get(gid, ""),
        })

    ovl_detail_js = []
    for d in ovl_detail:
        ovl_detail_js.append({
            "glass_id": d["glass_id"],
            "oper_code": d["oper_code"],
            "site_no": d["site_no"],
            "param_name": d["param_name"],
            "param_value": d["param_value"],
            "measure_x": d["measure_x"] or "",
            "measure_y": d["measure_y"] or "",
            "eqp_id": d["eqp_id"] or "",
            "glass_judge": d["glass_judge"] or "",
            "start_time": d["start_time"] or "",
        })

    html = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>OVL异常报告 — 任务 #""" + str(task_id) + r"""</title>
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
.tab-btn { padding:12px 20px; color:var(--text-secondary); font-size:13px; cursor:pointer; border:none; background:none; border-bottom:2px solid transparent; }
.tab-btn:hover { color:var(--text-primary); }
.tab-btn.active { color:var(--text-title); border-bottom-color:var(--text-title); }
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
.left { width:55%; background:var(--bg-card-light); border-right:1px solid var(--border-color); display:flex; flex-direction:column; overflow:hidden; }
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
thead th { background:var(--bg-table-header); position:sticky; top:0; padding:4px 8px; text-align:left; border-bottom:2px solid #d0d0d0; white-space:nowrap; z-index:2; overflow:hidden; text-overflow:ellipsis; position:relative; }
thead th .resize-handle { position:absolute; right:0; top:0; bottom:0; width:5px; cursor:col-resize; z-index:3; }
thead th .resize-handle:hover { background:rgba(79,209,197,0.3); }
thead th.filter-th { top:26px; z-index:1; padding:2px 4px; background:var(--bg-light); border-bottom:1px solid var(--border-light); }
thead th.filter-th input { width:100%; border:1px solid var(--border-light); border-radius:2px; padding:2px 4px; font-size:10px; }
td { padding:3px 8px; border-bottom:1px solid var(--border-lighter); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; user-select:text !important; }
tr:hover td { background:#f0f8ff; }
tr.ovl td { color:#2196F3; }
tr.risk td { background:#fff3f3; }
tr.risk.ovl td { color:#2196F3; background:#fff3f3; }
tr.selected td { background:#c8e6ff !important; }
tr.selected:hover td { background:#bbdefb !important; }
tr.selected.risk td { background:#ffcdd2 !important; }
tr[data-color] td { transition:background 0.2s; }
tr[data-color]:not(.selected) td { background:var(--row-color) !important; }
tr[data-color].selected td { background:var(--row-color) !important; filter:brightness(0.92); }
.selection-box { position:absolute; border:2px dashed var(--accent); background:rgba(79,209,197,0.1); pointer-events:none; z-index:99; display:none; }
.right { width:45%; padding:12px; overflow-y:auto; background:var(--bg-light); }
.chart-box { background:var(--bg-card-light); border:1px solid var(--border-color); border-radius:6px; padding:12px; margin-bottom:12px; }
.chart-box h3 { font-size:13px; margin-bottom:8px; color:var(--text-dark); }
.chart-box canvas { width:100%; height:260px; }
.spec-legend { font-size:10px; color:#999; margin-top:4px; line-height:1.6; }
.tab-content { display:none; flex:1; overflow:hidden; }
.tab-content.active { display:flex; flex-direction:column; }
.tab-content#tab-detail { background:#f5f5f5; overflow-y:auto; }
body.light .tab-content#tab-detail { background:#fafafa; }
.detail-table { padding:16px; background:var(--bg-card-light); }
.detail-table h2 { font-size:15px; margin-bottom:12px; }
.empty { color:#ccc; }
.bar-chart-box { background:var(--bg-card-light); border:1px solid var(--border-color); border-radius:6px; padding:12px; margin-bottom:12px; }
.bar-chart-box h3 { font-size:13px; margin-bottom:8px; color:var(--text-dark); }
.bar-chart-box canvas { width:100%; height:260px; }
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
    <h1>异常处理报告 — 量测异常 / OVL</h1>
    <div class="info">
      <b>任务编号:</b> #""" + str(task_id) + r""" &nbsp;|&nbsp; <b>站点:</b> """ + site + r""" &nbsp;|&nbsp; <b>机台:</b> """ + machine + r"""<br>
      <b>时间范围:</b> """ + start + r""" ~ """ + end + r""" &nbsp;|&nbsp; <b>主流程:</b> ALL<br>
      <b>生成时间:</b> """ + now_ts + r"""
    </div>
  </div>
  <div class="header-notes">
    <h3>📝 交接 <button class="btn" onclick="expandNotes()" style="font-size:11px;padding:2px 10px;background:#4fd1c5;color:#fff;border:none;border-radius:3px;cursor:pointer;">展开</button></h3>
    <div id="report-notes" contenteditable="true" placeholder="在此输入交接内容...（支持粘贴图片）">""" + _load_notes(task_id) + r"""</div>
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
""" + "\n".join(f'<option value="{g}">{g}</option>' for g in sorted(state.get("groups", {}).keys())) + r"""
      </select>
      <label style="font-size:10px;color:#9a9ab0;">Recipe:</label>
      <select id="filter-recipe" onchange="onFilterChange()" style="background:#1a1a2e;color:#e2e2ee;border:1px solid #444;border-radius:3px;padding:2px 6px;font-size:11px;">
        <option value="">全部</option>
""" + "\n".join(f'<option value="{r}">{r}</option>' for r in state.get("recipe_ids", [])) + r"""
      </select>
    </div>
    <div id="analysis-box" style="font-size:11px;color:#ccc;line-height:1.6;max-height:80px;overflow-y:auto;">
      加载分析建议中...
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
      <table id="main-table" style="min-width:1400px;">
        <thead>
          <tr>
            <th>glass_id</th>
            <th>oper_code</th>
            <th>start_time</th>
            <th>line_id</th>
            <th>product_id</th>
            <th>recipe_id</th>
            <th>group</th>
            <th>目前站点</th>
            <th>目前Lot_ID</th>
            <th>EGAEXEC_MODE</th>
            <th>CVD_chamber</th>
            <th>量测站点</th>
            <th>cst_id</th>
            <th>Hold</th>
            <th>做账</th>
          </tr>
          <tr>
            <th class="filter-th"><input id="f-glass_id" oninput="applyFilters()" placeholder="筛选..."></th>
            <th class="filter-th"><input id="f-oper_code" oninput="applyFilters()" placeholder="筛选..."></th>
            <th class="filter-th"><input id="f-start_time" oninput="applyFilters()" placeholder="筛选..."></th>
            <th class="filter-th"><input id="f-line_id" oninput="applyFilters()" placeholder="筛选..."></th>
            <th class="filter-th"><input id="f-product_id" oninput="applyFilters()" placeholder="筛选..."></th>
            <th class="filter-th"><input id="f-recipe_id" oninput="applyFilters()" placeholder="筛选..."></th>
            <th class="filter-th"><input id="f-group" oninput="applyFilters()" placeholder="筛选..."></th>
            <th class="filter-th"><input id="f-目前站点" oninput="applyFilters()" placeholder="筛选..."></th>
            <th class="filter-th"><input id="f-目前Lot_ID" oninput="applyFilters()" placeholder="筛选..."></th>
            <th class="filter-th"><input id="f-egaexec_mode" oninput="applyFilters()" placeholder="筛选..."></th>
            <th class="filter-th"><input id="f-CVD_chamber" oninput="applyFilters()" placeholder="筛选..."></th>
            <th class="filter-th"><input id="f-msite" oninput="applyFilters()" placeholder="筛选..."></th>
            <th class="filter-th"><input id="f-cst_id" oninput="applyFilters()" placeholder="筛选..."></th>
            <th class="filter-th"><input id="f-hold" oninput="applyFilters()" placeholder="筛选..."></th>
            <th class="filter-th"><input id="f-做账" oninput="applyFilters()" placeholder="筛选..."></th>
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
</div>

<!-- ===== 量测详情 Tab ===== -->
<div class="tab-content" id="tab-detail">
  <div class="detail-table">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
      <h2 style="margin:0;">量测数据详情（共 """ + str(len(ovl_detail)) + r""" 条）</h2>
    </div>
    <div style="overflow:auto; max-height:70vh;">
      <table><thead><tr>
        <th>glass_id</th><th>oper_code</th><th>site_no</th><th>param_name</th>
        <th>param_value</th><th>measure_x</th><th>measure_y</th>
        <th>eqp_id</th><th>glass_judge</th><th>start_time</th>
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
var ALL_DETAIL = """ + json.dumps(ovl_detail_js, ensure_ascii=False) + r""";
var CHART_DATA = """ + json.dumps(chart_data_js, ensure_ascii=False) + r""";
var CHART_GLASS_LIST = """ + json.dumps(chart_glass_list, ensure_ascii=False) + r""";
var CHART_OVERSHOOT = """ + json.dumps(state.get("chart_has_overshoot", {}), ensure_ascii=False) + r""";
var COLUMNS = """ + json.dumps(COLUMNS) + r""";
var COLUMN_STORE_KEY = 'ovl_columns_' + TASK_ID;
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
var SPEC_LINE = """ + str(spec_line or 0) + r""";
var DB_LINE = """ + str(db_line or 0) + r""";
var HAS_DB_LINE = """ + json.dumps(has_db_line) + r""";
var BAR_LABELS = """ + json.dumps(bar_labels, ensure_ascii=False) + r""";
var BAR_VALUES = """ + json.dumps(bar_values) + r""";
var VALID_HOLD_OPS = """ + json.dumps(VALID_HOLD_OPS) + r""";
var VALID_POSTING_OPS = """ + json.dumps(VALID_POSTING_OPS) + r""";
var GROUPS = """ + json.dumps(state.get("groups", {}), ensure_ascii=False) + r""";
var RECIPE_IDS = """ + json.dumps(state.get("recipe_ids", []), ensure_ascii=False) + r""";
var selectedGids = {};
var chartGlassChecked = {};
var currentRows = ALL_ROWS;
var isDragging = false, dragStartX = 0, dragStartY = 0;
var didDrag = false;  // 标记刚完成框选，防止 click 事件重复 toggle

function renderTable(rows) {
  var tbody = document.getElementById('table-body');
  var html = '';
  rows.forEach(function(r) {
    var cls = '';
    if (r.is_risk && r.has_ovl) cls = 'risk ovl';
    else if (r.is_risk) cls = 'risk';
    else if (r.has_ovl) cls = 'ovl';
    if (selectedGids[r.glass_id]) cls += ' selected';
    var style = '';
    if (r.color_mark) style = ' data-color="1" style="--row-color:' + r.color_mark + '"';
    var vals = CURRENT_COLUMNS.map(function(c) {
      if (c === 'msite') return r.msite || '<span class="empty">-</span>';
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

function applyFilters() {
  var filters = {};
  CURRENT_COLUMNS.forEach(function(c) {
    var el = document.getElementById('f-' + c);
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
      if (c === 'msite') val = r.msite;
      else if (c === 'hold') val = r.hold;
      else if (c === '做账') val = r['做账'];
      else val = r[c];
      return (val || '').toLowerCase().indexOf(fv) !== -1;
    });
  });
  renderTable(currentRows);
  refreshCharts();
  updateStats();
}

// ===== Group/Recipe 筛选联动 =====
function onFilterChange() {
  var selGroup = document.getElementById('filter-group').value;
  var recipeSel = document.getElementById('filter-recipe');
  var prevVal = recipeSel.value;
  // 更新 recipe 下拉
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
  // 尝试恢复之前的选中值
  var found = false;
  for (var i = 0; i < recipeSel.options.length; i++) {
    if (recipeSel.options[i].value === prevVal) { recipeSel.value = prevVal; found = true; break; }
  }
  if (!found) recipeSel.value = '';
  applyFilters();
  updateAnalysis();
}

// ===== 更新统计 =====
function updateStats() {
  var rows = currentRows.length > 0 ? currentRows : ALL_ROWS;
  var gids = rows.map(function(r) { return r.glass_id; });
  var measured = 0, risk = 0;
  rows.forEach(function(r) {
    if (r.has_ovl) measured++;
    if (r.is_risk) risk++;
  });
  document.getElementById('stat-total').textContent = rows.length;
  document.getElementById('stat-measured').textContent = measured;
  document.getElementById('stat-risk').textContent = risk;
  document.getElementById('stat-selected').textContent = Object.keys(selectedGids).length;
}

// ===== 分析建议 =====
function updateAnalysis() {
  var selGroup = document.getElementById('filter-group').value;
  var selRecipe = document.getElementById('filter-recipe').value;
  var box = document.getElementById('analysis-box');
  box.innerHTML = '分析中...';
  fetch('/api/report/' + TASK_ID + '/analyze', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({group: selGroup || null, recipe: selRecipe || null})
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    box.innerHTML = data.html || '<div style="color:#9a9ab0;">当前筛选条件下无 NG glass，无需分析</div>';
  })
  .catch(function() { box.innerHTML = '<div style="color:#ef4444;">分析加载失败</div>'; });
}

// ===== 框选 =====
(function(){
  var selBox = document.getElementById('selection-box');
  var tableWrap = document.getElementById('table-wrap');
  tableWrap.addEventListener('mousedown', function(e) {
    if (e.button !== 0) return;
    if (e.target.tagName === 'BUTTON' || e.target.tagName === 'INPUT') return;
    // 如果正在选中文字则跳过框选
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
      // 单击：由 click 事件处理，这里只重置标志
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
    updateStats(); refreshCharts();
  });
})();

// ===== 单击选中行（兼容文字复制和框选） =====
document.getElementById('table-body').addEventListener('click', function(e) {
  if (didDrag) { didDrag = false; return; }  // 框选刚完成，跳过 click
  if (window.getSelection().toString().length > 0) return;  // 正在复制文字，不切换选中
  var tr = e.target.closest('tr');
  if (!tr || !tr.getAttribute('data-gid')) return;
  var gid = tr.getAttribute('data-gid');
  if (selectedGids[gid]) { delete selectedGids[gid]; tr.classList.remove('selected'); }
  else { selectedGids[gid] = true; tr.classList.add('selected'); }
  updateStats(); refreshCharts();
});

// ===== 列宽拖拽调整 =====
(function(){
  var table = document.getElementById('main-table');
  var resizeHandle = null, startX = 0, startWidth = 0, thEl = null;

  // 初始化：给每个表头加拖拽手柄
  function initResizeHandles() {
    var headers = table.querySelectorAll('thead tr:first-child th');
    headers.forEach(function(th) {
      var handle = document.createElement('div');
      handle.className = 'resize-handle';
      th.appendChild(handle);
    });
  }

  table.addEventListener('mousedown', function(e) {
    if (!e.target.classList.contains('resize-handle')) return;
    e.preventDefault();
    e.stopPropagation();
    resizeHandle = e.target;
    thEl = resizeHandle.parentElement;
    startX = e.clientX;
    startWidth = thEl.offsetWidth;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  });

  document.addEventListener('mousemove', function(e) {
    if (!resizeHandle) return;
    var diff = e.clientX - startX;
    var newWidth = Math.max(30, startWidth + diff);
    thEl.style.width = newWidth + 'px';
    thEl.style.minWidth = newWidth + 'px';
  });

  document.addEventListener('mouseup', function() {
    if (!resizeHandle) return;
    resizeHandle = null;
    thEl = null;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  });

  initResizeHandles();
})();

function selectAll() { currentRows.forEach(function(r) { selectedGids[r.glass_id] = true; }); renderTable(currentRows); refreshCharts(); }
function clearSelect() { selectedGids = {}; renderTable(currentRows); refreshCharts(); }

// ===== 颜色标记 =====
var COLOR_STORE_KEY = 'ovl_color_' + TASK_ID;
var pickedColor = '';

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
var cancelHoldData = [];  // [{reasonCode, operation, gids, count}]

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
    // 更新前端 hold 列：移除取消成功的 operation
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

  // 解析站点：支持英文逗号、中文逗号、空格分隔
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
      // 更新 ALL_ROWS 中对应行的做账列
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

// ===== 交接展开 =====
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
  if (!confirm('刷新将重新查询 YMS 获取最新数据（步骤7-9），确定继续？')) return;
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

// ===== 图表 =====
function refreshCharts() {
  var area = document.getElementById('chart-area');
  var selectedList = Object.keys(selectedGids);
  var targetParams;
  if (selectedList.length > 0) {
    targetParams = Object.keys(CHART_DATA).filter(function(pn) { return Object.keys(CHART_DATA[pn]).some(function(g) { return selectedGids[g]; }); });
  } else { targetParams = Object.keys(CHART_DATA); }
  var oldBoxes = area.querySelectorAll('.chart-box:not(.bar-chart-box)');
  oldBoxes.forEach(function(b) { b.remove(); });
  var barBox = document.getElementById('bar-chart-box');
  targetParams.forEach(function(pn) {
    var box = document.createElement('div'); box.className = 'chart-box';
    var pnSafe = pn.replace(/[^a-zA-Z0-9\u4e00-\u9fff]/g, '_');
    box.setAttribute('data-param', pnSafe);
    var checkHtml = '';
    CHART_GLASS_LIST.forEach(function(gid) {
      if (!CHART_DATA[pn] || !CHART_DATA[pn][gid]) return;
      // 首次初始化：>5 的量测值默认不勾选
      var key = pn + '|' + gid;
      if (!(key in chartGlassChecked)) {
        var hasOvershoot = CHART_OVERSHOOT[pn] && CHART_OVERSHOOT[pn][gid];
        chartGlassChecked[key] = !hasOvershoot;
      }
      var checked = chartGlassChecked[key] ? 'checked' : '';
      checkHtml += '<label style="display:inline-block;margin-right:12px;font-size:11px;cursor:pointer;">' +
        '<input type="checkbox" class="chart-gid-check" data-pn="' + pnSafe + '" data-gid="' + gid + '" ' + checked + ' onchange="onChartCheckChange()"> ' + gid + '</label>';
    });
    box.innerHTML = '<h3>' + pn + ' — 量测值折线图</h3>' +
      '<div style="margin-bottom:8px;">' + checkHtml + '</div>' +
      '<canvas id="chart_' + pnSafe + '"></canvas>' +
      '<div class="spec-legend">规格线: ±' + SPEC_LINE + (HAS_DB_LINE ? ' | Database线: ±' + DB_LINE : '') + '</div>';
    barBox.parentNode.insertBefore(box, barBox.nextSibling);
  });
  setTimeout(function() { targetParams.forEach(function(pn) { drawChart('chart_' + pn.replace(/[^a-zA-Z0-9\u4e00-\u9fff]/g, '_'), pn); }); }, 50);
}

function onChartCheckChange() {
  document.querySelectorAll('.chart-box:not(.bar-chart-box)').forEach(function(box) {
    var pnSafe = box.getAttribute('data-param'), paramName = null;
    Object.keys(CHART_DATA).forEach(function(pn) { if (pn.replace(/[^a-zA-Z0-9\u4e00-\u9fff]/g, '_') === pnSafe) paramName = pn; });
    if (!paramName) return;
    box.querySelectorAll('.chart-gid-check').forEach(function(cb) { chartGlassChecked[paramName + '|' + cb.getAttribute('data-gid')] = cb.checked; });
    drawChart('chart_' + pnSafe, paramName);
  });
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

function drawChart(canvasId, paramName) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;
  var ctx = canvas.getContext('2d');
  var box = canvas.parentElement;
  var W = box.clientWidth - 24, H = 260;
  canvas.width = W; canvas.height = H;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  var pad = {top:30, right:20, bottom:50, left:60};
  var pw = W - pad.left - pad.right, ph = H - pad.top - pad.bottom;
  var allVals = [], allSites = [];
  var activeSeries = [];
  Object.keys(CHART_DATA[paramName] || {}).sort().forEach(function(gid) {
    var checked = (chartGlassChecked[paramName + '|' + gid] !== false);
    if (!checked) return;
    var pts = CHART_DATA[paramName][gid];
    activeSeries.push({name: gid, points: pts});
    pts.forEach(function(p) { allVals.push(p.value); allSites.push(p.site); });
  });
  if (activeSeries.length === 0) { ctx.fillStyle='#999'; ctx.fillText('无勾选数据', 10, 20); return; }
  var minSite = Math.min.apply(null, allSites), maxSite = Math.max.apply(null, allSites);
  var siteRange = maxSite - minSite || 1;
  var minV = Math.min.apply(null, allVals), maxV = Math.max.apply(null, allVals);
  if (SPEC_LINE) { minV = Math.min(minV, -SPEC_LINE*1.3); maxV = Math.max(maxV, SPEC_LINE*1.3); }
  if (DB_LINE && HAS_DB_LINE) { minV = Math.min(minV, -DB_LINE*1.1); maxV = Math.max(maxV, DB_LINE*1.1); }
  var range = maxV - minV || 1;
  ctx.fillStyle = '#fafafa'; ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = '#eee'; ctx.lineWidth = 0.5;
  for (var i = 0; i <= 5; i++) {
    var y = pad.top + ph * i / 5;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(W - pad.right, y); ctx.stroke();
    ctx.fillStyle = '#999'; ctx.font = '10px Microsoft YaHei';
    ctx.fillText((maxV - range * i / 5).toFixed(3), 2, y + 3);
  }
  var colors = ['#4fd1c5','#60a5fa','#fbbf24','#f87171','#a78bfa','#34d399','#f472b6','#818cf8','#fb923c','#e879f9'];
  if (SPEC_LINE) {
    [SPEC_LINE, -SPEC_LINE].forEach(function(v) {
      var sy = pad.top + ph - ph * (v - minV) / range;
      ctx.strokeStyle = '#f87171'; ctx.lineWidth = 1; ctx.setLineDash([4,4]);
      ctx.beginPath(); ctx.moveTo(pad.left, sy); ctx.lineTo(W - pad.right, sy); ctx.stroke();
      ctx.setLineDash([]); ctx.fillStyle = '#f87171'; ctx.font = '9px Microsoft YaHei';
      ctx.fillText('spec ' + (v>0?'+':'') + v, W - pad.right - 60, sy - 3);
    });
  }
  if (DB_LINE && HAS_DB_LINE) {
    [DB_LINE, -DB_LINE].forEach(function(v) {
      var dy = pad.top + ph - ph * (v - minV) / range;
      ctx.strokeStyle = '#fbbf24'; ctx.lineWidth = 1; ctx.setLineDash([2,6]);
      ctx.beginPath(); ctx.moveTo(pad.left, dy); ctx.lineTo(W - pad.right, dy); ctx.stroke();
      ctx.setLineDash([]); ctx.fillStyle = '#fbbf24'; ctx.font = '9px Microsoft YaHei';
      ctx.fillText('db ' + (v>0?'+':'') + v, W - pad.right - 60, dy - 3);
    });
  }
  ctx.fillStyle = '#666'; ctx.font = '9px Microsoft YaHei';
  var step = Math.max(1, Math.floor(allSites.length / 15));
  for (var i = 0; i < allSites.length; i += step) {
    var x = pad.left + pw * (allSites[i] - minSite) / siteRange;
    ctx.fillText(allSites[i], x - 8, H - pad.bottom + 15);
  }
  activeSeries.forEach(function(s, si) {
    var pts = s.points;
    ctx.strokeStyle = colors[si % colors.length]; ctx.lineWidth = 2;
    ctx.beginPath();
    pts.forEach(function(p, i) {
      var x = pad.left + pw * (p.site - minSite) / siteRange;
      var y = pad.top + ph - ph * (p.value - minV) / range;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = colors[si % colors.length];
    pts.forEach(function(p) { var x = pad.left + pw * (p.site - minSite) / siteRange; var y = pad.top + ph - ph * (p.value - minV) / range; ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI*2); ctx.fill(); });
    ctx.fillStyle = colors[si % colors.length]; ctx.font = '11px Microsoft YaHei';
    ctx.fillText(s.name, pad.left + 90 * si, pad.top - 10);
  });

  // 鼠标悬停 tooltip
  canvas.onmousemove = function(e) {
    var rect = canvas.getBoundingClientRect();
    var mx = e.clientX - rect.left, my = e.clientY - rect.top;
    var minDist = Infinity, nearest = null;
    activeSeries.forEach(function(s) {
      s.points.forEach(function(p) {
        var px = pad.left + pw * (p.site - minSite) / siteRange;
        var py = pad.top + ph - ph * (p.value - minV) / range;
        var dist = Math.sqrt((mx-px)*(mx-px) + (my-py)*(my-py));
        if (dist < minDist && dist < 20) { minDist = dist; nearest = {gid: s.name, site: p.site, value: p.value, x: px, y: py}; }
      });
    });
    var tip = document.getElementById('chart-tooltip');
    if (nearest) {
      tip.style.display = 'block';
      tip.style.left = (rect.left + nearest.x + 12) + 'px';
      tip.style.top = (rect.top + nearest.y - 20) + 'px';
      tip.innerHTML = '<b>' + nearest.gid + '</b><br>site: ' + nearest.site + ' | value: ' + nearest.value.toFixed(3);
    } else {
      tip.style.display = 'none';
    }
  };
  canvas.onmouseleave = function() { document.getElementById('chart-tooltip').style.display = 'none'; };
}

function renderDetail() {
  var tbody = document.getElementById('detail-body');
  if (!tbody) return;
  var html = '';
  ALL_DETAIL.forEach(function(d) {
    html += '<tr>' +
      '<td>' + d.glass_id + '</td><td>' + d.oper_code + '</td><td>' + d.site_no + '</td>' +
      '<td>' + d.param_name + '</td><td>' + d.param_value + '</td>' +
      '<td>' + (d.measure_x || '<span class="empty">-</span>') + '</td>' +
      '<td>' + (d.measure_y || '<span class="empty">-</span>') + '</td>' +
      '<td>' + (d.eqp_id || '<span class="empty">-</span>') + '</td>' +
      '<td>' + (d.glass_judge || '<span class="empty">-</span>') + '</td>' +
      '<td>' + (d.start_time || '<span class="empty">-</span>') + '</td>' +
      '</tr>';
  });
  tbody.innerHTML = html;
}

function switchTab(name, evt) {
  document.querySelectorAll('.tab-content').forEach(function(t){ t.classList.remove('active'); });
  document.querySelectorAll('.tab-btn').forEach(function(b){ b.classList.remove('active'); });
  document.getElementById('tab-'+name).classList.add('active');
  (evt || event).target.classList.add('active');
  if (name === 'list') setTimeout(function() { drawBarChart(); refreshCharts(); }, 100);
}

function downloadExcel() {
  var a = document.createElement('a');
  a.href = '/api/report/' + TASK_ID + '/excel';
  a.download = '异常报告_任务' + TASK_ID + '.xlsx';
  a.click();
}

// ===== 明暗模式切换 =====
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
  drawBarChart();
  refreshCharts();
}
function updateThemeBtn() {
  var btn = document.getElementById('btn-theme');
  if (btn) {
    btn.textContent = document.body.classList.contains('light') ? '🌙 暗色' : '☀️ 亮色';
  }
}

window.onload = function() { loadColorMarks(); initColumnDrag(); applyColumnOrder(); renderTable(ALL_ROWS); renderDetail(); drawBarChart(); refreshCharts(); updateAnalysis(); };
</script>
</body>
</html>"""

    return html


# ===== 公开 API =====

GENERATE_TIMEOUT = 180  # 单次生成超时秒数（3分钟）
GENERATE_MAX_RETRIES = 1  # 超时后最多重试次数


def _build_demo_state(task):
    """DEMO 模式：构造纯演示的 OVL 报告 state（不调用 FAB API）。

    产出与 _run_step_10 相同结构的 state，供 _build_html 渲染。
    所有标识符均使用 DEMO- 前缀，不含任何真实数据。
    """
    oper_code = "3400"
    spec_line = 1.4        # 与 _get_spec('3xxx') → (1.4, None) 一致
    db_line = None
    has_db_line = False

    recipe_id = "DEMO-RCP-001"
    product_id = "DEMO-PROD-01"
    group = recipe_id[:4]   # "DEMO"

    # 10 片玻璃：8 OK + 2 NG（NG 超规 → 标记为风险）
    glass_spec = [(f"DEMO-G{i:03d}", "OK", round(0.4 + (i % 5) * 0.15, 2)) for i in range(1, 9)]
    glass_spec += [("DEMO-G009", "NG", 2.25), ("DEMO-G010", "NG", -1.85)]

    base_rows = []
    ovl_detail = []
    chart_param = {}          # param_name -> glass_id -> [{site, value}]

    n = len(glass_spec)
    for idx, (gid, judge, base) in enumerate(glass_spec):
        ts = (datetime.now() - timedelta(minutes=(n - idx) * 7)).strftime("%Y-%m-%d %H:%M:%S")
        is_risk = (judge == "NG")
        base_rows.append({
            "glass_id": gid,
            "oper_code": oper_code,
            "egaexec_mode": "AUTO",
            "start_time": ts,
            "line_id": f"DEMO-LINE-{idx % 3 + 1}",
            "product_id": product_id,
            "recipe_id": recipe_id,
            "目前站点": f"DEMO-OP{idx % 5 + 1:03d}",
            "目前Lot_ID": f"DEMO-LOT-{idx + 1:03d}",
            "cst_id": f"DEMO-CST-{idx + 1:03d}",
            "做账": "",
            "CVD_chamber": f"CH-{idx % 3 + 1}",
            "has_ovl": True,
            "量测站点": f"OVL-{idx % 5 + 1}",
            "is_risk": is_risk,
            "group": group,
        })

        pn = "OVL_X"
        chart_param.setdefault(pn, {})[gid] = []
        for s in range(1, 6):
            delta = round(((s + idx) % 3 - 1) * 0.08, 2)
            val = round(base + delta, 3)
            chart_param[pn][gid].append({"site": s, "value": val})
            ovl_detail.append({
                "glass_id": gid,
                "oper_code": oper_code,
                "site_no": str(s),
                "param_name": pn,
                "param_value": val,
                "measure_x": f"{val:.3f}",
                "measure_y": f"{delta:.3f}",
                "eqp_id": "DEMO-EQP-01",
                "glass_judge": judge,
                "start_time": ts,
                "end_time": ts,
            })

    chart_data_js = {}
    chart_has_overshoot = {}
    for pn in sorted(chart_param.keys()):
        chart_data_js[pn] = {}
        chart_has_overshoot[pn] = {}
        for gid in sorted(chart_param[pn].keys()):
            pts = sorted(chart_param[pn][gid], key=lambda x: x["site"])
            chart_data_js[pn][gid] = pts
            chart_has_overshoot[pn][gid] = any(abs(p["value"]) > 5 for p in pts)
    chart_glass_list = sorted({d["glass_id"] for d in ovl_detail})

    bar_data = {}
    for r in base_rows:
        k = r["目前站点"] or "(空)"
        bar_data[k] = bar_data.get(k, 0) + 1
    bar_labels = sorted(bar_data.keys())
    bar_values = [bar_data[k] for k in bar_labels]

    return {
        "base_rows": base_rows,
        "ovl_detail": ovl_detail,
        "chart_data_js": chart_data_js,
        "chart_glass_list": chart_glass_list,
        "bar_labels": bar_labels,
        "bar_values": bar_values,
        "spec_line": spec_line,
        "db_line": db_line,
        "has_db_line": has_db_line,
        "total_glass": len(base_rows),
        "measured_glass": len(chart_glass_list),
        "risk_glass": sum(1 for r in base_rows if r["is_risk"]),
        "groups": {group: [recipe_id]},
        "recipe_ids": [recipe_id],
        "chart_has_overshoot": chart_has_overshoot,
    }


def _do_generate(task, version):
    """实际执行生成流程（每步检查版本，被取消时抛出 GenerationCancelled）"""
    task_id = task["id"]

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    print(f"\n{'='*50}")
    print(f"生成任务 #{task_id} 的 OVL 报告 (v{version})...")
    print(f"{'='*50}")

    # DEMO 模式：跳过 FAB token 与外部 API，直接用演示数据构造报告
    if os.environ.get("DEMO_MODE", "").strip() == "1":
        print("[DEMO] 模拟报告生成过程（演示用延时）...")
        time.sleep(2.5)
        state = _build_demo_state(task)
        html = _build_html(task, state)
        _report_cache[task_id] = {
            "html": html,
            "state": state,
            "glass_ids": [r["glass_id"] for r in state["base_rows"]],
            "site": task.get("site", ""),
            "data4": [],
            "cvd_map": {},
            "main_process": task.get("main_process", "[]"),
            "machine": task.get("machine", ""),
            "start_time": task.get("start_time", ""),
            "end_time": task.get("end_time", ""),
        }
        path = _report_file(task_id)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        _save_cache(task_id)
        print(f"DEMO 报告已生成并写入: {path}")
        return html

    token = _get_fab_token()
    print(f"token: {token}")

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    site = task["site"]
    data4, glass_ids, cvd_map = _run_steps_3_6(token, task)
    print(f"步骤4: {len(data4)} 行, 步骤6: {len(cvd_map)} CVD 映射")

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    data8_ovl = _run_steps_7_8(token, glass_ids, task.get("main_process"))
    print(f"步骤8: {len(data8_ovl)} OVL 行")

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    data9 = _run_step_9(glass_ids, site)
    print(f"步骤9: {len(data9.get('data', []))} 条")

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    state = _run_step_10(data4, cvd_map, data8_ovl, data9)
    print(f"步骤10: {state['total_glass']} 玻璃, 有量测: {state['measured_glass']}, 风险: {state['risk_glass']}")

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    html = _build_html(task, state)
    print(f"HTML 已生成 ({len(html)} 字符)")

    # 缓存（仅当仍然是最新版本时才写入，防止旧线程覆盖新结果）
    if not _is_current(task_id, version):
        print(f"任务 #{task_id} v{version} 已被取代，跳过缓存写入")
        raise GenerationCancelled("已被新请求取代")

    _report_cache[task_id] = {"html": html, "state": state, "glass_ids": glass_ids, "site": site,
                              "data4": data4, "cvd_map": cvd_map, "main_process": task.get("main_process", "[]"),
                              "machine": task.get("machine", ""), "start_time": task.get("start_time", ""),
                              "end_time": task.get("end_time", "")}

    # 写入磁盘
    path = _report_file(task_id)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    _save_cache(task_id)
    print(f"已写入磁盘: {path}")

    return html


def generate_report(task):
    """生成报告，带超时和重试。返回 HTML 字符串。"""
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
            # 超时：线程仍在跑，版本号已失效，旧线程下次检查会自动退出
            if attempt < GENERATE_MAX_RETRIES:
                print(f"任务 #{task_id} 第 {attempt + 1} 次生成超时（>{GENERATE_TIMEOUT}s），重试...")
                version = _start_generation(task_id)  # 新版本号，旧线程自动失效
                continue
            else:
                print(f"任务 #{task_id} 生成超时，已达最大重试次数")
                raise RuntimeError(f"报告生成超时（>{GENERATE_TIMEOUT}s），已重试 {GENERATE_MAX_RETRIES} 次仍失败")

        if error[0] is not None:
            if isinstance(error[0], GenerationCancelled):
                raise error[0]
            raise error[0]

        return result[0]

    raise RuntimeError("报告生成失败")


def get_report(task_id):
    """获取已缓存的报告 HTML（优先内存，其次磁盘）"""
    if task_id in _report_cache:
        return _report_cache[task_id].get("html")
    # 尝试从磁盘恢复
    if _load_cache(task_id):
        return _report_cache[task_id].get("html")
    return None


def get_report_state(task_id):
    """获取已缓存的报告数据状态"""
    if task_id not in _report_cache:
        _load_cache(task_id)
    return _report_cache.get(task_id, {}).get("state")


def get_report_glass_ids(task_id):
    """获取已缓存的 glass_ids"""
    if task_id not in _report_cache:
        _load_cache(task_id)
    return _report_cache.get(task_id, {}).get("glass_ids", [])


def has_report(task_id):
    """检查是否已有缓存报告（内存或磁盘）"""
    if task_id in _report_cache:
        return True
    return _load_cache(task_id)
    return task_id in _report_cache


def do_hold(task_id, glass_ids, hold_op):
    """执行 Hold 操作，返回每条 glass 的结果"""
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
    reason = f"OVL NG 暂hold {hold_op} {now_ts}"

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
    """取消 Hold 操作。

    cancel_records: [{"reasonCode": ..., "operation": ..., "gids": [...]}, ...]
    每组发一次 cancelHold HTTP 请求。
    """
    site = _report_cache.get(task_id, {}).get("site", "DEMO-B")
    hold_data = _load_hold_data(task_id)

    results = {}
    for rec in cancel_records:
        reason_code = rec["reasonCode"]
        operation = rec["operation"]
        involved_gids = rec["gids"]
        # 取选中 glass 和该记录涉及 glass 的交集
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

    # 更新 hold_data：删除取消成功的记录
    for gid, result in results.items():
        if result["status"] == "ok" and gid in hold_data:
            hold_data[gid] = [h for h in hold_data[gid]
                              if h["operation"] != result["operation"]]
            if not hold_data[gid]:
                del hold_data[gid]
    _save_hold_data(task_id, hold_data)

    # 清除报告 HTML 缓存，下次访问重新生成 HTML（hold 列会刷新）
    if task_id in _report_cache:
        _report_cache[task_id]["html"] = None
    path = _report_file(task_id)
    if os.path.exists(path):
        os.remove(path)

    return {"ok": True, "results": results}


def get_hold_summary(task_id, glass_ids):
    """获取选中 glass 的 hold 记录汇总，供取消 Hold 弹窗使用。
    返回: [(reasonCode, operation, involved_gids, count), ...]
    """
    hold_data = _load_hold_data(task_id)
    groups = {}  # (reasonCode, operation) -> set of gids
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
    """执行做账操作。posting_ops 为站点列表，reason 为做账原因。"""
    state = _report_cache.get(task_id, {}).get("state")
    if not state:
        return {"ok": False, "error": "报告未生成"}

    site = _report_cache[task_id].get("site", "DEMO-B")

    # 校验站点
    invalid = [op for op in posting_ops if op not in VALID_POSTING_OPS]
    if invalid:
        return {"ok": False, "error": f"以下站点不可在此页面做账: {', '.join(invalid)}"}

    # 构建请求内容
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

        # 判断结果：API 成功时返回以"是"开头的内容（如"是做账成功xxx"），失败时返回"不是\n做账失败..."
        if result_text.startswith("是"):
            # 保存做账记录
            posting_data = _load_posting_data(task_id)
            for gid in glass_ids:
                if gid not in posting_data:
                    posting_data[gid] = []
                for op in posting_ops:
                    if op not in posting_data[gid]:
                        posting_data[gid].append(op)
            _save_posting_data(task_id, posting_data)

            # 更新 state 中的 base_rows
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
    """刷新报告：重新执行步骤7-9"""
    state = _report_cache.get(task_id, {}).get("state")
    if not state:
        return {"ok": False, "error": "报告未生成"}

    glass_ids = _report_cache[task_id]["glass_ids"]
    data4 = _report_cache[task_id].get("data4", [])
    cvd_map = _report_cache[task_id].get("cvd_map", {})
    site = _report_cache[task_id].get("site", "DEMO-B")
    main_process = _report_cache[task_id].get("main_process", "[]")

    # 获取 token
    token = _get_fab_token()

    data8_ovl = _run_steps_7_8(token, glass_ids, main_process)
    data9 = _run_step_9(glass_ids, site)
    new_state = _run_step_10(data4, cvd_map, data8_ovl, data9)

    # 恢复做账数据到 state
    posting_data = _load_posting_data(task_id)
    for r in new_state["base_rows"]:
        gid = r["glass_id"]
        if gid in posting_data:
            r["做账"] = ",".join(posting_data[gid])

    # 重新生成 HTML — 使用缓存中的任务信息
    machine = _report_cache[task_id].get("machine", "")
    start_time = _report_cache[task_id].get("start_time", "")
    end_time = _report_cache[task_id].get("end_time", "")
    task_info = {"id": task_id, "site": site, "machine": machine, "start_time": start_time, "end_time": end_time}
    html = _build_html(task_info, new_state)
    _report_cache[task_id] = {"html": html, "state": new_state, "glass_ids": glass_ids, "site": site,
                              "data4": data4, "cvd_map": cvd_map, "main_process": main_process,
                              "machine": machine, "start_time": start_time, "end_time": end_time}

    path = _report_file(task_id)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    _save_cache(task_id)

    print(f"刷新完成: {new_state['total_glass']} 玻璃, 有量测: {new_state['measured_glass']}, 风险: {new_state['risk_glass']}")
    return {"ok": True}