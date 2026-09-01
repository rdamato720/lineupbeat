const KEY='lineupBeatEspnRosterV1';
const ESPN_ORIGIN='https://fantasy.espn.com',ESPN_PATH='/football/';
const MY_TEAM_ORIGIN='https://lineupbeat-dev.pages.dev',MY_TEAM_PATH='/my-team/';
function senderMatches(sender,origin,path){
  try{const url=new URL(sender&&sender.url||'');return url.origin===origin&&url.pathname.startsWith(path)}catch(_error){return false}
}
function reject(sendResponse){sendResponse({ok:false,error:'unexpected_sender'});return false}
chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  if(!message||message.version!==1)return;
  if(message.type==='LB_CAPTURE_ESPN_ROSTER'){
    if(!senderMatches(sender,ESPN_ORIGIN,ESPN_PATH))return reject(sendResponse);
    chrome.storage.local.set({[KEY]:message.payload}).then(()=>sendResponse({ok:true}));return true;
  }
  if(message.type==='LB_GET_ESPN_ROSTER'){
    if(!senderMatches(sender,MY_TEAM_ORIGIN,MY_TEAM_PATH))return reject(sendResponse);
    chrome.storage.local.get(KEY).then(result=>sendResponse({ok:true,payload:result[KEY]||null}));return true;
  }
  if(message.type==='LB_CLEAR_ESPN_ROSTER'){
    if(!senderMatches(sender,MY_TEAM_ORIGIN,MY_TEAM_PATH))return reject(sendResponse);
    chrome.storage.local.remove(KEY).then(()=>sendResponse({ok:true}));return true;
  }
});
