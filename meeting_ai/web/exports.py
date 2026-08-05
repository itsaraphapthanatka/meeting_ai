"""แปลงบันทึกการประชุมเป็นไฟล์ฟอร์แมตต่างๆ — stdlib ล้วน (.docx สร้างจาก zipfile ตรงๆ)."""

from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape

from .. import pipeline
from . import store

FORMATS = {
    "md": ("text/markdown; charset=utf-8", ".md"),
    "txt": ("text/plain; charset=utf-8", ".txt"),
    "srt": ("application/x-subrip; charset=utf-8", ".srt"),
    "vtt": ("text/vtt; charset=utf-8", ".vtt"),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
}


def _stamp(sec: float, comma: bool) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    sep = "," if comma else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _cue_text(seg: dict) -> str:
    speaker = seg.get("speaker")
    text = seg.get("text", "").strip()
    return f"{speaker}: {text}" if speaker else text


def to_srt(meeting: dict) -> str:
    out = []
    for i, seg in enumerate(meeting.get("segments_list", []), start=1):
        out.append(
            f"{i}\n"
            f"{_stamp(seg.get('start', 0), True)} --> {_stamp(seg.get('end', 0), True)}\n"
            f"{_cue_text(seg)}\n"
        )
    return "\n".join(out)


def to_vtt(meeting: dict) -> str:
    out = ["WEBVTT", ""]
    for seg in meeting.get("segments_list", []):
        out.append(f"{_stamp(seg.get('start', 0), False)} --> {_stamp(seg.get('end', 0), False)}")
        out.append(_cue_text(seg))
        out.append("")
    return "\n".join(out)


def to_txt(meeting: dict) -> str:
    head = [
        f"บันทึกการประชุม: {meeting.get('title', '')}",
        f"วันที่: {meeting.get('created', '')}   ความยาว: {store.fmt_time(meeting.get('duration', 0))}",
    ]
    if meeting.get("speakers"):
        head.append("ผู้พูด: " + ", ".join(meeting["speakers"]))
    head += ["", "=" * 60, "สรุป", "=" * 60, "", meeting.get("summary", "") or "(ไม่มีสรุป)",
             "", "=" * 60, "บทถอดเสียง", "=" * 60, "", meeting.get("transcript", "")]
    return "\n".join(head)


def to_md(meeting: dict) -> str:
    return pipeline.build_report_parts(
        title=meeting.get("title", ""),
        language=meeting.get("language", ""),
        summary_md=meeting.get("summary", ""),
        timestamped=meeting.get("transcript", ""),
    )


# ---------- docx ----------

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _heading_style(level: int, size_half_pt: int) -> str:
    return (
        f'<w:style w:type="paragraph" w:styleId="Heading{level}">'
        f'<w:name w:val="heading {level}"/><w:basedOn w:val="Normal"/>'
        f'<w:pPr><w:keepNext/><w:spacing w:before="{240 - level * 20}" w:after="80"/></w:pPr>'
        f'<w:rPr><w:b/><w:sz w:val="{size_half_pt}"/></w:rPr></w:style>'
    )


# ไม่มี styles.xml แล้ว Word จะมองข้าม w:pStyle ทั้งหมด สรุปจะออกมาเป็นข้อความเรียบทั้งหน้า
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f"<w:styles {_W_NS}>"
    '<w:docDefaults><w:rPrDefault><w:rPr>'
    '<w:rFonts w:ascii="Sarabun" w:hAnsi="Sarabun" w:cs="Sarabun"/>'
    '<w:sz w:val="22"/><w:szCs w:val="22"/>'
    "</w:rPr></w:rPrDefault></w:docDefaults>"
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
    '<w:name w:val="Normal"/><w:pPr><w:spacing w:after="80"/></w:pPr></w:style>'
    + _heading_style(1, 40)
    + _heading_style(2, 30)
    + _heading_style(3, 26)
    + _heading_style(4, 24)
    + '<w:style w:type="paragraph" w:styleId="ListParagraph">'
    '<w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:ind w:left="360"/><w:spacing w:after="20"/></w:pPr></w:style>'
    "</w:styles>"
)

_TABLE_SEP_RE = re.compile(r"^\|?[\s:-]*-[\s|:-]*\|?$")


def _para(text: str, style: str | None = None, bold: bool = False) -> str:
    props = []
    if style:
        props.append(f'<w:pStyle w:val="{style}"/>')
    ppr = f"<w:pPr>{''.join(props)}</w:pPr>" if props else ""
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return (
        f"<w:p>{ppr}<w:r>{rpr}"
        f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
    )


def _row(cells: list[str], header: bool) -> str:
    tcs = []
    for c in cells:
        tcs.append(
            "<w:tc><w:tcPr><w:tcBorders>"
            '<w:top w:val="single" w:sz="4"/><w:bottom w:val="single" w:sz="4"/>'
            '<w:left w:val="single" w:sz="4"/><w:right w:val="single" w:sz="4"/>'
            f"</w:tcBorders></w:tcPr>{_para(c, bold=header)}</w:tc>"
        )
    return f"<w:tr>{''.join(tcs)}</w:tr>"


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _markdown_to_docx_body(md: str) -> str:
    """แปลง Markdown subset ที่เราสร้าง (heading, bullet, ตาราง) เป็น WordprocessingML."""
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            level = min(4, len(heading[1]))
            out.append(_para(heading[2], style=f"Heading{level}"))
            i += 1
            continue

        if line.strip().startswith("|") and _TABLE_SEP_RE.match((lines[i + 1] or "").strip() if i + 1 < len(lines) else ""):
            head = _split_row(line)
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_split_row(lines[i]))
                i += 1
            body = _row(head, True) + "".join(_row(r, False) for r in rows)
            out.append(
                '<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/></w:tblPr>'
                + body + "</w:tbl>" + _para("")
            )
            continue

        bullet = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if bullet:
            out.append(_para("• " + bullet[1], style="ListParagraph"))
            i += 1
            continue

        out.append(_para(re.sub(r"\*\*([^*]+)\*\*", r"\1", line.strip())))
        i += 1
    return "".join(out)


def to_docx(meeting: dict) -> bytes:
    body = [
        _para(f"บันทึกการประชุม: {meeting.get('title', '')}", style="Heading1"),
        _para(
            f"{meeting.get('created', '')} · ความยาว {store.fmt_time(meeting.get('duration', 0))}"
            + (" · ผู้พูด: " + ", ".join(meeting["speakers"]) if meeting.get("speakers") else "")
        ),
        _markdown_to_docx_body(meeting.get("summary", "") or "(ไม่มีสรุป)"),
        _para("บทถอดเสียงเต็ม", style="Heading2"),
    ]
    for seg in meeting.get("segments_list", []):
        head = f"[{store.fmt_time(seg.get('start', 0))}]"
        speaker = seg.get("speaker")
        if speaker:
            head += f" {speaker}:"
        body.append(_para(f"{head} {seg.get('text', '').strip()}"))

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {_W_NS}>"
        f"<w:body>{''.join(body)}</w:body></w:document>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        zf.writestr("word/styles.xml", _STYLES)
        zf.writestr("word/document.xml", document)
    return buf.getvalue()


def render(meeting: dict, fmt: str) -> tuple[bytes, str]:
    """คืน (bytes, content-type) ของฟอร์แมตที่ขอ."""
    if fmt not in FORMATS:
        raise ValueError(f"ไม่รองรับฟอร์แมต '{fmt}' (ที่มี: {', '.join(FORMATS)})")
    ctype = FORMATS[fmt][0]
    if fmt == "docx":
        return to_docx(meeting), ctype
    text = {"md": to_md, "txt": to_txt, "srt": to_srt, "vtt": to_vtt}[fmt](meeting)
    return text.encode("utf-8"), ctype
