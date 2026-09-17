"""BUG-010 — รอบรีวิวที่ 2: สามช่องโหว่ที่ code-reviewer เจอ ผ่านชุดเทสต์รอบแรกทั้งหมด
(`tests/test_bug_010_login_rate_limit.py`, 73 tests, 1 skipped) เพราะรอบแรกไม่ได้ครอบคลุม:

1. `X-Vercel-Forwarded-For` เดิมถูก hardcode ไว้ที่ `Handler._client_ip` กลาง — Handler ฐาน
   (self-hosted, ไม่มี edge ของ Vercel คั่น) เลยเผลอเชื่อหัวข้อนี้ไปด้วย ทั้งที่ proxy ทั่วไป
   (nginx/Cloudflare) ส่งหัวข้อที่ไม่รู้จักผ่านไปตรงๆ = ใครก็ตั้งหัวข้อนี้เองเพื่อเลือกถังใหม่ได้
   ทุกคำขอ (ก่อนแก้: 40 คำขอที่ผลัดกันใช้หัวข้อนี้ 40 ค่า ได้ 40 ถัง ไม่มี 429 เลยสักครั้ง)
   ตอนนี้ย้ายเป็น `Handler.forwarded_headers` (คลาสตัวแปร) ที่ `api/index.py` เท่านั้นเป็นคนขยาย
2. `_client_ip` เดิมใช้ regex ยอมรับขยะ (`....`, `999.999.999.999`) และไม่ตัดพอร์ตทิ้ง —
   `127.0.0.1:8080` ถูกเก็บทั้งพอร์ต ทำให้ proxy ที่ต่อพอร์ตมาด้วยจริง (Azure App Gateway,
   IIS ARR) ได้ถังใหม่ทุกการเชื่อมต่อ = ไม่ได้จำกัดอะไรเลย ตอนนี้เปลี่ยนไปใช้ `_ip_key()`
   (stdlib `ipaddress`) ตัดพอร์ต, ยุบ IPv6 เป็น /64, ยุบ ::ffff:a.b.c.d กลับเป็น IPv4
3. `web/ratelimit.py::_prune` เดิมเรียกหลัง `hit()` เพิ่มค่าและใส่คีย์ใหม่ลงตารางแล้ว โดยเรียง
   ตาม count มากไปน้อยแล้วเก็บ MAX_KEYS แรก — คีย์ที่เพิ่งถูกนับครั้งแรก (count=1) เมื่อ dict
   เต็มเพดานด้วยคีย์อื่นที่ count สูงกว่าอยู่แล้ว จะโดนทิ้งในคำเรียกเดียวกับที่เพิ่งสร้างมันขึ้นมา
   ผลคือคีย์นั้น "นับไม่ทันถึงเพดานเลย" คืน 0.0 ตลอดไป (ราวกับไม่มี rate limit ทั้งที่ระบบคิดว่ามี)
4. ผู้ใช้ที่ล็อกอินอยู่แล้ว (มี `mai_session` ใช้ได้) ควรข้ามถังแคบ (10/15นาที, ล้างได้ตอนสำเร็จ)
   ได้ กันคนนอกยิงขยะจาก IP เดียวกัน (เช่นออฟฟิศ) ไม่ให้ล็อกคนในตึกที่ล็อกอินอยู่แล้วออกไปด้วย
   แต่ต้องไม่ข้ามถังแข็ง (60/ชั่วโมง, ไม่ล้าง) ไม่งั้นคนที่มีบัญชีจริงใบเดียวจะไม่มีเพดาน scrypt เลย

ห้ามยึดเวลานาฬิกาจริงเป็นเกณฑ์ผ่าน/ไม่ผ่าน — ใช้จำนวนครั้ง/สถานะ/เนื้อหาตัวนับแทนเสมอ
"""

from __future__ import annotations

import threading
import unittest
from unittest import mock

from _harness import CloudCase, server
from meeting_ai.web import ratelimit

LOGIN = "/api/auth/login"
LIMIT = server.AUTH_RATE_LIMIT          # ถังแคบ: 10 / 15 นาที
HARD_LIMIT = server.AUTH_HARD_LIMIT     # ถังแข็ง: 60 / ชั่วโมง


class RateLimitBase(CloudCase):
    def setUp(self) -> None:
        super().setUp()
        ratelimit.clear()
        self.addCleanup(ratelimit.clear)


# ---------- 1) forwarded_headers: ฐานไม่เชื่อ X-Vercel-Forwarded-For, subclass เชื่อได้ ----------

class TestForwardedHeadersAllowlist(RateLimitBase):
    def test_bug_010_base_handler_ignores_x_vercel_forwarded_for(self):
        self.assertEqual(server.Handler.forwarded_headers, ("X-Forwarded-For",),
                         "baseline: Handler กลาง (self-hosted) ต้องไม่มี X-Vercel-Forwarded-For "
                         "อยู่ในรายการที่ยอมอ่าน")
        with mock.patch.object(server.Handler, "trust_proxy", True):
            bad = {"email": "ivan@example.com", "password": "wrong"}
            # ผลัดใช้ค่าใหม่ทุกครั้งในหัวข้อที่ Handler นี้ไม่ควรอ่าน — ถ้าอ่าน จะได้ 40 ถัง
            # ไม่มี 429 เลยสักครั้ง (นี่คือช่องโหว่ที่รีวิวรอบสองเจอ)
            for i in range(LIMIT):
                status, _, _ = self.post_json(
                    LOGIN, bad, headers={"X-Vercel-Forwarded-For": f"1.2.3.{i}"})
                self.assertEqual(status, 401)
            status, _, headers = self.post_json(
                LOGIN, bad, headers={"X-Vercel-Forwarded-For": "1.2.3.99"})
            self.assertEqual(
                status, 429,
                "Handler กลางต้องเมิน X-Vercel-Forwarded-For และตกไปใช้ client_address "
                "(คงที่ตลอดทดสอบนี้) — ค่าที่ต่างกันในหัวข้อนี้ต้องไม่สร้างถังใหม่",
            )
            self.assertIn("Retry-After", headers)

    def test_bug_010_vercel_style_subclass_honours_its_own_header(self):
        # จำลอง api/index.py: subclass ที่ประกาศ forwarded_headers ของตัวเอง (ไม่แก้ Handler กลาง)
        class VercelLikeHandler(server.Handler):
            forwarded_headers = ("X-Vercel-Forwarded-For", "X-Forwarded-For")

        with mock.patch.object(server.Handler, "trust_proxy", True):
            httpd2 = server.Server(("127.0.0.1", 0), VercelLikeHandler)
            port2 = httpd2.server_address[1]
            thread2 = threading.Thread(target=httpd2.serve_forever, daemon=True)
            thread2.start()

            def _stop2() -> None:
                try:
                    httpd2.shutdown()
                finally:
                    httpd2.server_close()

            self.addCleanup(_stop2)

            original_port = self.port
            self.port = port2
            try:
                bad = {"email": "juno@example.com", "password": "wrong"}
                for _ in range(LIMIT):
                    status, _, _ = self.post_json(
                        LOGIN, bad, headers={"X-Vercel-Forwarded-For": "9.9.9.9"})
                    self.assertEqual(status, 401)
                status_blocked, _, _ = self.post_json(
                    LOGIN, bad, headers={"X-Vercel-Forwarded-For": "9.9.9.9"})
                self.assertEqual(status_blocked, 429)

                # คนละค่าในหัวข้อเดียวกัน = คนละถัง (พิสูจน์ว่า subclass "เชื่อ" หัวข้อนี้จริง)
                status_fresh, _, _ = self.post_json(
                    LOGIN, bad, headers={"X-Vercel-Forwarded-For": "8.8.4.4"})
                self.assertEqual(status_fresh, 401)
            finally:
                self.port = original_port


# ---------- 2) _ip_key(): ตัดพอร์ต / ยุบ IPv6 เป็น /64 / ปฏิเสธขยะ ----------

class TestIpKeyNormalisation(unittest.TestCase):
    def test_bug_010_ip_key_table(self):
        cases = [
            ("127.0.0.1:8080", "127.0.0.1"),           # IIS ARR / Azure App Gateway ต่อพอร์ตมา
            ("[2001:db8::1]:443", "2001:db8::/64"),
            ("::ffff:1.2.3.4", "1.2.3.4"),              # IPv4-mapped IPv6
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(server._ip_key(raw), expected)

    def test_bug_010_ip_key_ipv6_forms_collapse_to_same_bucket(self):
        forms = ["2001:db8::1", "2001:0db8::0001", "2001:DB8::1"]
        keys = {server._ip_key(f) for f in forms}
        self.assertEqual(len(keys), 1,
                         "รูปเขียนต่างกันของ IPv6 เดียวกันต้องได้คีย์เดียวกัน ไม่งั้นคนหนีโควตา "
                         "ได้แค่เปลี่ยนวิธีเขียนที่อยู่เดิม")
        self.assertEqual(keys.pop(), "2001:db8::/64")

    def test_bug_010_ip_key_rejects_junk_falls_back_to_peer(self):
        for junk in ("....", ":::", "abc", "999.999.999.999"):
            with self.subTest(junk=junk):
                self.assertEqual(
                    server._ip_key(junk), "",
                    "ค่าที่ไม่ใช่ IP ต้องคืนค่าว่าง (ให้ผู้เรียกตกไปใช้ TCP peer address แทน) "
                    "ไม่ใช่กลายเป็นคีย์ตามใจคนนอก",
                )


# ---------- 3) ratelimit._prune ต้องไม่ทิ้งคีย์ที่กำลังนับอยู่ ----------

class TestRatelimitEviction(unittest.TestCase):
    def setUp(self) -> None:
        ratelimit.clear()
        self.addCleanup(ratelimit.clear)

    def test_bug_010_hot_key_reaches_its_limit_despite_a_full_table(self):
        limit, window = 3, 300
        hot = "login:203.0.113.9"

        # เติม dict ให้เต็มเพดานด้วยคีย์อื่นที่ "count สูงกว่า hot เสมอ" (hot เริ่มที่ 1) —
        # การตัดแต่งแบบเก่า (เรียงตาม count มากไปน้อย เก็บ MAX_KEYS แรก) จะทิ้ง hot ทันทีที่มัน
        # ถูกสร้างในคำเรียกเดียวกัน เพราะ count ของมันต่ำสุดในตารางเสมอ
        for i in range(ratelimit.MAX_KEYS):
            filler = f"filler:{i}"
            for _ in range(limit):
                ratelimit.hit(filler, 10_000, window)

        waits = [ratelimit.hit(hot, limit, window) for _ in range(limit + 2)]

        self.assertIn(
            hot, ratelimit._hits,
            "คีย์ที่กำลังถูกนับอยู่ (ผ่านมาแล้ว >=1 ครั้งในลูปนี้) ต้องไม่ถูกทิ้งจากการตัดแต่ง",
        )
        self.assertTrue(
            any(w > 0 for w in waits),
            "ก่อนแก้: hot ถูกทิ้งทุกครั้งที่นับใหม่ (นับไม่ทันถึงเพดาน) จึงคืน 0.0 ตลอดไป "
            "ทั้งที่ยิงเกินเพดานไปแล้วหลายรอบ",
        )

    def test_bug_010_blocked_key_survives_flood_of_new_keys(self):
        blocked_key = "login:198.51.100.5"
        ratelimit.block(blocked_key, 120)

        for i in range(ratelimit.HARD_MAX_KEYS + 500):
            ratelimit.hit(f"flood:{i}", 10_000, 60)

        self.assertIn(blocked_key, ratelimit._hits,
                     "รายการที่ถูกบล็อกอยู่คือรายการที่กำลังทำร้ายเรา ต้องอยู่จนหมดหน้าต่างของมัน "
                     "แม้คีย์ใหม่ท่วมเข้ามาเกินเพดานทั้งสองชั้น")
        self.assertEqual(ratelimit._hits[blocked_key][1], ratelimit._BLOCKED)


# ---------- 4) session ที่ยังใช้ได้ ข้ามถังแคบได้ แต่ข้ามถังแข็งไม่ได้ ----------

class TestSessionBypassesSoftBucketOnly(RateLimitBase):
    def test_bug_010_valid_session_skips_soft_bucket_but_still_hits_hard_bucket(self):
        bad = {"email": "kim@example.com", "password": "wrong"}
        cookies = {"mai_session": self.tokA}   # session ที่ยังใช้ได้จาก CloudCase._seed

        # เกินถังแคบ (LIMIT) ไปมาก แต่ต้องยังไม่โดน 429 เลยสักครั้ง เพราะถือ session อยู่
        soft_over = LIMIT + 5
        statuses = [self.post_json(LOGIN, bad, cookies=cookies)[0] for _ in range(soft_over)]
        self.assertEqual(
            statuses, [401] * soft_over,
            "ผู้ใช้ที่ล็อกอินอยู่แล้วต้องข้ามถังแคบได้ ไม่งั้นคนนอกยิงขยะจาก IP เดียวกัน (เช่น "
            "ออฟฟิศเดียวกัน) จะล็อกคนในตึกที่ล็อกอินอยู่แล้วออกจากระบบไปด้วย",
        )

        # ถังแข็ง (60/ชม.) ยังนับต่อเนื่องมาตั้งแต่ต้น — ต้องกดเพดานได้แม้ถือ session อยู่
        remaining = HARD_LIMIT - soft_over
        statuses2 = [self.post_json(LOGIN, bad, cookies=cookies)[0] for _ in range(remaining)]
        self.assertEqual(statuses2, [401] * remaining)

        status_blocked, body, headers = self.post_json(LOGIN, bad, cookies=cookies)
        self.assertEqual(
            status_blocked, 429,
            "session ที่ยังใช้ได้ต้องข้ามได้แค่ถังแคบ ไม่ใช่ถังแข็ง — ไม่งั้นคนที่มีบัญชีจริง "
            "ใบเดียว (หรือรหัสผ่านที่ซื้อมา) จะไม่มีเพดานงาน scrypt เลย",
        )
        self.assertIn("Retry-After", headers)
        self.assertIn("retry_after", body)
