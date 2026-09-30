'use strict';
const token=document.querySelector('meta[name="tooru-token"]').content;
const content=document.getElementById('content');
let state,page='main',tab='chat';
const labels={main:'Главная',profile:'Личный кабинет',ai:'Tooru/Ai',work:'Рабочие проекты',home:'Домашние проекты',mobile:'Мобильное приложение',settings:'Настройки',updates:'Система обновления',diagnostics:'Система диагностики'};
const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtDate=value=>{try{return new Date(value).toLocaleString('ru-RU')}catch(_e){return ''}};
function message(text){document.getElementById('message').textContent=text||''}
function formatBytes(value){
  const bytes=Number(value)||0;
  if(!bytes)return '—';
  if(bytes<1024)return bytes+' Б';
  if(bytes<1048576)return (bytes/1024).toFixed(1)+' КБ';
  return (bytes/1048576).toFixed(2)+' МБ';
}
function updateStatusMarkup(u){
  const stateText=!u.tracked?'Локальная сборка ещё не синхронизирована':u.update_available?'Есть обновление':'Установлена последняя ревизия';
  const installed=u.installed_revision?u.installed_revision.slice(0,12):'—';
  const description=u.latest_description||'Описание не указано.';
  let files='';
  if(!u.files_available)files='<p class="hint">Список файлов появится после первой синхронизации локальной сборки.</p>';
  else if(!(u.files||[]).length)files='<p class="hint">Изменённых файлов нет.</p>';
  else files='<div class="update-files"><h3>Файлы обновления</h3><ul>'+u.files.map(file=>'<li><strong>'+escapeHtml(file.status_label||file.status)+'</strong> · <code>'+escapeHtml(file.path)+'</code>'+(file.will_update?'':' <span class="meta">не заменяется обновлятором</span>')+'</li>').join('')+'</ul></div>';
  const last='<dl><dt>Версия</dt><dd>'+escapeHtml(u.version)+'</dd><dt>Установленная ревизия</dt><dd>'+escapeHtml(installed)+'</dd><dt>Ревизия на GitHub</dt><dd>'+escapeHtml(u.latest_revision.slice(0,12))+'</dd><dt>Состояние</dt><dd>'+escapeHtml(stateText)+'</dd><dt>Установлено</dt><dd>'+escapeHtml(u.installed_at?fmtDate(u.installed_at):'—')+'</dd><dt>Скачано в последний раз</dt><dd>'+escapeHtml(formatBytes(u.last_download_bytes))+'</dd><dt>Последняя системная копия</dt><dd>'+escapeHtml(u.last_backup||'—')+'</dd><dt>Название обновления</dt><dd>'+escapeHtml(u.latest_message||'—')+'</dd><dt>Описание</dt><dd>'+escapeHtml(description)+'</dd></dl>';
  const history=(u.history||[]).length?'<div class="update-files"><h3>История обновлений</h3><ul>'+u.history.map(item=>'<li><strong>'+escapeHtml((item.revision||'').slice(0,12))+'</strong> · '+escapeHtml(item.installed_at?fmtDate(item.installed_at):'')+' · '+escapeHtml(formatBytes(item.download_bytes))+'<br><span>'+escapeHtml(item.title||'Без названия')+'</span></li>').join('')+'</ul></div>':'<p class="hint">История появится после следующего обновления.</p>';
  return last+files+history;
}
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
function profilePanel(){
  return `<div class="library-layout">
    <form id="profile-form" class="panel clean-form">
      <span class="eyebrow">Профиль</span><h2>Личный кабинет</h2>
      <label for="name">Как к тебе обращаться</label>
      <input id="name" name="name" maxlength="80" value="${escapeHtml(state.settings.name)}" required>
      <button class="primary">Сохранить имя</button>
    </form>
    <section class="panel library-intro"><span class="eyebrow">Сводка</span><h2>Твоя TOORU</h2>
      <p>Локальная память, проекты и обучение хранятся на этом устройстве.</p>
      <div class="profile-stats"><span><strong>${state.counts.memory||0}</strong> память</span><span><strong>${state.counts.knowledge||0}</strong> знания</span><span><strong>${(state.counts.work||0)+(state.counts.home||0)}</strong> проекты</span></div>
    </section>
  </div>`;
}
function appearanceSettings(){
  return `<form id="appearance-form" class="panel clean-form">
    <span class="eyebrow">Интерфейс</span><h2>Оформление</h2>
    <label for="theme">Тема</label>
    <select id="theme" name="theme"><option value="system">Как в системе</option><option value="light">Светлая</option><option value="dark">Тёмная</option></select>
    <button class="primary">Сохранить тему</button>
  </form>`;
}
function aiSettings(){
  const q=state.learning||{};
  return `<section class="panel settings-card">
    <div class="section-head"><div><span class="eyebrow">Модель</span><h2>Yandex AI Studio</h2></div><span class="status-label ${q.last_success>0?'ok':''}">${!q.configured?'Не настроено':q.last_success>0?'Подключено':'Авторизация не проверена'}</span></div>
    <form id="ai-config-form" class="clean-form">
      <label for="ai-folder">Каталог Yandex Cloud</label>
      <input id="ai-folder" name="folder_id" maxlength="64" value="${escapeHtml(q.folder_id||'')}" required>
      <label for="ai-model">Модель</label>
      <input id="ai-model" name="model" maxlength="120" value="${escapeHtml(q.model||'')}" required>
      <label for="ai-auth">Тип авторизации</label>
      <select id="ai-auth" name="auth_type"><option value="api_key">API-ключ сервисного аккаунта</option><option value="iam_token">IAM-токен пользователя</option></select>
      <label for="ai-key">Ключ / токен</label>
      <input id="ai-key" name="api_key" type="password" maxlength="500" autocomplete="off" placeholder="${q.configured?'Секрет сохранён — оставь пустым, чтобы не менять':'Вставь API-ключ или IAM-токен'}">
      <div class="budget-fields">
        <label>Вход, ₽ / 1000 токенов<input name="input_rub_per_1k" type="number" min="0" max="1000" step="0.01" value="${q.usage?.input_rub_per_1k??0.2}"></label>
        <label>Выход, ₽ / 1000 токенов<input name="output_rub_per_1k" type="number" min="0" max="1000" step="0.01" value="${q.usage?.output_rub_per_1k??0.3}"></label>
        <label>Лимит в месяц, ₽<input name="monthly_budget_rub" type="number" min="0" max="10000000" step="1" value="${q.usage?.monthly_budget_rub??1000}"></label>
      </div>
      <div class="form-actions"><button class="primary" type="submit">Сохранить</button><button class="action" type="button" data-ai-test ${q.configured?'':'disabled'}>Проверить связь</button></div>
      <p class="hint">0 ₽ в поле лимита отключает блокировку. Текущий тариф Qwen3.6 35B: 0,2 ₽ вход / 0,3 ₽ выход за 1000 токенов.</p>
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
    <form id="record-form-${kind}" data-kind="${kind}" class="panel clean-form">
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
    const role=x.role==='user'?'Ты':x.role==='tori'?'Дракончик Тоору':'Система';
    const side=x.role==='user'?'user':x.role==='tori'?'assistant':'system';
    const usage=(x.input_tokens||x.output_tokens)?`<span>${x.input_tokens||0} + ${x.output_tokens||0} ток.</span>`:'';
    const context=(x.context||[]).length?`<div class="context-used"><span>Использовано:</span>${x.context.map(item=>`<span class="context-chip">${item.kind==='memory'?'Память':'Знание'} · ${escapeHtml(item.title)}</span>`).join('')}</div>`:'';
    return `<article class="chat-message ${side}"><div class="chat-author">${role}</div><div class="chat-bubble">${escapeHtml(x.text)}</div>${context}<div class="chat-meta">${escapeHtml(fmtDate(x.created_at))}${usage}</div></article>`;
  }).join('');
}
function usageStrip(usage,scope='all'){
  if(!usage)return '';
  const limit=usage.monthly_budget_rub||0;
  const total=usage.month||{};
  const scoped=scope==='learning'?(usage.learning_month||{}):scope==='chat'?(usage.chat_month||{}):total;
  const spent=Number(scoped.cost_rub||0);
  const totalSpent=Number(total.cost_rub||0);
  const pct=limit>0?Math.min(100,totalSpent/limit*100):0;
  const label=scope==='learning'?'Обучение':scope==='chat'?'Чат':'24 часа';
  const first=scope==='all'?(usage.day||{}):scoped;
  return `<div class="usage-strip ${usage.blocked?'is-blocked':''}">
    <div><span>${label}</span><strong>${Number(first.cost_rub||0).toFixed(2)} ₽</strong></div>
    <div><span>${scope==='all'?'Месяц':'Всего AI за месяц'}</span><strong>${(scope==='all'?spent:totalSpent).toFixed(2)} ₽${limit>0?' / '+limit.toFixed(0)+' ₽':''}</strong></div>
    <div><span>Токены ${scope==='all'?'':'раздела'}</span><strong>${Number(scoped.input_tokens||0)+Number(scoped.output_tokens||0)}</strong></div>
    ${limit>0?`<div class="usage-progress"><i style="width:${pct}%"></i></div>`:''}
  </div>`;
}
function brainSuggestionsMarkup(items){
  const rows=items||[];
  if(!rows.length)return '';
  const names={memory:'Память',knowledge:'Знание',learning:'Доучиться'};
  return `<section class="brain-panel">
    <div class="section-head"><div><span class="eyebrow">Мозг Тори</span><h2>Предложения</h2></div><span class="status-label">${rows.length}</span></div>
    <div class="brain-list">${rows.map(x=>{
      const main=x.kind==='learning'?(x.topic||'Тема'):(x.title||'Предложение');
      const body=x.kind==='learning'?(x.question||''):(x.body||'');
      const confidence=Math.round((Number(x.confidence)||0)*100);
      return `<article class="brain-card">
        <div class="brain-card-head"><span class="brain-kind">${names[x.kind]||escapeHtml(x.kind)}</span><span class="meta">${confidence}%</span></div>
        <h3>${escapeHtml(main)}</h3>
        <p>${escapeHtml(body)}</p>
        ${x.reason?`<small>${escapeHtml(x.reason)}</small>`:''}
        <div class="brain-actions"><button class="primary" data-brain-action="accept" data-brain-id="${x.id}">Принять</button><button class="action" data-brain-action="reject" data-brain-id="${x.id}">Отклонить</button></div>
      </article>`;
    }).join('')}</div>
  </section>`;
}
function brainGoalsMarkup(goals){
  const rows=goals||[];
  return `<section class="brain-panel">
    <div class="section-head"><div><span class="eyebrow">Цели Тори</span><h2>Планирование</h2></div><span class="status-label">${rows.length} активных</span></div>
    <form id="goal-form" class="goal-form">
      <input name="title" maxlength="300" placeholder="Цель, например: улучшить TOORU" required>
      <textarea name="description" maxlength="8000" rows="2" placeholder="Что важно учесть?"></textarea>
      <button class="primary">Составить план</button>
    </form>
    <div class="goal-list">${rows.map(goal=>`<article class="goal-card">
      <div class="brain-card-head"><div><span class="brain-kind">Цель</span><h3>${escapeHtml(goal.title)}</h3></div><button class="mini-action" data-goal-action="done" data-goal-id="${goal.id}">Завершить</button></div>
      ${goal.description?`<p>${escapeHtml(goal.description)}</p>`:''}
      ${goal.progress?.summary?`<small>${escapeHtml(goal.progress.summary)}</small>`:''}
      <div class="goal-steps">${(goal.plan||[]).map((step,index)=>{
        const done=step.status==='done';
        const queued=step.status==='queued';
        const action=step.type==='learning'&&!queued&&!done?`<button class="mini-action" data-goal-action="queue_learning" data-goal-id="${goal.id}" data-step="${index}">В обучение</button>`:(!done&&!queued?`<button class="mini-action" data-goal-action="complete_step" data-goal-id="${goal.id}" data-step="${index}">Готово</button>`:'');
        return `<div class="goal-step ${done?'is-done':''}"><span>${index+1}</span><div><strong>${escapeHtml(step.title)}</strong>${step.reason?`<small>${escapeHtml(step.reason)}</small>`:''}</div><em>${queued?'В очереди':done?'Готово':step.type==='learning'?'Нужно изучить':'Действие'}</em>${action}</div>`;
      }).join('')}</div>
    </article>`).join('')}</div>
  </section>`;
}
function chatPanel(){
  const c=state.chat||{configured:false,messages:[]};
  const blocked=!!c.usage?.blocked;
  return `<section class="conversation-shell">
    <div class="conversation-head"><div><span class="eyebrow">Дракончик Тоору</span><h2>Чат</h2></div><span class="status-label ${c.last_success>0?'ok':''}">${!c.configured?'Настрой AI Studio':c.last_success>0?'Онлайн':'Готова'}</span></div>
    ${usageStrip(c.usage,'all')}
    <div class="chat-stream" id="chat-stream">${chatMessages(c)}</div>
    <form id="chat-form" class="chat-composer">
      <textarea name="text" maxlength="12000" rows="2" placeholder="${blocked?'Месячный лимит AI достигнут':'Напиши Тори…'}" required ${c.configured&&!blocked?'':'disabled'}></textarea>
      <div class="composer-side">
        <label class="context-toggle"><input type="checkbox" name="use_context" value="1" checked> Память и знания</label>
        <label class="context-toggle"><input type="checkbox" name="analyze" value="1" checked> Мозг: предложения</label>
        <button class="primary" ${c.configured&&!blocked?'':'disabled'}>Отправить</button>
      </div>
    </form>
    ${!c.configured?'<p class="hint">Сначала добавь API-ключ в разделе «Настройки».</p>':blocked?'<p class="hint warning">Месячный лимит достигнут. Измени бюджет в Настройках.</p>':''}
  </section>`;
}
function reasoningItemsMarkup(r){
  const rows=r.items||[];
  if(!rows.length)return '<div class="empty-state">Разборов пока нет.</div>';
  return rows.map(item=>{
    const x=item.result||{};
    const list=(title,values)=>Array.isArray(values)&&values.length?'<div class="review-box"><strong>'+escapeHtml(title)+'</strong><ul>'+values.map(v=>'<li>'+escapeHtml(v)+'</li>').join('')+'</ul></div>':'';
    const options=Array.isArray(x.options)&&x.options.length?'<div class="goal-steps">'+x.options.map((o,index)=>'<div class="goal-step"><span>'+String(index+1)+'</span><div><strong>'+escapeHtml(o.title||'Вариант')+'</strong>'+(o.pros?.length?'<small>Плюсы: '+escapeHtml(o.pros.join(' · '))+'</small>':'')+(o.cons?.length?'<small>Минусы: '+escapeHtml(o.cons.join(' · '))+'</small>':'')+'</div></div>').join('')+'</div>':'';
    const sources=(item.context||[]).length?'<small>Контекст: '+item.context.map(s=>escapeHtml(s.title)).join(' · ')+'</small>':'';
    return '<article class="goal-card"><div class="brain-card-head"><div><span class="brain-kind">Логический разбор</span><h3>'+escapeHtml(item.problem)+'</h3></div><span class="status-label">'+Math.round((Number(x.confidence)||0)*100)+'%</span></div>'+
      (x.summary?'<p>'+escapeHtml(x.summary)+'</p>':'')+
      list('Факты',x.facts)+list('Допущения',x.assumptions)+options+list('Противоречия',x.contradictions)+
      list('Слабые места',x.critique?.weaknesses)+list('Не хватает данных',x.critique?.missing_evidence)+
      list('Что стоит изучить',(x.learning_gaps||[]).map(g=>g.topic+' — '+g.question))+
      (x.decision?'<div class="review-box"><strong>Решение после проверки</strong><p>'+escapeHtml(x.decision)+'</p></div>':'')+
      (x.next_step?'<p><strong>Следующий шаг:</strong> '+escapeHtml(x.next_step)+'</p>':'')+
      sources+'<small>'+escapeHtml(fmtDate(item.created_at))+' · '+String(item.input_tokens||0)+' + '+String(item.output_tokens||0)+' ток.</small></article>';
  }).join('');
}
function reasoningPanel(){
  const r=state.reasoning||{configured:false,items:[],usage:{}};
  const blocked=!!r.usage?.blocked;
  return '<section class="learning-shell"><div class="learning-hero"><div><span class="eyebrow">Разум Тори · v2</span><h2>Логика, критик и решения</h2><p>Сначала строит разбор, затем независимый критик ищет слабые места, недостающие данные и уточняет итоговый вывод.</p></div><span class="model-pill">Qwen3.6 35B</span></div>'+
    usageStrip(r.usage,'all')+
    '<form id="reasoning-form" class="panel clean-form"><label for="reasoning-problem">Задача или решение</label><textarea id="reasoning-problem" name="problem" maxlength="12000" rows="5" placeholder="Например: какой следующий модуль TOORU делать и почему?" required '+(r.configured&&!blocked?'':'disabled')+'></textarea>'+
    '<label class="context-toggle"><input type="checkbox" name="use_context" value="1" checked> Использовать релевантную Память и Знания</label>'+
    '<button class="primary" '+(r.configured&&!blocked?'':'disabled')+'>Разобрать логически</button></form>'+
    (!r.configured?'<p class="hint">Сначала настрой AI Studio.</p>':blocked?'<p class="hint warning">Месячный лимит AI достигнут.</p>':'')+
    '</section><section class="panel compact-panel"><div class="section-head"><div><span class="eyebrow">История</span><h2>Разборы Тори</h2></div></div>'+reasoningItemsMarkup(r)+'</section>';
}
function learningQueueMarkup(q){
  const labels={pending:'В очереди',running:'Получает ответ',done:'Готово',stale:'Зависла',error:'Ошибка',cancelled:'Отменена',skipped:'Пропущена'};
  const rows=q.queue||[];
  return rows.length?rows.map(x=>{
    const retry=['stale','error','cancelled','skipped'].includes(x.status)?`<button class="mini-action" data-learning-action="retry" data-learning-id="${x.id}">Повторить</button>`:'';
    const cancel=['pending','running','stale','error'].includes(x.status)?`<button class="mini-action" data-learning-action="cancel" data-learning-id="${x.id}">Отменить</button>`:'';
    const skip=['pending','stale','error'].includes(x.status)?`<button class="mini-action" data-learning-action="skip" data-learning-id="${x.id}">Пропустить</button>`:'';
    const review=x.review&&Object.keys(x.review).length?`<div class="review-box"><strong>Самопроверка: ${escapeHtml({good:'хорошо',partial:'частично',uncertain:'неуверенно'}[x.review.verdict]||x.review.verdict||'')}</strong><span>${Math.round((Number(x.review.confidence)||0)*100)}%</span>${x.review.summary?`<p>${escapeHtml(x.review.summary)}</p>`:''}</div>`:'';
    return `<article class="queue-item"><div><span class="queue-topic">${escapeHtml(x.topic)}</span><p>${escapeHtml(x.question)}</p>${review}${x.last_error?`<small class="queue-error">${escapeHtml(x.last_error)}</small>`:''}<div class="queue-actions">${retry}${skip}${cancel}</div></div><span class="queue-state state-${escapeHtml(x.status)}">${labels[x.status]||escapeHtml(x.status)}</span></article>`;
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
      <div class="status-chip"><i class="status-dot ${q.last_success>0?'is-on':q.configured?'is-warn':'is-off'}"></i><span>AI</span><strong>${q.last_success>0?'Подключён':q.configured?'Авторизация не проверена':'Не настроен'}</strong></div>
      <div class="status-chip"><i class="status-dot ${q.mode==='running'?'is-on':q.mode==='paused'?'is-warn':'is-off'}"></i><span>Режим</span><strong id="learning-status">${modeText}</strong></div>
      <div class="status-chip"><i class="status-dot is-idle"></i><span>Очередь</span><strong id="queue-status">${active}</strong></div>
      <div class="status-chip"><i class="status-dot is-on"></i><span>Знания</span><strong id="memory-status">${q.knowledge_count||0}</strong></div>
    </div>
    <div id="learning-usage">${usageStrip(q.usage,'learning')}</div>
    <div class="learning-controls"><div class="segmented"><button class="action mode-button" data-learning-control="start" ${startDisabled}>▶ Начать</button><button class="action mode-button" data-learning-control="pause" ${pauseDisabled}>Ⅱ Пауза</button><button class="action mode-button" data-learning-control="stop" ${stopDisabled}>■ Стоп</button></div><span class="hint">Зависшие задачи отмечаются автоматически.</span></div>
    <form id="brain-automation-form" class="panel clean-form">
      <span class="eyebrow">Мозг Тори · v2</span><h3>Самообучение через Qwen</h3>
      <label class="context-toggle"><input type="checkbox" name="enabled" value="1" ${q.automation?.enabled?'checked':''}> Автоматически продолжать полезные учебные цепочки</label>
      <div class="budget-fields">
        <label>Минимальная уверенность<input name="min_confidence" type="number" min="0.50" max="0.95" step="0.05" value="${q.automation?.min_confidence??0.75}"></label>
        <label>Лимит задач в день<input name="daily_limit" type="number" min="1" max="20" step="1" value="${q.automation?.daily_limit??5}"></label>
        <label>Глубина цепочки по теме<input name="chain_limit" type="number" min="1" max="6" step="1" value="${q.automation?.chain_limit??3}"></label>
      </div>
      <button class="primary" type="submit">Сохранить самообучение</button>
      <p class="hint">Сегодня автоматически добавлено: ${q.automation?.today_count||0} из ${q.automation?.daily_limit||5}. Сначала Тори берёт учебные шаги активных целей, затем — сильные пробелы из самопроверки и Разума. Одна тема ограничена глубиной ${q.automation?.chain_limit||3}. Память и личные факты остаются под твоим подтверждением.</p>
    </form>
  </section>
  <section class="conversation-shell learning-chat"><div class="conversation-head"><div><span class="eyebrow">Живой журнал</span><h2>Разговор обучения</h2></div><span class="status-label">${(q.messages||[]).length} сообщений</span></div><div class="chat-stream" id="learning-chat-stream">${learningConversation(q)}</div></section>
  <div class="learning-columns">
    <form id="learning-queue-form" class="panel clean-form"><span class="eyebrow">Новая тема</span><h2>Что изучить</h2><label for="learning-topic">Тема</label><input id="learning-topic" name="topic" maxlength="300" placeholder="Например: сети" required><label for="learning-question">Вопрос</label><textarea id="learning-question" name="question" maxlength="8000" placeholder="Что Тори должна узнать?" required></textarea><button class="primary">Добавить</button></form>
    <section class="panel compact-panel"><div class="section-head"><div><span class="eyebrow">Очередь</span><h2>План обучения</h2></div></div><div id="queue-list">${learningQueueMarkup(q)}</div></section>
  </div>${brainSuggestionsMarkup(q.suggestions)}`;
}
function brainCenterPanel(){
  const q=state.learning||{mode:'stopped',configured:false,queue:[],messages:[],automation:{}};
  const r=state.reasoning||{configured:false,items:[],usage:{}};
  const goals=state.chat?.goals||[];
  const active=(q.queue||[]).filter(x=>['pending','running'].includes(x.status)).length;
  const auto=!!q.automation?.enabled;
  return `<section class="learning-shell">
    <div class="learning-hero"><div><span class="eyebrow">Мозг Тори</span><h2>Разум + цели + самообучение</h2><p>Один цикл: понять цель → найти нехватку знаний → изучить → проверить → обновить план.</p></div><span class="model-pill">Qwen3.6 35B</span></div>
    <div class="learning-statusbar compact-status">
      <div class="status-chip"><i class="status-dot ${auto?'is-on':'is-off'}"></i><span>Авторазвитие</span><strong>${auto?'Включено':'Выключено'}</strong></div>
      <div class="status-chip"><i class="status-dot ${q.last_success>0?'is-on':'is-warn'}"></i><span>AI</span><strong>${q.last_success>0?'Подключён':'Готовность не проверена'}</strong></div>
      <div class="status-chip"><i class="status-dot is-idle"></i><span>В работе</span><strong>${active}</strong></div>
      <div class="status-chip"><i class="status-dot is-on"></i><span>Цели</span><strong>${goals.length}</strong></div>
    </div>
    ${usageStrip(q.usage,'all')}
    <form id="brain-automation-form" class="panel clean-form">
      <label class="context-toggle"><input type="checkbox" name="enabled" value="1" ${auto?'checked':''}> <strong>Авторазвитие Тори</strong> — самой продолжать обучение и переоценивать цели</label>
      <details><summary>Дополнительно</summary><div class="budget-fields">
        <label>Уверенность<input name="min_confidence" type="number" min="0.50" max="0.95" step="0.05" value="${q.automation?.min_confidence??0.75}"></label>
        <label>Задач в день<input name="daily_limit" type="number" min="1" max="20" value="${q.automation?.daily_limit??5}"></label>
        <label>Глубина темы<input name="chain_limit" type="number" min="1" max="6" value="${q.automation?.chain_limit??3}"></label>
      </div></details>
      <button class="primary">Сохранить</button>
    </form>
  </section>
  ${brainGoalsMarkup(goals)}
  <section class="panel clean-form"><span class="eyebrow">Разум v2</span><h2>Разобрать задачу</h2>
    <form id="reasoning-form"><textarea name="problem" maxlength="12000" rows="3" placeholder="Что Тори должна обдумать?" required></textarea>
    <label class="context-toggle"><input type="checkbox" name="use_context" value="1" checked> Память и знания</label><button class="primary">Обдумать</button></form>
  </section>
  <details class="panel"><summary><strong>Последние логические разборы</strong></summary>${reasoningItemsMarkup(r)}</details>
  <details class="panel"><summary><strong>Обучение и очередь</strong> · ${active} активных</summary>
    <form id="learning-queue-form" class="clean-form"><input name="topic" maxlength="300" placeholder="Тема" required><textarea name="question" maxlength="8000" placeholder="Вопрос для Qwen" required></textarea><button class="action">Добавить вручную</button></form>
    <div id="queue-list">${learningQueueMarkup(q)}</div>
  </details>
  <details class="panel"><summary><strong>Предложения Мозга</strong> · ${(q.suggestions||[]).length}</summary>${brainSuggestionsMarkup(q.suggestions)}</details>`;
}
function dataPanel(){
  return '<div class="learning-columns">'+
    '<div>'+libraryPanel('memory','Память','Личные факты и предпочтения.','Что запомнить','Подробности')+'</div>'+
    '<div>'+libraryPanel('knowledge','Знания','Материалы Тори и результаты обучения.','Название','Содержание')+'</div>'+
  '</div>';
}
function dragonSettings(){
  const d=state.dragon||{name:'Дракончик Тоору',permissions:{},actions:[]};
  const p=d.permissions||{};
  const toggle=(name,label,checked)=>'<label class="context-toggle"><input type="checkbox" name="'+name+'" value="1" '+(checked?'checked':'')+'> '+label+'</label>';
  return '<section class="panel clean-form"><span class="eyebrow">Дракончик Тоору</span><h2>Права помощника</h2><p>Доступ к рабочему проекту и действиям TOORU. Все изменения проекта записываются в журнал.</p>'+
    '<form id="dragon-permissions-form">'+
      toggle('project_read','Читать весь рабочий проект',p.project_read)+
      toggle('project_write','Изменять файлы проекта с резервной копией',p.project_write)+
      toggle('data_manage','Работать с данными TOORU',p.data_manage)+
      toggle('brain_auto','Автоматически развивать Мозг',p.brain_auto)+
      toggle('update_check','Проверять обновления',p.update_check)+
      toggle('notifications','Показывать всплывающие сообщения',p.notifications)+
      toggle('delete_files','Удалять файлы проекта',p.delete_files)+
      '<button class="primary" type="submit">Сохранить права</button>'+
    '</form>'+
    '<details><summary><strong>Журнал действий</strong></summary><div class="dragon-action-list">'+
      (d.actions||[]).map(a=>'<div class="queue-item"><div><strong>'+escapeHtml(a.summary)+'</strong><small>'+escapeHtml(a.target||'')+'</small></div><span class="meta">'+escapeHtml(fmtDate(a.created_at))+'</span></div>').join('')+
    '</div></details></section>';
}
function ensureDragonToastHost(){
  let host=document.getElementById('dragon-toast-host');
  if(!host){host=document.createElement('div');host.id='dragon-toast-host';host.className='dragon-toast-host';document.body.appendChild(host)}
  return host;
}
function showDragonToast(note){
  const host=ensureDragonToastHost();
  if(host.querySelector('[data-dragon-note="'+note.id+'"]'))return;
  const el=document.createElement('div');
  el.className='dragon-toast level-'+escapeHtml(note.level||'info');
  el.dataset.dragonNote=String(note.id);
  el.innerHTML='<div><strong>'+escapeHtml(note.title)+'</strong>'+(note.body?'<p>'+escapeHtml(note.body)+'</p>':'')+'</div><button aria-label="Закрыть">×</button>';
  el.querySelector('button').addEventListener('click',async()=>{try{await api('dragon/notifications/read',{ids:[note.id]})}catch(_e){}el.remove()});
  host.appendChild(el);
  setTimeout(()=>{if(el.isConnected)el.remove()},12000);
}
async function refreshDragonNotifications(){
  if(!state)return;
  try{
    const d=await api('dragon/status');
    state.dragon=d;
    (d.notifications||[]).filter(n=>!n.is_read).slice(0,3).forEach(showDragonToast);
  }catch(_error){}
}
function mainPanel(){
  const learning=state.learning||{};
  return `<section class="welcome"><span class="eyebrow">TOORU · DRAGON</span><h2>Добро пожаловать, ${escapeHtml(state.settings.name)}</h2><p>Один центр для общения, памяти, проектов и обучения Тори.</p></section>
  <div class="dashboard-grid">
    <button class="dashboard-card primary-card" data-go="ai" data-open-tab="chat"><span>✦</span><strong>Поговорить с Тори</strong><small>${state.ai_connected?'AI подключён':'Настрой AI Studio'}</small></button>
    <button class="dashboard-card" data-go="ai" data-open-tab="brain"><span>◎</span><strong>Мозг Тори</strong><small>${{running:'Работает',paused:'Пауза',stopped:'Остановлено'}[learning.mode]||'Остановлено'} · ${(learning.queue||[]).filter(x=>['pending','running'].includes(x.status)).length} в очереди</small></button>
    <button class="dashboard-card" data-go="ai" data-open-tab="data"><span>◈</span><strong>Память и знания</strong><small>${state.counts.memory||0} память · ${state.counts.knowledge||0} знания</small></button>
    <button class="dashboard-card" data-go="work"><span>▣</span><strong>Проекты</strong><small>${(state.counts.work||0)+(state.counts.home||0)} всего</small></button>
  </div>`;
}
function render(){
  document.getElementById('title').textContent=labels[page];
  document.getElementById('crumb').textContent=labels[page];
  document.querySelectorAll('nav button').forEach(b=>b.dataset.page===page?b.setAttribute('aria-current','page'):b.removeAttribute('aria-current'));
  document.getElementById('description').textContent='';
  if(page==='main')content.innerHTML=mainPanel();
  else if(page==='profile'){content.innerHTML=profilePanel()}
  else if(page==='settings'){content.innerHTML='<div class="settings-grid">'+appearanceSettings()+aiSettings()+dragonSettings()+'</div>';content.querySelector('#theme').value=state.settings.theme;const auth=content.querySelector('#ai-auth');if(auth)auth.value=state.learning?.auth_type||'api_key'}
  else if(page==='work')content.innerHTML=projectPanel('work','Рабочие проекты');
  else if(page==='home')content.innerHTML=projectPanel('home','Домашние проекты');
  else if(page==='ai'){
    const tabs={chat:'Чат',brain:'Мозг',data:'Данные'};
    if(!tabs[tab])tab='chat';
    let html='<div class="tabs" role="tablist" aria-label="Разделы Tooru/Ai">'+Object.entries(tabs).map(([key,value])=>`<button class="tab" role="tab" aria-selected="${tab===key}" data-tab="${key}">${value}</button>`).join('')+'</div>';
    if(tab==='chat')html+=chatPanel();
    if(tab==='brain')html+=brainCenterPanel();
    if(tab==='data')html+=dataPanel();
    content.innerHTML=html;
    if(tab==='chat')scrollChat('chat-stream');
  }else if(page==='mobile')content.innerHTML='<section class="panel simple-state"><span class="eyebrow">Позже</span><h2>Мобильное приложение</h2><p>Интерфейс уже адаптивный, но удалённое подключение пока отключено ради безопасности.</p></section>';
  else if(page==='updates')content.innerHTML='<section class="panel simple-state"><span class="eyebrow">GitHub → локально</span><h2>Система обновления</h2><p>Проверяет ветку main, сохраняет данные и переносимый Python, создаёт резервные копии и перезапускает TOORU после установки.</p><div class="form-actions"><button class="action" data-update-check>Проверить обновление</button><button class="primary" data-update-start>Обновить и перезапустить</button><button class="action" data-backup>Создать копию базы</button></div><div id="update-result" aria-live="polite"></div></section>';
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
    if(b.dataset.brainAction){await api('brain/action',{id:Number(b.dataset.brainId),action:b.dataset.brainAction});await reload();render();message(b.dataset.brainAction==='accept'?'Предложение применено.':'Предложение отклонено.')}
    if(b.dataset.goalAction){await api('brain/goal/action',{id:Number(b.dataset.goalId),action:b.dataset.goalAction,step:b.dataset.step===undefined?null:Number(b.dataset.step)});await reload();render();message('Цель обновлена.')}
    if(b.hasAttribute('data-ai-test')){const result=await api('ai/test',{});await reload();render();message('AI Studio отвечает: '+result.answer)}
    if(b.hasAttribute('data-update-check')){const u=await api('update/status');const el=document.getElementById('update-result');if(el)el.innerHTML=updateStatusMarkup(u)}
    if(b.hasAttribute('data-update-start')){if(!confirm('Обновить TOORU из GitHub и перезапустить программу?'))return;const u=await api('update/start',{});if(u.already_current){message('Уже установлена последняя ревизия GitHub.')}else{const el=document.getElementById('update-result');if(el)el.textContent='Обновление запущено. TOORU сейчас перезапустится.';message('Создана копия базы: data/backups/'+u.database_backup)}}
    if(b.hasAttribute('data-backup')){const result=await api('backup',{});message('Копия создана: data/backups/'+result.filename)}
    if(b.hasAttribute('data-diagnose')){const d=await api('diagnostics');const pairs=[['База',d.database==='ok'?'OK':d.database],['Python',d.python],['SQLite',d.sqlite],['AI',d.ai],['Модель',d.qwen],['Доступ',d.access]];const el=document.getElementById('diagnostic-result');if(el)el.innerHTML='<dl>'+pairs.map(([a,v])=>`<dt>${escapeHtml(a)}</dt><dd>${escapeHtml(v)}</dd>`).join('')+'</dl>'}
  }catch(error){message(error.message)}finally{b.disabled=false}
});
content.addEventListener('submit',async event=>{
  event.preventDefault();const f=event.target,b=f.querySelector('button[type="submit"],button:not([type])');if(b)b.disabled=true;
  try{
    const values=Object.fromEntries(new FormData(f));
    if(f.id.startsWith('record-form'))await api('records',{...values,kind:f.dataset.kind});
    if(f.id==='profile-form')await api('settings',{name:values.name,theme:state.settings.theme});
    if(f.id==='appearance-form')await api('settings',{name:state.settings.name,theme:values.theme});
    if(f.id==='ai-config-form')await api('ai/config',values);
    if(f.id==='dragon-permissions-form')await api('dragon/permissions',{
      project_read:values.project_read==='1',
      project_write:values.project_write==='1',
      data_manage:values.data_manage==='1',
      brain_auto:values.brain_auto==='1',
      update_check:values.update_check==='1',
      notifications:values.notifications==='1',
      delete_files:values.delete_files==='1'
    });
    if(f.id==='learning-queue-form')await api('learning/queue',values);
    if(f.id==='brain-automation-form')await api('brain/automation',{enabled:values.enabled==='1',min_confidence:Number(values.min_confidence),daily_limit:Number(values.daily_limit),chain_limit:Number(values.chain_limit)});
    if(f.id==='goal-form')await api('brain/goal',values);
    if(f.id==='reasoning-form')await api('brain/reason',{problem:values.problem,use_context:values.use_context==='1'});
    if(f.id==='chat-form'){await api('chat/send',{text:values.text,use_context:values.use_context==='1',analyze:values.analyze==='1'});f.reset();for(const name of ['use_context','analyze']){const toggle=f.querySelector('[name="'+name+'"]');if(toggle)toggle.checked=true}}
    await reload();render();
    message(f.id==='chat-form'?'Тори ответила.':f.id==='reasoning-form'?'Логический разбор готов.':'Сохранено.');
  }catch(error){message(error.message)}finally{if(b)b.disabled=false}
});
async function refreshLearningStatus(){
  if(page!=='ai'||tab!=='brain'||!state)return;
  try{
    const activeElement=document.activeElement;
    const userEditing=activeElement&&['INPUT','TEXTAREA','SELECT'].includes(activeElement.tagName);
    const q=await api('learning/status');
    state.learning=q;
    const queueStatus=document.getElementById('queue-status'),queue=document.getElementById('queue-list');
    if(queueStatus)queueStatus.textContent=(q.queue||[]).filter(x=>['pending','running'].includes(x.status)).length;
    if(queue)queue.innerHTML=learningQueueMarkup(q);
    if(!userEditing){
      const fresh=await api('state');
      state=fresh;
      render();
    }
  }catch(_error){}
}
setInterval(refreshLearningStatus,3000);
setInterval(refreshDragonNotifications,4000);
reload().then(()=>{render();refreshDragonNotifications()}).catch(error=>{document.getElementById('connection').textContent='Нет связи';message(error.message+' Перезапусти StartTooruDragon.bat и обнови страницу.')});
