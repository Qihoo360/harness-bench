#!/usr/bin/env python3
"""Generate fixtures/sales.csv, policy.pdf, template.docx (stdlib only)."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

_TASK = Path(__file__).resolve().parent.parent
_FIX = _TASK / "fixtures"


def _write_sales_csv() -> None:
    _FIX.mkdir(parents=True, exist_ok=True)
    (_FIX / "sales.csv").write_text(
        "region,category,amount,status\n"
        "North,Electronics,1000,ok\n"
        "North,Electronics,250,return\n"
        "North,Office,300,ok\n"
        "South,Electronics,500,ok\n"
        "South,Office,200,return\n"
        "East,Electronics,400,ok\n",
        encoding="utf-8",
    )


def _pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_pdf_bytes() -> bytes:
    lines = [
        "POLICY-2024-Q3 Regional Sales Consolidation",
        "",
        "Rules:",
        "1. Exclude rows where status equals return from all amount sums.",
        "2. Sum amount by region for remaining rows only.",
        "3. Final report.docx must cite policy id POLICY-2024-Q3.",
        "4. Write out/summary.json with: policy_id, exclude_status, totals_by_region, grand_total.",
    ]
    parts: list[bytes] = [b"BT\n/F1 11 Tf\n"]
    x, y = 72, 720
    for i, ln in enumerate(lines):
        esc = _pdf_escape(ln).encode("latin-1", errors="replace")
        if i == 0:
            parts.append(f"{x} {y} Td (".encode("ascii") + esc + b") Tj\n")
        else:
            parts.append(b"0 -14 Td (" + esc + b") Tj\n")
    parts.append(b"ET")
    stream = b"".join(parts)

    o4 = b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"

    chunks: list[bytes] = [b"%PDF-1.4\n"]
    offsets: list[int] = [0]

    def add_obj(content: bytes) -> None:
        offsets.append(sum(len(c) for c in chunks))
        chunks.append(content)

    add_obj(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    add_obj(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    add_obj(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    )
    add_obj(b"4 0 obj\n" + o4 + b"\nendobj\n")
    add_obj(b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")

    body = b"".join(chunks)
    xref_pos = len(body)
    num_objs = 6
    xref = [f"xref\n0 {num_objs}\n0000000000 65535 f \n".encode("ascii")]
    # offsets[1]..offsets[5] are byte positions for objects 1..5
    for i in range(1, num_objs):
        off = offsets[i]
        xref.append(f"{off:010d} 00000 n \n".encode("ascii"))
    trailer = (
        b"trailer\n<< /Size "
        + str(num_objs).encode("ascii")
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref_pos).encode("ascii")
        + b"\n%%EOF\n"
    )
    return body + b"".join(xref) + trailer


def _write_policy_pdf() -> None:
    (_FIX / "policy.pdf").write_bytes(_build_pdf_bytes())


def _docx_xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_template_docx() -> bytes:
    paras = [
        "Quarterly Sales Report — Template",
        "Read sales.csv and policy.pdf in this workspace. Apply POLICY-2024-Q3: exclude rows with status return, then sum amount by region.",
        "Deliverables: (1) out/summary.json with policy_id, exclude_status, totals_by_region, grand_total.",
        "(2) out/report.docx — completed memo that cites POLICY-2024-Q3 and lists each region subtotal and the grand total.",
    ]
    body_parts = []
    for p in paras:
        t = _docx_xml_escape(p)
        body_parts.append(
            f'<w:p><w:r><w:t xml:space="preserve">{t}</w:t></w:r></w:p>'
        )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body_parts)}</w:body></w:document>"
    )
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""
    doc_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
    return buf.getvalue()


def _write_template_docx() -> None:
    (_FIX / "template.docx").write_bytes(_build_template_docx())


def main() -> None:
    _write_sales_csv()
    _write_policy_pdf()
    _write_template_docx()
    print("wrote", _FIX / "sales.csv", _FIX / "policy.pdf", _FIX / "template.docx")


if __name__ == "__main__":
    main()
