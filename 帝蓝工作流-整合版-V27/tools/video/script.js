

let cfg=null, project=null, currentProject=null, currentScreen='workspace';
let parents=[], selectedParent=null;
let sideMode='assets', sideCategory='人物', dragShotId=null, dragSceneId=null, dragTabInfo=null;
let selectedBase={}, pendingState={}, redoState={}, chatMode={}, drafts={}, storyIndex={}, storyViewMode={}, terminalOpen=false, terminalLogs=[];
let selectedAssets=new Set(), selecting=false, selectStart=null;
let storageBatchMode=false, selectedStorageImages=new Set(), storageSelecting=false, storageSelectStart=null;
let currentUserName='';
let promptUiState={};
let promptResizeObserver=null;
let promptSaveTimer=null;
const REVIEW_EDIT_MODE=true;

const TOOL_PATH='video', MODULE_LABEL='视频', MODULE_CHILD_LABEL='视频子项目';
const PROJECT_MODULES=[{key:'material',label:'美术'},{key:'image',label:'分镜'},{key:'video',label:'视频'}];
function switchTool(tool){const q=selectedParent?'?parent='+encodeURIComponent(selectedParent):'';if(selectedParent)localStorage.setItem('dilanSelectedParent',selectedParent);location.href='/' + tool + q}
function selectModule(tool){if(!selectedParent)return toast('请先选择或新建父项目');if(tool===TOOL_PATH)return;switchTool(tool)}
function renderModulePane(){const root=$('moduleList');if(!root)return;root.innerHTML=PROJECT_MODULES.map(m=>{const active=m.key===TOOL_PATH;const disabled=!selectedParent;return `<button type="button" class="module-card ${active?'active':''} ${disabled?'disabled':''}" ${disabled?'disabled':''} onclick="selectModule('${m.key}')">${m.label}</button>`}).join('')}
async function apiMaybe(url,opts={}){try{return await api(url,opts)}catch(e){term&&term('模块同步提示：'+e.message);return null}}
async function ensureParentInTool(tool,parentId){return true}
async function syncParentToAllModules(parentId){if(!parentId)return;localStorage.setItem('dilanSelectedParent',parentId)}
async function deleteParentFromAllModules(pid){await api('/api/parent/delete',{method:'POST',body:{parent:pid}})}
async function renameParentInAllModules(pid,newName){await api('/api/parent/rename',{method:'POST',body:{parent:pid,new_name:newName}})}
const modelMap={'seedance2.0':'doubao-seedance-2-0-260128','seedance2.0fast':'doubao-seedance-2-0-fast-260128','doubao-seedance-2-0-260128':'seedance2.0','doubao-seedance-2-0-fast-260128':'seedance2.0fast'};
function modelDisplay(v){return modelMap[v]&&modelMap[v].startsWith('seedance')?modelMap[v]:((v==='seedance2.0fast'||v==='doubao-seedance-2-0-fast-260128')?'seedance2.0fast':'seedance2.0')}
function modelId(v){return v==='seedance2.0fast'?'doubao-seedance-2-0-fast-260128':'doubao-seedance-2-0-260128'}
const ratios=['9:16','1:1','4:3','3:4','16:9','21:9'], resolutions=['480p','720p','1080p'], categories=['人物','场景','道具','音频','视频'];
function $(id){return document.getElementById(id)}
function uid(p){return p+'_'+Math.random().toString(16).slice(2,12)}
function esc(s){return String(s||'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function jsstr(s){return String(s||'').replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/\r/g,'\\r').replace(/\n/g,'\\n')}
function toast(msg){const t=$('toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2600)}
function term(msg){const line=`[${new Date().toLocaleTimeString()}] ${msg}`;terminalLogs.push(line);if(terminalLogs.length>300)terminalLogs=terminalLogs.slice(-300);renderTerminal()}
function renderTerminal(){const b=$('terminalBody'); if(!b)return; b.innerHTML=terminalLogs.map(l=>`<div>${esc(l)}</div>`).join(''); b.scrollTop=b.scrollHeight; const last=terminalLogs[terminalLogs.length-1]||'准备就绪。'; $('termText').textContent=last.length>160?last.slice(0,160)+'...':last; $('terminalWrap').classList.toggle('open',terminalOpen); $('terminalArrow').textContent=terminalOpen?'▾':'▸'}
function toggleTerminal(){terminalOpen=!terminalOpen;renderTerminal()} function clearTerminal(){terminalLogs=[];renderTerminal()}
async function api(url,opts={}){opts.headers=opts.headers||{};if(opts.body&&typeof opts.body!=='string'){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(opts.body)}const r=await fetch(url,opts);let d={};try{d=await r.json()}catch(e){}if(!r.ok){const err=new Error(d.error_cn||d.error||'HTTP '+r.status);err.status=r.status;err.response=d;err.raw=d.error||'';err.detail=d.error_detail_text||d.error||'';err.report=d.error_report||null;err.data=d.data||null;err.usage=d.usage||null;throw err}return d}
function apiErrorDetail(e){const d=e&&e.response?e.response:{};return d.error_detail_text||e?.detail||d.error||e?.stack||String(e||'未知错误')}
function apiErrorTitle(e){const d=e&&e.response?e.response:{};return d.error_cn||e?.message||d.error||'请求失败'}
function fileToDataUrl(file){return new Promise((res,rej)=>{const r=new FileReader();r.onload=()=>res(r.result);r.onerror=rej;r.readAsDataURL(file)})}
function isImageFile(f){return f&&((f.type||'').startsWith('image/')||/\.(png|jpe?g|webp|gif|bmp|svg)$/i.test(f.name||''))}
function isAudioFile(f){return f&&((f.type||'').startsWith('audio/')||/\.(mp3|wav|m4a|aac|ogg|flac)$/i.test(f.name||''))}
function isVideoFile(f){return f&&((f.type||'').startsWith('video/')||/\.(mp4|mov|webm|m4v|avi|mkv)$/i.test(f.name||''))}
function isAssetFile(f){return isImageFile(f)||isAudioFile(f)||isVideoFile(f)}
function inferCategory(file,fallback=sideCategory){if(isAudioFile(file))return '音频';if(isVideoFile(file))return '视频';return fallback||'人物'}
function isVideoPath(src){return /\.(mp4|mov|webm|m4v)(\?|#|$)/i.test(src||'')||String(src||'').includes('/output/video/')}
function isAudioPath(src){return /\.(mp3|wav|m4a|aac|ogg|flac)(\?|#|$)/i.test(src||'')}
function mediaThumb(item,cls=''){const src=item.file_path||item.url||'';const name=esc(item.name||item.display_name||'素材');if(isVideoPath(src))return `<video class="${cls}" muted preload="metadata" ondblclick="openPreview('${src}', event)" src="${src}"></video>`;if(isAudioPath(src)||item.category==='音频')return `<div class="media-icon ${cls}" ondblclick="openPreview('${src}', event)">♫</div>`;return `<img class="${cls}" ondblclick="openPreview('${src}', event)" src="${src}" alt="${name}">`}
async function init(){ $('apiKey').value=localStorage.getItem('seedanceArkApiKey')||localStorage.getItem('imageToolApiKey')||''; await initUser(); await loadConfig(); const params=new URLSearchParams(location.search); const parentParam=params.get('parent')||localStorage.getItem('dilanSelectedParent')||''; if(parentParam)selectedParent=parentParam; await refreshParents(); const urlParent=params.get('parent')||''; if(urlParent&&selectedParent&&!parents.some(p=>p.parent_id===selectedParent)){await api('/api/parents/create',{method:'POST',body:{name:selectedParent}});await refreshParents()} renderTerminal(); const pid=params.get('project'); const h=location.hash.replace('#',''); if(pid) await openProject(pid,true).catch(e=>term('自动打开项目失败：'+e.message)); if(['workspace','assets','storage','config'].includes(h)) showScreen(h); document.addEventListener('keydown',onKeyDown); }
async function initUser(){currentUserName=localStorage.getItem('imageToolUserName')||''; if(!currentUserName){$('userModal').classList.add('show');return} updateUserUI(); await refreshUsage()}
async function saveUserName(){const name=$('userNameInput').value.trim(); if(!name)return toast('请填写昵称'); const r=await api('/api/user/set_name',{method:'POST',body:{user_name:name}}); currentUserName=r.user.user_name; localStorage.setItem('imageToolUserName',currentUserName); $('userModal').classList.remove('show'); updateUserUI(); updateUsageUI(r.usage)}
function updateUserUI(){if($('topUserName'))$('topUserName').textContent=currentUserName||'-'; if($('projectUserName'))$('projectUserName').textContent=currentUserName||'-'}
async function refreshUsage(){if(!currentUserName)return; const r=await api('/api/usage?user='+encodeURIComponent(currentUserName)); updateUsageUI(r.usage)}
function updateUsageUI(u){if(!u)return; $('usageCost').textContent='¥'+Number(u.cny||0).toFixed(4)}

function findTabById(tabId){
    if(!project||!project.scenes)return null;
    for(const s of project.scenes||[]){
        for(const h of s.shots||[]){
            for(const t of h.tabs||[]){
                if(t.tab_id===tabId)return {scene:s,shot:h,tab:t};
            }
        }
    }
    return null;
}
function activePromptId(){const el=document.activeElement;return el&&el.classList&&el.classList.contains('prompt')?el.id.replace(/^input-/,''):null}
function cleanPx(v, fallback=180){const n=parseInt(String(v||'').replace(/[^0-9]/g,''),10);return Number.isFinite(n)&&n>=90&&n<=900?n:fallback}
function capturePromptStates(){
    const out={...promptUiState,_active:activePromptId()};
    document.querySelectorAll('textarea.prompt').forEach(ta=>{
        const id=ta.id.replace(/^input-/,'');
        const h=ta.style.height?cleanPx(ta.style.height,ta.offsetHeight):ta.offsetHeight;
        out[id]={scrollTop:ta.scrollTop||0,scrollLeft:ta.scrollLeft||0,selectionStart:ta.selectionStart||0,selectionEnd:ta.selectionEnd||ta.selectionStart||0,height:h};
    });
    return out;
}
function rememberPromptState(tabId,ta,persist=false){
    if(!ta)return;
    const h=ta.style.height?cleanPx(ta.style.height,ta.offsetHeight):ta.offsetHeight;
    promptUiState[tabId]={scrollTop:ta.scrollTop||0,scrollLeft:ta.scrollLeft||0,selectionStart:ta.selectionStart||0,selectionEnd:ta.selectionEnd||ta.selectionStart||0,height:h};
    const f=findTabById(tabId);
    if(f&&f.tab){f.tab.prompt_box_height=h;f.tab.prompt_scroll_top=ta.scrollTop||0;}
    if(persist)scheduleSaveProject();
}
function restorePromptStates(states={}){
    promptUiState={...promptUiState,...states};
    Object.keys(states||{}).forEach(k=>{
        if(k==='_active')return;
        const ta=$('input-'+k); if(!ta)return;
        const st=states[k]||{};
        if(st.height)ta.style.height=cleanPx(st.height,ta.offsetHeight)+'px';
        ta.scrollTop=st.scrollTop||0; ta.scrollLeft=st.scrollLeft||0;
        const hl=$('hl-'+k); if(hl){hl.innerHTML=highlightPrompt(ta.value||'',k);syncHighlight(k)}
    });
    const active=states&&states._active;
    if(active&&$('input-'+active)){
        const ta=$('input-'+active), st=states[active]||{};
        setTimeout(()=>{try{ta.focus();ta.setSelectionRange(st.selectionStart||0,st.selectionEnd||st.selectionStart||0);ta.scrollTop=st.scrollTop||0;ta.scrollLeft=st.scrollLeft||0;syncHighlight(active)}catch(e){}},0);
    }
    setupPromptObservers();
}
function setupPromptObservers(){
    if(!window.ResizeObserver)return;
    if(promptResizeObserver)promptResizeObserver.disconnect();
    promptResizeObserver=new ResizeObserver(entries=>{
        for(const en of entries){
            const ta=en.target; if(!ta||!ta.id||!ta.classList.contains('prompt'))continue;
            const id=ta.id.replace(/^input-/,'');
            rememberPromptState(id,ta,false);
            const f=findTabById(id); if(f&&f.tab)f.tab.prompt_box_height=ta.offsetHeight;
            scheduleSaveProject();
            syncHighlight(id);
        }
    });
    document.querySelectorAll('textarea.prompt').forEach(ta=>{promptResizeObserver.observe(ta);syncHighlight(ta.id.replace(/^input-/,''));});
}
function syncPromptDraftsToProject(){
    if(!project)return;
    document.querySelectorAll('textarea.prompt').forEach(ta=>{
        const tabId=ta.id.replace(/^input-/,'');
        drafts[tabId]=ta.value||'';
        const f=findTabById(tabId);
        if(f&&f.tab){
            f.tab.draft_prompt=drafts[tabId];
            const h=ta.style.height?cleanPx(ta.style.height,ta.offsetHeight):ta.offsetHeight;
            f.tab.prompt_box_height=h;
            f.tab.prompt_scroll_top=ta.scrollTop||0;
        }
    });
    Object.keys(drafts||{}).forEach(tabId=>{
        const f=findTabById(tabId);
        if(f&&f.tab)f.tab.draft_prompt=drafts[tabId]||'';
    });
}
function hydratePromptDraftsFromProject(reset=false){
    if(reset)drafts={};
    if(!project||!project.scenes)return;
    (project.scenes||[]).forEach(scene=>{
        (scene.shots||[]).forEach(shot=>{
            (shot.tabs||[]).forEach(tab=>{
                const id=tab.tab_id;
                if(!id)return;
                const saved=typeof tab.draft_prompt==='string'?tab.draft_prompt:'';
                if(reset || drafts[id]===undefined || (!drafts[id]&&saved))drafts[id]=saved;
                tab.draft_prompt=drafts[id]||'';
            });
        });
    });
}
function clearPromptDraftForTab(tabId,dataObj=null){
    if(!tabId)return;
    delete drafts[tabId];
    const ta=$('input-'+tabId);
    if(ta){
        ta.value='';
        const hl=$('hl-'+tabId);
        if(hl)hl.innerHTML='';
        try{syncHighlight(tabId)}catch(e){}
    }
    const target=dataObj||project;
    if(!target||!target.scenes)return;
    (target.scenes||[]).forEach(scene=>{
        (scene.shots||[]).forEach(shot=>{
            (shot.tabs||[]).forEach(tab=>{
                if(tab.tab_id===tabId)tab.draft_prompt='';
            });
        });
    });
}
function preservePromptDraftForTab(tabId,text,dataObj=null){
    if(!tabId)return;
    const val=typeof text==='string'?text:'';
    drafts[tabId]=val;
    const ta=$('input-'+tabId);
    if(ta){
        ta.value=val;
        const hl=$('hl-'+tabId);
        if(hl)hl.innerHTML=highlightPrompt(val,tabId);
        try{syncHighlight(tabId)}catch(e){}
    }
    const target=dataObj||project;
    if(!target||!target.scenes)return;
    (target.scenes||[]).forEach(scene=>{
        (scene.shots||[]).forEach(shot=>{
            (shot.tabs||[]).forEach(tab=>{
                if(tab.tab_id===tabId)tab.draft_prompt=val;
            });
        });
    });
}
function scheduleSaveProject(){clearTimeout(promptSaveTimer);promptSaveTimer=setTimeout(()=>{if(project)saveProject().catch(()=>{})},900)}
function handlePromptWheel(e,tabId){const ta=e.currentTarget;if(!ta)return;const canUp=ta.scrollTop>0;const canDown=ta.scrollTop+ta.clientHeight<ta.scrollHeight-1;if((e.deltaY<0&&canUp)||(e.deltaY>0&&canDown)){e.stopPropagation();ta.scrollTop+=e.deltaY;rememberPromptState(tabId,ta,false);syncHighlight(tabId);e.preventDefault()}}
async function loadConfig(){cfg=await api('/api/config');const g=cfg.global_defaults||{};$('cfgModel').value=modelDisplay(g.video_model||'seedance2.0');$('cfgRatio').value=g.video_ratio||'16:9';$('cfgResolution').value=g.video_resolution||'720p';$('cfgQuality').value=g.video_duration||8;$('cfgUsd').value=g.seedance_fps||24; if($('cfgP1')){$('cfgP1').value=g.seedance_regular_no_video_cny_per_million||46;$('cfgP2').value=g.seedance_regular_with_video_cny_per_million||28;$('cfgP3').value=g.seedance_fast_no_video_cny_per_million||37;$('cfgP4').value=g.seedance_fast_with_video_cny_per_million||22}}
async function saveConfig(){cfg=await api('/api/config/save',{method:'POST',body:{global_defaults:{video_model:$('cfgModel').value,video_ratio:$('cfgRatio').value,video_resolution:$('cfgResolution').value,video_duration:parseInt($('cfgQuality').value||'8',10)||8,seedance_fps:parseFloat($('cfgUsd').value||'24'),seedance_regular_no_video_cny_per_million:parseFloat($('cfgP1')?.value||'46'),seedance_regular_with_video_cny_per_million:parseFloat($('cfgP2')?.value||'28'),seedance_fast_no_video_cny_per_million:parseFloat($('cfgP3')?.value||'37'),seedance_fast_with_video_cny_per_million:parseFloat($('cfgP4')?.value||'22')}}});if(project){const g=cfg.global_defaults||{};project.project_settings=project.project_settings||{};['video_model','video_ratio','video_resolution','video_duration'].forEach(k=>{if(g[k]!==undefined)project.project_settings[k]=g[k]});if(project.project_settings.video_model)project.project_settings.video_model=modelDisplay(project.project_settings.video_model);if(project.project_settings.video_model==='seedance2.0fast'&&project.project_settings.video_resolution==='1080p')project.project_settings.video_resolution='720p';await saveProject()}toast('已保存全局设置；已存在子栏参数不变，之后新建子栏使用新参数')}
async function refreshParents(){const r=await api('/api/parents');parents=r.parents||[]; if(selectedParent&&!parents.some(p=>p.parent_id===selectedParent)){selectedParent=parents[0]?.parent_id||null} if(!selectedParent&&parents[0])selectedParent=parents[0].parent_id; if(selectedParent)localStorage.setItem('dilanSelectedParent',selectedParent); renderParents(); renderModulePane(); await refreshProjects()}
function parentModuleMeta(p){const c=p.module_counts||{};const parts=[`美术 ${c.material||0}`,`分镜 ${c.image||0}`,`视频 ${c.video||0}`];return parts.join(' · ')+(p.updated_at?' · '+esc(p.updated_at):'')}
function renderParents(){const root=$('parentList');root.innerHTML=parents.length?parents.map(p=>`<div class="project-item ${p.parent_id===selectedParent?'active':''}"><div onclick="selectParent('${esc(p.parent_id)}')"><b>${esc(p.parent_name)}</b><div class="meta">${parentModuleMeta(p)}</div></div><div><button class="btn ghost small" title="修改父项目显示名称" onclick="event.stopPropagation();renameParent('${esc(p.parent_id)}')">改名</button> <button class="btn danger small" onclick="event.stopPropagation();deleteParent('${esc(p.parent_id)}')">删除</button></div></div>`).join(''):'<div class="desc">暂无父项目，请新建。</div>'}
async function selectParent(pid){selectedParent=pid;localStorage.setItem('dilanSelectedParent',pid);renderParents();renderModulePane();await refreshProjects()}
async function createParent(){const name=$('newParentName').value.trim();if(!name)return toast('请输入父项目名');const r=await api('/api/parents/create',{method:'POST',body:{name}});$('newParentName').value='';selectedParent=r.parent.parent_id;localStorage.setItem('dilanSelectedParent',selectedParent);await syncParentToAllModules(selectedParent);await refreshParents();toast('父项目已创建：已自动生成美术 / 分镜 / 视频三个模块')}
async function deleteParent(pid){if(!confirm('删除父项目会连同美术 / 分镜 / 视频三个模块下的所有子项目一起删除，确定？'))return;await deleteParentFromAllModules(pid);if(selectedParent===pid){selectedParent=null;localStorage.removeItem('dilanSelectedParent')}await refreshParents()}
async function renameParent(pid){const p=parents.find(x=>x.parent_id===pid);const name=prompt('修改父项目名称',p?.parent_name||pid);if(!name||!name.trim())return;await renameParentInAllModules(pid,name.trim());await refreshParents();toast('父项目已改名')}
async function refreshProjects(){const root=$('projectList');if(!selectedParent){$('selectedParentTitle').textContent='未选择父项目。';root.innerHTML='<div class="desc">请先选择或新建父项目。</div>';return}const r=await api('/api/projects?parent='+encodeURIComponent(selectedParent));const sp=parents.find(p=>p.parent_id===selectedParent);$('selectedParentTitle').textContent='当前父级项目：'+(sp?sp.parent_name:selectedParent);root.innerHTML=r.projects.length?r.projects.map(p=>`<div class="project-item"><div><b>${esc(p.project_name)}</b><div class="meta">${p.scene_count||0} 个 SC · ${p.image_count||0} 条视频 · ${esc(p.updated_at||'')}</div></div><div><button class="btn small" onclick="openProject('${esc(p.project_id)}')">打开</button> <button class="btn ghost small" onclick="exportProject('${esc(p.project_id)}')">导出工程</button> <button class="btn ghost small" onclick="renameProject('${esc(p.project_id)}','${encodeURIComponent(p.project_name||'')}')">改名</button> <button class="btn danger small" onclick="deleteProject('${esc(p.project_id)}')">删除</button></div></div>`).join(''):'<div class="desc">该父项目的当前模块下暂无子项目。</div>'}
async function createProject(){if(!selectedParent)return toast('请先选择父项目');const name=$('newProjectName').value.trim();if(!name)return toast('请输入子项目名');const scene_start=($('sceneStart')?.value||'').trim();const scene_end=($('sceneEnd')?.value||'').trim();if((scene_start&&!scene_end)||(!scene_start&&scene_end))return toast('分镜范围需要同时填写开始和结束，或两个都不填');if(scene_start&&scene_end&&Number(scene_start)>Number(scene_end))return toast('分镜范围必须左小于等于右');const r=await api('/api/projects/create',{method:'POST',body:{parent:selectedParent,name,scene_start,scene_end}});$('newProjectName').value='';if($('sceneStart'))$('sceneStart').value='';if($('sceneEnd'))$('sceneEnd').value='';setProject(r.data);$('projectScreen').style.display='none';$('app').style.display='flex';term('已创建并打开视频子项目：'+r.data.project_name)}
async function openProject(pid,silent=false){const r=await api('/api/project/load?project='+encodeURIComponent(pid));setProject(r.data);$('projectScreen').style.display='none';$('app').style.display='flex';if(!silent)term('已打开子项目：'+r.data.project_name)}
async function deleteProject(pid){if(!confirm('确定删除子项目？'))return;await api('/api/project/delete',{method:'POST',body:{project:pid}});refreshProjects()}
async function renameProject(pid,encodedName=''){const oldName=decodeURIComponent(encodedName||'');const name=prompt('修改子项目名称',oldName);if(!name||!name.trim())return;const r=await api('/api/project/rename',{method:'POST',body:{project:pid,new_name:name.trim()}});if(project&&currentProject===pid)setProject(r.data);await refreshProjects();toast('子项目已改名')}
function setProject(data){project=data;currentProject=data.project_id;selectedParent=data.parent_id||selectedParent;$('topProjectName').textContent=(data.parent_id?data.parent_id+' / ':'')+data.project_name;const u=new URL(location.href);u.searchParams.set('project',currentProject);history.replaceState({},'',u.pathname+u.search+location.hash);hydratePromptDraftsFromProject(true);renderAll();setSave('已保存')}
function backProjects(){const u=new URL(location.href);u.searchParams.delete('project');history.replaceState({},'',u.pathname);$('app').style.display='none';$('projectScreen').style.display='flex';refreshParents()}
function setSave(t){$('saveText').textContent=t}
async function saveProject(){if(!project)return;syncPromptDraftsToProject();syncAllReviewRoundShots();setSave('保存中');const r=await api('/api/project/save',{method:'POST',body:{project:currentProject,data:project}});project=r.data;currentProject=project.project_id;hydratePromptDraftsFromProject(false);setSave('已保存')}
async function exportProject(pid=null){const target=pid||currentProject;if(!target)return toast('请先打开或选择子项目');try{if(project&&currentProject===target)await saveProject();const user=encodeURIComponent(currentUserName||'未命名用户');window.location.href='/api/project/export?project='+encodeURIComponent(target)+'&user_name='+user;term('正在导出工程包：'+target)}catch(e){toast('导出失败：'+e.message);term('导出失败：'+e.message)}}
function chooseImportProject(){const input=$('importProjectFile');if(!input)return toast('导入控件未找到');input.value='';input.click()}
async function handleImportProjectFile(file){
  if(!file)return;
  if(!file.name.toLowerCase().endsWith('.zip'))return toast('请选择工程导出的 .zip 文件');
  if(!confirm('将读取工程包自带的父项目 / 模块 / 子项目信息并自动归位；若本地已有同名子项目，会自动导入为副本，不会覆盖现有工程。是否继续？'))return;
  try{
    term('正在读取工程包：'+file.name);
    const dataUrl=await fileToDataUrl(file);
    const r=await api('/api/project/import',{method:'POST',body:{filename:file.name,dataUrl,user_name:currentUserName||'未命名用户'}});
    const importedModule=String((r.data&&(r.data.module_type||r.data.project_module_type))||(r.manifest&&(r.manifest.module_type||r.manifest.tool_module))||'').trim();
    const moduleLabels={material:'美术',image:'分镜',video:'视频'};
    const targetTool=({material:'material',image:'image',video:'video'})[importedModule]||'';
    selectedParent=(r.data&&r.data.parent_id)||selectedParent;
    if(selectedParent)localStorage.setItem('dilanSelectedParent',selectedParent);
    const importedProject=(r.data&&r.data.project_id)||'';
    if(targetTool&&targetTool!==TOOL_PATH){
      const q=new URLSearchParams();
      if(selectedParent)q.set('parent',selectedParent);
      if(importedProject)q.set('project',importedProject);
      toast('导入完成，正在切换到'+(moduleLabels[targetTool]||'对应')+'模块');
      term('已自动导入到'+(moduleLabels[targetTool]||targetTool)+'模块：'+importedProject+'，复制文件 '+(r.copied_files||0)+' 个');
      location.href='/' + targetTool + (q.toString()?('?'+q.toString()):'');
      return;
    }
    await refreshParents();
    setProject(r.data);
    $('projectScreen').style.display='none';
    $('app').style.display='flex';
    toast('导入完成');
    term('已导入工程包：'+r.data.project_id+'，复制文件 '+(r.copied_files||0)+' 个');
  }catch(e){
    toast('导入失败：'+e.message);
    term('导入失败：'+e.message);
  }
}

async function exportAssetPackage(pid=null){const target=pid||currentProject;if(!target)return toast('请先打开或选择子项目');try{if(project&&currentProject===target)await saveProject();const user=encodeURIComponent(currentUserName||'未命名用户');window.location.href='/api/assets/package/export?project='+encodeURIComponent(target)+'&user_name='+user;term('正在导出素材包：'+target)}catch(e){toast('导出素材包失败：'+e.message);term('导出素材包失败：'+e.message)}}
function chooseImportAssetPackage(){if(!currentProject)return toast('请先打开要导入素材包的子项目');const input=$('importAssetPackageFile');if(!input)return toast('导入控件未找到');input.value='';input.click()}
async function handleImportAssetPackageFile(file){if(!file)return;if(!file.name.toLowerCase().endsWith('.zip'))return toast('请选择素材包 .zip 文件');if(!currentProject)return toast('请先打开目标子项目');if(!confirm('将把素材包导入当前子项目的素材库。同名组会合并，同名素材会自动递增编号，不会覆盖现有素材。是否继续？'))return;try{term('正在读取素材包：'+file.name);const dataUrl=await fileToDataUrl(file);const r=await api('/api/assets/package/import',{method:'POST',body:{project:currentProject,filename:file.name,dataUrl,user_name:currentUserName||'未命名用户'}});updateFromServer(r);toast(`素材包导入完成：${r.imported_count||0} 个素材`);term(`已导入素材包：${file.name}，素材 ${r.imported_count||0} 个，重名改名 ${r.renamed_count||0} 个，跳过 ${r.skipped_count||0} 个`)}catch(e){toast('导入素材包失败：'+e.message);term('导入素材包失败：'+e.message)}}
function updateFromServer(r){project=r.data;currentProject=project.project_id;hydratePromptDraftsFromProject(false);syncPromptDraftsToProject();renderAll();setSave('已保存');if(r.usage)updateUsageUI(r.usage)}
function navMouse(e,screen){if(e.button===1){e.preventDefault();window.open(location.origin+location.pathname+(currentProject?'?project='+encodeURIComponent(currentProject):'')+'#'+screen,'_blank');return false} if(e.button===0){e.preventDefault();showScreen(screen)}}
function showScreen(screen){currentScreen=screen;document.querySelectorAll('.screen').forEach(s=>s.classList.remove('active'));$('screen-'+screen).classList.add('active');document.querySelectorAll('.navbtn').forEach(n=>n.classList.remove('active'));$('nav-'+(screen==='workspace'?'work':screen)).classList.add('active');location.hash=screen;hideStorageMenu();renderAll()}
function renderAll(){const _promptState=capturePromptStates();syncPromptDraftsToProject();setTimeout(hydrateVideoThumbnails,80);if(!project)return;renderScenes(_promptState);renderSidePanel();renderLibrary();renderStorage()}
function allShots(){return project.scenes.flatMap(s=>s.shots.map(h=>({scene:s,shot:h})))}
function shotById(id){for(const s of project.scenes){for(const h of s.shots){if(h.shot_id===id)return{scene:s,shot:h}}}return null}
function shotByTabId(tabId){if(!project||!project.scenes)return null;for(const s of project.scenes){for(const h of s.shots||[]){for(const t of h.tabs||[]){if(t.tab_id===tabId)return{scene:s,shot:h,tab:t}}}}return null}
function activeTab(shot){shot.tabs=shot.tabs&&shot.tabs.length?shot.tabs:[newCleanTab(1)];let t=shot.tabs.find(x=>x.tab_id===shot.active_tab_id)||shot.tabs[0];shot.active_tab_id=t.tab_id;return t}
function tabKey(shot){return activeTab(shot).tab_id}
function assetById(id){return (project.assets||[]).find(a=>a.asset_id===id)}
function projectDefaults(){const g=(project&&project.project_settings)||((cfg&&cfg.global_defaults)||{});return {video_model:modelDisplay(g.video_model||'seedance2.0'),video_ratio:g.video_ratio||g.image_ratio||'16:9',video_resolution:g.video_resolution||'720p',video_duration:g.video_duration||8,video_seed:g.video_seed||'',video_generate_audio:g.video_generate_audio!==false,video_watermark:!!g.video_watermark}}
function cloneObj(o){return JSON.parse(JSON.stringify(o||{}))}
function reviewLabel(n){return n===1?'一审':n===2?'二审':'三审'}
function statusText(st){return st==='approved'?'通过':st==='rejected'?'不通过':'待审核'}
function statusClass(st){return st==='approved'?'approved':st==='rejected'?'rejected':'pending'}
function ensureSceneReview(scene){scene.review=scene.review&&typeof scene.review==='object'?scene.review:{};scene.review.current_round=Math.min(Math.max(Number(scene.review.current_round||1),1),3);scene.review.max_round_available=Math.min(Math.max(Number(scene.review.max_round_available||1),scene.review.current_round,1),3);scene.review.rounds=scene.review.rounds&&typeof scene.review.rounds==='object'?scene.review.rounds:{};for(let i=1;i<=scene.review.max_round_available;i++){const k=String(i);scene.review.rounds[k]=scene.review.rounds[k]&&typeof scene.review.rounds[k]==='object'?scene.review.rounds[k]:{};scene.review.rounds[k].label=reviewLabel(i);scene.review.rounds[k].status=['pending','approved','rejected'].includes(scene.review.rounds[k].status)?scene.review.rounds[k].status:'pending';scene.review.rounds[k].locked=!!scene.review.rounds[k].locked;if(!Array.isArray(scene.review.rounds[k].shots))delete scene.review.rounds[k].shots;}return scene.review}
function currentRoundNo(scene){return ensureSceneReview(scene).current_round||1}
function currentRound(scene){const r=ensureSceneReview(scene);return r.rounds[String(r.current_round)]}
function sceneLocked(scene){return !!currentRound(scene).locked}
function syncSceneReviewRoundShots(scene){const r=ensureSceneReview(scene);const cur=String(r.current_round||1);r.rounds[cur]=r.rounds[cur]||{};if(!r.rounds[cur].locked&&Array.isArray(r.rounds[cur].shots)){r.rounds[cur].shots=cloneObj(scene.shots||[])}}
function syncAllReviewRoundShots(){if(!project)return;(project.scenes||[]).forEach(syncSceneReviewRoundShots)}
function canEditScene(scene){return REVIEW_EDIT_MODE&&!sceneLocked(scene)}
function findSceneByShotId(shotId){const f=shotById(shotId);return f?f.scene:null}
function canEditShot(shotId){const s=findSceneByShotId(shotId);return !!(s&&canEditScene(s))}
function assertEditScene(scene){if(!scene||!canEditScene(scene)){toast('当前审核轮次已锁定，无法修改');return false}return true}
function assertEditShot(shotId){const s=findSceneByShotId(shotId);return assertEditScene(s)}
function renderReviewControls(scene){const r=ensureSceneReview(scene);const round=currentRound(scene);const st=round.status||'pending';const opts=[];for(let i=1;i<=r.max_round_available;i++)opts.push(`<option value="${i}" ${i===r.current_round?'selected':''}>${reviewLabel(i)}</option>`);const locked=round.locked?'<span class="locked-tag">已锁定</span>':'';const disabled=round.locked?'disabled':'';return `<div class="review-controls"><select class="round-select" onchange="switchReviewRound('${scene.scene_id}',this.value)">${opts.join('')}</select><span class="review-word ${statusClass(st)}">${statusText(st)}</span>${locked}<button class="btn small" ${disabled} onclick="submitReview('${scene.scene_id}','approved')">通过</button><button class="btn danger small" ${disabled} onclick="submitReview('${scene.scene_id}','rejected')">不通过</button></div>`}
function clearStoryboardInShots(shots){(shots||[]).forEach(sh=>{sh.storyboard_candidates=[];sh.status='unconfirmed'})}
function switchReviewRound(sceneId,value){const scene=sceneById(sceneId);if(!scene)return;syncSceneReviewRoundShots(scene);const n=Math.min(Math.max(Number(value||1),1),3);const r=ensureSceneReview(scene);if(n>r.max_round_available)return;r.current_round=n;const rd=r.rounds[String(n)]||{};if(Array.isArray(rd.shots)){scene.shots=cloneObj(rd.shots||[])}renderAll();saveProject()}
function submitReview(sceneId,result){const scene=sceneById(sceneId);if(!scene)return;const r=ensureSceneReview(scene);const round=currentRound(scene);if(round.locked)return toast('该审核轮次已经锁定');const label=reviewLabel(r.current_round);const word=result==='approved'?'通过':'不通过';if(!confirm(`${label}将提交为“${word}”。一旦提交无法修改，是否确认？`))return;round.shots=cloneObj(scene.shots||[]);round.status=result;round.locked=true;round.submitted_at=new Date().toLocaleString();round.submitted_by=currentUserName||'未命名用户';if(result==='rejected'&&r.current_round<3){const next=r.current_round+1;const k=String(next);if(!r.rounds[k]){const shots=cloneObj(scene.shots||[]);clearStoryboardInShots(shots);r.rounds[k]={label:reviewLabel(next),status:'pending',locked:false,created_from_round:r.current_round,created_at:new Date().toLocaleString(),shots};}r.max_round_available=Math.max(r.max_round_available,next)}renderAll();saveProject();toast(`${label}已提交：${word}`)}
function newCleanTab(n){return {tab_id:uid('tab'),tab_name:'标签'+n,sort_order:n,settings:projectDefaults(),referenced_assets:[],messages:[],generated_images:[],generated_videos:[],current_base_image_id:null,context_summary:'',draft_prompt:''}}
function renderScenes(preservedState=null){const state=preservedState||capturePromptStates();const root=$('sceneRoot');const scenes=project.scenes||[];root.innerHTML=scenes.length?scenes.map(scene=>renderScene(scene)).join(''):'<div class="scene"><div class="scene-head"><div class="desc">当前子项目还没有 SC。点击顶部“+ 新增 SC”创建第一个 SC。</div></div></div>';restorePromptStates(state)}
function renderScene(scene){ensureSceneReview(scene);const locked=sceneLocked(scene);const shots=(scene.shots||[]).map((shot,i)=>`${i>0?`<div class="insert-line"><button ${locked?'disabled':''} onclick="insertShot('${scene.scene_id}','${scene.shots[i-1].shot_id}')">＋ 在此插入子栏</button></div>`:''}${renderShot(scene,shot)}`).join('');return `<div class="scene ${locked?'locked-ui':''}"><div class="scene-head"><button class="btn ghost small" onclick="toggleScene('${scene.scene_id}')">${scene.expanded?'▾':'▸'}</button><div class="scene-title" title="点击修改 SC 编号" onclick="editSceneNumber('${scene.scene_id}')">${esc(scene.scene_code)}</div>${renderSceneTime(scene)}<div class="scene-actions"><button class="btn ghost small" ${locked?'disabled':''} onclick="insertScene('${scene.scene_id}')">新增 SC</button><button class="btn small" ${locked?'disabled':''} onclick="addShot('${scene.scene_id}')">＋ 子栏</button><button class="btn danger small" ${locked?'disabled':''} onclick="deleteScene('${scene.scene_id}')">删除 SC</button></div>${renderReviewControls(scene)}</div>${scene.expanded?`<div class="shot-list">${shots}</div>`:''}</div>`}
function renderSceneTime(scene){scene.time_start=scene.time_start||{};scene.time_end=scene.time_end||{};const dis=sceneLocked(scene)?'disabled':'';return `<div class="scene-time"><input class="timebox" ${dis} value="${esc(scene.time_start.h||'')}" onchange="setSceneTime('${scene.scene_id}','time_start','h',this.value)">时<input class="timebox" ${dis} value="${esc(scene.time_start.m||'')}" onchange="setSceneTime('${scene.scene_id}','time_start','m',this.value)">分<input class="timebox" ${dis} value="${esc(scene.time_start.s||'')}" onchange="setSceneTime('${scene.scene_id}','time_start','s',this.value)">秒 - <input class="timebox" ${dis} value="${esc(scene.time_end.h||'')}" onchange="setSceneTime('${scene.scene_id}','time_end','h',this.value)">时<input class="timebox" ${dis} value="${esc(scene.time_end.m||'')}" onchange="setSceneTime('${scene.scene_id}','time_end','m',this.value)">分<input class="timebox" ${dis} value="${esc(scene.time_end.s||'')}" onchange="setSceneTime('${scene.scene_id}','time_end','s',this.value)">秒</div>`}
function setSceneTime(sceneId,which,k,v){const s=sceneById(sceneId);if(!s||!assertEditScene(s))return;s[which]=s[which]||{};s[which][k]=v.replace(/[^0-9]/g,'').slice(0,2);saveProject()}
function sceneById(id){return project.scenes.find(s=>s.scene_id===id)}
function sceneNum(scene){const m=String(scene.scene_code||'').match(/(\d+)/);return scene.scene_number||Number(m?.[1]||1)}
async function editSceneNumber(sceneId){const s=sceneById(sceneId);if(!s||!assertEditScene(s))return;const cur=sceneNum(s);const val=prompt('修改 SC 编号（只填数字，例如 5 会显示为 SC-0005）',cur);if(val===null)return;const clean=String(val).replace(/[^0-9]/g,'');if(!clean||Number(clean)<1)return toast('SC 编号只能填写大于 0 的数字');const n=Number(clean);const dup=(project.scenes||[]).find(x=>x.scene_id!==sceneId&&sceneNum(x)===n);if(dup)return toast(`SC-${String(n).padStart(4,'0')} 已存在，不能重复`);updateFromServer(await api('/api/scene/renumber',{method:'POST',body:{project:currentProject,scene_id:sceneId,scene_number:n}}));term('已修改 SC 编号为 SC-'+String(n).padStart(4,'0'))}
function renderShot(scene,shot){const tab=activeTab(shot);const collapsed=shot.collapsed;const locked=sceneLocked(scene);const status=shot.status==='confirmed'?'<span class="dot green"></span>已确认分镜视频':'<span class="dot red"></span>未确认分镜视频';return `<div class="shot ${collapsed?'collapsed':''} ${locked?'locked-ui':''}" ondragover="${locked?'':'shotDragOver(event,this)'}" ondragleave="this.classList.remove('drop-before')" ondrop="${locked?'':'shotDrop(event,\''+scene.scene_id+'\',\''+shot.shot_id+'\')'}"><div class="shot-head" ${locked?'':'draggable="true"'} ondragstart="${locked?'':'shotDragStart(event,\''+scene.scene_id+'\',\''+shot.shot_id+'\')'}" ondragend="shotDragEnd()"><span class="handle">⋮⋮</span><button class="btn ghost small" onclick="toggleShot('${shot.shot_id}')">${collapsed?'▸':'▾'}</button><div class="shot-title">${scene.scene_code}/${shot.shot_code}</div><div class="shot-duration"><input class="timebox" ${locked?'disabled':''} value="${esc(shot.duration_seconds||'')}" onchange="setShotDuration('${shot.shot_id}',this.value)">秒</div><div class="status">${status}</div><div class="shot-actions"><button class="btn danger small" ${locked?'disabled':''} onclick="deleteShot('${shot.shot_id}')">删除</button></div></div>${renderTabs(shot)}<div class="shot-body"><div class="tabbed-work"><div class="tab-content"><div class="shot-left">${renderSettings(shot,tab)}${renderRefs(shot,tab)}</div><div class="shot-center"><div class="chat">${renderVideoInfo(shot,tab)}${renderImages(shot,tab)}</div>${renderComposer(shot,tab)}</div></div></div>${renderStory(shot)}</div></div>`}
function setShotDuration(shotId,v){const f=shotById(shotId);if(!f||!assertEditShot(shotId))return;f.shot.duration_seconds=v.replace(/[^0-9]/g,'').slice(0,4);saveProject()}
function renderTabs(shot){const locked=!canEditShot(shot.shot_id);shot.tabs=shot.tabs||[];shot.tabs.sort((a,b)=>(a.sort_order||0)-(b.sort_order||0));return `<div class="shot-tabs">${shot.tabs.map(tab=>`<div class="work-tab ${tab.tab_id===shot.active_tab_id?'active':''}" ${locked?'':'draggable="true"'} ondragstart="${locked?'':'tabDragStart(event,\''+shot.shot_id+'\',\''+tab.tab_id+'\')'}" ondragover="${locked?'':'tabDragOver(event)'}" ondrop="${locked?'':'tabDrop(event,\''+shot.shot_id+'\',\''+tab.tab_id+'\')'}" onclick="setActiveTab('${shot.shot_id}','${tab.tab_id}')"><span ondblclick="event.stopPropagation();${locked?'':'renameTab(\''+shot.shot_id+'\',\''+tab.tab_id+'\')'}">${esc(tab.tab_name||'标签')}</span><button class="mini tab-close" ${locked?'disabled':''} onclick="event.stopPropagation();closeTab('${shot.shot_id}','${tab.tab_id}')">×</button></div>`).join('')}<button class="btn ghost small tab-add" ${locked?'disabled':''} onclick="addTab('${shot.shot_id}')">＋</button><div class="tab-menu"><button class="btn ghost small" ${locked?'disabled':''} onclick="copyTab('${shot.shot_id}')">复制</button></div></div>`}
function renderSettings(shot,tab){return `<div class="settings"><label>模型 ${sel('video_model',shot,tab,'seedance2.0',['seedance2.0','seedance2.0fast'])}</label><label>比例 ${sel('video_ratio',shot,tab,'16:9',ratios)}</label><label>分辨率 ${sel('video_resolution',shot,tab,'720p',resolutions)}</label><label>时长 ${inputSetting('video_duration',shot,tab,8,'number')}</label><label>Seed ${inputSetting('video_seed',shot,tab,'','text')}</label><label>同步音频 ${boolSel('video_generate_audio',shot,tab,true)}</label><label>保留水印 ${boolSel('video_watermark',shot,tab,false)}</label></div>`}
function sel(key,shot,tab,def,opts){const val=key==='video_model'?modelDisplay(tab.settings?.[key]||def):(tab.settings?.[key]||def);const dis=!canEditShot(shot.shot_id)?'disabled':'';return `<select ${dis} onchange="changeTabSetting('${shot.shot_id}','${tab.tab_id}','${key}',this.value)">${opts.map(o=>`<option ${o===val?'selected':''}>${o}</option>`).join('')}</select>`}
function inputSetting(key,shot,tab,def,type='text'){const val=tab.settings?.[key]??def;const dis=!canEditShot(shot.shot_id)?'disabled':'';return `<input class="mini-setting" type="${type}" ${dis} value="${esc(val)}" onchange="changeTabSetting('${shot.shot_id}','${tab.tab_id}','${key}',this.value)">`}
function boolSel(key,shot,tab,def=false){const val=tab.settings?.[key];const cur=(val===undefined?def:!!val)?'true':'false';const dis=!canEditShot(shot.shot_id)?'disabled':'';return `<select ${dis} onchange="changeTabSetting('${shot.shot_id}','${tab.tab_id}','${key}',this.value==='true')"><option value="true" ${cur==='true'?'selected':''}>开</option><option value="false" ${cur==='false'?'selected':''}>关</option></select>`}
function renderRefs(shot,tab){const locked=!canEditShot(shot.shot_id);const refs=(tab.referenced_assets||[]).map(assetById).filter(Boolean);return `<div class="refs" tabindex="0" ondragover="${locked?'':'overDrag(event,this)'}" ondragleave="this.classList.remove('drag')" ondrop="${locked?'':'dropOnRefs(event,\''+shot.shot_id+'\',\''+tab.tab_id+'\',this)'}" onpaste="${locked?'':'pasteImageToRefs(event,\''+shot.shot_id+'\',\''+tab.tab_id+'\')'}"><div class="refs-head"><span class="refs-title">当前标签页引用素材</span><button class="btn ghost small" ${locked?'disabled':''} onclick="pickTempRef('${shot.shot_id}','${tab.tab_id}')">＋</button></div><div class="refs-list">${refs.map(a=>`<div class="ref-chip" draggable="true" ondragstart="assetDrag(event,'${a.asset_id}')">${mediaThumb(a)}<span>${esc(a.name)}</span><button class="mini" ${locked?'disabled':''} onclick="quickRenameAsset('${a.asset_id}')">✎</button><button class="mini del" ${locked?'disabled':''} onclick="removeRef('${shot.shot_id}','${tab.tab_id}','${a.asset_id}')">×</button></div>`).join('')||'<span class="desc">拖入素材，或点击＋添加。引用只属于当前标签页。</span>'}</div></div>`}
function messageVersions(m){const vs=Array.isArray(m.content_versions)?m.content_versions:[];return vs.length?vs:[{content:m.content||'',time:m.time||'',label:'当前'}]}
function messageViewIndex(m){const vs=messageVersions(m);let i=Number(m.version_view_index);if(!Number.isFinite(i))i=vs.length-1;return Math.min(Math.max(i,0),vs.length-1)}
function messageDisplayContent(m){const vs=messageVersions(m);return vs[messageViewIndex(m)]?.content||''}
function messageVersionControls(shotId,tabId,m,locked){const vs=messageVersions(m);if(vs.length<=1)return '';const idx=messageViewIndex(m);return `<span class="msg-version"><button class="mini" ${idx<=0?'disabled':''} onclick="switchMessageVersion('${shotId}','${tabId}','${m.message_id}',-1)">‹</button><span>${idx+1}/${vs.length}</span><button class="mini" ${idx>=vs.length-1?'disabled':''} onclick="switchMessageVersion('${shotId}','${tabId}','${m.message_id}',1)">›</button></span>`}
function switchMessageVersion(shotId,tabId,msgId,d){const f=shotById(shotId);if(!f)return;const t=f.shot.tabs.find(x=>x.tab_id===tabId)||activeTab(f.shot);const m=(t.messages||[]).find(x=>x.message_id===msgId);if(!m)return;const vs=messageVersions(m);m.version_view_index=Math.min(Math.max(messageViewIndex(m)+d,0),vs.length-1);renderScenes();saveProject()}
function renderMessages(shot,tab){const locked=!canEditShot(shot.shot_id);const pending=pendingState[tab.tab_id];return (tab.messages||[]).map(m=>`<div class="msg ${m.role}"><div class="msg-role">${m.role==='user'?'用户':'系统'} · ${esc(m.time||'')} ${m.role==='user'?messageVersionControls(shot.shot_id,tab.tab_id,m,locked):''}</div><div class="bubble-row"><div class="bubble">${esc(messageDisplayContent(m))}</div>${m.role==='user'?`<button class="redo-btn" ${locked?'disabled':''} onclick="redoFromMessage('${shot.shot_id}','${tab.tab_id}','${m.message_id}')">↺ 重做</button>`:''}</div></div>`).join('')+(pending?`<div class="msg assistant"><div class="msg-role">系统 · 等待中</div><div class="bubble">⏳ ${esc(pending)}</div></div>`:'')}
function isImagePath(src){return /\.(png|jpe?g|webp|gif|bmp|svg)(\?|#|$)/i.test(src||'')||String(src||'').includes('/output/image/')}
function videoTag(src,thumb='',attrs=''){return `<video ${attrs} controls preload="metadata" playsinline poster="${esc(thumb||'')}" onloadeddata="capturePoster(this)" src="${esc(src||'')}"></video>`}
function mediaTag(src,thumb='',attrs=''){if(isVideoPath(src))return videoTag(src,thumb,attrs);if(isAudioPath(src))return `<div ${attrs} class="media-icon">♫</div>`;return `<img ${attrs} src="${esc(src||'')}" alt="素材">`}
function statusTimeMs(v){if(!v)return Date.now();if(typeof v==='number')return v;const t=Date.parse(String(v).replace(/-/g,'/'));return Number.isFinite(t)?t:Date.now()}
function runningStatusStart(last){return statusTimeMs(last.started_at||last.time||last.created_at||last.updated_at)}
function renderVideoInfo(shot,tab){const k=tab.tab_id;const p=pendingState[k];const last=tab.last_video_status||{};if(p&&p.state==='running')return `<div class="video-info running"><b>视频生成中 <span class="gen-elapsed" data-start="${p.start}">0</span>秒</b><br>${esc(p.stage||'正在提交任务，请等待。')}</div>`;if(p&&p.state==='error')return `<div class="video-info error"><b>生成失败</b><br>${esc(p.message||'视频生成失败。')}<details open><summary>技术详情</summary><pre>${esc(p.raw||'')}</pre></details></div>`;if(last.state==='running'||last.state==='submitting'||last.state==='polling')return `<div class="video-info running"><b>视频生成中 <span class="gen-elapsed" data-start="${runningStatusStart(last)}">0</span>秒</b><br>${esc(last.message||'任务已经提交，刷新页面后会继续保留此状态。')}${last.task_id?`<br><span class="desc">任务ID：${esc(last.task_id)}</span>`:''}</div>`;if(last.state==='error')return `<div class="video-info error"><b>上次生成失败</b><br>${esc(last.message||'视频生成失败。')}<details><summary>技术详情</summary><pre>${esc(last.error_detail_text||last.error||'')}</pre></details></div>`;if(last.state==='success')return `<div class="video-info success"><b>生成完成</b><br>${esc(last.message||'视频生成完成。')}</div>`;return `<div class="video-info"><b>视频生成信息</b><br>填写提示词后点击“生成视频”，这里会显示生成耗时、阶段状态、成功信息和失败原因。</div>`}
function renderImages(shot,tab){const locked=!canEditShot(shot.shot_id);return (tab.generated_images||[]).length?`<div class="image-grid">${tab.generated_images.map(img=>`<div class="gen-img">${mediaTag(img.file_path,img.thumb_path,`draggable="true" ondragstart="generatedImageDrag(event,'${shot.shot_id}','${img.image_id}')" ondblclick="openPreview('${img.file_path}', event)"`)}<div class="img-actions"><button class="overlay-btn" ${locked?'disabled':''} onclick="addCandidate('${shot.shot_id}','${img.image_id}')">加入待选</button><button class="overlay-btn danger" ${locked?'disabled':''} onclick="deleteGeneratedImage('${shot.shot_id}','${img.image_id}')">删除</button></div></div>`).join('')}</div>`:''}
function renderComposer(shot,tab){const locked=!canEditShot(shot.shot_id);const k=tab.tab_id, redo=redoState[k];const pst=promptUiState[k]||{};const h=cleanPx(tab.prompt_box_height||pst.height||180,180);return `<div class="composer"><div class="prompt-area"><div class="prompt-wrap"><div class="prompt-highlight" id="hl-${k}">${highlightPrompt((drafts[k]!==undefined?drafts[k]:(tab.draft_prompt||'')),k)}</div><textarea class="prompt" id="input-${k}" style="height:${h}px" ${locked?'disabled':''} placeholder="输入 Seedance 2.0 视频提示词。用 @素材名 调用当前标签页引用素材。" oninput="promptInput('${shot.shot_id}','${k}',this)" onkeyup="rememberPromptState('${k}',this,false);handleAtInput('${shot.shot_id}','${k}',this)" onclick="rememberPromptState('${k}',this,false);handleAtInput('${shot.shot_id}','${k}',this)" onmouseup="rememberPromptState('${k}',this,true)" onblur="rememberPromptState('${k}',this,true)" onwheel="handlePromptWheel(event,'${k}')" onscroll="rememberPromptState('${k}',this,false);syncHighlight('${k}');handleAtInput('${shot.shot_id}','${k}',this)" ondragover="${locked?'':'allowPromptDrop(event)'}" ondragleave="event.currentTarget.classList.remove('drag-over-prompt')" ondrop="${locked?'':'dropOnPrompt(event,\''+shot.shot_id+'\',\''+k+'\')'}" onpaste="${locked?'':'pasteImageToPrompt(event,\''+shot.shot_id+'\',\''+k+'\')'}">${esc(drafts[k]!==undefined?drafts[k]:(tab.draft_prompt||''))}</textarea></div>${redo?`<div class="desc">正在重做历史提示词，生成结果会追加到当前标签页末尾，旧视频和后续记录都会保留。</div>`:''}</div><div class="composer-actions"><button class="btn small" ${locked?'disabled':''} onclick="submitMessage('${shot.shot_id}','${k}')">生成视频</button></div><div class="suggest" id="suggest-${k}"></div></div>`}
function renderStory(shot){const mode=storyViewMode[shot.shot_id]||'story';const active=mode==='notes';return `<div class="story"><div class="story-head"><div class="story-switch"><button class="tab-btn ${!active?'active':''}" onclick="storyViewMode['${shot.shot_id}']='story';renderScenes()">待选分镜</button><button class="tab-btn ${active?'active':''}" onclick="storyViewMode['${shot.shot_id}']='notes';renderScenes()">审核备注</button></div></div>${active?renderReviewNote(shot):renderStoryboardPanel(shot)}</div>`}
function renderStoryboardPanel(shot){const locked=!canEditShot(shot.shot_id);const cs=shot.storyboard_candidates||[];const idx=storyIndex[shot.shot_id]||0;const c=cs[idx]||cs[0];const st=cs.some(x=>x.review_status==='approved')?'<span class="dot green"></span>已确认':cs.length?'<span class="dot yellow"></span>待确认':'';return `<div class="story-head">待选分镜素材 <span class="review-status">${st}</span><button class="btn ghost small" ${locked?'disabled':''} onclick="pickStoryFile('${shot.shot_id}')">＋素材</button></div><div class="story-preview" ondragover="${locked?'':'overDrag(event,this)'}" ondragleave="this.classList.remove('drag')" ondrop="${locked?'':'dropOnStory(event,\''+shot.shot_id+'\',this)'}">${c?mediaTag(c.file_path,c.thumb_path,`draggable="true" ondragstart="storyDrag(event,'${shot.shot_id}','${c.image_id}')" ondblclick="openPreview('${c.file_path}', event)"`):`<div class="story-empty">暂无待选素材<br>支持图片和视频<br>这是整个子栏共用区域</div>`}</div><div class="story-foot"><button class="arrow" onclick="moveStory('${shot.shot_id}',-1)">←</button><span>${cs.length?(idx+1)+' / '+cs.length:'0 / 0'}</span><button class="arrow" onclick="moveStory('${shot.shot_id}',1)">→</button>${c?`<button class="btn danger small" ${locked?'disabled':''} onclick="removeCandidate('${shot.shot_id}','${c.image_id}')">移除</button>`:''}</div>`}
function reviewNoteForShot(shot){const scene=findSceneByShotId(shot.shot_id);const rn=scene?currentRoundNo(scene):1;shot.review_notes_by_round=shot.review_notes_by_round||{};shot.review_notes_by_round[String(rn)]=shot.review_notes_by_round[String(rn)]||{text:'',images:[],active_index:0};return shot.review_notes_by_round[String(rn)]}
function renderReviewNote(shot){const locked=!canEditShot(shot.shot_id);const note=reviewNoteForShot(shot);const imgs=note.images||[];const idx=Math.min(Math.max(Number(note.active_index||0),0),Math.max(imgs.length-1,0));note.active_index=idx;const img=imgs[idx];return `<div class="story-head">审核备注 <button class="btn ghost small" ${locked?'disabled':''} onclick="pickNoteImages('${shot.shot_id}')">＋视频</button></div><div class="note-preview" ondragover="${locked?'':'overDrag(event,this)'}" ondragleave="this.classList.remove('drag')" ondrop="${locked?'':'dropOnNote(event,\''+shot.shot_id+'\',this)'}">${img?`<img draggable="true" ondragstart="noteImageDrag(event,'${shot.shot_id}','${img.note_image_id}')" ondblclick="openPreview('${img.file_path}', event)" src="${img.file_path}">`:'<div class="story-empty">拖入本地视频、工程素材或生成图作为审核备注图</div>'}</div><div class="note-tools"><button class="arrow" onclick="moveNoteImage('${shot.shot_id}',-1)">←</button><span>${imgs.length?(idx+1)+' / '+imgs.length:'0 / 0'}</span><button class="arrow" onclick="moveNoteImage('${shot.shot_id}',1)">→</button>${img?`<button class="btn danger small" ${locked?'disabled':''} onclick="removeNoteImage('${shot.shot_id}','${img.note_image_id}')">移除</button>`:''}</div><textarea class="note-box" ${locked?'disabled':''} placeholder="输入审核备注文字" onchange="setReviewNoteText('${shot.shot_id}',this.value)">${esc(note.text||'')}</textarea>`}
function setActiveTab(shotId,tabId){const f=shotById(shotId);if(!f)return;f.shot.active_tab_id=tabId;renderScenes();saveProject()}
function addTab(shotId){if(!assertEditShot(shotId))return;const f=shotById(shotId);if(!f)return;f.shot.tabs=f.shot.tabs||[];const t=newCleanTab(f.shot.tabs.length+1);f.shot.tabs.push(t);f.shot.active_tab_id=t.tab_id;renderScenes();saveProject()}
function copyTab(shotId){if(!assertEditShot(shotId))return;const f=shotById(shotId);if(!f)return;const src=activeTab(f.shot);const clone=JSON.parse(JSON.stringify(src));clone.tab_id=uid('tab');clone.tab_name=(src.tab_name||'标签')+' 副本';clone.sort_order=(f.shot.tabs||[]).length+1;clone.generated_images=(clone.generated_images||[]).map(img=>({...img,image_id:uid('vid'),video_id:null,copied_from_image_id:img.image_id}));clone.generated_images.forEach(x=>x.video_id=x.image_id);clone.generated_videos=clone.generated_images;clone.messages=(clone.messages||[]).map(m=>({...m,message_id:uid('msg')}));f.shot.tabs.push(clone);f.shot.active_tab_id=clone.tab_id;renderScenes();saveProject()}
function closeTab(shotId,tabId){if(!assertEditShot(shotId))return;const f=shotById(shotId);if(!f)return;if((f.shot.tabs||[]).length<=1)return toast('至少保留一个标签页');if(!confirm('关闭该标签页？不会删除文件夹中的视频。'))return;f.shot.tabs=f.shot.tabs.filter(t=>t.tab_id!==tabId);if(f.shot.active_tab_id===tabId)f.shot.active_tab_id=f.shot.tabs[0].tab_id;renderScenes();saveProject()}
function renameTab(shotId,tabId){if(!assertEditShot(shotId))return;const f=shotById(shotId);if(!f)return;const t=f.shot.tabs.find(x=>x.tab_id===tabId);const name=prompt('标签页名称',t.tab_name||'标签');if(name&&name.trim()){t.tab_name=name.trim();renderScenes();saveProject()}}
function tabDragStart(e,shotId,tabId){if(!canEditShot(shotId))return;dragTabInfo={shotId,tabId};e.dataTransfer.setData('text/plain',tabId)}
function tabDragOver(e){e.preventDefault()}
function tabDrop(e,shotId,targetTabId){e.preventDefault();if(!dragTabInfo||dragTabInfo.shotId!==shotId||dragTabInfo.tabId===targetTabId)return;const f=shotById(shotId);const tabs=f.shot.tabs;const from=tabs.findIndex(t=>t.tab_id===dragTabInfo.tabId);const to=tabs.findIndex(t=>t.tab_id===targetTabId);const [m]=tabs.splice(from,1);tabs.splice(to,0,m);tabs.forEach((t,i)=>t.sort_order=i+1);dragTabInfo=null;renderScenes();saveProject()}
function changeTabSetting(shotId,tabId,k,v){if(!assertEditShot(shotId))return;const f=shotById(shotId);if(f){const t=f.shot.tabs.find(x=>x.tab_id===tabId);t.settings=t.settings||{};if(k==='video_model')v=modelDisplay(v);t.settings[k]=v;if(k==='video_model'&&v==='seedance2.0fast'&&t.settings.video_resolution==='1080p'){t.settings.video_resolution='720p';toast('seedance2.0fast 暂不使用 1080p，已切换为 720p')}if(k==='video_resolution'&&v==='1080p'&&modelDisplay(t.settings.video_model)==='seedance2.0fast'){t.settings.video_resolution='720p';toast('seedance2.0fast 暂不使用 1080p，已切换为 720p')}saveProject();renderScenes()}}
function toggleChatMode(k){return} function clearBase(k){delete selectedBase[k];renderScenes()}
function scopedMentionAssets(tabId){
    if(!project)return [];
    const f=tabId?shotByTabId(tabId):null;
    const tab=f?.tab||(f?.shot?.tabs||[]).find(x=>x.tab_id===tabId);
    const refIds=Array.isArray(tab?.referenced_assets)?tab.referenced_assets:[];
    const seen=new Set();
    const out=[];
    refIds.forEach(id=>{
        const a=assetById(id);
        // @ 候选只来自“当前标签页引用素材”，临时拖入的引用素材也必须能 @。
        if(a&&a.name&&!seen.has(a.asset_id)){
            seen.add(a.asset_id);
            out.push(a);
        }
    });
    return out;
}
function findMentionMatches(text,tabId=null){
    const assets=tabId?scopedMentionAssets(tabId):[];
    const names=[...new Set(assets.filter(a=>a&&a.name).map(a=>String(a.name)))].sort((a,b)=>b.length-a.length);
    const lower=String(text||'').toLowerCase();
    const matches=[];
    let i=0;
    while(i<lower.length){
        if(lower[i]!=='@'){i++;continue}
        let found='';
        for(const name of names){
            if(lower.slice(i+1,i+1+name.length)===name.toLowerCase()){
                found=name;
                break;
            }
        }
        if(found){
            matches.push({start:i,end:i+1+found.length});
            i=i+1+found.length;
        }else{
            i++;
        }
    }
    return matches;
}
function highlightPrompt(s,tabId=null){
    s=String(s||'');
    const ms=findMentionMatches(s,tabId);
    if(!ms.length)return esc(s);
    let out='',last=0;
    for(const m of ms){
        out+=esc(s.slice(last,m.start));
        out+='<span class=\"mention\">'+esc(s.slice(m.start,m.end))+'</span>';
        last=m.end;
    }
    out+=esc(s.slice(last));
    return out;
}
function promptInput(shotId,k,ta){if(!canEditShot(shotId))return;drafts[k]=ta.value;const f=findTabById(k);if(f&&f.tab)f.tab.draft_prompt=ta.value;rememberPromptState(k,ta,false);$('hl-'+k).innerHTML=highlightPrompt(ta.value,k);syncHighlight(k);handleAtInput(shotId,k,ta);scheduleSaveProject()}
function syncHighlight(k){
    const ta=$('input-'+k),hl=$('hl-'+k);
    if(!ta||!hl)return;
    // 让高亮层的可换行宽度严格等于 textarea 的可输入宽度。
    // textarea 出现纵向滚动条后，offsetWidth 会包含滚动条，clientWidth 不包含；
    // 如果高亮层继续用 100% 宽度，长文本换行位置会和真实光标错开。
    const w=ta.clientWidth||ta.offsetWidth;
    const h=ta.clientHeight||ta.offsetHeight;
    hl.style.width=w+'px';
    hl.style.height=h+'px';
    hl.scrollTop=ta.scrollTop||0;
    hl.scrollLeft=ta.scrollLeft||0;
}
async function addScene(){updateFromServer(await api('/api/scene/add',{method:'POST',body:{project:currentProject}}))}
async function insertScene(id){const s=sceneById(id);if(s&&!assertEditScene(s))return;updateFromServer(await api('/api/scene/insert',{method:'POST',body:{project:currentProject,after_scene_id:id}}))}
async function deleteScene(id){const s=sceneById(id);if(!s||!assertEditScene(s))return;if(confirm('确定删除 SC？'))updateFromServer(await api('/api/scene/delete',{method:'POST',body:{project:currentProject,scene_id:id}}))}
function toggleScene(id){const s=sceneById(id);if(s){s.expanded=!s.expanded;renderScenes();saveProject()}}
async function addShot(id){const s=sceneById(id);if(!s||!assertEditScene(s))return;updateFromServer(await api('/api/shot/add',{method:'POST',body:{project:currentProject,scene_id:id}}))}
async function insertShot(sceneId,afterId){const s=sceneById(sceneId);if(!s||!assertEditScene(s))return;updateFromServer(await api('/api/shot/insert',{method:'POST',body:{project:currentProject,scene_id:sceneId,after_shot_id:afterId}}))}
async function deleteShot(id){if(!assertEditShot(id))return;if(confirm('确定删除子栏？'))updateFromServer(await api('/api/shot/delete',{method:'POST',body:{project:currentProject,shot_id:id}}))}
function toggleShot(id){const f=shotById(id);if(f){f.shot.collapsed=!f.shot.collapsed;renderScenes();saveProject()}}
function shotDragStart(e,sid,hid){const s=sceneById(sid);if(!s||!canEditScene(s))return;dragShotId=hid;dragSceneId=sid;e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',hid);const sh=e.currentTarget.closest('.shot');if(sh)setTimeout(()=>sh.classList.add('dragging'),0)}
function shotDragEnd(){document.querySelectorAll('.shot').forEach(x=>x.classList.remove('dragging','drop-before'));dragShotId=null;dragSceneId=null}
function shotDragOver(e,el){if(!dragShotId)return;e.preventDefault();el.classList.add('drop-before')}
async function shotDrop(e,sid,targetId){e.preventDefault();document.querySelectorAll('.shot').forEach(x=>x.classList.remove('drop-before'));const scene=sceneById(sid);if(!scene||!assertEditScene(scene))return;if(!dragShotId||dragShotId===targetId)return;const ids=scene.shots.map(s=>s.shot_id).filter(id=>id!==dragShotId);const idx=ids.indexOf(targetId);ids.splice(idx,0,dragShotId);updateFromServer(await api('/api/shot/reorder_drag',{method:'POST',body:{project:currentProject,scene_id:sid,shot_order:ids}}))}

function groupedCategories(){return ['人物','场景']}
function isGroupedCategory(cat){return groupedCategories().includes(cat)}
function ensureClientAssetGroups(){if(!project)return;project.asset_groups=Array.isArray(project.asset_groups)?project.asset_groups:[];(project.assets||[]).forEach(a=>{if(isGroupedCategory(a.category)&&!a.temporary&&!a.group_id){let g=(project.asset_groups||[]).filter(x=>x.category===a.category).sort((x,y)=>(x.sort_order||0)-(y.sort_order||0))[0];if(!g){g={group_id:uid('grp'),category:a.category,name:'未分组'+a.category,sort_order:1,expanded:true,created_at:new Date().toLocaleString()};project.asset_groups.push(g)}a.group_id=g.group_id}})}
function groupById(id){ensureClientAssetGroups();return (project.asset_groups||[]).find(g=>g.group_id===id)||null}
function groupsFor(cat){ensureClientAssetGroups();return (project.asset_groups||[]).filter(g=>g.category===cat).sort((a,b)=>(a.sort_order||0)-(b.sort_order||0))}
function assetsForGroup(cat,gid){return (project.assets||[]).filter(a=>!a.temporary&&a.category===cat&&a.group_id===gid)}
function assetGroupLabel(a){const g=a&&a.group_id?groupById(a.group_id):null;return g?`${a.category} / ${g.name}`:(a?.category||'素材')}
function groupDropHint(cat){return `拖入本地素材，或把已有素材拖到这里，加入该${cat}组`}
function renderSidePanel(){if(!$('sideContent')||!project)return;$('sideBtnAssets').classList.toggle('active',sideMode==='assets');$('sideBtnBoard').classList.toggle('active',sideMode==='board'); if(sideMode==='assets')renderSideAssets(); else renderSideBoard()}
function renderSideAssets(){
    ensureClientAssetGroups();
    const tabs=`<div class="asset-tabs">${categories.map(c=>`<button class="tab-btn ${c===sideCategory?'active':''}" onclick="sideCategory='${c}';renderSideAssets()">${c}</button>`).join('')}</div>`;
    if(isGroupedCategory(sideCategory)){
        const gs=groupsFor(sideCategory);
        $('sideContent').innerHTML=tabs+`<div class="group-tools"><button class="btn small" onclick="createAssetGroup('${sideCategory}')">＋ 新建${sideCategory}</button><span class="desc mini-desc">组名只用于分类，@ 引用具体素材名</span></div><div class="asset-groups side-groups">${gs.map(g=>renderAssetGroup(sideCategory,g,true)).join('')||`<div class="drop-hint">暂无${sideCategory}组，请先新建${sideCategory}</div>`}</div>`;
        bindSideGroupedDropZones(sideCategory);
        return;
    }
    const assets=(project.assets||[]).filter(a=>a.category===sideCategory&&!a.temporary);
    $('sideContent').innerHTML=tabs+`<div class="asset-grid side-drop" id="sideDrop"><div class="drop-hint">拖入本地素材，导入当前分类：${esc(sideCategory)}</div>${assets.map(a=>assetCard(a)).join('')||'<div class="drop-hint">暂无素材</div>'}</div>`;
    const el=$('sideDrop');
    el.ondragover=e=>overDrag(e,el);
    el.ondragleave=()=>el.classList.remove('drag');
    el.ondrop=e=>dropOnSideAssets(e,el);
}
function bindSideGroupedDropZones(cat){
    const root=$('sideContent');
    if(!root)return;
    const clear=()=>document.querySelectorAll('.side-groups .asset-group').forEach(x=>x.classList.remove('drag','drop-before'));
    root.querySelectorAll('.side-groups .asset-group,.side-groups .group-hint,.side-groups .group-grid').forEach(el=>{
        el.ondragover=e=>groupDragOver(e,el);
        el.ondragenter=e=>groupDragOver(e,el);
        el.ondragleave=e=>{const g=el.closest('.asset-group')||el;g.classList.remove('drag','drop-before')};
        el.ondrop=e=>{const g=el.closest('.asset-group');const gid=g?.dataset?.groupId;if(gid)dropOnAssetGroup(e,cat,gid,el)};
    });
    root.ondragover=e=>{
        if(sideMode!=='assets'||!isGroupedCategory(sideCategory))return;
        if(e.target.closest('.asset-group,.group-hint,.group-grid'))return;
        if(!hasDroppableDragData(e))return;
        e.preventDefault();
        if(e.dataTransfer)e.dataTransfer.dropEffect=dragTypes(e).includes('asset_group_id')?'move':'copy';
        const gs=groupsFor(sideCategory);
        if(gs.length===1){
            const only=root.querySelector(`.asset-group[data-group-id="${gs[0].group_id}"]`);
            if(only)only.classList.add('drag');
        }
    };
    root.ondragleave=e=>{if(!root.contains(e.relatedTarget))clear()};
    root.ondrop=async e=>{
        if(sideMode!=='assets'||!isGroupedCategory(sideCategory))return;
        if(e.target.closest('.asset-group,.group-hint,.group-grid'))return;
        if(!hasDroppableDragData(e))return;
        const gs=groupsFor(sideCategory);
        if(gs.length!==1){toast('请拖到具体素材组内');return;}
        e.preventDefault();
        const only=root.querySelector(`.asset-group[data-group-id="${gs[0].group_id}"]`)||root;
        await dropOnAssetGroup(e,sideCategory,gs[0].group_id,only);
        clear();
    };
}
function dragTypes(e){return Array.from(e.dataTransfer?.types||[]).map(t=>String(t).toLowerCase())}
function hasDroppableDragData(e){const types=dragTypes(e);return types.includes('files')||types.includes('asset_id')||types.includes('asset_ids')||types.includes('generated_image')||types.includes('text/uri-list')}
function assetCard(a){return `<div class="asset-card" draggable="true" ondblclick="openPreview('${a.file_path}', event)" ondragstart="event.stopPropagation();assetDrag(event,'${a.asset_id}')">${mediaThumb(a)}<div class="row"><div class="name" title="@${esc(a.name)}">@${esc(a.name)}</div><button class="mini" onclick="event.stopPropagation();quickRenameAsset('${a.asset_id}')">✎</button><button class="mini del" onclick="event.stopPropagation();deleteAsset('${a.asset_id}')">×</button></div></div>`}
function libraryAssetCard(a){return `<div class="library-card ${selectedAssets.has(a.asset_id)?'selected':''}" data-asset-id="${a.asset_id}" draggable="true" ondragstart="event.stopPropagation();libraryDrag(event,'${a.asset_id}')" onclick="toggleAssetSelect(event,'${a.asset_id}')">${mediaThumb(a)}<div class="library-row"><input value="${esc(a.name)}" onchange="renameAssetDirect('${a.asset_id}',this.value)" onclick="event.stopPropagation()" title="素材名全局唯一，@引用使用这个名字"><button class="mini" onclick="event.stopPropagation();quickRenameAsset('${a.asset_id}')">✎</button><button class="mini del" onclick="event.stopPropagation();deleteAsset('${a.asset_id}')">×</button></div></div>`}
function renderAssetGroup(cat,g,compact=false){
    const items=assetsForGroup(cat,g.group_id);
    const collapsed=!g.expanded;
    const groupName=esc(g.name||'未命名组');
    const foldText=collapsed?'展开':'折叠';
    return `<div class="asset-group ${compact?'compact':''} ${collapsed?'collapsed':''}" data-group-id="${g.group_id}" ondragover="groupDragOver(event,this)" ondragleave="this.classList.remove('drag','drop-before')" ondrop="dropOnAssetGroup(event,'${cat}','${g.group_id}',this)">
        <div class="asset-group-head">
            <div class="asset-group-top">
                <span class="handle group-handle" draggable="true" ondragstart="groupDragStart(event,'${cat}','${g.group_id}')" ondragend="groupDragEnd(event)" title="拖动素材组排序">⋮⋮</span>
                <b class="asset-group-title" title="${groupName}">${groupName}</b>
            </div>
            <div class="asset-group-controls">
                <button class="mini group-fold" onclick="event.stopPropagation();toggleAssetGroup('${g.group_id}')">${foldText}</button>
                <span class="meta asset-count">${items.length} 个素材</span>
                <button class="btn ghost small group-upload" onclick="event.stopPropagation();pickGroupUpload('${cat}','${g.group_id}')">＋素材</button>
                <button class="mini group-rename" onclick="event.stopPropagation();renameAssetGroup('${g.group_id}')">改组名</button>
                <button class="mini del group-delete" onclick="event.stopPropagation();deleteAssetGroup('${g.group_id}')">删组</button>
            </div>
        </div>
        ${collapsed?'':`<div class="group-hint" ondragover="groupDragOver(event,this)" ondragleave="this.closest('.asset-group')?.classList.remove('drag','drop-before')" ondrop="dropOnAssetGroup(event,'${cat}','${g.group_id}',this)">${groupDropHint(cat)}</div><div class="${compact?'asset-grid':'lib-grid'} group-grid" ondragover="groupDragOver(event,this)" ondragleave="this.closest('.asset-group')?.classList.remove('drag','drop-before')" ondrop="dropOnAssetGroup(event,'${cat}','${g.group_id}',this)" onmousedown="${compact?'':'startBoxSelect(event,\''+cat+'\')'}">${items.map(a=>compact?assetCard(a):libraryAssetCard(a)).join('')||'<div class="desc">暂无素材。点击＋素材或拖入文件。</div>'}</div>`}
    </div>`;
}

function renderSideBoard(){const cards=allShots().map(({scene,shot})=>{const cs=shot.storyboard_candidates||[];const idx=storyIndex[shot.shot_id]||0;const c=cs[idx]||cs[0];return `<div class="board-card"><h4>${scene.scene_code} / ${shot.shot_code}</h4><div class="board-img">${c?`${mediaTag(c.file_path,c.thumb_path,`ondblclick="openPreview('${c.file_path}', event)"`)}`:'<span class="desc">暂无待选素材</span>'}</div><div class="board-foot"><button class="arrow" onclick="moveStory('${shot.shot_id}',-1)">←</button><span>${cs.length?(idx+1)+' / '+cs.length:'0 / 0'}</span><button class="arrow" onclick="moveStory('${shot.shot_id}',1)">→</button></div></div>`}).join('');$('sideContent').innerHTML=`<div class="board-grid">${cards}</div>`}
function renderLibrary(){const root=$('libraryRoot');if(!root||!project)return;ensureClientAssetGroups();root.innerHTML=categories.map(cat=>{if(isGroupedCategory(cat)){const gs=groupsFor(cat);return `<div class="asset-column grouped-column" data-cat="${cat}"><h3>${cat}</h3><div class="group-tools"><button class="btn small" onclick="createAssetGroup('${cat}')">＋ 新建${cat}</button><span class="desc mini-desc">组名只负责归类，@ 引用素材名</span></div><div class="asset-groups">${gs.map(g=>renderAssetGroup(cat,g,false)).join('')||`<div class="desc">暂无${cat}组。请先点击“新建${cat}”。</div>`}</div></div>`}const items=(project.assets||[]).filter(a=>!a.temporary&&a.category===cat);return `<div class="asset-column" data-cat="${cat}" ondragover="overDrag(event,this)" ondragleave="this.classList.remove('drag')" ondrop="dropOnLibraryColumn(event,'${cat}',this)" onmousedown="startBoxSelect(event,'${cat}')"><h3>${cat}</h3><div class="lib-grid">${items.map(a=>libraryAssetCard(a)).join('')||'<div class="desc">拖素材到此栏目导入</div>'}</div></div>`}).join('');bindVideoLibraryFallbackDrop(root)}
function bindVideoLibraryFallbackDrop(root){if(!root)return;root.ondragover=e=>{if(!hasDroppableDragData(e))return;if(e.target.closest('.asset-column,.asset-group'))return;e.preventDefault();e.stopPropagation();if(e.dataTransfer)e.dataTransfer.dropEffect='copy';root.classList.add('drag')};root.ondragleave=()=>root.classList.remove('drag');root.ondrop=async e=>{if(e.target.closest('.asset-column,.asset-group'))return;if(!hasDroppableDragData(e))return;e.preventDefault();e.stopPropagation();root.classList.remove('drag');if(e.dataTransfer?.files?.length){const fallbackCat=$('assetCategory')?.value||sideCategory||'道具';await uploadAsset(e.dataTransfer.files,fallbackCat);return}const gen=e.dataTransfer.getData('generated_image');if(gen){const p=JSON.parse(gen);const fallbackCat=$('assetCategory')?.value||sideCategory||'道具';await uploadFileToLibrary(await fetchImageAsFile(p.file_path,p.name),fallbackCat);toast('已导入参考素材')}}}

async function createAssetGroup(cat){const label=cat==='人物'?'人物姓名':'场景名称';const name=prompt(`请输入${label}`,'');if(!name||!name.trim())return;try{updateFromServer(await api('/api/assets/group/create',{method:'POST',body:{project:currentProject,category:cat,name:name.trim()}}));toast(`已新建${cat}组`)}catch(e){toast(e.message)}}
async function renameAssetGroup(gid){const g=groupById(gid);if(!g)return;const name=prompt('修改素材组名称',g.name||'');if(!name||!name.trim()||name.trim()===g.name)return;try{updateFromServer(await api('/api/assets/group/rename',{method:'POST',body:{project:currentProject,group_id:gid,new_name:name.trim()}}))}catch(e){toast(e.message)}}
async function deleteAssetGroup(gid){const g=groupById(gid);if(!g)return;const n=(project.assets||[]).filter(a=>a.group_id===gid).length;if(!confirm(`删除素材组“${g.name}”会连同组内 ${n} 个素材一起删除，且会从所有提示词引用区移除。确定继续？`))return;if(!confirm('二次确认：删除后无法恢复，确认删除整个素材组？'))return;try{selectedAssets.clear();updateFromServer(await api('/api/assets/group/delete',{method:'POST',body:{project:currentProject,group_id:gid}}));toast('素材组已删除')}catch(e){toast(e.message)}}
function toggleAssetGroup(gid){const g=groupById(gid);if(!g)return;g.expanded=!g.expanded;renderAll();saveProject()}
function groupDragStart(e,cat,gid){e.stopPropagation();e.dataTransfer.setData('asset_group_id',gid);e.dataTransfer.setData('asset_group_cat',cat);e.dataTransfer.setData('text/plain',gid);e.dataTransfer.effectAllowed='move';const group=e.currentTarget.closest('.asset-group');if(group)setTimeout(()=>group.classList.add('dragging'),0)}
function groupDragEnd(e){document.querySelectorAll('.asset-group').forEach(x=>x.classList.remove('drag','drop-before','dragging'))}
function groupDragOver(e,el){e.preventDefault();e.stopPropagation();if(el&&el.closest)el=el.closest('.asset-group')||el;if(!el)return;el.classList.add('drag');const types=dragTypes(e);if(types.includes('asset_group_id')){el.classList.add('drop-before');if(e.dataTransfer)e.dataTransfer.dropEffect='move'}else{if(e.dataTransfer)e.dataTransfer.dropEffect='copy'}}
async function dropOnAssetGroup(e,cat,gid,el){e.preventDefault();e.stopPropagation();if(el&&el.closest)el=el.closest('.asset-group')||el;document.querySelectorAll('.asset-group').forEach(x=>x.classList.remove('drag','drop-before','dragging'));const dragged=e.dataTransfer.getData('asset_group_id');if(dragged){const fromCat=e.dataTransfer.getData('asset_group_cat');if(fromCat===cat&&dragged!==gid){const ids=groupsFor(cat).map(g=>g.group_id).filter(id=>id!==dragged);const idx=ids.indexOf(gid);ids.splice(idx<0?ids.length:idx,0,dragged);try{updateFromServer(await api('/api/assets/group/reorder',{method:'POST',body:{project:currentProject,category:cat,group_order:ids}}))}catch(err){toast(err.message)}}return}const ids=getDragAssetIds(e);if(ids.length){await moveAssets(ids,cat,gid);return}const gen=e.dataTransfer.getData('generated_image');if(gen){try{const p=JSON.parse(gen);await uploadFileToLibrary(await fetchImageAsFile(p.file_path,p.name),cat,gid);toast('已导入参考素材')}catch(err){toast('导入素材失败：'+err.message)}return}const files=Array.from(e.dataTransfer.files||[]).filter(isAssetFile);if(files.length){await uploadAsset(files,cat,gid);return}const url=e.dataTransfer.getData('text/uri-list')||e.dataTransfer.getData('text/plain');if(url&&/^https?:|^\//i.test(url)){try{await uploadFileToLibrary(await fetchImageAsFile(url,'拖入素材'),cat,gid);toast('已导入参考素材');return}catch(err){toast('导入素材失败：'+err.message);return}}toast('没有识别到可导入的素材，请拖入图片、音频或视频文件')}
function pickGroupUpload(cat,gid){const inp=$('assetFile');if(!inp)return toast('上传控件未找到');inp.value='';inp.onchange=async()=>{try{if(inp.files&&inp.files.length)await uploadAsset(inp.files,cat,gid)}finally{inp.value='';inp.onchange=null}};inp.click()}
async function uploadAsset(filesArg=null,category=null,groupId=null){let files=filesArg?Array.from(filesArg):Array.from($('assetFile')?.files||[]);files=files.filter(isAssetFile);if(!files.length)return toast('请选择图片、音频或视频素材');const chosen=category||$('assetCategory')?.value||sideCategory||'人物';if(isGroupedCategory(chosen)&&!groupId)return toast(`请先新建${chosen}组，并在具体组内上传素材`);for(const f of files){const cat=category?chosen:inferCategory(f,chosen);const gid=isGroupedCategory(cat)?groupId:'';if(isGroupedCategory(cat)&&!gid){toast(`“${cat}”素材需要先选择具体素材组`);continue}const name=(files.length===1&&$('assetName')?.value.trim())?$('assetName').value.trim():f.name.replace(/\.[^.]+$/,'');const dataUrl=await fileToDataUrl(f);try{const r=await api('/api/assets/upload',{method:'POST',body:{project:currentProject,category:cat,group_id:gid,name,filename:f.name,dataUrl}});project=r.data;term('导入素材：'+r.asset.name+' -> '+cat)}catch(e){toast(e.message);term('导入素材失败：'+e.message)}}if($('assetFile')){$('assetFile').value='';$('assetFile').onchange=null}if($('assetName'))$('assetName').value='';renderAll()}
async function quickRenameAsset(id){const a=assetById(id);const name=prompt('素材名称（全局唯一，@引用使用这个名字）',a?.name||'');if(name&&name.trim())await renameAssetDirect(id,name.trim())}
async function renameAssetDirect(id,name){const a=assetById(id);const oldName=a?.name||'';try{const r=await api('/api/assets/rename',{method:'POST',body:{project:currentProject,asset_id:id,new_name:name}});syncDraftMentions(oldName,r.new_name||r.asset?.name||name);updateFromServer(r)}catch(e){toast(e.message)}}
async function deleteAsset(id){if(confirm('删除素材？'))updateFromServer(await api('/api/assets/delete',{method:'POST',body:{project:currentProject,asset_id:id}}))}
function assetDrag(e,id){e.dataTransfer.setData('asset_id',id);e.dataTransfer.setData('asset_ids',JSON.stringify([id]));e.dataTransfer.setData('text/plain',id);e.dataTransfer.effectAllowed='copyMove'}
function generatedImageDrag(e,shotId,imageId){const f=shotById(shotId);let img=null;for(const t of f.shot.tabs||[]){img=(t.generated_images||[]).find(x=>x.image_id===imageId);if(img)break}if(img)e.dataTransfer.setData('generated_image',JSON.stringify({shotId,imageId,file_path:img.file_path,name:imageId}))}
function storyDrag(e,shotId,imageId){const c=(shotById(shotId).shot.storyboard_candidates||[]).find(x=>x.image_id===imageId);if(c)e.dataTransfer.setData('generated_image',JSON.stringify({shotId,imageId,file_path:c.file_path,name:imageId}))}
async function uploadFileToLibrary(file,cat=sideCategory,groupId=null){const explicitGroupedTarget=!!groupId&&isGroupedCategory(cat);cat=explicitGroupedTarget?cat:inferCategory(file,cat);if(isGroupedCategory(cat)&&!groupId){toast(`请先在${cat}栏新建组，并把素材放进具体组内`);throw new Error('missing asset group')}const dataUrl=await fileToDataUrl(file);const name=(file.name||('素材_'+Date.now()+'')).replace(/\.[^.]+$/,'');const r=await api('/api/assets/upload',{method:'POST',body:{project:currentProject,category:cat,group_id:groupId||'',name,filename:file.name||name+'',dataUrl}});project=r.data;renderAll();return r.asset}

async function uploadTempRefFile(shotId,tabId,file){
    if(!file)throw new Error('没有可导入的文件');
    const dataUrl=await fileToDataUrl(file);
    const name=(file.name||('引用素材_'+Date.now())).replace(/\.[^.]+$/,'');
    const r=await api('/api/asset/temp_upload',{method:'POST',body:{project:currentProject,shot_id:shotId,tab_id:tabId,name,filename:file.name||name,dataUrl}});
    updateFromServer(r);
    return r.asset;
}
async function uploadFileToLibraryAndRef(shotId,tabId,file,insertText=false){
    // 拖到“当前标签页引用素材”的本地文件，不再依赖右侧素材栏当前分类或人物/场景分组，直接作为当前标签页临时引用素材保存。
    const asset=await uploadTempRefFile(shotId,tabId,file);
    if(insertText)appendAtToTextarea(tabId,asset.name);
    else renderAll();
    return asset;
}
function overDrag(e,el){if(!hasDroppableDragData(e))return;e.preventDefault();if(e.dataTransfer)e.dataTransfer.dropEffect='copy';el.classList.add('drag')}
async function dropOnSideAssets(e,el){e.preventDefault();e.stopPropagation();el.classList.remove('drag');const aid=e.dataTransfer.getData('asset_id');if(aid)return;const gen=e.dataTransfer.getData('generated_image');if(gen){const p=JSON.parse(gen);await uploadFileToLibrary(await fetchImageAsFile(p.file_path,p.name),sideCategory);toast('已导入参考素材');return}if(e.dataTransfer.files?.length)await uploadAsset(e.dataTransfer.files,sideCategory)}
async function dropOnRefs(e,shotId,tabId,el){
    e.preventDefault();e.stopPropagation();el.classList.remove('drag');
    if(!assertEditShot(shotId))return;
    const ids=getDragAssetIds(e);
    if(ids.length){await addRefs(shotId,tabId,ids);return}
    const gen=e.dataTransfer.getData('generated_image');
    if(gen){const p=JSON.parse(gen);await uploadFileToLibraryAndRef(shotId,tabId,await fetchImageAsFile(p.file_path,p.name),false);return}
    if(e.dataTransfer.files?.length){
        for(const f of Array.from(e.dataTransfer.files).filter(isAssetFile))await uploadFileToLibraryAndRef(shotId,tabId,f,false);
        return;
    }
}
async function addRefs(shotId,tabId,ids){
    if(!assertEditShot(shotId))return false;
    const f=shotById(shotId);if(!f)return false;
    const t=f.shot.tabs.find(x=>x.tab_id===tabId)||activeTab(f.shot);if(!t)return false;
    t.referenced_assets=Array.isArray(t.referenced_assets)?t.referenced_assets:[];
    let changed=false;
    ids.filter(Boolean).forEach(aid=>{if(assetById(aid)&&!t.referenced_assets.includes(aid)){t.referenced_assets.push(aid);changed=true}});
    if(!changed)return false;
    const st=capturePromptStates();
    renderScenes(st);
    await saveProject();
    renderScenes(st);
    handleAtInput(shotId,tabId,$('input-'+tabId));
    return true;
}
async function addRef(shotId,tabId,aid){return addRefs(shotId,tabId,[aid])}
function removeRef(shotId,tabId,aid){if(!assertEditShot(shotId))return;const f=shotById(shotId);const t=f.shot.tabs.find(x=>x.tab_id===tabId)||activeTab(f.shot);if(f){t.referenced_assets=Array.isArray(t.referenced_assets)?t.referenced_assets:[];t.referenced_assets=t.referenced_assets.filter(x=>x!==aid);const st=capturePromptStates();renderScenes(st);saveProject().then(()=>renderScenes(st))}}
function pickTempRef(shotId,tabId){if(!assertEditShot(shotId))return;const inp=$('tempFile');inp.onchange=async()=>{try{for(const f of Array.from(inp.files||[]).filter(isAssetFile))await uploadFileToLibraryAndRef(shotId,tabId,f,false)}finally{inp.value=''}};inp.click()}
function allowPromptDrop(e){e.preventDefault();if(e.dataTransfer)e.dataTransfer.dropEffect='copy';e.currentTarget.classList.add('drag-over-prompt')}
async function dropOnPrompt(e,shotId,tabId){
    e.preventDefault();e.stopPropagation();e.currentTarget.classList.remove('drag-over-prompt');
    if(!assertEditShot(shotId))return;
    const ids=getDragAssetIds(e);
    if(ids.length){
        await addRefs(shotId,tabId,ids);
        const first=assetById(ids[0]);
        if(first)appendAtToTextarea(tabId,first.name);
        return;
    }
    const gen=e.dataTransfer.getData('generated_image');
    if(gen){const p=JSON.parse(gen);await uploadFileToLibraryAndRef(shotId,tabId,await fetchImageAsFile(p.file_path,p.name),true);return}
    if(e.dataTransfer.files?.length){
        const files=Array.from(e.dataTransfer.files).filter(isAssetFile);
        for(let i=0;i<files.length;i++)await uploadFileToLibraryAndRef(shotId,tabId,files[i],i===0);
        return;
    }
}
async function pasteImageToPrompt(e,shotId,tabId){if(!canEditShot(shotId))return;const imgs=Array.from(e.clipboardData?.items||[]).filter(i=>i.type.startsWith('image/'));if(!imgs.length)return;e.preventDefault();for(let i=0;i<imgs.length;i++){const blob=imgs[i].getAsFile();await uploadFileToLibraryAndRef(shotId,tabId,new File([blob],`粘贴视频_${Date.now()}_${i+1}.png`,{type:blob.type||'image/png'}),true)}}
async function pasteImageToRefs(e,shotId,tabId){if(!canEditShot(shotId))return;const imgs=Array.from(e.clipboardData?.items||[]).filter(i=>i.type.startsWith('image/'));if(!imgs.length)return;e.preventDefault();for(let i=0;i<imgs.length;i++){const blob=imgs[i].getAsFile();await uploadFileToLibraryAndRef(shotId,tabId,new File([blob],`粘贴视频_${Date.now()}_${i+1}.png`,{type:blob.type||'image/png'}),false)}}
function appendAtToTextarea(tabId,name){const ta=$('input-'+tabId);const base=ta?ta.value:(drafts[tabId]||'');const pos=ta?(ta.selectionStart||base.length):base.length;const insert='@'+String(name||'').replace(/^@+/,'')+' ';drafts[tabId]=base.slice(0,pos)+insert+base.slice(pos);if(ta){ta.value=drafts[tabId];ta.focus();ta.setSelectionRange(pos+insert.length,pos+insert.length);promptInput(shotByTabId(tabId)?.shot?.shot_id||'',tabId,ta);return}renderScenes();setTimeout(()=>$('input-'+tabId)?.focus(),30)}
function activeMentionRange(ta){const caret=ta.selectionStart||0;const before=ta.value.slice(0,caret);const atPos=before.lastIndexOf('@');if(atPos<0)return null;const q=before.slice(atPos+1);if(/\s/.test(q)||q.includes('@'))return null;return {start:atPos,end:caret,query:q.toLowerCase()}}
function handleAtInput(shotId,tabId,ta){
    const sg=$('suggest-'+tabId);
    if(!sg)return;
    const range=activeMentionRange(ta);
    if(!range){
        delete ta.dataset.mentionStart;
        delete ta.dataset.mentionEnd;
        sg.classList.remove('open');
        sg.innerHTML='';
        return;
    }
    ta.dataset.mentionStart=String(range.start);
    ta.dataset.mentionEnd=String(range.end);
    const scopedAssets=scopedMentionAssets(tabId);
    if(!scopedAssets.length){
        sg.classList.remove('open');
        sg.innerHTML='';
        return;
    }
    let list=scopedAssets.filter(a=>String(a.name||'').toLowerCase().includes(range.query));
    list=list.sort((a,b)=>String(a.name||'').localeCompare(String(b.name||''))).slice(0,30);
    if(!list.length){
        sg.classList.remove('open');
        sg.innerHTML='';
        return;
    }
    sg.innerHTML=list.map(a=>`<div class="suggest-item" data-asset-id="${esc(a.asset_id)}" onpointerdown="insertAssetSuggestion(event,'${tabId}','${jsstr(a.asset_id)}')" onmousedown="insertAssetSuggestion(event,'${tabId}','${jsstr(a.asset_id)}')">@${esc(a.name)} <span class="desc">当前标签页引用素材</span></div>`).join('');
    positionSuggest(sg,ta,ta.selectionStart||range.start);
    sg.classList.add('open');
}
function caretCoords(ta,pos){
    const div=document.createElement('div');
    const st=getComputedStyle(ta);
    ['fontFamily','fontSize','fontWeight','lineHeight','letterSpacing','textTransform','wordSpacing','paddingTop','paddingRight','paddingBottom','paddingLeft','borderTopWidth','borderRightWidth','borderBottomWidth','borderLeftWidth','boxSizing','tabSize','fontVariantLigatures','textRendering'].forEach(k=>div.style[k]=st[k]);
    div.style.position='absolute';
    div.style.visibility='hidden';
    div.style.whiteSpace='pre-wrap';
    div.style.overflowWrap='break-word';
    div.style.wordBreak='break-word';
    div.style.overflow='hidden';
    div.style.width=(ta.clientWidth||ta.offsetWidth)+'px';
    div.textContent=ta.value.substring(0,pos);
    const span=document.createElement('span');
    span.textContent='​';
    div.appendChild(span);
    document.body.appendChild(div);
    const r=ta.getBoundingClientRect();
    const dr=div.getBoundingClientRect();
    const sr=span.getBoundingClientRect();
    const line=parseFloat(st.lineHeight)||parseFloat(st.fontSize)||18;
    const out={left:r.left+sr.left-dr.left-(ta.scrollLeft||0),top:r.top+sr.top-dr.top-(ta.scrollTop||0)+line};
    div.remove();
    return out;
}
function positionSuggest(sg,ta,pos){try{const c=caretCoords(ta,Number.isFinite(pos)?pos:ta.selectionStart);sg.style.left=Math.min(window.innerWidth-260,Math.max(8,c.left))+'px';sg.style.top=Math.min(window.innerHeight-220,Math.max(8,c.top+6))+'px'}catch(e){const r=ta.getBoundingClientRect();sg.style.left=(r.left+12)+'px';sg.style.top=(r.bottom-6)+'px'}}
function insertAssetSuggestion(e,tabId,assetId){if(e){e.preventDefault();e.stopPropagation()}const a=assetById(assetId);if(!a){toast('素材不存在或已被删除');return}insertAt(tabId,a.name,a.asset_id)}
function insertAt(tabId,name,assetId){const ta=$('input-'+tabId);if(!ta)return;const v=ta.value;let start=parseInt(ta.dataset.mentionStart||'-1',10);let end=parseInt(ta.dataset.mentionEnd||String(ta.selectionStart||0),10);if(start<0||start>v.length){const range=activeMentionRange(ta);if(range){start=range.start;end=range.end}else{start=ta.selectionStart||0;end=start}}name=String(name||'').replace(/^@+/,'');const insert='@'+name+' ';drafts[tabId]=v.slice(0,start)+insert+v.slice(end);if(assetId){const f=shotByTabId(tabId);const t=f?.tab||(f?.shot?.tabs||[]).find(x=>x.tab_id===tabId);if(t){t.referenced_assets=t.referenced_assets||[];if(!t.referenced_assets.includes(assetId))t.referenced_assets.push(assetId)}}const cursor=start+insert.length;const sg=$('suggest-'+tabId);if(sg){sg.classList.remove('open');sg.innerHTML=''}ta.value=drafts[tabId];ta.focus();ta.setSelectionRange(cursor,cursor);rememberPromptState(tabId,ta,false);saveProject();const st=capturePromptStates();st[tabId]={...(st[tabId]||{}),selectionStart:cursor,selectionEnd:cursor,scrollTop:ta.scrollTop||0,scrollLeft:ta.scrollLeft||0,height:ta.offsetHeight};st._active=tabId;renderScenes(st);setTimeout(()=>{const nt=$('input-'+tabId);if(nt){nt.focus();nt.setSelectionRange(cursor,cursor);const hl=$('hl-'+tabId);if(hl)hl.innerHTML=highlightPrompt(drafts[tabId]||'',tabId);syncHighlight(tabId)}},30)}
async function submitMessage(shotId,tabId){if(!assertEditShot(shotId))return;const msg=(drafts[tabId]||'').trim();if(!msg)return toast('请输入视频提示词');const started=Date.now();pendingState[tabId]={state:'running',start:started,stage:'正在提交任务，等待 Seedance 生成视频…'};const f=findTabById(tabId);if(f&&f.tab){f.tab.draft_prompt=msg;f.tab.last_video_status={state:'running',time:new Date().toISOString(),started_at:new Date().toISOString(),message:'视频正在生成中。刷新页面后会保留正在生成状态。',prompt:msg}}renderScenes();const redoId=redoState[tabId]?.messageId||null;try{await saveProject();const r=await api('/api/video/generate',{method:'POST',body:{project:currentProject,shot_id:shotId,tab_id:tabId,message:msg,mode:'generate',redo_message_id:redoId,api_key:$('apiKey').value.trim(),user_name:currentUserName}});preservePromptDraftForTab(tabId,msg,r.data);delete redoState[tabId];delete pendingState[tabId];updateFromServer(r);if(r.cost_details)term(`视频生成完成：${r.task_id||''}，预估消耗 ¥${Number(r.cost_details.estimated_cny||0).toFixed(4)}`);else term('视频生成完成：'+(r.task_id||''));setTimeout(hydrateVideoThumbnails,80)}catch(e){const title=apiErrorTitle(e);const detail=apiErrorDetail(e);if(e.data){project=e.data;currentProject=project.project_id;if(e.usage)updateUsageUI(e.usage)}pendingState[tabId]={state:'error',message:title,raw:detail};renderScenes();toast(title);term('失败：'+title);if(detail&&detail!==title)term('详细错误：\n'+detail)}}
function redoFromMessage(shotId,tabId,messageId){if(!assertEditShot(shotId))return;const f=shotById(shotId);const t=f.shot.tabs.find(x=>x.tab_id===tabId)||activeTab(f.shot);const msg=(t.messages||[]).find(m=>m.message_id===messageId);if(!msg)return;redoState[tabId]={messageId};drafts[tabId]=messageDisplayContent(msg);renderScenes()}
function editFromImage(tabId,imageId){selectedBase[tabId]=imageId;renderScenes()}
async function deleteGeneratedImage(shotId,imageId){if(!assertEditShot(shotId))return;if(!confirm('从视频生成区删除这条视频？文件夹里的视频文件会保留。'))return;updateFromServer(await api('/api/video/delete',{method:'POST',body:{project:currentProject,shot_id:shotId,image_id:imageId}}))}
async function addCandidate(shotId,imageId){if(!assertEditShot(shotId))return;updateFromServer(await api('/api/storyboard/add_candidate',{method:'POST',body:{project:currentProject,shot_id:shotId,image_id:imageId}}))}
async function removeCandidate(shotId,imageId){if(!assertEditShot(shotId))return;updateFromServer(await api('/api/storyboard/remove_candidate',{method:'POST',body:{project:currentProject,shot_id:shotId,image_id:imageId}}))}
function moveStory(shotId,d){const f=shotById(shotId);const n=(f.shot.storyboard_candidates||[]).length;if(!n)return;storyIndex[shotId]=((storyIndex[shotId]||0)+d+n)%n;renderAll()}
function pickStoryFile(shotId){if(!assertEditShot(shotId))return;const inp=$('storyFile');inp.onchange=async()=>{if(inp.files[0])await uploadStoryFile(shotId,inp.files[0]);inp.value=''};inp.click()}
async function uploadStoryFile(shotId,file){if(!assertEditShot(shotId))return;const f=shotById(shotId);const tabId=activeTab(f.shot).tab_id;const dataUrl=await fileToDataUrl(file);updateFromServer(await api('/api/storyboard/upload_candidate',{method:'POST',body:{project:currentProject,shot_id:shotId,tab_id:tabId,name:file.name.replace(/\.[^.]+$/,''),filename:file.name,dataUrl}}))}
async function dropOnStory(e,shotId,el){e.preventDefault();el.classList.remove('drag');if(!assertEditShot(shotId))return;const gen=e.dataTransfer.getData('generated_image');if(gen){const p=JSON.parse(gen);await uploadStoryFile(shotId,await fetchImageAsFile(p.file_path,p.name));return}const aid=e.dataTransfer.getData('asset_id');if(aid){const a=assetById(aid);await uploadStoryFile(shotId,await fetchImageAsFile(a.file_path,a.name));return}if(e.dataTransfer.files?.length)await uploadStoryFile(shotId,e.dataTransfer.files[0])}
async function uploadNoteImageFile(shotId,file){if(!assertEditShot(shotId)||!file||!isImageFile(file))return;const f=shotById(shotId);const scene=f.scene;const dataUrl=await fileToDataUrl(file);const r=await api('/api/review/note_image_upload',{method:'POST',body:{project:currentProject,scene_id:scene.scene_id,shot_id:shotId,round:currentRoundNo(scene),filename:file.name||'审核备注视频.png',dataUrl}});project=r.data;storyViewMode[shotId]='notes';renderAll();setSave('已保存')}
function pickNoteImages(shotId){if(!assertEditShot(shotId))return;const inp=$('noteFile');inp.onchange=async()=>{const files=Array.from(inp.files||[]).filter(isImageFile);for(const f of files)await uploadNoteImageFile(shotId,f);inp.value=''};inp.click()}
async function dropOnNote(e,shotId,el){e.preventDefault();el.classList.remove('drag');if(!assertEditShot(shotId))return;const gen=e.dataTransfer.getData('generated_image');if(gen){const info=JSON.parse(gen);await uploadNoteImageFile(shotId,await fetchImageAsFile(info.file_path,info.name||'工程视频'));return}const aid=e.dataTransfer.getData('asset_id');if(aid){const a=assetById(aid);if(a)await uploadNoteImageFile(shotId,await fetchImageAsFile(a.file_path,a.name||'素材'));return}if(e.dataTransfer.files?.length){for(const f of Array.from(e.dataTransfer.files).filter(isImageFile))await uploadNoteImageFile(shotId,f)}}
function moveNoteImage(shotId,d){const f=shotById(shotId);if(!f)return;const note=reviewNoteForShot(f.shot);const n=(note.images||[]).length;if(!n)return;note.active_index=((note.active_index||0)+d+n)%n;renderScenes();saveProject()}
function setReviewNoteText(shotId,text){if(!assertEditShot(shotId))return;const f=shotById(shotId);const note=reviewNoteForShot(f.shot);note.text=text;saveProject()}
function removeNoteImage(shotId,imgId){if(!assertEditShot(shotId))return;const f=shotById(shotId);const note=reviewNoteForShot(f.shot);note.images=(note.images||[]).filter(x=>x.note_image_id!==imgId);note.active_index=Math.min(note.active_index||0,Math.max(note.images.length-1,0));renderScenes();saveProject()}
function noteImageDrag(e,shotId,imgId){const f=shotById(shotId);const note=reviewNoteForShot(f.shot);const img=(note.images||[]).find(x=>x.note_image_id===imgId);if(img)e.dataTransfer.setData('generated_image',JSON.stringify({shotId,imageId:imgId,file_path:img.file_path,name:img.name||'审核备注图'}))}
function capturePoster(v){try{if(v.dataset.posterDone||v.poster)return;const draw=()=>{try{const c=document.createElement('canvas');const w=v.videoWidth||320,h=v.videoHeight||180;if(!w||!h)return;c.width=w;c.height=h;c.getContext('2d').drawImage(v,0,0,w,h);v.poster=c.toDataURL('image/jpeg',0.78);v.dataset.posterDone='1'}catch(e){}};if(v.readyState>=2){if(v.currentTime<0.4){v.currentTime=Math.min(0.6,Math.max(0.1,(v.duration||1)/4));v.addEventListener('seeked',draw,{once:true})}else draw()}}catch(e){}}
function hydrateVideoThumbnails(){document.querySelectorAll('video').forEach(v=>capturePoster(v))}
function updateElapsed(){document.querySelectorAll('.gen-elapsed').forEach(el=>{const s=Number(el.dataset.start||Date.now());el.textContent=Math.max(0,Math.floor((Date.now()-s)/1000))})}
setInterval(updateElapsed,1000);
let fullscreenPreviewEl=null;
function stopEvent(e){if(!e)return;try{e.preventDefault()}catch(err){}try{e.stopPropagation()}catch(err){}}
function fsElement(){return document.fullscreenElement||document.webkitFullscreenElement||document.msFullscreenElement||null}
function requestFs(el){const fn=el.requestFullscreen||el.webkitRequestFullscreen||el.msRequestFullscreen;return fn?fn.call(el):Promise.reject(new Error('当前浏览器不支持全屏预览'))}
function pauseAllPageMedia(except=null){document.querySelectorAll('video,audio').forEach(el=>{try{if(el!==except)el.pause()}catch(e){}})}
function hidePreviewModal(){const m=$('previewModal');if(!m)return;m.querySelectorAll('video,audio').forEach(el=>{try{el.pause();el.removeAttribute('src');el.load()}catch(err){}});m.classList.remove('show');m.innerHTML=''}
function cleanupFullscreenPreview(){const el=fullscreenPreviewEl;if(!el)return;fullscreenPreviewEl=null;try{el.pause()}catch(e){}try{el.classList.remove('fullscreen-active-video')}catch(e){}if(el.dataset&&el.dataset.fullscreenTemp==='1'){try{el.removeAttribute('src');el.load()}catch(e){}try{el.remove()}catch(e){}}}
function onFullscreenPreviewChange(){if(!fsElement()&&fullscreenPreviewEl)cleanupFullscreenPreview()}
document.addEventListener('fullscreenchange',onFullscreenPreviewChange);document.addEventListener('webkitfullscreenchange',onFullscreenPreviewChange);document.addEventListener('msfullscreenchange',onFullscreenPreviewChange);
function closePreview(e){const fs=fsElement();if(fullscreenPreviewEl&&fs===fullscreenPreviewEl){try{(document.exitFullscreen||document.webkitExitFullscreen||document.msExitFullscreen).call(document)}catch(err){}return}const m=$('previewModal');if(!m)return;if(e&&e.target!==m)return;hidePreviewModal()}
async function openVideoFullscreen(src,e){e=e||window.event;stopEvent(e);hidePreviewModal();cleanupFullscreenPreview();let v=null;if(e){const t=e.currentTarget||e.target;if(t&&String(t.tagName||'').toUpperCase()==='VIDEO')v=t;else if(t&&t.closest)v=t.closest('video')}if(!v){v=document.createElement('video');v.dataset.fullscreenTemp='1';v.controls=true;v.preload='auto';v.playsInline=false;v.src=src;Object.assign(v.style,{position:'fixed',left:'-10000px',top:'0',width:'1px',height:'1px',background:'#000',zIndex:'99999'});document.body.appendChild(v)}else if(src&&!(v.currentSrc||v.src)){v.src=src}
fullscreenPreviewEl=v;v.controls=true;v.classList.add('fullscreen-active-video');pauseAllPageMedia(v);try{const fsPromise=Promise.resolve(requestFs(v));const playPromise=v.play?v.play().catch(()=>{}):Promise.resolve();await fsPromise;await playPromise;if(v.paused&&v.play)await v.play().catch(()=>{});}catch(err){console.warn('全屏预览失败',err);cleanupFullscreenPreview();toast('浏览器阻止了全屏预览，请再双击一次视频或检查浏览器权限。')}}
function openPreview(src,e){if(!src)return;e=e||window.event;stopEvent(e);if(isVideoPath(src)){openVideoFullscreen(src,e);return}pauseAllPageMedia();cleanupFullscreenPreview();const m=$('previewModal');const safe=esc(src);m.innerHTML=isAudioPath(src)?`<audio controls autoplay src="${safe}" style="width:70vw" onclick="event.stopPropagation()" ondblclick="event.preventDefault();event.stopPropagation()"></audio>`:`<img id="previewImage" src="${safe}" onclick="event.stopPropagation()" ondblclick="event.preventDefault();event.stopPropagation()">`;m.classList.add('show');const media=m.querySelector('audio');if(media&&media.play){media.play().catch(()=>{})}}
function escapeRegExp(s){return String(s||'').replace(/[.*+?^${}()|[\]\\]/g,'\\$&')}
function knownAssetMentionNames(){const names=new Set();(project?.assets||[]).forEach(a=>{if(a&&a.name)names.add(String(a.name).replace(/^@+/,''))});return Array.from(names)}
function replaceAtMentionsText(text,oldName,newName,protectedNames=null){
    if(typeof text!=='string'||!oldName||!newName||oldName===newName)return text;
    const oldToken=String(oldName).replace(/^@+/,'');
    const newToken=String(newName).replace(/^@+/,'');
    if(!oldToken||!newToken||oldToken===newToken)return text;
    const names=Array.isArray(protectedNames)?protectedNames:knownAssetMentionNames();
    const oldLower=oldToken.toLowerCase();
    const longerNames=names.map(n=>String(n||'').replace(/^@+/,'')).filter(n=>n&&n!==oldToken&&n.toLowerCase().startsWith(oldLower)).sort((a,b)=>b.length-a.length);
    const re=new RegExp('(^|[^A-Za-z0-9._%+\\-])@'+escapeRegExp(oldToken),'g');
    return text.replace(re,(match,prefix,offset,full)=>{
        const atIndex=offset+prefix.length;
        const nameStart=atIndex+1;
        const nameEnd=nameStart+oldToken.length;
        const tail=full.slice(nameEnd);
        if(/^[A-Za-z0-9_\-＿]/.test(tail))return match;
        const restLower=full.slice(nameStart).toLowerCase();
        for(const n of longerNames){if(restLower.startsWith(n.toLowerCase()))return match;}
        return prefix+'@'+newToken;
    });
}
function syncDraftMentions(oldName,newName){
    if(!oldName||!newName||oldName===newName)return 0;
    let changed=0;
    const protectedNames=knownAssetMentionNames();
    const apply=(s)=>replaceAtMentionsText(s,oldName,newName,protectedNames);
    Object.keys(drafts||{}).forEach(k=>{
        const before=String(drafts[k]||'');
        const after=apply(before);
        if(after!==before){drafts[k]=after;changed++;}
    });
    if(project&&Array.isArray(project.scenes)){
        project.scenes.forEach(scene=>{
            (scene.shots||[]).forEach(shot=>{
                if(typeof shot.draft_prompt==='string'){
                    const before=shot.draft_prompt, after=apply(before);
                    if(after!==before){shot.draft_prompt=after;changed++;}
                }
                (shot.tabs||[]).forEach(tab=>{
                    if(typeof tab.draft_prompt==='string'){
                        const before=tab.draft_prompt, after=apply(before);
                        if(after!==before){tab.draft_prompt=after;changed++;}
                    }
                });
            });
        });
    }
    document.querySelectorAll('textarea.prompt').forEach(ta=>{
        const before=ta.value||'';
        const after=apply(before);
        if(after!==before){
            const tabId=ta.id.replace(/^input-/,'');
            const start=ta.selectionStart||0,end=ta.selectionEnd||start,scrollTop=ta.scrollTop||0,scrollLeft=ta.scrollLeft||0;
            ta.value=after;
            drafts[tabId]=after;
            const f=findTabById(tabId);if(f&&f.tab)f.tab.draft_prompt=after;
            const delta=after.length-before.length;
            try{ta.setSelectionRange(Math.max(0,start+delta),Math.max(0,end+delta));}catch(e){}
            ta.scrollTop=scrollTop;ta.scrollLeft=scrollLeft;
            const hl=$('hl-'+tabId);if(hl)hl.innerHTML=highlightPrompt(after,tabId);
            try{syncHighlight(tabId)}catch(e){}
            changed++;
        }
    });
    return changed;
}
function allGeneratedItems(){const items=[];(project?.scenes||[]).forEach(scene=>{(scene.shots||[]).forEach(shot=>{(shot.tabs||[]).forEach(tab=>{const list=(tab.generated_images&&tab.generated_images.length)?tab.generated_images:(tab.generated_videos||[]);list.forEach(img=>{items.push({scene,shot,tab,img})})})})});return items}
function imageName(img){return img.display_name||img.name||String(img.file_path||'video').split('/').pop().replace(/\.[^.]+$/,'')}
function renderStorage(){const root=$('storageRoot');if(!root||!project)return;const items=allGeneratedItems();const groups={};items.forEach(it=>{const sk=it.scene.scene_code||'未命名SC';const hk=it.shot.shot_code||'01';groups[sk]=groups[sk]||{};groups[sk][hk]=groups[sk][hk]||[];groups[sk][hk].push(it)});$('storageCount').textContent=`共 ${items.length} 条生成视频`;const html=Object.keys(groups).sort().map(sc=>`<div class="storage-sc"><h3>${esc(sc)}</h3>${Object.keys(groups[sc]).sort().map(h=>`<div class="storage-shot"><h4>${esc(sc)}/${esc(h)}</h4><div class="storage-grid" onmousedown="startStorageBoxSelect(event)">${groups[sc][h].map(({scene,shot,tab,img})=>storageCard(scene,shot,tab,img)).join('')}</div></div>`).join('')}</div>`).join('')||'<div class="desc">暂无生成视频。</div>';root.innerHTML=html}

function getDragAssetIds(e){try{const multi=e.dataTransfer.getData('asset_ids');if(multi){const parsed=JSON.parse(multi);return Array.isArray(parsed)?parsed.filter(Boolean):[]}}catch(err){}const aid=e.dataTransfer.getData('asset_id');return aid?[aid]:[]}
function libraryDrag(e,id){const ids=selectedAssets.has(id)?Array.from(selectedAssets):[id];e.dataTransfer.setData('asset_ids',JSON.stringify(ids));e.dataTransfer.setData('asset_id',id);e.dataTransfer.setData('text/plain',ids.join(','));e.dataTransfer.effectAllowed='copyMove'}
function toggleAssetSelect(e,id){if(e.ctrlKey||e.metaKey){selectedAssets.has(id)?selectedAssets.delete(id):selectedAssets.add(id)}else{selectedAssets.clear();selectedAssets.add(id)}renderLibrary()}
function startBoxSelect(e,cat){if(e.target.closest('.library-card')||e.button!==0)return;const box=$('selectBox');selecting=true;selectedAssets.clear();selectStart={x:e.clientX,y:e.clientY,cat};box.style.display='block';box.style.left=e.clientX+'px';box.style.top=e.clientY+'px';box.style.width='0px';box.style.height='0px';document.onmousemove=boxSelectMove;document.onmouseup=endBoxSelect}
function boxSelectMove(e){if(!selecting)return;const x=Math.min(e.clientX,selectStart.x),y=Math.min(e.clientY,selectStart.y),w=Math.abs(e.clientX-selectStart.x),h=Math.abs(e.clientY-selectStart.y);const box=$('selectBox');Object.assign(box.style,{left:x+'px',top:y+'px',width:w+'px',height:h+'px'});selectedAssets.clear();document.querySelectorAll(`.asset-column[data-cat="${selectStart.cat}"] .library-card`).forEach(card=>{const r=card.getBoundingClientRect();if(!(r.right<x||r.left>x+w||r.bottom<y||r.top>y+h))selectedAssets.add(card.dataset.assetId)});renderLibrary()}
function endBoxSelect(){selecting=false;$('selectBox').style.display='none';document.onmousemove=null;document.onmouseup=null;renderLibrary()}
async function dropOnLibraryColumn(e,cat,el){e.preventDefault();e.stopPropagation();el.classList.remove('drag');if(isGroupedCategory(cat)){toast('请拖到具体素材组内');return}const ids=getDragAssetIds(e);if(ids.length){await moveAssets(ids,cat);return}if(e.dataTransfer.files?.length){await uploadAsset(e.dataTransfer.files,cat)}}

async function moveAssets(ids,cat,groupId=null){const r=await api('/api/assets/move',{method:'POST',body:{project:currentProject,asset_ids:ids,category:cat,group_id:groupId||''}});selectedAssets.clear();updateFromServer(r)}
function storageCard(scene,shot,tab,img){const id=img.image_id;const sel=selectedStorageImages.has(id);return `<div class="storage-card ${sel?'selected':''}" data-image-id="${id}" oncontextmenu="storageContext(event,'${id}')" onclick="storageClick(event,'${id}')">${mediaTag(img.file_path,img.thumb_path,`draggable="true" ondragstart="generatedImageDrag(event,'${shot.shot_id}','${id}')" ondblclick="event.stopPropagation();openPreview('${img.file_path}', event)"`)}<div class="storage-meta"><b>${esc(scene.scene_code)}/${esc(shot.shot_code)}</b><span>${esc(tab.tab_name||'标签')}</span></div><input class="storage-name" value="${esc(imageName(img))}" onclick="event.stopPropagation()" onchange="renameStorageImage('${id}',this.value)"></div>`}
function toggleStorageBatch(){storageBatchMode=!storageBatchMode;selectedStorageImages.clear();$('storageBatchBtn').textContent=storageBatchMode?'退出批量管理':'批量管理';renderStorage()}
function storageClick(e,id){if(storageBatchMode||e.ctrlKey||e.metaKey){selectedStorageImages.has(id)?selectedStorageImages.delete(id):selectedStorageImages.add(id);renderStorage();return}const it=allGeneratedItems().find(x=>x.img.image_id===id);if(it)openPreview(it.img.file_path)}
function storageContext(e,id){e.preventDefault();if(id&&!selectedStorageImages.has(id)){selectedStorageImages.clear();selectedStorageImages.add(id);renderStorage()}const menu=$('storageMenu');menu.style.display='block';menu.style.left=e.clientX+'px';menu.style.top=e.clientY+'px';$('storageMenuCount').textContent=selectedStorageImages.size||1}
function hideStorageMenu(){const m=$('storageMenu');if(m)m.style.display='none'}
async function deleteSelectedStorage(){hideStorageMenu();const ids=Array.from(selectedStorageImages);if(!ids.length)return toast('未选择视频');if(!confirm(`将从文件夹彻底删除 ${ids.length} 条生成视频，并同步从待选分镜空栏移除，无法恢复。确定？`))return;const r=await api('/api/storage/delete_videos',{method:'POST',body:{project:currentProject,image_ids:ids}});selectedStorageImages.clear();updateFromServer(r);toast(`已删除 ${r.deleted||0} 条视频片`)}
async function downloadSelectedStorage(){hideStorageMenu();const ids=Array.from(selectedStorageImages);if(!ids.length)return toast('未选择视频');const resp=await fetch('/api/storage/download_videos',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project:currentProject,image_ids:ids})});if(!resp.ok){toast('下载失败');return}const blob=await resp.blob();const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='storage_videos.zip';document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url)}
async function renameStorageImage(id,name){const old=allGeneratedItems().find(x=>x.img.image_id===id)?.img;const oldName=old?imageName(old):'';const r=await api('/api/storage/rename_video',{method:'POST',body:{project:currentProject,image_id:id,new_name:name}});syncDraftMentions(oldName,r.new_name||name);updateFromServer(r)}
function startStorageBoxSelect(e){if(!storageBatchMode||e.target.closest('.storage-card')||e.button!==0)return;const box=$('selectBox');storageSelecting=true;selectedStorageImages.clear();storageSelectStart={x:e.clientX,y:e.clientY};box.style.display='block';box.style.left=e.clientX+'px';box.style.top=e.clientY+'px';box.style.width='0px';box.style.height='0px';document.onmousemove=storageBoxMove;document.onmouseup=endStorageBoxSelect}
function storageBoxMove(e){if(!storageSelecting)return;const x=Math.min(e.clientX,storageSelectStart.x),y=Math.min(e.clientY,storageSelectStart.y),w=Math.abs(e.clientX-storageSelectStart.x),h=Math.abs(e.clientY-storageSelectStart.y);const box=$('selectBox');Object.assign(box.style,{left:x+'px',top:y+'px',width:w+'px',height:h+'px'});selectedStorageImages.clear();document.querySelectorAll('.storage-card').forEach(card=>{const r=card.getBoundingClientRect();if(!(r.right<x||r.left>x+w||r.bottom<y||r.top>y+h))selectedStorageImages.add(card.dataset.imageId)});renderStorage()}
function endStorageBoxSelect(){storageSelecting=false;$('selectBox').style.display='none';document.onmousemove=null;document.onmouseup=null;renderStorage()}

async function onKeyDown(e){if(e.key==='Escape'&&$('previewModal')?.classList.contains('show')){closePreview();return}if(currentScreen==='storage'&&e.key==='Delete'&&selectedStorageImages.size){await deleteSelectedStorage();return}if(currentScreen==='assets'&&e.key==='Delete'&&selectedAssets.size){if(confirm('删除选中的素材？')){const r=await api('/api/assets/bulk_delete',{method:'POST',body:{project:currentProject,asset_ids:Array.from(selectedAssets)}});selectedAssets.clear();updateFromServer(r)}}}
document.addEventListener('click',()=>hideStorageMenu());
window.addEventListener('hashchange',()=>{const h=location.hash.replace('#','');if(['workspace','assets','storage','config'].includes(h))showScreen(h)});init().catch(e=>{console.error(e);toast(e.message)})

