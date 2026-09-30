'use strict';
const token = document.querySelector('meta[name="tooru-token"]').content;
const content = document.getElementById('content');
let state, page = 'main', tab = 'chat';
const labels = {main:'Главная',profile:'Личный кабинет',ai:'Tooru/Ai',work:'Рабочие проекты',home:'Домашние проекты',mobile:'Мобильное приложение',settings:'Настройки',updates:'Система обновления',diagnostics:'Система диагностики'};
const escapeHtml = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function message(text) { document.getElementById('message').textContent = text; }
async function api(path, data) {
  const response = await fetch('/api/' + path, {method:data === undefined ? 'GET':'POST',headers:{'X-Tooru-Token':token,'Content-Type':'application/json'},body:data === undefined ? undefined:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Ошибка сервера');
  return result;
}
async function reload() {
  state = await api('state');
  document.documentElement.style.colorScheme = state.settings.theme === 'system' ? 'light dark':state.settings.theme;
  document.getElementById('avatar').textContent = state.settings.name.slice(0,1).toUpperCase();
  document.getElementById('connection').textContent = 'Локально';
}
function form(kind, titleLabel, bodyLabel) {
  return `<form id="record-form" data-kind="${kind}" class="panel"><h2>Добавить запись</h2><label for="record-title">${titleLabel}</label><input id="record-title" name="title" maxlength="300" required><label for="record-body">${bodyLabel}</label><textarea id="record-body" name="body" maxlength="20000"></textarea><button class="primary">Сохранить</button></form>`;
}
function records(kind) {
  const rows = state.records[kind];
  return `<section class="panel"><h2>Сохранённые записи · ${state.counts[kind] || 0}</h2>${rows.length ? rows.map(r => `<article class="record"><div class="row"><h3>${escapeHtml(r.title)}</h3><button class="action" data-delete="${r.id}">Удалить</button></div><p>${escapeHtml(r.body)}</p><span class="meta">${escapeHtml(r.source)} · ${escapeHtml(new Date(r.created_at).toLocaleString('ru-RU'))}</span></article>`).join('') : '<div class="empty">Пока нет записей.</div>'}${(state.counts[kind] || 0) > 200 ? '<p class="hint">Показаны последние 200 записей. Все данные сохранены в базе.</p>':''}</section>`;
}
function settingsForm(profile) {
  return `<form id="settings-form" class="panel"><h2>${profile?'Твой профиль':'Параметры интерфейса'}</h2><label for="name">Имя</label><input id="name" name="name" maxlength="80" value="${escapeHtml(state.settings.name)}" required><label for="theme">Оформление</label><select id="theme" name="theme"><option value="system">Как в системе</option><option value="light">Светлое</option><option value="dark">Тёмное</option></select><button class="primary">Сохранить настройки</button></form>`;
}

function learningQueueMarkup(q) {
  const labels={pending:'В очереди',running:'Получает ответ',done:'Готово',stale:'Зависла',error:'Ошибка',cancelled:'Отменена',skipped:'Пропущена'};
  const rows=q.queue||[];
  return rows.length ? rows.map(x=>{
    const retry=['stale','error','cancelled','skipped'].includes(x.status)?`<button class="mini-action" data-learning-action="retry" data-learning-id="${x.id}">Повторить</button>`:'';
    const cancel=['pending','running','stale','error'].includes(x.status)?`<button class="mini-action" data-learning-action="cancel" data-learning-id="${x.id}">Отменить</button>`:'';
    const skip=['pending','stale','error'].includes(x.status)?`<button class="mini-action" data-learning-action="skip" data-learning-id="${x.id}">Пропустить</button>`:'';
    const error=x.last_error?`<small class="queue-error">${escapeHtml(x.last_error)}</small>`:'';
    const usage=(x.input_tokens||x.output_tokens)?`<small class="meta">${x.input_tokens||0} вход · ${x.output_tokens||0} выход</small>`:'';
    return `<article class="queue-item"><div><span class="queue-topic">${escapeHtml(x.topic)}</span><p>${escapeHtml(x.question)}</p>${error}${usage}<div class="queue-actions">${retry}${skip}${cancel}</div></div><span class="queue-state state-${escapeHtml(x.status)}">${labels[x.status]||escapeHtml(x.status)}</span></article>`;
  }).join('') : '<div class="empty-state">Очередь пуста. Добавь первый вопрос для Тори.</div>';
}
function learningJournalMarkup(q) {
  const rows=(q.queue||[]).filter(x=>['done','error','stale'].includes(x.status)).slice(0,6);
  return rows.length ? rows.map(x=>{
    const text=x.status==='done'?(x.response_text||'Ответ сохранён в знания.'):(x.last_error||'Задача требует внимания.');
    return `<article class="journal-item"><span class="journal-role">${x.status==='done'?'Знание':'Система'}</span><div><strong>${escapeHtml(x.topic)}</strong><p>${escapeHtml(text)}</p></div><time>${escapeHtml(new Date(x.updated_at).toLocaleString('ru-RU'))}</time></article>`;
  }).join('') : '<div class="empty-state">Результаты обучения появятся здесь.</div>';
}
function learningSessionMarkup(q) {
  const rows=q.queue||[];
  const active=rows.find(x=>x.status==='running')||rows.find(x=>x.status==='pending')||rows.find(x=>x.status==='stale');
  const done=rows.filter(x=>x.status==='done').length;
  const activeCount=rows.filter(x=>['pending','running'].includes(x.status)).length;
  const title=active ? escapeHtml(active.topic) : 'Нет активной задачи';
  const text=active ? escapeHtml(active.question) : 'Добавь вопрос — Тори обработает его автоматически после запуска обучения.';
  return `<div class="session-main"><span class="eyebrow">Текущая задача</span><h3>${title}</h3><p>${text}</p></div><div class="session-progress"><strong>${activeCount}</strong><span>в работе</span><small>${done} готово</small></div>`;
}
function learningPanel() {
  const q=state.learning||{mode:'stopped',configured:false,queue:[]};
  const modeText={running:'Работает',paused:'Пауза',stopped:'Остановлено'}[q.mode]||q.mode;
  const memoryCount=q.memory_count ?? state.counts.memory ?? 0;
  const knowledgeCount=q.knowledge_count ?? state.counts.knowledge ?? 0;
  const verified=q.last_success>0;
  const apiText=!q.configured?'Нужно настроить':verified?'Связь проверена':'Ключ сохранён';
  const startDisabled=q.mode==='running'?'disabled':'';
  const pauseDisabled=q.mode==='paused'?'disabled':'';
  const stopDisabled=q.mode==='stopped'?'disabled':'';
  return `<section class="learning-shell">
    <div class="learning-hero">
      <div>
        <span class="eyebrow">TOORU · AI STUDIO</span>
        <h2>Обучение Тори</h2>
        <p>Qwen3.6 35B работает в Yandex AI Studio. Никакой локальной модели и автоматизации сайта Qwen.</p>
      </div>
      <span class="model-pill">Qwen3.6 35B</span>
    </div>
    <div class="learning-statusbar">
      <div class="status-chip"><i class="status-dot ${q.configured?(verified?'is-on':'is-warn'):'is-off'}"></i><span>AI Studio</span><strong id="ai-status">${apiText}</strong></div>
      <div class="status-chip"><i class="status-dot ${q.mode==='running'?'is-on':q.mode==='paused'?'is-warn':'is-off'}"></i><span>Обучение</span><strong id="learning-status">${modeText}</strong></div>
      <div class="status-chip"><i class="status-dot is-idle"></i><span>Очередь</span><strong id="queue-status">${(q.queue||[]).filter(x=>['pending','running'].includes(x.status)).length} задач</strong></div>
      <div class="status-chip"><i class="status-dot is-on"></i><span>Память</span><strong id="memory-status">${memoryCount} · ${knowledgeCount}</strong></div>
    </div>
    <div class="learning-controls">
      <div class="segmented" aria-label="Управление обучением">
        <button class="action mode-button" data-learning-control="start" ${startDisabled}>▶ Начать</button>
        <button class="action mode-button" data-learning-control="pause" ${pauseDisabled}>Ⅱ Пауза</button>
        <button class="action mode-button" data-learning-control="stop" ${stopDisabled}>■ Стоп</button>
      </div>
      <span class="hint">Зависшая задача автоматически помечается через ${Math.round((q.stale_seconds||600)/60)} мин.</span>
    </div>
    <div class="session-card" id="session-card">${learningSessionMarkup(q)}</div>
  </section>
  <div class="learning-columns">
    <form id="ai-config-form" class="panel compact-panel">
      <span class="eyebrow">Подключение</span>
      <h2>Yandex AI Studio</h2>
      <label for="ai-folder">Каталог</label>
      <input id="ai-folder" name="folder_id" maxlength="64" value="${escapeHtml(q.folder_id||'')}" required>
      <label for="ai-model">Модель</label>
      <input id="ai-model" name="model" maxlength="120" value="${escapeHtml(q.model||'')}" required>
      <label for="ai-key">API-ключ</label>
      <input id="ai-key" name="api_key" type="password" maxlength="500" autocomplete="off" placeholder="${q.configured?'Ключ уже сохранён локально':'Вставь API-ключ'}">
      <p class="hint">Ключ остаётся только в локальной базе data и не показывается обратно в интерфейсе.</p>
      <div class="row"><button class="primary" type="submit">Сохранить</button><button class="action" type="button" data-ai-test ${q.configured?'':'disabled'}>Проверить связь</button></div>
    </form>
    <form id="learning-queue-form" class="panel compact-panel">
      <span class="eyebrow">Новая задача</span>
      <h2>Что изучить</h2>
      <label for="learning-topic">Тема</label>
      <input id="learning-topic" name="topic" maxlength="300" placeholder="Например: Python" required>
      <label for="learning-question">Вопрос</label>
      <textarea id="learning-question" name="question" maxlength="8000" placeholder="Что Тори должна узнать?" required></textarea>
      <button class="primary">Добавить в очередь</button>
    </form>
  </div>
  <section class="panel compact-panel">
    <div class="section-head"><div><span class="eyebrow">Очередь</span><h2>План обучения</h2></div></div>
    <div id="queue-list">${learningQueueMarkup(q)}</div>
  </section>
  <section class="panel compact-panel">
    <div class="section-head"><div><span class="eyebrow">Результаты</span><h2>Журнал обучения</h2></div><span class="memory-summary" id="memory-summary">${knowledgeCount} знаний</span></div>
    <div class="journal-list" id="journal-list">${learningJournalMarkup(q)}</div>
  </section>`;
}
function render() {
  document.getElementById('title').textContent = labels[page];
  document.getElementById('crumb').textContent = labels[page];
  document.querySelectorAll('nav button').forEach(b => {if(b.dataset.page===page)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
  document.getElementById('description').textContent = page==='main'?'Твои проекты и Тори — в одном месте.':'';
  if(page==='main') content.innerHTML = `<section class="panel"><h2>Добро пожаловать, ${escapeHtml(state.settings.name)}</h2><p>С чего начнём?</p><div class="grid"><button class="action" data-go="ai">Открыть Tooru/Ai →</button><button class="action" data-go="work">Рабочие проекты · ${state.counts.work||0} →</button><button class="action" data-go="home">Домашние проекты · ${state.counts.home||0} →</button><button class="action" data-go="mobile">Мобильное приложение →</button></div></section><p class="hint">Версия 0.0.0 · Данные сохраняются локально. AI Studio: ${state.learning?.configured?'настроена':'нужно подключить'}.</p>`;
  else if(page==='profile'||page==='settings') {content.innerHTML=settingsForm(page==='profile');content.querySelector('#theme').value=state.settings.theme;}
  else if(page==='work'||page==='home') content.innerHTML=form(page,'Название проекта','Описание и задачи')+records(page);
  else if(page==='ai') {
    const tabs={chat:'Чат',memory:'Память',knowledge:'Знания',topic:'Обучение'};
    let html='<div class="tabs" role="tablist" aria-label="Разделы Tooru/Ai">'+Object.entries(tabs).map(([key,value])=>`<button class="tab" role="tab" aria-selected="${tab===key}" data-tab="${key}">${value}</button>`).join('')+'</div>';
    if(tab==='chat') html+='<section class="panel"><h2>Тори ещё не подключена</h2><p>Можно сохранять свои сообщения. Ответы появятся после подключения ИИ-модели.</p><span class="badge">Без генерации ответов</span></section>'+form('chat','Тема сообщения','Твоё сообщение')+records('chat');
    if(tab==='memory') html+='<p class="hint">Здесь ты управляешь сведениями, которые сможешь разрешить Тори использовать.</p>'+form('memory','Что запомнить','Подробности')+records('memory');
    if(tab==='knowledge') html+='<p class="hint">Локальные заметки. Поиск по смыслу и загрузка документов появятся позже.</p>'+form('knowledge','Название материала','Содержание и источник')+records('knowledge');
    if(tab==='topic') html+=learningPanel();
    content.innerHTML=html;
  }
  else if(page==='mobile') content.innerHTML='<section class="panel"><h2>Доступ с телефона</h2><span class="badge">В разработке</span><p>Интерфейс адаптируется к небольшому экрану. Сервер этой версии доступен только на компьютере, где он запущен. Сетевое подключение телефона и синхронизация пока не включены.</p></section>';
  else if(page==='updates') content.innerHTML='<section class="panel"><h2>TOORU · DRAGON 0.0.0</h2><p>Автоматическая установка обновлений и откат пока не подключены. Перед ручной заменой файлов сделай копию базы и сохрани папку data.</p><div class="row"><button class="primary" data-backup>Создать копию базы</button><a href="https://github.com/Aspksa/TOORU-DRAGON" target="_blank" rel="noopener noreferrer">Репозиторий проекта ↗</a></div><p class="hint">Копия базы сохраняется в data/backups. Документы и модели в эту копию не входят.</p></section>';
  else if(page==='diagnostics') content.innerHTML='<section class="panel"><h2>Проверка системы</h2><p>Проверим целостность базы и версии компонентов.</p><button class="primary" data-diagnose>Запустить диагностику</button><div id="diagnostic-result" aria-live="polite"></div></section>';
}
function go(target) {page=target;message('');render();}
document.querySelector('nav').addEventListener('click',event=>{const button=event.target.closest('[data-page]');if(button&&state)go(button.dataset.page);});
content.addEventListener('click',async event=>{
  const b=event.target.closest('button');if(!b)return;
  if(b.closest('form')&&b.type==='submit')return;
  if(b.dataset.go){go(b.dataset.go);return;}
  if(b.dataset.tab){tab=b.dataset.tab;render();return;}
  b.disabled=true;
  try {
    if(b.dataset.delete){if(!confirm('Удалить эту запись из базы?'))return;await api('delete',{id:Number(b.dataset.delete)});await reload();render();message('Запись удалена.');}
    if(b.dataset.learningControl){await api('learning/control',{action:b.dataset.learningControl});await reload();render();message('Режим обучения обновлён.');}
    if(b.dataset.learningAction){await api('learning/action',{id:Number(b.dataset.learningId),action:b.dataset.learningAction});await reload();render();message('Задача обновлена.');}
    if(b.hasAttribute('data-ai-test')){const result=await api('ai/test',{});await reload();render();message('Связь с AI Studio работает: '+result.answer);}
    if(b.hasAttribute('data-backup')){const result=await api('backup',{});message('Копия базы создана: data/backups/'+result.filename);}
    if(b.hasAttribute('data-diagnose')){const d=await api('diagnostics');const pairs=[['База данных',d.database==='ok'?'Проверка пройдена':d.database],['Версия',d.version],['Python',d.python],['SQLite',d.sqlite],['Папка данных',d.data_directory],['ИИ-модель',d.ai],['Qwen',d.qwen],['Доступ',d.access]];const result=document.getElementById('diagnostic-result');if(result)result.innerHTML='<dl>'+pairs.map(([a,v])=>`<dt>${escapeHtml(a)}</dt><dd>${escapeHtml(v)}</dd>`).join('')+'</dl>';}
  }catch(error){message(error.message);}finally{b.disabled=false;}
});
content.addEventListener('submit',async event=>{
  event.preventDefault();const f=event.target;const b=f.querySelector('button');b.disabled=true;
  try{
    const values=Object.fromEntries(new FormData(f));
    if(f.id==='record-form')await api('records',{...values,kind:f.dataset.kind});
    if(f.id==='settings-form')await api('settings',values);
    if(f.id==='ai-config-form')await api('ai/config',values);
    if(f.id==='learning-queue-form')await api('learning/queue',values);
    await reload();render();message('Сохранено.');
  }catch(error){message(error.message);}finally{b.disabled=false;}
});
async function refreshLearningStatus(){
  if(page!=='ai'||tab!=='topic'||!state)return;
  try{
    const q=await api('learning/status');
    state.learning=q;
    const ai=document.getElementById('ai-status');
    const learning=document.getElementById('learning-status');
    const queueStatus=document.getElementById('queue-status');
    const memory=document.getElementById('memory-status');
    const summary=document.getElementById('memory-summary');
    const session=document.getElementById('session-card');
    const queue=document.getElementById('queue-list');
    const journal=document.getElementById('journal-list');
    if(ai)ai.textContent=!q.configured?'Нужно настроить':q.last_success>0?'Связь проверена':'Ключ сохранён';
    if(learning)learning.textContent={running:'Работает',paused:'Пауза',stopped:'Остановлено'}[q.mode]||q.mode;
    if(queueStatus)queueStatus.textContent=`${(q.queue||[]).filter(x=>['pending','running'].includes(x.status)).length} задач`;
    if(memory)memory.textContent=`${q.memory_count||0} · ${q.knowledge_count||0}`;
    if(summary)summary.textContent=`${q.knowledge_count||0} знаний`;
    if(session)session.innerHTML=learningSessionMarkup(q);
    if(queue)queue.innerHTML=learningQueueMarkup(q);
    if(journal)journal.innerHTML=learningJournalMarkup(q);
  }catch(_error){}
}
setInterval(refreshLearningStatus,2000);
reload().then(render).catch(error=>{document.getElementById('connection').textContent='Нет связи';message(error.message+' Перезапусти StartTooruDragon.bat и обнови страницу.');});
