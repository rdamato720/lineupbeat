const KEY='lineupBeatEspnRosterV1';
chrome.runtime.onMessage.addListener((message,_sender,sendResponse)=>{
  if(!message||message.version!==1)return;
  if(message.type==='LB_CAPTURE_ESPN_ROSTER'){
    chrome.storage.local.set({[KEY]:message.payload}).then(()=>sendResponse({ok:true}));return true;
  }
  if(message.type==='LB_GET_ESPN_ROSTER'){
    chrome.storage.local.get(KEY).then(result=>sendResponse({ok:true,payload:result[KEY]||null}));return true;
  }
  if(message.type==='LB_CLEAR_ESPN_ROSTER'){
    chrome.storage.local.remove(KEY).then(()=>sendResponse({ok:true}));return true;
  }
});
