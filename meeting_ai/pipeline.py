"""ร้อยขั้นตอน: ไฟล์เสียง → ถอดเสียง → สรุป → เขียนผลเป็น Markdown."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import summarizer, transcriber


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def build_report(
    title: str,
    transcript: transcriber.Transcript,
    summary_md: str,
) -> str:
    return f"""# บันทึกการประชุม: {title}

_สร้างโดย meeting_ai · {_stamp()} · ภาษา: {transcript.language}_

{summary_md}

---

<details>
<summary>📝 บทถอดเสียงเต็ม (คลิกเพื่อดู)</summary>

```
{transcript.to_timestamped()}
```
</details>
"""


def process_file(
    audio_path: str | Path,
    title: str | None = None,
    language: str | None = None,
    out_dir: str | Path = "recordings",
) -> dict:
    """รันทั้ง pipeline กับไฟล์เสียงหนึ่งไฟล์. คืน dict ของ path ผลลัพธ์."""
    audio_path = Path(audio_path)
    title = title or audio_path.stem
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"🎧 ถอดเสียง: {audio_path.name} ...")
    transcript = transcriber.transcribe(audio_path, language=language)
    print(f"   ได้ {len(transcript.segments)} ช่วงประโยค")

    print("🧠 กำลังสรุปด้วย LLM ...")
    summary = summarizer.summarize(transcript.text, meeting_title=title)

    report = build_report(title, transcript, summary)

    base = out_dir / audio_path.stem
    report_path = base.with_name(base.name + "_สรุป.md")
    transcript_path = base.with_name(base.name + "_transcript.txt")

    report_path.write_text(report, encoding="utf-8")
    transcript_path.write_text(transcript.to_timestamped(), encoding="utf-8")

    print(f"✅ เสร็จ! สรุป: {report_path}")
    return {"report": report_path, "transcript": transcript_path, "summary": summary}
