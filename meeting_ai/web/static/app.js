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

function banner(msg) {
  const el = $('#banner');
  if (!msg) { el.hidden = true; return; }
  el.hidden = false;
  el.textContent = msg;
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
    return `<div class="job ${failed ? 'err' : ''}">
      <div class="jt">${j.kind === 'bot' ? '🤖 ' : ''}${esc(j.title)}</div>
      <div class="js">${esc(failed ? j.error : j.step)}</div>
      ${failed ? '' : `<div class="bar"><div style="width:${pct}%"></div></div>`}
      ${canStop ? `<button class="btn btn-sm job-stop" data-job="${esc(j.id)}"
        type="button">ให้บอทออกจากห้องแล้วสรุป</button>` : ''}
    </div>`;
  }).join('');
  $$('#jobs .job-stop').forEach((b) => {
    b.onclick = () => { b.disabled = true; stopJob(b.dataset.job); };
  });
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

function renderWorkers() {
  const el = $('#workers');
  const ws = state.workers || [];
  if (!state.config.auth_required || !ws.length) { el.hidden = true; return; }
  el.hidden = false;

  const rows = ws.map((w) => {
    const cls = w.status === 'busy' ? 'busy' : (w.alive ? 'idle' : 'gone');
    const label = { busy: 'กำลังทำงาน', idle: 'ว่าง', gone: 'หลุดไป' }[cls];
    const detail = w.status === 'busy' && w.job_title
      ? esc(w.job_title)
      : (w.alive ? `เห็นล่าสุด ${fmtAgo(w.quiet_for)}` : `เงียบไป ${fmtAgo(w.quiet_for)}`);
    return `<div class="wk ${cls}">
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

function renderUserBox() {
  const box = $('#userbox');
  if (!state.config.auth_required) { box.hidden = true; return; }
  box.hidden = false;
  if (state.user) {
    box.innerHTML = `<span class="who">${esc(state.user.name || state.user.email)}</span>`
      + (isAdmin() ? '<button id="btn-invite" class="btn btn-sm" type="button">เชิญสมาชิก</button>' : '')
      + '<button id="btn-logout" class="btn btn-sm" type="button">ออกจากระบบ</button>';
    $('#btn-logout').onclick = async () => {
      await api('/api/auth/logout', { method: 'POST' }).catch(() => {});
      location.href = '/';
    };
    const inv = $('#btn-invite');
    if (inv) inv.onclick = inviteMember;
  } else if (state.share) {
    box.innerHTML = '<span class="who">เปิดจากลิงก์แชร์'
      + (state.share.can_edit ? ' (แก้ได้)' : ' (อ่านอย่างเดียว)') + '</span>';
  } else {
    box.innerHTML = '';
  }
  $('#btn-new').hidden = !canEdit() || !!state.share;
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

function ensurePolling() {
  const busy = state.jobs.some((j) => j.status === 'queued' || j.status === 'running');
  if (busy && !state.polling) state.polling = setInterval(pollJobs, 1500);
  else if (!busy && state.polling) { clearInterval(state.polling); state.polling = null; }
}

async function pollJobs() {
  let data;
  try { data = await api('/api/jobs'); } catch (e) { return; }

  const before = state.jobs.filter((j) => j.status === 'running' || j.status === 'queued');
  state.jobs = data.jobs || [];
  renderJobs();
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

function applyHash() {
  const h = location.hash;
  const m = h.match(/^#m\/([\w-]+)$/);
  if (m) openMeeting(m[1]);
  else showNew();
}

function showNew() {
  state.current = null;
  state.meeting = null;
  setHash('#new');
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
  sttSel.innerHTML = provs.map((p) => {
    const label = p.available ? p.label : `${p.label} — ใช้ไม่ได้`;
    return `<option value="${esc(p.key)}" ${p.available ? '' : 'disabled'}>${esc(label)}</option>`;
  }).join('');
  const preferred = provs.find((p) => p.key === state.config.stt_default && p.available)
    || provs.find((p) => p.available);
  if (preferred) sttSel.value = preferred.key;

  const showSttNote = () => {
    const p = provs.find((x) => x.key === sttSel.value);
    $('#stt-note').textContent = p ? (p.available ? p.note : p.why) : '';
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

  $('#btn-pick').onclick = () => $('#f-file').click();
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

  $('#btn-rec').onclick = startRecording;
  $('#btn-stop').onclick = stopRecording;
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
  $('#btn-bot').onclick = sendBot;
  $('#b-url').onkeydown = (e) => { if (e.key === 'Enter') sendBot(); };
}

async function sendBot() {
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
    $('#b-url').value = '';
    banner('ส่งบอทแล้ว — ไปกด "รับเข้าห้อง" (Admit) ในห้องประชุมด้วย');
  } catch (e) {
    banner(`ส่งบอทไม่สำเร็จ: ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

async function stopJob(id) {
  try {
    await api(`/api/jobs/${encodeURIComponent(id)}/stop`, { method: 'POST' });
    banner('สั่งให้บอทออกจากห้องแล้ว — รออีกไม่เกินสิบวินาทีแล้วจะเริ่มถอดเสียง');
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

function setupSources() {
  const saved = store.get(REC_MODE_KEY);
  const savedRadio = saved && $(`#rec-modes input[value="${saved}"]`);
  if (savedRadio) savedRadio.checked = true;

  // แชร์แท็บใช้ได้แค่บนเบราว์เซอร์เดสก์ท็อป — บนมือถือไม่มี API นี้เลย
  if (!navigator.mediaDevices?.getDisplayMedia) {
    const tab = $('#rec-modes input[value="tab"]');
    tab.disabled = true;
    tab.closest('.mode').title = 'เบราว์เซอร์นี้แชร์เสียงแท็บไม่ได้ (มือถือส่วนใหญ่ทำไม่ได้)';
    if (tab.checked) $('#rec-modes input[value="room"]').checked = true;
  }

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
const SILENT_WARN = {
  room: 'ยังไม่ได้ยินเสียงเลย — ตรวจว่าเลือกไมค์ถูกตัว ไมค์ไม่ได้ปิด (mute) และเสียงประชุมเปิดออกลำโพงอยู่',
  device: 'ยังไม่ได้ยินเสียงเลย — อุปกรณ์วนเสียงกลับมักเงียบถ้าเสียงระบบถูกปิด '
    + 'ลองเปิดเพลงทดสอบ หรือสลับไปโหมดไมค์เดียว',
  tab: 'ยังไม่ได้ยินเสียงเลย — ตรวจว่าติ๊ก “แชร์เสียงแท็บ” และเสียงประชุมไม่ได้ปิดอยู่',
};

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
};

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
    rec.started = Date.now();
    rec.recording = true;
    $('#rec-idle').hidden = true;
    $('#rec-live').hidden = false;
    $('#rec-warn').hidden = true;

    if (wantLive) {
      $('#live-wrap').hidden = false;
      $('#live-text').innerHTML = '<p class="muted">รอข้อความชุดแรก…</p>';
      $('#live-status').textContent = 'พรีวิว — ข้อความสุดท้ายจะแม่นกว่านี้';
      cycleLive(mime);
    }

    rec.timer = setInterval(() => {
      const sec = (Date.now() - rec.started) / 1000;
      $('#rec-time').textContent = fmtClock(sec);
      if (sec > 6 && rec.peak < 0.004) {
        const warn = $('#rec-warn');
        warn.hidden = false;
        warn.textContent = SILENT_WARN[rec.mode] || SILENT_WARN.room;
      }
    }, 500);

    const buf = new Uint8Array(analyser.fftSize);
    const tick = () => {
      analyser.getByteTimeDomainData(buf);
      let sum = 0;
      for (const v of buf) { const d = (v - 128) / 128; sum += d * d; }
      const level = Math.sqrt(sum / buf.length);
      rec.level = level;            // ตัวตัดคลิปสดใช้ค่านี้หาจังหวะเงียบ
      rec.peak = Math.max(rec.peak, level);
      $('#meter-bar').style.width = `${Math.min(100, level * 320)}%`;
      rec.raf = requestAnimationFrame(tick);
    };
    tick();
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
    if (st) st.textContent = `พรีวิวสดหยุดไป: ${e.message}`;
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
  }
}

/* ---------------- pane: รายละเอียด ---------------- */

const SPEAKER_CLASSES = 6;
const speakerClass = (name, all) => `spk-${(all.indexOf(name) % SPEAKER_CLASSES) + 1}`;

function renderTranscript(editing) {
  const m = state.meeting;
  const segs = m.segments_list || [];
  const all = m.speakers || [];
  if (!segs.length) {
    $('#d-transcript').innerHTML = '<p class="muted">(ไม่มีบทถอดเสียง)</p>';
    return;
  }
  $('#d-transcript').innerHTML = segs.map((s, i) => {
    const spk = s.speaker
      ? `<span class="tspk ${speakerClass(s.speaker, all)}" ${editing ? 'contenteditable="true" spellcheck="false"' : ''}>${esc(s.speaker)}</span>`
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
    `<button class="chip ${speakerClass(s, all)}" data-speaker="${esc(s)}" title="คลิกเพื่อเปลี่ยนชื่อ">${esc(s)}</button>`
  ).join('');
}

function renderSummaryView() {
  const m = state.meeting;
  const lang = $('#d-lang').value;
  const text = lang === 'orig' ? (m.summary || '') : ((m.translations || {})[lang] || '');
  const view = $('#d-summary');
  if (text) {
    view.innerHTML = renderMarkdown(text);
  } else if (lang === 'orig') {
    view.innerHTML = '<p class="muted">ยังไม่มีสรุป — กด “สรุปใหม่ด้วย AI”</p>';
  } else {
    view.innerHTML = '<p class="muted">ยังไม่ได้แปลเป็นภาษานี้ — กด “แปล”</p>';
  }
  $('#d-translate').disabled = lang === 'orig';
  $('#d-edit').disabled = lang !== 'orig';
}

function renderLangSelect() {
  const m = state.meeting;
  const done = m.translations || {};
  const langs = state.config.languages || {};
  const opts = ['<option value="orig">ต้นฉบับ</option>'];
  for (const [code, label] of Object.entries(langs)) {
    if (code === (m.language || 'th') && !done[code]) continue;
    opts.push(`<option value="${esc(code)}">${esc(label)}${done[code] ? ' ✓' : ''}</option>`);
  }
  $('#d-lang').innerHTML = opts.join('');
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
  renderList();

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
      const job = await api(`/api/meetings/${id}/resummarize`, { method: 'POST' });
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

  if (!sameMeeting) banner('');
}

/* ---------------- init ---------------- */

let searchTimer = null;
$('#search').oninput = (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value;
  searchTimer = setTimeout(() => { state.query = q; refresh(); }, 220);
};

$('#btn-new').onclick = showNew;

$('#list').onclick = (e) => {
  const li = e.target.closest('li[data-id]');
  if (li) openMeeting(li.dataset.id);
};

window.addEventListener('beforeunload', (e) => {
  if (rec.recording) { e.preventDefault(); e.returnValue = ''; }
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
  await refreshConfig();
  if (needsAuth()) { showAuth(); return; }

  await refresh();
  // คนถือลิงก์แชร์เปิดได้แค่การประชุมนั้น พาไปเลยไม่ต้องผ่านรายการ
  if (state.share) return openMeeting(state.share.meeting_id);
  if (location.hash) applyHash();
  else if (state.meetings.length) openMeeting(state.meetings[0].id);
  else showNew();
})();
