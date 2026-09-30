'use strict';
const seen=new Set();
const stability=new Map();

function configure(){
  const hash=location.hash.startsWith('#')?location.hash.slice(1):'';
  const params=new URLSearchParams(hash);
  const port=params.get('tooru_port');
  const token=params.get('tooru_bridge');
  if(port&&token){
    chrome.runtime.sendMessage({type:'tooru-config',port:Number(port),token});
    try{history.replaceState(null,'',location.pathname+location.search);}catch(_error){}
  }
}

function visible(element){
  const style=getComputedStyle(element);
  const rect=element.getBoundingClientRect();
  return style.display!=='none'&&style.visibility!=='hidden'&&rect.width>0&&rect.height>0;
}

function roleOf(element){
  for(let node=element;node&&node!==document.body;node=node.parentElement){
    const value=((node.getAttribute('data-role')||'')+' '+(node.getAttribute('data-testid')||'')+' '+String(node.className||'')).toLowerCase();
    if(/assistant|bot|answer|response/.test(value)) return 'assistant';
    if(/user|question|human/.test(value)) return 'user';
  }
  return null;
}

function scan(){
  const selectors=[
    '[data-message-id]','[data-testid*="message"]','[class*="message-content"]',
    '[class*="messageContent"]','[class*="chat-message"]','[class*="message-item"]'
  ];
  const nodes=[...document.querySelectorAll(selectors.join(','))];
  for(const node of nodes){
    if(!visible(node)) continue;
    const role=roleOf(node);
    const text=(node.innerText||'').trim();
    if(!role||text.length<1||text.length>50000) continue;
    const key=role+'\0'+text;
    const previous=stability.get(node);
    if(previous&&previous.text===text) previous.count+=1;
    else stability.set(node,{text,count:1});
    const current=stability.get(node);
    if(current.count<2||seen.has(key)) continue;
    seen.add(key);
    chrome.runtime.sendMessage({type:'tooru-event',role,text,source_url:location.href});
  }
}

function setComposerValue(element,text){
  if(element instanceof HTMLTextAreaElement||element instanceof HTMLInputElement){
    const proto=element instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
    const setter=Object.getOwnPropertyDescriptor(proto,'value').set;
    setter.call(element,text);
  }else{
    element.focus();
    element.textContent=text;
  }
  element.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:text}));
  element.dispatchEvent(new Event('change',{bubbles:true}));
}

function sendQuestion(question){
  const inputs=[...document.querySelectorAll('textarea,[contenteditable="true"],[role="textbox"]')]
    .filter(visible);
  const input=inputs[inputs.length-1];
  if(!input) return false;
  setComposerValue(input,question);
  input.focus();
  const scope=input.closest('form')||input.parentElement&&input.parentElement.parentElement||document;
  const buttons=[...scope.querySelectorAll('button')].filter(visible);
  const send=buttons.find(button=>{
    const label=((button.getAttribute('aria-label')||'')+' '+(button.getAttribute('title')||'')+' '+(button.innerText||'')).toLowerCase();
    return /send|发送|отправ/.test(label);
  });
  if(send){send.click();return true;}
  input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',bubbles:true,cancelable:true}));
  input.dispatchEvent(new KeyboardEvent('keyup',{key:'Enter',code:'Enter',bubbles:true,cancelable:true}));
  return true;
}

chrome.runtime.onMessage.addListener((message,_sender,sendResponse)=>{
  if(message.type==='tooru-send-question'){
    try{sendResponse({ok:sendQuestion(String(message.question||''))});}
    catch(error){sendResponse({ok:false,error:String(error)});}
  }
});

configure();
setInterval(()=>{
  scan();
  chrome.runtime.sendMessage({type:'tooru-tick'}).catch(()=>{});
},1500);
