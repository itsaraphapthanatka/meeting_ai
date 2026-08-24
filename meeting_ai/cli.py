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
    md = summarizer.summarize(text, meeting_title=args.title, template=args.template)
    if args.output:
        Path(args.output).write_text(md, encoding="utf-8")
        print(f"✅ เขียนสรุป: {args.output}")
    else:
        print(md)
    return 0


def _cmd_process(args: argparse.Namespace) -> int:
    from . import pipeline
    pipeline.process_file(args.audio, title=args.title, language=args.lang,
                          out_dir=args.out_dir, template=args.template)
    return 0


def _cmd_bot(args: argparse.Namespace) -> int:
    import time

    from . import bot
    out_dir = Path(args.out_dir)
    name = args.output or f"meet_{time.strftime('%Y%m%d_%H%M')}.wav"
    wav = bot.join_and_record(
        args.url, out_dir / name, name=args.name, max_minutes=args.max_minutes,
    )
    if not args.no_process:
        from . import pipeline
        pipeline.process_file(wav, title=args.title, language=args.lang, out_dir=args.out_dir)
    return 0


def _cmd_bot_login(args: argparse.Namespace) -> int:
    from . import bot
    bot.login(args.site)
    return 0


def _cmd_web(args: argparse.Namespace) -> int:
    import os

    if args.cloud:
        # ต้องตั้งก่อน import web เพราะ backend เลือกแบ็กเอนด์ตอน import
        # เขียนชื่อตัวแปรตรงๆ ห้าม import จาก .web.backend มาอ่านค่าคงที่
        # เพราะการ import นั้นเองจะทำให้ backend เลือกโหมดไปก่อนที่เราจะตั้งค่า
        os.environ["MEETING_AI_CLOUD"] = "1"
    from . import web
    web.serve(host=args.host, port=args.port, open_browser=not args.no_open)
    return 0


def _cmd_db_init(args: argparse.Namespace) -> int:
    from .web import db
    gaps = db.missing_pieces()
    if gaps:
        print("❌ ยังขาด: " + "; ".join(gaps), file=sys.stderr)
        return 2
    tables = db.init()
    print("✅ สร้าง/อัปเดต schema `meeting_ai` เรียบร้อย")
    for t in tables:
        print(f"   - {t}")
    print("\nเปิดเว็บด้วย `mai web` แล้วสมัครบัญชีแรก — คนแรกจะเป็นแอดมินอัตโนมัติ")
    return 0


def worker_default_bots() -> int:
    """ค่าเริ่มต้นของ --max-bots (แยกออกมาไม่ให้ต้อง import worker ตอน parse args)."""
    return 3


def _cmd_worker(args: argparse.Namespace) -> int:
    from . import worker
    from .config import config
    token = args.token or config.worker_token
    if not token:
        print("❌ ต้องมี token — ใส่ --token หรือตั้ง WORKER_TOKEN ใน .env", file=sys.stderr)
        return 2
    return worker.run(api=args.api, token=token, once=args.once, poll=args.poll,
                      name=args.name, max_bots=args.max_bots)


def _add_template_arg(sp: argparse.ArgumentParser) -> None:
    from . import summarizer
    sp.add_argument(
        "--template", default=summarizer.DEFAULT_TEMPLATE, choices=list(summarizer.TEMPLATES),
        help="รูปแบบสรุป: " + ", ".join(f"{k}={v['label']}" for k, v in summarizer.TEMPLATES.items()),
    )


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
    _add_template_arg(sp)
    sp.set_defaults(func=_cmd_summarize)

    sp = sub.add_parser("process", help="ครบวงจร: ไฟล์เสียง → ถอดเสียง → สรุป → Markdown")
    sp.add_argument("audio", help="ไฟล์เสียง/วิดีโอของการประชุม")
    sp.add_argument("--title", help="ชื่อการประชุม")
    sp.add_argument("--lang", help="ภาษา (th/en/auto)")
    sp.add_argument("--out-dir", default="recordings", help="โฟลเดอร์ผลลัพธ์")
    _add_template_arg(sp)
    sp.set_defaults(func=_cmd_process)

    sp = sub.add_parser("bot", help="ส่งบอทเข้าห้องประชุมออนไลน์ (Google Meet) เพื่ออัด+สรุป")
    sp.add_argument("url", help="ลิงก์ห้องประชุม เช่น https://meet.google.com/xxx-xxxx-xxx")
    sp.add_argument("--name", default="AI Notetaker", help="ชื่อบอทที่แสดงในห้อง")
    sp.add_argument("-o", "--output", help="ชื่อไฟล์เสียง (ไม่ใส่ = ตั้งอัตโนมัติตามเวลา)")
    sp.add_argument("--out-dir", default="recordings", help="โฟลเดอร์ผลลัพธ์")
    sp.add_argument("--title", help="ชื่อการประชุม (ใส่ในสรุป)")
    sp.add_argument("--lang", help="ภาษา (th/en/auto)")
    sp.add_argument("--max-minutes", type=int, default=180, help="เวลาสูงสุดที่บอทอยู่ในห้อง")
    sp.add_argument("--no-process", action="store_true", help="อัดอย่างเดียว ไม่ต้องถอด/สรุป")
    sp.set_defaults(func=_cmd_bot)

    sp = sub.add_parser("bot-login", help="ล็อกอินให้บอทครั้งเดียว (ผ่านเบราว์เซอร์) — จำเป็นถ้าห้องไม่รับ guest")
    sp.add_argument("--site", choices=("google", "teams", "zoom"), default="google",
                    help="จะล็อกอินเจ้าไหน (profile เดียวเก็บได้หลายเจ้า)")
    sp.set_defaults(func=_cmd_bot_login)

    sp = sub.add_parser("web", help="เปิดหน้าเว็บ: อัปโหลด/อัดสด/ค้นหาคลังการประชุม")
    sp.add_argument("--host", default="127.0.0.1",
                    help="interface ที่ผูก (ค่าเริ่มต้นเปิดได้จากเครื่องนี้เท่านั้น)")
    sp.add_argument("--port", type=int, default=8765, help="พอร์ต (ค่าเริ่มต้น 8765)")
    sp.add_argument("--no-open", action="store_true", help="ไม่ต้องเปิดเบราว์เซอร์ให้อัตโนมัติ")
    sp.add_argument("--cloud", action="store_true",
                    help="ใช้ Postgres + ระบบล็อกอิน/แชร์ (ต้องตั้ง DATABASE_URL) "
                         "ไม่ใส่ = เก็บเป็นไฟล์ในเครื่อง ไม่มีล็อกอิน")
    sp.set_defaults(func=_cmd_web)

    sp = sub.add_parser("db-init", help="สร้างตารางใน Postgres (โหมด cloud — ต้องตั้ง DATABASE_URL)")
    sp.set_defaults(func=_cmd_db_init)

    sp = sub.add_parser(
        "worker",
        help="รับงานถอดเสียงจากเซิร์ฟเวอร์ (ใช้ตอนเว็บอยู่บน cloud ที่ไม่มี GPU)",
    )
    sp.add_argument("--api", default="http://127.0.0.1:8765",
                    help="ที่อยู่เซิร์ฟเวอร์ เช่น https://xxx.vercel.app")
    sp.add_argument("--token", help="WORKER_TOKEN (ไม่ใส่ = อ่านจาก .env)")
    sp.add_argument("--once", action="store_true", help="ทำงานเดียวแล้วออก (ใช้ทดสอบ)")
    sp.add_argument("--poll", type=float, default=3.0, help="วินาทีที่รอเมื่อคิวว่าง")
    sp.add_argument("--name", help="ชื่อเครื่องที่จะโชว์ในหน้าเว็บ (ไม่ใส่ = ชื่อ hostname)")
    sp.add_argument("--max-bots", type=int, default=worker_default_bots(),
                    help="รับงานบอทพร้อมกันได้กี่ห้อง (ถอดเสียงยังทำทีละงาน)")
    sp.set_defaults(func=_cmd_worker)

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
