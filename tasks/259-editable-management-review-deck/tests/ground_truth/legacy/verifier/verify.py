#!/usr/bin/env python3
"""Deterministically verify the editable presentation and protected workspace."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


CASE_ROOT = Path(__file__).resolve().parents[1]
SLIDE_W = 12_192_000
SLIDE_H = 6_858_000
FONT = "Noto Sans SC"
NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalized_pptx_sha256(path: Path) -> str:
    items: list[dict[str, str]] = []
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            items.append({"path": name, "sha256": hashlib.sha256(archive.read(name)).hexdigest()})
    return canonical_sha(items)


def load_metrics(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return {(row["metric"], row["scope"]): row for row in csv.DictReader(stream)}


def texts(root: ET.Element) -> list[str]:
    return [node.text or "" for node in root.findall(".//a:t", NS)]


def shape_description(shape: ET.Element) -> str:
    node = shape.find("./p:nvSpPr/p:cNvPr", NS)
    return node.get("descr", "") if node is not None else ""


def shape_geometry(shape: ET.Element) -> tuple[int, int, int, int] | None:
    off = shape.find("./p:spPr/a:xfrm/a:off", NS)
    ext = shape.find("./p:spPr/a:xfrm/a:ext", NS)
    if off is None or ext is None:
        return None
    return tuple(int(value) for value in (off.get("x", "-1"), off.get("y", "-1"), ext.get("cx", "-1"), ext.get("cy", "-1")))  # type: ignore[return-value]


def first_font_size(shape: ET.Element) -> float | None:
    node = shape.find(".//a:rPr", NS)
    if node is None or node.get("sz") is None:
        return None
    return int(node.get("sz", "0")) / 100.0


def estimated_overflow(shape: ET.Element) -> bool:
    geometry = shape_geometry(shape)
    size = first_font_size(shape)
    paragraphs = ["".join(node.text or "" for node in p.findall(".//a:t", NS)) for p in shape.findall(".//a:p", NS)]
    if not geometry or not size or not paragraphs:
        return False
    _, _, width_emu, height_emu = geometry
    width_pt = width_emu / 12_700
    height_pt = height_emu / 12_700
    max_chars = max(1, math.floor((width_pt - 12) / (size * 0.82)))
    line_capacity = max(1, math.floor((height_pt - 6) / (size * 1.25)))
    needed_lines = sum(max(1, math.ceil(len(text) / max_chars)) for text in paragraphs)
    return needed_lines > line_capacity


def check(condition: bool, check_id: str, detail: str, checks: list[dict[str, object]], failures: list[str]) -> None:
    checks.append({"check_id": check_id, "passed": condition, "detail": detail})
    if not condition:
        failures.append(check_id)


def verify(workspace: Path) -> dict[str, object]:
    checks: list[dict[str, object]] = []
    failures: list[str] = []
    manifest = json.loads((CASE_ROOT / "fixtures" / "source_manifest.json").read_text(encoding="utf-8"))
    expected_files = {item["path"]: item["sha256"] for item in manifest["agent_visible_files"]}
    integrity_ok = True
    for relative, expected in expected_files.items():
        path = workspace / relative
        if not path.is_file() or path.is_symlink() or sha256(path) != expected:
            integrity_ok = False
    check(integrity_ok, "input_integrity", "TASK.md and all input assets must retain their frozen SHA-256", checks, failures)

    allowed = set(expected_files) | {"output/.keep", "output/management_review.pptx"}
    allowed_directories = {"input", "output"}
    observed_paths = list(workspace.rglob("*"))
    symlinks = sorted(path.relative_to(workspace).as_posix() for path in observed_paths if path.is_symlink())
    special_files = sorted(
        path.relative_to(workspace).as_posix()
        for path in observed_paths
        if not path.is_symlink() and not path.is_file() and not path.is_dir()
    )
    observed_directories = {
        path.relative_to(workspace).as_posix()
        for path in observed_paths
        if path.is_dir() and not path.is_symlink()
    }
    unexpected_directories = sorted(observed_directories - allowed_directories)
    observed = {
        path.relative_to(workspace).as_posix()
        for path in observed_paths
        if path.is_file() and not path.is_symlink()
    }
    unexpected = sorted(observed - allowed)
    check(
        not unexpected and not unexpected_directories and not symlinks and not special_files,
        "output_scope",
        f"unexpected files={unexpected}; unexpected directories={unexpected_directories}; symlinks={symlinks}; special={special_files}",
        checks,
        failures,
    )
    output = workspace / "output" / "management_review.pptx"
    check(output.is_file() and not output.is_symlink(), "required_output", "output/management_review.pptx must exist", checks, failures)
    if not output.is_file():
        return finalize(checks, failures, {"unexpected_files": unexpected})

    try:
        archive = zipfile.ZipFile(output)
        bad_member = archive.testzip()
        names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        check(False, "pptx_package", "file is not a readable OOXML ZIP package", checks, failures)
        return finalize(checks, failures, {"unexpected_files": unexpected})
    safe_names = all(not name.startswith("/") and ".." not in Path(name).parts for name in names)
    check(bad_member is None and safe_names, "pptx_package", "ZIP members must be readable and path-safe", checks, failures)
    required_parts = {"[Content_Types].xml", "ppt/presentation.xml", "ppt/theme/theme1.xml"}
    check(required_parts.issubset(names), "required_ooxml_parts", "presentation, content-types and theme parts are required", checks, failures)
    media = sorted(name for name in names if name.startswith("ppt/media/"))
    check(not media, "no_raster_shortcut", f"media parts={media}", checks, failures)
    external_relationships: list[str] = []
    relationship_parse_ok = True
    for name in sorted(value for value in names if value.endswith(".rels")):
        try:
            rel_root = ET.fromstring(archive.read(name))
        except ET.ParseError:
            relationship_parse_ok = False
            continue
        for node in rel_root.findall("rel:Relationship", NS):
            if node.get("TargetMode") == "External":
                external_relationships.append(f"{name}:{node.get('Id', '')}")
    check(relationship_parse_ok and not external_relationships, "no_external_relationships", f"external relationships={external_relationships}", checks, failures)

    try:
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        size = presentation.find("p:sldSz", NS)
        canvas_ok = size is not None and int(size.get("cx", "0")) == SLIDE_W and int(size.get("cy", "0")) == SLIDE_H
    except (KeyError, ET.ParseError, ValueError):
        canvas_ok = False
    check(canvas_ok, "canvas_16_9", "canvas must be 12192000 x 6858000 EMU", checks, failures)

    slide_names = sorted(
        (name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
        key=lambda value: int(re.search(r"\d+", value).group()),
    )
    check(len(slide_names) == 6, "slide_count", f"observed slide count={len(slide_names)}", checks, failures)
    roots: list[ET.Element] = []
    parse_ok = True
    for name in slide_names:
        try:
            roots.append(ET.fromstring(archive.read(name)))
        except ET.ParseError:
            parse_ok = False
    check(parse_ok and len(roots) == len(slide_names), "slide_xml_parse", "every slide must be valid XML", checks, failures)
    if len(roots) != 6:
        archive.close()
        return finalize(checks, failures, {"unexpected_files": unexpected, "media_parts": media})

    metrics = load_metrics(workspace / "input" / "metrics.csv")
    total = metrics[("revenue", "total")]
    completion = float(total["actual"]) / float(total["plan"]) * 100
    gap = int(float(total["plan"]) - float(total["actual"]))
    delivery = metrics[("on_time_delivery", "total")]
    returns = metrics[("return_rate", "total")]
    gross = metrics[("gross_margin", "total")]
    west_delivery = metrics[("on_time_delivery", "西部")]
    north_return = metrics[("return_rate", "华北")]
    promotion = metrics[("promotion_margin", "total")]
    expected_titles = [
        f"第二季度收入完成 {completion:.1f}%，八周内优先修复交付与退货",
        f"收入完成 {completion:.1f}%，区域缺口集中在西部和华北",
        "交付延迟、退货上升和低毛利促销共同拖累结果",
        "先修复最大缺口，再把有效做法复制到其他区域",
        "八周只跟踪四项指标，每两周复盘一次",
        "今天确认责任人与复盘节奏，八周行动即可开始",
    ]
    all_slide_texts: list[list[str]] = []
    shape_counts: list[int] = []
    overflow_shapes: list[str] = []
    font_failures: list[str] = []
    geometry_failures: list[str] = []
    auto_fit_found = False
    picture_or_blip: list[str] = []
    for slide_index, root in enumerate(roots, 1):
        slide_text = texts(root)
        all_slide_texts.append(slide_text)
        shapes = root.findall(".//p:sp", NS)
        shape_counts.append(len(shapes))
        title_shapes = [shape for shape in shapes if shape_description(shape) == "role:title"]
        title_text = "".join(texts(title_shapes[0])) if len(title_shapes) == 1 else ""
        check(title_text == expected_titles[slide_index - 1], f"slide_{slide_index}_title", f"title={title_text}", checks, failures)
        if root.find(".//p:pic", NS) is not None or root.find(".//a:blip", NS) is not None:
            picture_or_blip.append(f"slide{slide_index}")
        for shape in shapes:
            descr = shape_description(shape) or f"slide{slide_index}:unnamed"
            geometry = shape_geometry(shape)
            if geometry:
                x, y, width, height = geometry
                if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > SLIDE_W or y + height > SLIDE_H:
                    geometry_failures.append(descr)
                geom_node = shape.find("./p:spPr/a:prstGeom", NS)
                if geom_node is not None and geom_node.get("prst") == "ellipse" and width != height:
                    geometry_failures.append(descr + ":ellipse_not_square")
            if estimated_overflow(shape):
                overflow_shapes.append(descr)
            nonempty_texts = [node for node in shape.findall(".//a:t", NS) if (node.text or "").strip()]
            runs = shape.findall(".//a:r", NS)
            run_text_count = sum(1 for run in runs if any((node.text or "").strip() for node in run.findall("a:t", NS)))
            if nonempty_texts and run_text_count != len(nonempty_texts):
                font_failures.append(descr + ":text_without_run")
            for run in runs:
                if not any((node.text or "").strip() for node in run.findall("a:t", NS)):
                    continue
                rpr = run.find("a:rPr", NS)
                if rpr is None or int(rpr.get("sz", "0")) <= 0:
                    font_failures.append(descr + ":missing_or_zero_rpr")
                    continue
                latin = rpr.find("a:latin", NS)
                east = rpr.find("a:ea", NS)
                complex_script = rpr.find("a:cs", NS)
                if (
                    latin is None
                    or east is None
                    or complex_script is None
                    or latin.get("typeface") != FONT
                    or east.get("typeface") != FONT
                    or complex_script.get("typeface") != FONT
                ):
                    font_failures.append(descr + ":typeface")
            if shape.find(".//a:spAutoFit", NS) is not None or shape.find(".//a:normAutofit", NS) is not None:
                auto_fit_found = True
    check(all(count >= 8 for count in shape_counts), "native_editable_objects", f"native shape counts={shape_counts}", checks, failures)
    check(not picture_or_blip, "no_picture_objects", f"picture/blip slides={picture_or_blip}", checks, failures)
    check(not font_failures, "font_geometry", f"font failures={font_failures}", checks, failures)
    check(not auto_fit_found, "no_auto_fit", "no shape may use automatic text scaling", checks, failures)
    check(not geometry_failures, "shape_bounds", f"geometry failures={geometry_failures}", checks, failures)
    check(not overflow_shapes, "text_overflow", f"estimated overflow shapes={overflow_shapes}", checks, failures)
    density = [sum(len(item) for item in page) for page in all_slide_texts]
    check(all(value <= 620 for value in density), "no_keyword_pile", f"visible character counts={density}", checks, failures)

    page_text = ["\n".join(page) for page in all_slide_texts]
    required_by_page = [
        [f"{completion:.1f}%", f"缺口 {gap} 万元", f"{float(gross['actual']):.1f}%", f"{float(delivery['actual']):.1f}%", "不补造预测值"],
        ["华东", "华南", "华北", "西部", "105.5%", "88.9%", "西部缺口 30 万元", "华北缺口 24 万元"],
        [f"{float(west_delivery['actual']):.1f}%", "21 万元", f"{float(north_return['actual']):.1f}%", "0.6 个百分点", f"{float(promotion['actual']):.1f}%"],
        ["第 1—2 周", "第 1—4 周", "第 3—8 周", "供应链", "销售＋财务", "41.7%", "33.3%", "25.0%"],
        [f"当前 {completion:.1f}%", f"当前 {float(delivery['actual']):.1f}%", f"当前 {float(returns['actual']):.1f}%", f"当前 {float(promotion['actual']):.1f}%", "第 2、4、6、8 周"],
        ["供应链、产品、销售、财务", "每两周一次", f"西部交付 ≥ {float(west_delivery['plan']):.1f}%", f"华北退货 ≤ {float(north_return['plan']):.1f}%", f"促销毛利 ≥ {float(promotion['plan']):.1f}%"],
    ]
    for index, required in enumerate(required_by_page, 1):
        missing = [item for item in required if item not in page_text[index - 1]]
        check(not missing, f"slide_{index}_evidence", f"missing page-specific evidence={missing}", checks, failures)

    source_text = "\n".join(
        (workspace / relative).read_text(encoding="utf-8")
        for relative in ("TASK.md", "input/report.md", "input/metrics.csv", "input/brand.json")
    )
    allowed_numbers = set(re.findall(r"(?<![A-Za-z])\d+(?:[.,]\d+)?", source_text))
    for row in metrics.values():
        plan_value = float(row["plan"])
        actual_value = float(row["actual"])
        allowed_numbers.add(f"{abs(plan_value - actual_value):.1f}")
        if plan_value:
            allowed_numbers.add(f"{actual_value / plan_value * 100:.1f}")
    negative_gaps = [
        max(0.0, float(metrics[("revenue", region)]["plan"]) - float(metrics[("revenue", region)]["actual"]))
        for region in ("华南", "华北", "西部")
    ]
    negative_total = sum(negative_gaps)
    for value in negative_gaps:
        allowed_numbers.add(f"{value / negative_total * 100:.1f}")
    visible_numbers = set(re.findall(r"(?<![A-Za-z])\d+(?:[.,]\d+)?", "\n".join(page_text)))
    disallowed_numbers = sorted(visible_numbers - allowed_numbers)
    check(not disallowed_numbers, "numeric_grounding", f"untraceable visible numeric tokens={disallowed_numbers}", checks, failures)

    bars = []
    for shape in roots[1].findall(".//p:sp", NS):
        descr = shape_description(shape)
        if descr.startswith("chart_bar|"):
            fields = dict(item.split("=", 1) for item in descr.split("|")[1:])
            geometry = shape_geometry(shape)
            bars.append((fields, geometry))
    bar_ok = len(bars) == 8
    if bar_ok:
        for fields, geometry in bars:
            row = metrics[("revenue", fields["scope"])]
            expected_value = int(float(row[fields["series"]]))
            expected_width = round(6_600_000 * expected_value / 360.0)
            if fields.get("value") != str(expected_value) or fields.get("max") != "360" or geometry is None or geometry[2] != expected_width:
                bar_ok = False
    check(bar_ok, "chart_data_binding", f"validated editable bar count={len(bars)}", checks, failures)
    archive.close()
    evidence = {
        "output_sha256": sha256(output),
        "normalized_pptx_sha256": normalized_pptx_sha256(output),
        "slide_count": len(slide_names),
        "shape_counts": shape_counts,
        "visible_character_counts": density,
        "unexpected_files": unexpected,
        "media_parts": media,
    }
    return finalize(checks, failures, evidence)


def finalize(checks: list[dict[str, object]], failures: list[str], evidence: dict[str, object]) -> dict[str, object]:
    total = max(1, len(checks))
    passed = sum(bool(item["passed"]) for item in checks)
    return {
        "schema_version": "nami_work_case05_verifier_result_v1",
        "verdict": "accept" if not failures else "reject",
        "score": round(passed / total, 6),
        "checks": checks,
        "hard_failures": sorted(set(failures)),
        "evidence": evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.workspace.resolve())
    except Exception as exc:
        error_type = type(exc).__name__
        fingerprint = hashlib.sha256(
            f"case_05_malformed_ooxml:{error_type}".encode("utf-8")
        ).hexdigest()
        result = {
            "schema_version": "nami_work_case05_verifier_result_v1",
            "verdict": "reject",
            "score": 0.0,
            "checks": [
                {
                    "check_id": "malformed_ooxml",
                    "passed": False,
                    "detail": "malformed or unsupported OOXML attribute was rejected",
                }
            ],
            "hard_failures": ["malformed_ooxml"],
            "evidence": {
                "error_type": error_type,
                "error_fingerprint": fingerprint,
            },
        }
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.result:
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0 if result["verdict"] == "accept" else 1


if __name__ == "__main__":
    raise SystemExit(main())
