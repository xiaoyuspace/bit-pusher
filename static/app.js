const $=id=>document.getElementById(id);let model=null,windows=[],selected=new Set(),resolveId=null,profileStateStamp=null;
const labels={blocked:'无法自动发帖',draft:'草稿',queued:'排队中',preparing:'准备中',uploading:'上传中',submitting:'提交中',success:'已发布',failed:'失败',unknown:'结果待核实',cancelled:'已取消'};
const runnable=t=>['draft','failed','cancelled','blocked'].includes(t.state);
const selectable=t=>!model?.closing_tasks?.includes(t.id)&&model?.current!==t.id&&
 (runnable(t)||['success','unknown'].includes(t.state));
function appendTaskActivity(cell, task){
 const activity=model.activity;
 if(activity?.id!==task.id||model.current!==task.id)return;
 const seconds=Math.max(0,Math.floor(Date.now()/1000-activity.since));
 const elapsed=seconds<60?`${seconds} 秒`:`${Math.floor(seconds/60)} 分 ${seconds%60} 秒`;
 cell.append(el('div',`${activity.label} · 已用 ${elapsed}`,'meta'));
}
let deletingTasks=false;
function el(tag,text,cls){const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n}
function toast(text){$('toast').textContent=text;$('toast').style.display='block';clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').style.display='none',6500)}
async function api(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Publisher-Token':model?.token||''},body:JSON.stringify(body||{})});const result=await r.json();if(!r.ok)throw Error(result.error||'操作失败');return result}
async function refresh(){try{const r=await fetch('/api/state');if(!r.ok)throw Error();const previousSync=model?.window_sync_revision;model=await r.json();render();renderWindowJob();const stamp=JSON.stringify(model.tasks.map(t=>[t.id,t.state]));if(profileStateStamp!==null&&(profileStateStamp!==stamp||previousSync!==model.window_sync_revision))loadProfiles();profileStateStamp=stamp}catch(e){$('connectionText').textContent='工作台服务已断开，请重新运行 start.cmd。'}}
async function loadProfiles(){try{$('reloadProfiles').disabled=true;const r=await fetch('/api/profiles');const data=await r.json();if(!r.ok)throw Error(data.error);windows=data.profiles;renderWindows();$('profile').replaceChildren(el('option','请选择窗口'));$('profile').firstChild.value='';for(const w of windows.filter(w=>w.state==='ready')){const o=el('option',`#${w.seq} · ${w.name||'未命名窗口'}`);o.value=w.id;$('profile').append(o)}$('connectionTitle').textContent='比特浏览器已连接';$('connectionText').textContent=`读取到 ${windows.length} 个窗口 · 每条任务独立指定账号、文案和视频`;$('dot').classList.add('on')}catch(e){$('connectionTitle').textContent='比特浏览器未连接';$('connectionText').textContent=e.message;$('dot').classList.remove('on')}finally{$('reloadProfiles').disabled=false}}
function render(){document.title='比特发布台 · '+(model.version||'旧版本，请重启');const tasks=model.tasks;const valid=new Set(tasks.filter(selectable).map(t=>t.id));selected=new Set([...selected].filter(id=>valid.has(id)));$('waiting').textContent=tasks.filter(t=>['draft','queued'].includes(t.state)).length;$('working').textContent=tasks.filter(t=>['preparing','uploading','submitting'].includes(t.state)).length;$('success').textContent=tasks.filter(t=>t.state==='success').length;$('attention').textContent=tasks.filter(t=>['failed','unknown','blocked'].includes(t.state)).length;const runnableCount=tasks.filter(t=>selected.has(t.id)&&runnable(t)).length;$('run').disabled=!runnableCount||deletingTasks;$('run').textContent=runnableCount?`执行选中任务 (${runnableCount})`:'执行选中任务';$('deleteTasks').disabled=!selected.size||deletingTasks;$('deleteTasks').textContent=selected.size?`删除选中 (${selected.size})`:'删除选中';$('stop').disabled=!model.running;$('rows').replaceChildren();const list=tasks.filter(taskMatches);const choices=list.filter(selectable);const checked=choices.filter(t=>selected.has(t.id)).length;$('selectAll').disabled=!choices.length;$('selectAll').checked=!!checked&&checked===choices.length;$('selectAll').indeterminate=checked>0&&checked<choices.length;$('count').textContent=`${list.length} 个任务`;$('empty').hidden=list.length>0;
for(const t of list){const tr=el('tr'),checkCell=el('td'),check=el('input');check.type='checkbox';check.checked=selected.has(t.id);check.disabled=!selectable(t);check.setAttribute('aria-label','选择 '+t.profile_name+' 的任务');check.addEventListener('change',()=>{check.checked?selected.add(t.id):selected.delete(t.id);render()});checkCell.append(check);tr.append(checkCell);const account=el('td');account.append(el('div',t.profile_name,'handle'));tr.append(account);const content=el('td');content.append(el('div',t.text,'task-text'),el('div','▧ '+t.video.split(/[\\/]/).pop(),'meta'));tr.append(content);const state=el('td');state.append(el('span',labels[t.state]||t.state,'badge '+t.state));tr.append(state);const result=el('td');result.append(el('div',t.message,'message'));appendTaskActivity(result,t);if(t.url){const link=el('a','查看已发布帖子 ↗','result-link');link.href=t.url;link.target='_blank';link.rel='noopener noreferrer';result.append(link)}if(t.state==='unknown'){const b=el('button','核实并记录链接','resolve');b.addEventListener('click',()=>{resolveId=t.id;$('postUrl').value='';$('resolveDialog').showModal()});result.append(b)}tr.append(result);$('rows').append(tr)}}
function newTask(){if(!windows.length){toast('请先连接比特浏览器并读取窗口列表');return}$('editor').showModal()}
$('newTask').onclick=newTask;$('emptyNew').onclick=newTask;$('closeEditor').onclick=()=>$('editor').close();$('reloadProfiles').onclick=loadProfiles;$('filter').onchange=()=>{selected.clear();render()};
$('selectAll').onchange=()=>{selected=$('selectAll').checked?new Set(model.tasks.filter(t=>selectable(t)&&taskMatches(t)).map(t=>t.id)):new Set();render()};
$('taskForm').onsubmit=async e=>{e.preventDefault();const w=windows.find(w=>w.id===$('profile').value);if(!w)return toast('请选择有效窗口');$('save').disabled=true;try{await api('/api/task',{profile_id:w.id,profile_name:w.name||String(w.seq),text:$('text').value,video:$('video').value});$('editor').close();$('taskForm').reset();toast('任务已保存为草稿');await refresh()}catch(e){toast(e.message)}finally{$('save').disabled=false}};
$('run').onclick=async()=>{$('run').disabled=true;try{await api('/api/run',{ids:model.tasks.filter(t=>selected.has(t.id)&&runnable(t)).map(t=>t.id)});selected.clear();$('selectAll').checked=false;toast('任务已排队，将按配置直接发布');await refresh()}catch(e){toast(e.message);render()}};
$('deleteTasks').onclick=async()=>{const ids=[...selected];if(!ids.length||deletingTasks||!confirm(`删除选中的 ${ids.length} 条任务记录？已发布的帖子和本地素材会保留。结果待核实的任务删除后仍会防止重复提交。`))return;deletingTasks=true;render();try{const result=await api('/api/tasks/delete',{ids});for(const id of ids)selected.delete(id);toast(`已删除 ${result.deleted} 条任务记录`);await refresh()}catch(e){toast(e.message);await refresh()}finally{deletingTasks=false;render()}};
$('stop').onclick=async()=>{try{await api('/api/stop');toast('已请求停止；已提交的任务将继续核对结果');await refresh()}catch(e){toast(e.message)}};
$('closeResolve').onclick=()=>$('resolveDialog').close();$('resolveForm').onsubmit=async e=>{e.preventDefault();try{await api('/api/resolve',{id:resolveId,url:$('postUrl').value});$('resolveDialog').close();await refresh()}catch(e){toast(e.message)}};
refresh();loadProfiles();setInterval(refresh,3000);

function generateCaptions(template, count){
 const markers=[...template.matchAll(/📷([0-9]{6})📷/gu)];
 if(markers.length!==1)throw Error('原文需要包含一处 📷六位数字📷，例如 📷345987📷。');
 if(/^\s*===\s*$/m.test(template))throw Error('原文不能包含单独一行的 ===，它用于分隔批量文案。');
 if([...template.trim()].length>25000)throw Error('每条文案最多 25,000 字符。');
 const used=new Set([Number(markers[0][1])]);
 const captions=[];
 while(captions.length<count){
  const code=100000+Math.floor(Math.random()*900000);
  if(used.has(code))continue;
  used.add(code);
  captions.push(template.replace(markers[0][0],`📷${code}📷`));
 }
 return captions.join('\n===\n');
}

$('captionOpen').onclick=()=>$('captionDialog').showModal();
$('captionClose').onclick=()=>$('captionDialog').close();
$('captionForm').oninput=()=>{
 $('captionOutput').value='';
 $('captionStatus').textContent='';
 $('captionDownload').disabled=true;
};
$('captionForm').onsubmit=e=>{
 e.preventDefault();
 try{
  const count=$('captionCount').valueAsNumber;
  const result=generateCaptions($('captionTemplate').value,count);
  if(new Blob([result]).size>5000000)throw Error('生成的 TXT 超过批量导入的 5 MB 上限，请减少条数。');
  $('captionOutput').value=result;
  $('captionStatus').textContent=`已生成 ${count} 条文案。`;
  $('captionDownload').disabled=false;
 }catch(e){
  $('captionOutput').value='';
  $('captionStatus').textContent=e.message;
  $('captionDownload').disabled=true;
 }
};
$('captionDownload').onclick=()=>{
 const file=new Blob([$('captionOutput').value],{type:'text/plain;charset=utf-8'});
 const url=URL.createObjectURL(file);
 const link=el('a');
 link.href=url;
 link.download='批量文案.txt';
 link.click();
 setTimeout(()=>URL.revokeObjectURL(url),1000);
};

let batchPlan=null;
function invalidateBatch(){batchPlan=null;$('batchRun').disabled=true;$('batchRows').replaceChildren();$('batchInfo').textContent='';}
function batchInput(){return {folder:$('batchFolder').value,text_file:$('batchTexts').value,profiles:[...$('batchProfiles').querySelectorAll('input:checked')].map(e=>e.value)}}
$('batchNew').onclick=()=>{invalidateBatch();$('batchProfiles').replaceChildren();for(const w of windows.filter(w=>w.state==='ready').sort((a,b)=>a.seq-b.seq)){const label=el('label'),input=el('input');input.type='checkbox';input.value=w.id;input.onchange=invalidateBatch;label.append(input,document.createTextNode(' #'+w.seq+' · '+w.name));$('batchProfiles').append(label)}$('batchSelectAll').checked=false;$('batchDialog').showModal()};
$('batchClose').onclick=()=>$('batchDialog').close();
$('batchSelectAll').onchange=()=>{for(const e of $('batchProfiles').querySelectorAll('input'))e.checked=$('batchSelectAll').checked;invalidateBatch()};
async function pickBatchPath(kind, input){
 if($('batchPreview').disabled)return;
 $('batchPreview').disabled=true;
 $('batchRun').disabled=true;
 $('pickBatchFolder').disabled=true;
 $('pickBatchTexts').disabled=true;
 try{
  const {path}=await api('/api/batch/pick',{kind});
  if(path&&path!==$(input).value){
   $(input).value=path;
   invalidateBatch();
  }
 }catch(e){toast(e.message)}
 finally{
  $('batchPreview').disabled=false;
  $('batchRun').disabled=!batchPlan;
  $('pickBatchFolder').disabled=false;
  $('pickBatchTexts').disabled=false;
 }
}
$('pickBatchFolder').onclick=()=>pickBatchPath('folder','batchFolder');
$('pickBatchTexts').onclick=()=>pickBatchPath('text_file','batchTexts');
$('batchPreview').onclick=async()=>{invalidateBatch();$('batchPreview').disabled=true;try{batchPlan=await api('/api/batch/preview',batchInput());const table=el('table');for(const r of batchPlan.rows){const tr=el('tr');tr.append(el('td',r.order),el('td','#'+r.seq+' · '+r.profile_name),el('td',r.video.split(/[\\/]/).pop()),el('td',r.text.slice(0,130)));table.append(tr)}$('batchRows').append(table);$('batchInfo').textContent=batchPlan.rows.length+' 组配对完成。失败素材复制到：'+batchPlan.failure_folder;$('batchRun').disabled=false}catch(e){toast(e.message)}finally{$('batchPreview').disabled=false}};
$('batchRun').onclick=async()=>{if(!batchPlan)return;$('batchRun').disabled=true;$('batchPreview').disabled=true;try{const result=await api('/api/batch/create',{...batchInput(),signature:batchPlan.signature});$('batchDialog').close();toast('已创建并开始执行 '+result.ids.length+' 条任务');invalidateBatch();await refresh()}catch(e){toast(e.message);invalidateBatch()}finally{$('batchPreview').disabled=false}};


const windowLabels={pending:'待配置',ready:'可使用',delete:'待删除'};
let chosenWindows=new Set(),wasWindowRunning=false,windowPage=1,windowMode='create';
let windowPageSize=Number($('windowPageSize').value);
function taskMatches(t){return ($('filter').value==='all'||t.state===$('filter').value)&&[t.profile_name,t.text,t.video].join(' ').toLowerCase().includes($('taskSearch').value.trim().toLowerCase())}
$('taskSearch').oninput=()=>{selected.clear();$('selectAll').checked=false;render()};
function showPage(name){const isWindows=name==='windows';$('tasksPage').hidden=isWindows;$('windowsPage').hidden=!isWindows;$('taskHeaderActions').hidden=isWindows;$('navTasks').classList.toggle('active',!isWindows);$('manageWindows').classList.toggle('active',isWindows);$('pageTitle').textContent=isWindows?'窗口管理':'发布任务';$('pageDescription').textContent=isWindows?'配置代理与账号，检测窗口状态，集中管理可用窗口。':'管理文案与视频，按队列自动发布到指定窗口。';$('breadcrumb').textContent='工作空间 / '+$('pageTitle').textContent;location.hash=name}
$('navTasks').onclick=()=>showPage('tasks');
$('manageWindows').onclick=()=>{showPage('windows');loadProfiles()};
function filteredWindows(){return [...windows].sort((a,b)=>a.seq-b.seq).filter(w=>($('windowFilter').value==='all'||$('windowFilter').value===w.state)&&`${w.seq} ${w.name} ${w.proxy_ip||''} ${w.remark||''}`.toLowerCase().includes($('windowSearch').value.trim().toLowerCase()))}
function currentWindows(){return filteredWindows().slice((windowPage-1)*windowPageSize,windowPage*windowPageSize)}
function renderWindows(){
 $('windowList').replaceChildren();
 const valid=new Set(windows.map(w=>w.id));chosenWindows=new Set([...chosenWindows].filter(id=>valid.has(id)));
 for(const [id,state] of Object.entries({totalWindows:null,readyWindows:'ready',pendingWindows:'pending',deleteWindows:'delete'}))$(id).textContent=state?windows.filter(w=>w.state===state).length:windows.length;
 const rows=filteredWindows(),pages=Math.max(1,Math.ceil(rows.length/windowPageSize));windowPage=Math.min(windowPage,pages);
 $('windowCount').textContent=`共 ${rows.length} 个窗口`;$('windowPageInfo').textContent=`第 ${windowPage} / ${pages} 页`;$('windowPrev').disabled=windowPage===1;$('windowNext').disabled=windowPage===pages;
 for(const w of currentWindows()){
  const tr=el('tr'),cell=el('td'),c=el('input');c.type='checkbox';c.checked=chosenWindows.has(w.id);c.setAttribute('aria-label','选择窗口 '+w.name);
  c.onchange=()=>{c.checked?chosenWindows.add(w.id):chosenWindows.delete(w.id);renderWindows()};cell.append(c);tr.append(cell);
  const title=el('td');title.append(el('div',w.name||'未命名窗口','handle'),el('div','#'+w.seq,'meta'));tr.append(title);
  tr.append(el('td',w.proxy_ip||'—','proxy-cell'),el('td',w.proxy_user||'—'),
   el('td',w.proxy_password||'—'),el('td',w.remark||'—','remark-cell'));
  const status=el('td');status.append(el('span',windowLabels[w.state],'badge '+w.state));tr.append(status);
  tr.append(el('td',w.checked?new Date(w.checked*1000).toLocaleString('zh-CN',{hour12:false}):'尚未检测','date-cell'),el('td',w.reason||'—','message'));
  const actions=el('td');for(const [text,mode] of [['配置','configure'],['编辑','edit'],['检测','detect']]){const button=el('button',text,'text-button row-action');button.disabled=!!(model?.running||model?.window_job?.running);button.onclick=()=>{chosenWindows=new Set([w.id]);renderWindows();mode==='detect'?windowAction(mode):openWindowForm(mode)};actions.append(button)}
  const manualCheck=el('button','手动检测','text-button row-action');
  manualCheck.onclick=()=>openManualCheck(w);
  actions.append(manualCheck);
  tr.append(actions);$('windowList').append(tr);
 }
 if(!rows.length){const tr=el('tr'),td=el('td','没有匹配的窗口，请调整筛选条件。','empty-cell');td.colSpan=10;tr.append(td);$('windowList').append(tr)}
 const visible=currentWindows();$('windowsAll').checked=visible.length>0&&visible.every(w=>chosenWindows.has(w.id));$('windowsAll').indeterminate=visible.some(w=>chosenWindows.has(w.id))&&!$('windowsAll').checked;
 renderWindowJob();
}
let shownDeletedProxies;
function renderWindowJob(){
 const job=model?.window_job;if(!job)return;
 const busy=job.running||model.running,targets=windows.filter(w=>chosenWindows.has(w.id));
 $('windowSelection').textContent=`已选 ${targets.length} 项`;
 $('windowCreate').disabled=busy;$('windowSubmit').disabled=busy;
 for(const id of ['windowConfigure','windowDetect'])$(id).disabled=busy||!targets.length;
 $('windowDelete').disabled=busy||!targets.length||targets.some(w=>w.state!=='delete');
 document.querySelectorAll('.row-action').forEach(b=>b.disabled=busy);
 let progress = '暂无进行中的窗口操作';
 if (model.running) progress = '发布队列运行中，完成后可管理窗口。';
 if (job.running) {
  progress = job.action === 'detect'
   ? `正在检测窗口 · 已完成 ${job.results.length}/${job.total} · 最多 ${job.workers} 个并行`
   : '正在处理窗口…';
 } else if (job.action === 'detect' && job.total) {
  progress = `检测已结束 · 已完成 ${job.results.length}/${job.total}`;
 }
 $('windowProgress').textContent = progress;
 $('windowSyncStatus').textContent = model.window_sync || '';
 $('windowResults').replaceChildren(...job.results.map(r=>el('p',(windows.find(w=>w.id===r.id)?.name||r.name||r.id||'窗口')+'：'+r.message)));
 if(!job.running&&job.action==='delete'&&job.deleted_proxies?.length&&shownDeletedProxies!==job.run_id){
  shownDeletedProxies=job.run_id;
  $('deletedProxyLines').value=job.deleted_proxies.join('\n');
  $('deletedProxyStatus').textContent='';
  $('deletedProxyDialog').showModal();
 }
 if(wasWindowRunning&&!job.running){wasWindowRunning=false;loadProfiles()}else wasWindowRunning=job.running;
}
$('copyDeletedProxies').onclick=async()=>{
 const lines=$('deletedProxyLines');
 try{
  await navigator.clipboard.writeText(lines.value);
  $('deletedProxyStatus').textContent='已复制全部代理。';
 }catch{
  lines.focus();
  lines.select();
  $('deletedProxyStatus').textContent='已选中全部代理，请按 Ctrl+C 复制。';
 }
};
function clearWindowForm(){$('windowTokens').value='';$('windowProxies').value=''}
function closeWindowForm(){$('windowsDialog').close();clearWindowForm()}
function openWindowForm(mode){
 if(mode!=='create'&&!chosenWindows.size)return toast('请先选择窗口');
 windowMode=mode;$('windowForm').reset();
 $('windowFormTitle').textContent={create:'新建窗口',configure:'配置窗口',edit:'修改窗口'}[mode];
 $('namesField').hidden=mode!=='create';$('tokensField').hidden=mode==='edit';$('remarkField').hidden=mode==='configure';
 const targets=windows.filter(w=>chosenWindows.has(w.id)).sort((a,b)=>a.seq-b.seq);
 $('windowTargets').textContent=mode==='create'?'每行填写一个窗口名称，可同时创建多个窗口。平台默认为 x.com。':`已选 ${targets.length} 个窗口：`+targets.map(w=>`#${w.seq} ${w.name}`).join('、');
 $('windowSubmit').textContent=mode==='create'?'创建窗口':'保存配置';$('windowsDialog').showModal();
}
$('windowsClose').onclick=closeWindowForm;$('windowCancel').onclick=closeWindowForm;$('windowsDialog').addEventListener('close',clearWindowForm);
$('windowFilter').onchange=$('windowSearch').oninput=()=>{windowPage=1;chosenWindows.clear();renderWindows()};
$('windowsAll').onchange=()=>{for(const w of currentWindows())$('windowsAll').checked?chosenWindows.add(w.id):chosenWindows.delete(w.id);renderWindows()};
$('clearWindows').onclick=()=>{chosenWindows.clear();renderWindows()};
$('windowPageSize').onchange=e=>{
 windowPageSize=Number(e.target.value);
 windowPage=1;
 renderWindows();
};
$('windowPrev').onclick=()=>{windowPage--;renderWindows()};$('windowNext').onclick=()=>{windowPage++;renderWindows()};
async function windowAction(action){
 const targets=windows.filter(w=>chosenWindows.has(w.id)).sort((a,b)=>a.seq-b.seq);
 if(action==='delete'&&!confirm('确定删除这些比特浏览器窗口？窗口数据将被删除：\n'+targets.map(w=>'#'+w.seq+' '+w.name).join('\n')))return;
 $('windowSubmit').disabled=true;
 try{
  await api('/api/windows',{action,ids:action==='create'?[]:targets.map(w=>w.id),names:action==='create'?$('windowNames').value:'',proxies:['create','configure','edit'].includes(action)?$('windowProxies').value:'',tokens:['create','configure'].includes(action)?$('windowTokens').value:'',remark:$('windowRemark').value,confirm:action==='delete'});
  closeWindowForm();wasWindowRunning=true;toast('窗口操作已开始，可在操作记录中查看结果');await refresh();await loadProfiles();
 }catch(e){toast(e.message)}finally{renderWindowJob()}
}
$('windowCreate').onclick=()=>openWindowForm('create');$('windowConfigure').onclick=()=>openWindowForm('configure');$('windowDetect').onclick=()=>windowAction('detect');$('windowDelete').onclick=()=>windowAction('delete');
$('windowForm').onsubmit=e=>{e.preventDefault();windowAction(windowMode)};
let manualCheckTarget=null;
let manualCheckBusy=false;
function openManualCheck(window){
 if(manualCheckBusy)return;
 manualCheckTarget=window;
 $('manualCheckState').value=window.state;
 $('manualCheckTarget').textContent=`#${window.seq} · ${window.name||'未命名窗口'}`;
 $('manualCheckStatus').textContent='';
 $('manualCheckDialog').showModal();
}
async function submitManualCheck(action){
 if(!manualCheckTarget||manualCheckBusy)return;
 const target=manualCheckTarget;
 const state=$('manualCheckState').value;
 manualCheckBusy=true;
 $('manualCheckOpen').disabled=true;
 $('manualCheckState').disabled=true;
 $('manualCheckConfirm').disabled=true;
 $('manualCheckStatus').textContent=action==='open'?'正在打开窗口…':'正在保存…';
 try{
  await api('/api/windows/manual-check',{id:target.id,action,state,confirmed:action==='confirm'});
  if(action==='open'){
   $('manualCheckStatus').textContent='窗口已打开。检查后请返回这里确认。';
  }else{
   $('manualCheckDialog').close();
   toast(`窗口已手动标为「${windowLabels[state]}」`);
   await loadProfiles();
  }
 }catch(e){$('manualCheckStatus').textContent=e.message}
 finally{
  manualCheckBusy=false;
  $('manualCheckOpen').disabled=false;
  $('manualCheckState').disabled=false;
  $('manualCheckConfirm').disabled=false;
 }
}
$('manualCheckOpen').onclick=()=>submitManualCheck('open');
$('manualCheckConfirm').onclick=()=>submitManualCheck('confirm');
showPage(location.hash==='#windows'?'windows':'tasks');
