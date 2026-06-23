'use strict';
const $ = id => document.getElementById(id);
let DATA = {parents: [], module_labels: {material:'美术', image:'分镜', video:'视频'}, module_order:['material','image','video']};
let currentParent = null;
let currentModule = 'material';
let currentSource = 'creator'; // 默认显示制作端；reviewer=审核端
let mergeMode = false;        // 合并选择模式
let mergeSel = [];            // 已选中的 child_id，按点击顺序
// 来源归属：制作端 = 制作端上传 + 手动上传到数据中心；审核端 = 审核端上传
function srcMatch(c){
  const ct = c.client_type || '';
  if(currentSource === 'reviewer') return ct === 'reviewer';
  return ct !== 'reviewer'; // creator / data_center / 其它 都归到“制作端”视图
}
// 按当前来源统计某父项目各模块子项目数
function parentCountsBySource(p){
  const counts = {material:0, image:0, video:0};
  (p.children||[]).forEach(c=>{ if(srcMatch(c) && counts[c.module_type]!=null) counts[c.module_type]++; });
  return counts;
}
let pendingConflictToken = null;

function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function toast(msg){const t=$('toast');t.textContent=msg;t.classList.add('show');clearTimeout(t._t);t._t=setTimeout(()=>t.classList.remove('show'),2600)}
function fmtSize(n){n=Number(n||0);if(n<1024)return n+' B';if(n<1048576)return (n/1024).toFixed(1)+' KB';if(n<1073741824)return (n/1048576).toFixed(1)+' MB';return (n/1073741824).toFixed(2)+' GB'}

async function api(path, opts){
  const r = await fetch(path, opts);
  const text = await r.text();
  let data; try{data = text?JSON.parse(text):{}}catch(e){data = {error:text}}
  if(!r.ok && !data.conflict) throw new Error(data.error || ('HTTP '+r.status));
  return data;
}

async function loadProjects(){
  try{
    const r = await api('/api/projects');
    DATA = Object.assign(DATA, r);
    if(!currentParent && r.parents.length) currentParent = r.parents[0].parent_id;
    if(currentParent && !r.parents.some(p=>p.parent_id===currentParent)) currentParent = r.parents[0] ? r.parents[0].parent_id : null;
    render();
  }catch(e){ toast('读取项目失败：'+e.message); }
}

function currentParentItem(){ return DATA.parents.find(p=>p.parent_id===currentParent) || null; }

function render(){
  // 来源切换按钮高亮
  document.querySelectorAll('#sourceSwitch .src-btn').forEach(b=>{
    b.classList.toggle('active', b.getAttribute('data-src')===currentSource);
  });
  // 父项目列表（计数按当前来源）
  const pl = $('parentList');
  pl.innerHTML = DATA.parents.length ? DATA.parents.map(p=>{
    const c = parentCountsBySource(p);
    return `<div class="parent-card ${p.parent_id===currentParent?'active':''}" onclick="selectParent('${esc(p.parent_id)}')">
      <div class="pname">${esc(p.parent_name)}</div>
      <div class="pcount">美术 ${c.material||0} · 分镜 ${c.image||0} · 视频 ${c.video||0}</div>
      <div class="ptime">最近更新：${esc(p.updated_at||'-')}</div>
      <div class="pactions">
        <button class="btn small" onclick="event.stopPropagation();downloadParent('${esc(p.parent_id)}')">下载父项目</button>
        <button class="btn danger small" onclick="event.stopPropagation();deleteParent('${esc(p.parent_id)}','${esc(p.parent_name)}')">删除</button>
      </div>
    </div>`;
  }).join('') : '<div class="desc">暂无项目。制作端 / 审核端上传项目后会显示在这里。</div>';

  // 模块切换
  $('moduleList').innerHTML = DATA.module_order.map(mt=>
    `<button class="module-card ${mt===currentModule?'active':''}" onclick="switchModule('${mt}')">${esc(DATA.module_labels[mt]||mt)}</button>`
  ).join('');

  // 子项目列表
  const p = currentParentItem();
  $('currentParentTitle').innerHTML = '<b>当前父项目：</b>' + (p ? esc(p.parent_name) : '未选择');
  const children = p ? (p.children||[]).filter(c=>c.module_type===currentModule && srcMatch(c)) : [];
  $('moduleCount').textContent = (DATA.module_labels[currentModule]||currentModule) + ' ' + children.length;

  const cl = $('childList');
  if(!p){ cl.innerHTML = '<div class="empty-center">请选择左侧父项目。</div>'; return; }
  cl.innerHTML = children.length ? children.map(c=>{
    const gens = (c.generator_names && c.generator_names.length) ? c.generator_names.join('、') : (c.generator_name||'-');
    const thumb = c.has_preview
      ? `<img src="/api/preview/${esc(c.child_id)}" onerror="this.parentNode.innerHTML='<span class=&quot;noimg&quot;>无预览</span>'">`
      : '<span class="noimg">无预览</span>';
    const order = mergeSel.indexOf(c.child_id);
    const selBadge = (mergeMode && order>=0) ? `<span class="merge-badge">${order+1}</span>` : '';
    const cardClick = mergeMode ? `onclick="toggleMergePick('${esc(c.child_id)}')"` : '';
    return `<div class="child-card ${mergeMode?'merge-mode':''} ${order>=0?'merge-picked':''}" ${cardClick}>
      ${selBadge}
      <div class="child-thumb">${thumb}</div>
      <div class="child-main">
        <div class="child-title">${esc(c.child_name)}</div>
        <div class="child-meta">
          <span class="k">模块：</span>${esc(c.module_label)}　
          <span class="k">生成者：</span>${esc(gens)}<br>
          <span class="k">来源：</span>${esc(c.source_label||'-')}　
          <span class="k">上传时间：</span>${esc(c.uploaded_at||'-')}　
          <span class="k">大小：</span>${fmtSize(c.file_size)}
        </div>
      </div>
      <div class="child-actions" ${mergeMode?'style="display:none"':''}>
        <button class="btn small" onclick="downloadChild('${esc(c.child_id)}','${esc(c.child_name)}')">下载项目</button>
        <button class="btn ghost small" onclick="previewChild('${esc(c.child_id)}')">预览</button>
        <button class="btn danger small" onclick="deleteChild('${esc(c.child_id)}','${esc(c.child_name)}')">删除</button>
      </div>
    </div>`;
  }).join('') : `<div class="empty-center">该父项目在「${currentSource==='reviewer'?'审核端':'制作端'} · ${esc(DATA.module_labels[currentModule]||currentModule)}」下暂无子项目。</div>`;
}

function selectParent(pid){ currentParent = pid; render(); }
function switchModule(mt){ currentModule = mt; render(); }
function switchSource(src){ currentSource = src; render(); }

function toggleMergeMode(){
  mergeMode = !mergeMode; mergeSel = [];
  const tb = $('mergeToggleBtn'), rb = $('mergeRunBtn');
  if(mergeMode){ tb.textContent='取消合并'; tb.classList.remove('ghost'); rb.style.display=''; updateMergeRunBtn(); }
  else { tb.textContent='合并子项目'; tb.classList.add('ghost'); rb.style.display='none'; }
  render();
}
function toggleMergePick(cid){
  const i = mergeSel.indexOf(cid);
  if(i>=0) mergeSel.splice(i,1); else mergeSel.push(cid);
  updateMergeRunBtn(); render();
}
function updateMergeRunBtn(){ const rb=$('mergeRunBtn'); if(rb) rb.textContent='执行合并('+mergeSel.length+')'; }
async function runMerge(){
  if(mergeSel.length<2) return toast('请按顺序选择至少 2 个子项目');
  const p = currentParentItem();
  const first = (p&&p.children||[]).find(c=>c.child_id===mergeSel[0]);
  const defName = first ? (first.base_child_name||first.child_name) : '合并子项目';
  const name = prompt('合并后子项目名称（默认用第一个选中项目的名字）：', defName||'合并子项目');
  if(name===null) return;
  toast('正在合并 '+mergeSel.length+' 个子项目…');
  try{
    const r = await api('/api/merge', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({child_ids:mergeSel, target_name:(name||'').trim()})});
    toast('合并完成：'+((r.child&&r.child.child_name)||name));
    mergeMode=false; mergeSel=[];
    $('mergeToggleBtn').textContent='合并子项目'; $('mergeToggleBtn').classList.add('ghost'); $('mergeRunBtn').style.display='none';
    await loadProjects();
  }catch(e){ toast('合并失败：'+e.message); }
}

function downloadChild(cid){ window.location.href = '/api/download/child/' + encodeURIComponent(cid); }
function downloadParent(pid){ window.location.href = '/api/download/parent/' + encodeURIComponent(pid); }

function previewChild(cid){
  const img = $('previewImg');
  img.src = '/api/preview/' + encodeURIComponent(cid) + '?t=' + Date.now();
  img.onerror = ()=>{ toast('该子项目没有可用预览图'); $('previewModal').classList.remove('show'); };
  $('previewModal').classList.add('show');
}

async function deleteParent(pid, name){
  if(!confirm('确定删除父项目【'+name+'】？\n它下面的所有子项目（美术/分镜/视频）和原始工程包都会一并删除，且不可恢复。')) return;
  try{ const r=await api('/api/parent/'+encodeURIComponent(pid), {method:'DELETE'}); if(r&&r.ok===false){toast('删除失败：'+(r.error||''));return;} if(currentParent===pid)currentParent=null; toast('已删除父项目'+(r&&r.deleted_children?('（含 '+r.deleted_children+' 个子项目）'):'')); await loadProjects(); }
  catch(e){ toast('删除失败：'+e.message); }
}

async function deleteChild(cid, name){
  if(!confirm('确定从数据中心删除子项目【'+name+'】？原始工程包会一并删除。')) return;
  try{ await api('/api/child/'+encodeURIComponent(cid), {method:'DELETE'}); toast('已删除'); await loadProjects(); }
  catch(e){ toast('删除失败：'+e.message); }
}

// ---- 手动上传 ----
function chooseUpload(){ const i=$('uploadFile'); i.value=''; i.click(); }
$('uploadFile').addEventListener('change', async e=>{
  const f = e.target.files && e.target.files[0];
  if(!f) return;
  if(!f.name.toLowerCase().endsWith('.zip')) return toast('请选择工程包 .zip 文件');
  await uploadFile(f, 'ask');
});

async function uploadFile(file, onConflict){
  toast('正在上传：'+file.name);
  const fd = new FormData();
  fd.append('file', file, file.name);
  fd.append('on_conflict', onConflict);
  fd.append('meta', JSON.stringify({client_type:'data_center', base_child_name:file.name.replace(/\.zip$/i,'')}));
  try{
    const r = await api('/api/upload', {method:'POST', body:fd});
    if(r.conflict){ openConflict(r); return; }
    toast('上传完成：'+ (r.child && r.child.child_name || file.name));
    await loadProjects();
  }catch(e){ toast('上传失败：'+e.message); }
}

// ---- 冲突 ----
function openConflict(r){
  pendingConflictToken = r.token;
  $('conflictDesc').textContent = `「${r.incoming.module_label} / ${r.incoming.child_name}」已存在。选择覆盖将替换原工程包（底层保留备份）；选择生成新版本会保留两者。`;
  $('conflictModal').classList.add('show');
}
function closeConflict(){ $('conflictModal').classList.remove('show'); pendingConflictToken=null; }
async function resolveConflict(action){
  if(!pendingConflictToken){ closeConflict(); return; }
  try{
    await api('/api/upload/resolve-conflict', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({token:pendingConflictToken, action})});
    toast(action==='overwrite'?'已覆盖':'已生成新版本');
    closeConflict(); await loadProjects();
  }catch(e){ toast('处理失败：'+e.message); }
}

// ---- 设置 ----
async function openConfig(){
  try{ const c = await api('/api/config'); $('cfgName').value=c.server_name||''; $('cfgPort').value=c.port||8787; }catch(e){}
  $('configModal').classList.add('show');
}
function closeConfig(){ $('configModal').classList.remove('show'); }

let __transfersTimer=null;
function fmtSize(n){n=Number(n||0);if(n<1024)return n+' B';if(n<1048576)return (n/1024).toFixed(1)+' KB';if(n<1073741824)return (n/1048576).toFixed(1)+' MB';return (n/1073741824).toFixed(2)+' GB';}
function transferStatusText(s){return {running:'进行中',done:'已完成',failed:'失败'}[s]||s;}
async function refreshTransfers(){
  try{
    const r=await api('/api/transfers');
    const list=(r&&r.transfers)||[];
    const el=$('transfersList');
    if(!list.length){ el.innerHTML='<div class="desc" style="padding:14px">暂无上传 / 下载任务。</div>'; return; }
    el.innerHTML=list.map(t=>{
      const pct = t.total>0 ? Math.min(100,Math.round((t.done/t.total)*100)) : (t.status==='done'?100:0);
      const barColor = t.status==='failed' ? '#ff6b6b' : (t.status==='done' ? '#00c281' : '#3a86ff');
      const kindLabel = t.kind==='upload' ? '上传' : '下载';
      return `<div class="tf-row" style="padding:10px 4px;border-bottom:1px solid rgba(255,255,255,.07)">
        <div style="display:flex;justify-content:space-between;gap:10px">
          <b style="word-break:break-all">${esc(t.name)}</b>
          <span class="pill">${kindLabel} · ${transferStatusText(t.status)}</span>
        </div>
        <div style="height:8px;background:#1a2029;border-radius:6px;overflow:hidden;margin:6px 0">
          <div style="height:100%;width:${pct}%;background:${barColor};transition:width .3s"></div>
        </div>
        <div class="desc" style="font-size:12px;display:flex;justify-content:space-between">
          <span>${pct}%　${fmtSize(t.done)} / ${t.total?fmtSize(t.total):'-'}</span>
          <span>${esc(t.updated_at||t.started_at||'')}</span>
        </div>
        ${t.error?`<div class="desc" style="color:#ff8080;font-size:12px">错误：${esc(t.error)}</div>`:''}
      </div>`;
    }).join('');
  }catch(e){ $('transfersList').innerHTML='<div class="desc" style="padding:14px;color:#ff8080">读取失败：'+esc(e.message)+'</div>'; }
}
function openTransfers(){ $('transfersModal').classList.add('show'); refreshTransfers(); if(__transfersTimer)clearInterval(__transfersTimer); __transfersTimer=setInterval(refreshTransfers,1500); }
function closeTransfers(){ $('transfersModal').classList.remove('show'); if(__transfersTimer){clearInterval(__transfersTimer);__transfersTimer=null;} }
async function saveConfigUI(){
  try{
    await api('/api/config', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({server_name:$('cfgName').value.trim(), port:Number($('cfgPort').value)||8787})});
    toast('已保存（端口修改需重启服务端生效）'); closeConfig();
  }catch(e){ toast('保存失败：'+e.message); }
}

loadProjects();
setInterval(loadProjects, 15000);
