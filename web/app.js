'use strict';
const token=document.querySelector('meta[name="tooru-token"]').content;
const content=document.getElementById('content');
let state,page='main',tab='chat';
const labels={main:'Главная',profile:'Личный кабинет',ai:'Tooru/Ai',work:'Рабочие проекты',home:'Домашние проекты',mobile:'Мобильное приложение',settings:'Настройки',updates:'Система обновления',diagnostics:'Система диагностики'};
const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtDate=value=>{try{return new Date(value).toLocaleString('ru-RU')}catch(_e){return ''}};
function message(text){document.getElementById('message').textContent=text||''}
async function api(path,data){
  const response=await fetch('/api/'+path,{method:data===undefined?'GET':'POST',headers:{'X-Tooru-Token':token,'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});
  const result=await response.json();
  if(!response.ok)throw new Error(result.error||'Ошибка сервера');
  return result;
}
async function reload(){
  state=await api('state');
  document.documentElement.style.colorScheme=state.settings.theme==='system'?'light dark':state.settings.theme;
  document.getElementById('avatar').textContent=state.settings.name.slice(0,1).toUpperCase();
  document.getElementById('connection').textContent=state.ai_connected?'AI подключён':'Локально';
}
function basicSettings(profile){
  return `<form id="settings-form" class="panel clean-form">
    <span class="eyebrow">${profile?'Профиль':'Интерфейс'}</span>
    <h2>${profile?'Личный кабинет':'Оформление'}</h2>
    <label for="name">Имя</label>
    <input id="name" name="name" maxlength="80" value="${escapeHtml(state.settings.name)}" required>
    <label for="theme">Тема</label>
    <select id="theme" name="theme"><option value="system">Как в системе</option><option value="light">Светлая</option><option value="dark">Тёмная</option></select>
    <button class="primary">Сохранить</button>
  </form>`;
}
function aiSettings(){
  const q=state.learning||{};
  return `<section class="panel settings-card">
    <div class="section-head"><div><span class="eyebrow">Модель</span><h2>AI Studio</h2></div><span class="status-label ${q.last_success>0?'ok':''}">${!q.configured?'Не настроено':q.last_success>0?'Подключено':'Ключ сохранён'}</span></div>
    <form id="ai-config-form" class="clean-form">
      <label for="ai-folder">Каталог Yandex Cloud</label>
      <input id="ai-folder" name="folder_id" maxlength="64" value="${escapeHtml(q.folder_id||'')}" required>
      <label for="ai-model">Модель</label>
      <input id="ai-model" name="model" maxlength="120" value="${escapeHtml(q.model||'')}" required>
      <label for="ai-key">API-ключ</label>
      <input id="ai-key" name="api_key" type="password" maxlength="500" autocomplete="off" placeholder="${q.configured?'Ключ сохранён — оставь пустым, чтобы не менять':'Вставь API-ключ'}">
      <div class="form-actions"><button class="primary" type="submit">Сохранить</button><button class="action" type="button" data-ai-test ${q.configured?'':'disabled'}>Проверить связь</button></div>
      <p class="hint">Ключ хранится только в локальной базе и не показывается обратно.</p>
    </form>
  </section>`;
}
function recordList(kind,emptyText){
  const rows=state.records[kind]||[];
  if(!rows.length)return `<div class="empty-state">${emptyText}</div>`;
  return rows.map(r=>`<article class="library-card">
    <div class="library-head"><div><h3>${escapeHtml(r.title)}</h3><span class="meta">${escapeHtml(r.source)} · ${escapeHtml(fmtDate(r.created_at))}</span></div><button class="icon-action" data-delete="${r.id}" title="Удалить">×</button></div>
    ${r.body?`<p>${escapeHtml(r.body)}</p>`:''}
  </article>`).join('');
}
function libraryPanel(kind,title,description,titleLabel,bodyLabel){
  return `<div class="library-layout">
    <section class="panel library-intro"><span class="eyebrow">${kind==='memory'?'Личное':'База'}</span><h2>${title}</h2><p>${description}</p><strong class="big-count">${state.counts[kind]||0}</strong><span class="meta">записей</span></section>
    <form id="record-form" data-kind="${kind}" class="panel clean-form">
      <h2>Добавить</h2>
      <label for="record-title">${titleLabel}</label><input id="record-title" name="title" maxlength="300" required>
      <label for="record-body">${bodyLabel}</label><textarea id="record-body" name="body" maxlength="20000"></textarea>
      <button class="primary">Сохранить</button>
    </form>
  </div>
  <section class="panel library-list"><div class="section-head"><h2>${title}</h2><span class="meta">Последние записи</span></div>${recordList(kind,'Пока пусто.')}</section>`;
}
function projectPanel(kind,title){
  return `<div class="library-layout"><section class="panel library-intro"><span class="eyebrow">Проекты</span><h2>${title}</h2><p>Задачи, заметки и контекст в одном месте.</p><strong class="big-count">${state.counts[kind]||0}</strong><span class="meta">проектов</span></section>
  <form id="record-form" data-kind="${kind}" class="panel clean-form"><h2>Новый проект</h2><label for="record-title">Название</label><input id="record-title" name="title" maxlength="300" required><label for="record-body">Описание и задачи</label><textarea id="record-body" name="body" maxlength="20000"></textarea><button class="primary">Создать</button></form></div>
  <section class="panel library-list">${recordList(kind,'Проектов пока нет.')}</section>`;
}
function chatMessages(c){
  const rows=c?.messages||[];
  if(!rows.length)return '<div class="chat-empty"><strong>Тори готова к диалогу</strong><span>Напиши первое сообщение.</span></div>';
  return rows.map(x=>{
    const role=x.role==='user'?'Ты':x.role==='tori'?'Тори':'Система';
    const side=x.role==='user'?'user':x.role==='tori'?'assistant':'system';
    const usage=(x.input_tokens||x.output_tokens)?`<span>${x.input_tokens||0} + ${x.output_tokens||0} ток.</span>`:'';
    return `<article class="chat-message ${side}"><div class="chat-author">${role}</div><div class="chat-bubble">${escapeHtml(x.text)}</div><div class="chat-meta">${escapeHtml(fmtDate(x.created_at))}${usage}</div></article>`;
  }).join('');
}
function chatPanel(){
  const c=state.chat||{configured:false,messages:[]};
  return `<section class="conversation-shell">
    <div class="conversation-head"><div><span class="eyebrow">Личный помощник</span><h2>Чат с Тори</h2></div><span class="status-label ${c.last_success>0?'ok':''}">${!c.configured?'Настрой AI Studio':c.last_success>0?'Онлайн':'Готова'}</span></div>
    <div class="chat-stream" id="chat-stream">${chatMessages(c)}</div>
    <form id="chat-form" class="chat-composer">
      <textarea name="text" maxlength="12000" rows="2" placeholder="Напиши Тори…" required ${c.configured?'':'disabled'}></textarea>
      <button class="primary" ${c.configured?'':'disabled'}>Отправить</button>
    </form>
    ${c.configured?'':'<p class="hint">Сначала добавь API-ключ в разделе «Настройки».</p>'}
  </section>`;
}
function learningQueueMarkup(q){
  const labels={pending:'В очереди',running:'Получает ответ',done:'Готово',stale:'Зависла',error:'Ошибка',cancelled:'Отменена',skipped:'Пропущена'};
  const rows=q.queue||[];
  return rows.length?rows.map(x=>{
    const retry=['stale','error','cancelled','skipped'].includes(x.status)?`<button class="mini-action" data-learning-action="retry" data-learning-id="${x.id}">Повторить</button>`:'';
    const cancel=['pending','running','stale','error'].includes(x.status)?`<button class="mini-action" data-learning-action="cancel" data-learning-id="${x.id}">Отменить</button>`:'';
    const skip=['pending','stale','error'].includes(x.status)?`<button class="mini-action" data-learning-action="skip" data-learning-id="${x.id}">Пропустить</button>`:'';
    return `<article class="queue-item"><div><span class="queue-topic">${escapeHtml(x.topic)}</span><p>${escapeHtml(x.question)}</p>${x.last_error?`<small class="queue-error">${escapeHtml(x.last_error)}</small>`:''}<div class="queue-actions">${retry}${skip}${cancel}</div></div><span class="queue-state state-${escapeHtml(x.status)}">${labels[x.status]||escapeHtml(x.status)}</span></article>`;
  }).join(''):'<div class="empty-state">Очередь пуста.</div>';
}
function learningConversation(q){
  const rows=q.messages||[];
  if(!rows.length)return '<div class="chat-empty"><strong>Диалог обучения ещё не начат</strong><span>Добавь тему и вопрос ниже.</span></div>';
  return rows.map(x=>{
    const role=x.role==='tori'?'Тори':x.role==='qwen'?'Qwen':'Система';
    const side=x.role==='tori'?'user':x.role==='qwen'?'assistant':'system';
    const usage=(x.input_tokens||x.output_tokens)?`<span>${x.input_tokens||0} + ${x.output_tokens||0} ток.</span>`:'';
    return `<article class="chat-message ${side}"><div class="chat-author">${role}</div><div class="chat-bubble">${escapeHtml(x.text)}</div><div class="chat-meta">${escapeHtml(fmtDate(x.created_at))}${usage}</div></article>`;
  }).join('');
}
function learningPanel(){
  const q=state.learning||{mode:'stopped',configured:false,queue:[],messages:[]};
  const modeText={running:'Работает',paused:'Пауза',stopped:'Остановлено'}[q.mode]||q.mode;
  const active=(q.queue||[]).filter(x=>['pending','running'].includes(x.status)).length;
  const startDisabled=q.mode==='running'?'disabled':'',pauseDisabled=q.mode==='paused'?'disabled':'',stopDisabled=q.mode==='stopped'?'disabled':'';
  return `<section class="learning-shell">
    <div class="learning-hero"><div><span class="eyebrow">Автоматическое обучение</span><h2>Тори ↔ Qwen</h2><p>Здесь видно весь учебный диалог: что Тори спрашивает и что Qwen отвечает.</p></div><span class="model-pill">Qwen3.6 35B</span></div>
    <div class="learning-statusbar compact-status">
      <div class="status-chip"><i class="status-dot ${q.last_success>0?'is-on':q.configured?'is-warn':'is-off'}"></i><span>AI</span><strong>${q.last_success>0?'Подключён':q.configured?'Ключ сохранён':'Не настроен'}</strong></div>
      <div class="status-chip"><i class="status-dot ${q.mode==='running'?'is-on':q.mode==='paused'?'is-warn':'is-off'}"></i><span>Режим</span><strong id="learning-status">${modeText}</strong></div>
      <div class="status-chip"><i class="status-dot is-idle"></i><span>Очередь</span><strong id="queue-status">${active}</strong></div>
      <div class="status-chip"><i class="status-dot is-on"></i><span>Знания</span><strong id="memory-status">${q.knowledge_count||0}</strong></div>
    </div>
    <div class="learning-controls"><div class="segmented"><button class="action mode-button" data-learning-control="start" ${startDisabled}>▶ Начать</button><button class="action mode-button" data-learning-control="pause" ${pauseDisabled}>Ⅱ Пауза</button><button class="action mode-button" data-learning-control="stop" ${stopDisabled}>■ Стоп</button></div><span class="hint">Зависшие задачи отмечаются автоматически.</span></div>
  </section>
  <section class="conversation-shell learning-chat"><div class="conversation-head"><div><span class="eyebrow">Живой журнал</span><h2>Разговор обучения</h2></div><span class="status-label">${(q.messages||[]).length} сообщений</span></div><div class="chat-stream" id="learning-chat-stream">${learningConversation(q)}</div></section>
  <div class="learning-columns">
    <form id="learning-queue-form" class="panel clean-form"><span class="eyebrow">Новая тема</span><h2>Что изучить</h2><label for="learning-topic">Тема</label><input id="learning-topic" name="topic" maxlength="300" placeholder="Например: сети" required><label for="learning-question">Вопрос</label><textarea id="learning-question" name="question" maxlength="8000" placeholder="Что Тори должна узнать?" required></textarea><button class="primary">Добавить</button></form>
    <section class="panel compact-panel"><div class="section-head"><div><span class="eyebrow">Очередь</span><h2>План обучения</h2></div></div><div id="queue-list">${learningQueueMarkup(q)}</div></section>
  </div>`;
}
function mainPanel(){
  const learning=state.learning||{};
  return `<section class="welcome"><span class="eyebrow">TOORU · DRAGON</span><h2>Добро пожаловать, ${escapeHtml(state.settings.name)}</h2><p>Один центр для общения, памяти, проектов и обучения Тори.</p></section>
  <div class="dashboard-grid">
    <button class="dashboard-card primary-card" data-go="ai" data-open-tab="chat"><span>✦</span><strong>Поговорить с Тори</strong><small>${state.ai_connected?'AI подключён':'Настрой AI Studio'}</small></button>
    <button class="dashboard-card" data-go="ai" data-open-tab="topic"><span>◎</span><strong>Обучение</strong><small>${{running:'Работает',paused:'Пауза',stopped:'Остановлено'}[learning.mode]||'Остановлено'} · ${(learning.queue||[]).filter(x=>['pending','running'].includes(x.status)).length} в очереди</small></button>
    <button class="dashboard-card" data-go="ai" data-open-tab="memory"><span>◈</span><strong>Память и знания</strong><small>${state.counts.memory||0} память · ${state.counts.knowledge||0} знания</small></button>
    <button class="dashboard-card" data-go="work"><span>▣</span><strong>Проекты</strong><small>${(state.counts.work||0)+(state.counts.home||0)} всего</small></button>
  </div>`;
}
function render(){
  document.getElementById('title').textContent=labels[page];
  document.getElementById('crumb').textContent=labels[page];
  document.querySelectorAll('nav button').forEach(b=>b.dataset.page===page?b.setAttribute('aria-current','page'):b.removeAttribute('aria-current'));
  document.getElementById('description').textContent='';
  if(page==='main')content.innerHTML=mainPanel();
  else if(page==='profile'){content.innerHTML=basicSettings(true);content.querySelector('#theme').value=state.settings.theme}
  else if(page==='settings'){content.innerHTML='<div class="settings-grid">'+basicSettings(false)+aiSettings()+'</div>';content.querySelector('#theme').value=state.settings.theme}
  else if(page==='work')content.innerHTML=projectPanel('work','Рабочие проекты');
  else if(page==='home')content.innerHTML=projectPanel('home','Домашние проекты');
  else if(page==='ai'){
    const tabs={chat:'Чат',memory:'Память',knowledge:'Знания',topic:'Обучение'};
    let html='<div class="tabs" role="tablist" aria-label="Разделы Tooru/Ai">'+Object.entries(tabs).map(([key,value])=>`<button class="tab" role="tab" aria-selected="${tab===key}" data-tab="${key}">${value}</button>`).join('')+'</div>';
    if(tab==='chat')html+=chatPanel();
    if(tab==='memory')html+=libraryPanel('memory','Память Тори','Факты и предпочтения, которые ты сохраняешь вручную. Они пока не отправляются модели автоматически.','Что запомнить','Подробности');
    if(tab==='knowledge')html+=libraryPanel('knowledge','Знания','Материалы, полученные во время обучения и добавленные вручную.','Название','Содержание');
    if(tab==='topic')html+=learningPanel();
    content.innerHTML=html;
    if(tab==='chat')scrollChat('chat-stream');
    if(tab==='topic')scrollChat('learning-chat-stream');
  }else if(page==='mobile')content.innerHTML='<section class="panel simple-state"><span class="eyebrow">Позже</span><h2>Мобильное приложение</h2><p>Интерфейс уже адаптивный, но удалённое подключение пока отключено ради безопасности.</p></section>';
  else if(page==='updates')content.innerHTML='<section class="panel simple-state"><span class="eyebrow">Резервная копия</span><h2>Обновления</h2><p>Автообновлятор ещё не подключён. Перед ручным обновлением сохрани базу.</p><button class="primary" data-backup>Создать копию базы</button></section>';
  else if(page==='diagnostics')content.innerHTML='<section class="panel simple-state"><span class="eyebrow">Система</span><h2>Диагностика</h2><p>Проверка базы, Python, SQLite и подключения AI.</p><button class="primary" data-diagnose>Запустить проверку</button><div id="diagnostic-result" aria-live="polite"></div></section>';
}
function scrollChat(id){requestAnimationFrame(()=>{const el=document.getElementById(id);if(el)el.scrollTop=el.scrollHeight})}
function go(target){page=target;message('');render()}
document.querySelector('nav').addEventListener('click',event=>{const b=event.target.closest('[data-page]');if(b&&state)go(b.dataset.page)});
content.addEventListener('click',async event=>{
  const b=event.target.closest('button');if(!b)return;
  if(b.closest('form')&&b.type==='submit')return;
  if(b.dataset.go){if(b.dataset.openTab)tab=b.dataset.openTab;go(b.dataset.go);return}
  if(b.dataset.tab){tab=b.dataset.tab;render();return}
  b.disabled=true;
  try{
    if(b.dataset.delete){if(!confirm('Удалить эту запись?'))return;await api('delete',{id:Number(b.dataset.delete)});await reload();render();message('Удалено.')}
    if(b.dataset.learningControl){await api('learning/control',{action:b.dataset.learningControl});await reload();render();message('Режим обучения обновлён.')}
    if(b.dataset.learningAction){await api('learning/action',{id:Number(b.dataset.learningId),action:b.dataset.learningAction});await reload();render();message('Задача обновлена.')}
    if(b.hasAttribute('data-ai-test')){const result=await api('ai/test',{});await reload();render();message('AI Studio отвечает: '+result.answer)}
    if(b.hasAttribute('data-backup')){const result=await api('backup',{});message('Копия создана: data/backups/'+result.filename)}
    if(b.hasAttribute('data-diagnose')){const d=await api('diagnostics');const pairs=[['База',d.database==='ok'?'OK':d.database],['Python',d.python],['SQLite',d.sqlite],['AI',d.ai],['Модель',d.qwen],['Доступ',d.access]];const el=document.getElementById('diagnostic-result');if(el)el.innerHTML='<dl>'+pairs.map(([a,v])=>`<dt>${escapeHtml(a)}</dt><dd>${escapeHtml(v)}</dd>`).join('')+'</dl>'}
  }catch(error){message(error.message)}finally{b.disabled=false}
});
content.addEventListener('submit',async event=>{
  event.preventDefault();const f=event.target,b=f.querySelector('button[type="submit"],button:not([type])');if(b)b.disabled=true;
  try{
    const values=Object.fromEntries(new FormData(f));
    if(f.id==='record-form')await api('records',{...values,kind:f.dataset.kind});
    if(f.id==='settings-form')await api('settings',values);
    if(f.id==='ai-config-form')await api('ai/config',values);
    if(f.id==='learning-queue-form')await api('learning/queue',values);
    if(f.id==='chat-form'){await api('chat/send',{text:values.text});f.reset()}
    await reload();render();
    message(f.id==='chat-form'?'Тори ответила.':'Сохранено.');
  }catch(error){message(error.message)}finally{if(b)b.disabled=false}
});
async function refreshLearningStatus(){
  if(page!=='ai'||tab!=='topic'||!state)return;
  try{
    const q=await api('learning/status');state.learning=q;
    const learning=document.getElementById('learning-status'),queueStatus=document.getElementById('queue-status'),memory=document.getElementById('memory-status'),queue=document.getElementById('queue-list'),stream=document.getElementById('learning-chat-stream');
    if(learning)learning.textContent={running:'Работает',paused:'Пауза',stopped:'Остановлено'}[q.mode]||q.mode;
    if(queueStatus)queueStatus.textContent=(q.queue||[]).filter(x=>['pending','running'].includes(x.status)).length;
    if(memory)memory.textContent=q.knowledge_count||0;
    if(queue)queue.innerHTML=learningQueueMarkup(q);
    if(stream){const nearBottom=stream.scrollHeight-stream.scrollTop-stream.clientHeight<120;stream.innerHTML=learningConversation(q);if(nearBottom)scrollChat('learning-chat-stream')}
  }catch(_error){}
}
setInterval(refreshLearningStatus,2000);
reload().then(render).catch(error=>{document.getElementById('connection').textContent='Нет связи';message(error.message+' Перезапусти StartTooruDragon.bat и обнови страницу.')});
