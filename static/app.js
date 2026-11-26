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
async function loadProfiles(){try{$('reloadProfiles').disabled=true;const r=await fetch('/api/profiles');const data=await r.json();if(!r.ok)throw Error(data.error);windows=data.profiles;renderWindows();$('profile').replaceChildren(el('option','请选择窗口'));$('profile').firstChild.value='';for(const w of windows.filter(w=>w.state==='ready')){const o=el('option',`#${w.seq} · ${w.name||'未命名窗口'}`);o.value=w.id;$('profile').append(o)}$('connectionTitle').textContent='比特浏览器已连接';$('connectionText').textContent=`读取到 ${windows.length} 个窗口`;$('dot').classList.add('on')}catch(e){$('connectionTitle').textContent='比特浏览器未连接';$('connectionText').textContent=e.message;$('dot').classList.remove('on')}finally{$('reloadProfiles').disabled=false}}
function render(){document.title='比特发布台 · '+(model.version||'旧版本，请重启');const tasks=model.tasks;const valid=new Set(tasks.filter(selectable).map(t=>t.id));selected=new Set([...selected].filter(id=>valid.has(id)));$('waiting').textContent=tasks.filter(t=>['draft','queued'].includes(t.state)).length;$('working').textContent=tasks.filter(t=>['preparing','uploading','submitting'].includes(t.state)).length;$('success').textContent=tasks.filter(t=>t.state==='success').length;$('attention').textContent=tasks.filter(t=>['failed','unknown','blocked'].includes(t.state)).length;const runnableCount=tasks.filter(t=>selected.has(t.id)&&runnable(t)).length;$('run').disabled=!runnableCount||deletingTasks;$('run').textContent=runnableCount?`执行选中任务 (${runnableCount})`:'执行选中任务';$('deleteTasks').disabled=!selected.size||deletingTasks;$('deleteTasks').textContent=selected.size?`删除选中 (${selected.size})`:'删除选中';$('stop').disabled=!model.running;$('rows').replaceChildren();const list=tasks.filter(taskMatches);const choices=list.filter(selectable);const checked=choices.filter(t=>selected.has(t.id)).length;$('selectAll').disabled=!choices.length;$('selectAll').checked=!!checked&&checked===choices.length;$('selectAll').indeterminate=checked>0&&checked<choices.length;$('count').textContent=`${list.length} 个任务`;$('empty').hidden=list.length>0;
const filtered=$('taskPlatform').value!=='all'||$('filter').value!=='all'||Boolean($('taskSearch').value.trim());
$('emptyTitle').textContent=filtered?'没有匹配的任务':'暂无发布任务';
$('emptyHint').textContent=filtered?'调整筛选条件或搜索关键词。':'在上方选择平台，创建第一条发布任务。';
$('clearTaskFilters').hidden=!filtered;
for(const t of list){const tr=el('tr'),checkCell=el('td'),check=el('input');check.type='checkbox';check.checked=selected.has(t.id);check.disabled=!selectable(t);check.setAttribute('aria-label','选择 '+t.profile_name+' 的任务');check.addEventListener('change',()=>{check.checked?selected.add(t.id):selected.delete(t.id);render()});checkCell.append(check);tr.append(checkCell);const account=el('td');account.append(el('div',t.profile_name,'handle'),el('small',t.platform==='bilibili'?'哔哩哔哩':'X / Twitter','platform-label'));tr.append(account);const content=el('td');content.append(el('div',t.text,'task-text'),el('div','▧ '+t.video.split(/[\\/]/).pop(),'meta'));tr.append(content);const state=el('td');state.append(el('span',labels[t.state]||t.state,'badge '+t.state));tr.append(state);const result=el('td');result.append(el('div',t.message,'message'));appendTaskActivity(result,t);if(t.url){const link=el('a','查看已发布帖子 ↗','result-link');link.href=t.url;link.target='_blank';link.rel='noopener noreferrer';result.append(link)}if(t.state==='unknown'){const b=el('button','核实并记录链接','resolve');b.addEventListener('click',()=>{resolveId=t.id;$('postUrl').placeholder=t.platform==='bilibili'?'https://www.bilibili.com/video/BV…':'https://x.com/username/status/…';$('postUrl').value='';$('resolveDialog').showModal()});result.append(b)}if(t.can_reassign&&model.current!==t.id)result.append(reassignButton(t));tr.append(result);$('rows').append(tr)}}
function newTask(){if(!windows.length){toast('请先连接比特浏览器并读取窗口列表');return}$('editor').showModal()}
$('newTask').onclick=newTask;$('closeEditor').onclick=()=>$('editor').close();$('reloadProfiles').onclick=loadProfiles;$('filter').onchange=()=>{selected.clear();render()};
$('selectAll').onchange=()=>{selected=$('selectAll').checked?new Set(model.tasks.filter(t=>selectable(t)&&taskMatches(t)).map(t=>t.id)):new Set();render()};
$('taskForm').onsubmit=async e=>{e.preventDefault();const w=windows.find(w=>w.id===$('profile').value);if(!w)return toast('请选择有效窗口');$('save').disabled=true;try{await api('/api/task',{profile_id:w.id,profile_name:w.name||String(w.seq),text:$('text').value,video:$('video').value});$('editor').close();$('taskForm').reset();toast('任务已保存为草稿');await refresh()}catch(e){toast(e.message)}finally{$('save').disabled=false}};
$('run').onclick=async()=>{$('run').disabled=true;try{await api('/api/run',{ids:model.tasks.filter(t=>selected.has(t.id)&&runnable(t)).map(t=>t.id)});selected.clear();$('selectAll').checked=false;toast('任务已排队，将按配置直接发布');await refresh()}catch(e){toast(e.message);render()}};
$('deleteTasks').onclick=async()=>{const ids=[...selected];if(!ids.length||deletingTasks||!confirm(`删除选中的 ${ids.length} 条任务记录？已发布的帖子和本地素材会保留。结果待核实的任务删除后仍会防止重复提交。`))return;deletingTasks=true;render();try{const result=await api('/api/tasks/delete',{ids});for(const id of ids)selected.delete(id);toast(`已删除 ${result.deleted} 条任务记录`);await refresh()}catch(e){toast(e.message);await refresh()}finally{deletingTasks=false;render()}};
$('stop').onclick=async()=>{try{await api('/api/stop');toast('已请求停止；已提交的任务将继续核对结果');await refresh()}catch(e){toast(e.message)}};
$('closeResolve').onclick=()=>$('resolveDialog').close();$('resolveForm').onsubmit=async e=>{e.preventDefault();try{await api('/api/resolve',{id:resolveId,url:$('postUrl').value});$('resolveDialog').close();await refresh()}catch(e){toast(e.message)}};
let reassignId;
function reassignButton(task){
 const button=el('button','重新分配','resolve');
 button.onclick=()=>{
  const target=$('reassignProfile');
  target.replaceChildren(new Option('请选择窗口',''));
  for(const window of windows.filter(window=>window.state==='ready')){
   target.append(new Option(`#${window.seq} · ${window.name||'未命名窗口'}`,window.id));
  }
  $('reassignSource').textContent=`原窗口 ${task.profile_name} · ${task.video.split(/[\\/]/).pop()}`;
  reassignId=task.id;
  $('reassignDialog').showModal();
 };
 return button;
}
$('closeReassign').onclick=()=>$('reassignDialog').close();
$('reassignForm').onsubmit=async event=>{
 event.preventDefault();
 const button=$('submitReassign');
 button.disabled=true;
 try{
  await api('/api/tasks/reassign',{id:reassignId,profile_id:$('reassignProfile').value});
  $('reassignDialog').close();
  toast('任务已重新分配并排队。');
  await refresh();
 }catch(error){toast(error.message)}
 finally{button.disabled=false}
};
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

function generateBookCaptions(rawLink,count){
 const link=rawLink.trim();
 let url;
 try{url=new URL(link)}catch{throw Error('请输入有效的合集链接。')}
 if(!/^https?:\/\/[^\s]+$/i.test(link)||!url.hostname||url.username||url.password){
  throw Error('请输入有效的 HTTP 或 HTTPS 合集链接。');
 }
 if(!Number.isInteger(count)||count<1||count>200)throw Error('请输入 1–200 条。');
 const titles=[
  [['金主','合约对象','联姻对象','前任','室友'],['不肯签字','把续约写成了情书','今天也在假装不熟','把钥匙留给了我','在散场后等我']],
  [['队长','替补','前队友','解说','榜一'],['请关掉你的麦','把告白发进了战队群','在决赛之后敲门','把双排留给了我','偷偷改了情侣名']],
  [['世子','少将军','小侯爷','剑客','掌门'],['不肯退婚','把婚书藏进了剑匣','今夜没有回府','在雪夜等一封信','把玉佩留在了渡口']],
  [['邻居','学长','竹马','店长','同桌'],['把我写进了情书','在末班车上等我','把秘密留在旧书里','在雨停前说喜欢','总来借同一本书']],
  [['演员','摄影师','主唱','编剧','电台主播'],['把告白剪进了预告','在谢幕时叫我的名字','给我留了一盏灯','只为我改了结局','在凌晨点了我的歌']]
 ];
 const surnames=['迟','白','折','闻','江','温','沈','顾','许','季','宋','林'];
 const names=['雾','栖','灯','雪迟','停舟','照野','南枝','晚棠','小满','听雨','青禾','知遥'];
 const choose=list=>list[Math.floor(Math.random()*list.length)];
 const seen=new Set();
 const captions=[];
 while(captions.length<count){
  const books=titles.map(([roles,hooks])=>`《${choose(roles)}${choose(hooks)}》作者：${choose(surnames)}${choose(names)}`).join('\n');
  if(seen.has(books))continue;
  seen.add(books);
  captions.push(`po文+po18+废文+AO3】合集万本连载🔥\n🔗：${link}\n${books} #po文合集 #po文`);
 }
 return captions.join('\n===\n');
}

$('captionMode').onchange=()=>{
 const books=$('captionMode').value==='books';
 $('captionTemplateField').hidden=books;
 $('captionTemplate').disabled=books;
 $('captionLinkField').hidden=!books;
 $('captionLink').disabled=!books;
 $('captionLink').required=books;
};
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
  const result=$('captionMode').value==='books'
   ?generateBookCaptions($('captionLink').value,count)
   :generateCaptions($('captionTemplate').value,count);
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
let batchGenerated='';
function setBatchSave(enabled){
 for(const id of ['batchRun','batchDraft'])$(id).disabled=!enabled;
}
function invalidateBatch(){batchPlan=null;setBatchSave(false);$('batchRows').replaceChildren();$('batchInfo').textContent='';}
function batchInput(){return {folder:$('batchFolder').value,text_file:$('batchTexts').value,text_only:$('batchMode').value==='text',generated:$('batchMode').value==='text'&&$('batchPastedText').value.trim()||batchGenerated,profiles:[...$('batchProfiles').querySelectorAll('input:checked')].map(e=>e.value)}}
$('batchMode').onchange=()=>{
 const textOnly=$('batchMode').value==='text';
 $('batchVideoPicker').hidden=textOnly;
 $('batchTextOnly').hidden=!textOnly;
 invalidateBatch();
};
$('batchPastedText').oninput=()=>{
 batchGenerated='';
 $('batchCaptionStatus').textContent='';
 invalidateBatch();
};
$('batchNew').onclick=()=>{invalidateBatch();$('batchProfiles').replaceChildren();for(const w of windows.filter(w=>w.state==='ready').sort((a,b)=>a.seq-b.seq)){const label=el('label'),input=el('input');input.type='checkbox';input.value=w.id;input.onchange=invalidateBatch;label.append(input,document.createTextNode(' #'+w.seq+' · '+w.name));$('batchProfiles').append(label)}$('batchSelectAll').checked=false;$('batchDialog').showModal()};
$('batchClose').onclick=()=>$('batchDialog').close();
$('batchSelectAll').onchange=()=>{for(const e of $('batchProfiles').querySelectorAll('input'))e.checked=$('batchSelectAll').checked;invalidateBatch()};
async function pickBatchPath(kind, input){
 if($('batchPreview').disabled)return;
 $('batchPreview').disabled=true;
 setBatchSave(false);
 $('pickBatchFolder').disabled=true;
 $('pickBatchTexts').disabled=true;
 try{
  const {path}=await api('/api/batch/pick',{kind});
  if(path&&(path!==$(input).value||(kind==='text_file'&&(batchGenerated||$('batchPastedText').value)))){
   if(kind==='text_file'){batchGenerated='';$('batchPastedText').value='';$('batchCaptionStatus').textContent='';}
   $(input).value=path;
   invalidateBatch();
  }
 }catch(e){$('batchInfo').textContent=e.message}
 finally{
  $('batchPreview').disabled=false;
  setBatchSave(!!batchPlan);
  $('pickBatchFolder').disabled=false;
  $('pickBatchTexts').disabled=false;
 }
}
$('pickBatchFolder').onclick=()=>pickBatchPath('folder','batchFolder');
$('pickBatchTexts').onclick=()=>pickBatchPath('text_file','batchTexts');
$('batchGenerate').onclick=()=>{
 const count=batchInput().profiles.length;
 try{
  if(count<1||count>200)throw Error('请选择 1–200 个窗口后生成。');
  const generated=generateCaptions($('batchTemplate').value,count);
  if(new Blob([generated]).size>5000000)throw Error('生成文案超过 5 MB。');
  batchGenerated=generated;
  $('batchPastedText').value='';
  $('batchTexts').value='';
  invalidateBatch();
  $('batchCaptionStatus').textContent=`已生成 ${count} 条文案，将用于本批预览。`;
 }catch(error){$('batchCaptionStatus').textContent=error.message}
};
$('batchTemplate').oninput=()=>{
 if(batchGenerated){batchGenerated='';invalidateBatch()}
 $('batchCaptionStatus').textContent='';
};
function renderBatchRows(){
 const table=el('table');
 const header=el('tr');
 for(const text of ['视频','窗口','文案','操作'])header.append(el('th',text));
 table.append(header);
 for(const row of batchPlan.rows){
  const tr=el('tr');
  tr.dataset.order=row.order;
  const windowCell=el('td'), textCell=el('td'), actions=el('td');
  const select=el('select');
  select.setAttribute('aria-label',`第 ${row.order} 行窗口`);
  for(const w of windows.filter(w=>w.state==='ready').sort((a,b)=>a.seq-b.seq)){
   select.append(new Option(`#${w.seq} · ${w.name}`,w.id));
  }
  select.value=row.profile_id;
  const caption=el('textarea');
  caption.value=row.text;
  caption.rows=3;
  caption.setAttribute('aria-label',`第 ${row.order} 行文案`);
  const remove=el('button','移除');
  remove.onclick=()=>{
   tr.remove();
   const count=table.querySelectorAll('[data-order]').length;
   $('batchInfo').textContent=`剩余 ${count} 条任务`;
   setBatchSave(count>0);
  };
  windowCell.append(select);textCell.append(caption);actions.append(remove);
  tr.append(el('td',row.video ? row.video.split(/[\\/]/).pop() : '仅文案'),windowCell,textCell,actions);
  table.append(tr);
 }
 $('batchRows').replaceChildren(table);
 $('batchInfo').textContent=`${batchPlan.rows.length} 条任务，可在表中调整配对。`;
}
$('batchPreview').onclick=async()=>{
 invalidateBatch();
 const input=batchInput();
 const status=$('batchInfo');
 if(input.text_only&&model?.text_only_batch!==true){
  status.textContent='当前服务不支持仅文案批量发布，请重新运行 start.cmd 后刷新页面。';
  return;
 }
 if(!input.profiles.length){
  status.textContent='请先勾选至少一个可使用窗口。';
  return;
 }
 if(!input.generated&&!input.text_file){
  status.textContent='请选择文案 TXT 文件，或在仅文案模式直接粘贴文案。';
  return;
 }
 if(!input.text_only&&!input.folder){
  status.textContent='请选择视频文件夹。';
  return;
 }
 status.textContent='正在检查数量并生成配对…';
 $('batchPreview').disabled=true;
 $('batchGenerate').disabled=true;
 try{
  const prepared=await api('/api/batch/preview',input);
  if(JSON.stringify(input)!==JSON.stringify(batchInput()))return;
  batchPlan={...prepared,input};
  renderBatchRows();
  setBatchSave(true);
 }catch(error){
  invalidateBatch();
  status.textContent=error.message;
 }
 finally{$('batchPreview').disabled=false;$('batchGenerate').disabled=false}
};
async function saveBatch(run){
 if(!batchPlan||$('batchPreview').disabled)return;
 if(model?.editable_batch!==true||(batchPlan.input.text_only&&model?.text_only_batch!==true)){
  $('batchInfo').textContent='请关闭旧版服务，重新运行 start.cmd 后刷新页面。';
  return;
 }
 const edits=[...$('batchRows').querySelectorAll('[data-order]')].map(row=>({
  order:Number(row.dataset.order),profile_id:row.querySelector('select').value,
  text:row.querySelector('textarea').value
 }));
 setBatchSave(false);
 $('batchPreview').disabled=true;
 try{
  const result=await api('/api/batch/create',{...batchPlan.input,signature:batchPlan.signature,edits,run});
  $('batchDialog').close();
  toast(result.started?`已创建并开始执行 ${result.ids.length} 条任务`:
   `已保存 ${result.ids.length} 条草稿`+(result.warning?`。未启动：${result.warning}`:''));
  invalidateBatch();
  await refresh();
 }catch(error){$('batchInfo').textContent=error.message}
 finally{
  $('batchPreview').disabled=false;
  setBatchSave(!!batchPlan&&!!$('batchRows').querySelector('[data-order]'));
 }
}
$('batchDraft').onclick=()=>saveBatch(false);
$('batchRun').onclick=()=>saveBatch(true);



const windowLabels={pending:'待配置',ready:'可使用',delete:'待删除'};
let chosenWindows=new Set(),wasWindowRunning=false,windowPage=1,windowMode='create';
let windowPageSize=Number($('windowPageSize').value);
function taskMatches(t){return ($('taskPlatform').value==='all'||(t.platform||'x')===$('taskPlatform').value)&&($('filter').value==='all'||t.state===$('filter').value)&&[t.profile_name,t.text,t.video].join(' ').toLowerCase().includes($('taskSearch').value.trim().toLowerCase())}
$('clearTaskFilters').onclick=()=>{
 $('taskSearch').value='';$('filter').value='all';$('taskPlatform').value='all';selected.clear();render();
};
$('taskPlatform').onchange=()=>{selected.clear();render()};
$('taskSearch').oninput=()=>{selected.clear();$('selectAll').checked=false;render()};
function showPage(name){const isWindows=name==='windows';$('tasksPage').hidden=isWindows;$('windowsPage').hidden=!isWindows;$('navTasks').classList.toggle('active',!isWindows);$('manageWindows').classList.toggle('active',isWindows);$('pageTitle').textContent=isWindows?'窗口管理':'发布工作台';$('pageDescription').textContent=isWindows?'配置代理与账号，检测窗口状态，集中管理可用窗口。':'选择发布平台，在队列中查看发布进度。';location.hash=name}
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
  tr.append(el('td',w.username||'—'),el('td',w.account_password||'—'),el('td',w.twofa||'—'));
  const actions=el('td');actions.dataset.profileId=w.id;for(const [text,mode] of [['配置','configure'],['编辑','edit'],['检测','detect']]){const button=el('button',text,'text-button row-action');button.onclick=()=>{chosenWindows=new Set([w.id]);renderWindows();mode==='detect'?windowAction(mode):openWindowForm(mode)};actions.append(button)}
  const manualCheck=el('button','手动检测','text-button row-action');
  manualCheck.onclick=()=>openManualCheck([w]);
  actions.append(manualCheck);
  tr.append(actions);$('windowList').append(tr);
 }
 if(!rows.length){const tr=el('tr'),td=el('td','没有匹配的窗口，请调整筛选条件。','empty-cell');td.colSpan=13;tr.append(td);$('windowList').append(tr)}
 const visible=currentWindows();$('windowsAll').checked=visible.length>0&&visible.every(w=>chosenWindows.has(w.id));$('windowsAll').indeterminate=visible.some(w=>chosenWindows.has(w.id))&&!$('windowsAll').checked;
 renderWindowJob();
}
let shownDeletedProxies;
function renderWindowJob(){
 const job=model?.window_job;if(!job)return;
 const supportsParallelWindows = Array.isArray(model.reserved_profiles);
 const busy=job.running||(model.running&&!supportsParallelWindows),targets=windows.filter(w=>chosenWindows.has(w.id));
 const reserved = new Set(model.reserved_profiles || []);
 const selectedBusy = targets.some(w => reserved.has(w.id));
 $('windowSelection').textContent=`已选 ${targets.length} 项`;
 $('windowCreate').disabled=busy;$('windowSubmit').disabled=busy||(windowMode!=='create'&&selectedBusy);
 for(const id of ['windowConfigure','windowDetect','windowManualCheck','windowBrowse'])$(id).disabled=busy||selectedBusy||!targets.length;
 $('windowDelete').disabled=busy||selectedBusy||!targets.length||targets.some(w=>w.state!=='delete');
 document.querySelectorAll('.row-action').forEach(button => {
  const reservedWindow = reserved.has(button.closest('[data-profile-id]')?.dataset.profileId);
  button.disabled = busy || reservedWindow;
  button.title = reservedWindow ? '该窗口已被发布队列占用' : '';
 });
 let progress = '暂无进行中的窗口操作';
 if (model.running) progress = supportsParallelWindows
  ? '发布队列运行中，可操作未被占用的窗口。'
  : '发布队列运行中，重启新版发布台后可并行操作其他窗口。';
 if (selectedBusy) progress = '所选窗口包含发布队列占用的窗口，请取消勾选后再操作。';
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
 const browsing=job.running&&job.action==='browse';
 $('stopWindowBrowse').hidden=!browsing;
 if(browsing){
  const current=windows.find(window=>window.id===job.current);
  $('windowProgress').textContent=`正在养号 · 已处理 ${job.results.length}/${job.total}`+
   (current?` · #${current.seq} ${current.name}`:'');
 }else if(job.action==='browse'){
  $('windowProgress').textContent=`养号已结束 · 共 ${job.results.length} 个窗口`;
  $('stopWindowBrowse').disabled=false;
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
$('openLoginExtractor').onclick=()=>$('loginExtractor').showModal();
$('loginCardFile').onchange=async()=>{
 const file=$('loginCardFile').files[0];
 if(file) $('loginCardText').value=await file.text();
};
$('extractLogins').onclick=async()=>{
 const button=$('extractLogins');
 button.disabled=true;
 try{
  const {logins}=await api('/api/windows/extract-logins',{text:$('loginCardText').value});
  if(!$('loginExtractor').open)return;
  $('windowTokens').value=logins;
  $('loginExtractor').close();
 }catch(e){$('loginExtractStatus').textContent=e.message}
 finally{button.disabled=false}
};
$('loginExtractor').addEventListener('close',()=>{
 $('loginCardText').value='';
 $('loginCardFile').value='';
 $('loginExtractStatus').textContent='';
});
function closeWindowForm(){$('windowsDialog').close();clearWindowForm()}
function openWindowForm(mode){
 if(mode!=='create'&&!chosenWindows.size)return toast('请先选择窗口');
 windowMode=mode;$('windowForm').reset();
 $('windowFormTitle').textContent={create:'新建窗口',configure:'配置窗口',edit:'修改窗口'}[mode];
 $('namesField').hidden=mode!=='create';$('tokensField').hidden=mode==='edit';$('remarkField').hidden=mode==='configure';
 const targets=windows.filter(w=>chosenWindows.has(w.id)).sort((a,b)=>a.seq-b.seq);
 $('windowTargets').textContent=mode==='create'?'每行填写一个窗口名称，可同时创建多个窗口。平台默认为 x.com。':`已选 ${targets.length} 个窗口：`+targets.map(w=>`#${w.seq} ${w.name}`).join('、');
 if(mode==='configure'){
  const note=el('p','同时填写代理和登录信息会按换号处理，先清空对应窗口的本地和云端浏览器数据，再保存新配置。');
  $('windowTargets').append(note);
 }
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
let manualCheckBusy=false;
let manualCheckTargets=[];
const manualCheckDialog=$('manualCheckDialog');

function openManualCheck(targets){
 if(manualCheckBusy||!targets.length)return;
 manualCheckTargets=targets;
 const single=targets.length===1;
 $('manualCheckTargets').textContent=`已选 ${targets.length} 个窗口：`+
  targets.map(window=>`#${window.seq} ${window.name||'未命名窗口'}`).join('、');
 $('manualCheckOpen').hidden=!single;
 $('manualCheckState').value=single?targets[0].state:'';
 $('manualCheckStatus').textContent='';
 manualCheckDialog.showModal();
}

async function submitManualCheck(action,id,name){
 if(manualCheckBusy)return;
 const state=$('manualCheckState').value;
 const status=$('manualCheckStatus');
 if(action==='confirm'&&!state){
  status.textContent='请选择要应用到所选窗口的状态。';
  return;
 }
 const results=manualCheckTargets.map(window=>({id:window.id,state}));
 if(action==='confirm'&&results.length>1&&model?.batch_manual_check!==true){
  status.textContent='当前服务版本不支持批量手动检测。请关闭旧版发布台服务，重新运行 start.cmd，再刷新页面。';
  return;
 }
 const controls=manualCheckDialog.querySelectorAll('button,select');
 manualCheckBusy=true;
 controls.forEach(control=>control.disabled=true);
 status.textContent=action==='open'?`正在打开 ${name}…`:'正在保存…';
 try{
  const payload=action==='open'?{id,action}:results.length===1
   ?{...results[0],action,confirmed:true}
   :{action,results,confirmed:true};
  await api('/api/windows/manual-check',payload);
  if(action==='open'){
   status.textContent=`${name} 已打开。检查后请返回这里选择结果。`;
  }else{
   manualCheckDialog.close();
   toast(`已保存 ${results.length} 个窗口的手动检测结果`);
   await loadProfiles();
  }
 }catch(e){status.textContent=e.message}
 finally{
  manualCheckBusy=false;
  controls.forEach(control=>control.disabled=false);
 }
}
manualCheckDialog.addEventListener('cancel',event=>{if(manualCheckBusy)event.preventDefault()});
$('windowManualCheck').onclick=()=>openManualCheck(
 windows.filter(window=>chosenWindows.has(window.id)).sort((a,b)=>a.seq-b.seq)
);
$('manualCheckOpen').onclick=()=>submitManualCheck('open',manualCheckTargets[0].id,manualCheckTargets[0].name);
$('manualCheckConfirm').onclick=()=>submitManualCheck('confirm');
const browseDialog=$('windowBrowseDialog');
let browseTargets=[];
$('windowBrowse').onclick=()=>{
 browseTargets=windows.filter(window=>chosenWindows.has(window.id)).map(window=>window.id);
 if(!browseTargets.length)return;
 $('windowBrowseTargets').textContent=`已选 ${browseTargets.length} 个窗口`;
 $('windowBrowseStatus').textContent='';
 browseDialog.showModal();
};
$('cancelWindowBrowse').onclick=()=>browseDialog.close();
$('windowBrowseForm').onsubmit=async event=>{
 event.preventDefault();
 $('startWindowBrowse').disabled=true;
 try{
  await api('/api/windows',{action:'browse',ids:browseTargets,minutes:$('windowBrowseMinutes').valueAsNumber});
  browseDialog.close();
  await refresh();
 }catch(error){$('windowBrowseStatus').textContent=error.message}
 finally{$('startWindowBrowse').disabled=false}
};
$('stopWindowBrowse').onclick=async()=>{
 $('stopWindowBrowse').disabled=true;
 try{
  await api('/api/windows/browse/stop');
  toast('已请求停止养号');
  await refresh();
 }catch(error){$('stopWindowBrowse').disabled=false;toast(error.message)}
};
showPage(location.hash==='#windows'?'windows':'tasks');

const credentialDialog = $('credentialTransfer');
const credentialEncoder = new TextEncoder();
const credentialDecoder = new TextDecoder();

function bytesToHex(bytes) {
 return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
}

function hexToBytes(hex, length) {
 if (typeof hex !== 'string' || hex.length !== length * 2 || !/^[a-f0-9]+$/i.test(hex)) {
  throw Error('加密文件格式无效。');
 }
 return Uint8Array.from(hex.match(/../g), pair => parseInt(pair, 16));
}

async function credentialKey(passphrase, salt) {
 const material = await crypto.subtle.importKey('raw', credentialEncoder.encode(passphrase), 'PBKDF2', false, ['deriveKey']);
 return crypto.subtle.deriveKey({name:'PBKDF2', salt, iterations:600000, hash:'SHA-256'},
  material, {name:'AES-GCM', length:256}, false, ['encrypt','decrypt']);
}

async function exportWindowCredentials(passphrase) {
 const {records} = await api('/api/windows/credentials/export');
 if (!Object.keys(records).length) throw Error('当前电脑没有已保存的窗口凭据。');
 const plaintext = credentialEncoder.encode(JSON.stringify({records}));
 if (plaintext.length > 1000000) throw Error('窗口凭据文件过大。');
 const salt = crypto.getRandomValues(new Uint8Array(16));
 const iv = crypto.getRandomValues(new Uint8Array(12));
 const key = await credentialKey(passphrase, salt);
 const data = await crypto.subtle.encrypt({name:'AES-GCM', iv}, key, plaintext);
 const payload = {version:1, salt:bytesToHex(salt), iv:bytesToHex(iv), data:bytesToHex(new Uint8Array(data))};
 const link = el('a');
 link.download = 'bit-publisher-credentials.json';
 link.href = URL.createObjectURL(new Blob([JSON.stringify(payload)], {type:'application/json'}));
 link.click();
 setTimeout(() => URL.revokeObjectURL(link.href), 1000);
 return '已导出加密文件。请在另一台电脑输入同一口令导入。';
}

async function importWindowCredentials(passphrase) {
 const file = $('credentialFile').files[0];
 if (!file || file.size > 2010000) throw Error('请选择有效的加密文件。');
 const payload = JSON.parse(await file.text());
 if (payload?.version !== 1 || typeof payload.data !== 'string' || payload.data.length > 2000032) {
  throw Error('不支持的凭据文件格式。');
 }
 const salt = hexToBytes(payload.salt, 16);
 const iv = hexToBytes(payload.iv, 12);
 const data = hexToBytes(payload.data, payload.data.length / 2);
 const key = await credentialKey(passphrase, salt);
 let plaintext;
 try {
  plaintext = await crypto.subtle.decrypt({name:'AES-GCM', iv}, key, data);
 } catch {
  throw Error('口令错误或加密文件已损坏。');
 }
 const result = await api('/api/windows/credentials/import', JSON.parse(credentialDecoder.decode(plaintext)));
 await loadProfiles();
 return `已补齐 ${result.imported} 个窗口，${result.existing} 个无需补齐，${result.missing} 个在本机未找到。`;
}

async function transferCredentials(action) {
 const passphrase = $('credentialPassphrase').value;
 const status = $('credentialStatus');
 if (passphrase.length < 12) {
  status.textContent = '请输入至少 12 个字符的口令。';
  return;
 }
 $('exportCredentials').disabled = $('importCredentials').disabled = true;
 status.textContent = '处理中…';
 try {
  status.textContent = await action(passphrase);
 } catch (error) {
  status.textContent = error instanceof SyntaxError ? '加密文件格式无效。' : error.message;
 } finally {
  $('credentialPassphrase').value = '';
  $('exportCredentials').disabled = $('importCredentials').disabled = false;
 }
}
$('openCredentialTransfer').onclick = () => {
 $('credentialStatus').textContent = '';
 credentialDialog.showModal();
};
$('exportCredentials').onclick = () => transferCredentials(exportWindowCredentials);
$('importCredentials').onclick = () => transferCredentials(importWindowCredentials);
credentialDialog.addEventListener('close', () => {
 $('credentialPassphrase').value = '';
 $('credentialFile').value = '';
});

const biliDialog=$('biliDialog');
let biliPlan=null;
function resetBili(){
 biliPlan=null;
 $('biliRows').replaceChildren();
 $('biliSubmit').disabled=true;
 $('biliStatus').textContent='';
}
function biliInput(){
 return {
  profiles:[...$('biliProfiles').querySelectorAll('input:checked')].map(box=>box.value),
  folder:$('biliFolder').value,cover_folder:$('biliCoverFolder').value,title_file:$('biliTitleFile').value,tags:$('biliTags').value,
  statement:$('biliStatement').value,category:$('biliCategory').value.trim(),description:$('biliDescription').value,
  source:$('biliStatement').value==='内容为转载'?$('biliSource').value:'',
 };
}
for(const name of '影视 娱乐 音乐 舞蹈 动画 绘画 鬼畜 游戏 资讯 知识 人工智能 科技数码 汽车 时尚美妆 家装房产 户外潮流 健身 体育运动 手工 美食 小剧场 旅游出行 三农 动物 亲子 健康 情感 vlog 生活兴趣 生活经验'.split(' ')){
 const option=el('option');option.value=name;$('biliCategories').append(option);
}
$('biliOpen').onclick=()=>{
 if(model?.bili_batch_options!==true){toast('请重启发布台服务，然后刷新页面以使用新版 B 站投稿。');return}
 resetBili();
 const list=$('biliProfiles');
 list.replaceChildren();
 for(const w of [...windows].sort((a,b)=>a.seq-b.seq)){
  const label=el('label'),box=el('input');
  box.type='checkbox';box.value=w.id;
  label.append(box,document.createTextNode(' #'+w.seq+' · '+w.name));list.append(label);
 }
 $('biliAll').checked=false;
 $('biliAll').indeterminate=false;
 biliDialog.showModal();
};
$('biliClose').onclick=()=>biliDialog.close();
for(const id of ['biliTags','biliCategory','biliSource','biliDescription'])$(id).oninput=resetBili;
$('biliStatement').onchange=()=>{
 $('biliSourceLabel').hidden=$('biliStatement').value!=='内容为转载';
 resetBili();
};
$('biliProfiles').onchange=()=>{
 const boxes=$('biliProfiles').querySelectorAll('input');
 const count=biliInput().profiles.length;
 $('biliAll').checked=count>0&&count===boxes.length;
 $('biliAll').indeterminate=count>0&&count<boxes.length;
 resetBili();
};
$('biliAll').onchange=()=>{
 for(const box of $('biliProfiles').querySelectorAll('input'))box.checked=$('biliAll').checked;
 resetBili();
};
async function pickBiliPath(kind, field){
 try{
  const {path}=await api('/api/batch/pick',{kind});
  if(path){
   resetBili();$(field).value=path;
   $('biliClearTitles').hidden=!$('biliTitleFile').value;
   $('biliClearCovers').hidden=!$('biliCoverFolder').value;
  }
 }catch(error){$('biliStatus').textContent=error.message}
}
$('biliPick').onclick=()=>pickBiliPath('folder','biliFolder');
$('biliPickCovers').onclick=()=>pickBiliPath('cover_folder','biliCoverFolder');
$('biliClearCovers').onclick=()=>{
 $('biliCoverFolder').value='';$('biliClearCovers').hidden=true;resetBili();
};
$('biliPickTitles').onclick=()=>pickBiliPath('title_file','biliTitleFile');
$('biliClearTitles').onclick=()=>{
 $('biliTitleFile').value='';$('biliClearTitles').hidden=true;resetBili();
};
$('biliPreview').onclick=async()=>{
 resetBili();
 const input=biliInput();
 if(input.cover_folder&&model?.bili_cover_folder!==true){
  $('biliStatus').textContent='请重启发布台服务并刷新页面，以使用自定义封面。';return;
 }
 $('biliPreview').disabled=true;
 try{
  const result=await api('/api/bili/preview',input);
  if(JSON.stringify(input)!==JSON.stringify(biliInput()))return;
  biliPlan={...result,input};
  for(const row of result.rows){
   const label=el('label',`#${row.seq} · ${row.profile_name} ← ${row.filename}`), title=el('input');
   label.append(el('small',row.cover_name?'封面 · '+row.cover_name:'B 站默认封面'));
   title.value=row.text;title.maxLength=80;label.append(title);$('biliRows').append(label);
  }
  $('biliStatus').textContent='共 '+result.rows.length+' 个视频，请核对视频、封面和标题。';
  $('biliSubmit').disabled=false;
 }catch(error){$('biliStatus').textContent=error.message}
 finally{$('biliPreview').disabled=false}
};
$('biliSubmit').onclick=async()=>{
 if(!biliPlan)return;
 const input=biliPlan.input;
 const titles=[...$('biliRows').querySelectorAll('input')].map(e=>e.value);
 const controls=biliDialog.querySelectorAll('button,input,select,textarea');
 controls.forEach(e=>e.disabled=true);
 try{
  const result=await api('/api/bili/create',{...input,signature:biliPlan.signature,titles});
  biliDialog.close();resetBili();toast(result.message);await refresh();
 }catch(error){$('biliStatus').textContent=error.message}
 finally{controls.forEach(e=>e.disabled=false);$('biliSubmit').disabled=!biliPlan}
};
