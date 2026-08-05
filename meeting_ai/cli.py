"""จุดเข้าใช้งานแบบ command-line ของ meeting_ai."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cmd_devices(args: argparse.Namespace) -> int:
    from . import recorder
    print(recorder.list_devices())
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    from . import recorder
    recorder.record(args.output, mic=not args.no_mic, system=not args.no_system)
    if args.process:
        from . import pipeline
        pipeline.process_file(args.output, title=args.title, language=args.lang)
    return 0


def _cmd_transcribe(args: argparse.Namespace) -> int:
    from . import transcriber
    t = transcriber.transcribe(args.audio, language=args.lang)
    out = t.to_timestamped()
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"✅ เขียน transcript: {args.output}")
    else:
        print(out)
    return 0


def _cmd_summarize(args: argparse.Namespace) -> int:
    from . import summarizer
    text = Path(args.transcript).read_text(encoding="utf-8")
    md = summarizer.summarize(text, meeting_title=args.title)
    if args.output:
        Path(args.output).write_text(md, encoding="utf-8")
        print(f"✅ เขียนสรุป: {args.output}")
    else:
        print(md)
    return 0


def _cmd_process(args: argparse.Namespace) -> int:
    from . import pipeline
    pipeline.process_file(args.audio, title=args.title, language=args.lang, out_dir=args.out_dir)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mai",
        description="🎙️ meeting_ai — บันทึกและสรุปการประชุมด้วย AI",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("devices", help="แสดงรายชื่ออุปกรณ์เสียง (หา index สำหรับอัด)")
    sp.set_defaults(func=_cmd_devices)

    sp = sub.add_parser("record", help="อัดเสียงประชุมสด (Ctrl+C เพื่อหยุด)")
    sp.add_argument("output", help="ไฟล์ปลายทาง เช่น recordings/meeting.wav")
    sp.add_argument("--no-mic", action="store_true", help="ไม่อัดไมค์")
    sp.add_argument("--no-system", action="store_true", help="ไม่อัดเสียงระบบ (BlackHole)")
    sp.add_argument("--process", action="store_true", help="ถอดเสียง+สรุปต่อทันทีหลังอัดเสร็จ")
    sp.add_argument("--title", help="ชื่อการประชุม")
    sp.add_argument("--lang", help="ภาษา (th/en/auto)")
    sp.set_defaults(func=_cmd_record)

    sp = sub.add_parser("transcribe", help="ถอดเสียงไฟล์เป็นข้อความ")
    sp.add_argument("audio", help="ไฟล์เสียง/วิดีโอ")
    sp.add_argument("-o", "--output", help="ไฟล์ผลลัพธ์ (ไม่ใส่ = พิมพ์ออกจอ)")
    sp.add_argument("--lang", help="ภาษา (th/en/auto)")
    sp.set_defaults(func=_cmd_transcribe)

    sp = sub.add_parser("summarize", help="สรุปจากไฟล์ transcript ที่มีอยู่แล้ว")
    sp.add_argument("transcript", help="ไฟล์ข้อความ transcript")
    sp.add_argument("-o", "--output", help="ไฟล์ผลลัพธ์ (ไม่ใส่ = พิมพ์ออกจอ)")
    sp.add_argument("--title", help="ชื่อการประชุม")
    sp.set_defaults(func=_cmd_summarize)

    sp = sub.add_parser("process", help="ครบวงจร: ไฟล์เสียง → ถอดเสียง → สรุป → Markdown")
    sp.add_argument("audio", help="ไฟล์เสียง/วิดีโอของการประชุม")
    sp.add_argument("--title", help="ชื่อการประชุม")
    sp.add_argument("--lang", help="ภาษา (th/en/auto)")
    sp.add_argument("--out-dir", default="recordings", help="โฟลเดอร์ผลลัพธ์")
    sp.set_defaults(func=_cmd_process)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # แสดง error เป็นภาษาคนอ่านง่าย
        print(f"❌ ผิดพลาด: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
