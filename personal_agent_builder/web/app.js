const token = document.querySelector('#app').dataset.token;
const $ = (selector) => document.querySelector(selector);
const state = { importId: null, prepared: {}, activeAgent: null, agents: [], generating: false,
  planId: null, planQuestions: [], attachedFiles: [], revisionId: null, revisionQuestions: [],
  chatSessions: new Map() };
const stepOrder = ['import', 'understand', 'confirm', 'chat'];
const hasChinese = text => /[\u4e00-\u9fff]/.test(text || '');
function abilityName(name) {
  return abilityNameFor(state.activeAgent,name);
}
function abilityNameFor(agent,name) {
  const description=agent?.tool_descriptions?.[name];
  return hasChinese(description) ? description : '程序功能';
}
function agentName(agent) {
  return hasChinese(agent?.interaction?.title) ? agent.interaction.title : '我的专属智能体';
}

function setBuildStep(name) {
  const current = stepOrder.indexOf(name);
  for (const step of document.querySelectorAll('#build-steps .step')) {
    const index = stepOrder.indexOf(step.dataset.step);
    step.classList.toggle('current', index === current);
    step.classList.toggle('done', index < current);
  }
}

async function api(path, method='GET', value=null, headers={}) {
  const options = { method, headers: { 'X-PAB-Token': token, ...headers } };
  if (value !== null) {
    if (value instanceof Blob || value instanceof ArrayBuffer) options.body = value;
    else { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(value); }
  }
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
  return data;
}

function notice(text, error=false) {
  const box = $('#upload-status'); box.textContent = text;
  box.classList.remove('hidden'); box.classList.toggle('error', error);
}

function report(analysis, candidates) {
  const host = $('#report'); host.replaceChildren();
  const summary = (hasChinese(analysis.summary) ? analysis.summary : `找到 ${candidates.length} 项功能，请在下方查看详情。`) +
    (analysis.adaptation_note ? ` ${analysis.adaptation_note}` : '');
  const examples = analysis.examples?.length ? `例如：“${analysis.examples[0]}”。你也可以用自己的话提出同类需求。`
    : candidates.length ? `例如：“请${hasChinese(candidates[0].description) ? candidates[0].description.replace(/[。.!！]+$/,'') : '使用这项功能处理我的文件'}”。你也可以用自己的话提出同类需求。`
    : '目前没有可保证执行的示例指令；请先补充可运行的功能。';
  const outputs = hasChinese(analysis.output_hint) ? analysis.output_hint : '创建后会显示实际结果和可打开的文件。';
  const sections = [
    ['这个项目能完成什么', summary],
    ['你可以发送哪些指令', examples],
    ['最后会得到什么结果', outputs]
  ];
  for (const [heading, body] of sections) {
    const title=document.createElement('h2');title.textContent=heading;
    const paragraph=document.createElement('p');paragraph.textContent=body;
    host.append(title,paragraph);
  }
}

function renderCandidates(candidates, prepared) {
  const host = $('#candidates'); host.replaceChildren();
  if (!candidates.length) {
    const note = document.createElement('div'); note.className = 'card candidate';
    note.textContent = '暂时没有找到可以直接使用的功能。请检查程序是否有明确的运行入口。';
    host.append(note); return;
  }
  for (const candidate of candidates) {
    const card = document.createElement('div'); card.className = 'candidate card';
    const top = document.createElement('div'); top.className = 'candidate-top';
    const check = document.createElement('input'); check.type = 'checkbox'; check.className = 'tool-check'; check.value = candidate.name;
    check.addEventListener('change',()=>{clearPlan();updateGenerate();});
    const heading = document.createElement('div');
    const title = document.createElement('h3'); title.textContent = /[\u4e00-\u9fff]/.test(candidate.description||'') ? candidate.description : abilityName(candidate.name);
    const description = document.createElement('p'); description.textContent = '有测试资料时可先试运行；没有资料也能创建，使用时再添加。';
    heading.append(title, description); top.append(check, heading); card.append(top);
    const details=document.createElement('details');details.className='test-options';
    const detailTitle=document.createElement('summary');detailTitle.textContent='如果现在有测试资料，可以先填写并试运行';details.append(detailTitle);
    const fields = document.createElement('div'); fields.className = 'fields';
    const tool = prepared[candidate.name].tool; const sample = prepared[candidate.name].sample;
    if (Object.keys(tool.parameters).length) {
      const guide=document.createElement('p');guide.className='field-guide';
      guide.textContent='请核对下面的输入。需要读取文件时，点“添加文件”选择电脑上的文件；结果文件由程序运行时创建。';
      details.append(guide);
    }
    for (const [index, [name, rule]] of Object.entries(tool.parameters).entries()) {
      const wrapper = document.createElement('div'); wrapper.className = 'field';
      const label = document.createElement('label');
      const fallback = rule.role === 'input_file' ? '需要读取的文件' : rule.role === 'output_path' ? '结果保存位置' : `设置项 ${index+1}`;
      label.textContent = `${hasChinese(rule.description) ? rule.description : fallback}${rule.required ? ' · 必填' : ''}`;
      const input = document.createElement('input'); input.dataset.tool = candidate.name; input.dataset.param = name;
      input.value = String(sample[name] ?? rule.default ?? '');
      input.placeholder = rule.role === 'input_file' ? '请添加需要处理的文件' : rule.role === 'output_path' ? '例如 result.json' : rule.required ? '请填写' : '可选';
      wrapper.append(label, input);
      if (rule.hint && hasChinese(rule.hint)) {
        const hint=document.createElement('small');hint.className='field-hint';hint.textContent=rule.hint;wrapper.append(hint);
      }
      if (rule.role === 'input_file') {
        const picker=document.createElement('input');picker.type='file';picker.className='hidden';
        picker.accept='.csv,.tsv,.txt,.json,.toml,.yaml,.yml,.ini,.cfg';
        const add=document.createElement('button');add.className='field-edit';add.type='button';add.textContent='添加文件';
        add.addEventListener('click',()=>picker.click());
        picker.addEventListener('change',async()=>{
          const file=picker.files?.[0];if(!file)return;
          add.disabled=true;add.textContent='正在添加…';
          try {
            const uploaded=await api(`/api/import/${state.importId}/support-file`,'PUT',file,
              {'X-Relative-Path':encodeURIComponent(file.name)});
            input.value=uploaded.name;add.textContent='已添加文件';notice(`已添加 ${uploaded.name}，创建时会用它检查程序。`);
          } catch (error) { add.textContent='添加文件';notice(error.message,true); }
          finally { add.disabled=false;picker.value=''; }
        });
        wrapper.append(add,picker);
      }
      fields.append(wrapper);
    }
    details.append(fields);card.append(details); host.append(card);
  }
}

function updateGenerate() {
  const hasSelection = Boolean(document.querySelector('.tool-check:checked'));
  $('#generate').disabled = state.generating || !$('#confirm').checked || !hasSelection || !state.planId;
  if (state.importId && !state.generating) setBuildStep(hasSelection ? 'confirm' : 'understand');
}

function clearPlan() {
  state.planId=null;state.planQuestions=[];
  $('#plan-questions').replaceChildren();$('#plan-questions').classList.add('hidden');
  $('#plan-summary').replaceChildren();$('#plan-summary').classList.add('hidden');
  $('#finish-plan').classList.add('hidden');$('#confirm').checked=false;
}

function allowedFile(path) {
  const parts = path.replaceAll('\\','/').split('/');
  if (parts.some(x => !x || x.startsWith('.') || ['venv','node_modules','__pycache__','artifacts','records','versions'].includes(x.toLowerCase()))) return false;
  const filename = parts.at(-1).toLowerCase();
  if (['credentials.json','secrets.json','id_rsa','id_ed25519'].includes(filename)) return false;
  return /\.(py|json|csv|tsv|txt|toml|yaml|yml|ini|cfg|md|png|svg|jpg|jpeg)$/i.test(filename);
}

async function readEntry(entry) {
  if (entry.isFile) return [await new Promise((resolve,reject)=>entry.file(file=>resolve({file,path:entry.fullPath.replace(/^\//,'')}),reject))];
  if (!entry.isDirectory) return [];
  const reader = entry.createReader(); const children = [];
  while (true) {
    const batch = await new Promise((resolve,reject)=>reader.readEntries(resolve,reject));
    if (!batch.length) break;
    for (const child of batch) children.push(...await readEntry(child));
  }
  return children;
}

async function importFiles(items) {
  $('#analysis-section').classList.add('hidden'); state.importId=null;
  const files = items.filter(item => allowedFile(item.path));
  if (!files.length) throw new Error('没有找到可导入的程序文件。');
  if (files.length > 250) throw new Error('可导入文件超过 250 个。');
  notice(`正在导入 ${files.length} 个文件；只读分析会在上传完成后开始…`);
  const roots = new Set(files.map(item => item.path.replaceAll('\\','/').split('/')[0]));
  const projectName = roots.size === 1 && files[0].path.includes('/') ? [...roots][0] : (files.find(item => item.path.endsWith('.py'))?.file.name.replace(/\.py$/i, '') || 'python-project');
  const started = await api('/api/import/start', 'POST', {project_name: projectName});
  for (let i=0; i<files.length; i++) {
    const item = files[i];
    await api(`/api/import/${started.import_id}/file`, 'PUT', item.file,
      { 'X-Relative-Path': encodeURIComponent(item.path) });
    if (i % 5 === 0 || i === files.length-1) notice(`已安全复制 ${i+1} / ${files.length} 个文件…`);
  }
  await finishImport(started.import_id);
}

async function importZip(file) {
  $('#analysis-section').classList.add('hidden'); state.importId=null;
  if (!file.name.toLowerCase().endsWith('.zip')) throw new Error('请选择压缩包。');
  if (file.size > 20_000_000) throw new Error('压缩包不能超过 20 兆。');
  notice('正在导入压缩包；不会运行其中的程序…');
  const started = await api('/api/import/start', 'POST', {project_name: file.name.replace(/\.zip$/i, '')});
  await api(`/api/import/${started.import_id}/archive`, 'POST', file);
  await finishImport(started.import_id);
}

async function finishImport(id) {
  notice('正在阅读刚导入的程序和说明，整理它能完成的事；此时不会运行程序…');
  const data = await api(`/api/import/${id}/finish`, 'POST', {});
  state.importId = id; state.prepared = data.prepared;
  report(data.analysis, data.candidates); renderCandidates(data.candidates, data.prepared);
  clearPlan();$('#agent-intent').value='';
  $('#confirm').checked = false; updateGenerate();
  setBuildStep('understand');
  $('#analysis-section').classList.remove('hidden');
  notice(`导入完成，找到 ${data.candidates.length} 项功能。请选择要使用的功能。`);
  $('#analysis-section').scrollIntoView({behavior:'smooth',block:'start'});
}

function showView(view) {
  $('#build-view').classList.toggle('hidden', view !== 'build');
  $('#chat-view').classList.toggle('hidden', view !== 'chat');
  $('#nav-build').classList.toggle('active', view === 'build');
  $('#page-title').textContent = view === 'build' ? '创建你的智能体' : '我的工作区';
  if (view === 'chat') setBuildStep('chat');
  window.scrollTo(0, 0);
}

function newImport() {
  state.importId=null; state.prepared={};clearPlan();
  $('#analysis-section').classList.add('hidden');
  $('#upload-status').classList.add('hidden');
  $('#file-input').value=''; $('#folder-input').value=''; $('#zip-input').value='';
  setBuildStep('import');
  showView('build'); window.scrollTo({top:0,behavior:'smooth'});
}

async function refreshAgents() {
  state.agents = (await api('/api/agents')).agents;
  const list = $('#agent-list'); list.replaceChildren();
  if (!state.agents.length) { const p=document.createElement('p'); p.className='muted small'; p.textContent='这里还没有智能体。请先导入一份程序。'; list.append(p); }
  for (const [index, agent] of state.agents.entries()) {
    const row=document.createElement('div');row.className='agent-row';
    const button = document.createElement('button'); button.className='agent-link'; button.textContent=`✦  ${agentName(agent)} ${index+1}`;
    button.dataset.agentId = agent.id;
    button.classList.toggle('selected', state.activeAgent?.id === agent.id);
    button.addEventListener('click',()=>openAgent(agent));
    const remove=document.createElement('button');remove.className='agent-delete';remove.type='button';remove.textContent='删除';
    remove.setAttribute('aria-label',`删除${agentName(agent)} ${index+1}`);
    remove.addEventListener('click',async()=>{
      if(!window.confirm(`确定删除“${agentName(agent)} ${index+1}”吗？\n它的对话记录和结果文件也会删除，无法恢复。原始程序不会受影响。`))return;
      try{
        await api(`/api/agents/${agent.id}`,'DELETE');
        state.chatSessions.delete(agent.id);
        if(state.activeAgent?.id===agent.id){state.activeAgent=null;state.attachedFiles=[];newImport();}
        await refreshAgents();notice('智能体已删除，原始程序仍在。');
      }catch(error){notice(error.message,true);}
    });
    row.append(button,remove);list.append(row);
  }
}

function openAgent(agent, tests=null) {
  if(state.activeAgent && state.chatSessions.has(state.activeAgent.id)) {
    const previous=state.chatSessions.get(state.activeAgent.id);
    previous.draft=$('#chat-input').value;
    previous.fileStatus=$('#chat-file-status').textContent;
    previous.attachedFiles=state.attachedFiles;
  }
  let session=state.chatSessions.get(agent.id);
  if(!session) {
    session={messages:$('#messages').cloneNode(false),tests:$('#test-results').cloneNode(false),
      attachedFiles:[],fileStatus:'可以反复添加新文件，不用重新创建智能体',draft:'',
      initialized:false,pending:false,pendingLoaded:false};
    state.chatSessions.set(agent.id,session);
  }
  if($('#messages')!==session.messages) $('#messages').replaceWith(session.messages);
  if($('#test-results')!==session.tests) $('#test-results').replaceWith(session.tests);
  state.activeAgent=agent;state.attachedFiles=session.attachedFiles;showView('chat');
  state.revisionId=null;state.revisionQuestions=[];
  $('#revision-panel').classList.add('hidden');
  $('#revision-questions').replaceChildren();$('#revision-questions').classList.add('hidden');
  $('#revision-summary').replaceChildren();$('#revision-summary').classList.add('hidden');
  $('#revision-finalize').classList.add('hidden');$('#revision-activate').classList.add('hidden');
  $('#revision-input').value=agent.pending_revision||'';
  $('#revision-status').textContent=agent.pending_revision?'上次的调整想法已保存，可以继续讨论。':'';
  $('#revert-adjust').classList.toggle('hidden', !agent.can_revert_interaction);
  $('#chat-file-status').textContent=session.fileStatus;
  $('#chat-input').value=session.draft;
  $('#chat-form button[type="submit"]').disabled=session.pending;
  $('#chat-add-file').textContent=agent.interaction?.upload_label||'添加本次文件';
  $('#rollback-upgrade').classList.toggle('hidden', !agent.can_rollback);
  for (const button of document.querySelectorAll('.agent-link')) button.classList.toggle('selected', button.dataset.agentId === agent.id);
  $('#agent-title').textContent = agentName(agent);
  $('#agent-tools').textContent = `可以使用：${agent.tools.map(abilityName).join('、')}`;
  if(!session.initialized) {
  $('#messages').replaceChildren();
  const greeting=document.createElement('div'); greeting.className='assistant-message';
  greeting.textContent=agent.interaction?.welcome||'你好，告诉我你想做什么。我会在这里显示处理结果和文件。'; $('#messages').append(greeting);
  const prompts=document.createElement('div'); prompts.className='prompt-suggestions';
  const promptLabel=document.createElement('span'); promptLabel.textContent='试试这样提问'; prompts.append(promptLabel);
  for (const name of agent.tools.slice(0,3)) {
    const example = `请${abilityName(name).replace(/[。.!！]+$/,'')}`;
    const button=document.createElement('button'); button.type='button'; button.textContent=`${example} ↗`;
    button.addEventListener('click',()=>{$('#chat-input').value=example;$('#chat-input').focus();});
    prompts.append(button);
  }
  $('#messages').append(prompts);
  if (agent.recent_turns?.length) {
    const label=document.createElement('div');label.className='muted small';
    label.textContent='最近的对话（可直接接着说）';$('#messages').append(label);
    for (const turn of agent.recent_turns) {
      const user=document.createElement('div');user.className='user-message';user.textContent=turn.user;
      const assistant=document.createElement('div');assistant.className='assistant-message';assistant.textContent=turn.assistant;
      $('#messages').append(user,assistant);
    }
    $('#messages').scrollTop=$('#messages').scrollHeight;
  }
  const host=$('#test-results'); host.replaceChildren();
  if (tests) for (const [name, test] of Object.entries(tests)) {
    const card=document.createElement('div'); const good=test.record?.verified_output===true;
    card.className=`test-card ${good?'':'fail'}`;
    const evidence=test.status==='skipped' ? '还没有试运行；添加资料后可在对话中使用'
      : good ? (test.artifacts?.length ? '运行成功，结果文件已检查' : '运行成功，结果已检查') : '运行检查未通过';
    card.textContent=`${abilityName(name)}：${evidence}${test.reason?` · ${test.reason}`:''}${test.error?` · ${test.error}`:''}${test.record?.validation_error?' · 结果未通过检查':''}`;
    host.append(card);
    if (test.record) renderResult(test.record,test.artifacts||[],host);
  }
  session.initialized=true;
  }
  if(!session.pendingLoaded){
    session.pendingLoaded=true;
    api(`/api/upgrade/pending?agent_id=${agent.id}`).then(data=>{
      if(!data.proposal)return;
      const box=document.createElement('div');box.className='result';
      const title=document.createElement('strong');title.textContent='上次准备的新功能（尚未启用）';box.append(title);
      showUpgradePanel(box,data.proposal,agent.id);session.messages.append(box);
    }).catch(()=>{session.pendingLoaded=false;});
  }
  $('#chat-input').focus({preventScroll:true});
}

function renderResult(record, artifacts, host=$('#messages'), requestText='', agentId=state.activeAgent?.id) {
  const box=document.createElement('div'); box.className='result';
  const title=document.createElement('strong');
  if (record.status === 'upgrade_requested') title.textContent='正在为这项需求准备新功能';
  else if (record.status === 'personalization_requested') title.textContent='可以调整这个智能体';
  else if (record.status === 'needs_clarification') title.textContent='需要补充信息';
  else if (record.status === 'service_unavailable') title.textContent='这次没有完成理解';
  else if (record.status === 'answered') title.textContent=record.source_verified?'关于已验证结果的说明':record.file_reviewed?'根据程序文件的说明':'关于这个智能体的说明';
  else title.textContent=`${abilityNameFor(state.agents.find(item=>item.id===agentId)||state.activeAgent,record.tool)} · ${record.verified_output?(artifacts.length?'已核对结果文件':'已核对计算结果'):'结果尚未通过检查'}`;
  box.append(title);
  if (record.message) {const p=document.createElement('p');p.textContent=record.message;box.append(p);}
  if (record.status === 'upgrade_requested' && record.upgrade_request) {
    const p=document.createElement('p');p.textContent=`准备修改的需求：${record.upgrade_request}`;box.append(p);
  }
  if (record.stdout) {
    const details=document.createElement('details');
    const label=document.createElement('summary');label.textContent='查看原始结果';
    const pre=document.createElement('pre');
    try {
      const payload=JSON.parse(record.stdout.trim().split('\n').at(-1));
      pre.textContent=JSON.stringify(payload,null,2);
    }
    catch {pre.textContent=record.stdout;}
    details.append(label,pre);box.append(details);
  }
  if (record.stderr || record.validation_error) { const p=document.createElement('p'); p.textContent='程序没有顺利完成，请检查输入内容；详细记录已保存在本机。';box.append(p); }
  if (artifacts.length) {
    const files=document.createElement('div'); files.className='artifacts';
    for (const item of artifacts) {
      const a=document.createElement('a'); a.className='artifact'; a.href=item.url; a.target='_blank'; a.rel='noopener';
      if (/\.(png|jpg|jpeg)$/i.test(item.name)) {const img=document.createElement('img');img.src=item.url;img.alt=item.name;a.append(img);}
      a.append(document.createTextNode(`↗ 打开结果文件 ${files.children.length+1}`)); files.append(a);
    }
    box.append(files);
  }
  if (record.status === 'upgrade_requested') {
    const prepare=document.createElement('button');prepare.className='secondary upgrade-action';
    prepare.type='button';prepare.textContent='准备新功能';
    prepare.addEventListener('click',async()=>{
      prepare.disabled=true;prepare.textContent='正在准备并检查新功能…';
      try{
        const request=record.upgrade_request||requestText||box.previousElementSibling?.textContent?.trim()||'';
        const result=await api('/api/upgrade/prepare','POST',{agent_id:agentId,request});
        if(result.status==='needs_clarification'){
          const p=document.createElement('p');p.textContent=result.message;box.append(p);
          prepare.textContent='补充需求后再准备升级';return;
        }
        showUpgradePanel(box,result,agentId);prepare.remove();
      }catch(error){prepare.disabled=false;prepare.textContent='重试准备升级';notice(error.message,true);}
    });box.append(prepare);
  }
  host.append(box); host.scrollTop=host.scrollHeight;
  if(record.status==='upgrade_requested' && requestText) queueMicrotask(()=>box.querySelector('button.upgrade-action')?.click());
  if(record.status==='personalization_requested' && requestText && state.activeAgent?.id===agentId) queueMicrotask(()=>{
    $('#revision-panel').classList.remove('hidden');
    $('#revision-input').value=requestText;
    $('#revision-discuss').click();
    $('#revision-panel').scrollIntoView({behavior:'smooth',block:'start'});
  });
}

function showUpgradePanel(box,result,agentId){
  const panel=document.createElement('div');panel.className='upgrade-panel';
  const heading=document.createElement('strong');heading.textContent=result.test_passed?'新功能已通过检查':'新功能尚未通过检查';
  const detail=document.createElement('p');detail.textContent=`新增功能：${result.new_ability||'新的程序功能'}`;
  const test=document.createElement('pre');test.textContent=result.test_result||'没有得到可核对的测试结果';
  const changes=document.createElement('details');
  const summary=document.createElement('summary');summary.textContent='查看程序修改内容';
  const diff=document.createElement('pre');diff.textContent=result.diff||'没有可展示的修改';
  changes.append(summary,diff);panel.append(heading,detail);
  if(result.assumptions?.length){
    const assumptions=document.createElement('p');assumptions.textContent=`本次沿用的设置：${result.assumptions.join('；')}`;
    panel.append(assumptions);
  }
  panel.append(test);
  if(result.regression?.length){
    const checks=document.createElement('p');checks.textContent=`原有功能复查：${result.regression.map(item=>item.status).join('、')}`;
    panel.append(checks);
  }
  panel.append(changes);
  if(result.test_passed){
    const approve=document.createElement('button');approve.className='primary upgrade-action';
    approve.type='button';approve.textContent='确认并启用这项升级';
    approve.addEventListener('click',async()=>{
      approve.disabled=true;approve.textContent='正在启用…';
      try{
        await api('/api/upgrade/activate','POST',{agent_id:agentId,proposal_id:result.proposal_id,version:result.version,confirmed:true});
        await refreshAgents();
        if(state.activeAgent?.id===agentId){
          state.activeAgent=state.agents.find(item=>item.id===agentId);
          $('#agent-tools').textContent=`可以使用：${state.activeAgent.tools.map(abilityName).join('、')}`;
          $('#rollback-upgrade').classList.remove('hidden');
        }
        const done=document.createElement('p');done.textContent='新功能已启用。现在可以直接使用。';panel.append(done);
        approve.remove();
      }catch(error){approve.disabled=false;approve.textContent='确认并启用这项升级';notice(error.message,true);}
    });panel.append(approve);
  }
  box.append(panel);
}

$('#choose-file').addEventListener('click',()=>$('#file-input').click());
$('#file-input').addEventListener('change',async event=>{
  try {if(event.target.files[0]) await importFiles([{file:event.target.files[0],path:event.target.files[0].name}]);}
  catch(error){notice(error.message,true);}
});
$('#choose-folder').addEventListener('click',()=>$('#folder-input').click());
$('#choose-zip').addEventListener('click',()=>$('#zip-input').click());
$('#folder-input').addEventListener('change',async event=>{
  try { await importFiles([...event.target.files].map(file=>({file,path:file.webkitRelativePath||file.name}))); }
  catch(error){notice(error.message,true);}
});
$('#zip-input').addEventListener('change',async event=>{
  try {if(event.target.files[0]) await importZip(event.target.files[0]);}
  catch(error){notice(error.message,true);}
});
const zone=$('#dropzone');
for(const name of ['dragenter','dragover']) zone.addEventListener(name,event=>{event.preventDefault();zone.classList.add('drag');});
for(const name of ['dragleave','drop']) zone.addEventListener(name,event=>{event.preventDefault();zone.classList.remove('drag');});
zone.addEventListener('drop',async event=>{
  try {
    const data=event.dataTransfer;
    if(data.files.length===1 && data.files[0].name.toLowerCase().endsWith('.zip')) {await importZip(data.files[0]);return;}
    const entries=[...data.items].map(item=>item.webkitGetAsEntry?.()).filter(Boolean);
    const files=[];for(const entry of entries) files.push(...await readEntry(entry));
    if(!files.length) files.push(...[...data.files].map(file=>({file,path:file.name})));
    await importFiles(files);
  } catch(error){notice(error.message,true);}
});
zone.addEventListener('keydown',event=>{if(event.key==='Enter') $('#file-input').click();});
$('#agent-intent').addEventListener('input',()=>{clearPlan();updateGenerate();});
$('#ask-plan').addEventListener('click',async()=>{
  const selected=[...document.querySelectorAll('.tool-check:checked')].map(x=>x.value);
  const intent=$('#agent-intent').value.trim();
  if(!selected.length||!intent){notice('请先选择功能，再说说你希望它怎样帮你。',true);return;}
  const button=$('#ask-plan');button.disabled=true;button.textContent='正在梳理…';clearPlan();
  try{
    const data=await api(`/api/import/${state.importId}/plan`,'POST',{stage:'questions',selected,intent});
    state.planQuestions=data.questions;
    const host=$('#plan-questions');host.replaceChildren();
    for(const [index,question] of data.questions.entries()){
      const row=document.createElement('div');row.className='plan-question';
      const label=document.createElement('label');label.textContent=`${index+1}. ${question}`;
      const answer=document.createElement('textarea');answer.className='plan-answer';answer.maxLength=1000;
      answer.placeholder='用自己的话回答即可';row.append(label,answer);host.append(row);
    }
    host.classList.toggle('hidden',data.questions.length===0);
    $('#finish-plan').classList.toggle('hidden',data.questions.length===0);
    if(data.questions.length) notice('请回答这个问题，再核对创建方案。');
    else {notice('信息已足够，正在整理创建方案…');$('#finish-plan').click();}
  }catch(error){notice(error.message,true);}
  finally{button.disabled=false;button.textContent='重新讨论使用方式';}
});
$('#finish-plan').addEventListener('click',async()=>{
  const selected=[...document.querySelectorAll('.tool-check:checked')].map(x=>x.value);
  const answers=[...document.querySelectorAll('.plan-answer')].map(x=>x.value.trim());
  if(answers.length!==state.planQuestions.length||answers.some(x=>!x)){notice('请先回答每个问题。',true);return;}
  const button=$('#finish-plan');button.disabled=true;button.textContent='正在整理方案…';
  try{
    const data=await api(`/api/import/${state.importId}/plan`,'POST',{
      stage:'finalize',selected,intent:$('#agent-intent').value.trim(),answers});
    state.planId=data.plan_id;
    const host=$('#plan-summary');host.replaceChildren();
    for(const [heading,value] of [['智能体名称',data.plan.title],['打开后会怎样引导你',data.plan.welcome],
      ['以后怎样使用',data.plan.usage],['遇到不确定时',data.plan.behavior],['当前做不到的事',data.plan.limits]]){
      const item=document.createElement('p');const strong=document.createElement('strong');strong.textContent=heading+'：';
      item.append(strong,document.createTextNode(value));host.append(item);
    }
    host.classList.remove('hidden');updateGenerate();notice('创建方案已准备好。请核对后再确认。');
  }catch(error){notice(error.message,true);}
  finally{button.disabled=false;button.textContent='重新整理方案';}
});
$('#confirm').addEventListener('change',updateGenerate);
$('#generate').addEventListener('click',async()=>{
  try {
    const selected=[...document.querySelectorAll('.tool-check:checked')].map(x=>x.value);
    if(!selected.length || !$('#confirm').checked) throw new Error('请先选择能力并确认。');
    const test_params={};
    for(const name of selected){test_params[name]={};for(const field of document.querySelectorAll('.field input')) if(field.dataset.tool===name && field.value.trim()) test_params[name][field.dataset.param]=field.value.trim();}
    state.generating=true; updateGenerate();
    $('#generate').textContent='正在创建并测试…';
    notice('正在创建智能体，并实际测试你选中的程序功能…');
    const result=await api('/api/generate','POST',{import_id:state.importId,plan_id:state.planId,selected,confirmed:true,test_params});
    await refreshAgents();openAgent(result.agent,result.tests);notice('智能体已创建。请查看测试结果，再开始对话。');
  }catch(error){notice(error.message,true);}finally{state.generating=false;$('#generate').innerHTML='创建并检查 <span aria-hidden="true">→</span>';updateGenerate();}
});
async function attachChatFiles(files){
  if(!state.activeAgent||!files.length)return;
  const agentId=state.activeAgent.id,session=state.chatSessions.get(agentId);
  if(session.attachedFiles.length+files.length>3){$('#chat-file-status').textContent='每次最多添加 3 个文件。';return;}
  session.fileStatus='正在添加文件…';$('#chat-file-status').textContent=session.fileStatus;
  try{
    for(const file of files){
      const uploaded=await api(`/api/agents/${agentId}/file`,'PUT',file,
        {'X-Relative-Path':encodeURIComponent(file.name)});
      session.attachedFiles.push({name:uploaded.name,path:uploaded.path});
    }
    session.fileStatus=`本次已添加：${session.attachedFiles.map(x=>x.name).join('、')}。现在说出要完成的事。`;
    if(state.activeAgent?.id===agentId){state.attachedFiles=session.attachedFiles;$('#chat-file-status').textContent=session.fileStatus;$('#chat-input').focus();}
  }catch(error){session.fileStatus=`文件未全部添加：${error.message}`;
    if(state.activeAgent?.id===agentId)$('#chat-file-status').textContent=session.fileStatus;}
}
$('#chat-add-file').addEventListener('click',()=>$('#chat-file-input').click());
$('#chat-file-input').addEventListener('change',async event=>{
  await attachChatFiles([...event.target.files]);event.target.value='';
});
const chatFiles=$('#chat-files');
for(const name of ['dragenter','dragover'])chatFiles.addEventListener(name,event=>{event.preventDefault();chatFiles.classList.add('drag');});
for(const name of ['dragleave','drop'])chatFiles.addEventListener(name,event=>{event.preventDefault();chatFiles.classList.remove('drag');});
chatFiles.addEventListener('drop',event=>{attachChatFiles([...event.dataTransfer.files]);});
$('#chat-form').addEventListener('submit',async event=>{
  event.preventDefault();const input=$('#chat-input');const message=input.value.trim();if(!message||!state.activeAgent)return;
  const agentId=state.activeAgent.id,session=state.chatSessions.get(agentId);
  if(session.pending)return;
  session.pending=true;$('#chat-form button[type="submit"]').disabled=true;
  input.value='';session.draft='';const host=session.messages;
  const bubble=document.createElement('div');bubble.className='user-message';bubble.textContent=message;host.append(bubble);
  const wait=document.createElement('div');wait.className='assistant-message';wait.textContent='正在理解需求并运行已批准的能力…';host.append(wait);
  try{
    const attached_files=session.attachedFiles.map(x=>x.path);
    const data=await api('/api/chat','POST',{agent_id:agentId,message,attached_files});
    session.attachedFiles=[];session.fileStatus='可以继续添加另一份文件，不用重新创建智能体';
    if(state.activeAgent?.id===agentId){state.attachedFiles=[];$('#chat-file-status').textContent=session.fileStatus;}
    wait.remove();renderResult(data.record,data.artifacts,host,message,agentId);
  }
  catch(error){wait.textContent=`请求未完成：${error.message}`;}
  finally{session.pending=false;if(state.activeAgent?.id===agentId)$('#chat-form button[type="submit"]').disabled=false;}
});
$('#adjust-agent').addEventListener('click',()=>{
  $('#revision-panel').classList.toggle('hidden');
  if(!$('#revision-panel').classList.contains('hidden'))$('#revision-input').focus();
});
$('#revision-input').addEventListener('input',()=>{
  state.revisionId=null;state.revisionQuestions=[];
  $('#revision-questions').replaceChildren();$('#revision-questions').classList.add('hidden');
  $('#revision-summary').replaceChildren();$('#revision-summary').classList.add('hidden');
  $('#revision-finalize').classList.add('hidden');$('#revision-activate').classList.add('hidden');
});
$('#revision-discuss').addEventListener('click',async()=>{
  const request=$('#revision-input').value.trim();
  if(!state.activeAgent||!request){$('#revision-status').textContent='请先说说希望怎样调整。';return;}
  const button=$('#revision-discuss');button.disabled=true;button.textContent='正在理解调整需求…';
  try{
    const data=await api('/api/revision/discuss','POST',{agent_id:state.activeAgent.id,request});
    if(data.status==='waiting_for_service'){$('#revision-status').textContent=data.message;return;}
    if(data.status==='program_change'){
      $('#revision-status').textContent=`${data.message} 已将需求放入下方对话框，请发送后查看副本修改和测试结果。`;
      $('#chat-input').value=request;$('#chat-input').focus();return;
    }
    state.revisionId=data.revision_id;state.revisionQuestions=data.questions;
    const host=$('#revision-questions');host.replaceChildren();
    for(const [index,question] of data.questions.entries()){
      const row=document.createElement('div');row.className='plan-question';
      const label=document.createElement('label');label.textContent=`${index+1}. ${question}`;
      const answer=document.createElement('textarea');answer.className='revision-answer';answer.maxLength=1000;
      answer.placeholder='用自己的话回答即可';row.append(label,answer);host.append(row);
    }
    host.classList.toggle('hidden',data.questions.length===0);
    $('#revision-finalize').classList.remove('hidden');
    $('#revision-status').textContent=data.questions.length?'请回答问题，再核对调整方案。':'需求已明确，可以直接查看调整方案。';
  }catch(error){$('#revision-status').textContent=`暂未完成：${error.message}`;}
  finally{button.disabled=false;button.textContent='讨论调整方式';}
});
$('#revision-finalize').addEventListener('click',async()=>{
  const answers=[...document.querySelectorAll('.revision-answer')].map(x=>x.value.trim());
  if(answers.length!==state.revisionQuestions.length||answers.some(x=>!x)){
    $('#revision-status').textContent='请先回答每个问题。';return;
  }
  const button=$('#revision-finalize');button.disabled=true;button.textContent='正在整理调整方案…';
  try{
    const data=await api('/api/revision/finalize','POST',{revision_id:state.revisionId,answers});
    const host=$('#revision-summary');host.replaceChildren();
    const intro=document.createElement('p');intro.textContent=`这次会改变：${data.plan.change_summary}`;host.append(intro);
    for(const [key,label] of [['title','显示名称'],['welcome','打开时的话'],['usage','日常使用方式'],
      ['behavior','提问与处理方式'],['limits','当前限制'],['upload_label','添加文件按钮']]){
      const row=document.createElement('p');const strong=document.createElement('strong');strong.textContent=label+'：';
      row.append(strong,document.createTextNode(data.plan.interaction[key]));host.append(row);
    }
    host.classList.remove('hidden');$('#revision-activate').classList.remove('hidden');
    $('#revision-status').textContent='请核对完整方案；确认后才会改变这个智能体。';
  }catch(error){$('#revision-status').textContent=`暂未完成：${error.message}`;}
  finally{button.disabled=false;button.textContent='查看调整方案';}
});
$('#revision-activate').addEventListener('click',async()=>{
  const button=$('#revision-activate');button.disabled=true;button.textContent='正在应用…';
  try{
    const id=state.activeAgent.id;
    await api('/api/revision/activate','POST',{revision_id:state.revisionId,confirmed:true});
    await refreshAgents();openAgent(state.agents.find(item=>item.id===id));
    notice('调整已应用。你可以继续使用这个智能体，也可以再次调整。');
  }catch(error){$('#revision-status').textContent=`应用失败：${error.message}`;}
  finally{button.disabled=false;button.textContent='确认并应用调整';}
});
$('#revert-adjust').addEventListener('click',async()=>{
  if(!state.activeAgent||!window.confirm('确定撤销上次对这个智能体使用方式的调整吗？'))return;
  try{
    const id=state.activeAgent.id;
    await api('/api/revision/rollback','POST',{agent_id:id,confirmed:true});
    await refreshAgents();openAgent(state.agents.find(item=>item.id===id));
    notice('已恢复之前的使用方式。');
  }catch(error){notice(error.message,true);}
});
$('#nav-build').addEventListener('click',newImport);
$('#new-project').addEventListener('click',newImport);
$('#rollback-upgrade').addEventListener('click',async()=>{
  if(!state.activeAgent||!window.confirm('确定恢复上一个版本吗？当前升级会停止使用，原有文件仍保留。'))return;
  try{
    const id=state.activeAgent.id;
    await api('/api/upgrade/rollback','POST',{agent_id:id,confirmed:true});
    await refreshAgents();openAgent(state.agents.find(item=>item.id===id));
    notice('已恢复上一个版本。');
  }catch(error){notice(error.message,true);}
});
$('#close-app').addEventListener('click',async()=>{try{await api('/api/shutdown','POST',{});}finally{document.body.innerHTML='<main style="padding:60px;font:20px sans-serif">应用已关闭。可以关闭这个页面。</main>';}});
refreshAgents().catch(error=>notice(error.message,true));
