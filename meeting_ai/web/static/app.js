/* meeting_ai web UI — vanilla JS ไม่มี dependency */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/* การถอดเสียงสด: ตัดคลิปตอน "เงียบ" ไม่ใช่ตามเวลาตายตัว
   ตัดกลางคำทำให้คำนั้นเพี้ยนทั้งสองชิ้น รอจังหวะที่ไม่มีใครพูดจะได้รอยต่อที่สะอาด
   และเล็งความยาวใกล้ 30 วิ ซึ่งเป็นหน้าต่างที่ whisper ถูกเทรนมา */
const LIVE_MIN_MS = 12000;      // สั้นกว่านี้ไม่ตัด แม้จะเงียบ
const LIVE_MAX_MS = 28000;      // ถ้าไม่เงียบเลยก็ตัดที่นี่
const LIVE_QUIET_LEVEL = 0.012; // ระดับที่ถือว่าเงียบ
const LIVE_QUIET_HOLD = 260;    // ต้องเงียบต่อเนื่องกี่ ms ถึงถือว่าจบประโยค
const LIVE_PROMPT_CHARS = 300;  // ส่งท้ายข้อความเดิมไปเป็นบริบทเท่านี้

const state = {
  meetings: [],
  jobs: [],
  current: null,       // id ของการประชุมที่เปิดอยู่
  meeting: null,       // ข้อมูลเต็มของการประชุมที่เปิดอยู่
  query: '',
  polling: null,
  pollMs: 0,         // จังหวะ poll ที่ใช้อยู่ — งานของเรา 1.5 วิ คิวระบบของแอดมิน 10 วิ
  config: {},
  workers: [],       // เครื่องประมวลผลที่รายงานตัวเข้ามา
  user: null,        // ผู้ใช้ที่ล็อกอิน (โหมด cloud)
  share: null,       // {meeting_id, can_edit} ถ้าเปิดมาจากลิงก์แชร์
  firstRun: false,   // ยังไม่มีผู้ใช้ในระบบ -> สมัครคนแรกได้เลย เป็นแอดมิน
};

/** โหมด cloud ที่ยังไม่ได้ล็อกอินและไม่ได้ถือลิงก์แชร์ = ต้องเข้าสู่ระบบก่อน */
const needsAuth = () => state.config.auth_required && !state.user && !state.share;
/** แก้ของได้ไหม — เจ้าของ/คนในทีม แก้ได้ คนถือลิงก์ต้องมี can_edit */
const canEdit = () => !state.config.auth_required || !!state.user
  || !!(state.share && state.share.can_edit);
const isAdmin = () => !!(state.user && state.user.is_admin);

/* ---------------- utils ---------------- */

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

function fmtDuration(sec) {
  sec = Math.round(sec || 0);
  if (!sec) return '—';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h) return `${h} ชม. ${m} นาที`;
  if (m) return `${m} นาที ${s} วิ`;
  return `${s} วิ`;
}

function fmtClock(sec) {
  const total = Math.floor(sec || 0);
  const h = Math.floor(total / 3600);
  const m = String(Math.floor((total % 3600) / 60)).padStart(2, '0');
  const s = String(total % 60).padStart(2, '0');
  return h ? `${h}:${m}:${s}` : `${m}:${s}`;
}

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString('th-TH', {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const ctype = res.headers.get('Content-Type') || '';
  const data = ctype.includes('json') ? await res.json() : await res.text();
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data;
}

const jsonPatch = (body) => ({
  method: 'PATCH',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

const jsonPost = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

/* tag = เหตุผลที่แบนเนอร์นี้ขึ้น ใช้ถอนมันทีหลังเมื่อเหตุผลนั้นหมดไป (ดู expireBanner) */
function banner(msg, tag = '') {
  const el = $('#banner');
  if (!msg) { el.hidden = true; el.dataset.tag = ''; return; }
  el.hidden = false;
  el.textContent = msg;
  el.dataset.tag = tag;
}

/* ข้อความขึ้นต้นของ step ที่ฝั่งเซิร์ฟเวอร์ส่งมาเมื่อบอทเข้าห้องได้แล้ว
   ต้องตรงกับ runner.py -> bot_job() -> tick() มีเทสต์ผูกสองฝั่งไว้ (BUG-072) */
const BOT_IN_ROOM = 'บอทอยู่ในห้อง';

/* แบนเนอร์ "ไปกด รับเข้าห้อง (Admit)" ถูกต้องตอนกดส่ง แต่พอบอทเข้าห้องได้แล้วมันกลายเป็น
   คำสั่งที่ขัดกับการ์ดงานในจอเดียวกันที่บอกว่า "บอทอยู่ในห้อง 03:02" — ผู้ใช้อ่านแล้วไม่รู้ว่า
   จะเชื่ออันไหน ถอนออกทันทีที่เหตุผลหมดไป */
function expireBanner() {
  if ($('#banner').dataset.tag !== 'bot-admit') return;
  if (state.jobs.some((j) => isMine(j) && (j.step || '').startsWith(BOT_IN_ROOM))) banner('');
}

/* ---------------- markdown ----------------
   เรนเดอร์เฉพาะ subset ที่ตัวสรุปของเราสร้าง: heading, bullet, ตาราง, bold, inline code
   เขียนเองเพื่อไม่ต้องพึ่ง library ภายนอก (โปรเจกต์นี้ไม่มี dependency)     */

function inline(text) {
  return esc(text)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

const splitRow = (line) => line.replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
const isSeparator = (line) => /^\|?[\s:-]*-[\s|:-]*\|?$/.test(line) && line.includes('-');

function renderMarkdown(src) {
  const lines = String(src || '').replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const level = Math.min(6, Math.max(2, heading[1].length));
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      i++;
      continue;
    }

    if (line.trim().startsWith('|') && isSeparator(lines[i + 1] || '')) {
      const head = splitRow(line.trim());
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        rows.push(splitRow(lines[i].trim()));
        i++;
      }
      out.push(
        '<div class="table-wrap"><table><thead><tr>'
        + head.map((c) => `<th>${inline(c)}</th>`).join('')
        + '</tr></thead><tbody>'
        + rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')
        + '</tbody></table></div>'
      );
      continue;
    }

    if (/^\s*[-*+]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*[-*+]\s+/, ''))}</li>`);
        i++;
      }
      out.push(`<ul>${items.join('')}</ul>`);
      continue;
    }

    const para = [];
    while (i < lines.length && lines[i].trim()
           && !/^(#{1,6}\s|\s*[-*+]\s)/.test(lines[i])
           && !lines[i].trim().startsWith('|')) {
      para.push(lines[i].trim());
      i++;
    }
    if (para.length) out.push(`<p>${inline(para.join(' '))}</p>`);
  }
  return out.join('\n');
}

/* ---------------- sidebar ---------------- */

function renderJobs() {
  $('#jobs').innerHTML = state.jobs.map((j) => {
    const pct = Math.round((j.progress || 0) * 100);
    const failed = j.status === 'error';
    // งานบอทที่ยังอยู่ในห้อง ต้องมีทางสั่งให้ออกมาสรุป ไม่ใช่รอครบเวลาเท่านั้น
    const canStop = j.kind === 'bot' && !failed && j.status !== 'done';
    // ช่วงที่บอทนั่งอยู่ในห้องกินเวลาเกือบทั้งงาน แต่แถบขยับแค่ 2%→35%
    // ตามสัดส่วนเวลาที่ตั้งเพดานไว้ (1% ต่อ 3 นาที) ซึ่งอ่านว่า 'ค้าง'
    // ช่วงนี้จึงโชว์เป็นแถบวิ่งแทน แล้วให้ข้อความบอกเวลาที่อยู่ในห้องเป็นตัวชี้
    const inRoom = j.kind === 'bot' && j.status === 'running' && (j.progress || 0) < 0.35;
    return `<div class="job ${failed ? 'err' : ''}">
      <div class="jt">${j.kind === 'bot' ? '🤖 ' : ''}${esc(j.title)}${
        isMine(j) ? '' : '<span class="job-sys">คิวระบบ</span>'}</div>
      <div class="js">${esc(failed ? j.error : j.step)}</div>
      ${failed ? '' : inRoom
        ? '<div class="bar rec"><div></div></div>'
        : `<div class="bar"><div style="width:${pct}%"></div></div>`}
      ${canStop ? `<button class="btn btn-sm job-stop" data-job="${esc(j.id)}"
        type="button">ให้บอทออกจากห้องแล้วสรุป</button>` : ''}
    </div>`;
  }).join('');
  $$('#jobs .job-stop').forEach((b) => {
    b.onclick = () => { b.disabled = true; stopJob(b.dataset.job); };
  });
}

/** งาน summarize/translate/process ที่กำลังวิ่งอยู่ของการประชุมที่เปิดอยู่ (ถ้ามี).
    ใช้ job.id เทียบกับ id การประชุมแทน job.meeting_id เพราะฝั่งเซิร์ฟเวอร์ (jobs.py)
    ปล่อย meeting_id เป็น None จนกว่างานจะ done ในโหมดไฟล์ — แต่ job id ของ
    process/summarize คือ mid ตรงๆ, translate คือ `${mid}.tr.<lang>` และสรุปเป็นภาษาอื่น
    คือ `${mid}.sum.<lang>` (ยืนยันจาก web/jobs.py: submit_summarize / submit_translate) */
function jobForMeeting(id) {
  if (!id) return null;
  return state.jobs.find((j) => (j.status === 'running' || j.status === 'queued')
    && (j.id === id || j.id.startsWith(`${id}.tr.`) || j.id.startsWith(`${id}.sum.`))) || null;
}

/** ป้าย "กำลังสรุปด้วย AI" ข้าง h3 สรุป — ผูกกับ pollJobs() เดิม ไม่มี transport ใหม่ */
function updateStreamingIndicator() {
  const badge = $('#d-ai-live');
  if (!badge) return;
  badge.hidden = !jobForMeeting(state.current);
}

function fmtAgo(sec) {
  if (sec === null || sec === undefined) return '';
  if (sec < 60) return `${sec} วิที่แล้ว`;
  if (sec < 3600) return `${Math.floor(sec / 60)} นาทีที่แล้ว`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} ชม.ที่แล้ว`;
  return `${Math.floor(sec / 86400)} วันที่แล้ว`;
}

const CAP_LABELS = { local: 'whisper ในเครื่อง', api: 'API', diarize: 'แยกผู้พูด',
                     bot: 'ส่งบอท' };

/** เครื่องนี้ทำอะไรได้ — worker รุ่นเก่ายังไม่ส่ง caps มา จะไม่แสดงบรรทัดนี้ */
function workerCan(w) {
  const can = w.can;
  if (!can) return '';
  const has = can.map((k) => CAP_LABELS[k] || k);
  const lacks = Object.keys(CAP_LABELS).filter((k) => !can.includes(k)).map((k) => CAP_LABELS[k]);
  return (has.length ? `ทำได้: ${esc(has.join(', '))}` : 'ยังทำอะไรไม่ได้')
    + (lacks.length ? ` · ขาด: ${esc(lacks.join(', '))}` : '');
}

/* ชิปสถานะของมือถือ — แทนกล่องรายชื่อเครื่องยาว ๆ ที่เคยกินพื้นที่บนสุดของหน้าแรก
   แตะแล้วเข้าหน้า #devices ที่มีรายละเอียดเต็ม */
function renderDeviceChip(ws) {
  const chip = $('#m-devices');
  if (!chip) return;
  const alive = ws.filter((w) => w.alive).length;
  chip.hidden = !ws.length;
  chip.dataset.state = alive ? 'ok' : 'down';
  $('#m-chip-text').textContent = alive
    ? `${alive} เครื่องพร้อม`
    : 'ไม่มีเครื่องประมวลผลออนไลน์';
}

function renderWorkers() {
  const el = $('#workers');
  const ws = state.workers || [];
  renderDeviceChip(state.config.auth_required ? ws : []);
  if (!state.config.auth_required || !ws.length) { el.hidden = true; return; }
  el.hidden = false;

  // เรียงตามสถานะแล้วคั่นหัวข้อกลุ่ม — เจ้าของเครื่องมี worker 4 ตัวที่ออฟไลน์ 3
  // ถ้าไม่จัดกลุ่มจะต้องอ่านทีละบรรทัดว่าตัวไหนยังใช้ได้
  const rank = (w) => (w.status === 'busy' ? 0 : w.alive ? 1 : 2);
  let lastGroup = null;
  const rows = [...ws].sort((a, b) => rank(a) - rank(b)).map((w) => {
    const group = w.alive ? 'พร้อมใช้งาน' : 'ออฟไลน์';
    const head = group === lastGroup ? '' : `<div class="wk-group">${group}</div>`;
    lastGroup = group;
    const cls = w.status === 'busy' ? 'busy' : (w.alive ? 'idle' : 'gone');
    const label = { busy: 'กำลังทำงาน', idle: 'ว่าง', gone: 'หลุดไป' }[cls];
    // server ตัด job_title/job_id ออกจาก worker ที่ส่งให้ผู้ใช้ทั่วไป (ไม่ใช่แอดมิน) เพราะเดิม
    // ชื่องานคือชื่อการประชุมของทีมอื่น รั่วออกมาทาง poll ทุก 1.5 วิ (BACKLOG #2/#3) — สองฟิลด์นี้
    // จึงเป็น undefined เสมอสำหรับผู้ใช้ทั่วไป ต้องเช็ค falsy ก่อนใช้ทุกครั้ง ห้ามแสดงตรงๆ
    // (ตอนนี้ไม่มี element ไหนผูกกับ job_id เป็นลิงก์/คลิกเป้าหมาย ถ้าจะเพิ่มในอนาคตก็ต้อง
    // เช็ค w.job_id ก่อนเช่นกัน) เมื่อไม่มี job_title ให้ถอยไปแสดงแค่ "เห็นล่าสุด/เงียบไป" เหมือนเดิม
    const detail = w.status === 'busy' && w.job_title
      ? esc(w.job_title)
      : (w.alive ? `เห็นล่าสุด ${fmtAgo(w.quiet_for)}` : `เงียบไป ${fmtAgo(w.quiet_for)}`);
    return `${head}<div class="wk ${cls}">
      <span class="wk-dot"></span>
      <div class="wk-body">
        <span class="wk-name">${esc(w.name)}</span>
        <span class="wk-sub">${label} · ${detail}</span>
        <span class="wk-sub">${w.gpu ? esc(w.gpu) + ' · ' : ''}ทำเสร็จ ${w.jobs_done} งาน</span>
        ${w.alive ? `<span class="wk-sub">${workerCan(w)}</span>` : ''}
      </div>
    </div>`;
  }).join('');

  const anyAlive = ws.some((w) => w.alive);
  const warn = anyAlive ? '' :
    '<p class="wk-warn">ไม่มีเครื่องประมวลผลออนไลน์ — งานจะค้างในคิวจนกว่าจะเปิด worker</p>';
  el.innerHTML = `<div class="wk-head">เครื่องประมวลผล</div>${rows}${warn}`;
}

function renderList() {
  const el = $('#list');
  // หัวข้อ "ล่าสุด" (จอแคบ) ไม่ควรลอยอยู่เหนือข้อความว่าง ๆ ว่ายังไม่มีการประชุม
  const recent = $('#m-recent');
  if (recent) recent.hidden = !state.meetings.length;
  if (!state.meetings.length) {
    el.innerHTML = `<li class="empty">${state.query ? 'ไม่พบการประชุมที่ตรงกับคำค้น' : 'ยังไม่มีการประชุม — กด “+ ประชุมใหม่”'}</li>`;
    return;
  }
  el.innerHTML = state.meetings.map((m) => {
    const bits = [fmtDate(m.created), fmtDuration(m.duration)];
    if (m.speakers && m.speakers.length) bits.push(`${m.speakers.length} คนพูด`);
    return `<li data-id="${esc(m.id)}" class="${m.id === state.current ? 'active' : ''}">
      <span class="t">${esc(m.title)}</span>
      <span class="s">${esc(bits.join(' · '))}</span>
      ${m.snippet ? `<span class="snip">${esc(m.snippet)}</span>` : ''}
    </li>`;
  }).join('');
}

async function refresh() {
  const q = state.query ? `?q=${encodeURIComponent(state.query)}` : '';
  const data = await api(`/api/meetings${q}`);
  state.meetings = data.meetings || [];
  state.jobs = data.jobs || [];
  renderList();
  renderJobs();
  refreshWorkers();
  ensurePolling();
}

async function refreshWorkers() {
  if (!state.config.auth_required || !state.user) return;
  try {
    const out = await api('/api/workers');
    state.workers = out.workers || [];
    renderWorkers();
  } catch (e) { /* ไม่สำคัญพอจะรบกวน */ }
}

async function refreshConfig() {
  try {
    state.config = await api('/api/config');
    state.user = state.config.user || null;
    const s = state.config.stats || {};
    $('#stats').textContent = `${s.count || 0} การประชุม · รวม ${fmtDuration(s.total_duration)}`;
    if (state.config.auth_required) {
      const me = await api('/api/auth/me').catch(() => ({}));
      state.user = me.user || state.user;
      state.share = me.share || null;
      state.firstRun = !!me.first_run;
    }
    renderUserBox();
    if (!state.config.llm_ready) {
      banner('ยังไม่ได้ตั้ง LLM_API_KEY — ถอดเสียงได้ แต่จะสรุปไม่ได้');
    }
  } catch (e) { /* ไม่สำคัญพอจะรบกวนผู้ใช้ */ }
}

// สวิตช์อัดสดจากเบราว์เซอร์ (แอดมินคุม) — ค่าเริ่มต้นเปิด
const liveOn = () => state.config.live_recording_enabled !== false;
const liveToggleLabel = () => (liveOn() ? '🟢 อัดสด: เปิด' : '🔴 อัดสด: ปิด');

async function toggleLiveRecording() {
  const next = !liveOn();
  try {
    await api('/api/settings', jsonPost({ live_recording_enabled: next }));
    await refreshConfig();               // อัปเดต state.config + วาดปุ่มใหม่
    const card = $('#rec-card');
    if (card) card.hidden = !next;       // ซ่อน/โชว์การ์ดทันทีถ้าอยู่หน้าประชุมใหม่
    banner(next ? 'เปิดให้ผู้ใช้อัดสดจากเบราว์เซอร์แล้ว' : 'ปิดการอัดสดจากเบราว์เซอร์แล้ว');
  } catch (e) {
    banner('ปรับตั้งค่าไม่สำเร็จ: ' + (e.message || e));
  }
}

/* แผ่นบัญชีของจอแคบ — แพตเทิร์นเดียวกับ action sheet ของหน้ารายละเอียด:
   ไม่ได้ทำปุ่มชุดใหม่ ให้ CSS ย้าย #userbox เดิมลงมาเป็นแผ่นล่างจอ ปุ่มทุกปุ่มจึงยังเป็นของเดิม */
/* .topbar เป็น position: sticky + z-index: 10 ซึ่ง "สร้าง stacking context" —
   z-index ของลูกทุกตัวถูกตีความข้างในบริบทนั้น ไม่ใช่เทียบกับทั้งหน้า แผ่นบัญชีที่ตั้ง
   z-index: 60 จึงไม่ได้อยู่เหนือม่านที่ 55 จริง ทั้ง topbar (z 10) อยู่ใต้ม่านทั้งก้อน
   ผลคือกดปุ่มไม่โดน (elementFromPoint ได้ #sheet-scrim) แล้วแผ่นปิดทันทีเพราะโดนม่านแทน
   ย้าย #userbox ออกมาไว้ใต้ body ตอนเปิด แล้วคืนที่เดิมตอนปิด — ตัวปุ่มเป็นก้อนเดิม
   ตัวจัดการเหตุการณ์ที่ผูกไว้จึงติดไปด้วย ไม่ต้องผูกใหม่ */
let _userboxHome = null;

function openAccountSheet() {
  const box = $('#userbox');
  if (!box) return;
  if (!_userboxHome) _userboxHome = { parent: box.parentElement, next: box.nextSibling };
  document.body.appendChild(box);
  document.body.classList.add('account-open');
  $('#sheet-scrim').hidden = false;
}

function closeAccountSheet() {
  const box = $('#userbox');
  if (box && _userboxHome && box.parentElement === document.body) {
    _userboxHome.parent.insertBefore(box, _userboxHome.next);
  }
  document.body.classList.remove('account-open');
  if (!document.body.classList.contains('sheet-open')) $('#sheet-scrim').hidden = true;
}

function renderUserBox() {
  const box = $('#userbox');
  const acct = $('#btn-account');
  if (!state.config.auth_required) { box.hidden = true; acct.hidden = true; return; }
  box.hidden = false;
  // ตัวอักษรแรกของชื่อ/อีเมล — ภาษาไทยก็ใช้ได้ ไม่ต้อง uppercase (ไทยไม่มีตัวพิมพ์ใหญ่)
  const label = (state.user && (state.user.name || state.user.email)) || '';
  acct.hidden = !label;
  acct.textContent = label.slice(0, 1).toUpperCase();
  if (state.user) {
    box.innerHTML = `<span class="who">${esc(state.user.name || state.user.email)}</span>`
      + (isAdmin() ? `<button id="btn-live-toggle" class="btn btn-sm" type="button">${liveToggleLabel()}</button>` : '')
      + (isAdmin() ? '<button id="btn-invite" class="btn btn-sm" type="button">เชิญสมาชิก</button>' : '')
      + '<button id="btn-logout" class="btn btn-sm" type="button">ออกจากระบบ</button>';
    $('#btn-logout').onclick = async () => {
      await api('/api/auth/logout', { method: 'POST' }).catch(() => {});
      location.href = '/';
    };
    const inv = $('#btn-invite');
    if (inv) inv.onclick = inviteMember;
    const lt = $('#btn-live-toggle');
    if (lt) lt.onclick = toggleLiveRecording;
  } else if (state.share) {
    box.innerHTML = '<span class="who">เปิดจากลิงก์แชร์'
      + (state.share.can_edit ? ' (แก้ได้)' : ' (อ่านอย่างเดียว)') + '</span>';
  } else {
    box.innerHTML = '';
  }
  $('#btn-new').hidden = !canEdit() || !!state.share;
  $('#m-new').hidden = $('#btn-new').hidden;   // ปุ่มเดียวกันคนละจอ ต้องซ่อนพร้อมกัน
  // คนถือลิงก์แชร์เห็นได้อันเดียว สถิติรวมกับช่องค้นหาจึงไม่มีความหมาย
  const shareOnly = !!state.share && !state.user;
  $('#stats').hidden = shareOnly;
  $('.search-wrap').hidden = shareOnly;
}

async function inviteMember() {
  const email = prompt('เชิญอีเมลไหน? (เว้นว่าง = ใครก็ใช้รหัสนี้ได้)', '');
  if (email === null) return;
  try {
    const out = await api('/api/auth/invite', jsonPost({ email: email.trim() || null }));
    const link = `${location.origin}/?invite=${encodeURIComponent(out.code)}`;
    await copyText(out.code);
    banner(`รหัสเชิญ (คัดลอกให้แล้ว): ${out.code}${out.email ? ' — สำหรับ ' + out.email : ''}`);
    console.log('ลิงก์สมัคร:', link);
  } catch (e) { banner(`สร้างรหัสเชิญไม่สำเร็จ: ${e.message}`); }
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    return false;   // เบราว์เซอร์ไม่ให้ (ต้อง https) — ผู้ใช้ค่อยเลือกเองจากช่อง
  }
}

/* ---------------- หน้าเข้าสู่ระบบ ---------------- */

/** โทเคนแชร์ที่ "รออยู่ใน URL" — /s/<token> ไม่ตั้งคุกกี้ให้แล้ว ต้องให้ผู้ใช้กดยืนยันเอง
 *  (BACKLOG #16) ถอดรหัสแบบเดียวกับฝั่งเซิร์ฟเวอร์: unquote แล้วตัด / หัวท้าย */
function pendingShareToken() {
  if (!location.pathname.startsWith('/s/')) return '';
  try {
    return decodeURIComponent(location.pathname.slice(3)).replace(/^\/+|\/+$/g, '');
  } catch (e) {
    return '';   // URL ที่ % ไม่ครบคู่ — ถือว่าไม่มีโทเคน เซิร์ฟเวอร์จะตอบ 404 อยู่แล้ว
  }
}

function showShareConfirm(token) {
  document.body.classList.add('auth-only');
  const panel = $('#panel');
  panel.innerHTML = '';
  panel.append($('#tpl-share-confirm').content.cloneNode(true));

  const err = $('#sc-error');
  $('#sc-open').onclick = async () => {
    err.hidden = true;
    $('#sc-open').disabled = true;
    try {
      // POST + Content-Type: application/json คือสิ่งที่เว็บอื่นสั่งเบราว์เซอร์เหยื่อทำแทนไม่ได้
      await api('/api/auth/share', jsonPost({ token }));
      location.replace('/');   // โหลดใหม่ให้สถานะสะอาด และไม่เก็บโทเคนไว้ในประวัติ/Referer
    } catch (e) {
      $('#sc-open').disabled = false;
      err.hidden = false;
      err.textContent = e.message || 'เปิดลิงก์แชร์นี้ไม่ได้';
    }
  };
  $('#sc-cancel').onclick = () => location.replace('/');
}

function showAuth(mode) {
  // ครั้งแรกของระบบยังไม่มีใคร ต้องสมัครก่อน
  const signup = mode === 'signup' || (mode === undefined && state.firstRun);
  document.body.classList.add('auth-only');
  const panel = $('#panel');
  panel.innerHTML = '';
  panel.append($('#tpl-auth').content.cloneNode(true));

  const first = state.firstRun;
  $('#a-title').textContent = signup ? (first ? 'สร้างบัญชีแรก' : 'สมัครสมาชิก') : 'เข้าสู่ระบบ';
  $('#a-note').textContent = signup
    ? (first ? 'ยังไม่มีใครในระบบ — บัญชีแรกจะเป็นแอดมินและเชิญคนอื่นได้'
             : 'ต้องมีรหัสเชิญจากแอดมินของทีม')
    : '';
  $('#a-name-wrap').hidden = !signup;
  $('#a-invite-wrap').hidden = !signup || first;
  $('#a-password').autocomplete = signup ? 'new-password' : 'current-password';
  $('#a-submit').textContent = signup ? 'สมัครและเข้าใช้งาน' : 'เข้าสู่ระบบ';
  $('#a-switch-text').textContent = signup ? 'มีบัญชีอยู่แล้ว?' : 'ได้รับรหัสเชิญมา?';
  $('#a-switch').textContent = signup ? 'เข้าสู่ระบบ' : 'สมัครสมาชิก';
  // บัญชีแรกยังไม่มีอะไรให้สลับไป ซ่อนทั้งบรรทัดไม่ให้เหลือข้อความค้าง
  $('.auth-switch').hidden = first;
  $('#a-switch').onclick = () => showAuth(signup ? 'login' : 'signup');

  // รหัสเชิญมาทาง ?invite= ก็เติมให้เลย
  const fromUrl = new URLSearchParams(location.search).get('invite');
  if (fromUrl && signup) $('#a-invite').value = fromUrl;

  const fail = (msg) => {
    const el = $('#a-error');
    el.hidden = false;
    el.textContent = msg;
  };

  const submit = async () => {
    const email = $('#a-email').value.trim();
    const password = $('#a-password').value;
    if (!email || !password) return fail('กรอกอีเมลและรหัสผ่านให้ครบ');
    $('#a-error').hidden = true;
    $('#a-submit').disabled = true;
    try {
      const body = signup
        ? { email, password, name: $('#a-name').value.trim(), invite: $('#a-invite').value.trim() }
        : { email, password };
      await api(`/api/auth/${signup ? 'signup' : 'login'}`, jsonPost(body));
      location.href = '/';    // โหลดใหม่ทั้งหน้าให้สถานะสะอาด
    } catch (e) {
      fail(e.message);
      $('#a-submit').disabled = false;
    }
  };

  $('#a-submit').onclick = submit;
  for (const id of ['#a-email', '#a-password', '#a-invite', '#a-name']) {
    const el = $(id);
    if (el) el.onkeydown = (e) => { if (e.key === 'Enter') submit(); };
  }
  $('#a-email').focus();
}

/* ---------------- job polling ---------------- */

/* งานของฉัน — เซิร์ฟเวอร์ติดธงนี้ให้เฉพาะแอดมิน ซึ่งเห็นคิวของทั้งระบบ
   ไม่มีฟิลด์ = ของฉัน (คนทั่วไปเห็นเฉพาะงานตัวเองอยู่แล้ว และเซิร์ฟเวอร์รุ่นก่อนไม่ส่งมา) */
const isMine = (j) => j.mine !== false;

const POLL_MINE_MS = 1500;
const POLL_SYSTEM_MS = 10000;   // คิวระบบของแอดมิน: สดพอจะไล่ปัญหา ไม่ถี่จนเปลืองฟังก์ชัน

function ensurePolling() {
  const busy = state.jobs.filter((j) => j.status === 'queued' || j.status === 'running');
  // แอดมินเคยโดน poll ทุก 1.5 วิตลอดเวลาที่ "มีใครสักคนในระบบ" มีงานเดินอยู่
  const ms = busy.some(isMine) ? POLL_MINE_MS : busy.length ? POLL_SYSTEM_MS : 0;
  if (ms === state.pollMs) return;
  if (state.polling) { clearInterval(state.polling); state.polling = null; }
  state.pollMs = ms;
  if (ms) state.polling = setInterval(pollJobs, ms);
}

async function pollJobs() {
  let data;
  try { data = await api('/api/jobs'); } catch (e) { return; }

  // เฉพาะงานของเราเท่านั้นที่มีสิทธิ์เปลี่ยนหน้าจอ/ขึ้นแบนเนอร์ — งานของคนอื่นที่แอดมินเห็น
  // ในคิวระบบยังแสดงในรายการตามปกติ แต่ห้ามสั่ง openMeeting() ไปที่ประชุมที่เราเปิดไม่ได้
  // (เดิมเด้งแบนเนอร์ 403 ใส่หน้าจอแอดมินทุกครั้งที่งานของ tenant อื่นจบ — BACKLOG #41)
  const before = state.jobs.filter(
    (j) => (j.status === 'running' || j.status === 'queued') && isMine(j));
  state.jobs = data.jobs || [];
  renderJobs();
  expireBanner();
  updateStreamingIndicator();
  // ผ่าน renderWorkers() ตัวเดียวกับ refreshWorkers() เสมอ — การ์ด job_title/job_id ที่นั่นพอแล้ว
  if (data.workers) { state.workers = data.workers; renderWorkers(); }

  const stillActive = new Set(state.jobs.map((j) => j.id));
  const finished = before.filter((j) => !stillActive.has(j.id));
  if (finished.length) {
    await refresh();
    await refreshConfig();
    let opened = false;
    for (const old of finished) {
      const job = await api(`/api/jobs/${encodeURIComponent(old.id)}`).catch(() => null);
      if (!job) continue;
      if (job.warning) banner(job.warning);
      if (job.status === 'error') banner(`ไม่สำเร็จ: ${job.error}`);
      if (job.status === 'done' && job.meeting_id) {
        // งานแปลไม่ควรเด้งหน้าจอไปที่อื่น แค่โหลดของเดิมใหม่
        if (job.kind === 'translate' && state.current === job.meeting_id) openMeeting(job.meeting_id);
        else if (job.kind !== 'translate' && !opened) { openMeeting(job.meeting_id); opened = true; }
        else if (state.current === job.meeting_id) openMeeting(job.meeting_id);
      }
    }
  }
  ensurePolling();
}

/* ---------------- pane: ประชุมใหม่ ---------------- */

/* ---------------- routing ด้วย hash ---------------- */

function setHash(h) {
  if (location.hash !== h) {
    state.ignoreHash = true;
    location.hash = h;
  }
}

/* บนจอแคบเราโชว์ทีละหน้าแทนการเอา sidebar มากองบน panel (ของเดิมยาวเกิน 3000px)
   ค่าใน body[data-view] เป็นตัวบอก CSS ว่าตอนนี้อยู่หน้าไหน — เดสก์ท็อปไม่สนใจค่านี้เลย */
const MOBILE_Q = window.matchMedia('(max-width: 860px)');
const isMobile = () => MOBILE_Q.matches;

const VIEW_BAR = {
  home:    { title: 'การประชุม', back: false },
  new:     { title: 'ประชุมใหม่', back: true },
  devices: { title: 'เครื่องประมวลผล', back: true },
  // หน้ารายละเอียดไม่ใส่ชื่อบนแถบ เพราะชื่อการประชุมในเนื้อหาแก้ไขได้ (contenteditable)
  // มีสองที่จะสับสนว่าต้องแก้อันไหน
  meeting: { title: '', back: true, action: '⋯' },
};

function setView(v) {
  document.body.dataset.view = v;
  // ออกจากหน้าแล้วแผ่นต้องไม่ค้างทับหน้าถัดไป
  closeAccountSheet();
  closeMeetingSheet();
  const bar = $('#mobilebar');
  const spec = VIEW_BAR[v] || VIEW_BAR.home;
  bar.hidden = !isMobile();
  $('#mb-back').hidden = !spec.back;
  $('#mb-title').textContent = spec.title;
  const act = $('#mb-action');
  act.hidden = !spec.action;
  act.textContent = spec.action || '';
}

// เดสก์ท็อปยังเห็น panel เป็นฟอร์ม "ประชุมใหม่" เหมือนเดิม — ต่างกันแค่ CSS ของจอแคบที่ซ่อน panel ไว้
const showHome = () => showNew('#home');

function showDevices() {
  setHash('#devices');
  renderWorkers();
  setView('devices');
}

function applyHash() {
  const h = location.hash;
  const m = h.match(/^#m\/([\w-]+)$/);
  if (m) openMeeting(m[1]);
  else if (h === '#devices') showDevices();
  else if (h === '#home') showHome();
  else showNew();
}

/* ---------- ตัวเล่นเสียงพร้อม waveform ---------- */

const PEAK_BARS = 64;
const PEAK_CACHE = 'mai_peaks_';
/* เพดานความยาวที่ยอมถอดในเบราว์เซอร์: decodeAudioData คลายไฟล์ทั้งไฟล์ลงแรมเป็น float32
   เท่ากับ วินาที × sampleRate × 4 ไบต์ × จำนวนช่อง — เราบังคับผ่าน OfflineAudioContext ที่
   8 kHz โมโนได้ราว 32 KB/วินาที (บางเบราว์เซอร์ยังคงจำนวนช่องเดิม จึงเผื่อเป็นสองเท่า)
   20 นาที ≈ 77 MB ซึ่งมือถือยังไหว ยาวกว่านั้นไม่ถอด ปล่อยให้เป็นแท่งสูงเท่ากัน
   ทางที่ถูกจริงคือให้ worker คำนวณตอนประมวลผลแล้วเก็บไว้กับการประชุม — ดู BACKLOG */
const PEAK_MAX_SECONDS = 20 * 60;

/* ยืด/ย่อชุดค่าให้พอดีกับจำนวนแท่ง — ฝั่ง worker ส่งมา 64 ค่าเท่ากับที่วาด แต่ถ้าวันหนึ่ง
   ฝั่งใดฝั่งหนึ่งเปลี่ยนจำนวน กราฟต้องไม่เพี้ยนหรือขาดหาย */
function resamplePeaks(src, n) {
  if (!src || !src.length) return null;
  if (src.length === n) return src;
  const out = [];
  for (let i = 0; i < n; i++) {
    const from = Math.floor(i * src.length / n);
    const to = Math.max(from + 1, Math.floor((i + 1) * src.length / n));
    let max = 0;
    for (let j = from; j < to && j < src.length; j++) max = Math.max(max, src[j] || 0);
    out.push(max);
  }
  return out;
}

function drawPeaks(bars, peaks) {
  bars.forEach((b, i) => { b.style.height = `${Math.max(6, peaks[i] || 0)}%`; });
}

async function loadPeaks(mid, duration) {
  try {
    const hit = localStorage.getItem(PEAK_CACHE + mid);
    if (hit) return JSON.parse(hit);
  } catch (e) { /* โหมดส่วนตัว หรือปิด storage ไว้ — ไม่ใช่เรื่องคอขาดบาดตาย */ }

  if (!duration || duration > PEAK_MAX_SECONDS) return null;
  const Ctx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  if (!Ctx) return null;

  try {
    // บน cloud endpoint นี้ 302 ไป R2 ซึ่งอาจไม่ตอบ CORS — ถ้า fetch ล้มก็ปล่อยให้ตกลง catch
    const res = await fetch(`/api/meetings/${mid}/audio`);
    if (!res.ok) return null;
    const raw = await res.arrayBuffer();
    // ขอ 8 kHz เพื่อให้ถอดออกมาเล็กที่สุด — ค่าที่ได้ใช้วาดกราฟ ไม่ได้ใช้ฟัง
    const ctx = new Ctx(1, 1, 8000);
    const buf = await ctx.decodeAudioData(raw);
    const data = buf.getChannelData(0);
    const per = Math.floor(data.length / PEAK_BARS) || 1;
    const peaks = [];
    let max = 0;
    for (let i = 0; i < PEAK_BARS; i++) {
      let sum = 0;
      const from = i * per;
      const to = Math.min(data.length, from + per);
      for (let j = from; j < to; j++) sum += data[j] * data[j];
      const rms = Math.sqrt(sum / Math.max(1, to - from));   // RMS อ่านง่ายกว่าค่าสูงสุด
      peaks.push(rms);
      if (rms > max) max = rms;
    }
    const scaled = peaks.map((v) => Math.round((max ? v / max : 0) * 100));
    try { localStorage.setItem(PEAK_CACHE + mid, JSON.stringify(scaled)); } catch (e) { /* เต็มก็ช่าง */ }
    return scaled;
  } catch (e) {
    return null;   // CORS, โคเดกที่ถอดไม่ได้, แรมไม่พอ — ตัวเล่นยังใช้ได้ แค่แท่งเท่ากันหมด
  }
}

const ICON_PLAY = 'M8 5.5v13l11-6.5-11-6.5Z';
const ICON_PAUSE = 'M7 5h3.5v14H7zM13.5 5H17v14h-3.5z';

function setupPlayer(mid) {
  const player = $('#player');
  if (!player) return;
  const audio = $('#d-audio');
  const wave = $('#p-wave');
  const playBtn = $('#p-play');
  const timeEl = $('#p-time');
  const errEl = $('#p-error');

  player.hidden = false;
  errEl.hidden = true;

  wave.innerHTML = '';
  const bars = [];
  for (let i = 0; i < PEAK_BARS; i++) {
    const b = document.createElement('span');
    b.className = 'p-bar';
    b.style.height = '34%';
    wave.appendChild(b);
    bars.push(b);
  }

  // duration ของ <audio> เชื่อไม่ได้เสมอ (สตรีม webm/ogg บางไฟล์คืน Infinity) —
  // ถอยไปใช้ค่าที่บันทึกไว้กับการประชุมซึ่งมาจากตอนประมวลผล
  const total = () => (Number.isFinite(audio.duration) && audio.duration > 0)
    ? audio.duration
    : ((state.meeting && state.meeting.duration) || 0);

  const paint = () => {
    const dur = total();
    const frac = dur ? Math.min(1, Math.max(0, audio.currentTime / dur)) : 0;
    const upto = Math.round(frac * PEAK_BARS);
    for (let i = 0; i < bars.length; i++) bars[i].classList.toggle('on', i < upto);
    timeEl.textContent = `${fmtClock(audio.currentTime)} / ${fmtClock(dur)}`;
    wave.setAttribute('aria-valuenow', String(Math.round(frac * 100)));
    wave.setAttribute('aria-valuetext', `${fmtClock(audio.currentTime)} จาก ${fmtClock(dur)}`);
  };

  const setIcon = () => {
    $('#p-icon').setAttribute('d', audio.paused ? ICON_PLAY : ICON_PAUSE);
    playBtn.setAttribute('aria-label', audio.paused ? 'เล่น' : 'หยุดชั่วคราว');
    player.classList.toggle('playing', !audio.paused);
  };

  playBtn.onclick = () => {
    if (audio.paused) audio.play().catch(() => {});
    else audio.pause();
  };
  audio.addEventListener('play', setIcon);
  audio.addEventListener('pause', setIcon);
  audio.addEventListener('timeupdate', paint);
  audio.addEventListener('loadedmetadata', paint);
  audio.addEventListener('error', () => {
    player.hidden = true;
    errEl.hidden = false;
    errEl.textContent = 'ไม่มีไฟล์เสียงของการประชุมนี้';
  });

  const seekTo = (clientX) => {
    const r = wave.getBoundingClientRect();
    if (!r.width) return;
    const frac = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
    const dur = total();
    if (dur) { audio.currentTime = frac * dur; paint(); }
  };
  let dragging = false;
  wave.addEventListener('pointerdown', (e) => {
    dragging = true;
    try { wave.setPointerCapture(e.pointerId); } catch (err) { /* ไม่รองรับก็ลากได้อยู่ดี */ }
    seekTo(e.clientX);
  });
  wave.addEventListener('pointermove', (e) => { if (dragging) seekTo(e.clientX); });
  wave.addEventListener('pointerup', () => { dragging = false; });
  wave.addEventListener('pointercancel', () => { dragging = false; });
  wave.addEventListener('keydown', (e) => {
    const dur = total();
    if (e.key === 'ArrowRight') audio.currentTime = Math.min(dur, audio.currentTime + 5);
    else if (e.key === 'ArrowLeft') audio.currentTime = Math.max(0, audio.currentTime - 5);
    else if (e.key === ' ' || e.key === 'Enter') playBtn.click();
    else return;
    e.preventDefault();
    paint();
  });

  setIcon();
  paint();

  // ทางหลัก: worker คำนวณให้ตอนประมวลผลแล้ว (BACKLOG #60) — ได้ทุกความยาว ไม่ต้องโหลดไฟล์ซ้ำ
  const served = resamplePeaks((state.meeting && state.meeting.peaks) || null, PEAK_BARS);
  if (served) {
    drawPeaks(bars, served);
    return;
  }
  // ทางสำรอง: ประชุมเก่าที่บันทึกไว้ก่อนมีฟีเจอร์นี้ — ถอดในเบราว์เซอร์ถ้าไฟล์สั้นพอ
  loadPeaks(mid, (state.meeting && state.meeting.duration) || 0).then((peaks) => {
    // ผู้ใช้อาจเปิดการประชุมอื่นไปแล้วระหว่างรอถอดไฟล์ — อย่าวาดทับของใหม่
    if (peaks && state.current === mid) drawPeaks(bars, peaks);
  });
}

/* action sheet ของหน้ารายละเอียด — ไม่สร้างปุ่มชุดใหม่ แต่ให้ CSS ย้าย .detail-actions
   (ปุ่มดาวน์โหลด/แชร์/ความเป็นส่วนตัว/ลบ ชุดเดิม) ลงมาเป็นแผ่นล่างจอตอน body.sheet-open
   ถ้าทำปุ่มใหม่ซ้อน จะมีสองชุดที่ต้องซิงก์สถานะ hidden/disabled กันเองตลอดไป */
function openMeetingSheet() {
  if (!$('.detail-actions')) return;
  document.body.classList.add('sheet-open');
  $('#sheet-scrim').hidden = false;
}

function closeMeetingSheet() {
  document.body.classList.remove('sheet-open');
  // ม่านใช้ร่วมกับแผ่นบัญชี — ซ่อนได้ต่อเมื่อไม่มีแผ่นไหนเปิดค้างอยู่
  if (!document.body.classList.contains('account-open')) $('#sheet-scrim').hidden = true;
}

/* จอแคบ: สรุปกับบทถอดเสียงเป็นแท็บ แทนที่จะต่อกันยาว */
/* เช็กลิสต์ Action Items (BACKLOG #53)

   `orphan` = เคยติ๊กว่าทำแล้ว แต่หายไปจากสรุปล่าสุด ระบบไม่ลบให้เองเพราะเป็นของที่คนทำไว้
   จึงต้องแยกกลุ่มและบอกเหตุผล ไม่ใช่ปนกับรายการปัจจุบันจนคนงงว่าทำไมมีงานที่อ่านไม่เจอในสรุป */
function actionRow(item) {
  const who = item.assignee || 'ไม่ได้ระบุ';
  const sub = [item.due ? `กำหนด ${esc(item.due)}` : ''].filter(Boolean).join(' · ');
  return `<div class="ai-row${item.done ? ' is-done' : ''}" data-id="${esc(item.id)}">
    <input type="checkbox" ${item.done ? 'checked' : ''} aria-label="ทำเสร็จแล้ว">
    <div class="ai-body">
      <div class="ai-text">${esc(item.text)}</div>
      <div class="ai-sub"><button type="button" class="ai-who">${esc(who)}</button>${sub ? ' · ' + sub : ''}</div>
    </div>
    ${item.orphan ? '<button type="button" class="ai-del" aria-label="ลบรายการนี้">✕</button>' : ''}
  </div>`;
}

function renderActionItems() {
  const box = $('#d-actions');
  if (!box) return;
  const items = state.meeting.action_items || [];
  const live = items.filter((i) => !i.orphan);
  const orphans = items.filter((i) => i.orphan);
  if (!items.length) {
    box.innerHTML = '<p class="muted">สรุปนี้ไม่มีตารางสิ่งที่ต้องทำ</p>';
    return;
  }
  let html = live.map(actionRow).join('');
  if (orphans.length) {
    html += `<div class="ai-orphans"><p>ทำแล้ว แต่ไม่อยู่ในสรุปล่าสุด</p>${orphans.map(actionRow).join('')}</div>`;
  }
  box.innerHTML = html;
}

async function saveActionItem(id, body) {
  const out = await api(`/api/meetings/${state.meeting.id}/action-items/${id}`, jsonPatch(body));
  state.meeting.action_items = out.action_items;
  renderActionItems();
}

function setupActionItems() {
  const box = $('#d-actions');
  if (!box) return;
  box.onchange = async (e) => {
    const cb = e.target.closest('input[type="checkbox"]');
    if (!cb) return;
    const row = cb.closest('.ai-row');
    try {
      await saveActionItem(row.dataset.id, { done: cb.checked });
    } catch (err) {
      cb.checked = !cb.checked;       // คืนสภาพให้ตรงกับเซิร์ฟเวอร์ ไม่ใช่ปล่อยให้โกหกตา
      banner(`บันทึกไม่สำเร็จ: ${err.message}`);
    }
  };
  box.onclick = async (e) => {
    const row = e.target.closest('.ai-row');
    if (!row) return;
    if (e.target.closest('.ai-who')) {
      const now = e.target.closest('.ai-who').textContent.trim();
      const next = prompt('ผู้รับผิดชอบ:', now === 'ไม่ได้ระบุ' ? '' : now);
      if (next === null) return;
      try {
        await saveActionItem(row.dataset.id, { assignee: next });
      } catch (err) { banner(`บันทึกไม่สำเร็จ: ${err.message}`); }
      return;
    }
    if (e.target.closest('.ai-del')) {
      try {
        const out = await api(
          `/api/meetings/${state.meeting.id}/action-items/${row.dataset.id}`,
          { method: 'DELETE' });
        state.meeting.action_items = out.action_items;
        renderActionItems();
      } catch (err) { banner(`ลบไม่สำเร็จ: ${err.message}`); }
    }
  };
}

/* สรุปกับบทถอดเสียงแยกเป็นแท็บ — **ใช้ทั้งสองจอ** ตั้งแต่ BACKLOG #80
   เดิมจอกว้างวางสองส่วนต่อกัน ซึ่งแปลว่าต้องเลื่อนผ่านสรุปทั้งอันกว่าจะถึงบทถอดเสียง
   เหตุผลเดียวกับที่จอแคบแยกมาตั้งแต่แรก ความกว้างจอไม่ได้ทำให้สรุปสั้นลง */
function setupDetailTabs() {
  const seg = $('#d-seg');
  if (!seg) return;
  const panes = $$('.dtab');
  seg.hidden = false;
  // ไม่มีตารางในสรุป = ไม่มีอะไรให้ดู ซ่อนปุ่มไปเลยดีกว่าให้กดแล้วเจอหน้าว่าง
  const hasItems = ((state.meeting || {}).action_items || []).length > 0;
  const actionsBtn = seg.querySelector('[data-tab="actions"]');
  if (actionsBtn) actionsBtn.hidden = !hasItems;
  const btns = $$('.seg-btn', seg).filter((b) => !b.hidden);
  const pick = (tab) => {
    btns.forEach((b) => {
      const on = b.dataset.tab === tab;
      b.classList.toggle('is-on', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    panes.forEach((p) => p.classList.toggle('tab-off', p.dataset.tab !== tab));
  };
  btns.forEach((b) => { b.onclick = () => pick(b.dataset.tab); });
  pick('summary');
}

/* แท็บที่เปิดหน้ามาแล้วตกใส่ — เจ้าของเลือกไว้ ไม่ใช่ "ปุ่มซ้ายสุด" อย่างที่เคยเป็น
   ลำดับปุ่มกับค่าเริ่มต้นจึงขยับแยกกันได้ ไม่ต้องเรียงใหม่ทั้งแถบเพื่อเปลี่ยนแท็บแรก */
const DEFAULT_CAP = 'rec';

/* เลือกวิธีนำเสียงเข้าทีละอันแทนการกางการ์ดทั้งสามใบพร้อมกัน — **ใช้ทั้งสองจอ**
   ตั้งแต่ BACKLOG #79 เดิมจอกว้างกางทั้งสามใบ ซึ่งกลายเป็นกริดสองคอลัมน์ที่ใบที่สาม
   ตกไปแถวล่างเหลือที่ว่างข้าง ๆ หนึ่งช่อง และทำให้หน้ายาวขึ้นโดยคนก็เลือกทางเดียวอยู่ดี
   การ์ดอัดสดอาจถูกปิดโดยแอดมิน (rec-card.hidden) ปุ่มของมันจึงต้องหายไปด้วย ไม่ใช่กดแล้วเจอที่ว่าง */
function setupCapturePicker() {
  const seg = $('#cap-seg');
  if (!seg) return;
  const label = $('#cap-label');
  const cards = $$('.cards .card');
  const avail = new Set(cards.filter((c) => !c.hidden).map((c) => c.dataset.cap));
  const btns = $$('.seg-btn', seg).filter((b) => {
    const ok = avail.has(b.dataset.cap);
    b.hidden = !ok;
    return ok;
  });
  seg.hidden = btns.length < 2;
  if (label) label.hidden = seg.hidden;   // หัวข้อโผล่คู่กับแถบเสมอ
  if (seg.hidden) return;

  const pick = (cap) => {
    btns.forEach((b) => {
      const on = b.dataset.cap === cap;
      b.classList.toggle('is-on', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    // ใช้คลาสไม่ใช่ hidden: การ์ดอัดสดใช้ hidden สื่อว่า "แอดมินปิดฟีเจอร์" อยู่แล้ว
    // ถ้าเอามาใช้ซ้ำเป็น "ไม่ได้เลือกแท็บนี้" สองความหมายจะทับกันจนหาบั๊กไม่เจอ
    cards.forEach((c) => c.classList.toggle('cap-off', c.dataset.cap !== cap));
    // แถบอัดลอยด้านล่างมีความหมายเฉพาะตอนอยู่แท็บอัดสด
    document.body.classList.toggle('cap-rec', cap === 'rec');
  };
  btns.forEach((b) => { b.onclick = () => pick(b.dataset.cap); });
  // แอดมินปิดการ์ดอัดสดได้ ปุ่มของมันจะไม่อยู่ใน btns เลย — ถอยไปปุ่มแรกที่เหลือ
  // ไม่ใช่ปล่อยให้ไม่มีแท็บไหนถูกเลือกแล้วเจอหน้าว่าง
  pick((btns.find((b) => b.dataset.cap === DEFAULT_CAP) || btns[0]).dataset.cap);
}

/* สรุปค่าใน <details> ให้เห็นบนหัวข้อ จะได้ไม่ต้องกางออกมาดูว่าตั้งอะไรไว้ */
function setupAdvSummary() {
  const out = $('#adv-sum');
  if (!out) return;
  // จอกว้างกางไว้เลย (ที่ว่างมีพอ และของเดิมก็เห็นทุกช่องอยู่แล้ว) จอแคบยุบไว้
  const det = out.closest('details');
  if (det) det.open = !isMobile();
  const label = (sel) => {
    const el = $(sel);
    return el && el.selectedIndex >= 0 ? el.options[el.selectedIndex].text : '';
  };
  const update = () => {
    const bits = [label('#f-lang'), label('#f-template'), label('#f-stt')].filter(Boolean);
    if ($('#f-diarize') && $('#f-diarize').checked) bits.push('แยกผู้พูด');
    out.textContent = bits.join(' · ');
  };
  ['#f-lang', '#f-template', '#f-stt', '#f-diarize', '#f-speakers']
    .forEach((s) => { const el = $(s); if (el) el.addEventListener('change', update); });
  update();
}

function showNew(hash = '#new') {
  state.current = null;
  state.meeting = null;
  setHash(hash);
  setView(hash === '#home' ? 'home' : 'new');
  renderList();
  const panel = $('#panel');
  panel.innerHTML = '';
  panel.append($('#tpl-new').content.cloneNode(true));

  $('#f-lang').value = state.config.lang || 'th';
  $('#f-template').innerHTML = (state.config.templates || [])
    .map((t) => `<option value="${esc(t.key)}">${esc(t.label)}</option>`).join('');

  // ตัวถอดเสียง — โชว์ทุกตัว แต่ตัวที่ใช้ไม่ได้จะเลือกไม่ได้พร้อมบอกเหตุผล
  const provs = state.config.stt_providers || [];
  const sttSel = $('#f-stt');
  // ห้าม disabled ทุกตัว: ถ้าไม่มีตัวไหนใช้ได้ เบราว์เซอร์จะเลือกอะไรไม่ได้เลย
  // แล้ว select แสดงว่างเปล่า พร้อมกับ #stt-note ที่หายไปด้วย — ผู้ใช้ไม่รู้เลยว่าติดอะไร
  // (เจอจริงตอน worker ออฟไลน์ทั้งหมด) จึงปล่อยให้เลือกได้ แล้วบอกเหตุผลไว้ใต้ช่อง
  sttSel.innerHTML = provs.map((p) => {
    const label = p.available ? p.label : `${p.label} — ใช้ไม่ได้`;
    return `<option value="${esc(p.key)}">${esc(label)}</option>`;
  }).join('');
  const preferred = provs.find((p) => p.key === state.config.stt_default && p.available)
    || provs.find((p) => p.available);
  if (preferred) sttSel.value = preferred.key;

  const showSttNote = () => {
    const p = provs.find((x) => x.key === sttSel.value) || provs[0];
    const note = $('#stt-note');
    note.textContent = p ? (p.available ? p.note : p.why) : '';
    note.className = p && !p.available ? 'warn' : 'hint';
  };
  sttSel.onchange = showSttNote;
  showSttNote();

  // เซิร์ฟเวอร์บน cloud ไม่มี whisper/ffmpeg จึงถอดเสียงสดให้ไม่ได้
  if (state.config.live_available === false) {
    const live = $('#c-live');
    live.checked = false;
    live.disabled = true;
    live.closest('label').title = 'เซิร์ฟเวอร์นี้ถอดเสียงเองไม่ได้ — ข้อความสดใช้ได้เฉพาะตอนรันในเครื่อง';
    live.closest('label').lastChild.textContent = ' แสดงข้อความสดระหว่างประชุม (เซิร์ฟเวอร์นี้ทำไม่ได้)';
  }

  // แอดมินปิดฟีเจอร์อัดสดจากเบราว์เซอร์ → ซ่อนการ์ดทั้งใบ (อัปโหลดไฟล์/เชิญบอทยังใช้ได้)
  // แถบควบคุมลอยด้านล่างอยู่นอกการ์ด จึงต้องซ่อน/โชว์คู่กันเอง พร้อมกับกันที่ให้ .panel
  const recCard = $('#rec-card');
  const recAvailable = liveOn();
  if (recCard) recCard.hidden = !recAvailable;
  document.body.classList.toggle('has-floatbar', recAvailable);
  if ($('#floatbar-idle')) $('#floatbar-idle').hidden = !recAvailable;
  if ($('#floatbar-live')) $('#floatbar-live').hidden = true;

  const diarizeBox = $('#f-diarize');
  if (!state.config.diarize_available) {
    diarizeBox.checked = false;
    diarizeBox.disabled = true;
    $('#f-speakers').disabled = true;
    const note = $('#diarize-note');
    note.hidden = false;
    note.textContent = 'แยกผู้พูดยังใช้ไม่ได้ ขาด: '
      + (state.config.diarize_missing || []).join('; ');
  }

  $('#btn-pick').onclick = () => { if (requireTitle()) $('#f-file').click(); };
  $('#f-file').onchange = (e) => { if (e.target.files[0]) uploadFile(e.target.files[0]); };

  const drop = $('#drop');
  ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => {
    e.preventDefault(); drop.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => {
    e.preventDefault(); drop.classList.remove('over');
  }));
  drop.addEventListener('drop', (e) => {
    const file = e.dataTransfer.files[0];
    if (file) uploadFile(file);
  });
  // บนมือถือไม่มีการลากไฟล์ กล่องทั้งใบจึงควรแตะได้ ไม่ใช่ต้องเล็งปุ่มเล็ก ๆ ตรงกลาง
  // (เช็ค closest('button') กัน dialog เปิดสองครั้งตอนกดโดนปุ่มพอดี)
  drop.addEventListener('click', (e) => {
    if (e.target.closest('button')) return;
    if (requireTitle()) $('#f-file').click();
  });

  $('#btn-rec').onclick = startRecording;
  $('#btn-stop').onclick = stopRecording;
  $('#btn-mute').onclick = toggleMute;
  buildWaveBars();
  setupCapturePicker();
  setupAdvSummary();
  setupSources();
  setupBot();
}

/* ---------------- บอทเข้าห้องประชุม ---------------- */

function setupBot() {
  const note = $('#bot-note');
  // ไม่ปิดช่องกรอกและไม่ปิดปุ่ม แม้ค่า config บอกว่ายังไม่พร้อม:
  //   1) config ถูกอ่านตอนโหลดหน้า ถ้าเพิ่งเปิด worker เสร็จ ค่าจะเก่าแล้วผู้ใช้ติดล็อกทั้งที่พร้อม
  //   2) ผู้ใช้ควรพิมพ์/วางลิงก์เตรียมไว้ได้ตลอด
  // ฝั่งเซิร์ฟเวอร์เป็นคนตัดสินจริง (ตอบ 409 พร้อมเหตุผลที่ตรงกับสถานะตอนนั้น)
  if (state.config.bot_available === false) {
    note.className = 'warn';
    note.textContent = 'ยังส่งไม่ได้ — ' + (state.config.bot_missing || []).join('; ');
  } else {
    note.className = 'hint';
    note.textContent = 'บอทเข้าห้องแล้ว host ต้องกด "รับเข้าห้อง" (Admit) ให้ก่อน '
      + 'เสร็จประชุมกด "ให้บอทออก" ที่แถบงาน หรือปล่อยให้ครบเวลาที่ตั้งไว้';
  }
  // ช่องรหัสโชว์เฉพาะลิงก์ Zoom — อีกสองเจ้าไม่มีรหัสแยกจากลิงก์
  const url = $('#b-url');
  const toggle = () => {
    $('#b-pass-field').hidden = !/zoom\.us/i.test(url.value);
    $('#b-detect').textContent = describeRoom(url.value);
  };
  url.oninput = toggle;
  toggle();
  $('#btn-bot').onclick = sendBot;
  $('#b-url').onkeydown = (e) => { if (e.key === 'Enter') sendBot(); };
}

/** บอกว่าลิงก์ที่วางมาเป็นห้องไหน — ให้ผู้ใช้เทียบเลขห้องกับหน้าต่างประชุมได้ก่อนกดส่ง.
    เคยพลาดจริง: ส่งบอทไปห้องเก่าที่ปิดแล้ว แล้วไล่สาเหตุกันหลายรอบเพราะไม่มีใครเห็นว่าเลขห้องต่างกัน */
function describeRoom(raw) {
  const v = (raw || '').trim();
  if (!v) return '';
  let u;
  try { u = new URL(v); } catch (e) { return 'ลิงก์ยังไม่สมบูรณ์'; }
  const host = u.hostname.toLowerCase();
  if (host === 'meet.google.com') {
    const code = u.pathname.replace(/^\/+/, '').split('/')[0];
    return code ? `Google Meet · ห้อง ${code}` : 'Google Meet';
  }
  if (host === 'teams.microsoft.com' || host === 'teams.live.com') {
    const m = u.pathname.match(/\/meet\/([0-9]+)/);
    return m ? `Microsoft Teams · ห้อง ${m[1]}` : 'Microsoft Teams';
  }
  if (host === 'zoom.us' || host.endsWith('.zoom.us')) {
    const m = u.pathname.match(/\/(?:j|wc\/join|wc)\/([0-9]{9,12})/);
    return m ? `Zoom · ห้อง ${m[1]} — ตรวจว่าตรงกับเลขในหน้าต่างประชุม` : 'Zoom';
  }
  return 'โฮสต์นี้ไม่รองรับ — ใช้ได้เฉพาะ Meet / Teams / Zoom';
}

async function sendBot() {
  if (!requireTitle()) return;
  const url = ($('#b-url').value || '').trim();
  if (!url) { banner('ใส่ลิงก์ห้องประชุมก่อน'); return; }
  const v = formValues();
  const btn = $('#btn-bot');
  btn.disabled = true;
  banner('');
  try {
    const job = await api('/api/meetings/bot', jsonPost({
      url,
      bot_name: ($('#b-name').value || '').trim(),
      max_minutes: parseInt($('#b-max').value || '120', 10),
      passcode: ($('#b-pass').value || '').trim(),
      title: v.title,
      lang: v.lang,
      template: v.template,
      diarize: v.diarize,
      num_speakers: v.num_speakers,
      stt: v.stt,
    }));
    state.jobs = [job, ...state.jobs.filter((j) => j.id !== job.id)];
    renderJobs();
    ensurePolling();
    // ไปหน้ารายการ อย่าค้างอยู่ที่ฟอร์มเดิม (BUG-071)
    //
    // บนจอแคบ `body[data-view="new"]` ซ่อน sidebar ทั้งแถบ ซึ่งเป็นที่อยู่ของการ์ดงาน
    // ผู้ใช้จึงไม่เห็น "บอทอยู่ในห้อง mm:ss" เลย เห็นแต่ฟอร์มเดิมที่ช่องลิงก์ถูกล้าง
    // ซึ่งอ่านได้ว่า "ยังไม่ได้ส่ง" แล้วกดส่งซ้ำ — ทั้งที่บอทเข้าห้องไปแล้ว
    // (showNew('#home') สร้างฟอร์มใหม่ให้ด้วย จึงไม่ต้องล้าง #b-url เอง)
    showNew('#home');
    banner('ส่งบอทแล้ว — ไปกด "รับเข้าห้อง" (Admit) ในห้องประชุมด้วย', 'bot-admit');
  } catch (e) {
    banner(`ส่งบอทไม่สำเร็จ: ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

async function stopJob(id) {
  try {
    const r = await api(`/api/jobs/${encodeURIComponent(id)}/stop`, { method: 'POST' });
    // งานที่ไม่มีเครื่องประมวลผลถืออยู่จะถูกยกเลิกทันที ไม่ใช่ "รอสิบวินาที"
    banner(r.cancelled
      ? 'งานนี้ไม่มีเครื่องประมวลผลถืออยู่ (worker หลุดกลางทาง) — ยกเลิกให้แล้ว'
      : 'สั่งให้บอทออกจากห้องแล้ว — รออีกไม่เกินสิบวินาทีแล้วจะเริ่มถอดเสียง');
    pollJobs();
  } catch (e) {
    banner(`สั่งหยุดไม่สำเร็จ: ${e.message}`);
  }
}

/* ---------------- แหล่งเสียง (โหมดอัด + อุปกรณ์) ---------------- */

/* โหมดอัด:
   room   = ไมค์ตัวเดียว ไม่มีกล่องขออนุญาตแชร์หน้าจอ -> ได้ทุกคนในห้องรวมอยู่แทร็กเดียว
   device = ไมค์ + อุปกรณ์อินพุตที่วนเสียงลำโพงกลับเข้ามา -> 2 แทร็ก แยกได้ว่าใครพูด
   tab    = แชร์แท็บ (getDisplayMedia) -> 2 แทร็ก เสียงอีกฝ่ายสะอาดที่สุด */
const REC_MODE_KEY = 'mai.recmode';
const MIC_DEV_KEY = 'mai.mic';
const SYS_DEV_KEY = 'mai.sysdev';

/* ชื่ออุปกรณ์อินพุตที่จริงๆ คือเสียงที่ออกลำโพงวนกลับเข้ามา
   ครอบทั้งชื่อไทย/อังกฤษ และไดรเวอร์เสมือนยอดนิยม */
const LOOPBACK_RE = new RegExp([
  'stereo\\s*mix', 'สเตอริโอ', 'what\\s*u\\s*hear', 'loopback', 'ลูปแบ็ค',
  'cable\\s*output', 'vb-?audio', 'voicemeeter', 'wave\\s*out', 'wasapi',
  'soundflower', 'blackhole', 'มิกซ์',
].join('|'), 'i');

const isLoopback = (d) => LOOPBACK_RE.test(d.label || '');

const recMode = () => $('#rec-modes input:checked')?.value || 'room';

const store = {
  get: (k, dflt = '') => { try { return localStorage.getItem(k) ?? dflt; } catch { return dflt; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* โหมดส่วนตัวเขียนไม่ได้ */ } },
};

const devices = { list: [], asked: false };

/* จอแคบ ๆ (ตรงกับ breakpoint มือถือใน style.css) ให้ซ่อนตัวเลือก "แชร์แท็บ" ไปเลย
   ไม่ใช่แค่ disabled — เดิมเช็คแค่ feature-detect getDisplayMedia ซึ่งพลาดกรณี Chrome
   เดสก์ท็อปจำลองจอมือถือ (DevTools) หรือ Chrome เต็มตัวบนแท็บเล็ตที่ยังมี API นี้จริง
   แต่ผู้ใช้จอแคบไม่มีทางแชร์แท็บได้อย่างมีความหมาย จึงต้องเช็ค viewport เพิ่มด้วย (BACKLOG #47) */
const MOBILE_BREAKPOINT_PX = 560;
/* ต้องเป็น MediaQueryList ตัวเดียวที่ฟัง change ได้ ไม่ใช่ matchMedia() ใหม่ทุกครั้งที่เรียก:
   ความกว้างเปลี่ยนระหว่างใช้งานได้จริง — หมุนแท็บเล็ต ย่อหน้าต่าง หรือสลับโหมดอุปกรณ์ใน
   DevTools ซึ่งเป็นกรณีที่ตั๋วยกมาเอง ถ้าเช็คแค่ตอนโหลดหน้า ตัวเลือกจะค้างอยู่ตามจอตอนนั้น */
const TAB_MODE_Q = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT_PX}px)`);

function updateTabModeAvailability() {
  const tab = $('#rec-modes input[value="tab"]');
  if (!tab) return;
  const mode = tab.closest('.mode');
  const supported = !!navigator.mediaDevices?.getDisplayMedia;
  const hide = !supported || TAB_MODE_Q.matches;
  mode.hidden = hide;
  tab.disabled = hide;
  if (hide && tab.checked) {
    // เคยเลือกไว้ตอนจอกว้างแล้วย่อ/หมุนเครื่อง — ต้องย้ายไปโหมดที่ใช้ได้จริง จำค่าใหม่
    // (ไม่งั้นครั้งหน้าจะโหลดโหมดที่ซ่อนอยู่กลับมาอีก) แล้ววาดแผงแหล่งเสียงใหม่ตามโหมด
    const room = $('#rec-modes input[value="room"]');
    room.checked = true;
    store.set(REC_MODE_KEY, room.value);
    renderSources();
  }
}

TAB_MODE_Q.addEventListener('change', updateTabModeAvailability);

function setupSources() {
  const saved = store.get(REC_MODE_KEY);
  const savedRadio = saved && $(`#rec-modes input[value="${saved}"]`);
  if (savedRadio) savedRadio.checked = true;

  updateTabModeAvailability();

  $$('#rec-modes input').forEach((r) => {
    r.onchange = () => { store.set(REC_MODE_KEY, r.value); renderSources(); };
  });
  $('#d-mic').onchange = (e) => store.set(MIC_DEV_KEY, e.target.value);
  $('#d-sys').onchange = (e) => store.set(SYS_DEV_KEY, e.target.value);

  // เบราว์เซอร์ปิดชื่ออุปกรณ์ไว้จนกว่าจะเคยได้สิทธิ์ไมค์ -> ครั้งแรกจะได้แค่รายการเปล่า
  // ถ้าเคยอนุญาตไว้แล้ว (permission ค้างอยู่) จะได้ชื่อครบตั้งแต่โหลดหน้า
  refreshDevices();
  navigator.mediaDevices?.addEventListener?.('devicechange', refreshDevices);
  renderSources();
}

async function refreshDevices() {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  try {
    const all = await navigator.mediaDevices.enumerateDevices();
    devices.list = all.filter((d) => d.kind === 'audioinput' && d.deviceId !== 'default');
  } catch {
    devices.list = [];
  }
  fillDeviceSelect($('#d-mic'), devices.list, store.get(MIC_DEV_KEY), 'ค่าเริ่มต้นของระบบ');
  const loops = devices.list.filter(isLoopback);
  fillDeviceSelect($('#d-sys'), loops.length ? loops : devices.list,
    store.get(SYS_DEV_KEY) || loops[0]?.deviceId, '— เลือกอุปกรณ์ —');
  renderSources();
}

function fillDeviceSelect(sel, items, want, placeholder) {
  const named = items.filter((d) => d.label);
  sel.innerHTML = `<option value="">${esc(placeholder)}</option>`
    + named.map((d) => `<option value="${esc(d.deviceId)}">${esc(d.label)}</option>`).join('');
  if (want && named.some((d) => d.deviceId === want)) sel.value = want;
}

/** อนุญาตไมค์หนึ่งครั้งเพื่อปลดล็อกชื่ออุปกรณ์ แล้วปิดสตรีมทิ้งทันที */
async function unlockDeviceNames() {
  devices.asked = true;
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    s.getTracks().forEach((t) => t.stop());
  } catch (e) {
    banner(`ขอสิทธิ์ไมค์ไม่ผ่าน: ${e.name === 'NotAllowedError' ? 'ถูกปฏิเสธ' : e.message}`);
  }
  await refreshDevices();
}

/* เตือนตอนอัดไปแล้วแต่ยังไม่ได้ยินเสียง — สาเหตุต่างกันตามโหมด */

// RMS ที่ถือว่า "มีเสียง" (~-48 dBFS) ต่ำกว่านี้คือเงียบจริง ไม่ใช่แค่พูดเบา
const HEARD_LEVEL = 0.004;
// ไม่เคยได้ยินอะไรเลยนานเท่านี้ = น่าจะตั้งค่าผิดตั้งแต่ต้น
const SILENT_START_SEC = 6;
// เคยได้ยินแล้วอยู่ ๆ เงียบยาวเท่านี้ = ไมค์อาจหลุด/ถูกปิดกลางทาง
// ยาวกว่าแบบแรกมาก เพราะประชุมเงียบกันเป็นนาทีเป็นเรื่องปกติ และคำเตือนนี้หายเองเมื่อมีเสียงกลับมา
const SILENT_LOST_SEC = 45;

const SILENT_WARN = {
  room: 'ยังไม่ได้ยินเสียงเลย — ตรวจว่าเลือกไมค์ถูกตัว ไมค์ไม่ได้ปิด (mute) และเสียงประชุมเปิดออกลำโพงอยู่',
  device: 'ยังไม่ได้ยินเสียงเลย — อุปกรณ์วนเสียงกลับมักเงียบถ้าเสียงระบบถูกปิด '
    + 'ลองเปิดเพลงทดสอบ หรือสลับไปโหมดไมค์เดียว',
  tab: 'ยังไม่ได้ยินเสียงเลย — ตรวจว่าติ๊ก “แชร์เสียงแท็บ” และเสียงประชุมไม่ได้ปิดอยู่',
};

/* คนละกรณีกับข้างบน: เคยได้ยินแล้วเงียบยาว — ของเดิมเตือนกรณีนี้ไม่ได้เลย เพราะเทียบกับ
   ค่าพีคสูงสุดตลอดกาล พอได้ยินเสียงครั้งเดียวก็ปิดปากตัวเองถาวร ไมค์ที่หลุดตอนนาทีที่ 5
   จึงอัดเป็นความเงียบไปจนจบโดยไม่มีอะไรบอก */
const LOST_WARN = 'เงียบมานานแล้ว — ถ้ายังประชุมกันอยู่ ให้ตรวจว่าไมค์ยังต่ออยู่และไม่ได้ถูกปิด '
  + '(ข้อความนี้จะหายเองเมื่อได้ยินเสียงอีกครั้ง)';

const REC_HINTS = {
  room: 'เปิดลำโพงไว้ ไมค์จะได้ทั้งเสียงคุณและอีกฝ่าย '
    + 'ระบบจะแยกผู้พูดให้จากเสียง (ติ๊ก “แยกผู้พูด” ด้านบน) ใช้ได้บนมือถือด้วย',
  device: 'เสียงคุณกับเสียงอีกฝ่ายถูกอัดแยกกัน จึงรู้แน่ว่าประโยคไหนใครพูด — ไม่มีกล่องขอแชร์หน้าจอ',
  tab: 'ตอนเลือกแท็บ ต้องติ๊ก “แชร์เสียงแท็บ” ด้วย ไม่งั้นจะได้ไฟล์เงียบ',
};

function renderSources() {
  const mode = recMode();
  $$('#rec-modes .mode').forEach((el) => {
    const input = $('input', el);
    el.classList.toggle('on', input.checked);
    el.classList.toggle('off', input.disabled);
  });

  const named = devices.list.some((d) => d.label);
  // โหมด room ใช้ค่าเริ่มต้นของระบบได้ ไม่ต้องเลือกอุปกรณ์ให้รก จนกว่าจะรู้ชื่ออุปกรณ์แล้ว
  $('#dev-wrap').hidden = mode === 'tab' || (mode === 'room' && !named);
  $('#d-sys-field').hidden = mode !== 'device';
  $('#rec-hint').textContent = REC_HINTS[mode] || '';
  // โหมด room ไมค์คือแหล่งเสียงเดียว ปิดไม่ได้ — อีกสองโหมดปิดได้ (เช่น อัดสัมมนาที่เราแค่นั่งฟัง)
  $('#c-mic-row').hidden = mode === 'room';

  const note = $('#dev-note');
  note.innerHTML = '';
  note.hidden = true;
  // โหมด room/tab ใช้อุปกรณ์เริ่มต้นของระบบได้เลย ไม่ต้องรู้ชื่ออุปกรณ์ก่อน
  if (mode !== 'device') return;
  note.hidden = false;

  if (!named) {
    note.innerHTML = 'ยังไม่รู้ชื่ออุปกรณ์ในเครื่อง (เบราว์เซอร์ปิดไว้จนกว่าจะได้สิทธิ์ไมค์) '
      + '<button type="button" class="linkbtn" id="btn-devperm">อนุญาตไมค์เพื่อดูรายชื่อ</button>';
    $('#btn-devperm').onclick = unlockDeviceNames;
    return;
  }
  if (!$('#d-sys').value) {
    note.innerHTML = 'ไม่พบอุปกรณ์ที่วนเสียงลำโพงกลับเข้ามา — เปิด <strong>Stereo Mix</strong> '
      + 'ใน Sound settings ▸ Recording (คลิกขวา ▸ Show Disabled Devices) '
      + 'หรือลงไดรเวอร์เสมือนอย่าง VB-CABLE แล้วกดรีเฟรชหน้า '
      + 'ถ้าไม่มีจริงๆ ใช้โหมดไมค์เดียวหรือแชร์แท็บแทนได้';
    return;
  }
  note.hidden = true;
}

/* ชื่อการประชุมเป็นช่องบังคับ (BACKLOG #78) — คืนชื่อที่ตัดช่องว่างแล้ว หรือ null
   พร้อมบอกผู้ใช้และโฟกัสช่องให้ ผู้เรียกต้องหยุดเองเมื่อได้ null

   **เรียกก่อนเริ่มงาน ไม่ใช่ตอนจบ**: ถ้าไปเช็คตอนส่ง คนอัดประชุมไปแล้วสี่สิบนาที
   จะเพิ่งรู้ตอนนั้นว่าลืมใส่ชื่อ ซึ่งสายเกินไปจนน่าโมโห */
function requireTitle() {
  const el = $('#f-title');
  const title = (el?.value || '').trim();
  if (title) return title;
  banner('ใส่ชื่อการประชุมก่อน');
  if (el) { el.focus(); el.select(); }
  return null;
}

function formValues() {
  return {
    title: ($('#f-title')?.value || '').trim(),
    lang: $('#f-lang')?.value || 'th',
    template: $('#f-template')?.value || 'general',
    diarize: !!$('#f-diarize')?.checked,
    num_speakers: parseInt($('#f-speakers')?.value || '0', 10),
    stt: $('#f-stt')?.value || null,
  };
}

/** สร้าง draft → อัปโหลดแทร็ก → สั่งประมวลผล */
async function submitMeeting(tracks, { source, fallbackTitle }) {
  const v = formValues();
  const draft = await api('/api/meetings', jsonPost({
    // ตาข่ายกันตก ไม่ใช่ทางปกติ — requireTitle() ดักไว้ตั้งแต่ก่อนเริ่มงานทุกเส้นแล้ว
    // (BACKLOG #78) แต่ถ้ามีเส้นไหนหลุดมาได้ การทิ้งไฟล์ที่อัดมาสี่สิบนาทีแย่กว่า
    // การตั้งชื่อให้เองมาก
    title: v.title || fallbackTitle,
    lang: v.lang,
    template: v.template,
    diarize: v.diarize,
    num_speakers: v.num_speakers,
    stt: v.stt,
    source,
  }));

  for (const [name, { blob, ext }] of Object.entries(tracks)) {
    const q = `ext=${encodeURIComponent(ext)}`;
    // ถามก่อนว่าให้อัปตรงเข้าที่เก็บภายนอกได้ไหม (เลี่ยงเพดาน body ของ serverless)
    let slot = null;
    try {
      slot = await api(`/api/meetings/${draft.id}/tracks/${name}/upload-url?${q}`);
    } catch (e) { /* เซิร์ฟเวอร์รุ่นเก่า/โหมดไฟล์ — ส่งไบต์ตรงไปเลย */ }

    if (slot && slot.url) {
      let put;
      try {
        put = await fetch(slot.url, { method: 'PUT', body: blob });
      } catch (e) {
        // fetch ข้ามโดเมนที่ถูก CORS บล็อกจะขึ้นแค่ "Failed to fetch" ไม่บอกสาเหตุ
        const host = new URL(slot.url).host;
        throw new Error(`อัปโหลดเข้าที่เก็บไฟล์ (${host}) ไม่ได้ — `
          + `ตรวจว่า CORS ของ bucket อนุญาต origin "${location.origin}" `
          + `และ method PUT แล้วหรือยัง (${e.message})`);
      }
      if (!put.ok) throw new Error(`อัปโหลดเข้าที่เก็บไม่สำเร็จ (HTTP ${put.status})`);
      await api(`/api/meetings/${draft.id}/tracks/${name}?${q}&key=${encodeURIComponent(slot.key)}`,
        { method: 'POST' });
    } else {
      await api(`/api/meetings/${draft.id}/tracks/${name}?${q}`, { method: 'POST', body: blob });
    }
  }

  const job = await api(`/api/meetings/${draft.id}/process`, { method: 'POST' });
  state.jobs = [job, ...state.jobs.filter((j) => j.id !== job.id)];
  renderJobs();
  ensurePolling();
  if ($('#f-title')) $('#f-title').value = '';
  return job;
}

async function uploadFile(file) {
  // ดักซ้ำอีกชั้นนอกเหนือจากตอนกดเปิด dialog — การลากไฟล์มาวางไม่ผ่านทางนั้น
  if (!requireTitle()) return;
  banner('');
  const ext = (file.name.split('.').pop() || '').toLowerCase();
  try {
    await submitMeeting(
      { mixed: { blob: file, ext } },
      { source: 'upload', fallbackTitle: file.name.replace(/\.[^.]+$/, '') },
    );
  } catch (e) {
    banner(`อัปโหลดไม่สำเร็จ: ${e.message}`);
  }
}

/* ---------------- recording ---------------- */

const rec = {
  recorders: {},      // ชื่อแทร็ก -> {recorder, chunks}
  streams: [], ctx: null, dest: null, mode: 'room', stopping: false,
  liveRecorder: null, liveTimer: null, liveBusy: false, liveText: [],
  timer: null, raf: null, started: 0, peak: 0, level: 0, recording: false,
  muted: false,
  // เวลาที่ 'ได้ยินเสียง' ครั้งล่าสุด (0 = ยังไม่เคยได้ยินเลยตั้งแต่เริ่มอัด)
  heardAt: 0,
};

/* คลื่นเสียงสด (waveform) — สร้างแท่งไว้ครั้งเดียวตอนเปิดหน้า "ประชุมใหม่" แล้วอัปเดต
   ความสูงทุกเฟรมใน tick() ของ startRecording() เท่านั้น (ดู style.css .wave/.wave-bar) */
const WAVE_BARS = 24;

function buildWaveBars() {
  const wrap = $('#wave');
  if (!wrap || wrap.childElementCount === WAVE_BARS) return;
  wrap.innerHTML = '';
  for (let i = 0; i < WAVE_BARS; i++) {
    const bar = document.createElement('span');
    bar.className = 'wave-bar';
    wrap.appendChild(bar);
  }
}

/** ปิด/เปิดไมค์ระหว่างอัด — ปิดทุกแทร็กเสียงต้นทาง (ไม่ใช่แค่ MediaRecorder) ผลคือ
    ช่วงที่ปิดไมค์จะถูกอัดเป็นความเงียบจริง ๆ ไม่ใช่แค่ไม่โชว์มิเตอร์ */
function toggleMute() {
  if (!rec.recording) return;
  rec.muted = !rec.muted;
  rec.streams.forEach((s) => s.getAudioTracks().forEach((t) => { t.enabled = !rec.muted; }));
  // เพิ่งเปิดไมค์กลับมา = เริ่มนับความเงียบใหม่ ไม่งั้นคำเตือน "เงียบมานานแล้ว" เด้งทันที
  // ทั้งที่ยังไม่ทันได้พูด (ตอนปิดไมค์ heardAt ไม่ขยับเลยเพราะแทร็กถูกปิดจริง)
  if (!rec.muted) rec.heardAt = Date.now();
  const btn = $('#btn-mute');
  if (btn) {
    btn.classList.toggle('on', rec.muted);
    btn.setAttribute('aria-pressed', String(rec.muted));
    btn.textContent = rec.muted ? '🔇 เปิดไมค์' : '🎙️ ปิดไมค์';
  }
}

function pickMime() {
  for (const t of ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']) {
    if (window.MediaRecorder && MediaRecorder.isTypeSupported(t)) return t;
  }
  return '';
}

const extFor = (mime) => (mime.includes('ogg') ? 'ogg' : mime.includes('mp4') ? 'm4a' : 'webm');

function newRecorder(stream, mime) {
  const entry = { chunks: [] };
  entry.recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
  entry.recorder.ondataavailable = (e) => { if (e.data.size) entry.chunks.push(e.data); };
  entry.recorder.start(1000);
  return entry;
}

const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)');

/** วนอ่านระดับเสียงจาก analyser ทุกเฟรม — คำนวณ rec.level/rec.peak เสมอ (ใช้หาจังหวะ
    เงียบสำหรับตัดคลิปถอดสด + เตือนไมค์เงียบ) แล้วเลือกวาดผลแบบใดแบบหนึ่ง:
    ปกติวาดเป็นคลื่นเสียงหลายแท่ง (.wave) ตาม prefers-reduced-motion ให้ถอยไปใช้
    มิเตอร์แท่งเดียวแบบเดิม (.meter) ที่ไม่มี transition ทุกเฟรม — แยกออกมาจาก
    startRecording() เพราะฟังก์ชันนั้นยาวอยู่แล้ว (~130 บรรทัด) ไม่อยากให้ยาวขึ้นอีก */
/** ข้อความเตือนที่ควรแสดงตอนนี้ — คืน '' แปลว่าไม่ต้องเตือน.
 *
 *  แยกออกมาเป็นฟังก์ชันล้วน ๆ (อ่าน rec + เวลา ไม่แตะ DOM) เพราะสองอาการที่ทำให้ต้องแก้
 *  ทดสอบไม่ได้เลยตอนมันฝังอยู่ใน setInterval:
 *   1. คำเตือนเดิม "ค้าง" — ตั้ง hidden = false แล้วไม่มีใครตั้งกลับ พอเงียบตอนเริ่ม 6 วินาที
 *      (ซึ่งปกติมาก คนกดอัดแล้วค่อยเริ่มพูด) คำเตือนจะอยู่ยาวจนจบ ทั้งที่เสียงเข้าตั้งนานแล้ว
 *      ผู้ใช้เห็นแล้วนึกว่าไฟล์เสีย — ทั้งที่ตัวอัดไฟล์อัดจากสตรีมต้นทางตรง ๆ ไม่เกี่ยวกับมิเตอร์
 *   2. เทียบกับ rec.peak ซึ่งเป็นค่าสูงสุด "ตลอดกาล" พอได้ยินเสียงครั้งเดียวเงื่อนไขก็เป็นเท็จ
 *      ตลอดไป ไมค์ที่หลุดกลางประชุมจึงไม่มีทางถูกเตือน (t.onended จับได้เฉพาะแทร็กที่ตายสนิท
 *      ไม่ใช่แทร็กที่ยังอยู่แต่ส่งความเงียบมา เช่น ถูกปิดที่ระดับ OS หรือ Bluetooth สลับโปรไฟล์)
 */
function silentWarning(now = Date.now()) {
  if (!rec.recording || rec.muted) return '';
  if (!rec.heardAt) {
    return (now - rec.started) / 1000 > SILENT_START_SEC
      ? (SILENT_WARN[rec.mode] || SILENT_WARN.room)
      : '';
  }
  return (now - rec.heardAt) / 1000 > SILENT_LOST_SEC ? LOST_WARN : '';
}

function startMeterLoop(analyser) {
  const buf = new Uint8Array(analyser.fftSize);
  const waveBars = $$('#wave .wave-bar');
  const barCount = waveBars.length;
  const chunk = barCount ? Math.max(1, Math.floor(buf.length / barCount)) : buf.length;
  const meterBar = $('#meter-bar');

  const tick = () => {
    analyser.getByteTimeDomainData(buf);
    let sum = 0;
    for (const v of buf) { const d = (v - 128) / 128; sum += d * d; }
    const level = Math.sqrt(sum / buf.length);
    rec.level = level;            // ตัวตัดคลิปสดใช้ค่านี้หาจังหวะเงียบ
    rec.peak = Math.max(rec.peak, level);
    if (level >= HEARD_LEVEL) rec.heardAt = Date.now();

    if (reducedMotion?.matches || !barCount) {
      if (meterBar) meterBar.style.width = `${Math.min(100, level * 320)}%`;
    } else {
      for (let i = 0; i < barCount; i++) {
        let s = 0;
        const start = i * chunk;
        const end = Math.min(buf.length, start + chunk);
        for (let j = start; j < end; j++) { const d = (buf[j] - 128) / 128; s += d * d; }
        const amp = Math.sqrt(s / (end - start));
        waveBars[i].style.height = `${Math.max(8, Math.min(100, amp * 380))}%`;
      }
    }
    rec.raf = requestAnimationFrame(tick);
  };
  tick();
}

/** ขอสตรีมจากอุปกรณ์อินพุตหนึ่งตัว — ระบุ deviceId ได้ ถ้าไม่ระบุใช้ตัวเริ่มต้นของระบบ */
async function openInput(deviceId, { echo }) {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error(window.isSecureContext
      ? 'เบราว์เซอร์นี้เข้าถึงไมค์ไม่ได้'
      : 'ต้องเปิดผ่าน http://127.0.0.1 หรือ https เท่านั้น '
        + '(เบราว์เซอร์ปิดการเข้าถึงไมค์บนหน้าที่ไม่ปลอดภัย)');
  }
  // echo=false ยังพ่วงปิดตัวลดเสียงรบกวนด้วย เพราะมันถูกออกแบบมาให้คนพูดใกล้ไมค์
  // ถ้าเปิดไว้ตอนอัดทั้งห้อง เสียงคนที่นั่งไกลจะถูกตัดทิ้งไปเลย (autoGainControl ช่วยดึงขึ้นมาแทน)
  const audio = { echoCancellation: echo, noiseSuppression: !!echo, autoGainControl: true };
  // exact เพื่อให้พังทันทีถ้าอุปกรณ์หาย ดีกว่าเงียบๆ ไปอัดตัวอื่นแล้วรู้ทีหลัง
  if (deviceId) audio.deviceId = { exact: deviceId };
  return navigator.mediaDevices.getUserMedia({ audio });
}

async function startRecording() {
  if (!requireTitle()) return;
  const mode = recMode();
  const wantLive = $('#c-live').checked;
  const micId = $('#d-mic')?.value || '';
  const sysId = $('#d-sys')?.value || '';
  if (mode === 'device' && !sysId) {
    banner('โหมดนี้ต้องเลือกอุปกรณ์เสียงในเครื่องก่อน — หรือสลับไปโหมดไมค์เดียว/แชร์แท็บ');
    return;
  }
  banner('');

  const mime = pickMime();
  let step = 'เตรียม AudioContext';
  try {
    const ctx = new AudioContext();
    // context ที่ยังถูก suspend อยู่ = กราฟเสียงไม่เดิน มิเตอร์นิ่งและคลิปถอดสดจะเงียบทั้งอัน
    // (แทร็กที่บันทึกลงไฟล์ไม่กระทบ เพราะอัดจากสตรีมต้นทางตรงๆ)
    if (ctx.state === 'suspended') await ctx.resume().catch(() => {});
    const dest = ctx.createMediaStreamDestination();
    rec.ctx = ctx;
    rec.dest = dest;
    rec.streams = [];
    rec.recorders = {};
    rec.liveText = [];
    rec.mode = mode;

    if (mode === 'tab') {
      step = 'ขอแชร์แท็บ (getDisplayMedia)';
      if (!navigator.mediaDevices?.getDisplayMedia) {
        throw new Error(window.isSecureContext
          ? 'เบราว์เซอร์นี้แชร์เสียงแท็บไม่ได้ — ใช้ Chrome หรือ Edge'
          : 'ต้องเปิดผ่าน http://127.0.0.1 หรือ https เท่านั้น (เบราว์เซอร์ปิดการเข้าถึงเสียงบนหน้าที่ไม่ปลอดภัย)');
      }
      // Chrome/Edge จะเสนอ "แชร์เสียงแท็บ" ได้ต่อเมื่อขอ video มาด้วย — ขอเฟรมเรตต่ำสุดแล้วไม่ใช้ภาพ
      let ds;
      try {
        ds = await navigator.mediaDevices.getDisplayMedia({
          video: { frameRate: 1 },
          audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
        });
      } catch (e) {
        if (e.name === 'NotAllowedError' || e.name === 'AbortError') throw e;
        // บางเบราว์เซอร์ไม่รับ constraint dictionary ของ display capture — ลองแบบง่ายสุดอีกที
        console.warn('getDisplayMedia แบบมี constraint ไม่ผ่าน, ลองแบบง่าย:', e.name, e.message);
        ds = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
      }
      rec.streams.push(ds);
      if (!ds.getAudioTracks().length) {
        cleanupRecording();
        banner('แท็บที่เลือกไม่ได้แชร์เสียงมา — ตอนเลือกแท็บต้องติ๊ก “แชร์เสียงแท็บ” ด้วย');
        return;
      }
      const tabOnly = new MediaStream(ds.getAudioTracks());
      ctx.createMediaStreamSource(tabOnly).connect(dest);
      step = 'สร้างตัวอัดของแทร็กแท็บ';
      rec.recorders.system = newRecorder(tabOnly, mime);
      ds.getVideoTracks().forEach((t) => { t.onended = () => stopRecording(); });
    }

    if (mode === 'device') {
      step = 'เปิดอุปกรณ์เสียงในเครื่อง';
      // อุปกรณ์วนเสียงกลับเป็นสัญญาณดิจิทัลตรงๆ — ตัวลดเสียงรบกวนจะกินเสียงพูดเปล่าๆ
      const ss = await openInput(sysId, { echo: false });
      rec.streams.push(ss);
      ctx.createMediaStreamSource(ss).connect(dest);
      rec.recorders.system = newRecorder(ss, mime);
    }

    if (mode === 'room' || $('#c-mic').checked) {
      step = mode === 'room' ? 'ขอสิทธิ์ไมโครโฟน' : 'ขอสิทธิ์ไมโครโฟน (แทร็กเสียงของคุณ)';
      // room: ไมค์ตัวเดียวต้องได้ทุกคนในห้อง จึงห้ามตัด echo (มันจะกินเสียงจากลำโพงทิ้ง)
      // device/tab: เสียงอีกฝ่ายมาทางแทร็กของตัวเองแล้ว ตัด echo กันซ้ำซ้อนได้เลย
      const ms = await openInput(micId, { echo: mode !== 'room' });
      rec.streams.push(ms);
      ctx.createMediaStreamSource(ms).connect(dest);
      step = 'สร้างตัวอัดของแทร็กไมค์';
      // ไมค์เดียวก็คือทั้งห้องรวมอยู่แทร็กเดียว = 'mixed' ไม่ใช่ 'mic'
      // ('mic' ฝั่งเซิร์ฟเวอร์ตีเป็น "ฉัน" ทุกประโยค ซึ่งผิดถ้าอีกฝ่ายก็เข้าไมค์ตัวนี้ด้วย)
      rec.recorders[mode === 'room' ? 'mixed' : 'mic'] = newRecorder(ms, mime);
    }

    // ถอนสิทธิ์ไมค์กลางทาง หรือถอดหูฟัง USB ออก = แทร็กตาย ถ้าไม่รู้ตัวจะอัดต่อได้ไฟล์เปล่า
    rec.streams.forEach((s) => s.getAudioTracks().forEach((t) => {
      t.onended = () => {
        if (rec.recording) {
          banner('อุปกรณ์เสียงหลุดกลางการอัด — หยุดและส่งเท่าที่อัดได้แล้ว');
          stopRecording();
        }
      };
    }));

    // ได้สิทธิ์แล้ว เบราว์เซอร์จึงเปิดเผยชื่ออุปกรณ์ — เก็บไว้ให้รอบหน้าเลือกได้
    refreshDevices();

    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    ctx.createMediaStreamSource(dest.stream).connect(analyser);

    rec.peak = 0;
    rec.heardAt = 0;
    rec.muted = false;
    rec.started = Date.now();
    rec.recording = true;
    $('#rec-idle').hidden = true;
    $('#rec-live').hidden = false;
    $('#rec-warn').hidden = true;
    $('#floatbar-idle').hidden = true;
    $('#floatbar-live').hidden = false;
    const muteBtn = $('#btn-mute');
    muteBtn.classList.remove('on');
    muteBtn.setAttribute('aria-pressed', 'false');
    muteBtn.textContent = '🎙️ ปิดไมค์';

    if (wantLive) {
      $('#live-wrap').hidden = false;
      $('#live-text').innerHTML = '<p class="muted">รอข้อความชุดแรก…</p>';
      const liveStatus = $('#live-status');
      liveStatus.classList.remove('stopped');
      liveStatus.textContent = 'พรีวิว — ข้อความสุดท้ายจะแม่นกว่านี้';
      cycleLive(mime);
    }

    rec.timer = setInterval(() => {
      const sec = (Date.now() - rec.started) / 1000;
      $('#rec-time').textContent = fmtClock(sec);
      $('#rec-warn').hidden = !silentWarning();
    }, 500);

    startMeterLoop(analyser);
  } catch (e) {
    cleanupRecording();
    console.error('startRecording ล้มเหลวที่ขั้น:', step, e);
    if (e.name === 'NotAllowedError') {
      banner('ไม่ได้รับอนุญาตให้เข้าถึงเสียง — กดอนุญาตในเบราว์เซอร์แล้วลองอีกครั้ง '
        + '(ถ้าเคยกดปฏิเสธไว้ ต้องไปแก้ที่ไอคอนรูปกุญแจข้าง URL)');
    } else if (e.name === 'OverconstrainedError') {
      // อุปกรณ์ที่จำไว้ถูกถอด/ปิดไปแล้ว — ล้างค่าที่จำไว้ให้กลับไปใช้ตัวเริ่มต้น
      store.set(MIC_DEV_KEY, '');
      store.set(SYS_DEV_KEY, '');
      refreshDevices();
      banner('ไม่พบอุปกรณ์ที่เลือกไว้ (อาจถูกถอดหรือปิดไป) — เลือกอุปกรณ์ใหม่แล้วลองอีกครั้ง');
    } else if (e.name === 'AbortError' || e.name === 'NotFoundError') {
      banner('ยกเลิกการเลือกแท็บ/ไม่พบอุปกรณ์เสียง — ลองอีกครั้ง');
    } else {
      // บอกขั้นที่พังกับชื่อ error ด้วย ไม่งั้นข้อความอย่าง "Not supported" ไล่ต่อไม่ได้
      banner(`เริ่มอัดไม่ได้ที่ขั้น "${step}" — ${e.name || 'Error'}: ${e.message}`);
    }
  }
}

/** ถอดเสียงสด: ตัดคลิปที่สมบูรณ์ในตัวเองแล้วส่งไปถอดทีละชิ้น
    (chunk ของ MediaRecorder ชิ้นหลังๆ ถอดเดี่ยวๆ ไม่ได้เพราะ header อยู่ชิ้นแรก
     จึงต้องปิด recorder แล้วเปิดใหม่ทุกรอบ)
    จุดตัดเลือกตอนเงียบ เพื่อไม่ให้คำถูกหักครึ่งที่รอยต่อ */
function cycleLive(mime) {
  if (!rec.recording) return;
  const chunks = [];
  const r = new MediaRecorder(rec.dest.stream, mime ? { mimeType: mime } : {});
  r.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  r.onstop = () => {
    const blob = new Blob(chunks, { type: r.mimeType || 'audio/webm' });
    if (blob.size > 2000) sendLive(blob, extFor(r.mimeType || 'audio/webm'));
    cycleLive(mime);
  };
  r.start();
  rec.liveRecorder = r;

  const startedAt = Date.now();
  let quietSince = null;
  const watch = () => {
    if (!rec.recording || r.state === 'inactive') return;
    const age = Date.now() - startedAt;
    if ((rec.level ?? 1) < LIVE_QUIET_LEVEL) {
      if (quietSince === null) quietSince = Date.now();
    } else {
      quietSince = null;
    }
    const quiet = quietSince !== null && Date.now() - quietSince >= LIVE_QUIET_HOLD;
    if (age >= LIVE_MAX_MS || (age >= LIVE_MIN_MS && quiet)) {
      r.stop();
      return;
    }
    rec.liveTimer = setTimeout(watch, 120);
  };
  rec.liveTimer = setTimeout(watch, 120);
}

async function sendLive(blob, ext) {
  if (rec.liveBusy) return;   // ยังถอดชิ้นก่อนไม่เสร็จ ข้ามชิ้นนี้ไปดีกว่าให้กองคิว
  rec.liveBusy = true;
  const lang = $('#f-lang')?.value || 'th';
  // ส่งท้ายข้อความที่ได้มาแล้วไปเป็นบริบท ให้ whisper ถอดต่อได้ต่อเนื่อง
  const prompt = rec.liveText.join(' ').slice(-LIVE_PROMPT_CHARS);
  try {
    const q = `ext=${ext}&lang=${encodeURIComponent(lang)}`
      + (prompt ? `&prompt=${encodeURIComponent(prompt)}` : '');
    const out = await api(`/api/live?${q}`, { method: 'POST', body: blob });
    const text = (out.text || '').trim();
    if (text) {
      rec.liveText.push(text);
      const el = $('#live-text');
      if (el) {
        el.innerHTML = rec.liveText.map((t) => `<p>${esc(t)}</p>`).join('');
        el.scrollTop = el.scrollHeight;
      }
    }
  } catch (e) {
    const st = $('#live-status');
    // หยุดพรีวิวแล้ว = ไม่ใช่สถานะ "สด" อีกต่อไป ถอดสีทีลออกไม่ให้เข้าใจผิด
    if (st) { st.classList.add('stopped'); st.textContent = `พรีวิวสดหยุดไป: ${e.message}`; }
  } finally {
    rec.liveBusy = false;
  }
}

function stopRecording() {
  // เข้าได้จากหลายทาง (กดปุ่ม / เลิกแชร์แท็บ / อุปกรณ์หลุด) กันเรียกซ้ำแล้วส่งไฟล์สองรอบ
  if (rec.stopping) return;
  rec.stopping = true;
  rec.recording = false;
  clearTimeout(rec.liveTimer);
  if (rec.liveRecorder && rec.liveRecorder.state !== 'inactive') {
    rec.liveRecorder.onstop = null;
    rec.liveRecorder.stop();
  }

  const entries = Object.entries(rec.recorders);
  if (!entries.length) { cleanupRecording(); return; }

  const seconds = Math.round((Date.now() - rec.started) / 1000);
  const silent = rec.peak < 0.004;
  let pending = entries.length;
  const tracks = {};

  entries.forEach(([name, entry]) => {
    entry.recorder.onstop = () => {
      const mime = entry.recorder.mimeType || 'audio/webm';
      const blob = new Blob(entry.chunks, { type: mime });
      if (blob.size) tracks[name] = { blob, ext: extFor(mime) };
      if (--pending === 0) finishRecording(tracks, seconds, silent);
    };
    if (entry.recorder.state !== 'inactive') entry.recorder.stop();
    else entry.recorder.onstop();
  });
}

async function finishRecording(tracks, seconds, silent) {
  cleanupRecording();
  if (!Object.keys(tracks).length) { banner('ไม่ได้ข้อมูลเสียงเลย — ลองอัดใหม่'); return; }

  const stamp = new Date().toLocaleString('th-TH', { dateStyle: 'short', timeStyle: 'short' });
  try {
    await submitMeeting(tracks, { source: 'record', fallbackTitle: `อัดสด ${stamp}` });
    banner(silent ? 'เตือน: ระดับเสียงตลอดการอัดเบามาก ไฟล์อาจเงียบ' : '');
  } catch (e) {
    banner(`ส่งไฟล์ที่อัดไม่สำเร็จ: ${e.message}`);
  }
}

function cleanupRecording() {
  rec.recording = false;
  rec.stopping = false;
  rec.heardAt = 0;
  rec.muted = false;
  clearInterval(rec.timer);
  clearTimeout(rec.liveTimer);
  cancelAnimationFrame(rec.raf);
  rec.timer = rec.raf = rec.liveTimer = null;
  rec.liveRecorder = null;
  rec.recorders = {};
  rec.streams.forEach((s) => s.getTracks().forEach((t) => t.stop()));
  rec.streams = [];
  if (rec.ctx) { rec.ctx.close().catch(() => {}); rec.ctx = null; }
  rec.dest = null;
  if ($('#rec-idle')) {
    $('#rec-idle').hidden = false;
    $('#rec-live').hidden = true;
    $('#rec-time').textContent = '00:00';
    $('#meter-bar').style.width = '0%';
    $$('#wave .wave-bar').forEach((b) => { b.style.height = '8%'; });
  }
  if ($('#floatbar-idle')) {
    $('#floatbar-idle').hidden = false;
    $('#floatbar-live').hidden = true;
  }
}

/* ---------------- pane: รายละเอียด ---------------- */

const SPEAKER_CLASSES = 6;
/** แฮชชื่อผู้พูดเป็นเลขบัคเก็ตสี — คงที่ไม่ว่าจะเรนเดอร์กี่รอบหรือโหลดหน้าใหม่กี่ครั้ง
    (เดิมใช้ index ในลิสต์ m.speakers ซึ่งขึ้นกับลำดับที่ server ส่งมา เปลี่ยนได้ถ้าลำดับเปลี่ยน) */
function hashSpeakerName(name) {
  let h = 0;
  const s = String(name || '');
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h;
}
const speakerClass = (name) => `spk-${(hashSpeakerName(name) % SPEAKER_CLASSES) + 1}`;

function renderTranscript(editing) {
  const m = state.meeting;
  const segs = m.segments_list || [];
  if (!segs.length) {
    $('#d-transcript').innerHTML = '<p class="muted">(ไม่มีบทถอดเสียง)</p>';
    return;
  }
  $('#d-transcript').innerHTML = segs.map((s, i) => {
    const spk = s.speaker
      ? `<span class="tspk ${speakerClass(s.speaker)}" ${editing ? 'contenteditable="true" spellcheck="false"' : ''}>${esc(s.speaker)}</span>`
      : '';
    return `<div class="tseg" data-i="${i}" data-start="${s.start}">
      <button class="tstart" type="button" title="ฟังตรงนี้">${esc(fmtClock(s.start))}</button>
      ${spk}
      <span class="ttext" ${editing ? 'contenteditable="true" spellcheck="false"' : ''}>${esc(s.text)}</span>
    </div>`;
  }).join('');
}

function renderSpeakers() {
  const m = state.meeting;
  const all = m.speakers || [];
  const el = $('#d-speakers');
  if (!all.length) {
    el.innerHTML = '<span class="muted">ไม่ได้แยกผู้พูดสำหรับการประชุมนี้</span>';
    return;
  }
  el.innerHTML = all.map((s) =>
    `<button class="chip ${speakerClass(s)}" data-speaker="${esc(s)}" title="คลิกเพื่อเปลี่ยนชื่อ">${esc(s)}</button>`
  ).join('');
}

function renderSummaryView() {
  const m = state.meeting;
  const lang = $('#d-lang').value;
  const text = lang === 'orig' ? (m.summary || '') : ((m.translations || {})[lang] || '');
  const view = $('#d-summary');
  if (text) {
    // ป้าย ✨ เป็น static markup ไม่ได้ผ่าน renderMarkdown เลยไม่กระทบการ escape ของเนื้อหาจริง
    view.innerHTML = '<span class="ai-badge">✨ สรุปโดย AI</span>' + renderMarkdown(text);
  } else if (lang === 'orig') {
    view.innerHTML = '<p class="muted">ยังไม่มีสรุป — กด “สรุปใหม่ด้วย AI”</p>';
  } else {
    view.innerHTML = '<p class="muted">ยังไม่ได้แปลเป็นภาษานี้ — กด “แปล”</p>';
  }
  $('#d-translate').disabled = lang === 'orig';
  $('#d-edit').disabled = lang !== 'orig';
  // ปุ่มต้องบอกสิ่งที่จะเกิดขึ้นจริง: เลือกภาษาอื่นอยู่แล้วกด "สรุปใหม่" = สรุปเป็นภาษานั้น
  // ตรงจากบทถอดเสียง ไม่ใช่แปลจากสรุปไทย (BACKLOG #9b) ถ้าปุ่มยังเขียนเหมือนเดิม
  // ผู้ใช้จะนึกว่าไปทับสรุปต้นฉบับ
  const langs = state.config.languages || {};
  $('#d-resummarize').textContent = lang === 'orig'
    ? 'สรุปใหม่ด้วย AI'
    : `สรุปใหม่เป็น${langs[lang] || lang}`;
}

function renderLangSelect() {
  const m = state.meeting;
  const done = m.translations || {};
  const langs = state.config.languages || {};
  // จำภาษาที่ดูอยู่ไว้ก่อนวาดใหม่: สั่งสรุปเป็นอังกฤษแล้วพองานเสร็จหน้าจะโหลดซ้ำ
  // ถ้าตัวเลือกเด้งกลับเป็น "ต้นฉบับ" ผู้ใช้จะเห็นสรุปไทยเหมือนเดิมแล้วนึกว่าไม่มีอะไรเกิดขึ้น
  const keep = $('#d-lang').value;
  const opts = ['<option value="orig">ต้นฉบับ</option>'];
  for (const [code, label] of Object.entries(langs)) {
    if (code === (m.language || 'th') && !done[code]) continue;
    opts.push(`<option value="${esc(code)}">${esc(label)}${done[code] ? ' ✓' : ''}</option>`);
  }
  $('#d-lang').innerHTML = opts.join('');
  if (keep && [...$('#d-lang').options].some((o) => o.value === keep)) $('#d-lang').value = keep;
}

async function openMeeting(id) {
  let m;
  try {
    m = await api(`/api/meetings/${id}`);
  } catch (e) {
    banner(`เปิดการประชุมไม่ได้: ${e.message}`);
    return;
  }
  const sameMeeting = state.current === id;
  state.current = id;
  state.meeting = m;
  setHash(`#m/${id}`);
  setView('meeting');
  renderList();
  // เผื่อมาจากหน้า "ประชุมใหม่" ที่ตั้ง padding กันแถบลอยด้านล่างไว้ — หน้านี้ไม่มีแถบนั้น
  document.body.classList.remove('has-floatbar');

  const panel = $('#panel');
  panel.innerHTML = '';
  panel.append($('#tpl-detail').content.cloneNode(true));

  $('#d-title').textContent = m.title;
  const bits = [
    fmtDate(m.created),
    fmtDuration(m.duration),
    `${m.segments} ช่วงประโยค`,
    `ภาษา ${m.language}`,
    m.source === 'record' ? 'อัดสด' : 'อัปโหลด',
  ];
  if (m.template) {
    const t = (state.config.templates || []).find((x) => x.key === m.template);
    if (t) bits.push(t.label);
  }
  if (m.edited) bits.push('สรุปถูกแก้ไขแล้ว');
  if (m.transcript_edited) bits.push('บทถอดเสียงถูกแก้ไขแล้ว');
  $('#d-meta').textContent = bits.join(' · ');

  $('#d-export-fmt').innerHTML = (state.config.formats || ['md'])
    .map((f) => `<option value="${esc(f)}">.${esc(f)}</option>`).join('');

  const audio = $('#d-audio');
  audio.src = `/api/meetings/${id}/audio`;

  renderSpeakers();
  renderLangSelect();
  renderSummaryView();
  renderTranscript(false);
  renderActionItems();
  setupActionItems();

  if (m.summary_error) {
    const err = $('#d-summary-err');
    err.hidden = false;
    err.textContent = `สรุปครั้งก่อนไม่สำเร็จ: ${m.summary_error} — บทถอดเสียงยังอยู่ครบ`;
  }

  /* --- ชื่อเรื่อง --- */
  $('#d-title').onblur = async () => {
    const title = $('#d-title').textContent.trim();
    if (!title || title === m.title) { $('#d-title').textContent = m.title; return; }
    try {
      await api(`/api/meetings/${id}`, jsonPatch({ title }));
      m.title = title;
      await refresh();
    } catch (e) { banner(`เปลี่ยนชื่อไม่สำเร็จ: ${e.message}`); }
  };
  $('#d-title').onkeydown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); $('#d-title').blur(); }
  };

  /* --- เปลี่ยนชื่อผู้พูด (เปลี่ยนทุกบรรทัดที่เป็นคนนั้น) --- */
  $('#d-speakers').onclick = async (e) => {
    const chip = e.target.closest('[data-speaker]');
    if (!chip) return;
    const old = chip.dataset.speaker;
    const next = prompt(`เปลี่ยนชื่อ “${old}” เป็น:`, old);
    if (!next || next.trim() === old) return;
    const segments = (m.segments_list || []).map((s) => (
      s.speaker === old ? { ...s, speaker: next.trim() } : s
    ));
    try {
      state.meeting = await api(`/api/meetings/${id}`, jsonPatch({ segments }));
      renderSpeakers();
      renderTranscript(false);
      await refresh();
    } catch (e2) { banner(`เปลี่ยนชื่อผู้พูดไม่สำเร็จ: ${e2.message}`); }
  };

  setupPlayer(id);
  setupDetailTabs();
  // ปุ่มในแผ่นเป็นปุ่มเดิมของ .detail-actions — กดแล้วต้องปิดแผ่นเอง ไม่งั้นม่านค้างทับหน้า
  //
  // แต่ห้ามปิดตอนกด <select> (BUG-063): บนมือถือ การแตะ select จะยิง click ขึ้นมาถึงตัวนี้
  // ก่อนที่ตัวเลือกจะโผล่ พอปิดแผ่น CSS ก็ซ่อน .detail-actions ทั้งก้อน select หายไปจาก
  // layout และตัวเลือกไม่เคยขึ้นเลย — อาการที่ผู้ใช้เห็นคือ "ความเป็นส่วนตัว" กับ "ฟอร์แมต
  // ดาวน์โหลด" กดไม่ได้ ส่วนปุ่มอื่นในแผ่นเดียวกันใช้ได้ปกติ
  $('.detail-actions').addEventListener('click', (e) => {
    if (e.target.closest('select')) return;
    if (document.body.classList.contains('sheet-open')) closeMeetingSheet();
  });
  // เลือกค่าเสร็จแล้วค่อยปิดแผ่น — ผู้ใช้เลือกความเป็นส่วนตัวเสร็จก็จบธุระแล้ว
  // (ฟอร์แมตดาวน์โหลดไม่ปิด เพราะต้องกดปุ่ม ⬇ ต่อในแผ่นเดียวกัน)
  $('#d-visibility').addEventListener('change', () => {
    if (document.body.classList.contains('sheet-open')) closeMeetingSheet();
  });

  /* --- คลิกบรรทัด -> กระโดดไปฟัง --- */
  $('#d-transcript').onclick = (e) => {
    if (e.target.isContentEditable) return;
    const row = e.target.closest('.tseg');
    if (!row) return;
    audio.currentTime = parseFloat(row.dataset.start) || 0;
    audio.play().catch(() => {});
  };

  audio.ontimeupdate = () => {
    const t = audio.currentTime;
    const segs = state.meeting.segments_list || [];
    let idx = -1;
    for (let i = 0; i < segs.length; i++) {
      if (t >= segs[i].start && t < segs[i].end) { idx = i; break; }
    }
    const prev = $('.tseg.playing');
    if (prev && Number(prev.dataset.i) === idx) return;
    if (prev) prev.classList.remove('playing');
    if (idx >= 0) {
      const row = $(`.tseg[data-i="${idx}"]`);
      if (row) row.classList.add('playing');
    }
  };

  /* --- แก้สรุป --- */
  const editor = $('#d-editor');
  const view = $('#d-summary');
  const setEditing = (on) => {
    editor.hidden = !on;
    view.hidden = on;
    $('#d-edit').hidden = on;
    $('#d-save').hidden = !on;
    $('#d-cancel').hidden = !on;
  };
  $('#d-edit').onclick = () => {
    editor.value = state.meeting.summary || '';
    setEditing(true);
    editor.focus();
  };
  $('#d-cancel').onclick = () => setEditing(false);
  $('#d-save').onclick = async () => {
    try {
      state.meeting = await api(`/api/meetings/${id}`, jsonPatch({ summary: editor.value }));
      renderSummaryView();
      setEditing(false);
      await refresh();
    } catch (e) { banner(`บันทึกไม่สำเร็จ: ${e.message}`); }
  };

  /* --- แก้บทถอดเสียง --- */
  const setTEditing = (on) => {
    $('#t-edit').hidden = on;
    $('#t-save').hidden = !on;
    $('#t-cancel').hidden = !on;
    renderTranscript(on);
  };
  $('#t-edit').onclick = () => setTEditing(true);
  $('#t-cancel').onclick = () => setTEditing(false);
  $('#t-save').onclick = async () => {
    const base = state.meeting.segments_list || [];
    const segments = $$('.tseg').map((row) => {
      const i = Number(row.dataset.i);
      const seg = { ...base[i] };
      seg.text = $('.ttext', row).textContent.trim();
      const spk = $('.tspk', row);
      if (spk) seg.speaker = spk.textContent.trim();
      return seg;
    });
    try {
      state.meeting = await api(`/api/meetings/${id}`, jsonPatch({ segments }));
      renderSpeakers();
      setTEditing(false);
      await refresh();
    } catch (e) { banner(`บันทึกบทถอดเสียงไม่สำเร็จ: ${e.message}`); }
  };

  /* --- แปล / สรุปใหม่ / export / ลบ --- */
  $('#d-lang').onchange = renderSummaryView;
  $('#d-translate').onclick = async () => {
    const lang = $('#d-lang').value;
    if (lang === 'orig') return;
    try {
      const job = await api(`/api/meetings/${id}/translate`, jsonPost({ lang }));
      state.jobs = [job, ...state.jobs.filter((j) => j.id !== job.id)];
      renderJobs();
      ensurePolling();
      banner('');
    } catch (e) { banner(`สั่งแปลไม่สำเร็จ: ${e.message}`); }
  };

  $('#d-resummarize').onclick = async () => {
    $('#d-resummarize').disabled = true;
    try {
      const lang = $('#d-lang').value;
      const job = await api(`/api/meetings/${id}/resummarize`,
                            lang === 'orig' ? { method: 'POST' } : jsonPost({ lang }));
      state.jobs = [job, ...state.jobs.filter((j) => j.id !== job.id)];
      renderJobs();
      ensurePolling();
      banner('');
    } catch (e) {
      banner(`สั่งสรุปใหม่ไม่สำเร็จ: ${e.message}`);
      $('#d-resummarize').disabled = false;
    }
  };

  $('#d-export').onclick = () => {
    location.href = `/api/meetings/${id}/export.${$('#d-export-fmt').value}`;
  };

  /* --- แชร์ / ใครเห็น (เจ้าของเท่านั้น) --- */
  const owner = state.config.auth_required && state.user
    && (!m.owner_id || m.owner_id === state.user.id);
  if (owner) {
    const vis = $('#d-visibility');
    vis.hidden = false;
    vis.value = m.visibility || 'private';
    vis.onchange = async () => {
      try {
        state.meeting = await api(`/api/meetings/${id}/visibility`,
          jsonPatch({ visibility: vis.value }));
        banner(vis.value === 'team' ? 'ทุกคนในทีมเห็นการประชุมนี้แล้ว' : 'กลับเป็นเฉพาะคุณแล้ว');
        await refresh();
      } catch (e) {
        banner(`เปลี่ยนไม่สำเร็จ: ${e.message}`);
        vis.value = m.visibility || 'private';
      }
    };

    const box = $('#share-box');
    $('#d-share').hidden = false;
    $('#d-share').onclick = async () => {
      box.hidden = !box.hidden;
      if (!box.hidden) await loadShares(id);
    };
    $('#share-new').onclick = async () => {
      try {
        const out = await api(`/api/meetings/${id}/share`,
          jsonPost({ can_edit: $('#share-edit').checked }));
        const url = location.origin + out.path;
        $('#share-link').value = url;
        const copied = await copyText(url);
        banner(copied ? 'คัดลอกลิงก์แชร์แล้ว' : 'สร้างลิงก์แล้ว — กดคัดลอกในช่องได้เลย');
        await loadShares(id);
      } catch (e) { banner(`สร้างลิงก์ไม่สำเร็จ: ${e.message}`); }
    };
    $('#share-copy').onclick = async () => {
      const v = $('#share-link').value;
      if (!v) return;
      $('#share-link').select();
      banner(await copyText(v) ? 'คัดลอกแล้ว' : 'กด Ctrl+C เพื่อคัดลอก');
    };
    $('#share-revoke').onclick = async () => {
      if (!confirm('ยกเลิกลิงก์แชร์ทั้งหมดของการประชุมนี้?')) return;
      try {
        const out = await api(`/api/meetings/${id}/share`, { method: 'DELETE' });
        $('#share-link').value = '';
        banner(`ยกเลิกไปแล้ว ${out.revoked} ลิงก์`);
        await loadShares(id);
      } catch (e) { banner(`ยกเลิกไม่สำเร็จ: ${e.message}`); }
    };
  }

  // คนถือลิงก์แบบอ่านอย่างเดียว ซ่อนปุ่มที่กดไปก็ 403
  if (!canEdit()) {
    for (const sel of ['#d-edit', '#d-save', '#d-cancel', '#d-resummarize', '#d-translate',
                       '#d-delete', '#t-edit', '#t-save', '#t-cancel']) {
      const el = $(sel);
      if (el) el.hidden = true;
    }
    $('#d-title').contentEditable = 'false';
  }

  $('#d-delete').onclick = async () => {
    if (!confirm(`ลบ “${m.title}” ทิ้ง? ลบแล้วเอากลับไม่ได้`)) return;
    try {
      await api(`/api/meetings/${id}`, { method: 'DELETE' });
      state.current = null;
      state.meeting = null;
      await refresh();
      await refreshConfig();
      showNew();
    } catch (e) { banner(`ลบไม่สำเร็จ: ${e.message}`); }
  };

  updateStreamingIndicator();
  if (!sameMeeting) banner('');
}

/* ---------------- init ---------------- */

let searchTimer = null;
$('#search').oninput = (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value;
  searchTimer = setTimeout(() => { state.query = q; refresh(); }, 220);
};

$('#btn-new').onclick = () => showNew();

// ปุ่มย้อนกลับของมือถือ — ทุกหน้ากลับไปที่รายการ (ไม่ใช้ history.back() เพราะผู้ใช้อาจเข้ามา
// ที่ #m/<id> ตรง ๆ จากลิงก์แชร์ แล้วย้อนกลับจะหลุดออกจากเว็บไปเลย)
$('#mb-back').onclick = () => showHome();
$('#m-new').onclick = () => showNew();
$('#m-devices').onclick = () => showDevices();
$('#mb-action').onclick = () => openMeetingSheet();
// สลับระหว่างจอกว้าง/แคบกลางคัน (หมุนเครื่อง, ย่อหน้าต่าง) ต้องอัปเดตแถบเอง
$('#btn-account').onclick = openAccountSheet;
// ปุ่มในแผ่นเป็นปุ่มเดิมของ #userbox — กดแล้วต้องปิดแผ่นเอง ไม่งั้นม่านค้างทับหน้า
$('#userbox').addEventListener('click', (e) => {
  if (e.target.closest('button')) closeAccountSheet();
});
$('#sheet-scrim').onclick = () => { closeMeetingSheet(); closeAccountSheet(); };
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  closeMeetingSheet();
  closeAccountSheet();
});

MOBILE_Q.addEventListener('change', () => {
  setView(document.body.dataset.view || 'home');
  // ฟอร์ม/หน้ารายละเอียดที่ค้างอยู่ต้องสลับระหว่าง "กางทั้งหมด" กับ "เลือกทีละอัน" ตามไปด้วย
  if ($('#cap-seg')) { setupCapturePicker(); setupAdvSummary(); }
  if ($('#d-seg')) setupDetailTabs();
  if (!isMobile()) { closeMeetingSheet(); closeAccountSheet(); }
});

$('#list').onclick = (e) => {
  const li = e.target.closest('li[data-id]');
  if (li) openMeeting(li.dataset.id);
};

window.addEventListener('beforeunload', (e) => {
  if (rec.recording) { e.preventDefault(); e.returnValue = ''; }
});

// ลงทะเบียนครั้งเดียวตอนโหลดสคริปต์ (ไม่ใช่ทุกครั้งที่ setupSources() รัน) กัน listener
// พอกพูนทุกรอบที่ผู้ใช้กลับมาหน้า "ประชุมใหม่" — no-op เองถ้าไม่ได้อยู่หน้านั้น
window.addEventListener('resize', () => {
  if (!$('#rec-modes')) return;
  updateTabModeAvailability();
  renderSources();   // เผื่อ resize บังคับสลับโหมดกลับไป room ต้องอัปเดตคำใบ้/ช่องอุปกรณ์ด้วย
});

window.addEventListener('hashchange', () => {
  // ข้ามรอบที่เราเปลี่ยน hash เอง ไม่ให้เรนเดอร์ซ้ำ
  if (state.ignoreHash) { state.ignoreHash = false; return; }
  applyHash();
});

// PWA: ลงทะเบียนเฉพาะ secure context (https หรือ localhost) ไม่งั้นเบราว์เซอร์ปฏิเสธอยู่ดี
if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}

async function loadShares(id) {
  try {
    const out = await api(`/api/meetings/${id}/share`);
    const n = (out.shares || []).length;
    $('#share-count').textContent = n ? `มี ${n} ลิงก์ที่ใช้ได้อยู่` : 'ยังไม่มีลิงก์';
  } catch (e) { /* ไม่ต้องรบกวน */ }
}

(async function init() {
  // ต้องมาก่อน needsAuth(): คนที่เพิ่งเปิดลิงก์แชร์ยังไม่มีคุกกี้อะไรเลย ถ้าไม่เช็คตรงนี้
  // จะเจอหน้าล็อกอินแทนหน้ายืนยัน (BACKLOG #16)
  const pendingShare = pendingShareToken();
  await refreshConfig();
  if (pendingShare) return showShareConfirm(pendingShare);
  if (needsAuth()) { showAuth(); return; }

  await refresh();
  // คนถือลิงก์แชร์เปิดได้แค่การประชุมนั้น พาไปเลยไม่ต้องผ่านรายการ
  if (state.share) return openMeeting(state.share.meeting_id);
  if (location.hash) applyHash();
  // บนมือถือหน้าแรกต้องเป็น "รายการ" ไม่ใช่กระโดดเข้าการประชุมล่าสุดทันที (เดสก์ท็อปเห็นทั้งสอง
  // ฝั่งพร้อมกันอยู่แล้ว การเปิดอันล่าสุดให้เลยจึงยังสมเหตุสมผลเฉพาะจอกว้าง)
  else if (state.meetings.length && !isMobile()) openMeeting(state.meetings[0].id);
  else showHome();
})();
