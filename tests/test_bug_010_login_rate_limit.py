"""BUG-010 — `POST /api/auth/login` และ `/api/auth/signup` เดิมไม่มี rate limit เลย.

อ้างอิง docs/tickets/BUG-010-login-rate-limit.md. ก่อนแก้ `_login()` เรียก
`store.verify_password()` (scrypt, ~16 MB + CPU จริงต่อครั้ง) ทันทีโดยไม่มีตัวนับใดๆ —
ทั้ง credential stuffing ไม่จำกัดจำนวนครั้ง และเป็น CPU/memory amplifier ที่ไม่ต้องล็อกอิน

หลังแก้มีสองชั้น: ตัวนับในหน่วยความจำ (`web/ratelimit.py`, ต่อ process) และตัวนับกลางใน
Postgres (`pgstore.rate_hit`, จำลองด้วย `FakeStore.rate_hit`/`rate_reset` ในเทสต์นี้ — ก่อน
เพิ่มสองเมธอดนี้ `_rate_limited()` จะจับ exception แล้วตกไปใช้ชั้นในหน่วยความจำเงียบๆ ชุดเทสต์
เดิมจึงไม่เคยพิสูจน์ชั้น DB เลย) ตัดสินใจก่อนอ่าน body และก่อนเรียก `verify_password` เสมอ —
เทสต์ที่สำคัญที่สุดในไฟล์นี้คือ test_bug_010_no_scrypt_after_block ซึ่งนับจำนวนครั้งที่
`verify_password` ถูกเรียกจริง ไม่ใช่แค่เช็คสถานะ 429 (เช็คแค่สถานะจะปล่อยให้ใครย้ายจุดเช็ค
รีเควสนับกลับไปอยู่หลัง verify_password ได้โดยเทสต์ยังผ่าน)

ห้ามยึดเวลานาฬิกาจริงเป็นเกณฑ์ผ่าน/ไม่ผ่าน (เครื่องรันเทสต์เร็วช้าไม่เท่ากัน) — ตัวเลขที่ตั๋ว
อ้างถึง (scrypt ~42.8 ms เทียบ request ที่ถูกบล็อก ~0.94 ms) เป็นของรายงาน dev เท่านั้น
ที่นี่พิสูจน์ด้วยจำนวนครั้งที่ verify_password ถูกเรียกแทน
"""

from __future__ import annotations

from unittest import mock

from _harness import CloudCase, server
from meeting_ai.web import ratelimit

LOGIN = "/api/auth/login"
SIGNUP = "/api/auth/signup"
LIMIT = server.AUTH_RATE_LIMIT  # 10 ครั้ง / 15 นาที ต่อ IP ต่อ endpoint (ดูตั๋ว)


class RateLimitBase(CloudCase):
    """ตัวนับในหน่วยความจำเป็น module-level state — ต้องล้างก่อน/หลังทุกเทสต์ ไม่งั้นเทสต์
    ที่รันก่อนหน้าจะทิ้งตัวนับค้างไว้ให้เทสต์ถัดไปเจอ 429 ทั้งที่ยังไม่ได้ยิงอะไรเลย.
    """

    def setUp(self) -> None:
        super().setUp()
        ratelimit.clear()
        self.addCleanup(ratelimit.clear)

    def _spam(self, path: str, body: dict, count: int) -> list[int]:
        return [self.post_json(path, body)[0] for _ in range(count)]


class TestLoginThreshold(RateLimitBase):
    def test_bug_010_login_blocks_at_threshold_with_retry_after(self):
        email = "alice@example.com"
        self.store.add_account(email, "correct-horse-1")
        bad = {"email": email, "password": "wrong"}

        statuses = self._spam(LOGIN, bad, LIMIT)
        self.assertEqual(statuses, [401] * LIMIT, "ก่อนถึงเพดานต้องเป็น 401 ปกติ ไม่ใช่ 429")
        self.assertEqual(len(self.store.verify_password_calls), LIMIT)

        status, body, headers = self.post_json(LOGIN, bad)
        self.assertEqual(status, 429)
        self.assertIn("Retry-After", headers)
        retry_after_header = int(headers["Retry-After"])
        self.assertGreaterEqual(retry_after_header, 1)
        self.assertLessEqual(retry_after_header, server.AUTH_RATE_WINDOW)
        self.assertEqual(body.get("retry_after"), retry_after_header)
        self.assertTrue(body.get("error"))

    def test_bug_010_no_scrypt_after_block(self):
        """หัวใจของตั๋ว: verify_password (ตัวแทนของ scrypt) ต้องไม่ถูกเรียกอีกเลยหลังถูกบล็อก."""
        email = "bob@example.com"
        self.store.add_account(email, "s3cr3t-passphrase")
        bad = {"email": email, "password": "wrong"}

        self._spam(LOGIN, bad, LIMIT)
        calls_at_threshold = len(self.store.verify_password_calls)
        self.assertEqual(calls_at_threshold, LIMIT)

        for _ in range(5):
            status, _, _ = self.post_json(LOGIN, bad)
            self.assertEqual(status, 429)

        self.assertEqual(
            len(self.store.verify_password_calls), calls_at_threshold,
            "verify_password (scrypt) ถูกเรียกหลังถูกบล็อกแล้ว — นี่คือ CPU/memory amplifier "
            "ที่ตั๋ว BUG-010 ตั้งใจปิด การเช็คแค่สถานะ 429 เพียงอย่างเดียวจะไม่จับบั๊กนี้",
        )

    def test_bug_010_successful_login_clears_counter(self):
        email = "carol@example.com"
        password = "right-password-1"
        self.store.add_account(email, password)
        bad = {"email": email, "password": "wrong"}
        good = {"email": email, "password": password}

        statuses = self._spam(LOGIN, bad, LIMIT - 1)
        self.assertEqual(statuses, [401] * (LIMIT - 1))

        status, body, headers = self.post_json(LOGIN, good)
        self.assertEqual(status, 200)
        self.assertIn("Set-Cookie", headers)
        self.assertIn("user", body)

        # ตัวนับต้องถูกล้างจริง ไม่ใช่แค่ "ยังไม่ทะลุเพดานเดิม" — ยิงผิดครบโควตาใหม่ทั้งหมด
        # ต้องไม่ติด 429 แม้แต่ครั้งเดียว จนกว่าจะถึงครั้งที่ LIMIT + 1 ของรอบใหม่
        statuses2 = self._spam(LOGIN, bad, LIMIT)
        self.assertEqual(statuses2, [401] * LIMIT,
                         "ตัวนับไม่ถูกล้างหลังล็อกอินสำเร็จ — ผู้ใช้จริงจะโดนล็อกทั้งที่ยังไม่ได้ทำอะไรผิด")
        status3, _, _ = self.post_json(LOGIN, bad)
        self.assertEqual(status3, 429)


class TestNoAccountEnumeration(RateLimitBase):
    def test_bug_010_429_identical_for_real_unknown_and_empty_body(self):
        email = "dora@example.com"
        password = "dora-real-password"
        self.store.add_account(email, password)

        # เอาโควตาไปให้หมดด้วยอีเมลที่ไม่เกี่ยวเลย
        self._spam(LOGIN, {"email": "burner@example.com", "password": "x"}, LIMIT)
        calls_before = len(self.store.verify_password_calls)

        status_real, body_real, headers_real = self.post_json(
            LOGIN, {"email": email, "password": password})  # อีเมลจริง รหัสถูกเป๊ะ
        status_unknown, body_unknown, headers_unknown = self.post_json(
            LOGIN, {"email": "nobody@example.com", "password": "whatever"})
        status_empty, body_empty, headers_empty = self.post_raw(LOGIN, b"")  # body ว่างสนิท

        for status in (status_real, status_unknown, status_empty):
            self.assertEqual(status, 429)
        self.assertEqual(body_real, body_unknown)
        self.assertEqual(body_real, body_empty)
        self.assertEqual(headers_real.get("Retry-After"), headers_unknown.get("Retry-After"))
        self.assertEqual(headers_real.get("Retry-After"), headers_empty.get("Retry-After"))

        # แม้อีเมลจริง+รหัสถูกเป๊ะ ก็ต้องไม่แตะ verify_password เลยเมื่อถูกบล็อกไปแล้ว
        self.assertEqual(len(self.store.verify_password_calls), calls_before)


class TestForwardedFor(RateLimitBase):
    def test_bug_010_xff_ignored_when_trust_proxy_off(self):
        self.assertFalse(server.Handler.trust_proxy,
                         "baseline: ค่าเริ่มต้นของ config.trust_proxy ต้องเป็น False")
        bad = {"email": "eve@example.com", "password": "wrong"}
        for i in range(LIMIT):
            status, _, _ = self.post_json(LOGIN, bad, headers={"X-Forwarded-For": f"10.0.0.{i}"})
            self.assertEqual(status, 401)

        status, _, _ = self.post_json(LOGIN, bad, headers={"X-Forwarded-For": "10.0.0.250"})
        self.assertEqual(
            status, 429,
            "trust_proxy=False ต้องนับตาม client_address จริง — X-Forwarded-For ที่ต่างกัน "
            "ในแต่ละคำขอ (ปลอมได้ง่าย) ต้องไม่ช่วยหนีโควตา",
        )

    def test_bug_010_xff_honoured_when_trust_proxy_on(self):
        with mock.patch.object(server.Handler, "trust_proxy", True):
            bad = {"email": "frank@example.com", "password": "wrong"}
            for _ in range(LIMIT):
                status, _, _ = self.post_json(LOGIN, bad, headers={"X-Forwarded-For": "9.9.9.9"})
                self.assertEqual(status, 401)

            status_blocked, _, _ = self.post_json(
                LOGIN, bad, headers={"X-Forwarded-For": "9.9.9.9"})
            self.assertEqual(status_blocked, 429)

            # คนละ IP (ตาม header) ต้องไม่โดนหางเลขของ 9.9.9.9 — โควตายังเต็ม
            status_fresh, _, _ = self.post_json(
                LOGIN, bad, headers={"X-Forwarded-For": "8.8.8.8"})
            self.assertEqual(status_fresh, 401)


class TestSignupRateLimit(RateLimitBase):
    def test_bug_010_signup_is_rate_limited_in_its_own_bucket(self):
        # invite ผิดพลาดพอที่จะไม่ทำให้ signup สำเร็จ (สำเร็จจะเรียก _rate_ok และล้างตัวนับ) แต่
        # rate limit ต้องถูกเช็คก่อนตรวจ invite เสมอ — เห็นผลเป็น 403 ซ้ำๆ จนกว่าจะถึงเพดาน
        body = {"email": "newperson@example.com", "password": "a-long-enough-pw",
                "invite": "not-a-real-invite-code"}

        statuses = self._spam(SIGNUP, body, LIMIT)
        self.assertEqual(statuses, [403] * LIMIT)

        status, resp_body, headers = self.post_json(SIGNUP, body)
        self.assertEqual(status, 429)
        self.assertIn("Retry-After", headers)
        self.assertIn("retry_after", resp_body)

        # ถังแยกจาก login ของ IP เดียวกัน — login ต้องยังไม่โดนหางเลข
        login_status, _, _ = self.post_json(LOGIN, {"email": "someone@example.com",
                                                     "password": "wrong"})
        self.assertEqual(login_status, 401)


class TestBothLayersIndependently(RateLimitBase):
    """ข้อกำชับจาก dev: FakeStore เดิมไม่มี rate_hit/rate_reset ทำให้ชั้น Postgres fail-open
    เงียบๆ และเทสต์เดิมผ่านได้โดยไม่เคยพิสูจน์ชั้นนี้เลย — ที่นี่พิสูจน์แยกทั้งสองชั้น เพื่อไม่ให้
    ชั้นหนึ่งกลบอีกชั้นจนดูเหมือนใช้งานได้ทั้งที่จริงมีแค่ชั้นเดียวทำงาน.
    """

    def test_bug_010_in_memory_layer_blocks_even_if_db_layer_never_blocks(self):
        # ปิดชั้น DB ไม่ให้มีผลต่อการตัดสินใจเลย (คืน 0.0 เสมอ) — ถ้ายังบล็อกได้ แปลว่าชั้นใน
        # หน่วยความจำ (web/ratelimit.py) ทำงานได้ด้วยตัวเอง ไม่ได้พึ่งชั้น DB
        self.store.rate_hit = lambda key, limit, window: 0.0
        bad = {"email": "gina@example.com", "password": "wrong"}

        statuses = self._spam(LOGIN, bad, LIMIT)
        self.assertEqual(statuses, [401] * LIMIT)
        status, _, headers = self.post_json(LOGIN, bad)
        self.assertEqual(status, 429)
        self.assertIn("Retry-After", headers)

    def test_bug_010_db_layer_blocks_even_if_process_memory_is_reset_every_request(self):
        # จำลองแต่ละคำขอเป็นคนละ Vercel invocation (ไม่แชร์หน่วยความจำกัน) ด้วยการล้างชั้น
        # ในหน่วยความจำก่อนทุกครั้ง — ถ้ายังบล็อกได้ แปลว่าตัวนับกลาง (pgstore.rate_hit
        # จำลองด้วย FakeStore.rate_hit) ทำงานได้ด้วยตัวเอง
        bad = {"email": "hank@example.com", "password": "wrong"}
        statuses = []
        for _ in range(LIMIT):
            ratelimit.clear()
            status, _, _ = self.post_json(LOGIN, bad)
            statuses.append(status)
        self.assertEqual(statuses, [401] * LIMIT)

        ratelimit.clear()
        status, body, headers = self.post_json(LOGIN, bad)
        self.assertEqual(
            status, 429,
            "ตัวนับกลางใน Postgres ต้องบล็อกได้เองแม้หน่วยความจำของ process ถูกล้างก่อนทุก "
            "คำขอ (ทรง serverless) — ถ้าไม่ผ่าน แปลว่า _rate_limited พึ่งชั้นในหน่วยความจำ "
            "อย่างเดียวและ Vercel จะไม่มีการจำกัดจริง",
        )
        self.assertIn("Retry-After", headers)
        self.assertIn("retry_after", body)
