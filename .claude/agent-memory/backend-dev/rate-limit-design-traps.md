---
name: rate-limit-design-traps
description: Four ways a rate limiter silently limits nothing — success-clears-quota, trusting a proxy header the proxy does not set, regex IP parsing plus /128 IPv6 keys, and an LRU that evicts the key it just counted
metadata:
  type: feedback
---

ก่อนบอกว่า "ใส่ rate limit แล้ว" ต้องพิสูจน์สี่ข้อนี้ ไม่งั้นมันไม่ได้จำกัดอะไรเลยแต่ backlog ขึ้นว่าปิดแล้ว

1. **ล้างโควตาเมื่อสำเร็จ = ไม่มีเพดาน** ใครมีบัญชีจริงใบเดียวสลับ "เดา N-1 ครั้ง + ล็อกอินตัวเอง"
   ได้ไม่จำกัด ต้องมีถังที่สองที่ความสำเร็จ **ไม่** ล้าง (เช่น 60/ชั่วโมง/IP) ถังแคบไว้กันเดารหัส
   ถังแข็งไว้กันค่า CPU
2. **หัวข้อ forwarded เชื่อได้เฉพาะหัวข้อที่ proxy ข้างหน้าเขียนทับจริง** nginx/Cloudflare ส่งหัวข้อ
   ที่ไม่รู้จักผ่านไปตรงๆ ดังนั้น `X-Vercel-Forwarded-For` เชื่อได้เฉพาะบน Vercel → ทำเป็น class
   attribute ที่ subclass ของแต่ละ deployment ประกาศเอง ไม่ใช่ลิสต์รวมใน Handler กลาง
3. **อย่าแปลง IP ด้วย regex** `ipaddress.ip_address()` เท่านั้น: regex ปล่อย `127.0.0.1:8080` ผ่าน
   (Azure App Gateway / IIS ARR ต่อพอร์ตมาจริง) = ทุกการเชื่อมต่อได้ถังของตัวเอง และต้องนับ IPv6
   เป็น **/64** ไม่ใช่ /128 (ผู้ใช้หนึ่งรายได้ทั้งบล็อกมาฟรี — ใช้เลี่ยงได้บน Vercel โดยไม่ต้องปลอมหัวข้อ)
4. **ตัวตัดแต่งแคชต้องไม่ทิ้งคีย์ที่กำลังนับอยู่** เรียงตามจำนวนครั้งแล้วตัดท้าย + `sorted` เสถียร =
   คีย์ที่เพิ่งใส่ (count=1) ถูกทิ้งในการเรียกเดียวกัน พอคีย์เต็มเพดาน ตัวนับหยุดนับทั้งชั้นเงียบๆ
   ตัดแต่งก่อนใส่ ห้ามแตะ key ปัจจุบัน และทิ้งรายการที่ยังไม่ถูกบล็อกก่อน

**Why:** ทั้งสี่ข้อเป็น BLOCKING ในรีวิว BUG-010 รอบที่ 1 ทุกข้อรีวิวเวอร์ทำซ้ำได้จริง และทุกข้อ
"เทสต์สถานะ 429 ผ่านหมด" — ต้องวัดด้วยจำนวนครั้งที่ไปถึงงานแพง (`verify_password`) และจำนวนคีย์
ที่ถูกสร้าง ไม่ใช่สถานะ HTTP

**How to apply:** ใช้เป็นเช็กลิสต์ทุกครั้งที่แตะ `server._rate_limited` / `web/ratelimit.py`
และคู่กับ [[http-server-early-reject]] (ตอบก่อนอ่าน body) กับ [[deploy-shape-matters-serverless]]
(ตัวนับต้องอยู่ใน Postgres) · fail-open ต้องส่งเสียงหนึ่งครั้ง ไม่ใช่เงียบ
