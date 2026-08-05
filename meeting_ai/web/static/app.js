/* meeting_ai web UI — vanilla JS ไม่มี dependency */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const LIVE_MS = 15000;   // ความยาวคลิปที่ส่งไปถอดเสียงสดแต่ละรอบ

const state = {
  meetings: [],
  jobs: [],
  current: null,       // id ของการประชุมที่เปิดอยู่
  meeting: null,       // ข้อมูลเต็มของการประชุมที่เปิดอยู่
  query: '',
  polling: null,
  config: {},
};

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
    return `<div class="job ${failed ? 'err' : ''}">
      <div class="jt">${esc(j.title)}</div>
      <div class="js">${esc(failed ? j.error : j.step)}</div>
      ${failed ? '' : `<div class="bar"><div style="width:${pct}%"></div></div>`}
    </div>`;
  }).join('');
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
  ensurePolling();
}

async function refreshConfig() {
  try {
    state.config = await api('/api/config');
    const s = state.config.stats || {};
    $('#stats').textContent = `${s.count || 0} การประชุม · รวม ${fmtDuration(s.total_duration)}`;
    if (!state.config.llm_ready) {
      banner('ยังไม่ได้ตั้ง LLM_API_KEY ใน .env — ถอดเสียงได้ แต่จะสรุปไม่ได้');
    }
  } catch (e) { /* ไม่สำคัญพอจะรบกวนผู้ใช้ */ }
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
}

function formValues() {
  return {
    title: ($('#f-title')?.value || '').trim(),
    lang: $('#f-lang')?.value || 'th',
    template: $('#f-template')?.value || 'general',
    diarize: !!$('#f-diarize')?.checked,
    num_speakers: parseInt($('#f-speakers')?.value || '0', 10),
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
    source,
  }));

  for (const [name, { blob, ext }] of Object.entries(tracks)) {
    await api(`/api/meetings/${draft.id}/tracks/${name}?ext=${encodeURIComponent(ext)}`,
      { method: 'POST', body: blob });
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
  streams: [], ctx: null, dest: null,
  liveRecorder: null, liveTimer: null, liveBusy: false, liveText: [],
  timer: null, raf: null, started: 0, peak: 0, recording: false,
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

async function startRecording() {
  const wantTab = $('#c-tab').checked;
  const wantMic = $('#c-mic').checked;
  const wantLive = $('#c-live').checked;
  if (!wantTab && !wantMic) { banner('ต้องเลือกอย่างน้อยหนึ่งแหล่งเสียง'); return; }
  banner('');

  const mime = pickMime();
  try {
    const ctx = new AudioContext();
    const dest = ctx.createMediaStreamDestination();
    rec.ctx = ctx;
    rec.dest = dest;
    rec.streams = [];
    rec.recorders = {};
    rec.liveText = [];

    if (wantTab) {
      // Chrome/Edge จะเสนอ "แชร์เสียงแท็บ" ได้ต่อเมื่อขอ video มาด้วย — ขอเฟรมเรตต่ำสุดแล้วไม่ใช้ภาพ
      const ds = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: 1 },
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      });
      rec.streams.push(ds);
      if (!ds.getAudioTracks().length) {
        cleanupRecording();
        banner('แท็บที่เลือกไม่ได้แชร์เสียงมา — ตอนเลือกแท็บต้องติ๊ก “แชร์เสียงแท็บ” ด้วย');
        return;
      }
      const tabOnly = new MediaStream(ds.getAudioTracks());
      ctx.createMediaStreamSource(tabOnly).connect(dest);
      rec.recorders.system = newRecorder(tabOnly, mime);
      ds.getVideoTracks().forEach((t) => { t.onended = () => stopRecording(); });
    }

    if (wantMic) {
      const ms = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: true, autoGainControl: true },
      });
      rec.streams.push(ms);
      ctx.createMediaStreamSource(ms).connect(dest);
      rec.recorders.mic = newRecorder(ms, mime);
    }

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
        warn.textContent = 'ยังไม่ได้ยินเสียงเลย — ตรวจว่าติ๊ก “แชร์เสียงแท็บ” และเสียงประชุมไม่ได้ปิดอยู่';
      }
    }, 500);

    const buf = new Uint8Array(analyser.fftSize);
    const tick = () => {
      analyser.getByteTimeDomainData(buf);
      let sum = 0;
      for (const v of buf) { const d = (v - 128) / 128; sum += d * d; }
      const level = Math.sqrt(sum / buf.length);
      rec.peak = Math.max(rec.peak, level);
      $('#meter-bar').style.width = `${Math.min(100, level * 320)}%`;
      rec.raf = requestAnimationFrame(tick);
    };
    tick();
  } catch (e) {
    cleanupRecording();
    banner(e.name === 'NotAllowedError'
      ? 'ไม่ได้รับอนุญาตให้เข้าถึงเสียง — กดอนุญาตในเบราว์เซอร์แล้วลองอีกครั้ง'
      : `เริ่มอัดไม่ได้: ${e.message}`);
  }
}

/** ถอดเสียงสด: ตัดคลิปสมบูรณ์ทุก LIVE_MS แล้วส่งไปถอดทีละชิ้น
    (chunk ของ MediaRecorder ชิ้นหลังๆ ถอดเดี่ยวๆ ไม่ได้เพราะ header อยู่ชิ้นแรก
     จึงต้องปิด recorder แล้วเปิดใหม่เพื่อให้ได้ไฟล์ที่สมบูรณ์ในตัวเอง) */
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
  rec.liveTimer = setTimeout(() => {
    if (r.state !== 'inactive') r.stop();
  }, LIVE_MS);
}

async function sendLive(blob, ext) {
  if (rec.liveBusy) return;   // ยังถอดชิ้นก่อนไม่เสร็จ ข้ามชิ้นนี้ไปดีกว่าให้กองคิว
  rec.liveBusy = true;
  const lang = $('#f-lang')?.value || 'th';
  try {
    const out = await api(`/api/live?ext=${ext}&lang=${encodeURIComponent(lang)}`,
      { method: 'POST', body: blob });
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

(async function init() {
  await refreshConfig();
  await refresh();
  if (location.hash) applyHash();
  else if (state.meetings.length) openMeeting(state.meetings[0].id);
  else showNew();
})();
