const $ = id => document.getElementById(id);
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let batch = null, busy = false;
const labels = {verified:'รับยศแล้ว',processing:'กำลังมอบยศ',error:'มอบยศไม่สำเร็จ',departed:'ออกหรือถูกถอดยศแล้ว'};
async function api(path, options={}) {
  const response = await fetch(path, {...options, headers:{'X-CSRF-Token':csrf,...options.headers}});
  if (response.status === 401) {location.assign('/'); throw Error('กรุณาเข้าสู่ระบบใหม่');}
  const data = await response.json();
  if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'คำขอไม่สำเร็จ กรุณาตรวจข้อมูล');
  return data;
}
async function refresh() {
  try {
    const {classes} = await api('/api/classes');
    $('error').hidden = true;
    $('total').textContent = classes.length;
    ['used','processing','remaining'].forEach(key => $(key).textContent = classes.reduce((sum,row) => sum + row[key],0));
    $('classes').innerHTML = classes.length ? classes.map(row => `<tr><td><div class="class-name">${esc(row.course)}</div><div class="class-role">${esc(row.role_name)} · ผู้ใช้กรอกชื่อเอง</div></td><td><span class="code-text">${esc(row.access_code)}</span></td><td>${row.used} / ${row.capacity}</td><td>${row.processing}</td><td>${row.remaining}</td><td><button class="button secondary" data-members="${esc(row.id)}">ดูสมาชิก</button></td></tr>`).join('') : '<tr><td colspan="6" class="empty-state"><strong>สร้างคลาสแรกของคุณ</strong>กำหนดโค้ด ยศ และจำนวนคน เช่น 30 คนต่อคลาส</td></tr>';
  } catch(error) {$('error').textContent = error.message; $('error').hidden=false;}
}
$('new-class').onclick = () => {$('create-error').textContent=''; $('create-dialog').showModal();};
$('close-create').onclick = () => {if (!busy) $('create-dialog').close();};
$('create-dialog').addEventListener('cancel', event => {if (busy) event.preventDefault();});
$('create-form').onsubmit = async event => {
  event.preventDefault(); if (busy) return; busy=true; $('save-class').disabled=true;
  const values = Object.fromEntries(new FormData(event.target)); values.capacity = Number(values.capacity);
  try {await api('/api/classes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)}); $('create-dialog').close(); event.target.reset(); await refresh();}
  catch(error) {$('create-error').textContent=error.message;}
  finally {busy=false; $('save-class').disabled=false;}
};
$('classes').onclick = async event => {
  const button = event.target.closest('[data-members]'); if (!button) return;
  $('members').innerHTML='<tr><td colspan="3">กำลังโหลด…</td></tr>'; $('members-dialog').showModal();
  try {const {members} = await api(`/api/classes/${encodeURIComponent(button.dataset.members)}/members`);
    $('members').innerHTML = members.length ? members.map(row => `<tr><td>${esc(row.full_name)}</td><td>${esc(row.discord_id)}</td><td><span class="status-pill ${esc(row.status)}" title="${esc(row.last_error)}">${esc(labels[row.status] || row.status)}</span></td></tr>`).join('') : '<tr><td colspan="3">ยังไม่มีคนใช้โค้ดคลาสนี้</td></tr>';
  } catch(error) {$('members').innerHTML=`<tr><td colspan="3">${esc(error.message)}</td></tr>`;}
};
$('close-members').onclick = () => $('members-dialog').close();
$('logout').onclick = async () => {try {await api('/auth/logout',{method:'POST'}); location.assign('/');} catch(error) {$('error').textContent=error.message; $('error').hidden=false;}};
$('file').onchange = async event => {
  batch=null; $('commit').disabled=true; $('import-error').textContent=''; $('import-preview').hidden=true;
  const file=event.target.files[0]; if (!file) return;
  if (file.size > 2*1024*1024) {$('import-error').textContent='ไฟล์ต้องไม่เกิน 2 MB'; return;}
  $('file').disabled=true; $('import-error').textContent='กำลังตรวจไฟล์…';
  try {const data=await api('/api/import/preview?filename='+encodeURIComponent(file.name),{method:'POST',body:file});
    $('import-preview').hidden=false; $('import-summary').textContent=`${data.rows.length} คลาส จากรายชื่อ ${data.count} รายการ`;
    $('import-details').innerHTML=data.rows.map(row=>`<p>${esc(row.course)} · ${row.capacity} คน · ยศ ${esc(row.role_name)}</p>`).join('');
    $('import-error').textContent=data.errors.map(row=>`แถว ${row.row}: ${row.message}`).join('\n'); batch=data.batch_id; $('commit').disabled=!batch;
  } catch(error) {$('import-error').textContent=error.message;} finally {$('file').disabled=false;}
};
$('commit').onclick = async () => {
  if (!batch) return; $('commit').disabled=true; $('file').disabled=true;
  try {const result=await api('/api/import/commit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({batch_id:batch})}); batch=null; $('import-summary').textContent=`สร้างแล้ว ${result.added} คลาส · อัปเดตรายชื่อในคลาสเดิม ${result.skipped}`; await refresh();}
  catch(error) {$('import-error').textContent=error.message;} finally {$('commit').disabled=!batch; $('file').disabled=false;}
};
refresh(); setInterval(()=>{if (!document.hidden && !busy) refresh();},30000);
