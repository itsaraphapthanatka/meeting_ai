"""BUG-012 — invite redemption TOCTOU (สมัครพร้อมกันด้วยรหัสเชิญเดียวได้หลายบัญชี).

ticket: docs/tickets/BUG-012-invite-redemption-toctou.md

จุดตัดสินสิทธิ์ตัวจริงคือ store.claim_invite() / store.claim_first_admin() (statement เดียว
ต่อการเรียกหนึ่งครั้ง) ส่วน store.invite_email() / store.count_users() / store.has_password()
เป็นแค่ pre-check ไว้เลือกข้อความเท่านั้น — เทสต์ในไฟล์นี้ยิง POST /api/auth/signup ผ่าน HTTP
จริงเข้า server.Handler (ไม่ import _signup ตรงๆ) เพื่อพิสูจน์พฤติกรรมที่ผู้ใช้เห็นจริง

เคส concurrency (SignupConcurrencyCase) ยิงจริงจาก N เธรดพร้อมกันด้วย threading.Barrier สอง
ชั้น: (1) เธรดฝั่งไคลเอนต์เริ่มพร้อมกันจริง (2) FakeStore.has_password() (จุดที่ _signup เรียก
เสมอ **หลัง** pre-check ทั้งหมดและ **ก่อน** เข้าสู่ claim_invite/claim_first_admin จริง — ดู
server.py:804) มี sync_barrier ขนาด N ให้ทุกเธรดต้องมาถึงพร้อมกันก่อนจะแยกไปแข่งกันที่จุด
ตัดสิน วิธีนี้บังคับหน้าต่าง race ให้เปิดจริงแบบ deterministic โดยไม่ใช้ sleep (ไม่ flaky
ตามความเร็วเครื่อง) — ดู docstring ของ FakeStore.has_password ใน tests/_harness.py

พิสูจน์แล้วว่าไม่ vacuous (ดูรายงาน test-engineer): สลับให้ FakeStore.claim_invite /
claim_first_admin คืน True เสมอ (จำลองโค้ดเดิมที่ทิ้งค่า return ของ redeem_invite) แล้วรัน
test_bug_012_concurrent_signups_* — ได้บัญชี/แอดมินมากกว่า 1 ทันที ตรงกับ negative control
ในตั๋ว (6 บัญชีจาก 1 รหัสเชิญ, แอดมิน 6 คนจากระบบว่าง) ก่อนคืนค่าเดิม
"""

from __future__ import annotations

import threading
import unittest
from contextlib import contextmanager
from unittest import mock

from _harness import AuthCase
from meeting_ai.web import pgstore


class SignupSequentialCase(AuthCase):
    """เคสลำดับเดิม (ไม่มีการแข่งกัน) — ข้อพิสูจน์ข้อ 3 ในตั๋ว ต้องไม่พังหลังเปลี่ยนข้อความ
    เชิญไม่สำเร็จจาก 403 เป็น 409 (403 เหลือไว้แค่ "ไม่กรอกรหัสเชิญเลย" กับ "รหัสผูกอีเมลอื่น")
    """

    def _bootstrap_admin(self, email: str = "admin-000@example.com") -> dict:
        status, body, _ = self.signup(email)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["user"]["is_admin"])
        return body["user"]

    # ---------- ผู้ใช้คนแรก ----------

    def test_bug_012_first_user_becomes_admin_and_claims_bootstrap(self):
        self.assertEqual(self.store.count_users(), 0)
        user = self._bootstrap_admin("owner@example.com")
        self.assertEqual(self.store.count_users(), 1)
        self.assertEqual(self.store.settings.get(self.store.BOOTSTRAP_KEY), "owner@example.com")
        self.assertEqual(self.store.users["owner@example.com"]["id"], user["id"])

    def test_bug_012_second_signup_without_invite_is_403_and_creates_no_account(self):
        self._bootstrap_admin()
        status, body, _ = self.signup("nobody@example.com")
        self.assertEqual(status, 403)
        self.assertIn("error", body)
        self.assertNotIn("nobody@example.com", self.store.users)
        self.assertEqual(self.store.count_users(), 1)

    def test_bug_012_invite_bound_to_other_email_is_403_and_invite_not_consumed(self):
        self._bootstrap_admin()
        self.store.add_invite("inv-a", email="only-a@example.com")

        status, body, _ = self.signup("someone-else@example.com", invite="inv-a")
        self.assertEqual(status, 403)
        self.assertIn("error", body)
        self.assertFalse(self.store.invites["inv-a"]["used_at"])
        self.assertNotIn("someone-else@example.com", self.store.users)
        self.assertEqual(self.store.count_users(), 1)

        # รหัสเชิญยังใช้ได้จริงสำหรับเจ้าของอีเมลที่ถูกต้อง — พิสูจน์ว่าไม่ได้ถูกเผาทิ้งไปแล้ว
        status, body, _ = self.signup("only-a@example.com", invite="inv-a")
        self.assertEqual(status, 200, body)
        self.assertTrue(self.store.invites["inv-a"]["used_at"])

    def test_bug_012_missing_expired_used_invite_return_identical_409(self):
        self._bootstrap_admin()
        self.store.add_invite("expired-code", expired=True)
        self.store.add_invite("used-code", used_by="ghost-user-id")

        bodies = []
        for code in ("missing-code", "expired-code", "used-code"):
            with self.subTest(code=code):
                status, body, _ = self.signup(f"victim-{code}@example.com", invite=code)
                self.assertEqual(status, 409)
                bodies.append(body)

        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(bodies[1], bodies[2])
        # ไม่มีบัญชีใหม่เกิดขึ้นจากทั้งสามความล้มเหลว
        self.assertEqual(self.store.count_users(), 1)

    def test_bug_012_reused_invite_after_success_is_409_and_no_second_account(self):
        self._bootstrap_admin()
        self.store.add_invite("one-shot", email=None)

        status, body, _ = self.signup("first-user@example.com", invite="one-shot")
        self.assertEqual(status, 200, body)

        status, body, _ = self.signup("second-user@example.com", invite="one-shot")
        self.assertEqual(status, 409)
        self.assertNotIn("second-user@example.com", self.store.users)
        self.assertEqual(self.store.count_users(), 2)

    def test_bug_012_existing_email_has_password_is_409_and_invite_not_consumed(self):
        self._bootstrap_admin("admin@example.com")
        self.store.add_invite("dup", email=None)

        status, body, _ = self.signup("admin@example.com", invite="dup")
        self.assertEqual(status, 409)
        self.assertIn("error", body)
        # has_password ตัดก่อนถึง claim_invite เลย — รหัสเชิญยังไม่ถูกแตะ
        self.assertFalse(self.store.invites["dup"]["used_at"])

    def test_bug_012_successful_signup_attaches_invite_to_new_user(self):
        self._bootstrap_admin()
        self.store.add_invite("attach-me", email=None)

        status, body, _ = self.signup("new-guy@example.com", invite="attach-me")
        self.assertEqual(status, 200, body)

        inv = self.store.invites["attach-me"]
        self.assertTrue(inv["used_at"])
        self.assertEqual(inv["used_by"], body["user"]["id"])
        self.assertEqual(self.store.users["new-guy@example.com"]["id"], body["user"]["id"])


class SignupConcurrencyCase(AuthCase):
    """ข้อพิสูจน์ข้อ 1-2 ในตั๋ว — concurrency ไม่ใช่ sequence."""

    N = 8

    def _fire_concurrent(self, requests: list[tuple[str, str]]) -> list[tuple[int, dict]]:
        """ยิง signup ของทุกรายการใน requests พร้อมกันจริงด้วย threading.Barrier ฝั่งไคลเอนต์
        คืน list (status, body) เรียงตามลำดับเดียวกับ requests (ไม่ใช่ลำดับที่ตอบกลับมา)
        """
        n = len(requests)
        start = threading.Barrier(n, timeout=5)
        results: list[tuple[int, dict] | None] = [None] * n

        def worker(i: int, email: str, invite: str) -> None:
            start.wait()
            status, body, _ = self.signup(email, invite=invite)
            results[i] = (status, body)

        threads = [threading.Thread(target=worker, args=(i, e, inv))
                  for i, (e, inv) in enumerate(requests)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
            self.assertFalse(t.is_alive(), "เธรดค้าง — sync_barrier ไม่ครบจำนวนเธรด?")
        return results  # type: ignore[return-value]

    def test_bug_012_concurrent_signups_one_invite_exactly_one_account(self):
        # ทำให้ first_run=False ก่อน ไม่งั้นจะไปแข่งเส้นทางแอดมินคนแรกแทนเส้นทางรหัสเชิญ
        status, admin_body, _ = self.signup("admin-000@example.com")
        self.assertEqual(status, 200, admin_body)

        self.store.add_invite("race-code", email=None)
        self.store.sync_barrier = threading.Barrier(self.N, timeout=5)

        emails = [f"racer{i}@example.com" for i in range(self.N)]
        results = self._fire_concurrent([(e, "race-code") for e in emails])
        statuses = [s for s, _ in results]

        self.assertEqual(statuses.count(200), 1, statuses)
        self.assertEqual(statuses.count(409), self.N - 1, statuses)
        # admin คนเดียว + ผู้ชนะการแข่ง 1 คน = 2 (ไม่ใช่ 1 + N ที่เคยเป็นบั๊ก)
        self.assertEqual(self.store.count_users(), 2)

        winner_email = next(e for e, (s, _) in zip(emails, results) if s == 200)
        self.assertIn(winner_email, self.store.users)
        inv = self.store.invites["race-code"]
        self.assertTrue(inv["used_at"])
        self.assertEqual(inv["used_by"], self.store.users[winner_email]["id"])

    def test_bug_012_concurrent_signups_empty_system_exactly_one_admin(self):
        self.assertEqual(self.store.count_users(), 0)
        self.store.sync_barrier = threading.Barrier(self.N, timeout=5)

        emails = [f"founder{i}@example.com" for i in range(self.N)]
        results = self._fire_concurrent([(e, "") for e in emails])
        statuses = [s for s, _ in results]

        self.assertEqual(statuses.count(200), 1, statuses)
        self.assertEqual(statuses.count(409), self.N - 1, statuses)
        self.assertEqual(self.store.count_users(), 1)

        admins = [u for u in self.store.users.values() if u["is_admin"]]
        self.assertEqual(len(admins), 1, self.store.users)
        self.assertEqual(self.store.settings.get(self.store.BOOTSTRAP_KEY),
                          admins[0]["email"])


class TestPgstoreClaimSqlShape(unittest.TestCase):
    """ไม่ต่อ DB จริง — ปลอม db.connect() แล้วตรวจแค่รูป SQL/พารามิเตอร์ของจุดตัดสิน BUG-012
    (claim_invite/attach_invite/claim_first_admin, pgstore.py:189-264) เทียบกับที่ตั๋วกำหนด
    ไว้ทุกเงื่อนไข (มีจริง/ยังไม่ถูกจอง/ยังไม่หมดอายุ/อีเมลตรง สำหรับรหัสเชิญ, และ unique-key
    latch ของ settings สำหรับแอดมินคนแรก) — จับบั๊กเรื่องลำดับ/จำนวนพารามิเตอร์และ WHERE ที่
    หลุดเงื่อนไขไปโดยไม่ต้องมี Postgres จริง (รูปแบบเดียวกับ TestPgstoreJobActiveSqlShape ในตั๋ว P0)
    """

    def _capture(self, fn, *args):
        calls: list[tuple] = []

        class FakeCursor:
            def fetchone(self):
                return None

        class FakeConn:
            def execute(self, sql, params):
                calls.append((sql, params))
                return FakeCursor()

        @contextmanager
        def fake_connect():
            yield FakeConn()

        with mock.patch.object(pgstore.db, "connect", fake_connect):
            fn(*args)
        self.assertEqual(len(calls), 1, calls)
        return calls[0]

    def test_claim_invite_where_covers_all_four_conditions(self):
        sql, params = self._capture(pgstore.claim_invite, "code123", "Foo@Example.com")
        self.assertEqual(sql.count("%s"), 2)
        self.assertEqual(len(params), 2)
        self.assertEqual(params[0], pgstore._hash("code123"))
        self.assertEqual(params[1], "foo@example.com")  # ต้อง lower-case ก่อนเทียบใน where
        for fragment in ("used_by is null", "used_at is null",
                        "expires_at is null or expires_at > now()",
                        "invites.email is null or invites.email = %s",
                        "returning code_hash"):
            self.assertIn(fragment, sql)

    def test_attach_invite_requires_already_claimed_not_yet_owned(self):
        sql, params = self._capture(pgstore.attach_invite, "code123", "user-9")
        self.assertEqual(sql.count("%s"), 2)
        self.assertEqual(params, ("user-9", pgstore._hash("code123")))
        self.assertIn("used_by is null", sql)
        self.assertIn("used_at is not null", sql)

    def test_claim_first_admin_latches_settings_key_idempotent_per_email(self):
        sql, params = self._capture(pgstore.claim_first_admin, "Owner@Example.com")
        self.assertEqual(sql.count("%s"), 2)
        self.assertEqual(params[0], pgstore.BOOTSTRAP_KEY)
        self.assertIn("owner@example.com", params[1])  # json.dumps ของอีเมล lower-cased
        self.assertIn("on conflict (key) do update", sql)
        self.assertIn("settings.value = excluded.value", sql)
        self.assertIn("returning key", sql)


if __name__ == "__main__":
    unittest.main()
