// Mirrors main.c's sector-5 shock log: magic word followed by three float32s.
// This virtual flash belongs only to this browser; no MCU or filesystem access.
const memoryStoreKey='stm32-browser-simulated-flash-v1';
const virtualFlash={record:null,pending:null};
function validFlashRecord(record){
  return record?.simulated===true&&record.magic==='0xDEADBEEF'&&Array.isArray(record.acceleration_g)&&record.acceleration_g.length===3&&record.acceleration_g.every(value=>Number.isFinite(value)&&Math.abs(value)<=4)&&Number.isFinite(record.session_time_s)&&typeof record.captured_at==='string';
}
try{const saved=JSON.parse(localStorage.getItem(memoryStoreKey)||'null');if(validFlashRecord(saved))virtualFlash.record=saved;}catch{}
function flashWords(record){
  if(!record)return ['0xFFFFFFFF','0xFFFFFFFF','0xFFFFFFFF','0xFFFFFFFF'];
  const view=new DataView(new ArrayBuffer(4));
  return ['0xDEADBEEF',...record.acceleration_g.map(value=>{view.setFloat32(0,value,true);return '0x'+view.getUint32(0,true).toString(16).toUpperCase().padStart(8,'0');})];
}
function refreshMemory(){
  const blocked=!state.connected||state.replay||state.paused||faultBusy()||calibrationBusy();
  byId('memory-crash').disabled=blocked||!!virtualFlash.pending||!!virtualFlash.record;
  byId('memory-clear').disabled=!virtualFlash.record||!!virtualFlash.pending;
  const record=virtualFlash.record;
  byId('memory-status').textContent=virtualFlash.pending?'Simulating impact · waiting for the next accepted frame…':record?`Crash saved · ${Math.hypot(...record.acceleration_g).toFixed(2)} g · sector 5. View the dump or clear to re-arm.`:state.replay?'Sector empty · switch to the demo to simulate a crash.':!state.connected?'Sector empty · connect the demo to simulate a crash.':state.paused?'Sector empty · resume telemetry to simulate a crash.':'Sector empty · ready for a simulated crash.';
}
byId('memory-crash').onclick=()=>{
  refreshMemory();
  if(byId('memory-crash').disabled)return;
  const axis=Math.floor(Math.random()*3),a=[0,0,1];a[axis]=(Math.random()<.5?-1:1)*(1.8+Math.random()*.6);
  virtualFlash.pending=a.map(value=>Number(value.toFixed(2)));
  log('WARN','SIMULATED CRASH IMPACT ARMED · VIRTUAL FLASH ONLY');refreshMemory();
};
memoryPrepare=sample=>{
  if(virtualFlash.pending&&calibrationBusy()){
    virtualFlash.pending=null;log('SYS','SIMULATED IMPACT CANCELED · CALIBRATION STARTED');refreshMemory();
  }
  return virtualFlash.pending&&state.connected&&!state.replay?{...sample,a:[...virtualFlash.pending]}:sample;
};
memoryFeed=sample=>{
  if(!virtualFlash.pending)return;
  virtualFlash.record={simulated:true,source:'browser:virtual-flash',sector:5,address:'0x08020000',magic:'0xDEADBEEF',captured_at:new Date().toISOString(),session_time_s:sample.t,acceleration_g:[...sample.a]};
  virtualFlash.pending=null;
  let persisted=true;try{localStorage.setItem(memoryStoreKey,JSON.stringify(virtualFlash.record));}catch{persisted=false;}
  log('WARN',`SIMULATED SHOCK DETECTED · ${Math.hypot(...sample.a).toFixed(2)} G · SECTOR 5 LOG SAVED${persisted?'':' FOR THIS TAB'}`);
  log('SYS',`[SIMULATED FLASH LOG] X:${sample.a[0].toFixed(2)} Y:${sample.a[1].toFixed(2)} Z:${sample.a[2].toFixed(2)} · MAGIC 0xDEADBEEF`);refreshMemory();
};
let viewedMemorySnapshot=null;
byId('memory-export').onclick=()=>{
  viewedMemorySnapshot={simulated:true,description:'Browser virtual flash snapshot. No physical memory was read.',sector:5,address:'0x08020000',exported_at:new Date().toISOString(),empty:!virtualFlash.record,words:flashWords(virtualFlash.record),crash:virtualFlash.record};
  const record=virtualFlash.record;
  byId('memory-dump-summary').textContent=record?`Saved impact · ${Math.hypot(...record.acceleration_g).toFixed(2)} g · session ${record.session_time_s.toFixed(2)} s`:'Sector empty · no crash has been recorded.';
  const lines=viewedMemorySnapshot.words.map((word,i)=>`${'0x'+(0x08020000+i*4).toString(16).toUpperCase().padStart(8,'0')}  ${word}  ${['Magic word','Acceleration X','Acceleration Y','Acceleration Z'][i]}${record&&i?' · '+record.acceleration_g[i-1].toFixed(2)+' g':''}`);
  byId('memory-dump-words').textContent=lines.join('\n');
  byId('memory-dump-json').textContent=JSON.stringify(viewedMemorySnapshot,null,2);
  byId('memory-dump-dialog').showModal();
  log('SYS',`SIMULATED MEMORY SNAPSHOT VIEWED · ${record?'CRASH RECORD':'SECTOR EMPTY'}`);
};
byId('memory-dump-close').onclick=()=>byId('memory-dump-dialog').close();
byId('memory-dump-download').onclick=()=>{
  if(!viewedMemorySnapshot)return;
  const blob=new Blob([JSON.stringify(viewedMemorySnapshot,null,2)+'\n'],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');
  link.href=url;link.download='simulated_flash_sector_5.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  log('SYS','SIMULATED MEMORY SNAPSHOT DOWNLOADED');
};
byId('memory-clear').onclick=()=>{
  if(byId('memory-clear').disabled)return;
  virtualFlash.record=null;virtualFlash.pending=null;
  try{localStorage.removeItem(memoryStoreKey);}catch{}
  log('SYS','SIMULATED FLASH SECTOR 5 CLEARED · WORDS 0xFFFFFFFF · CRASH DETECTION RE-ARMED');refreshMemory();
};
memoryShortcut=key=>byId(key==='d'?'memory-export':'memory-clear').click();
setInterval(()=>{
  if(virtualFlash.pending&&(!state.connected||state.replay)){virtualFlash.pending=null;log('SYS','SIMULATED IMPACT CANCELED · SOURCE CHANGED');}
  refreshMemory();
},100);
refreshMemory();
