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
function render() {
  document.getElementById('title').textContent = labels[page];
  document.getElementById('crumb').textContent = labels[page];
  document.querySelectorAll('nav button').forEach(b => {if(b.dataset.page===page)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
  document.getElementById('description').textContent = page==='main'?'Твои проекты и Тори — в одном месте.':'';
  if(page==='main') content.innerHTML = `<section class="panel"><h2>Добро пожаловать, ${escapeHtml(state.settings.name)}</h2><p>С чего начнём?</p><div class="grid"><button class="action" data-go="ai">Открыть Tooru/Ai →</button><button class="action" data-go="work">Рабочие проекты · ${state.counts.work||0} →</button><button class="action" data-go="home">Домашние проекты · ${state.counts.home||0} →</button><button class="action" data-go="mobile">Мобильное приложение →</button></div></section><p class="hint">Версия 0.0.0 · Проекты, память и настройки сохраняются на этом компьютере. ИИ-модель и Qwen пока не подключены.</p>`;
  else if(page==='profile'||page==='settings') {content.innerHTML=settingsForm(page==='profile');content.querySelector('#theme').value=state.settings.theme;}
  else if(page==='work'||page==='home') content.innerHTML=form(page,'Название проекта','Описание и задачи')+records(page);
  else if(page==='ai') {
    const tabs={chat:'Чат',memory:'Память',knowledge:'Знания',topic:'Обучение'};
    let html='<div class="tabs" role="tablist" aria-label="Разделы Tooru/Ai">'+Object.entries(tabs).map(([key,value])=>`<button class="tab" role="tab" aria-selected="${tab===key}" data-tab="${key}">${value}</button>`).join('')+'</div>';
    if(tab==='chat') html+='<section class="panel"><h2>Тори ещё не подключена</h2><p>Можно сохранять свои сообщения. Ответы появятся после подключения ИИ-модели.</p><span class="badge">Без генерации ответов</span></section>'+form('chat','Тема сообщения','Твоё сообщение')+records('chat');
    if(tab==='memory') html+='<p class="hint">Здесь ты управляешь сведениями, которые сможешь разрешить Тори использовать.</p>'+form('memory','Что запомнить','Подробности')+records('memory');
    if(tab==='knowledge') html+='<p class="hint">Локальные заметки. Поиск по смыслу и загрузка документов появятся позже.</p>'+form('knowledge','Название материала','Содержание и источник')+records('knowledge');
    if(tab==='topic') html+='<section class="panel"><h2>Учитель Qwen</h2><span class="badge">Отдельный браузер Тори</span><p>Открой Qwen и войди в аккаунт самостоятельно. Для Тори используется отдельный профиль выбранного браузера.</p><label for="qwen-browser">Браузер на этом компьютере</label><select id="qwen-browser"><option value="edge">Microsoft Edge</option><option value="chrome">Google Chrome</option></select><button class="primary" data-qwen-open>Открыть браузер Тори</button><p class="hint">Профили Edge и Chrome раздельные и сохраняются в data/browser-profile. Перед переносом проекта закрой все окна браузера Тори.</p><p>Наблюдение за диалогом, автоматические вопросы и обучение ещё не подключены. Сейчас можно общаться с Qwen и сохранять темы здесь.</p></section>'+form('topic','Тема обучения','Что нужно изучить')+records('topic');
    content.innerHTML=html;
  }
  else if(page==='mobile') content.innerHTML='<section class="panel"><h2>Доступ с телефона</h2><span class="badge">В разработке</span><p>Интерфейс адаптируется к небольшому экрану. Сервер этой версии доступен только на компьютере, где он запущен. Сетевое подключение телефона и синхронизация пока не включены.</p></section>';
  else if(page==='updates') content.innerHTML='<section class="panel"><h2>TOORU · DRAGON 0.0.0</h2><p>Автоматическая установка обновлений и откат пока не подключены. Перед ручной заменой файлов сделай копию базы и сохрани папку data.</p><div class="row"><button class="primary" data-backup>Создать копию базы</button><a href="https://github.com/Aspksa/TOORU-DRAGON" target="_blank" rel="noopener noreferrer">Репозиторий проекта ↗</a></div><p class="hint">Копия базы сохраняется в data/backups. Документы, модели и профиль браузера в эту копию не входят.</p></section>';
  else if(page==='diagnostics') content.innerHTML='<section class="panel"><h2>Проверка системы</h2><p>Проверим целостность базы и версии компонентов.</p><button class="primary" data-diagnose>Запустить диагностику</button><div id="diagnostic-result" aria-live="polite"></div></section>';
}
function go(target) {page=target;message('');render();}
document.querySelector('nav').addEventListener('click',event=>{const button=event.target.closest('[data-page]');if(button&&state)go(button.dataset.page);});
content.addEventListener('click',async event=>{
  const b=event.target.closest('button');if(!b)return;
  if(b.closest('form'))return;
  if(b.dataset.go){go(b.dataset.go);return;}
  if(b.dataset.tab){tab=b.dataset.tab;render();return;}
  b.disabled=true;
  try {
    if(b.dataset.delete){if(!confirm('Удалить эту запись из базы?'))return;await api('delete',{id:Number(b.dataset.delete)});await reload();render();message('Запись удалена.');}
    if(b.hasAttribute('data-qwen-open')){const result=await api('qwen/open',{browser:document.getElementById('qwen-browser').value});message(result.message);}
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
    await reload();render();message('Сохранено.');
  }catch(error){message(error.message);}finally{b.disabled=false;}
});
reload().then(render).catch(error=>{document.getElementById('connection').textContent='Нет связи';message(error.message+' Перезапусти StartTooruDragon.bat и обнови страницу.');});
