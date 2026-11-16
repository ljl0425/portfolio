"""CSV 数据分析引擎。

根据 CSV 数据结构判断是否需要触发图像识别。
业务规则待用户提供后填充，当前使用可扩展的占位逻辑。
"""

import json
from typing import Any


def analyze_csv(csv_data: dict, **params) -> dict:
    """分析 CSV 数据，决定是否需要图像识别。

    Args:
        csv_data: csv_reader.read_csv() 的返回结果
        params: 可选的业务参数（阈值、规则等）

    Returns:
        {
            "need_image": bool,        # 是否需要图像识别
            "reason": str,             # 决策原因
            "confidence": float,       # 置信度 0-1
            "matched_rules": [...],    # 匹配到的规则
            "anomaly_columns": [...],  # 发现异常的列
            "image_paths": [...],      # 需要分析的图片路径
            "summary": str,            # 分析摘要
        }
    """
    headers = csv_data.get("headers", [])
    total_rows = csv_data.get("total_rows", 0)
    column_stats = csv_data.get("column_stats", {})

    if not headers or total_rows == 0:
        return {
            "need_image": False,
            "reason": "CSV 文件为空或无效",
            "confidence": 0.0,
            "matched_rules": [],
            "anomaly_columns": [],
            "image_paths": [],
            "summary": "文件无有效数据，无需图像分析",
        }

    matched_rules = []
    anomaly_columns = []
    checks = []

    # ---- 规则1：空值率过高（默认阈值 > 30%） ----
    null_threshold = params.get("null_ratio_threshold", 0.30)
    for col_name, stats in column_stats.items():
        null_ratio = stats["null_count"] / max(stats["total"], 1)
        if null_ratio > null_threshold:
            matched_rules.append(f"column_null_ratio:{col_name}")
            anomaly_columns.append(col_name)
            checks.append(
                f"列 '{col_name}' 空值率 {null_ratio:.1%}，超过阈值 {null_threshold:.0%}"
            )

    # ---- 规则2：数值范围异常（默认：有数值列的最大值超过均值的 3 倍标准差） ----
    outlier_factor = params.get("outlier_factor", 3.0)
    for col_name, stats in column_stats.items():
        if not stats.get("is_numeric"):
            continue
        avg = stats.get("numeric_avg", 0)
        mx = stats.get("numeric_max", 0)
        mn = stats.get("numeric_min", 0)
        if avg != 0 and abs(mx / avg) > outlier_factor:
            matched_rules.append(f"numeric_outlier_max:{col_name}")
            if col_name not in anomaly_columns:
                anomaly_columns.append(col_name)
            checks.append(
                f"列 '{col_name}' 最大值 {mx} 偏离均值 {avg} 超过 {outlier_factor} 倍"
            )
        if avg != 0 and avg > 0 and mn < -abs(avg) * outlier_factor:
            matched_rules.append(f"numeric_outlier_min:{col_name}")
            if col_name not in anomaly_columns:
                anomaly_columns.append(col_name)
            checks.append(
                f"列 '{col_name}' 最小值 {mn} 严重偏离均值 {avg}"
            )

    # ---- 规则3：数据量异常（行数超过阈值） ----
    row_threshold = params.get("row_count_threshold", 0)
    if row_threshold > 0 and total_rows > row_threshold:
        matched_rules.append("row_count_exceeded")
        checks.append(f"数据行数 {total_rows} 超过阈值 {row_threshold}")

    # ---- 规则4：用户自定义阈值列（按指定列的值判断） ----
    custom_thresholds = params.get("thresholds", {})
    # 格式：{"column_name": {"min": X, "max": Y}}
    if custom_thresholds:
        rows = csv_data.get("rows", [])
        for col_name, limits in custom_thresholds.items():
            if col_name not in headers:
                continue
            col_idx = headers.index(col_name)
            min_val = limits.get("min")
            max_val = limits.get("max")
            violations = 0
            for row in rows:
                if col_idx < len(row):
                    try:
                        val = float(row[col_idx])
                        if min_val is not None and val < min_val:
                            violations += 1
                        if max_val is not None and val > max_val:
                            violations += 1
                    except (ValueError, TypeError):
                        pass
            if violations > 0:
                matched_rules.append(f"custom_threshold:{col_name}")
                if col_name not in anomaly_columns:
                    anomaly_columns.append(col_name)
                checks.append(
                    f"列 '{col_name}' 有 {violations} 行超出阈值范围 "
                    f"[{min_val or '-∞'}, {max_val or '+∞'}]"
                )

    # ---- 决策 ----
    need_image = len(matched_rules) > 0
    confidence = min(0.5 + len(matched_rules) * 0.15, 0.95) if need_image else 0.0

    # 提取图片路径
    image_column = params.get("image_column")
    from . import csv_reader
    image_paths = csv_reader.extract_image_paths(csv_data, image_column)

    return {
        "need_image": need_image,
        "reason": "; ".join(checks) if checks else "数据正常，无需图像分析",
        "confidence": round(confidence, 2),
        "matched_rules": matched_rules,
        "anomaly_columns": anomaly_columns,
        "image_paths": image_paths,
        "summary": (
            f"分析了 {total_rows} 行 {len(headers)} 列数据，"
            f"发现 {len(anomaly_columns)} 个异常列，"
            f"{'需要' if need_image else '不需要'}图像识别"
        ),
    }