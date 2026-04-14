#!/usr/bin/env python3
"""Demo 模式：写入符合 ground_truth 的 out/summary.json 与 out/report.docx。"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

_TASK = Path(__file__).resolve().parent.parent


def _docx_xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_report_docx() -> bytes:
    paras = [
        "Quarterly Sales Memo",
        "Per POLICY-2024-Q3, rows with status return were excluded from amount sums.",
        "Regional subtotals: North 1300, South 500, East 400. Grand total 2200.",
    ]
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{_docx_xml_escape(p)}</w:t></w:r></w:p>'
        for p in paras
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    ct = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
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
    dr = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ct)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/_rels/document.xml.rels", dr)
    return buf.getvalue()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: demo_apply.py <WORKSPACE>", file=sys.stderr)
        return 2
    w = Path(sys.argv[1]).resolve()
    gt = json.loads((_TASK / "ground_truth.json").read_text(encoding="utf-8"))
    out = w / "out"
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "policy_id": gt["policy_id"],
        "exclude_status": gt["exclude_status"],
        "totals_by_region": gt["totals_by_region"],
        "grand_total": gt["grand_total"],
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out / "report.docx").write_bytes(_build_report_docx())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
