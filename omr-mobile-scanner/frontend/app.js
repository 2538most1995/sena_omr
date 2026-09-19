const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];

const APP_BASE = (() => {
  const source = document.currentScript?.src || new URL('assets/app.js', window.location.href).href;
  const path = new URL(source, window.location.href).pathname;
  return path.replace(/\/assets\/app\.js$/, '').replace(/\/$/, '');
})();

function appUrl(path) {
  const suffix = String(path || '').startsWith('/') ? String(path || '') : `/${path}`;
  return `${APP_BASE}${suffix}` || '/';
}

function readJson(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key) || JSON.stringify(fallback)); }
  catch { return fallback; }
}

function normaliseCode(value) {
  return String(value || '').trim().toUpperCase().replace(/[\s-]+/g, '');
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[character]);
}

function loadSubjectSetups() {
  const setups = readJson('omr-subject-setups', {});
  const oldConfig = readJson('omr-config', {});
  const oldKey = readJson('omr-key', {});
  if (!Object.keys(setups).length && oldConfig.subjectCode) {
    setups[normaliseCode(oldConfig.subjectCode)] = { ...oldConfig, answerKey: oldKey };
    localStorage.setItem('omr-subject-setups', JSON.stringify(setups));
  }
  return setups;
}

const savedConfig = readJson('omr-config', {
  term: '1/2569', subjectCode: '', paperSubjectCode: '', subjectName: '', schoolCode: '', groupId: '', groupName: '',
});

const state = {
  side: 'front', stream: null, cameraReady: false, fileBlob: null, front: null, back: null,
  answers: {}, answerKey: {}, config: savedConfig, subjects: [], groups: [], students: [],
  subjectSetups: loadSubjectSetups(), validation: { ok: false }, loadingSubjects: false,
  reportRows: [], reportSubjectCode: '', reportLoading: false, resolvedStudentCode: '',
};

function currentTerm() {
  return $('#termInput')?.value.trim() || state.config.term || '1/2569';
}

function toast(message) {
  const element = $('#toast');
  element.textContent = message;
  element.classList.add('show');
  setTimeout(() => element.classList.remove('show'), 3200);
}

function histories() { return readJson('omr-history', []); }
function setHistories(items) { localStorage.setItem('omr-history', JSON.stringify(items.slice(0, 1000))); }

function saveConfig(config) {
  localStorage.setItem('omr-config', JSON.stringify(config));
  state.config = config;
}

function subjectSetup(code) {
  return state.subjectSetups[normaliseCode(code)] || null;
}

function saveSubjectSetup(setup) {
  state.subjectSetups[normaliseCode(setup.subjectCode)] = setup;
  localStorage.setItem('omr-subject-setups', JSON.stringify(state.subjectSetups));
  localStorage.setItem('omr-key', JSON.stringify(setup.answerKey));
}

function replaceServerAnswerKeys(rows, term) {
  state.subjectSetups = Object.fromEntries(
    Object.entries(state.subjectSetups).filter(([, setup]) => setup.term && setup.term !== term),
  );
  (rows || []).forEach(row => {
    const setup = {
      term: row.term, subjectCode: row.subject_code, subjectName: row.subject_name || row.subject_code,
      paperSubjectCode: row.paper_subject_code || row.subject_code,
      schoolCode: row.school_code, groupId: '', groupName: '', answerKey: row.answers || {},
    };
    state.subjectSetups[normaliseCode(setup.subjectCode)] = setup;
  });
  localStorage.setItem('omr-subject-setups', JSON.stringify(state.subjectSetups));
}

async function apiFetch(url, options) {
  const response = await fetch(appUrl(url), options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `API ตอบกลับ HTTP ${response.status}`);
  return data;
}

async function ping() {
  try {
    const data = await apiFetch('/api/health');
    $('#serverStatus').textContent = data.data_source === 'unconfigured' ? 'ระบบพร้อม • ยังไม่เชื่อมข้อมูล' : 'ระบบพร้อม • เชื่อมข้อมูลแล้ว';
    $('#answerKeyStorage').textContent = data.storage === 'mysql' ? 'MySQL กลาง' : 'ฐานข้อมูลทดสอบ';
    $('.status-pill').classList.add('online');
  } catch {
    $('#serverStatus').textContent = 'เชื่อมต่อเซิร์ฟเวอร์ไม่ได้';
  }
}

$$('.nav-item').forEach(button => {
  button.onclick = () => {
    $$('.nav-item').forEach(item => item.classList.remove('active'));
    button.classList.add('active');
    $$('.view').forEach(view => view.classList.remove('active'));
    $('#view-' + button.dataset.view).classList.add('active');
    const labels = {
      scan: ['สแกนกระดาษคำตอบ', 'เลือกวิชา แล้วระบบจะค้นหากลุ่มเรียนจากรหัสนักศึกษาให้อัตโนมัติ'],
      key: ['ตั้งค่าการตรวจ', 'เฉลยแต่ละวิชาถูกแยกเก็บอิสระและเรียกใช้โดยอัตโนมัติ'],
      report: ['รายงานการตรวจ', 'ติดตามนักศึกษาที่ตรวจแล้วและยังไม่ตรวจตามรายวิชาและกลุ่มเรียน'],
    };
    $('#pageTitle').textContent = labels[button.dataset.view][0];
    $('#pageSubtitle').textContent = labels[button.dataset.view][1];
    if (button.dataset.view === 'report') loadReportDashboard();
  };
});

function setSide(side) {
  if (side === 'back' && requiredPageCount() === 1) return toast('วิชานี้ตรวจเฉพาะหน้า 1-50');
  state.side = side;
  $('#frontTab').classList.toggle('active', side === 'front');
  $('#backTab').classList.toggle('active', side === 'back');
  $('#scanSideTitle').textContent = side === 'front' ? '1. ถ่ายด้านหน้า' : '2. ถ่ายด้านหลัง';
  resetCapture();
}

$('#frontTab').onclick = () => setSide('front');
$('#backTab').onclick = () => setSide('back');

function hasScanSetup() {
  return Boolean(state.config.subjectCode && Object.keys(state.answerKey).length);
}

function pageCountForAnswerKey(answerKey) {
  const questions = Object.keys(answerKey || {}).map(Number).filter(Number.isFinite);
  return questions.length && Math.max(...questions) <= 50 ? 1 : 2;
}

function requiredPageCount() {
  return pageCountForAnswerKey(state.answerKey);
}

function hasRequiredPages() {
  return Boolean(state.front && (requiredPageCount() === 1 || state.back));
}

function updateScanControls() {
  const ready = hasScanSetup();
  const pages = requiredPageCount();
  $('#requiredSides').textContent = pages;
  $('#readTotal').textContent = pages === 1 ? 50 : 100;
  $('#doneSides').textContent = (state.front ? 1 : 0) + (pages === 2 && state.back ? 1 : 0);
  $('#backTab').disabled = pages === 1;
  $('#backTab').title = pages === 1 ? 'วิชานี้ใช้เฉพาะหน้า 1-50' : 'ข้อ 51-100';
  if (pages === 1 && state.side === 'back') setSide('front');
  $('#openCameraBtn').disabled = !ready;
  $('#fileInput').disabled = !ready;
  $('#scanBtn').disabled = !(ready && state.fileBlob);
  if (!state.fileBlob && !state.stream) {
    $('#qualityBadge').textContent = ready ? 'พร้อมถ่ายภาพ' : 'เลือกวิชาและบันทึกเฉลยก่อน';
    $('#qualityBadge').className = 'quality-badge';
  }
}

async function openCamera() {
  if (!hasScanSetup()) return toast('กรุณาเลือกวิชาและบันทึกเฉลยก่อน');
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    return toast('เบราว์เซอร์นี้เปิดกล้องไม่ได้ กรุณาใช้ HTTPS หรือเลือกรูปจากเครื่อง');
  }
  try {
    stopCamera();
    $('#qualityBadge').textContent = 'กำลังเปิดและปรับโฟกัสกล้อง';
    const attempts = [
      { facingMode: { exact: 'environment' }, width: { ideal: 3840 }, height: { ideal: 2160 } },
      { facingMode: { ideal: 'environment' }, width: { ideal: 2560 }, height: { ideal: 1920 } },
      { facingMode: { ideal: 'environment' } },
      true,
    ];
    let lastError;
    for (const videoConstraints of attempts) {
      try {
        state.stream = await navigator.mediaDevices.getUserMedia({ video: videoConstraints, audio: false });
        break;
      } catch (error) { lastError = error; }
    }
    if (!state.stream) throw lastError || new Error('Camera unavailable');

    const video = $('#video');
    video.srcObject = state.stream;
    const track = state.stream.getVideoTracks()[0];
    const capabilities = track?.getCapabilities?.() || {};
    const advanced = {};
    if (capabilities.focusMode?.includes('continuous')) advanced.focusMode = 'continuous';
    if (capabilities.exposureMode?.includes('continuous')) advanced.exposureMode = 'continuous';
    if (capabilities.whiteBalanceMode?.includes('continuous')) advanced.whiteBalanceMode = 'continuous';
    if (Object.keys(advanced).length) await track.applyConstraints({ advanced: [advanced] }).catch(() => {});

    await new Promise((resolve, reject) => {
      if (video.readyState >= 2 && video.videoWidth) return resolve();
      const timer = setTimeout(() => reject(new Error('Camera start timeout')), 8000);
      video.addEventListener('loadedmetadata', () => { clearTimeout(timer); resolve(); }, { once: true });
      video.addEventListener('error', () => { clearTimeout(timer); reject(new Error('Camera video error')); }, { once: true });
    });
    await video.play();
    state.cameraReady = true;
    $('#cameraFrame').classList.add('has-video');
    $('#cameraFrame').classList.remove('has-preview');
    $('#openCameraBtn').classList.add('hidden');
    $('#captureBtn').classList.remove('hidden');
    $('#captureBtn').disabled = false;
    $('#retakeBtn').classList.add('hidden');
    $('#qualityBadge').textContent = 'พร้อมถ่าย • ระบบจะตรวจให้อัตโนมัติ';
  } catch (error) {
    stopCamera();
    const messages = {
      NotAllowedError: 'ยังไม่ได้อนุญาตใช้กล้อง กรุณาเปิดสิทธิ์กล้องในเบราว์เซอร์',
      NotFoundError: 'ไม่พบกล้องบนอุปกรณ์นี้',
      NotReadableError: 'กล้องกำลังถูกแอปอื่นใช้งาน กรุณาปิดแอปนั้นแล้วลองใหม่',
      OverconstrainedError: 'กล้องไม่รองรับค่าที่ขอ กรุณาลองใหม่หรือเลือกรูปจากเครื่อง',
      SecurityError: 'ต้องเปิดเว็บไซต์ผ่าน HTTPS จึงจะใช้กล้องได้',
    };
    toast(messages[error?.name] || 'เปิดกล้องไม่ได้ กรุณาลองใหม่หรือเลือกรูปจากเครื่อง');
  }
}

function stopCamera() {
  if (state.stream) state.stream.getTracks().forEach(track => track.stop());
  state.stream = null;
  state.cameraReady = false;
  const video = $('#video');
  if (video) video.srcObject = null;
}

$('#openCameraBtn').onclick = openCamera;

async function capture() {
  const video = $('#video');
  if (!state.cameraReady || video.readyState < 2 || !video.videoWidth) {
    return toast('กล้องยังปรับภาพไม่เสร็จ กรุณารอสักครู่แล้วถ่ายใหม่');
  }
  $('#captureBtn').disabled = true;
  $('#qualityBadge').textContent = 'กำลังบันทึกภาพความละเอียดสูง';
  const canvas = $('#captureCanvas');
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext('2d').drawImage(video, 0, 0);
  const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .94));
  if (!blob) {
    $('#captureBtn').disabled = false;
    return toast('บันทึกภาพจากกล้องไม่สำเร็จ กรุณาลองใหม่');
  }
  stopCamera();
  setPreviewBlob(blob, true);
}

$('#captureBtn').onclick = capture;

function setPreviewBlob(blob, autoScan = false) {
  state.fileBlob = blob;
  $('#preview').src = URL.createObjectURL(blob);
  $('#cameraFrame').classList.remove('has-video');
  $('#cameraFrame').classList.add('has-preview');
  $('#captureBtn').classList.add('hidden');
  $('#openCameraBtn').classList.add('hidden');
  $('#retakeBtn').classList.remove('hidden');
  $('#qualityBadge').textContent = autoScan ? 'กำลังวิเคราะห์อัตโนมัติ' : 'พร้อมวิเคราะห์';
  $('#qualityBadge').className = 'quality-badge good';
  updateScanControls();
  if (autoScan) setTimeout(() => $('#scanBtn').click(), 0);
}

$('#fileInput').onchange = event => {
  const file = event.target.files?.[0];
  if (file) setPreviewBlob(file, true);
};

function resetCapture() {
  stopCamera();
  state.fileBlob = null;
  $('#cameraFrame').classList.remove('has-video', 'has-preview');
  $('#openCameraBtn').classList.remove('hidden');
  $('#captureBtn').classList.add('hidden');
  $('#retakeBtn').classList.add('hidden');
  $('#qualityBadge').className = 'quality-badge';
  $('.guide-label').textContent = 'วางกระดาษให้เต็มกรอบ • เห็นขอบกระดาษครบ 4 ด้าน';
  $('#fileInput').value = '';
  updateScanControls();
}

$('#retakeBtn').onclick = resetCapture;

async function downscaleBlob(blob, maxWidth = 4200) {
  try {
    const bitmap = await createImageBitmap(blob);
    const scale = Math.min(1, maxWidth / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close?.();
    return await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .92));
  } catch { return blob; }
}

$('#scanBtn').onclick = async () => {
  if (!state.fileBlob || !hasScanSetup()) return;
  const button = $('#scanBtn');
  button.disabled = true;
  button.innerHTML = '<span>กำลังประมวลผล…</span>';
  $('#qualityBadge').textContent = 'กำลังอ่านรหัสและคำตอบ';
  $('#qualityBadge').className = 'quality-badge';
  try {
    const blob = await downscaleBlob(state.fileBlob);
    const body = new FormData();
    body.append('image', blob, 'scan.jpg');
    const data = await apiFetch('/api/scan?side=' + state.side, { method: 'POST', body });
    if (state.side === 'front') {
      state.back = null;
      state.answers = {};
    }
    state[state.side] = data;
    data.answers.forEach(answer => { state.answers[answer.question] = answer; });
    if (state.side === 'front') await resolveStudentGroup(data.metadata?.candidate_id?.value || '');
    renderResults(data);
    renderAllAnswers();
    $('#debugImage').src = 'data:image/jpeg;base64,' + data.debug_image_base64;
    if (state.side === 'front' && !state.back) {
      if (state.validation.ok) {
        if (requiredPageCount() === 1) {
          toast(`พบกลุ่ม ${state.config.groupName || state.config.groupId} แล้ว พร้อมตรวจและบันทึกคะแนน`);
        } else {
          setTimeout(() => { toast(`พบกลุ่ม ${state.config.groupName || state.config.groupId} แล้ว ต่อไปถ่ายด้านหลัง`); setSide('back'); }, 600);
        }
      } else {
        toast('ข้อมูลบนกระดาษไม่ผ่านการตรวจสอบ จึงยังไม่ตรวจคะแนน');
      }
    }
  } catch (error) {
    toast(error.message || 'สแกนไม่สำเร็จ');
    $('#qualityBadge').textContent = 'กรุณาลองใหม่';
    $('#qualityBadge').className = 'quality-badge bad';
  } finally {
    button.innerHTML = '<span class="scan-icon">⌁</span><span>วิเคราะห์กระดาษคำตอบ</span>';
    updateScanControls();
  }
};

function setCheck(id, expected, actual, customMatch) {
  const element = $(id);
  element.classList.remove('pass', 'fail');
  const expectedCode = normaliseCode(expected);
  const actualCode = normaliseCode(actual);
  const complete = actualCode && !actualCode.includes('?');
  const matches = customMatch === undefined
    ? Boolean(expectedCode && complete && expectedCode === actualCode)
    : Boolean(complete && customMatch);
  element.classList.add(matches ? 'pass' : 'fail');
  if (!expectedCode) element.querySelector('b').textContent = 'ยังไม่ได้ตั้งค่า';
  else if (!complete) element.querySelector('b').textContent = `อ่านไม่ครบ (${actual || '-'})`;
  else if (matches) element.querySelector('b').textContent = `ถูกต้อง (${actual})`;
  else element.querySelector('b').textContent = customMatch === undefined
    ? `คาด ${expected} / อ่าน ${actual}` : `ไม่พบ ${actual} ในกลุ่ม`;
  return matches;
}

function validatePaper() {
  if (!state.front) {
    ['#checkStudent', '#checkSubject', '#checkSchool'].forEach(id => {
      const element = $(id);
      element.classList.remove('pass', 'fail');
      element.querySelector('b').textContent = hasScanSetup() ? 'รอสแกน' : 'ยังไม่ได้ตั้งค่า';
    });
    state.validation = { ok: false };
    $('#validationPanel').classList.remove('pass', 'fail');
    $('#validationSummary').textContent = hasScanSetup()
      ? 'รอสแกนด้านหน้าเพื่อค้นหากลุ่มและยืนยันข้อมูล' : 'กรุณาเลือกวิชาและบันทึกเฉลย';
    updateSaveButton();
    return false;
  }
  const metadata = state.front.metadata || {};
  const candidate = state.resolvedStudentCode || metadata.candidate_id?.value || '';
  const subject = metadata.subject_code?.value || '';
  const school = metadata.school_code?.value || '';
  const rosterCodes = new Set(state.students.map(student => normaliseCode(student.code)));
  const studentOk = setCheck('#checkStudent', state.config.groupId || 'AUTO', candidate, rosterCodes.has(normaliseCode(candidate)));
  const subjectOk = setCheck('#checkSubject', state.config.paperSubjectCode || state.config.subjectCode, subject);
  const schoolOk = setCheck('#checkSchool', state.config.schoolCode, school);
  state.validation = { ok: studentOk && subjectOk && schoolOk, studentOk, subjectOk, schoolOk };
  const panel = $('#validationPanel');
  panel.classList.remove('pass', 'fail');
  panel.classList.add(state.validation.ok ? 'pass' : 'fail');
  $('#validationSummary').textContent = state.validation.ok
    ? `ยืนยันข้อมูลแล้ว กลุ่ม ${state.config.groupName || state.config.groupId}`
    : 'ไม่พบผู้เรียน หรือข้อมูลวิชาและสถานศึกษาไม่ตรง';
  updateSaveButton();
  return state.validation.ok;
}

function renderResults(data) {
  const metadata = data.metadata || {};
  if (metadata.candidate_id) {
    const observed = metadata.candidate_id.value || 'อ่านไม่ครบ';
    $('#candidateId').textContent = state.resolvedStudentCode && normaliseCode(state.resolvedStudentCode) !== normaliseCode(observed)
      ? `${observed} -> ${state.resolvedStudentCode}` : observed;
  }
  if (metadata.school_code) $('#schoolCode').textContent = metadata.school_code.value || 'อ่านไม่พบ';
  if (metadata.subject_code) $('#paperSubjectCode').textContent = metadata.subject_code.value || 'อ่านไม่พบ';
  $('#quadStatus').textContent = data.quality.quad_found && data.quality.timing_confidence >= .55
    ? `จัดแนวแล้ว • timing ${data.quality.timing_bar_count || 0} จุด`
    : data.quality.quad_found ? 'พบขอบ • timing ไม่ครบ' : 'ไม่พบขอบกระดาษ';
  $('#avgConfidence').textContent = Math.round((data.quality.average_confidence || 0) * 100) + '%';
  const uncertain = Object.values(state.answers).filter(answer => answer.needs_review).length;
  $('#uncertainCount').textContent = uncertain;
  $('#readCount').textContent = Object.keys(state.answers).length;
  $('#doneSides').textContent = (state.front ? 1 : 0) + (requiredPageCount() === 2 && state.back ? 1 : 0);
  $('#resultChip').textContent = state.side === 'front' ? 'อ่านด้านหน้าแล้ว' : 'อ่านด้านหลังแล้ว';
  $('#reviewStatus').textContent = uncertain ? 'มีข้อให้ตรวจทาน' : 'พร้อมบันทึก';
  const issueLabels = {
    low_resolution: 'ภาพเล็กเกินไป', document_edges_missing: 'ไม่พบขอบกระดาษ',
    excessive_perspective: 'มุมเอียงมากเกินไป', image_blur: 'ภาพไม่คมชัด',
    too_dark: 'ภาพมืดเกินไป', too_bright: 'ภาพสว่างเกินไป', glare: 'มีแสงสะท้อน',
    shadow_clipping: 'เงาดำบังแบบฟอร์ม', uneven_lighting: 'แสงไม่สม่ำเสมอ',
    timing_marks_missing: 'ไม่พบ timing marks ครบ', registration_marks_missing: 'ไม่พบ registration marks',
    side_mismatch: 'ถ่ายกระดาษผิดด้าน',
    answer_grid_fallback: 'ใช้ตำแหน่งสำรอง', low_read_confidence: 'ความเชื่อมั่นต่ำ',
    metadata_unreliable: 'รหัสบนกระดาษอ่านไม่ครบ',
  };
  const blockingIssues = (data.quality.issues || []);
  $('#qualityBadge').textContent = blockingIssues.length
    ? issueLabels[blockingIssues[0]] || 'ควรถ่ายใหม่' : 'ตรวจจับสำเร็จ';
  $('#qualityBadge').className = 'quality-badge ' + (data.quality.capture_ok ? 'good' : 'bad');
  if (!data.quality.capture_ok) {
    toast(`ภาพยังไม่พร้อม: ${blockingIssues.map(issue => issueLabels[issue] || issue).join(', ')}`);
  }
  validatePaper();
  renderScore();
}

function renderAllAnswers() {
  const grid = $('#answerGrid');
  const lastQuestion = requiredPageCount() === 1 ? 50 : 100;
  $('#answerRangeLabel').textContent = `คำตอบ 1-${lastQuestion}`;
  grid.innerHTML = '';
  for (let question = 1; question <= lastQuestion; question++) {
    const answer = state.answers[question];
    const element = document.createElement('button');
    element.className = 'answer ' + (!answer ? 'blank' : answer.needs_review ? 'warn' : answer.status === 'ok' ? 'ok' : 'blank');
    element.dataset.q = question;
    element.title = answer?.needs_review ? `ควรตรวจทานข้อ ${question}` : answer?.status === 'blank' ? 'ไม่ได้ตอบ' : '';
    element.innerHTML = `<div class="q">${question}</div><div class="v">${answer?.choice || (answer?.status === 'multiple' ? '!' : '-')}</div>`;
    element.onclick = () => editAnswer(question);
    grid.appendChild(element);
  }
  renderReviewStatus();
  renderScore();
}

function renderReviewStatus() {
  const uncertain = Object.values(state.answers).filter(answer => answer.needs_review).length;
  $('#uncertainCount').textContent = uncertain;
  if (!state.front) $('#reviewStatus').textContent = 'รอสแกน';
  else if (!hasRequiredPages()) $('#reviewStatus').textContent = `รอสแกนครบ ${requiredPageCount()} หน้า`;
  else if (uncertain) $('#reviewStatus').textContent = `ต้องตรวจทาน ${uncertain} ข้อ`;
  else $('#reviewStatus').textContent = 'พร้อมบันทึก';
}

function editAnswer(question) {
  const oldValue = state.answers[question]?.choice || '';
  const value = prompt(`ข้อ ${question}: กรอก A, B, C, D หรือเว้นว่าง`, oldValue);
  if (value === null) return;
  const choice = value.trim().toUpperCase();
  if (choice && !['A', 'B', 'C', 'D'].includes(choice)) return toast('กรุณากรอก A, B, C หรือ D');
  state.answers[question] = {
    question, choice: choice || null, status: choice ? 'ok' : 'blank',
    confidence: 1, needs_review: false, review_reason: null, manual: true,
  };
  renderAllAnswers();
}

function calculateScore() {
  let score = 0;
  for (const [question, key] of Object.entries(state.answerKey)) {
    if (state.answers[question]?.choice === key) score++;
  }
  return { score, total: Object.keys(state.answerKey).length };
}

function renderScore() {
  const { score, total } = calculateScore();
  if (!total) $('#scoreText').textContent = 'ยังไม่มีเฉลยสำหรับวิชานี้';
  else if (!state.validation.ok) $('#scoreText').textContent = 'รอตรวจข้อมูล';
  else if (!hasRequiredPages()) $('#scoreText').textContent = `รอสแกนครบ ${requiredPageCount()} หน้า`;
  else $('#scoreText').textContent = `${score}/${total}`;
  $$('.answer').forEach(element => {
    element.classList.remove('correct', 'wrong');
    const question = element.dataset.q;
    if (state.validation.ok && state.answerKey[question] && state.answers[question]?.choice) {
      element.classList.add(state.answers[question].choice === state.answerKey[question] ? 'correct' : 'wrong');
    }
  });
  updateSaveButton();
}

function updateSaveButton() {
  const hasPendingReview = Object.values(state.answers).some(answer => answer.needs_review);
  const capturesAreUsable = Boolean(state.front?.quality?.capture_ok
    && (requiredPageCount() === 1 || state.back?.quality?.capture_ok));
  $('#saveBtn').disabled = !(hasRequiredPages() && state.validation.ok
    && Object.keys(state.answerKey).length && !hasPendingReview && capturesAreUsable);
}

function parseKey(text) {
  const trimmed = text.trim();
  if (!trimmed) return {};
  try {
    const json = JSON.parse(trimmed);
    const source = json.answers || json.answer_key || json;
    if (Array.isArray(source)) {
      return Object.fromEntries(source.slice(0, 100).map((choice, index) => [index + 1, String(choice).trim().toUpperCase()]));
    }
    if (source && typeof source === 'object') {
      const result = {};
      Object.entries(source).forEach(([question, choice]) => {
        const answer = String(choice).trim().toUpperCase();
        if (+question >= 1 && +question <= 100 && ['A', 'B', 'C', 'D'].includes(answer)) result[+question] = answer;
      });
      if (Object.keys(result).length) return result;
    }
  } catch { /* Continue with text/CSV parsing. */ }
  const normalised = trimmed.toUpperCase().replace(/[ก]/g, 'A').replace(/[ข]/g, 'B').replace(/[ค]/g, 'C').replace(/[ง]/g, 'D');
  const result = {};
  const pairs = [...normalised.matchAll(/(\d{1,3})\s*[,;:]?\s*[:=\-]\s*([ABCD])/g)];
  if (pairs.length) {
    pairs.forEach(match => { if (+match[1] >= 1 && +match[1] <= 100) result[+match[1]] = match[2]; });
    return result;
  }
  normalised.split(/\r?\n/).map(row => row.split(/[,;\t]/).map(cell => cell.trim())).forEach(cells => {
    if (+cells[0] >= 1 && +cells[0] <= 100 && ['A', 'B', 'C', 'D'].includes(cells[1])) result[+cells[0]] = cells[1];
  });
  if (Object.keys(result).length) return result;
  [...normalised.replace(/[^ABCD]/g, '')].slice(0, 100).forEach((choice, index) => { result[index + 1] = choice; });
  return result;
}

function answerKeyIssues(key, sourceText = '') {
  const questions = Object.keys(key || {}).map(Number).filter(Number.isFinite).sort((a, b) => a - b);
  const highest = questions.at(-1) || 0;
  const present = new Set(questions);
  const missing = Array.from({ length: highest }, (_, index) => index + 1).filter(question => !present.has(question));
  const explicitNumbers = [...String(sourceText).matchAll(/(\d{1,3})\s*[,;:]?\s*[:=\-]\s*[ABCDกขคง]/gi)]
    .map(match => Number(match[1]));
  const seen = new Set();
  const duplicates = [...new Set(explicitNumbers.filter(question => seen.has(question) || !seen.add(question)))];
  return { highest, missing, duplicates };
}

function answerKeyIssueMessage(issues) {
  const messages = [];
  if (issues.missing.length) messages.push(`ขาดข้อ ${issues.missing.join(', ')}`);
  if (issues.duplicates.length) messages.push(`เลขข้อซ้ำ ${issues.duplicates.join(', ')}`);
  return messages.join(' • ');
}

function setStatus(selector, message, isError = false) {
  $(selector).textContent = message;
  $(selector).classList.toggle('error', isError);
}

function populateSubjectSelect(select, subjects, selectedCode = '') {
  select.innerHTML = subjects.length ? '<option value="">เลือกรายวิชา</option>' : '<option value="">ไม่พบรายวิชา</option>';
  subjects.forEach(subject => {
    const option = document.createElement('option');
    option.value = subject.code;
    const paperCode = subject.paper_code || subject.code;
    option.textContent = `${paperCode} - ${subject.name}`;
    option.dataset.name = subject.name;
    option.dataset.paperCode = paperCode;
    option.dataset.schoolCode = subject.school_code || '';
    select.appendChild(option);
  });
  select.disabled = !subjects.length;
  const selected = [...select.options].find(option => normaliseCode(option.value) === normaliseCode(selectedCode));
  if (selected) select.value = selected.value;
}

function populateSubjects(subjects, selectedCode = '') {
  populateSubjectSelect($('#subjectCode'), subjects, selectedCode);
  populateSubjectSelect($('#scanSubjectCode'), subjects, selectedCode);
  populateSubjectSelect($('#reportSubject'), subjects, selectedCode);
}

function clearDetectedGroup(message = 'ระบบจะค้นหาให้อัตโนมัติ') {
  state.students = [];
  state.resolvedStudentCode = '';
  saveConfig({ ...state.config, groupId: '', groupName: '' });
  $('#detectedGroup').textContent = message;
  $('.auto-group').classList.remove('resolved');
  setStatus('#detectedGroupStatus', 'อ่านรหัสนักศึกษาจากด้านหน้าเพื่อค้นหากลุ่มเรียน');
}

async function resolveStudentGroup(studentCode) {
  clearDetectedGroup('กำลังค้นหากลุ่มเรียน...');
  const code = String(studentCode || '').trim();
  state.resolvedStudentCode = '';
  if (!code) {
    setStatus('#detectedGroupStatus', 'ไม่พบรหัสนักศึกษาในภาพ', true);
    return false;
  }
  try {
    const data = await apiFetch(`/api/subjects/${encodeURIComponent(state.config.subjectCode)}/resolve-student?term=${encodeURIComponent(currentTerm())}&observed_code=${encodeURIComponent(code)}`);
    const group = data.group || {};
    const student = data.student || { code };
    state.resolvedStudentCode = String(student.code || code);
    state.students = [student];
    saveConfig({ ...state.config, groupId: String(group.id || ''), groupName: group.name || group.code || '' });
    $('#detectedGroup').textContent = group.name || group.code || group.id;
    $('.auto-group').classList.add('resolved');
    if (data.resolution === 'recovered') {
      $('#candidateId').textContent = `${code} -> ${student.code}`;
      setStatus('#detectedGroupStatus', `ยืนยันรหัส ${student.code} จากรายชื่อ: ${student.name || student.code}`);
    } else {
      setStatus('#detectedGroupStatus', `${student.name || student.code} อยู่ในกลุ่ม ${group.name || group.code || group.id}`);
    }
    return true;
  } catch (error) {
    setStatus('#detectedGroupStatus', error.message || 'ไม่พบกลุ่มเรียนของนักศึกษาคนนี้', true);
    return false;
  }
}

function renderKeyForSubject(code) {
  const setup = subjectSetup(code);
  state.answerKey = setup?.answerKey || {};
  $('#expectedSchoolCode').value = setup?.schoolCode || state.subjects.find(item => normaliseCode(item.code) === normaliseCode(code))?.school_code || '';
  $('#keyInput').value = Object.entries(state.answerKey).map(([question, answer]) => `${question}:${answer}`).join(', ');
  const count = Object.keys(state.answerKey).length;
  const issueMessage = answerKeyIssueMessage(answerKeyIssues(state.answerKey));
  setStatus('#keyCount', issueMessage || (count
    ? `มีเฉลย ${count} ข้อ • ตรวจ ${pageCountForAnswerKey(state.answerKey)} หน้า`
    : `ยังไม่มีเฉลยสำหรับ ${code || 'วิชานี้'}`), Boolean(issueMessage));
  return setup;
}

async function loadAllSubjects() {
  if (state.loadingSubjects) return;
  state.loadingSubjects = true;
  $('#loadSubjectsBtn').disabled = true;
  $('#scanLoadSubjectsBtn').disabled = true;
  setStatus('#subjectStatus', 'กำลังโหลดรายวิชาทั้งหมดจาก SDL_school…');
  setStatus('#scanSubjectStatus', 'กำลังโหลดรายวิชาทั้งหมด…');
  try {
    const data = await apiFetch(`/api/subjects?term=${encodeURIComponent(currentTerm())}&per_page=100`);
    state.subjects = data.subjects || [];
    $('#termInput').value = data.term || currentTerm();
    $('#scanTermLabel').textContent = data.term || currentTerm();
    let keyStorageMessage = '';
    try {
      const keyData = await apiFetch(`/api/answer-keys?term=${encodeURIComponent(data.term || currentTerm())}`);
      replaceServerAnswerKeys(keyData.answer_keys, data.term || currentTerm());
      keyStorageMessage = ` • เฉลยกลาง ${keyData.total || 0} วิชา`;
      $('#answerKeyStorage').textContent = keyData.storage === 'mysql' ? 'MySQL กลาง' : 'ฐานข้อมูลทดสอบ';
    } catch (error) {
      keyStorageMessage = ' • ใช้เฉลยสำรองในเครื่อง';
    }
    const selectedCode = state.config.subjectCode || state.subjects[0]?.code || '';
    populateSubjects(state.subjects, selectedCode);
    const message = state.subjects.length
      ? `พบ ${state.subjects.length} รายวิชา • ภาคเรียน ${data.term}${keyStorageMessage}`
      : 'ไม่พบรายวิชาในภาคเรียนนี้';
    setStatus('#subjectStatus', message, !state.subjects.length);
    setStatus('#scanSubjectStatus', message, !state.subjects.length);
    if (selectedCode) await selectSubject(selectedCode);
  } catch (error) {
    const savedSubjects = Object.values(state.subjectSetups).map(setup => ({
      code: setup.subjectCode, paper_code: setup.paperSubjectCode || setup.subjectCode,
      name: setup.subjectName || setup.subjectCode, school_code: setup.schoolCode || '',
    }));
    state.subjects = savedSubjects;
    populateSubjects(savedSubjects, state.config.subjectCode);
    setStatus('#subjectStatus', error.message || 'เชื่อมต่อ SDL_school ไม่สำเร็จ', true);
    setStatus('#scanSubjectStatus', 'ใช้ได้เฉพาะวิชาที่เคยบันทึก กรุณาตรวจการตั้งค่า API', true);
    if (state.config.subjectCode) await selectSubject(state.config.subjectCode, { loadRemoteGroups: false });
  } finally {
    state.loadingSubjects = false;
    $('#loadSubjectsBtn').disabled = false;
    $('#scanLoadSubjectsBtn').disabled = false;
    updateScanControls();
  }
}

async function selectSubject(code) {
  if (!code) {
    state.answerKey = {};
    saveConfig({
      term: currentTerm(), subjectCode: '', subjectName: '', paperSubjectCode: '',
      schoolCode: '', groupId: '', groupName: '',
    });
    $('#subjectCode').value = '';
    $('#scanSubjectCode').value = '';
    resetForNextStudent();
    return;
  }
  const subject = state.subjects.find(item => normaliseCode(item.code) === normaliseCode(code)) || { code, name: code };
  const setup = renderKeyForSubject(subject.code);
  saveConfig({
    term: currentTerm(), subjectCode: subject.code, subjectName: subject.name,
    paperSubjectCode: subject.paper_code || subject.code,
    schoolCode: setup?.schoolCode || subject.school_code || '',
    groupId: '', groupName: '',
  });
  $('#subjectCode').value = subject.code;
  $('#scanSubjectCode').value = subject.code;
  if ([...$('#reportSubject').options].some(option => option.value === subject.code)) {
    $('#reportSubject').value = subject.code;
    state.reportSubjectCode = '';
  }
  resetForNextStudent();
}

$('#loadSubjectsBtn').onclick = loadAllSubjects;
$('#scanLoadSubjectsBtn').onclick = loadAllSubjects;
$('#subjectCode').onchange = event => selectSubject(event.target.value);
$('#scanSubjectCode').onchange = event => selectSubject(event.target.value);

$('#keyFileInput').onchange = async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    const key = parseKey(await file.text());
    if (!Object.keys(key).length) throw new Error('ไม่พบเฉลยในไฟล์');
    $('#keyInput').value = Object.entries(key).map(([question, answer]) => `${question}:${answer}`).join(', ');
    const issueMessage = answerKeyIssueMessage(answerKeyIssues(key));
    setStatus('#keyCount', issueMessage || `นำเข้าแล้ว ${Object.keys(key).length} ข้อ กดบันทึกการตั้งค่า`, Boolean(issueMessage));
    if (issueMessage) throw new Error(`เฉลยไม่ต่อเนื่อง: ${issueMessage}`);
  } catch (error) { toast(error.message || 'อ่านไฟล์เฉลยไม่สำเร็จ'); }
};

$('#downloadKeyTemplateBtn').onclick = () => {
  const content = 'question,answer\n' + Array.from({ length: 100 }, (_, index) => `${index + 1},`).join('\n');
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob(['\ufeff' + content], { type: 'text/csv;charset=utf-8' }));
  link.download = `answer-key-${state.config.subjectCode || 'template'}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
};

$('#saveKeyBtn').onclick = async () => {
  const select = $('#subjectCode');
  const option = select.options[select.selectedIndex];
  const sourceText = $('#keyInput').value;
  const key = parseKey(sourceText);
  const issueMessage = answerKeyIssueMessage(answerKeyIssues(key, sourceText));
  const setup = {
    term: currentTerm(), subjectCode: select.value.trim(), subjectName: option?.dataset.name || '',
    paperSubjectCode: option?.dataset.paperCode || select.value.trim(),
    schoolCode: $('#expectedSchoolCode').value.trim(), groupId: '', groupName: '', answerKey: key,
  };
  if (!setup.subjectCode || !setup.schoolCode) return toast('กรุณาเลือกรายวิชาและระบุรหัสสถานศึกษา');
  if (!Object.keys(key).length) return toast('กรุณากรอกหรือนำเข้าเฉลยก่อนบันทึก');
  if (issueMessage) {
    setStatus('#keyCount', `บันทึกไม่ได้ • ${issueMessage}`, true);
    $('#keyInput').focus();
    return toast(`กรุณาแก้เฉลย: ${issueMessage}`);
  }
  const button = $('#saveKeyBtn');
  button.disabled = true;
  button.textContent = 'กำลังบันทึก...';
  try {
    const result = await apiFetch(`/api/answer-keys/${encodeURIComponent(setup.subjectCode)}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        subject_code: setup.subjectCode, paper_subject_code: setup.paperSubjectCode,
        subject_name: setup.subjectName, term: setup.term, school_code: setup.schoolCode,
        answers: setup.answerKey,
      }),
    });
    $('#answerKeyStorage').textContent = result.storage === 'mysql' ? 'MySQL กลาง' : 'ฐานข้อมูลทดสอบ';
  } catch (error) {
    button.disabled = false;
    button.textContent = 'บันทึกการตั้งค่า';
    return toast(error.message || 'บันทึกเฉลยลงฐานข้อมูลไม่สำเร็จ');
  }
  saveSubjectSetup(setup);
  const { answerKey, ...config } = setup;
  saveConfig(config);
  state.answerKey = key;
  if ([...$('#reportSubject').options].some(reportOption => reportOption.value === setup.subjectCode)) {
    $('#reportSubject').value = setup.subjectCode;
    state.reportSubjectCode = '';
  }
  $('#keyCount').textContent = `บันทึก ${Object.keys(key).length} ข้อ • ตรวจ ${pageCountForAnswerKey(key)} หน้า • รหัสบนกระดาษ ${setup.paperSubjectCode}`;
  resetForNextStudent();
  button.disabled = false;
  button.textContent = 'บันทึกการตั้งค่า';
  toast(`บันทึกวิชา ${setup.paperSubjectCode} ลงฐานข้อมูลแล้ว ใช้กระดาษ ${pageCountForAnswerKey(key)} หน้า`);
};

$('#clearKeyBtn').onclick = async () => {
  if (!state.config.subjectCode) return;
  try {
    await apiFetch(`/api/answer-keys/${encodeURIComponent(state.config.subjectCode)}?term=${encodeURIComponent(currentTerm())}`, { method: 'DELETE' });
  } catch (error) {
    return toast(error.message || 'ลบเฉลยจากฐานข้อมูลไม่สำเร็จ');
  }
  $('#keyInput').value = '';
  state.answerKey = {};
  const setup = subjectSetup(state.config.subjectCode);
  if (setup) saveSubjectSetup({ ...setup, answerKey: {} });
  $('#keyCount').textContent = `ยังไม่มีเฉลยสำหรับ ${state.config.subjectCode}`;
  resetForNextStudent();
};

function buildHistoryItem() {
  const { score, total } = calculateScore();
  const candidate = state.resolvedStudentCode || state.front.metadata.candidate_id.value;
  return {
    id: Date.now(), time: new Date().toISOString(), candidate,
    candidateName: state.students.find(student => normaliseCode(student.code) === normaliseCode(candidate))?.name || '',
    school: state.front.metadata.school_code.value,
    subject: state.config.subjectCode, subjectName: state.config.subjectName,
    groupId: state.config.groupId, groupName: state.config.groupName,
    term: state.config.term, score, total,
    uncertain: Object.values(state.answers).filter(answer => answer.needs_review).length,
    blank: Object.values(state.answers).filter(answer => answer.status === 'blank').length,
    answers: Object.fromEntries(Object.entries(state.answers).map(([question, answer]) => [question, answer.choice])),
    scanQuality: {
      front: { quality: state.front?.quality || {}, audit: state.front?.audit || {} },
      back: { quality: state.back?.quality || {}, audit: state.back?.audit || {} },
    },
    reviewCount: (state.front?.quality?.needs_review || 0) + (state.back?.quality?.needs_review || 0),
    correctedCount: Object.values(state.answers).filter(answer => answer.manual).length,
    syncStatus: 'pending', syncError: '',
  };
}

async function syncHistoryItem(item) {
  const payload = {
    student_code: item.candidate, subject_code: item.subject, class_group_id: String(item.groupId),
    term: item.term, score: item.score, max_score: item.total, answers: item.answers,
    scan_quality: item.scanQuality, review_count: item.reviewCount,
    corrected_count: item.correctedCount, checked_at: item.time,
  };
  try {
    const result = await apiFetch('/api/scores', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    item.syncStatus = result.delivery === 'synced' ? 'synced' : 'stored';
    item.syncError = '';
    item.syncedAt = item.syncStatus === 'synced' ? new Date().toISOString() : '';
    return item.syncStatus;
  } catch (error) {
    item.syncStatus = 'failed';
    item.syncError = error.message || 'ส่งคะแนนไม่สำเร็จ';
    return '';
  }
}

function upsertHistory(item) {
  const items = histories();
  const duplicate = items.findIndex(old => normaliseCode(old.candidate) === normaliseCode(item.candidate)
    && normaliseCode(old.subject) === normaliseCode(item.subject)
    && String(old.groupId) === String(item.groupId)
    && old.term === item.term);
  if (duplicate >= 0) items.splice(duplicate, 1);
  items.unshift(item);
  setHistories(items);
}

$('#saveBtn').onclick = async () => {
  if (!state.validation.ok) return toast('ไม่สามารถบันทึกได้ เพราะไม่พบผู้เรียนหรือข้อมูลบนกระดาษไม่ตรง');
  const button = $('#saveBtn');
  button.disabled = true;
  button.textContent = 'กำลังบันทึกและส่งคะแนน…';
  const item = buildHistoryItem();
  upsertHistory(item);
  const delivery = await syncHistoryItem(item);
  upsertHistory(item);
  button.textContent = 'บันทึกผลการตรวจ';
  toast(delivery === 'synced' ? 'บันทึกและส่งคะแนนเข้า SDL_school แล้ว'
    : delivery === 'stored' ? 'บันทึกคะแนนเรียบร้อยแล้ว' : `เก็บผลในเบราว์เซอร์แล้ว แต่บันทึกเซิร์ฟเวอร์ไม่สำเร็จ: ${item.syncError}`);
  if (delivery) {
    state.reportSubjectCode = '';
    state.reportRows = [];
    resetForNextStudent();
  }
  else updateSaveButton();
};

function resetForNextStudent() {
  state.front = null;
  state.back = null;
  state.answers = {};
  state.validation = { ok: false };
  ['candidateId', 'schoolCode', 'paperSubjectCode'].forEach(id => $('#' + id).textContent = '-');
  $('#doneSides').textContent = '0';
  $('#readCount').textContent = '0';
  $('#uncertainCount').textContent = '0';
  $('#resultChip').textContent = 'ยังไม่สแกน';
  $('#quadStatus').textContent = '-';
  $('#avgConfidence').textContent = '-';
  $('#reviewStatus').textContent = 'รอสแกน';
  $('#debugImage').removeAttribute('src');
  $('#debugImage').closest('details').open = false;
  clearDetectedGroup();
  setSide('front');
  renderAllAnswers();
  validatePaper();
}

function populateReportGroups(rows, selectedId = '') {
  const select = $('#reportGroup');
  const groups = new Map();
  rows.forEach(row => groups.set(String(row.group_id), row.group_name || row.group_id));
  select.innerHTML = '<option value="">ทุกกลุ่มเรียน</option>';
  [...groups.entries()].sort((a, b) => a[1].localeCompare(b[1], 'th')).forEach(([id, name]) => {
    const option = document.createElement('option');
    option.value = id;
    option.textContent = `${id} - ${name}`;
    select.appendChild(option);
  });
  if (selectedId && groups.has(String(selectedId))) select.value = String(selectedId);
}

function formatReportDate(value) {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('th-TH', { dateStyle: 'short', timeStyle: 'short' });
}

function renderReport() {
  const groupId = $('#reportGroup').value;
  const status = $('#reportStatus').value;
  const search = $('#reportSearch').value.trim().toLocaleLowerCase('th');
  const groupRows = state.reportRows.filter(row => !groupId || String(row.group_id) === groupId);
  const checked = groupRows.filter(row => row.checked).length;
  const groups = new Set(groupRows.map(row => row.group_id));
  $('#reportTotal').textContent = groupRows.length;
  $('#reportChecked').textContent = checked;
  $('#reportPending').textContent = groupRows.length - checked;
  $('#reportRate').textContent = `${groupRows.length ? Math.round(checked * 100 / groupRows.length) : 0}%`;
  $('#reportGroupCount').textContent = `${groups.size} กลุ่มเรียน`;

  const rows = groupRows.filter(row => {
    if (status === 'checked' && !row.checked) return false;
    if (status === 'pending' && row.checked) return false;
    if (!search) return true;
    return `${row.student_code} ${row.student_name}`.toLocaleLowerCase('th').includes(search);
  });
  $('#reportTableBody').innerHTML = rows.map(row => {
    const result = row.checked && row.max_score ? `${row.score}/${row.max_score}` : '-';
    const statusHtml = row.checked
      ? '<span class="report-status checked">ตรวจแล้ว</span>'
      : '<span class="report-status pending">ยังไม่ตรวจ</span>';
    const actionHtml = row.checked
      ? `<button class="cancel-score" data-student="${escapeHtml(row.student_code)}" data-group="${escapeHtml(row.group_id)}" data-name="${escapeHtml(row.student_name || row.student_code)}">ยกเลิกผลตรวจ</button>`
      : '<span class="no-action">-</span>';
    return `<tr>
      <td data-label="รหัสนักศึกษา">${escapeHtml(row.student_code)}</td>
      <td data-label="ชื่อ - นามสกุล"><div class="report-name"><b>${escapeHtml(row.student_name || row.student_code)}</b></div></td>
      <td data-label="กลุ่มเรียน"><div class="report-name"><b>${escapeHtml(row.group_name || row.group_id)}</b><small>${escapeHtml(row.group_id)}</small></div></td>
      <td data-label="สถานะ">${statusHtml}</td>
      <td data-label="คะแนน">${escapeHtml(result)}</td>
      <td data-label="ตรวจล่าสุด">${escapeHtml(formatReportDate(row.checked_at))}</td>
      <td data-label="จัดการ">${actionHtml}</td>
    </tr>`;
  }).join('');
  $('#reportTableWrap').classList.toggle('hidden', !rows.length);
  $('#reportEmpty').classList.toggle('hidden', Boolean(rows.length));
}

$('#reportTableBody').onclick = async event => {
  const button = event.target.closest('.cancel-score');
  if (!button) return;
  const studentCode = button.dataset.student;
  const groupId = button.dataset.group;
  const studentName = button.dataset.name || studentCode;
  if (!confirm(`ยกเลิกผลตรวจของ ${studentName} (${studentCode}) เพื่อให้ตรวจใหม่ใช่หรือไม่?`)) return;
  button.disabled = true;
  button.textContent = 'กำลังยกเลิก…';
  try {
    await apiFetch(`/api/scores/${encodeURIComponent(state.reportSubjectCode)}/${encodeURIComponent(studentCode)}?term=${encodeURIComponent(currentTerm())}&class_group_id=${encodeURIComponent(groupId)}`, { method: 'DELETE' });
    setHistories(histories().filter(item => !(normaliseCode(item.candidate) === normaliseCode(studentCode)
      && normaliseCode(item.subject) === normaliseCode(state.reportSubjectCode)
      && String(item.groupId) === String(groupId) && item.term === currentTerm())));
    toast(`ยกเลิกผลตรวจของ ${studentName} แล้ว สามารถตรวจใหม่ได้`);
    state.reportSubjectCode = '';
    await loadReportDashboard(true);
  } catch (error) {
    button.disabled = false;
    button.textContent = 'ยกเลิกผลตรวจ';
    toast(error.message || 'ยกเลิกผลตรวจไม่สำเร็จ');
  }
};

async function loadReportDashboard(force = false) {
  const subjectCode = $('#reportSubject').value || state.config.subjectCode || state.subjects[0]?.code || '';
  if (!subjectCode || state.reportLoading) return;
  if (!force && state.reportSubjectCode === subjectCode && state.reportRows.length) {
    renderReport();
    return;
  }
  state.reportLoading = true;
  $('#reportLoading').classList.remove('hidden');
  $('#reportError').classList.add('hidden');
  $('#reportTableWrap').classList.add('hidden');
  $('#reportEmpty').classList.add('hidden');
  try {
    const data = await apiFetch(`/api/reports/students?term=${encodeURIComponent(currentTerm())}&subject_code=${encodeURIComponent(subjectCode)}`);
    const selectedGroup = $('#reportGroup').value;
    state.reportRows = data.rows || [];
    state.reportSubjectCode = subjectCode;
    $('#reportSubject').value = subjectCode;
    populateReportGroups(state.reportRows, selectedGroup);
    renderReport();
  } catch (error) {
    state.reportRows = [];
    $('#reportError').textContent = error.message || 'โหลดรายงานไม่สำเร็จ';
    $('#reportError').classList.remove('hidden');
  } finally {
    state.reportLoading = false;
    $('#reportLoading').classList.add('hidden');
  }
}

$('#reportRefreshBtn').onclick = () => loadReportDashboard(true);
$('#reportSubject').onchange = () => {
  state.reportSubjectCode = '';
  state.reportRows = [];
  $('#reportGroup').value = '';
  loadReportDashboard(true);
};
$('#reportGroup').onchange = renderReport;
$('#reportStatus').onchange = renderReport;
$('#reportSearch').oninput = renderReport;

function initialiseSetup() {
  $('#termInput').value = state.config.term || '1/2569';
  $('#scanTermLabel').textContent = state.config.term || '1/2569';
  if (state.config.subjectCode) {
    const saved = subjectSetup(state.config.subjectCode) || { ...state.config, answerKey: readJson('omr-key', {}) };
    state.answerKey = saved.answerKey || {};
  }
  renderAllAnswers();
  validatePaper();
  updateScanControls();
  ping();
  loadAllSubjects();
}

initialiseSetup();

if ('serviceWorker' in navigator) navigator.serviceWorker.register(appUrl('/sw.js')).catch(() => {});
window.addEventListener('beforeunload', stopCamera);
