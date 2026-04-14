from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

def _to_native(obj):
    if hasattr(obj, "item"):
        return obj.item()
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_native(v) for v in obj]
    return obj

def _image_hash(image_path: Path) -> str:
    """计算图片的简单哈希（用于判断是否修改）"""
    try:
        # 使用 PIL 如果可用，否则回退到文件内容哈希
        from PIL import Image
        import numpy as np
        img = Image.open(image_path).resize((128, 128)).convert("L")
        pixels = np.array(img).flatten()
        # 取前 256 个像素的平均值作为简单指纹
        avg = int(np.mean(pixels))
        return f"{avg}_{hashlib.md5(img.tobytes()).hexdigest()[:8]}"
    except ImportError:
        # 没有 PIL，回退到文件内容哈希
        return hashlib.md5(image_path.read_bytes()).hexdigest()[:16]
    except Exception:
        return "error"

def score_workspace(workspace: Path) -> dict[str, Any]:
    w = workspace.resolve()
    out_dir = w / "out"
    in_dir = w / "in"
    task_dir = w.parent.parent
    gt_path = task_dir / "ground_truth.json"
    gt = json.loads(gt_path.read_text(encoding="utf-8")) if gt_path.exists() else {}

    required = gt.get("required_images", ["cat_styled.png", "cat_scene.png"])
    min_size = gt.get("min_file_size_bytes", 5000)

    checks = []
    file_score = 0.0
    modified_score = 0.0
    description_score = 0.0

    # 1. 文件存在性 (40%)
    existing = []
    for fname in required:
        fpath = out_dir / fname
        exists = fpath.exists() and fpath.stat().st_size >= min_size
        existing.append(exists)
        checks.append({
            "id": f"file_{fname}",
            "label": f"{fname} 存在且大小≥{min_size}B",
            "pass": exists,
            "weight": 0.40 / len(required),
            "detail": {"size": fpath.stat().st_size if fpath.exists() else 0}
        })
    file_score = sum(existing) / len(required)

    # 2. 图片是否明显不同于原图 (40%)
    original = in_dir / "cat.jpg"
    if original.exists() and file_score > 0:
        # 对每张输出图片，检查与原图的哈希差异
        original_hash = _image_hash(original)
        modified_count = 0
        for idx, fname in enumerate(required):
            fpath = out_dir / fname
            if fpath.exists():
                new_hash = _image_hash(fpath)
                # 如果哈希不同，认为有修改（简单启发）
                if new_hash != original_hash and new_hash != "error":
                    modified_count += 1
                else:
                    # 即使哈希相同，如果文件大小差异很大也可能修改，但简化
                    pass
        modified_score = modified_count / len(required)
        checks.append({
            "id": "image_modification",
            "label": f"图片与原图差异检测: {modified_count}/{len(required)} 不同",
            "pass": modified_score >= 0.5,
            "weight": 0.40,
            "detail": {"different": modified_count, "total": len(required)}
        })
    else:
        checks.append({
            "id": "image_modification",
            "label": "无法检测修改（原图缺失或输出文件缺失）",
            "pass": False,
            "weight": 0.40,
            "detail": None
        })

    # 3. 描述文件质量 (20%)
    desc_file = out_dir / "description.txt"
    if desc_file.exists():
        content = desc_file.read_text(encoding="utf-8").strip()
        # 检查是否包含关键词
        has_style = any(k in content.lower() for k in ["style", "风格", "卡通", "油画", "水彩", "像素", "cartoon", "oil", "watercolor", "pixel"])
        has_scene = any(k in content.lower() for k in ["scene", "场景", "背景", "月球", "海滩", "太空", "moon", "beach", "space"])
        if has_style and has_scene:
            description_score = 1.0
        elif has_style or has_scene:
            description_score = 0.5
        else:
            description_score = 0.2
        checks.append({
            "id": "description",
            "label": "description.txt 包含风格和场景说明",
            "pass": description_score >= 0.5,
            "weight": 0.20,
            "detail": {"has_style": has_style, "has_scene": has_scene}
        })
    else:
        checks.append({
            "id": "description",
            "label": "description.txt 缺失",
            "pass": False,
            "weight": 0.20,
            "detail": None
        })

    total_score = file_score * 0.40 + modified_score * 0.40 + description_score * 0.20
    thresholds = gt.get("scoring", {}).get("thresholds", {"excellent": 0.90, "good": 0.75, "pass": 0.60})
    if total_score >= thresholds["excellent"]:
        level = "excellent"
    elif total_score >= thresholds["good"]:
        level = "good"
    elif total_score >= thresholds["pass"]:
        level = "pass"
    else:
        level = "fail"

    result = {
        "task": "14-image-edit",
        "workspace": str(w),
        "outcome_score": round(float(total_score), 4),
        "level": level,
        "checks": _to_native(checks),
        "summary": {
            "files_exist": round(float(file_score), 4),
            "images_modified": round(float(modified_score), 4),
            "description_quality": round(float(description_score), 4)
        },
        "needs_human_review": total_score >= 0.6 and total_score < 0.9,  # 自动通过但可能需要人工复核
        "auto_grade_only": True
    }
    return json.loads(json.dumps(result, default=str))

def score_workspace_safe(workspace: Path) -> dict[str, Any]:
    try:
        return score_workspace(workspace)
    except Exception as e:
        return {
            "task": "14-image-edit",
            "workspace": str(workspace),
            "outcome_score": 0.0,
            "level": "error",
            "error": str(e),
            "checks": [],
            "summary": {}
        }