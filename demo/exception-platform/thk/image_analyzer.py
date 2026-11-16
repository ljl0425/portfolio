"""OpenCV 图像分析引擎。

提供 Mura 检测、缺陷/异常检测、CSV 辅助分析等功能。
所有方法返回结构化结果，便于前端展示和报告生成。
"""

import base64
import json
import os
from io import BytesIO
from typing import Any

import cv2
import numpy as np


# ===== 工具函数 =====

def _image_to_base64(image: np.ndarray, fmt: str = ".jpg") -> str:
    """将 OpenCV 图像转为 base64 字符串（用于前端展示）。"""
    _, buffer = cv2.imencode(fmt, image)
    return base64.b64encode(buffer).decode("utf-8")


def _draw_rectangles(image: np.ndarray,
                      regions: list[dict],
                      color=(0, 0, 255),
                      thickness=2) -> np.ndarray:
    """在图像上绘制矩形区域标注。"""
    annotated = image.copy()
    for region in regions:
        x, y, w, h = region.get("bbox", (0, 0, 0, 0))
        label = region.get("label", "")
        cv2.rectangle(annotated, (x, y), (x + w, y + h), color, thickness)
        if label:
            cv2.putText(annotated, label, (x, y - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    return annotated


# ===== 核心分析方法 =====

def _filter_regions(regions: list[dict], params: dict) -> list[dict]:
    """过滤掉面积过小或 severity 过低的区域。"""
    min_severity = params.get("min_severity", 0.35)
    min_area_ratio = params.get("min_area_ratio", 0.015)  # 1.5% of total image
    image_area = params.get("_image_area", 1)
    return [
        r for r in regions
        if r.get("severity", 0) >= min_severity
        and r.get("area", 0) >= image_area * min_area_ratio
    ]


def detect_mura(image_path: str, **params) -> dict:
    """Mura 检测：亮度不均匀、色斑、大面积低对比度异常。

    Args:
        image_path: 图像文件路径
        params: 可选参数
            - block_size: 分块大小 (默认 64)
            - brightness_threshold: 亮度差异阈值 (默认 30)
            - gradient_threshold: 渐变检测阈值 (默认 50)

    Returns:
        {
            "has_mura": bool,
            "regions": [{bbox, area, severity, label}, ...],
            "metrics": {overall_uniformity, max_deviation, ...},
            "annotated_base64": str,
        }
    """
    img = cv2.imread(image_path)
    if img is None:
        return {"has_mura": False, "error": f"无法读取图像: {image_path}"}

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # 高斯模糊平滑随机噪声，避免正常纹理被误判为 Mura
    gray = cv2.GaussianBlur(gray, (5, 5), 1.0)
    h, w = gray.shape
    regions = []

    block_size = params.get("block_size", 48)
    brightness_threshold = params.get("brightness_threshold", 40)
    gradient_threshold = params.get("gradient_threshold", 50)

    # 构建有效区域掩码：排除黑暗背景（如晶圆边缘外的区域）
    # 取 Otsu 阈值分离前景（晶圆）与背景
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # 如果前景和背景亮度差异不够大，默认全图为有效
    fg_pixels = cv2.countNonZero(mask)
    if fg_pixels < h * w * 0.3 or fg_pixels > h * w * 0.95:
        mask = np.ones_like(gray) * 255

    # ---- 1. 分块亮度均匀性分析（仅分析有效区域） ----
    block_means = []
    for y in range(0, h, block_size):
        for x in range(0, w, block_size):
            block = gray[y:y + block_size, x:x + block_size]
            block_mask = mask[y:y + block_size, x:x + block_size]
            if block.size == 0:
                continue
            # 只统计掩码内像素
            valid = block[block_mask > 0]
            if valid.size < block.size * 0.3:
                continue  # 大部分是背景，跳过该块
            mean_val = float(np.mean(valid))
            block_means.append({
                "x": x, "y": y,
                "w": min(block_size, w - x),
                "h": min(block_size, h - y),
                "mean": mean_val,
            })

    if block_means:
        all_means = [b["mean"] for b in block_means]
        global_mean = np.mean(all_means)
        global_std = np.std(all_means)

        for b in block_means:
            deviation = abs(b["mean"] - global_mean)
            if deviation > brightness_threshold and global_std > 0:
                severity = min(deviation / (brightness_threshold * 2), 1.0)
                regions.append({
                    "bbox": (b["x"], b["y"], b["w"], b["h"]),
                    "area": b["w"] * b["h"],
                    "severity": round(severity, 2),
                    "type": "mura_brightness",
                    "label": f"Mura({severity:.1f})",
                })

    # ---- 2. 渐变/边缘检测（大面积 Mura） ----
    sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    gradient_magnitude = np.sqrt(sobel_x ** 2 + sobel_y ** 2)
    gradient_magnitude = cv2.normalize(
        gradient_magnitude, None, 0, 255, cv2.NORM_MINMAX
    ).astype(np.uint8)

    _, grad_thresh = cv2.threshold(
        gradient_magnitude, gradient_threshold, 255, cv2.THRESH_BINARY
    )

    contours, _ = cv2.findContours(
        grad_thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area > (h * w * 0.01):  # 面积超过总面积的 1%
            x, y, cw, ch = cv2.boundingRect(cnt)
            regions.append({
                "bbox": (x, y, cw, ch),
                "area": int(area),
                "severity": round(min(area / (h * w) * 10, 1.0), 2),
                "type": "mura_gradient",
                "label": f"Gradient({area:.0f})",
            })

    # ---- 汇总 ----
    params["_image_area"] = h * w
    regions = _filter_regions(regions, params)
    has_mura = len(regions) > 0
    metrics = {
        "image_size": f"{w}x{h}",
        "block_count": len(block_means),
        "overall_uniformity": round(
            float(1.0 - min(global_std / 128, 0.99)), 2
        ) if block_means and global_std > 0 else 1.0,
        "max_deviation": round(
            float(max((abs(b["mean"] - global_mean) if block_means else 0)
                      for b in block_means)), 1
        ) if block_means else 0.0,
    }

    annotated = _draw_rectangles(img, regions) if has_mura else img

    return {
        "has_mura": has_mura,
        "regions": regions,
        "metrics": metrics,
        "annotated_base64": _image_to_base64(annotated),
    }


def detect_defects(image_path: str, **params) -> dict:
    """缺陷/异常检测：阈值分割、斑点分析、轮廓检测。

    Args:
        image_path: 图像文件路径
        params: 可选参数
            - threshold_method: 二值化方法 (otsu/adaptive/triangle, 默认 otsu)
            - min_defect_area: 最小缺陷面积 (默认 50)
            - blob_params: SimpleBlobDetector 参数覆盖

    Returns:
        {
            "defect_count": int,
            "defects": [{bbox, area, type, confidence}, ...],
            "annotated_base64": str,
        }
    """
    img = cv2.imread(image_path)
    if img is None:
        return {"defect_count": 0, "defects": [], "error": f"无法读取图像: {image_path}"}

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    defects = []

    min_area = params.get("min_defect_area", 50)

    # ---- 1. 二值化 + 轮廓检测 ----
    method = params.get("threshold_method", "otsu")
    if method == "adaptive":
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 21, 5
        )
    elif method == "triangle":
        _, binary = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_TRIANGLE
        )
    else:  # otsu
        _, binary = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )

    # 形态学操作去噪
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(
        binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area:
            continue
        # 过滤掉太大的（可能是整幅图的边缘）
        if area > h * w * 0.3:
            continue

        x, y, cw, ch = cv2.boundingRect(cnt)
        # 计算圆度作为置信度指标
        perimeter = cv2.arcLength(cnt, True)
        circularity = (
            4 * np.pi * area / (perimeter ** 2) if perimeter > 0 else 0
        )

        # 判断缺陷类型
        aspect_ratio = cw / max(ch, 1)
        if aspect_ratio > 4 or aspect_ratio < 0.25:
            defect_type = "scratch"  # 划痕
        elif circularity > 0.7:
            defect_type = "spot"  # 斑点
        else:
            defect_type = "irregular"  # 不规则

        confidence = min(circularity, 0.9) if defect_type == "spot" else 0.6

        defects.append({
            "bbox": (x, y, cw, ch),
            "area": int(area),
            "type": defect_type,
            "confidence": round(confidence, 2),
            "label": f"{defect_type}({area:.0f})",
        })

    # ---- 2. 斑点检测（SimpleBlobDetector） ----
    blob_params = cv2.SimpleBlobDetector_Params()
    blob_params.minArea = max(min_area, 10)
    blob_params.maxArea = int(h * w * 0.1)
    blob_params.filterByArea = True
    blob_params.filterByCircularity = False
    blob_params.filterByConvexity = False
    blob_params.filterByInertia = False

    # 用户自定义参数覆盖
    for key, val in params.get("blob_params", {}).items():
        if hasattr(blob_params, key):
            setattr(blob_params, key, val)

    detector = cv2.SimpleBlobDetector_create(blob_params)
    keypoints = detector.detect(gray)

    existing_bboxes = {(d["bbox"][0], d["bbox"][1]) for d in defects}
    for kp in keypoints:
        x = int(kp.pt[0] - kp.size / 2)
        y = int(kp.pt[1] - kp.size / 2)
        s = int(kp.size)
        # 去重
        if (x, y) in existing_bboxes:
            continue
        existing_bboxes.add((x, y))
        defects.append({
            "bbox": (max(x, 0), max(y, 0), s, s),
            "area": int(np.pi * (kp.size / 2) ** 2),
            "type": "blob",
            "confidence": round(kp.response * 0.5, 2),
            "label": f"Blob({kp.response:.1f})",
        })

    annotated = _draw_rectangles(img, defects) if defects else img

    return {
        "defect_count": len(defects),
        "defects": defects,
        "annotated_base64": _image_to_base64(annotated),
    }


def full_analysis(image_path: str, csv_context: dict = None,
                   **params) -> dict:
    """综合图像分析：同时运行 mura 检测和缺陷检测。

    Args:
        image_path: 图像文件路径
        csv_context: CSV 辅助数据（来自 analyzer 分析结果的上下文）
        params: 传递给子方法的参数

    Returns:
        {
            "mura_result": ...,
            "defect_result": ...,
            "has_anomaly": bool,    # 综合判断是否有异常
            "anomaly_summary": str,
        }
    """
    mura_result = detect_mura(image_path, **params)
    defect_result = detect_defects(image_path, **params)

    has_anomaly = mura_result.get("has_mura", False) or \
                  defect_result.get("defect_count", 0) > 0

    summary_parts = []
    if mura_result.get("has_mura"):
        summary_parts.append(
            f"检测到 {len(mura_result['regions'])} 个 Mura 区域"
        )
    if defect_result.get("defect_count", 0) > 0:
        summary_parts.append(
            f"检测到 {defect_result['defect_count']} 个缺陷"
        )

    # 如果有 CSV 上下文，尝试关联
    csv_note = ""
    if csv_context:
        anomaly_cols = csv_context.get("anomaly_columns", [])
        if anomaly_cols:
            csv_note = f"（CSV 异常列: {', '.join(anomaly_cols[:5])}）"

    return {
        "image_path": image_path,
        "mura_result": mura_result,
        "defect_result": defect_result,
        "has_anomaly": has_anomaly,
        "anomaly_summary": (
            "; ".join(summary_parts) if summary_parts
            else "未检测到明显异常"
        ) + csv_note,
    }


def save_annotated_image(image_base64: str, output_path: str) -> str:
    """将 base64 标注图保存为文件。"""
    img_data = base64.b64decode(image_base64)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(img_data)
    return output_path