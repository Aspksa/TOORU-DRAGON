'use strict';
let config=null;
let qwenTabId=null;
let activeQueueId=null;
let busy=false;

async function restore(){
  const saved=await chrome.storage.local.get(['tooruConfig','activeQueueId']);
  config=saved.tooruConfig||null;
  activeQueueId=saved.activeQueueId||null;
}
restore();

async function bridge(payload){
  if(!config) throw new Error('bridge_not_configured');
  const response=await fetch(`http://127.0.0.1:${config.port}/api/qwen/bridge`,{
    method:'POST',
    headers:{'Content-Type':'application/json','X-Tooru-Bridge':config.token},
    body:JSON.stringify(payload)
  });
  if(!response.ok) throw new Error('bridge_http_'+response.status);
  return response.json();
}

async function tick(){
  if(busy||!config||!qwenTabId||activeQueueId) return;
  busy=true;
  try{
    const state=await bridge({action:'poll'});
    if(!state.item) return;
    const result=await chrome.tabs.sendMessage(qwenTabId,{type:'tooru-send-question',question:state.item.question});
    if(result&&result.ok){
      await bridge({action:'claim',queue_id:state.item.id});
      activeQueueId=state.item.id;
      await chrome.storage.local.set({activeQueueId});
    }
  }catch(_error){}finally{busy=false;}
}

chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  (async()=>{
    if(message.type==='tooru-config'){
      const port=Number(message.port);
      const token=String(message.token||'');
      if(!Number.isInteger(port)||port<1||port>65535||token.length<20) throw new Error('bad_config');
      config={port,token};
      qwenTabId=sender.tab&&sender.tab.id;
      await chrome.storage.local.set({tooruConfig:config});
      await bridge({action:'heartbeat'});
      sendResponse({ok:true});
      return;
    }
    if(message.type==='tooru-tick'){
      qwenTabId=sender.tab&&sender.tab.id||qwenTabId;
      await bridge({action:'heartbeat'});
      await tick();
      sendResponse({ok:true});
      return;
    }
    if(message.type==='tooru-event'){
      const payload={
        action:'event',
        role:message.role,
        text:message.text,
        source_url:message.source_url
      };
      if(message.role==='assistant'&&activeQueueId) payload.queue_id=activeQueueId;
      const result=await bridge(payload);
      if(message.role==='assistant'&&activeQueueId&&result.inserted){
        activeQueueId=null;
        await chrome.storage.local.remove('activeQueueId');
      }
      sendResponse({ok:true});
      return;
    }
    sendResponse({ok:false});
  })().catch(error=>sendResponse({ok:false,error:String(error)}));
  return true;
});
