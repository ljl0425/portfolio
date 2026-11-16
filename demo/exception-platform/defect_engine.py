"""Defect 异常报告引擎 — 纯逻辑模块，支持按任务参数生成独立报告。

工作流：
- 步骤1：提取 defect_code、defect_size（默认 0）
- 步骤2：获取 token（同 OVL）
- 步骤3：POST newDefectQuery（processEqpType=DEMO-PHL,DEMO-PHL2）→ conditionId1
- 步骤4：POST newDefectQuery（processEqpType=DEMO-WET,DEMO-WET2, processOperCode=2401,3402）→ conditionId2
- 步骤5：两个 conditionId 分别分页取数据
- 步骤6：待定（先输出原始数据到 HTML 供分析）
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

# ===== Defect 查询的 RESULT_COLUMNS（来自 newDefectQuery 接口） =====
DEFECT_RESULT_COLUMNS = [
    {"column_name": "factory", "column_desc": "FACTORY", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "glass_id", "column_desc": "GLASS_ID", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "product_id", "column_desc": "PRODUCT_ID", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "start_time", "column_desc": "START_TIME", "column_define": "Date", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "end_time", "column_desc": "END_TIME", "column_define": "Date", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "line_id", "column_desc": "LINE_ID", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "eqp_id", "column_desc": "EQP_ID", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "eqp_type", "column_desc": "EQP_TYPE", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "mark_type", "column_desc": "MARK_TYPE", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "oper_code", "column_desc": "OPER_CODE", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "owner_code", "column_desc": "OWNER_CODE", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "glass_judge", "column_desc": "GLASS_JUDGE", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "panel_id", "column_desc": "PANEL_ID", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "panel_no", "column_desc": "PANEL_NO", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "defect_code", "column_desc": "DEFECT_CODE", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "defect_no", "column_desc": "DEFECT_NO", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "defect_size", "column_desc": "DEFECT_SIZE", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "defect_size_flag", "column_desc": "DEFECT_SIZE_FLAG", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "image_data", "column_desc": "IMAGE_DATA", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "x1", "column_desc": "X1", "column_define": "Number", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "x2", "column_desc": "X2", "column_define": "Number", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "x3", "column_desc": "X3", "column_define": "Number", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "y1", "column_desc": "Y1", "column_define": "Number", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "y2", "column_desc": "Y2", "column_define": "Number", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "y3", "column_desc": "Y3", "column_define": "Number", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "data1", "column_desc": "DATA1", "column_define": "Number", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "data2", "column_desc": "DATA2", "column_define": "Number", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "data3", "column_desc": "DATA3", "column_define": "Number", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "gate1", "column_desc": "GATE1", "column_define": "Number", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "gate2", "column_desc": "GATE2", "column_define": "Number", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "gate3", "column_desc": "GATE3", "column_define": "Number", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "product_group", "column_desc": "PRODUCT_GROUP", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "cst_id", "column_desc": "CST_ID", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "item21", "column_desc": "TAPE_NUMBERS_OF_TIMES", "column_define": "Number", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "item24", "column_desc": "BEFORE_POLISH_HEIGHT", "column_define": "Number", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "item25", "column_desc": "AFTER_POLISH_HEIGHT", "column_define": "Number", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "item27", "column_desc": "REVIEW", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "item42", "column_desc": "ADC", "column_define": "Number", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "item43", "column_desc": "RTIR", "column_define": "Number", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "item44", "column_desc": "REPAIR_REVIEW", "column_define": "Number", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "item51", "column_desc": "POSITION_CHANGE", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "item110", "column_desc": "OPERATOR_ID", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "item118", "column_desc": "DEFECT_FLAG_5", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "item128", "column_desc": "COMBINE_PROCESS_ID", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "item126", "column_desc": "DEFECT_X", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "item127", "column_desc": "DEFECT_Y", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
    {"column_name": "std_defect_desc_cn", "column_desc": "DEFECT_DESC_CN", "column_define": "String", "table_name": "a_r_ftp_defect_t", "default_flag": "Y"},
]

DEFECT_ASSOCIATED_RESULT_COLUMNS = [
    {"column_name": "event_time", "column_desc": "EVENT_TIME", "column_define": "Date", "table_name": "a_r_mes_glass_special_t", "column_no": 7, "default_flag": "Y"},
    {"column_name": "line_id", "column_desc": "LINE_ID", "column_define": "String", "table_name": "a_r_mes_glass_special_t", "column_no": 9, "default_flag": "Y"},
]

# 根据 DEFECT_RESULT_COLUMNS 顺序构建 column_name → itemN 的映射
_DEFECT_COL_INDEX = {}
for _i, _col in enumerate(DEFECT_RESULT_COLUMNS):
    _DEFECT_COL_INDEX[_col["column_name"]] = f"item{_i + 1}"

# Defect 查询公共模板
DEFECT_QUERY_COMMON = {
    "associatedProcess": True,
    "dataLevel": "line",
    "defectGroup": "('')",
    "defectType": "('multi_defect')",
    "factory": "ARRAY",
    "ownerCode": "('')",
    "ownerType": "('')",
    "permissionId": "defectanalysis.defectdetail.rawdataDefect",
    "processFactory": "ARRAY",
    "processLineId": "('')",
    "processEqpId": "('')",
    "processUnitId": "('')",
    "processSlotId": "('')",
    "productGroup": "('')",
    "productId": "('')",
    "rework": 2,
    "selectType": "FALL BACK",
    "site": [["DEMO-A", "DEMO-B"], ["DEMO-A", "DEMO-B"]],
    "noCode": False,
    "startTime": None,
    "endTime": None,
    "testLineId": "('')",
    "testEqpId": "('')",
    "testEqpType": "('TJMRE')",
    "testOperCode": None,
    "timeType": "Inspect Time",
    "userId": "DEMO-USER-ID",
    "defectCode": None,
    "processEqpType": None,
    "processOperCode": None,
    "resultColumns": DEFECT_RESULT_COLUMNS,
    "associatedResultColumns": DEFECT_ASSOCIATED_RESULT_COLUMNS,
    "glassIdType": "TFT GLASS ID",
}

# 主站点 → testOperCode 映射（用于 defect 查询）
MAIN_PROCESS_TO_TEST_OPER = {
    "1300": "18R0",
    "2300": "28R0",
    "3300": "38R0",
    "4300": "48R0",
    "5300": "58R0",
}

# 主站点 → processOperCode 映射（用于步骤4）
MAIN_PROCESS_TO_PROCESS_OPER_STEP4 = {
    "1300": "1401",
    "2300": "2401",
    "3300": "3402",
    "4300": "4402",
    "5300": "5402",
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
    return os.path.join(OUTPUT_DIR, f"defect_cache_{task_id}.json")


def _report_file(task_id):
    return os.path.join(OUTPUT_DIR, f"defect_report_task{task_id}.html")


def _save_cache(task_id):
    entry = _report_cache.get(task_id)
    if not entry:
        return
    data = {
        "glass_ids": entry.get("glass_ids", []),
        "site": entry.get("site", ""),
        "data3": entry.get("data3", []),
        "data4": entry.get("data4", []),
        "state": entry.get("state", {}),
        "main_process": entry.get("main_process", "[]"),
        "machine": entry.get("machine", ""),
        "start_time": entry.get("start_time", ""),
        "end_time": entry.get("end_time", ""),
        "defect_code": entry.get("defect_code", ""),
        "defect_size": entry.get("defect_size", ""),
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
            "data3": data.get("data3", []),
            "data4": data.get("data4", []),
            "main_process": data.get("main_process", "[]"),
            "machine": data.get("machine", ""),
            "start_time": data.get("start_time", ""),
            "end_time": data.get("end_time", ""),
            "defect_code": data.get("defect_code", ""),
            "defect_size": data.get("defect_size", ""),
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


def _get_fab_token():
    """步骤2：获取 fab token（同 OVL）"""
    conn = http.client.HTTPConnection(FAB_API_HOST, FAB_API_PORT, timeout=30)
    conn.request("POST", "/api/v1/chat/completions",
                 json.dumps({"messages": [{"role": "user", "content": "1"}], "stream": False}),
                 {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"})
    resp = conn.getresponse()
    token = json.loads(resp.read().decode())["choices"][0]["message"]["content"].strip()
    conn.close()
    return token


def _fetch_all_pages(condition_id, token, page_size=50):
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


# ===== 参数推导 =====
def _derive_params(task):
    """从 task 对象提取参数，推导 defect 查询请求体所需字段。"""
    site = task["site"]
    machine = task["machine"]
    start = task["start_time"]
    end = task["end_time"]
    defect_code = task.get("defect_code", "")
    defect_size = task.get("defect_size") or "0"

    def _to_edc_datetime(s):
        if not s:
            return "Invalid date"
        s = s.replace("T", " ").replace("-", "/")
        if s.count(":") == 1:
            s += ":00"
        return s

    start_full = _to_edc_datetime(start)
    end_full = _to_edc_datetime(end)

    # 主制程 → processOperCode 和 testOperCode
    main_process_raw = task.get("main_process", "[]")
    main_process = json.loads(main_process_raw) if isinstance(main_process_raw, str) else main_process_raw
    if not main_process:
        main_process = ["ALL"]

    if "ALL" in main_process:
        # ALL 时：所有主站点
        all_main = ["1300", "2300", "3300", "4300", "5300"]
        process_oper_code = "(" + ",".join(f"'{p}'" for p in all_main) + ")"
        test_oper_codes = [MAIN_PROCESS_TO_TEST_OPER[p] for p in all_main]
        test_oper_code = "(" + ",".join(f"'{t}'" for t in test_oper_codes) + ")"
        # 步骤4 的 processOperCode
        step4_opers = [MAIN_PROCESS_TO_PROCESS_OPER_STEP4[p] for p in all_main]
        step4_process_oper = "(" + ",".join(f"'{o}'" for o in step4_opers) + ")"
    else:
        process_oper_code = "(" + ",".join(f"'{p}'" for p in main_process) + ")"
        test_oper_codes = [MAIN_PROCESS_TO_TEST_OPER.get(p, f"{p[0]}8R0") for p in main_process]
        test_oper_code = "(" + ",".join(f"'{t}'" for t in test_oper_codes) + ")"
        step4_opers = [MAIN_PROCESS_TO_PROCESS_OPER_STEP4.get(p, f"{p[0]}402") for p in main_process]
        step4_process_oper = "(" + ",".join(f"'{o}'" for o in step4_opers) + ")"

    print(f"[参数推导] 主制程: {main_process}")
    print(f"[参数推导] defect_code: {defect_code}, defect_size: {defect_size}")
    print(f"[参数推导] processOperCode: {process_oper_code}")
    print(f"[参数推导] testOperCode: {test_oper_code}")
    print(f"[参数推导] 步骤4 processOperCode: {step4_process_oper}")

    return {
        "site": site,
        "machine": machine,
        "start_full": start_full,
        "end_full": end_full,
        "defect_code": defect_code,
        "defect_size": defect_size,
        "process_oper_code": process_oper_code,
        "test_oper_code": test_oper_code,
        "step4_process_oper": step4_process_oper,
    }


# ===== 步骤3：PHL 站点 defect 查询 =====
def _run_step_3(token, params):
    """步骤3：POST newDefectQuery（processEqpType=DEMO-PHL,DEMO-PHL2）→ conditionId1"""
    print(f"[步骤3] PHL defect 查询: defectCode={params['defect_code']}, "
          f"processOperCode={params['process_oper_code']}, testOperCode={params['test_oper_code']}")

    body = dict(DEFECT_QUERY_COMMON)
    body.update({
        "defectCode": f"('{params['defect_code']}')",
        "startTime": f"'{params['start_full']}'",
        "endTime": f"'{params['end_full']}'",
        "processOperCode": params["process_oper_code"],
        "processEqpType": "('DEMO-PHL','DEMO-PHL2')",
        "testOperCode": params["test_oper_code"],
    })

    r3 = _do_post("/api/yms-analysis-service/defectNew/newDefectQuery", body, token)
    cid3 = r3["data"]["conditionId"]
    print(f"  conditionId (PHL): {cid3}")
    return cid3


# ===== 步骤4：WET 站点 defect 查询 =====
def _run_step_4(token, params):
    """步骤4：POST newDefectQuery（processEqpType=DEMO-WET,DEMO-WET2, processOperCode=2401,3402）→ conditionId2"""
    print(f"[步骤4] WET defect 查询: defectCode={params['defect_code']}, "
          f"processOperCode={params['step4_process_oper']}")

    body = dict(DEFECT_QUERY_COMMON)
    body.update({
        "defectCode": f"('{params['defect_code']}')",
        "startTime": f"'{params['start_full']}'",
        "endTime": f"'{params['end_full']}'",
        "processOperCode": params["step4_process_oper"],
        "processEqpType": "('DEMO-WET','DEMO-WET2')",
        "testOperCode": params["test_oper_code"],
    })

    r4 = _do_post("/api/yms-analysis-service/defectNew/newDefectQuery", body, token)
    cid4 = r4["data"]["conditionId"]
    print(f"  conditionId (WET): {cid4}")
    return cid4


# ===== 步骤5：分页获取两个 conditionId 的数据 =====
def _run_step_5(token, cid3, cid4):
    """步骤5：分别用两个 conditionId 分页获取数据"""
    print("[步骤5] 获取 PHL condition 数据...")
    data3 = _fetch_all_pages(cid3, token)
    print(f"  PHL 数据: {len(data3)} 条")

    print("[步骤5] 获取 WET condition 数据...")
    data4 = _fetch_all_pages(cid4, token)
    print(f"  WET 数据: {len(data4)} 条")

    return data3, data4


# ===== 步骤6：数据整合（待定，先输出原始数据） =====
def _run_step_6(data3, data4, params):
    """步骤6：暂时返回原始数据用于分析"""
    # 提取所有 glass_id
    glass_ids_3 = []
    for row in data3:
        gid = row.get(_DEFECT_COL_INDEX["glass_id"], "")
        if gid:
            glass_ids_3.append(gid)

    glass_ids_4 = []
    for row in data4:
        gid = row.get(_DEFECT_COL_INDEX["glass_id"], "")
        if gid:
            glass_ids_4.append(gid)

    all_glass_ids = list(set(glass_ids_3 + glass_ids_4))

    # 取样前几条数据用于展示
    sample3 = data3[:5] if len(data3) > 5 else data3
    sample4 = data4[:5] if len(data4) > 5 else data4

    # 统计 defect_code 分布
    defect_codes_3 = {}
    for row in data3:
        dc = row.get(_DEFECT_COL_INDEX["defect_code"], "") or "(空)"
        defect_codes_3[dc] = defect_codes_3.get(dc, 0) + 1

    defect_codes_4 = {}
    for row in data4:
        dc = row.get(_DEFECT_COL_INDEX["defect_code"], "") or "(空)"
        defect_codes_4[dc] = defect_codes_4.get(dc, 0) + 1

    # 统计 oper_code 分布
    oper_codes_3 = {}
    for row in data3:
        oc = row.get(_DEFECT_COL_INDEX["oper_code"], "") or "(空)"
        oper_codes_3[oc] = oper_codes_3.get(oc, 0) + 1

    oper_codes_4 = {}
    for row in data4:
        oc = row.get(_DEFECT_COL_INDEX["oper_code"], "") or "(空)"
        oper_codes_4[oc] = oper_codes_4.get(oc, 0) + 1

    # 收集所有字段名（用于展示）
    all_fields = [col["column_name"] for col in DEFECT_RESULT_COLUMNS]

    return {
        "data3": data3,
        "data4": data4,
        "sample3": sample3,
        "sample4": sample4,
        "glass_ids_3": glass_ids_3,
        "glass_ids_4": glass_ids_4,
        "all_glass_ids": all_glass_ids,
        "defect_codes_3": defect_codes_3,
        "defect_codes_4": defect_codes_4,
        "oper_codes_3": oper_codes_3,
        "oper_codes_4": oper_codes_4,
        "all_fields": all_fields,
        "total_3": len(data3),
        "total_4": len(data4),
        "unique_glass_3": len(set(glass_ids_3)),
        "unique_glass_4": len(set(glass_ids_4)),
        "unique_glass_total": len(all_glass_ids),
    }


# ===== HTML 生成 =====
def _build_html(task, state):
    """生成数据预览 HTML（步骤6 待定，先展示原始数据）"""
    task_id = task["id"]
    site = task["site"]
    machine = task["machine"]
    start = task["start_time"]
    end = task["end_time"]
    defect_code = task.get("defect_code", "")
    defect_size = task.get("defect_size") or "0"

    now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 数据摘要
    total_3 = state.get("total_3", 0)
    total_4 = state.get("total_4", 0)
    unique_glass_3 = state.get("unique_glass_3", 0)
    unique_glass_4 = state.get("unique_glass_4", 0)
    unique_glass_total = state.get("unique_glass_total", 0)

    sample3 = state.get("sample3", [])
    sample4 = state.get("sample4", [])
    all_fields = state.get("all_fields", [])

    defect_codes_3 = state.get("defect_codes_3", {})
    defect_codes_4 = state.get("defect_codes_4", {})
    oper_codes_3 = state.get("oper_codes_3", {})
    oper_codes_4 = state.get("oper_codes_4", {})

    def _dict_to_table(d):
        rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in sorted(d.items(), key=lambda x: -x[1]))
        return f"<table><tr><th>Key</th><th>Count</th></tr>{rows}</table>"

    def _sample_table(sample, label):
        if not sample:
            return f"<p>无数据</p>"
        # 只展示关键字段
        key_fields = ["glass_id", "oper_code", "eqp_id", "eqp_type", "defect_code",
                      "defect_size", "defect_no", "start_time", "end_time", "line_id",
                      "product_id", "glass_judge", "cst_id", "mark_type", "std_defect_desc_cn"]
        rows = ""
        for row in sample:
            cells = ""
            for f in key_fields:
                idx = f"item{all_fields.index(f) + 1}" if f in all_fields else None
                val = row.get(idx, "") if idx else ""
                cells += f"<td>{val}</td>"
            rows += f"<tr>{cells}</tr>"
        header = "".join(f"<th>{f}</th>" for f in key_fields)
        return f"<h4>{label}</h4><div style='overflow-x:auto;'><table><tr>{header}</tr>{rows}</table></div>"

    def _full_sample_json(sample, label):
        """以 JSON 格式展示完整的一条数据"""
        if not sample:
            return f"<h4>{label}</h4><p>无数据</p>"
        # 将 itemN 映射回字段名
        mapped = []
        for row in sample:
            r = {}
            for i, f in enumerate(all_fields):
                r[f] = row.get(f"item{i + 1}", "")
            mapped.append(r)
        return f"<h4>{label}（完整字段 JSON）</h4><pre style='max-height:500px;overflow:auto;background:#1e1e1e;color:#d4d4d4;padding:12px;border-radius:4px;font-size:11px;'>{json.dumps(mapped, ensure_ascii=False, indent=2)}</pre>"

    html = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>Defect 异常报告 — 任务 #""" + str(task_id) + r"""</title>
<style>
:root { --bg: #1a1a2e; --bg-card: #16213e; --bg-card-light: #1a2740; --bg-light: #0f3460;
  --text: #e0e0e0; --text-dark: #c0c0c0; --border-color: #2a3a5c;
  --accent: #4fd1c5; --danger: #f87171; --warning: #fbbf24; }
body.light { --bg: #f5f7fa; --bg-card: #fff; --bg-card-light: #fafbfc; --bg-light: #e8ecf1;
  --text: #333; --text-dark: #555; --border-color: #d0d5dd; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif; background: var(--bg); color: var(--text); padding: 16px; }
.header { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 8px; padding: 16px 20px; margin-bottom: 12px; }
.header h1 { font-size: 18px; margin-bottom: 8px; }
.header .meta { font-size: 12px; color: var(--text-dark); display: flex; flex-wrap: wrap; gap: 8px 20px; }
.header .meta span { white-space: nowrap; }
.section { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 8px; padding: 16px 20px; margin-bottom: 12px; }
.section h2 { font-size: 15px; margin-bottom: 10px; border-bottom: 1px solid var(--border-color); padding-bottom: 8px; }
.stats { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.stat-card { background: var(--bg-light); border-radius: 6px; padding: 12px 16px; min-width: 120px; }
.stat-card .num { font-size: 24px; font-weight: bold; color: var(--accent); }
.stat-card .lbl { font-size: 11px; color: var(--text-dark); margin-top: 2px; }
table { width: 100%; border-collapse: collapse; font-size: 11px; }
th { background: var(--bg-light); padding: 6px 8px; text-align: left; border: 1px solid var(--border-color); position: sticky; top: 0; }
td { padding: 4px 8px; border: 1px solid var(--border-color); }
pre { white-space: pre-wrap; word-break: break-all; }
.two-col { display: flex; gap: 12px; }
.two-col > div { flex: 1; min-width: 0; }
.btn-row { display: flex; gap: 8px; margin-top: 12px; }
.btn { padding: 6px 14px; border: 1px solid var(--border-color); border-radius: 4px; cursor: pointer; font-size: 12px; background: var(--bg-card); color: var(--text); }
.btn:hover { opacity: 0.8; }
</style>
</head>
<body>
<div class="header">
  <h1>🔍 Defect 异常报告 — 任务 #""" + str(task_id) + r"""（数据预览）</h1>
  <div class="meta">
    <span>厂别: """ + (site or "") + r"""</span>
    <span>机台: """ + (machine or "") + r"""</span>
    <span>时间段: """ + (start or "") + r""" ~ """ + (end or "") + r"""</span>
    <span>Defect Code: """ + (defect_code or "") + r"""</span>
    <span>Defect Size: """ + (defect_size or "") + r"""</span>
    <span>生成时间: """ + now_ts + r"""</span>
  </div>
</div>

<div class="section">
  <h2>📊 数据概览</h2>
  <div class="stats">
    <div class="stat-card"><div class="num">""" + str(total_3) + r"""</div><div class="lbl">PHL 数据条数</div></div>
    <div class="stat-card"><div class="num">""" + str(unique_glass_3) + r"""</div><div class="lbl">PHL 唯一 Glass</div></div>
    <div class="stat-card"><div class="num">""" + str(total_4) + r"""</div><div class="lbl">WET 数据条数</div></div>
    <div class="stat-card"><div class="num">""" + str(unique_glass_4) + r"""</div><div class="lbl">WET 唯一 Glass</div></div>
    <div class="stat-card"><div class="num">""" + str(unique_glass_total) + r"""</div><div class="lbl">合计唯一 Glass</div></div>
  </div>
</div>

<div class="two-col">
  <div class="section">
    <h2>PHL 站点 — Defect Code 分布</h2>
    """ + _dict_to_table(defect_codes_3) + r"""
  </div>
  <div class="section">
    <h2>WET 站点 — Defect Code 分布</h2>
    """ + _dict_to_table(defect_codes_4) + r"""
  </div>
</div>

<div class="two-col">
  <div class="section">
    <h2>PHL 站点 — Oper Code 分布</h2>
    """ + _dict_to_table(oper_codes_3) + r"""
  </div>
  <div class="section">
    <h2>WET 站点 — Oper Code 分布</h2>
    """ + _dict_to_table(oper_codes_4) + r"""
  </div>
</div>

<div class="section">
  <h2>PHL 数据样例（前5条，关键字段）</h2>
  """ + _sample_table(sample3, "PHL") + r"""
</div>

<div class="section">
  <h2>WET 数据样例（前5条，关键字段）</h2>
  """ + _sample_table(sample4, "WET") + r"""
</div>

<div class="section">
  <h2>PHL 完整字段 JSON（前3条）</h2>
  """ + _full_sample_json(sample3[:3], "PHL") + r"""
</div>

<div class="section">
  <h2>WET 完整字段 JSON（前3条）</h2>
  """ + _full_sample_json(sample4[:3], "WET") + r"""
</div>

<div class="section">
  <h2>字段列表（共 """ + str(len(all_fields)) + r""" 个字段）</h2>
  <pre>""" + json.dumps(all_fields, ensure_ascii=False) + r"""</pre>
</div>

</body>
</html>"""
    return html


# ===== Hold / 做账 操作 =====
def _load_hold_data(task_id):
    path = os.path.join(OUTPUT_DIR, f"defect_hold_{task_id}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_hold_data(task_id, data):
    path = os.path.join(OUTPUT_DIR, f"defect_hold_{task_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_posting_data(task_id):
    path = os.path.join(OUTPUT_DIR, f"defect_posting_{task_id}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_posting_data(task_id, data):
    path = os.path.join(OUTPUT_DIR, f"defect_posting_{task_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def do_hold(task_id, glass_ids, hold_op):
    """执行 Hold 操作"""
    token = _get_fab_token()
    results = {}
    for gid in glass_ids:
        body = {
            "eventUser": EVENT_USER,
            "factory": "ARRAY",
            "glassId": gid,
            "holdOperation": hold_op,
            "site": "T6T7",
            "userId": "DEMO-USER-ID",
        }
        try:
            r = _do_post("/api/ai-studio-service/mesOperation/hold", body, token,
                         {"apiKey": HOLD_API_KEY})
            results[gid] = {"operation": hold_op, "status": "ok", "data": r}
        except Exception as e:
            results[gid] = {"operation": hold_op, "status": "error", "error": str(e)}

    hold_data = _load_hold_data(task_id)
    for gid, r in results.items():
        if gid not in hold_data:
            hold_data[gid] = []
        hold_data[gid].append(r)
    _save_hold_data(task_id, hold_data)

    ok = sum(1 for v in results.values() if v["status"] == "ok")
    return {"ok": True, "total": len(glass_ids), "success": ok, "results": results}


def do_cancel_hold(task_id, glass_ids, cancel_records):
    """取消 Hold 操作"""
    token = _get_fab_token()
    results = {}
    for item in cancel_records:
        gid = item.get("glass_id", "")
        hold_op = item.get("operation", "")
        body = {
            "eventUser": EVENT_USER,
            "factory": "ARRAY",
            "glassId": gid,
            "holdOperation": hold_op,
            "cancelFlag": True,
            "site": "T6T7",
            "userId": "DEMO-USER-ID",
        }
        try:
            r = _do_post("/api/ai-studio-service/mesOperation/cancelHold", body, token,
                         {"apiKey": HOLD_API_KEY})
            results[gid] = {"operation": hold_op, "status": "ok", "data": r}
        except Exception as e:
            results[gid] = {"operation": hold_op, "status": "error", "error": str(e)}

    hold_data = _load_hold_data(task_id)
    for gid, r in results.items():
        if gid in hold_data:
            hold_data[gid] = [h for h in hold_data[gid] if not (
                h["operation"] == r["operation"] and h["status"] == "ok"
            )]
    _save_hold_data(task_id, hold_data)

    ok = sum(1 for v in results.values() if v["status"] == "ok")
    return {"ok": True, "total": len(cancel_records), "success": ok, "results": results}


def get_hold_summary(task_id, glass_ids):
    """获取选中 glass 的 hold 记录"""
    hold_data = _load_hold_data(task_id)
    records = []
    for gid in glass_ids:
        for h in hold_data.get(gid, []):
            records.append({"glass_id": gid, "operation": h["operation"], "status": h["status"]})
    return records


def do_posting(task_id, glass_ids, posting_ops, reason):
    """执行做账操作"""
    conn = http.client.HTTPConnection(FAB_API_HOST, FAB_API_PORT, timeout=120)
    body = {
        "messages": [{"role": "user", "content": json.dumps({
            "glass_ids": glass_ids,
            "posting_ops": posting_ops,
            "reason": reason,
        }, ensure_ascii=False)}],
        "stream": False,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {POSTING_API_KEY}",
    }
    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
    conn.request("POST", "/api/v2/chat/completions", body_bytes, headers)
    resp = conn.getresponse()
    result = json.loads(resp.read().decode())
    conn.close()

    posting_data = _load_posting_data(task_id)
    for gid in glass_ids:
        if gid not in posting_data:
            posting_data[gid] = []
        for op in posting_ops:
            if op not in posting_data[gid]:
                posting_data[gid].append(op)
    _save_posting_data(task_id, posting_data)

    return {"ok": True, "result": result}


def get_report(task_id):
    """获取已缓存的报告 HTML（优先内存，其次磁盘）"""
    if task_id in _report_cache:
        return _report_cache[task_id].get("html")
    if _load_cache(task_id):
        return _report_cache[task_id].get("html")
    return None


def get_report_state(task_id):
    """获取报告状态"""
    if task_id not in _report_cache:
        _load_cache(task_id)
    return _report_cache.get(task_id, {}).get("state")


def has_report(task_id):
    if task_id in _report_cache:
        return True
    return _load_cache(task_id)


# ===== 生成入口 =====
GENERATE_TIMEOUT = 180
GENERATE_MAX_RETRIES = 1


def _build_demo_raw_data(task):
    """构造 demo Defect 原始数据（PHL/WET 两组），复用 _run_step_6 组装 state。"""
    idx = _DEFECT_COL_INDEX
    defect_code = task.get("defect_code", "") or "DEMO-DEFECT-001 (颗粒)"
    defect_size = task.get("defect_size", "") or "0.5"
    machine = task.get("machine", "DEMO-M03")
    start = task.get("start_time", "2026-11-09 12:00")
    end = task.get("end_time", "2026-11-09 20:00")

    def _row(gid, oper_code, eqp_id, eqp_type):
        return {
            idx["factory"]: "DEMO",
            idx["glass_id"]: gid,
            idx["product_id"]: "DEMO-PROD-01",
            idx["start_time"]: start,
            idx["end_time"]: end,
            idx["line_id"]: machine,
            idx["eqp_id"]: eqp_id,
            idx["eqp_type"]: eqp_type,
            idx["mark_type"]: "DEMO",
            idx["oper_code"]: oper_code,
            idx["owner_code"]: "DEMO",
            idx["glass_judge"]: "NG",
            idx["panel_id"]: "DEMO-PNL",
            idx["panel_no"]: "DEMO",
            idx["defect_code"]: defect_code,
            idx["defect_no"]: "1",
            idx["defect_size"]: defect_size,
            idx["defect_size_flag"]: "DEMO",
            idx["image_data"]: "",
            idx["std_defect_desc_cn"]: "演示颗粒缺陷",
            idx["cst_id"]: "DEMO-CST",
        }

    # PHL 6 片 + WET 4 片
    data3 = [_row(f"DEMO-G{i:03d}", "3400", "DEMO-EQP-P01", "DEMO-PHL") for i in range(1, 7)]
    data4 = [_row(f"DEMO-G{i:03d}", "2401", "DEMO-EQP-W01", "DEMO-WET") for i in range(7, 11)]
    return data3, data4


def _do_generate(task, version):
    """实际执行生成流程"""
    task_id = task["id"]

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    print(f"\n{'='*50}")
    print(f"生成任务 #{task_id} 的 Defect 报告 (v{version})...")
    print(f"{'='*50}")

    # 步骤1：提取参数
    params = _derive_params(task)

    if os.environ.get("DEMO_MODE", "").strip() == "1":
        print("[DEMO] Defect 报告：跳过外部数据获取，使用演示数据")
        time.sleep(2.5)
        data3, data4 = _build_demo_raw_data(task)
        state = _run_step_6(data3, data4, params)
        html = _build_html(task, state)
        glass_ids = state.get("all_glass_ids", [])
        _report_cache[task_id] = {
            "html": html, "state": state, "glass_ids": glass_ids,
            "site": task.get("site", ""), "data3": data3, "data4": data4,
            "main_process": task.get("main_process", "[]"),
            "machine": task.get("machine", ""),
            "start_time": task.get("start_time", ""),
            "end_time": task.get("end_time", ""),
            "defect_code": task.get("defect_code", ""),
            "defect_size": task.get("defect_size", ""),
        }
        path = _report_file(task_id)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        _save_cache(task_id)
        print(f"[DEMO] 已写入磁盘: {path}")
        return html

    # 步骤2：获取 token
    token = _get_fab_token()
    print(f"token: {token}")

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    # 步骤3：PHL defect 查询
    cid3 = _run_step_3(token, params)

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    # 步骤4：WET defect 查询
    cid4 = _run_step_4(token, params)

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    # 步骤5：分页获取数据
    data3, data4 = _run_step_5(token, cid3, cid4)
    print(f"步骤5: PHL={len(data3)} 条, WET={len(data4)} 条")

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    # 步骤6：数据整合（待定）
    state = _run_step_6(data3, data4, params)
    print(f"步骤6: PHL={state['total_3']} 条({state['unique_glass_3']} glass), "
          f"WET={state['total_4']} 条({state['unique_glass_4']} glass), "
          f"合计 {state['unique_glass_total']} 唯一 glass")

    if not _is_current(task_id, version):
        raise GenerationCancelled("已被新请求取代")

    html = _build_html(task, state)
    print(f"HTML 已生成 ({len(html)} 字符)")

    if not _is_current(task_id, version):
        print(f"任务 #{task_id} v{version} 已被取代，跳过缓存写入")
        raise GenerationCancelled("已被新请求取代")

    glass_ids = state.get("all_glass_ids", [])
    _report_cache[task_id] = {
        "html": html,
        "state": state,
        "glass_ids": glass_ids,
        "site": task.get("site", ""),
        "data3": data3,
        "data4": data4,
        "main_process": task.get("main_process", "[]"),
        "machine": task.get("machine", ""),
        "start_time": task.get("start_time", ""),
        "end_time": task.get("end_time", ""),
        "defect_code": task.get("defect_code", ""),
        "defect_size": task.get("defect_size", ""),
    }

    path = _report_file(task_id)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    _save_cache(task_id)
    print(f"已写入磁盘: {path}")

    return html


def generate_report(task):
    """生成报告，带超时和重试"""
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
                print(f"任务 #{task_id} 生成超时，已达最大重试次数")
                raise RuntimeError(f"报告生成超时（>{GENERATE_TIMEOUT}s），已重试 {GENERATE_MAX_RETRIES} 次仍失败")

        if error[0] is not None:
            if isinstance(error[0], GenerationCancelled):
                raise error[0]
            raise error[0]

        return result[0]

    raise RuntimeError("报告生成失败")


def refresh_report(task_id):
    """刷新报告：重新执行步骤5-6"""
    entry = _report_cache.get(task_id)
    if not entry:
        return {"ok": False, "error": "报告未生成"}

    params = _derive_params({
        "site": entry.get("site", ""),
        "machine": entry.get("machine", ""),
        "start_time": entry.get("start_time", ""),
        "end_time": entry.get("end_time", ""),
        "defect_code": entry.get("defect_code", ""),
        "defect_size": entry.get("defect_size", ""),
        "main_process": entry.get("main_process", "[]"),
    })

    token = _get_fab_token()

    # 重新执行步骤3-4获取新 conditionId
    cid3 = _run_step_3(token, params)
    cid4 = _run_step_4(token, params)
    data3, data4 = _run_step_5(token, cid3, cid4)
    state = _run_step_6(data3, data4, params)

    machine = entry.get("machine", "")
    start_time = entry.get("start_time", "")
    end_time = entry.get("end_time", "")
    task_info = {"id": task_id, "site": entry.get("site", ""), "machine": machine,
                 "start_time": start_time, "end_time": end_time,
                 "defect_code": entry.get("defect_code", ""), "defect_size": entry.get("defect_size", "")}
    html = _build_html(task_info, state)

    glass_ids = state.get("all_glass_ids", [])
    _report_cache[task_id] = {
        "html": html, "state": state, "glass_ids": glass_ids,
        "site": entry.get("site", ""), "data3": data3, "data4": data4,
        "main_process": entry.get("main_process", "[]"),
        "machine": machine, "start_time": start_time, "end_time": end_time,
        "defect_code": entry.get("defect_code", ""), "defect_size": entry.get("defect_size", ""),
    }

    path = _report_file(task_id)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    _save_cache(task_id)

    print(f"刷新完成: PHL={state['total_3']} 条, WET={state['total_4']} 条")
    return {"ok": True}