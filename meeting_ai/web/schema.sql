-- โครงฐานข้อมูลของ meeting_ai (Postgres)
--
-- อยู่ใน schema ของตัวเองชื่อ meeting_ai เพื่อไม่ชนกับตารางอื่นในฐานเดียวกัน
-- ทุก query อ้างชื่อแบบเต็ม (meeting_ai.xxx) ไม่พึ่ง search_path
-- เพราะ connection pooler ของ Neon เป็น transaction mode ซึ่งไม่การันตี session-level SET
--
-- รันซ้ำได้ (idempotent) — ใช้เป็น migration ตัวเดียวไปก่อน

create schema if not exists meeting_ai;

-- ---------- ผู้ใช้ ----------

create table if not exists meeting_ai.users (
    id          uuid primary key default gen_random_uuid(),
    email       text not null unique,
    name        text,
    is_admin    boolean not null default false,
    created_at  timestamptz not null default now()
);

-- รหัสผ่าน: เก็บเป็น scrypt hash + salt (ไม่มีทางถอดกลับ) — เพิ่มแยกให้ upgrade ฐานเก่าได้
alter table meeting_ai.users add column if not exists password_hash bytea;
alter table meeting_ai.users add column if not exists password_salt bytea;

-- session ของเบราว์เซอร์ — เก็บเฉพาะ hash ของ token ไม่เก็บตัว token
create table if not exists meeting_ai.sessions (
    token_hash  bytea primary key,
    user_id     uuid not null references meeting_ai.users(id) on delete cascade,
    user_agent  text,
    expires_at  timestamptz not null,
    created_at  timestamptz not null default now()
);

create index if not exists sessions_user_idx on meeting_ai.sessions (user_id);
create index if not exists sessions_expiry_idx on meeting_ai.sessions (expires_at);

-- สมัครได้เฉพาะคนที่มีรหัสเชิญ (เครื่องมือของทีม ไม่ได้เปิดให้ใครก็สมัคร)
create table if not exists meeting_ai.invites (
    code_hash   bytea primary key,
    email       text,
    created_by  uuid references meeting_ai.users(id) on delete set null,
    used_by     uuid references meeting_ai.users(id) on delete set null,
    used_at     timestamptz,
    expires_at  timestamptz,
    created_at  timestamptz not null default now()
);

-- ---------- การประชุม ----------

create table if not exists meeting_ai.meetings (
    id                text primary key,
    owner_id          uuid references meeting_ai.users(id) on delete set null,
    title             text not null,
    -- private = เจ้าของคนเดียว, team = ทุกคนที่ล็อกอินในฐานนี้เห็น
    visibility        text not null default 'private',
    language          text,
    duration          double precision not null default 0,
    segment_count     integer not null default 0,
    source            text not null default 'upload',
    template          text not null default 'general',
    speakers          text[] not null default '{}',
    summary           text not null default '',
    summary_error     text,
    edited            boolean not null default false,
    transcript_edited boolean not null default false,
    segments          jsonb not null default '[]'::jsonb,
    translations      jsonb not null default '{}'::jsonb,
    -- key ของไฟล์เสียงในที่เก็บ (ดิสก์ในเครื่อง หรือ blob บน cloud)
    audio_key         text,
    -- title + สรุป + บทถอดเสียง รวมไว้ให้ค้นด้วย ILIKE ได้ทีเดียว
    -- (ภาษาไทยไม่มีช่องว่างระหว่างคำ full-text search จะพลาดมากกว่า substring)
    search_text       text not null default '',
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);

create index if not exists meetings_owner_idx on meeting_ai.meetings (owner_id, created_at desc);
create index if not exists meetings_created_idx on meeting_ai.meetings (created_at desc);

-- ---------- ลิงก์แชร์ ----------

create table if not exists meeting_ai.shares (
    token_hash  bytea primary key,
    meeting_id  text not null references meeting_ai.meetings(id) on delete cascade,
    created_by  uuid references meeting_ai.users(id) on delete set null,
    can_edit    boolean not null default false,
    expires_at  timestamptz,
    created_at  timestamptz not null default now()
);

create index if not exists shares_meeting_idx on meeting_ai.shares (meeting_id);

-- ---------- คิวงาน ----------

-- worker หยิบงานด้วย UPDATE ... RETURNING แบบ atomic (ดู db.claim_job)
create table if not exists meeting_ai.jobs (
    id          text primary key,
    meeting_id  text,
    kind        text not null,
    status      text not null default 'queued',
    step        text not null default 'รอคิว',
    progress    double precision not null default 0,
    title       text not null default '',
    error       text,
    warning     text,
    -- ทุกอย่างที่ runner ต้องรู้ รวมชื่อแทร็กและ key ของไฟล์เสียง
    spec        jsonb not null default '{}'::jsonb,
    attempts    integer not null default 0,
    claimed_at  timestamptz,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create index if not exists jobs_queue_idx on meeting_ai.jobs (status, created_at);

-- เครื่องไหน claim งานนี้ไป (เพิ่มแยกเพื่อให้ upgrade ฐานเก่าได้)
alter table meeting_ai.jobs add column if not exists worker text;

-- ---------- เครื่องประมวลผล ----------

-- worker ส่ง heartbeat มาเรื่อยๆ แม้ตอนว่าง จะได้รู้ว่ามีเครื่องไหนพร้อมอยู่
-- และรู้ว่าเครื่องไหนเงียบหายไป (เทียบ last_seen กับเวลาปัจจุบัน)
create table if not exists meeting_ai.workers (
    name        text primary key,
    status      text not null default 'idle',   -- idle | busy
    job_id      text,
    jobs_done   integer not null default 0,
    gpu         text,
    last_seen   timestamptz not null default now(),
    started_at  timestamptz not null default now()
);

create index if not exists workers_seen_idx on meeting_ai.workers (last_seen desc);

-- worker ทำอะไรได้บ้าง (ถอดเสียงในเครื่องได้ไหม / มีคีย์ API ไหม / รุ่นโมเดล)
-- สำคัญเพราะฝั่ง cloud ไม่มี whisper เอง จะบอกผู้ใช้ว่าเลือกอะไรได้ต้องดูจาก worker
alter table meeting_ai.workers add column if not exists caps jsonb not null default '{}'::jsonb;
