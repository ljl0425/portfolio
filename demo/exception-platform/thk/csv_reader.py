"""CSV 文件读取与解析模块。

支持从 UNC 路径（网络共享）读取 CSV 文件。
提供文件扫描、读取、基本信息提取等功能。
"""

import csv
import os
import glob
import time
from typing import Any


def scan_csv_files(unc_path: str, file_pattern: str = "*.csv") -> list[dict]:
    """扫描 UNC 目录下的 CSV 文件，返回文件列表。

    返回格式：[{name, path, size, modified_at}, ...]

    注：UNC 路径访问可能较慢，已捕获常见异常。
    """
    if not os.path.isdir(unc_path):
        raise FileNotFoundError(f"目录不存在或无法访问: {unc_path}")

    search_pattern = os.path.join(unc_path, file_pattern)
    files = []
    try:
        for file_path in glob.glob(search_pattern):
            try:
                stat = os.stat(file_path)
                files.append({
                    "name": os.path.basename(file_path),
                    "path": file_path,
                    "size": stat.st_size,
                    "modified_at": time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.localtime(stat.st_mtime),
                    ),
                })
            except OSError:
                continue
    except Exception as e:
        print(f"[CSV扫描] 扫描异常: {e}")
    return files


def read_csv(file_path: str, encoding: str = "utf-8-sig") -> dict:
    """读取 CSV 文件并返回结构化数据。

    自动尝试多种编码：utf-8-sig → gbk → latin-1。
    返回格式：
    {
        "headers": [...],       # 列名列表
        "rows": [[...], ...],   # 数据行列表（最多 500 行预览）
        "total_rows": int,      # 总行数
        "column_stats": {...},  # 每列的统计信息
    }
    """
    encodings_to_try = [encoding, "gbk", "latin-1"]
    content = None
    used_encoding = None

    for enc in encodings_to_try:
        try:
            with open(file_path, "r", encoding=enc, newline="") as f:
                content = f.read()
            used_encoding = enc
            break
        except (UnicodeDecodeError, LookupError):
            continue

    if content is None:
        raise ValueError(f"无法以任何已知编码读取文件: {file_path}")

    lines = content.splitlines()
    reader = csv.reader(lines)
    try:
        headers = next(reader)
    except StopIteration:
        return {
            "headers": [], "rows": [], "total_rows": 0,
            "column_stats": {}, "encoding": used_encoding,
        }

    # 清理 BOM
    if headers and headers[0].startswith("﻿"):
        headers[0] = headers[0][1:]

    all_rows = list(reader)
    total_rows = len(all_rows)
    preview_rows = all_rows[:500]  # 最多预览 500 行

    # 计算每列的简单统计
    column_stats = _compute_column_stats(headers, all_rows)

    return {
        "headers": headers,
        "rows": preview_rows,
        "total_rows": total_rows,
        "column_stats": column_stats,
        "encoding": used_encoding,
    }


def _compute_column_stats(headers: list[str],
                           all_rows: list[list[str]]) -> dict[str, dict]:
    """计算每列的基本统计信息。"""
    stats = {}
    for i, header in enumerate(headers):
        col_values = []
        null_count = 0
        numeric_values = []

        for row in all_rows:
            if i < len(row):
                val = row[i].strip()
                col_values.append(val)
                if not val:
                    null_count += 1
                else:
                    try:
                        numeric_values.append(float(val))
                    except ValueError:
                        pass
            else:
                null_count += 1

        col_stats = {
            "index": i,
            "total": len(all_rows),
            "null_count": null_count,
            "non_null_count": len(all_rows) - null_count,
            "unique_count": len(set(col_values)),
            "is_numeric": len(numeric_values) > len(all_rows) * 0.5,
        }

        if numeric_values:
            col_stats["numeric_min"] = round(min(numeric_values), 4)
            col_stats["numeric_max"] = round(max(numeric_values), 4)
            col_stats["numeric_avg"] = round(
                sum(numeric_values) / len(numeric_values), 4
            )

        stats[header] = col_stats

    return stats


def extract_image_paths(csv_data: dict,
                         image_column: str = None) -> list[str]:
    """从 CSV 数据中提取图片路径。

    如果指定了 image_column，从该列提取；
    否则自动检测可能包含图片路径的列（含 .png/.jpg/.bmp 等扩展名）。
    返回去重后的图片路径列表。
    """
    headers = csv_data.get("headers", [])
    rows = csv_data.get("rows", [])

    if not headers or not rows:
        return []

    target_indices = []
    if image_column and image_column in headers:
        target_indices = [headers.index(image_column)]
    else:
        # 自动检测：查找值中包含图片扩展名的列
        img_extensions = (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".gif")
        for i, header in enumerate(headers):
            for row in rows[:50]:  # 采样前 50 行检测
                if i < len(row):
                    val = row[i].strip().lower()
                    if any(val.endswith(ext) for ext in img_extensions):
                        target_indices.append(i)
                        break

    paths = set()
    for i in target_indices:
        for row in rows:
            if i < len(row):
                val = row[i].strip()
                if val:
                    paths.add(val)

    return sorted(paths)