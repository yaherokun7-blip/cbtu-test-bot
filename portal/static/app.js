const $ = (id) => document.getElementById(id);
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const state = { page: 1, batch: null, request: 0, importing: false };
const labels = { pending: 'รอยืนยัน', processing: 'กำลังยืนยัน', verified: 'ยืนยันแล้ว', error: 'แจกยศไม่สำเร็จ', departed: 'ไม่มีสิทธิ์ใน Discord แล้ว' };
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const number = (value) => new Intl.NumberFormat('th-TH').format(value);
let toastTimer;
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 5500); }
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'X-CSRF-Token': csrf, ...options.headers}});
  if (response.status === 401) { window.location.assign('/'); throw new Error('กรุณาเข้าสู่ระบบใหม่'); }
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'คำขอไม่สำเร็จ กรุณาลองใหม่');
  return data;
}
async function loadRoster() {
  const request = ++state.request;
  try {
    const params = new URLSearchParams({q: $('search').value, course: $('course').value, status: $('status').value, page: state.page});
    const data = await api('/api/roster?' + params);
    if (request !== state.request) return;
    $('load-error').hidden = true;
    const counts = data.counts;
    $('stat-total').textContent = number(Object.values(counts).reduce((sum, count) => sum + count, 0));
    $('stat-verified').textContent = number(counts.verified || 0);
    $('stat-pending').textContent = number((counts.pending || 0) + (counts.processing || 0));
    $('stat-error').textContent = number((counts.error || 0) + (counts.departed || 0));
    $('people-count').textContent = `จากผู้เรียน ${number(data.learner_count)} คน`;
    $('result-count').textContent = number(data.total);
    $('bot-status').textContent = data.bot.online ? '● บอตเชื่อมต่อแล้ว' : 'บอตยังไม่เชื่อมต่อ';
    $('bot-status').classList.toggle('online', data.bot.online);
    $('updated-label').textContent = `อัปเดต ${new Date().toLocaleTimeString('th-TH', {hour:'2-digit', minute:'2-digit'})}`;
    const selectedCourse = $('course').value;
    $('course').innerHTML = '<option value="">ทุกหลักสูตร</option>' + data.courses.map((course) => `<option value="${esc(course)}">${esc(course)}</option>`).join('');
    $('course').value = selectedCourse;
    $('roster-body').innerHTML = data.rows.length ? data.rows.map((row) => `<tr>
      <td><div class="learner-cell"><span class="learner-avatar" aria-hidden="true">${esc([...row.full_name][0])}</span><div><div class="learner-name">${esc(row.full_name)}</div><div class="learner-id">${esc(row.student_id)}</div></div></div></td>
      <td><span class="course-label">${esc(row.course)}</span></td><td><span class="status-pill ${esc(row.status)}" title="${esc(row.last_error || labels[row.status])}">${esc(labels[row.status] || row.status)}</span></td>
      <td class="discord-id">${row.discord_id ? esc(row.discord_id) : '—'}</td><td class="date">${row.verified_at ? new Date(row.verified_at * 1000).toLocaleDateString('th-TH', {day:'numeric',month:'short',year:'2-digit'}) : '—'}</td></tr>`).join('') : `<tr><td colspan="5" class="empty-state"><strong>${data.learner_count ? 'ไม่พบรายชื่อที่ตรงกับตัวกรอง' : 'เริ่มต้นด้วยรายชื่อผู้เรียนของคุณ'}</strong>${data.learner_count ? 'ลองเปลี่ยนคำค้นหาหรือเลือกหลักสูตรอื่น' : 'อัปโหลด Excel หรือ CSV เพื่อตรวจรายการและสร้างรหัสยืนยันรายบุคคล'}</td></tr>`;
    $('page-number').textContent = state.page;
    $('previous').disabled = state.page <= 1;
    $('next').disabled = state.page * data.page_size >= data.total;
    $('range').textContent = data.total ? `${number((state.page - 1) * data.page_size + 1)}–${number(Math.min(state.page * data.page_size, data.total))} จาก ${number(data.total)} รายการ` : 'ยังไม่มีรายการ';
  } catch (error) { if (request === state.request) { $('load-error').textContent = error.message; $('load-error').hidden = false; } }
}
let searchTimer;
$('search').addEventListener('input', () => {clearTimeout(searchTimer); searchTimer = setTimeout(() => {state.page = 1; loadRoster();}, 250);});
['course', 'status'].forEach(id => $(id).addEventListener('change', () => {state.page = 1; loadRoster();}));
$('previous').onclick = () => {state.page--; loadRoster();};
$('next').onclick = () => {state.page++; loadRoster();};
$('logout').onclick = async () => {try {await api('/auth/logout', {method:'POST'}); window.location.assign('/');} catch(error) {toast(error.message);}};
function resetImport() {state.batch = null; $('file').value = ''; ['import-progress','import-error','import-preview'].forEach(id => $(id).hidden = true); $('commit-import').disabled = true;}
document.querySelectorAll('[data-open-import]').forEach(button => button.onclick = () => {resetImport(); $('import-dialog').showModal();});
function closeImport() {if (!state.importing) $('import-dialog').close();}
$('close-import').onclick = closeImport; $('cancel-import').onclick = closeImport;
$('import-dialog').addEventListener('cancel', event => {if (state.importing) event.preventDefault();});
async function previewFile(file) {
  if (!file || state.importing) return;
  state.batch = null; $('commit-import').disabled = true; $('import-preview').hidden = true; $('import-error').hidden = true;
  if (!/\.(csv|xlsx)$/i.test(file.name) || file.size > 2 * 1024 * 1024) { $('import-error').textContent = 'เลือกไฟล์ .csv หรือ .xlsx ขนาดไม่เกิน 2 MB'; $('import-error').hidden = false; return; }
  state.importing = true; $('file').disabled = true;
  $('import-progress').textContent = 'กำลังตรวจสอบรายชื่อ…'; $('import-progress').hidden = false;
  try {
    const data = await api('/api/import/preview?filename=' + encodeURIComponent(file.name), {method:'POST', body:file, headers:{'Content-Type':'application/octet-stream'}});
    state.batch = data.batch_id;
    $('file-name').textContent = data.filename;
    $('import-summary').textContent = `เพิ่มใหม่ ${number(data.new_count)} รายการ · ข้ามรายการเดิม ${number(data.skipped)} · ต้องแก้ไข ${number(data.error_count)} แถว`;
    $('preview-body').innerHTML = data.rows.map(row => `<tr><td>${row.row}</td><td>${esc(row.student_id)}</td><td>${esc(row.full_name)}</td><td>${esc(row.course)}</td></tr>`).join('');
    $('import-preview').hidden = false;
    if (data.error_count) { $('import-error').textContent = 'กรุณาแก้ไฟล์แล้วอัปโหลดใหม่ก่อนบันทึก\n' + data.errors.map(error => `แถว ${error.row}: ${error.message}`).join('\n'); $('import-error').hidden = false; }
    $('commit-import').disabled = !data.batch_id;
  } catch(error) { $('import-error').textContent = error.message; $('import-error').hidden = false; }
  finally {state.importing = false; $('file').disabled = false; $('import-progress').hidden = true;}
}
$('file').addEventListener('change', event => previewFile(event.target.files[0]));
['dragenter','dragover'].forEach(name => $('dropzone').addEventListener(name, event => {event.preventDefault(); $('dropzone').classList.add('dragover');}));
['dragleave','drop'].forEach(name => $('dropzone').addEventListener(name, event => {event.preventDefault(); $('dropzone').classList.remove('dragover');}));
$('dropzone').addEventListener('drop', event => {if (event.dataTransfer.files.length !== 1) {toast('เลือกครั้งละหนึ่งไฟล์'); return;} previewFile(event.dataTransfer.files[0]);});
$('commit-import').onclick = async () => {
  if (!state.batch || state.importing) return;
  state.importing = true; $('commit-import').disabled = true; $('commit-import').textContent = 'กำลังบันทึก…';
  try { const result = await api('/api/import/commit', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({batch_id:state.batch})}); state.batch = null; $('import-dialog').close(); toast(`เพิ่มแล้ว ${number(result.added)} รายการ · ดาวน์โหลดรหัสเพื่อส่งให้ผู้เรียนได้เลย`); state.page = 1; await loadRoster(); }
  catch(error) { $('import-error').textContent = error.message; $('import-error').hidden = false; }
  finally {state.importing = false; $('commit-import').textContent = 'ยืนยันนำเข้า'; $('commit-import').disabled = !state.batch;}
};
loadRoster();
setInterval(() => {if (!document.hidden && !$('import-dialog').open) loadRoster();}, 30000);
