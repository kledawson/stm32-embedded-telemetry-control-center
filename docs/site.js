// Lightweight browser port. Equations follow sources.py, kinematics.py,
// and motion_events.py. The desktop Python app remains the reference.
const byId = id => document.getElementById(id);
const radians = angle => angle * Math.PI / 180;
const degrees = angle => angle * 180 / Math.PI;
const wrap = angle => ((angle + 180) % 360 + 360) % 360 - 180;
const state = {
  connected:false, replay:false, paused:false, interval:30, visualStart:0, model:'aircraft', start:0, lastAt:0, samples:[], frameTimes:[], renderTimes:[],
  pitch:0, roll:0, yaw:0, linear:[0,0,0], magnitude:0, peak:0, rotation:0,
  motion:'Waiting for data', candidate:'Stationary', candidateSince:0,
  motionHistory:[], previous:null, latestEvent:null, events:[], sequence:0,
  impactActive:false, impactUntil:-Infinity, cooldownUntil:-Infinity, quietSince:null,
  logs:[], lastSummary:-Infinity, sampleTimer:null, renderTimer:null
};
let faultBeforeReceive=()=>true,faultRefresh=()=>{},faultReset=()=>{},faultBusy=()=>false;
let calibrationBusy=()=>false,calibrationPrepare=s=>s,calibrationFeed=()=>{},calibrationCorrect=s=>s,calibrationCancel=()=>{},calibrationMetadata=()=>({});
let memoryPrepare=s=>s,memoryFeed=()=>{},memoryShortcut=()=>{};
const metricValues = [...document.querySelectorAll('.metric strong')];
const eventValues = [...document.querySelectorAll('.event-row>span:last-child')];
const healthValues = [...document.querySelectorAll('.session-health .health-value')];
const plotColors = ['#00e5ff','#e040fb','#ffea00'];
// Camera is independent of the attitude estimator, as in SteadyGLViewWidget.
const camera = {azimuth:45,elevation:30,zoom:1,panX:0,panY:0,drag:null};
const terminalState={timestamps:true,categories:new Set(['SYS','TEL','EVT','WARN']),rate:0,health:true,follow:true};
const terminalIntervals=[1,.5,.25,.1,.03];
const terminalLabels=['1000 ms','500 ms','250 ms','100 ms','30 ms'];
const rateButtons = [...document.querySelectorAll('[data-rate]')];

function updateStreamControls(){
  rateButtons.forEach(button=>{
    button.disabled=!state.connected||state.replay||faultBusy()||calibrationBusy();
    const selected=Number(button.dataset.rate)===state.interval;
    button.classList.toggle('checked',selected);
    button.setAttribute('aria-pressed',String(selected));
  });
  syncRecordingControls();syncReplayControls();
  byId('pause').disabled=!state.connected||faultBusy()||calibrationBusy();
  byId('pause').textContent=`${state.paused?'Resume':'Pause'} telemetry  [P]`;
  byId('pause').setAttribute('aria-pressed',String(state.paused));
  const status=document.querySelector('.connection-status');
  status.textContent=!state.connected?'OFFLINE':state.replay?(state.paused?'REPLAY · PAUSED':'REPLAY'):state.paused?'DEMO · PAUSED':'DEMO · CONNECTED';
  status.style.color=!state.connected?'#a9bbcb':state.paused?'#ebc66d':'#86d8a6';
  document.querySelectorAll('[data-focus-rate]').forEach(button=>{
    const selected=Number(button.dataset.focusRate)===state.interval;
    button.disabled=!state.connected||state.replay||faultBusy()||calibrationBusy();button.classList.toggle('checked',selected);
    button.setAttribute('aria-pressed',String(selected));
  });
  document.querySelectorAll('[data-focus-stream]').forEach(row=>row.hidden=state.replay);
  document.querySelectorAll('[data-focus-replay]').forEach(row=>row.hidden=!state.replay);
}
function setStreamRate(interval){
  if(!state.connected||state.replay||faultBusy()||calibrationBusy())return;
  state.interval=interval;
  state.frameTimes=[];
  clearInterval(state.sampleTimer);
  state.sampleTimer=setInterval(receive,interval);
  log('SYS',`DEMO STREAM INTERVAL · ${interval} MS`);
  updateStreamControls();
}
function togglePause(){
  if(!state.connected||faultBusy()||calibrationBusy())return;
  if(state.replay){toggleReplay();return;}
  state.paused=!state.paused;
  // The desktop worker keeps its clock ticking while paused. Do not integrate
  // a stale gyro measurement across the entire pause when the stream resumes.
  if(!state.paused){state.lastAt=performance.now()/1000;state.frameTimes=[];}
  log('SYS',`DEMO TELEMETRY ${state.paused?'PAUSED':'RESUMED'}`);
  updateStreamControls();updateReadings();
}
rateButtons.forEach(button=>button.onclick=()=>setStreamRate(Number(button.dataset.rate)));
byId('pause').onclick=togglePause;
document.addEventListener('keydown',event=>{
  if(event.repeat||event.ctrlKey||event.metaKey||event.altKey)return;
  if(event.target.matches('input,select,textarea,[contenteditable]'))return;
  const key=event.key.toLowerCase(),rates={v:30,f:500,n:1000,s:2000};
  if(key==='r'){event.preventDefault();resetAttitude();return;}
  if(!state.connected)return;
  if(key in rates){event.preventDefault();setStreamRate(rates[key]);}
  else if(key==='p'){event.preventDefault();togglePause();}
  else if(key==='d'||key==='c'){event.preventDefault();memoryShortcut(key);}
});
function resetAttitude(){
  state.pitch=state.roll=state.yaw=0;
  state.linear=[0,0,0];
  log('SYS','ORIENTATION ZEROED');
  drawAircraft();updateReadings();
}
byId('reset-attitude').onclick=resetAttitude;
byId('model').onchange=()=>{
  if(byId('model').value==='load-stl'){
    syncModelPickers();byId('stl-file').click();return;
  }
  state.model=byId('model').value;
  syncModelPickers();
  drawAircraft();
};

// DemoWorker.sample_at, with the same jitter ranges and wire precision.
function demoSample(t) {
  const pitch = radians(15 * Math.sin(.60 * t));
  const roll = radians(20 * Math.sin(.45 * t));
  const pulse = Math.floor(t) % 6 < 2 ? .28 * Math.sin(2.4 * t) : 0;
  const accel = [-Math.sin(roll)*Math.cos(pitch)+pulse,
    Math.sin(pitch)+.12*Math.cos(1.8*t), Math.cos(roll)*Math.cos(pitch)];
  const gyro = [9*Math.cos(.60*t),9*Math.cos(.45*t),18];
  const jitter = range => (Math.random()*2-1)*range;
  return {t, a:accel.map(v=>Number((v+jitter(.018)).toFixed(2))),
    g:gyro.map(v=>Number((v+jitter(.25)).toFixed(1)))};
}

// AttitudeEstimator.update: same deadbands, dt limit, gravity trust, and blend.
function estimate(sample, dt) {
  dt = Math.min(Math.max(dt,.001),.25);
  const [ax,ay,az] = sample.a;
  const [gx,gy,gz] = sample.g.map(v=>Math.abs(v)>1.2?v:0);
  const accelPitch = degrees(Math.atan2(ay,Math.hypot(ax,az)));
  const accelRoll = degrees(Math.atan2(-ax,az));
  const trust = Math.max(0,1-Math.abs(Math.hypot(ax,ay,az)-1)/.18);
  const weight = (1-Math.pow(.94,dt/.03))*trust;
  state.pitch = (1-weight)*(state.pitch+gx*dt)+weight*accelPitch;
  state.roll = (1-weight)*(state.roll+gy*dt)+weight*accelRoll;
  state.yaw = wrap(state.yaw+gz*dt);
  const p=radians(state.pitch),r=radians(state.roll);
  const gravity=[-Math.sin(r)*Math.cos(p),Math.sin(p),Math.cos(r)*Math.cos(p)];
  state.linear=sample.a.map((v,i)=>Math.abs(v-gravity[i])>.05?v-gravity[i]:0);
}

// MotionEvents.update: same windows, thresholds, hysteresis, and debounce.
function detectMotion(sample, report=true) {
  const now=sample.t, axes=sample.a;
  state.magnitude=Math.hypot(...axes); state.rotation=Math.hypot(...sample.g);
  if(state.motionHistory.length && now-state.motionHistory.at(-1)[0]>.3){
    state.motionHistory=[];state.previous=null;
  }
  const delta=axes.map((v,i)=>Math.abs(v-(state.previous||axes)[i]));
  const axis=Math.max(...delta)>.05?'XYZ'[delta.indexOf(Math.max(...delta))]:'—';
  state.previous=axes;state.motionHistory.push([now,state.magnitude]);
  while(state.motionHistory.length>80||now-state.motionHistory[0][0]>.8)state.motionHistory.shift();
  let emitted=null;
  if(state.impactActive){
    if(state.magnitude>state.latestEvent.peak)state.latestEvent={...state.latestEvent,peak:state.magnitude,axis:axis==='—'?state.latestEvent.axis:axis};
    if(state.magnitude<1.5){
      if(state.quietSince===null)state.quietSince=now;
      if(now-state.quietSince>=.15){state.impactActive=false;state.cooldownUntil=now+.5;}
    }else state.quietSince=null;
  }else if(state.magnitude>=2 && now>=state.cooldownUntil){
    state.impactActive=true;state.impactUntil=now+.7;state.quietSince=null;
    state.latestEvent=emitted={kind:'Impact',t:now,peak:state.magnitude,axis};
  }
  if(state.impactActive||now<state.impactUntil)state.motion='Impact';
  else {
    const values=state.motionHistory.map(v=>v[1]),slopes=[];
    for(let i=1;i<values.length;i++)if(Math.abs(values[i]-values[i-1])>.025)slopes.push(values[i]-values[i-1]);
    let turns=0;for(let i=1;i<slopes.length;i++)if(slopes[i]*slopes[i-1]<0)turns++;
    const shaking=values.length>=8&&Math.max(...values)-Math.min(...values)>.25&&turns>=3;
    const candidate=shaking?'Shaking':state.rotation>(state.motion==='Rotating'?8:12)?'Rotating':Math.abs(state.magnitude-1)>.12?'Active motion':'Stationary';
    if(candidate!==state.candidate){state.candidate=candidate;state.candidateSince=now;}
    if(now-state.candidateSince>=.18&&candidate!==state.motion){
      state.motion=candidate;
      if(['Shaking','Rotating'].includes(candidate))state.latestEvent=emitted={kind:candidate,t:now,peak:Math.max(...values),axis:candidate==='Shaking'?axis:'—'};
    }
  }
  if(emitted){state.events.unshift({...emitted,time:new Date().toLocaleTimeString()});state.events.length=Math.min(3,state.events.length);if(report)log('EVT',`${emitted.kind.toUpperCase()} · ${emitted.peak.toFixed(2)} G MEASURED PEAK`);}
  return emitted;
}
function log(category,message){
  const entry={time:new Date().toLocaleTimeString(),category,message};
  state.logs.push(entry);
  if(state.logs.length>1000)state.logs.shift();
  if(terminalState.categories.has(category)){
    const pre=document.querySelector('.terminal-body pre');
    pre.append(terminalLine(entry));
    while(pre.children.length>1000)pre.firstElementChild.remove();
    if(terminalState.follow)pre.scrollTop=pre.scrollHeight;
  }
}
function terminalLine(entry){
  const line=document.createElement('span');line.className=`terminal-line category-${entry.category}`;
  if(terminalState.timestamps){
    const stamp=document.createElement('span');stamp.className='terminal-timestamp';stamp.textContent=entry.time+'  ';line.append(stamp);
  }
  const prefix=document.createElement('span');prefix.className=`terminal-category category-${entry.category}`;
  prefix.textContent=`${entry.category}  `;
  line.append(prefix,document.createTextNode(entry.message));return line;
}
function renderTerminal(){
  const pre=document.querySelector('.terminal-body pre');
  const scroll=pre.scrollTop;
  pre.replaceChildren();
  const fragment=document.createDocumentFragment();
  for(const entry of state.logs)if(terminalState.categories.has(entry.category))fragment.append(terminalLine(entry));
  pre.append(fragment);pre.scrollTop=terminalState.follow?pre.scrollHeight:scroll;
}
function syncTerminalControls(){
  document.querySelectorAll('[data-terminal-category]').forEach(button=>{
    const checked=terminalState.categories.has(button.dataset.terminalCategory);
    button.classList.toggle('checked',checked);button.setAttribute('aria-pressed',String(checked));
  });
  document.querySelectorAll('[data-timestamps],#timestamps').forEach(button=>{
    button.classList.toggle('checked',terminalState.timestamps);button.setAttribute('aria-pressed',String(terminalState.timestamps));
  });
  byId('terminal-rate').value=terminalState.rate;byId('terminal-rate-label').textContent=terminalLabels[terminalState.rate];
  const picker=document.querySelector('[data-terminal-rate-picker]');if(picker)picker.value=terminalState.rate;
  byId('session-health').hidden=!terminalState.health;
  byId('health-toggle').setAttribute('aria-expanded',String(terminalState.health));
  byId('health-toggle').textContent=`Session Health ${terminalState.health?'▾':'▸'}`;
  const healthButton=document.querySelector('[data-terminal-health]');
  if(healthButton){healthButton.classList.toggle('checked',terminalState.health);healthButton.setAttribute('aria-pressed',String(terminalState.health));}
  byId('follow-live').hidden=terminalState.follow;
  const followButton=document.querySelector('[data-terminal-follow]');if(followButton)followButton.disabled=terminalState.follow;
  if(document.querySelector('#focus-controls .terminal-overlay-body'))requestAnimationFrame(placeChartOverlay);
}
function toggleTerminalCategory(category){
  if(terminalState.categories.has(category))terminalState.categories.delete(category);else terminalState.categories.add(category);
  renderTerminal();syncTerminalControls();
}
function toggleTimestamps(){terminalState.timestamps=!terminalState.timestamps;renderTerminal();syncTerminalControls();}
function toggleTerminalHealth(){terminalState.health=!terminalState.health;syncTerminalControls();}
function setTerminalRate(index){terminalState.rate=Number(index);syncTerminalControls();}
function followTerminal(){terminalState.follow=true;const pre=document.querySelector('.terminal-body pre');pre.scrollTop=pre.scrollHeight;syncTerminalControls();}
document.querySelectorAll('[data-terminal-category]').forEach(button=>button.onclick=()=>toggleTerminalCategory(button.dataset.terminalCategory));
byId('timestamps').onclick=toggleTimestamps;
byId('terminal-rate').oninput=event=>setTerminalRate(event.target.value);
byId('follow-live').onclick=followTerminal;
document.querySelector('.terminal-body pre').addEventListener('scroll',event=>{
  const pre=event.target;terminalState.follow=pre.scrollHeight-pre.clientHeight-pre.scrollTop<8;syncTerminalControls();
});
// Session schema follows session_io.py (format version 1).
const sessionColumns=['timestamp_s','received_dt_s','ax_g','ay_g','az_g','gx_dps','gy_dps','gz_dps','pitch_deg','roll_deg','yaw_deg','linear_ax_g','linear_ay_g','linear_az_g','motion_state','event_kind','event_peak_g','event_axis'];
const recording={active:false,rows:[],firstTime:null,metadata:null,basename:'',saved:null};
function syncRecordingControls(){
  byId('record').disabled=!state.connected||state.replay||((faultBusy()||calibrationBusy())&&!recording.active);
  byId('record').textContent=recording.active?'Stop & Save':'Start recording';
  byId('record').setAttribute('aria-pressed',String(recording.active));
  byId('record-status').hidden=!recording.active;
  if(recording.active){
    const duration=recording.rows.at(-1)?.[0]||0;
    byId('record-status').textContent=`● Recording · ${recording.rows.length} samples · ${duration.toFixed(1)} s`;
  }
  byId('record-downloads').hidden=!recording.saved;
  byId('open-replay').disabled=recording.active||calibrationBusy();
}
function startRecording(){
  if(!state.connected||state.replay||recording.active||faultBusy()||calibrationBusy())return;
  const now=performance.now()/1000,created=new Date().toISOString();
  Object.assign(recording,{active:true,rows:[],firstTime:null,basename:`telemetry_session_${created.replace(/[:.]/g,'-')}`,metadata:{
    format_version:1,created_at_utc:created,application_version:'telemetry-console-browser-prototype',
    source:'demo',simulated:true,serial_port:null,requested_interval_ms:state.interval,
    firmware_version:'unknown',calibration_id:'not-calibrated',calibration_device_key:null,
    processing_baseline:'attitude and motion-event state reset at recording start',
    recording_policy:'simulated raw samples; derived attitude and events included',...calibrationMetadata()
  }});
  // Reset derived state while keeping the demo generator's clock continuous.
  Object.assign(state,{visualStart:now,lastAt:now,samples:[],frameTimes:[],pitch:0,roll:0,yaw:0,linear:[0,0,0],magnitude:0,peak:0,rotation:0,
    motion:'Waiting for data',candidate:'Stationary',candidateSince:0,motionHistory:[],previous:null,latestEvent:null,events:[],
    impactActive:false,impactUntil:-Infinity,cooldownUntil:-Infinity,quietSince:null,lastSummary:-Infinity});
  log('SYS','RECORDING STARTED · SIMULATED DATA · SESSION BASELINE RESET');
  syncRecordingControls();redraw();updateReadings();
}
function captureSample(sample,dt,event){
  if(!recording.active)return;
  if(recording.firstTime===null)recording.firstTime=sample.t;
  recording.rows.push([sample.t-recording.firstTime,dt,...sample.a,...sample.g,
    state.pitch,state.roll,state.yaw,...state.linear,state.motion,event?.kind||'',event?.peak??'',event?.axis||'']);
  syncRecordingControls();
  // Keep browser memory bounded; the complete captured session remains downloadable.
  if(recording.rows.length>=20000)stopRecording('sample_limit');
}
function stopRecording(reason='user_stopped'){
  if(!recording.active)return;
  recording.active=false;
  if(recording.rows.length){
    recording.saved={basename:recording.basename,rows:recording.rows,metadata:{...recording.metadata,
      csv_file:recording.basename+'.csv',sample_count:recording.rows.length,
      duration_s:recording.rows.at(-1)[0],closed_at_utc:new Date().toISOString(),source_end_reason:reason}};
    log('SYS',`RECORDING READY · ${recording.rows.length} SAMPLES · DOWNLOAD SESSION ZIP`);
    showRecordingDownloads();
  }else log('WARN','RECORDING EMPTY · NO SAMPLES CAPTURED');
  syncRecordingControls();
}
function showRecordingDownloads(){
  if(!recording.saved)return;
  const m=recording.saved.metadata;
  byId('export-summary').textContent=`${m.sample_count} simulated samples · ${m.duration_s.toFixed(1)} seconds`;
  byId('export-bundle').textContent='Download session · CSV + JSON';
  if(!byId('export-dialog').open)byId('export-dialog').showModal();
}
// A small, dependency-free ZIP writer: two UTF-8 files, stored without compression.
function crc32(bytes){
  let crc=0xffffffff;
  for(const byte of bytes){crc^=byte;for(let bit=0;bit<8;bit++)crc=(crc>>>1)^((crc&1)?0xedb88320:0);}
  return (crc^0xffffffff)>>>0;
}
function sessionZip(files){
  const encoder=new TextEncoder(),parts=[],directory=[];let offset=0,directorySize=0;
  for(const file of files){
    const name=encoder.encode(file.name),data=encoder.encode(file.text),crc=crc32(data);
    const local=new Uint8Array(30+name.length),lv=new DataView(local.buffer);
    lv.setUint32(0,0x04034b50,true);lv.setUint16(4,20,true);lv.setUint16(6,0x800,true);
    lv.setUint16(12,33,true);lv.setUint32(14,crc,true);lv.setUint32(18,data.length,true);lv.setUint32(22,data.length,true);lv.setUint16(26,name.length,true);local.set(name,30);
    const central=new Uint8Array(46+name.length),cv=new DataView(central.buffer);
    cv.setUint32(0,0x02014b50,true);cv.setUint16(4,20,true);cv.setUint16(6,20,true);cv.setUint16(8,0x800,true);
    cv.setUint16(14,33,true);cv.setUint32(16,crc,true);cv.setUint32(20,data.length,true);cv.setUint32(24,data.length,true);cv.setUint16(28,name.length,true);cv.setUint32(42,offset,true);central.set(name,46);
    parts.push(local,data);directory.push(central);offset+=local.length+data.length;directorySize+=central.length;
  }
  const end=new Uint8Array(22),ev=new DataView(end.buffer);
  ev.setUint32(0,0x06054b50,true);ev.setUint16(8,files.length,true);ev.setUint16(10,files.length,true);ev.setUint32(12,directorySize,true);ev.setUint32(16,offset,true);
  return new Blob([...parts,...directory,end],{type:'application/zip'});
}
function downloadRecording(){
  const saved=recording.saved;if(!saved)return;
  const cell=value=>{const text=String(value);return /[",\r\n]/.test(text)?'"'+text.replace(/"/g,'""')+'"':text;};
  const csv=[sessionColumns,...saved.rows].map(row=>row.map(cell).join(',')).join('\r\n')+'\r\n';
  const blob=sessionZip([{name:saved.basename+'.csv',text:csv},{name:saved.basename+'.json',text:JSON.stringify(saved.metadata,null,2)+'\n'}]);
  const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=saved.basename+'.zip';
  document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);
  byId('export-bundle').textContent='Download session again · CSV + JSON';
}
byId('record').onclick=()=>recording.active?stopRecording():startRecording();
byId('record-downloads').onclick=showRecordingDownloads;
byId('export-close').onclick=()=>byId('export-dialog').close();
byId('export-bundle').onclick=downloadRecording;
// Replay uses raw samples and the same estimator/event pipeline as the desktop.
const replay={loaded:null,index:-1,position:0,speed:1,anchor:0,wall:0,timer:null};
const replaySpeeds=[.25,.5,1,2,4];
function syncReplayControls(){
  const available=!!replay.loaded&&(!state.connected||state.replay)&&!calibrationBusy()&&!faultBusy();
  byId('replay-play').disabled=!available;
  byId('replay-play').textContent=state.replay&&!state.paused?'Pause ‖':'Play ▶';
  for(const id of ['replay-restart','replay-position','replay-speed','replay-step'])byId(id).disabled=!available;
  byId('open-replay').disabled=recording.active||calibrationBusy();
  byId('replay-speed-label').textContent=`${replay.speed}×`;
  if(replay.loaded){
    byId('replay-progress').textContent=`Replay ${replay.position.toFixed(2)} / ${replay.loaded.duration.toFixed(2)} s · ${replay.speed}×`;
    byId('replay-position').value=replay.loaded.duration?Math.round(replay.position/replay.loaded.duration*1000):0;
  }
  document.querySelectorAll('[data-replay-action]').forEach(button=>{
    button.disabled=!available;
    if(button.dataset.replayAction==='play')button.textContent=byId('replay-play').textContent;
  });
  document.querySelectorAll('[data-replay-seek]').forEach(slider=>{
    slider.disabled=!available;
    if(!slider.dataset.seeking)slider.value=byId('replay-position').value;
  });
  document.querySelectorAll('[data-replay-speed-picker]').forEach(picker=>{picker.disabled=!available;picker.value=replay.speed;});
  document.querySelectorAll('[data-replay-progress]').forEach(label=>{
    const slider=label.closest('[data-focus-replay]').querySelector('[data-replay-seek]');
    const position=slider.dataset.seeking&&replay.loaded?replay.loaded.duration*Number(slider.value)/1000:replay.position;
    label.textContent=`${position.toFixed(2)} s`;
  });
  document.querySelectorAll('[data-replay-duration]').forEach(label=>label.textContent=`${(replay.loaded?.duration||0).toFixed(2)} s`);
  document.querySelectorAll('[data-replay-state]').forEach(label=>{
    const complete=state.replay&&replay.loaded&&replay.index===replay.loaded.rows.length-1;
    label.textContent=complete?'Complete':state.paused?'Paused':'Playing';label.classList.toggle('paused',state.paused);
  });
  document.querySelectorAll('[data-replay-name]').forEach(label=>{label.textContent=replay.loaded?.name||'Recorded session';label.title=label.textContent;});
}
function resetReplayBaseline(){
  Object.assign(state,{samples:[],frameTimes:[],renderTimes:[],pitch:0,roll:0,yaw:0,linear:[0,0,0],magnitude:0,peak:0,rotation:0,
    motion:'Waiting for data',candidate:'Stationary',candidateSince:0,motionHistory:[],previous:null,latestEvent:null,events:[],
    impactActive:false,impactUntil:-Infinity,cooldownUntil:-Infinity,quietSince:null,lastSummary:-Infinity});
}
function replaySample(index,report=true){
  const row=replay.loaded.rows[index];
  const sample=calibrationCorrect({t:row[0],a:row.slice(2,5),g:row.slice(5,8)},replay.loaded.metadata.calibration_profile||null);
  estimate(sample,row[1]);detectMotion(sample,report);state.peak=Math.max(state.peak,state.magnitude);
  state.samples.push(sample);while(state.samples.length&&state.samples[0].t<sample.t-30)state.samples.shift();
  replay.index=index;replay.position=sample.t;state.lastAt=performance.now()/1000;
  if(report){
    state.frameTimes.push(state.lastAt);if(state.frameTimes.length>120)state.frameTimes.shift();
    if(sample.t-state.lastSummary>=terminalIntervals[terminalState.rate]){
      state.lastSummary=sample.t;
      log('TEL',`REPLAY ${sample.t.toFixed(2)} S · AX:${sample.a[0].toFixed(2)} AY:${sample.a[1].toFixed(2)} AZ:${sample.a[2].toFixed(2)} · GX:${sample.g[0].toFixed(1)} GY:${sample.g[1].toFixed(1)} GZ:${sample.g[2].toFixed(1)}`);
    }
  }
}
function anchorReplay(){replay.anchor=replay.position;replay.wall=performance.now()/1000;}
function replayTick(){
  if(!state.replay||state.paused)return;
  const target=replay.anchor+(performance.now()/1000-replay.wall)*replay.speed;
  // Yield after bounded batches when a large file or background tab falls behind.
  let count=0;
  while(replay.index+1<replay.loaded.rows.length&&replay.loaded.rows[replay.index+1][0]<=target&&count++<500)replaySample(replay.index+1);
  if(replay.index===replay.loaded.rows.length-1){state.paused=true;log('SYS','REPLAY COMPLETE');updateStreamControls();}
  syncReplayControls();
}
function startReplay(){
  if(!replay.loaded||recording.active)return;
  calibrationCancel('Source changed. Start simulated calibration again.');
  faultReset();
  clearInterval(state.sampleTimer);clearInterval(state.renderTimer);clearInterval(replay.timer);
  state.connected=true;state.replay=true;state.paused=false;
  resetReplayBaseline();replay.index=-1;replay.position=0;replaySample(0);anchorReplay();
  byId('connect').textContent='Stop replay';
  replay.timer=setInterval(replayTick,15);
  state.renderTimer=setInterval(()=>{if(!document.hidden){state.renderTimes.push(performance.now()/1000);redraw();updateReadings();}},50);
  log('SYS',`REPLAY LOADED · ${replay.loaded.rows.length} SAMPLES · ${replay.loaded.name}`);
  updateStreamControls();redraw();updateReadings();
}
function toggleReplay(){
  if(!replay.loaded||state.connected&&!state.replay||calibrationBusy()||faultBusy())return;
  if(!state.replay){startReplay();return;}
  if(state.paused&&replay.index===replay.loaded.rows.length-1){startReplay();return;}
  state.paused=!state.paused;state.frameTimes=[];anchorReplay();
  log('SYS',`REPLAY ${state.paused?'PAUSED':'PLAYING'}`);updateStreamControls();updateReadings();
}
function seekReplay(target){
  if(!state.replay){startReplay();state.paused=true;}
  resetReplayBaseline();replay.index=-1;
  let low=0,high=replay.loaded.rows.length-1;
  while(low<high){const mid=(low+high)>>1;if(replay.loaded.rows[mid][0]<target)low=mid+1;else high=mid;}
  for(let i=0;i<=low;i++)replaySample(i,false);
  anchorReplay();log('SYS',`REPLAY SEEK · ${replay.position.toFixed(2)} S`);
  updateStreamControls();redraw();updateReadings();
}
function stepReplay(){
  if(!state.replay){startReplay();state.paused=true;}
  else{state.paused=true;if(replay.index+1<replay.loaded.rows.length)replaySample(replay.index+1);}
  anchorReplay();updateStreamControls();redraw();updateReadings();
}
// Parse CSV quoting explicitly; never evaluate imported content.
function parseSessionCsv(text){
  const rows=[];let row=[],cell='',quoted=false;
  text=text.replace(/^\uFEFF/,'');
  for(let i=0;i<text.length;i++){
    const c=text[i];
    if(c==='"'){if(quoted&&text[i+1]==='"'){cell+='"';i++;}else quoted=!quoted;}
    else if(!quoted&&(c===','||c==='\n'||c==='\r')){
      row.push(cell);cell='';if(c!==','){if(row.some(v=>v!==''))rows.push(row);row=[];if(c==='\r'&&text[i+1]==='\n')i++;}
    }else cell+=c;
  }
  if(quoted)throw Error('CSV contains an unfinished quoted field.');
  row.push(cell);if(row.some(v=>v!==''))rows.push(row);
  return rows;
}
function validateSession(csv,metadata,name){
  if(!metadata||typeof metadata!=='object'||Array.isArray(metadata))throw Error('The JSON manifest must be an object.');
  if(metadata.format_version!==1)throw Error('This session format is unsupported. Expected version 1.');
  if(metadata.csv_file&&metadata.csv_file!==name)throw Error('The JSON references a different CSV. Select the matching pair.');
  const parsed=parseSessionCsv(csv),header=parsed.shift()||[];
  if(sessionColumns.some(column=>!header.includes(column)))throw Error('CSV is missing required session columns.');
  if(new Set(header).size!==header.length)throw Error('CSV has duplicate column names.');
  if(!parsed.length||parsed.length>100000)throw Error('Choose a session with 1–100,000 samples.');
  const indices=sessionColumns.map(column=>header.indexOf(column));let previous=-1;
  const rows=parsed.map((raw,index)=>{
    const values=indices.map(i=>raw[i]);
    if(raw.length!==header.length)throw Error(`CSV row ${index+2} has missing or extra fields.`);
    for(let i=0;i<14;i++){
      if(values[i].trim()===''||!Number.isFinite(Number(values[i])))throw Error(`Invalid ${sessionColumns[i]} at row ${index+2}.`);
      values[i]=Number(values[i]);
    }
    if(values[0]<previous||values[0]<0||values[1]<0)throw Error('Session timing must be nonnegative and chronological.');
    previous=values[0];
    if(values[16]!==''&&!Number.isFinite(Number(values[16])))throw Error(`Invalid event peak at row ${index+2}.`);
    return values;
  });
  if(metadata.sample_count!=null&&metadata.sample_count!==rows.length)throw Error('Sample count does not match the JSON manifest.');
  return {rows,metadata,name,duration:rows.at(-1)[0]};
}
function openReplayDialog(){
  pendingSessionFiles.clear();byId('load-selection').hidden=true;
  byId('load-error').hidden=true;byId('session-files').value='';byId('load-last').hidden=!recording.saved;
  byId('load-dialog').showModal();
}
function loadReplay(session){
  replay.loaded=session;byId('load-dialog').close();startReplay();
}
byId('open-replay').onclick=openReplayDialog;
byId('load-close').onclick=()=>byId('load-dialog').close();
byId('load-last').onclick=()=>loadReplay({rows:recording.saved.rows,metadata:recording.saved.metadata,name:recording.saved.basename+'.csv',duration:recording.saved.metadata.duration_s});
const pendingSessionFiles=new Map();
let sessionImportBusy=false;
async function readSessionZip(file){
  if(file.size>32*1024*1024)throw Error('Session ZIP must be smaller than 32 MB.');
  const bytes=new Uint8Array(await file.arrayBuffer()),view=new DataView(bytes.buffer),decoder=new TextDecoder('utf-8',{fatal:true});
  let end=-1;
  for(let i=bytes.length-22;i>=Math.max(0,bytes.length-65557);i--)if(view.getUint32(i,true)===0x06054b50&&i+22+view.getUint16(i+20,true)===bytes.length){end=i;break;}
  if(end<0)throw Error('This ZIP is incomplete or invalid.');
  if(view.getUint16(end+4,true)||view.getUint16(end+6,true))throw Error('Choose a single session ZIP, not a split archive.');
  const count=view.getUint16(end+10,true),size=view.getUint32(end+12,true);let cursor=view.getUint32(end+16,true);
  if(count!==2||cursor+size>end)throw Error('Session ZIP must contain exactly one CSV and one JSON.');
  const files=[];
  for(let i=0;i<count;i++){
    if(cursor+46>end||view.getUint32(cursor,true)!==0x02014b50)throw Error('Invalid ZIP directory.');
    const flags=view.getUint16(cursor+8,true),method=view.getUint16(cursor+10,true),crc=view.getUint32(cursor+16,true),packed=view.getUint32(cursor+20,true),length=view.getUint32(cursor+24,true),nameLength=view.getUint16(cursor+28,true),extra=view.getUint16(cursor+30,true),comment=view.getUint16(cursor+32,true),local=view.getUint32(cursor+42,true);
    if(cursor+46+nameLength+extra+comment>end||local+30>bytes.length||view.getUint32(local,true)!==0x04034b50)throw Error('Invalid ZIP entry.');
    const name=decoder.decode(bytes.slice(cursor+46,cursor+46+nameLength));
    if(flags&1||method!==0)throw Error('Choose the ZIP downloaded by this demo, or extract the ZIP and select its CSV and JSON together.');
    const start=local+30+view.getUint16(local+26,true)+view.getUint16(local+28,true);
    if(length>16*1024*1024||packed!==length||start+length>bytes.length)throw Error('Invalid or oversized session file.');
    const data=bytes.slice(start,start+length);
    if(crc32(data)!==crc)throw Error('A file in this ZIP is damaged. Download or select the session again.');
    files.push({name,text:decoder.decode(data)});cursor+=46+nameLength+extra+comment;
  }
  const csv=files.find(f=>f.name.toLowerCase().endsWith('.csv')),json=files.find(f=>f.name.toLowerCase().endsWith('.json'));
  if(!csv||!json)throw Error('Session ZIP must contain a CSV and its JSON manifest.');
  return validateSession(csv.text,JSON.parse(json.text),csv.name);
}
async function importSessionFiles(files){
  if(sessionImportBusy||!files.length)return;
  sessionImportBusy=true;byId('load-error').hidden=true;
  try{
    const zip=files.find(f=>f.name.toLowerCase().endsWith('.zip'));
    if(zip){
      if(files.length!==1)throw Error('Choose one session ZIP, or the two extracted files.');
      loadReplay(await readSessionZip(zip));return;
    }
    for(const file of files){
      const kind=file.name.toLowerCase().endsWith('.csv')?'csv':file.name.toLowerCase().endsWith('.json')?'json':null;
      if(!kind)throw Error('Choose a ZIP, CSV or JSON session file.');
      if(file.size>16*1024*1024)throw Error('Each session file must be smaller than 16 MB.');
      pendingSessionFiles.set(kind,file);
    }
    const csv=pendingSessionFiles.get('csv'),json=pendingSessionFiles.get('json');
    byId('load-selection').hidden=false;
    byId('load-selection').textContent=[...pendingSessionFiles.values()].map(f=>f.name).join(' + ');
    if(!csv||!json){byId('load-selection').textContent+=` — now choose or drop the matching ${csv?'JSON':'CSV'}.`;return;}
    const [data,manifest]=await Promise.all([csv.text(),json.text()]);
    loadReplay(validateSession(data,JSON.parse(manifest),csv.name));
  }catch(error){byId('load-error').textContent=error.message;byId('load-error').hidden=false;}
  finally{sessionImportBusy=false;byId('session-files').value='';}
}
byId('session-files').onchange=event=>importSessionFiles([...event.target.files]);
const sessionDrop=byId('session-drop');
sessionDrop.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();byId('session-files').click();}};
sessionDrop.ondragover=event=>{event.preventDefault();sessionDrop.classList.add('drag-over');};
sessionDrop.ondragleave=()=>sessionDrop.classList.remove('drag-over');
sessionDrop.ondrop=event=>{event.preventDefault();sessionDrop.classList.remove('drag-over');importSessionFiles([...event.dataTransfer.files]);};
byId('replay-play').onclick=toggleReplay;
byId('replay-restart').onclick=startReplay;
byId('replay-step').onclick=stepReplay;
byId('replay-position').onchange=event=>seekReplay(replay.loaded.duration*Number(event.target.value)/1000);
byId('replay-speed').oninput=event=>{replay.speed=replaySpeeds[Number(event.target.value)];anchorReplay();syncReplayControls();};


function receive(){
  if(!state.connected||state.paused)return;
  const now=performance.now()/1000,sample=memoryPrepare(calibrationPrepare(demoSample(now-state.start)));
  if(!faultBeforeReceive(sample,now))return;
  sample.t=now-state.visualStart;
  memoryFeed(sample);
  const dt=now-state.lastAt;
  calibrationFeed(sample,now);
  const corrected=calibrationCorrect(sample);
  estimate(corrected,dt);state.lastAt=now;state.sequence++;
  const emitted=detectMotion(corrected);state.peak=Math.max(state.peak,state.magnitude);
  captureSample(sample,dt,emitted);
  state.samples.push(corrected);while(state.samples[0].t<sample.t-30)state.samples.shift();
  state.frameTimes.push(now);if(state.frameTimes.length>120)state.frameTimes.shift();
  if(sample.t-state.lastSummary>=terminalIntervals[terminalState.rate]){
    state.lastSummary=sample.t;
    log('TEL',`AX:${sample.a[0].toFixed(2)} AY:${sample.a[1].toFixed(2)} AZ:${sample.a[2].toFixed(2)} · GX:${sample.g[0].toFixed(1)} GY:${sample.g[1].toFixed(1)} GZ:${sample.g[2].toFixed(1)}`);
  }
}
function updateReadings(){
  faultRefresh();
  const now=performance.now()/1000;
  while(state.renderTimes.length&&state.renderTimes[0]<now-1)state.renderTimes.shift();
  if(state.connected){
    const stamps=state.frameTimes;
    const streamHz=!state.paused&&now-state.lastAt<Math.max(.15,state.interval/500)&&stamps.length>1?(stamps.length-1)/(stamps.at(-1)-stamps[0]):0;
    metricValues[0].textContent=state.replay?'Replay':'Simulation';metricValues[1].textContent=`${streamHz.toFixed(1)} / ${state.renderTimes.length} Hz`;
    metricValues[2].textContent=`${state.magnitude.toFixed(2)} g`;metricValues[3].textContent=`${state.peak.toFixed(2)} g`;
    document.querySelector('.events-panel h3').textContent=state.motion;
    eventValues[0].textContent=state.latestEvent?`${state.latestEvent.peak.toFixed(2)} g`:'—';
    eventValues[1].textContent=state.latestEvent?.axis==='—'?'—':state.latestEvent?`IMU ${state.latestEvent.axis}`:'—';
    eventValues[2].textContent=`${state.rotation.toFixed(1)} °/s`;
    document.querySelector('.events-panel>.muted:not(.event-note)').textContent=state.events.length?state.events.map(e=>`${e.time}  ${e.kind}`).join('\n'):'No events recorded';
    healthValues[0].textContent=state.replay?'REPLAY':'DEMO';healthValues[1].textContent=`${Math.round((now-state.lastAt)*1000)} MS`;healthValues[3].textContent=state.paused?'PAUSED':state.replay?'RECORDED':'SIMULATED';
  }else{metricValues[0].textContent='Offline';metricValues[1].textContent='— / — Hz';healthValues[1].textContent='—';healthValues[3].textContent='OFFLINE';}
  const signed=v=>`${v>=0?'+':''}${v.toFixed(1)}°`;
  document.querySelector('.angles').textContent=`Pitch  ${signed(state.pitch)}     Roll  ${signed(state.roll)}     Yaw  ${signed(state.yaw)}`;
}
byId('connect').onclick=()=>{
  if(state.connected){
    calibrationCancel('Demo disconnected. Start simulated calibration again.');
    faultReset();
    if(recording.active)stopRecording('disconnected');
    state.connected=false;state.replay=false;state.paused=false;clearInterval(replay.timer);clearInterval(state.sampleTimer);clearInterval(state.renderTimer);
    byId('connect').textContent='Connect';document.querySelector('.connection-status').textContent='OFFLINE';
    document.querySelector('.connection-status').style.color='#a9bbcb';log('SYS','SOURCE DISCONNECTED');updateStreamControls();updateReadings();return;
  }
  faultReset(true);
  Object.assign(state,{connected:true,replay:false,paused:false,interval:30,start:performance.now()/1000,visualStart:performance.now()/1000,lastAt:performance.now()/1000,samples:[],frameTimes:[],renderTimes:[],pitch:0,roll:0,yaw:0,linear:[0,0,0],peak:0,sequence:0,motion:'Waiting for data',candidate:'Stationary',candidateSince:0,motionHistory:[],previous:null,latestEvent:null,events:[],impactActive:false,impactUntil:-Infinity,cooldownUntil:-Infinity,quietSince:null,lastSummary:-Infinity});
  byId('connect').textContent='Disconnect';document.querySelector('.connection-status').textContent='DEMO · CONNECTED';document.querySelector('.connection-status').style.color='#86d8a6';
  log('SYS','DEMO CONNECTED · SIMULATED SENSOR STREAM · 30 MS INTERVAL');updateStreamControls();receive();redraw();updateReadings();
  state.sampleTimer=setInterval(receive,30);
  state.renderTimer=setInterval(()=>{if(!document.hidden){state.renderTimes.push(performance.now()/1000);redraw();updateReadings();}},50);
};

function surface(id){const canvas=byId(id),bounds=canvas.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2);const width=Math.round(bounds.width*dpr),height=Math.round(bounds.height*dpr);if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height;}const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);return {ctx,w:bounds.width,h:bounds.height};}
function drawPlot(id,label,unit,range){
  const{ctx,w,h}=surface(id),left=42,top=15,right=w-10,bottom=h-33;
  const end=Math.max(30,state.samples.at(-1)?.t||0),start=end-30;
  ctx.fillStyle='#101720';ctx.fillRect(0,0,w,h);ctx.font='11px Segoe UI';ctx.strokeStyle='#28323e';ctx.lineWidth=.6;
  for(let i=0;i<=6;i++){const y=top+(bottom-top)*i/6;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();ctx.fillStyle='#a9bbcb';ctx.textAlign='right';ctx.fillText(String(range-range*i/3),left-6,y+4);}
  for(let i=0;i<=6;i++){const x=left+(right-left)*i/6;ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,bottom);ctx.stroke();ctx.fillStyle='#a9bbcb';ctx.textAlign='center';ctx.fillText((start+i*5).toFixed(0),x,bottom+16);}
  ctx.strokeStyle='#7b8b9e';ctx.beginPath();ctx.moveTo(left,top);ctx.lineTo(left,bottom);ctx.lineTo(right,bottom);ctx.stroke();ctx.fillStyle='#a9bbcb';ctx.fillText('Session time (s)',(left+right)/2,h-4);
  ctx.save();ctx.translate(12,(top+bottom)/2);ctx.rotate(-Math.PI/2);ctx.fillText(`${label} (${unit})`,0,0);ctx.restore();
  ctx.save();ctx.beginPath();ctx.rect(left,top,right-left,bottom-top);ctx.clip();
  for(let axis=0;axis<3;axis++){ctx.strokeStyle=plotColors[axis];ctx.lineWidth=2;ctx.beginPath();let drawing=false;for(const sample of state.samples){if(sample.t<start)continue;const value=(id==='gyro'?sample.g:sample.a)[axis],x=left+(sample.t-start)/30*(right-left),y=top+(range-value)/(2*range)*(bottom-top);if(drawing)ctx.lineTo(x,y);else{ctx.moveTo(x,y);drawing=true;}}ctx.stroke();}ctx.restore();
  ['X','Y','Z'].forEach((axis,i)=>{const y=top+15+i*16;ctx.strokeStyle=plotColors[i];ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(left+18,y);ctx.lineTo(left+39,y);ctx.stroke();ctx.fillStyle='#a9bbcb';ctx.textAlign='left';ctx.fillText(axis,left+48,y+4);});
}
// Same transform order as app.py: yaw(Z) * pitch(X) * roll(Y).
function rotateModel([x,y,z]){
  const r=radians(state.roll),p=radians(state.pitch),yaw=radians(state.yaw);
  [x,z]=[x*Math.cos(r)+z*Math.sin(r),-x*Math.sin(r)+z*Math.cos(r)];
  [y,z]=[y*Math.cos(p)-z*Math.sin(p),y*Math.sin(p)+z*Math.cos(p)];
  return[x*Math.cos(yaw)-y*Math.sin(yaw),x*Math.sin(yaw)+y*Math.cos(yaw),z];
}
function drawAircraft(){
  const{ctx,w,h}=surface('attitude');ctx.fillStyle='#101923';ctx.fillRect(0,0,w,h);
  const scale=Math.min(w/23,h/15)*camera.zoom;
  const azimuth=radians(camera.azimuth),elevation=radians(camera.elevation);
  const ca=Math.cos(azimuth),sa=Math.sin(azimuth),ce=Math.cos(elevation),se=Math.sin(elevation);
  function project([x,y,z]){
    const xx=y*ca-x*sa,depth=x*ca+y*sa;
    return[w/2+camera.panX+xx*scale,h/2+camera.panY+(depth*se-z*ce)*scale,depth*ce+z*se];
  }
  ctx.strokeStyle='#415c6f78';ctx.lineWidth=.7;
  for(let i=-25;i<=25;i+=5)for(const line of [[[i,-25,-4],[i,25,-4]],[[-25,i,-4],[25,i,-4]]]){const a=project(line[0]),b=project(line[1]);ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.stroke();}
  // Meshes are generated directly from the desktop builders in models.py.
  const model=state.model==='custom'?customModel:builtInModels[state.model];
  const vertices=model.vertices.map(v=>project(rotateModel(v)));
  const faces=model.faces.map((face,i)=>({...face,i,depth:face.indices.reduce((s,j)=>s+vertices[j][2],0)/3})).sort((a,b)=>a.depth-b.depth);
  for(const face of faces){
    ctx.beginPath();face.indices.forEach((index,i)=>i?ctx.lineTo(vertices[index][0],vertices[index][1]):ctx.moveTo(vertices[index][0],vertices[index][1]));ctx.closePath();
    if(state.model==='aircraft')ctx.fillStyle=['#9e2424','#cf3333','#531414','#380b0b','#901e1e','#672020'][face.i];
    else{
      const [a,b,c]=face.indices.map(i=>rotateModel(model.vertices[i]));
      const u=b.map((v,i)=>v-a[i]),v=c.map((n,i)=>n-a[i]);
      const normal=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]],length=Math.hypot(...normal)||1;
      const shade=.5+.5*Math.abs((normal[0]*.3+normal[1]*.4+normal[2]*.866)/length);
      const rgb=face.color.slice(0,3).map(n=>Math.round(n*255*shade));
      ctx.fillStyle=`rgba(${rgb.join(',')},${face.color[3]})`;
    }
    ctx.fill();ctx.strokeStyle=state.model==='aircraft'?'#edf3fa':'#243b528c';ctx.lineWidth=.65;ctx.stroke();
  }
  const origin=project([0,0,0]);
  [[[6,0,0],'#ff0000'],[[0,6,0],'#00ff00'],[[0,0,6],'#0000ff']].forEach(([v,color])=>{const end=project(rotateModel(v));ctx.strokeStyle=color;ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(origin[0],origin[1]);ctx.lineTo(end[0],end[1]);ctx.stroke();});
  if(state.connected){const end=project([-state.linear[0]*10,state.linear[1]*10,state.linear[2]*10]);ctx.beginPath();ctx.moveTo(origin[0],origin[1]);ctx.lineTo(end[0],end[1]);ctx.strokeStyle='#00ffff';ctx.lineWidth=3;ctx.stroke();}
}
function redraw(){drawAircraft();drawPlot('acceleration','Acceleration','g',3);drawPlot('gyro','Angular velocity','°/s',300);}
function resetCamera(){
  Object.assign(camera,{azimuth:45,elevation:30,zoom:1,panX:0,panY:0});
  drawAircraft();
}
function zoomCamera(factor){
  camera.zoom=Math.min(4,Math.max(.3,camera.zoom*factor));
  drawAircraft();
}
const attitudeCanvas=byId('attitude');
attitudeCanvas.addEventListener('pointerdown',event=>{
  if(event.button!==0&&event.button!==1)return;
  event.preventDefault();attitudeCanvas.focus({preventScroll:true});
  camera.drag={id:event.pointerId,x:event.clientX,y:event.clientY,pan:event.ctrlKey||event.button===1};
  attitudeCanvas.setPointerCapture(event.pointerId);
  attitudeCanvas.classList.add('dragging');
});
attitudeCanvas.addEventListener('pointermove',event=>{
  if(!camera.drag||camera.drag.id!==event.pointerId)return;
  const dx=event.clientX-camera.drag.x,dy=event.clientY-camera.drag.y;
  camera.drag.x=event.clientX;camera.drag.y=event.clientY;
  if(camera.drag.pan||event.ctrlKey){
    const bounds=attitudeCanvas.getBoundingClientRect();
    camera.panX=Math.min(bounds.width,Math.max(-bounds.width,camera.panX+dx));
    camera.panY=Math.min(bounds.height,Math.max(-bounds.height,camera.panY+dy));
  }else{
    // Same 0.25 drag sensitivity used by the desktop widget.
    camera.azimuth=wrap(camera.azimuth-dx*.25);
    camera.elevation=Math.min(89,Math.max(-89,camera.elevation+dy*.25));
  }
  drawAircraft();
});
function endCameraDrag(event){
  if(camera.drag?.id!==event.pointerId)return;
  camera.drag=null;attitudeCanvas.classList.remove('dragging');
  if(attitudeCanvas.hasPointerCapture(event.pointerId))attitudeCanvas.releasePointerCapture(event.pointerId);
}
attitudeCanvas.addEventListener('pointerup',endCameraDrag);
attitudeCanvas.addEventListener('pointercancel',endCameraDrag);
attitudeCanvas.addEventListener('lostpointercapture',()=>{camera.drag=null;attitudeCanvas.classList.remove('dragging');});
attitudeCanvas.addEventListener('wheel',event=>{
  event.preventDefault();
  const pixels=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?300:1);
  zoomCamera(Math.exp(-Math.min(200,Math.max(-200,pixels))*.0015));
},{passive:false});
attitudeCanvas.addEventListener('dblclick',resetCamera);
attitudeCanvas.addEventListener('keydown',event=>{
  if(event.ctrlKey||event.metaKey||event.altKey)return;
  let handled=true;
  if(event.key==='ArrowLeft')camera.azimuth=wrap(camera.azimuth+5);
  else if(event.key==='ArrowRight')camera.azimuth=wrap(camera.azimuth-5);
  else if(event.key==='ArrowUp')camera.elevation=Math.min(89,camera.elevation+5);
  else if(event.key==='ArrowDown')camera.elevation=Math.max(-89,camera.elevation-5);
  else if(event.key==='+'||event.key==='=')zoomCamera(1.1);
  else if(event.key==='-'||event.key==='_')zoomCamera(1/1.1);
  else if(event.key==='Home')resetCamera();
  else handled=false;
  if(handled){event.preventDefault();drawAircraft();}
});
// Like FocusOverlay, move the live content rather than starting another stream
// or copying a chart. The original panel keeps a frozen visual placeholder.
const focusDialog=byId('focus-dialog');
let focusedPanel=null;
const chartOverlay={x:null,y:null,drag:null};
function placeChartOverlay(){
  const controls=byId('focus-controls'),host=byId('focus-content');
  if(!focusDialog.open||!controls.classList.contains('chart-focus-controls'))return;
  const area=controls.querySelector('.terminal-overlay-body')?host.querySelector('.terminal-display')||host:host;
  const hostRect=host.getBoundingClientRect(),areaRect=area.getBoundingClientRect();
  controls.style.maxWidth=`${Math.max(1,area.clientWidth-16)}px`;controls.style.maxHeight=`${Math.max(1,area.clientHeight-16)}px`;
  const margin=8,maxX=Math.max(margin,area.clientWidth-controls.offsetWidth-margin),maxY=Math.max(margin,area.clientHeight-controls.offsetHeight-margin);
  chartOverlay.x=Math.max(margin,Math.min(maxX,chartOverlay.x??maxX));
  chartOverlay.y=Math.max(margin,Math.min(maxY,chartOverlay.y??maxY));
  controls.style.left=`${Math.round(areaRect.left-hostRect.left+chartOverlay.x)}px`;controls.style.top=`${Math.round(areaRect.top-hostRect.top+chartOverlay.y)}px`;
}
function addChartDragHandle(controls,title='Chart controls'){
  const header=document.createElement('div');header.className='chart-overlay-header';
  const handle=document.createElement('button');handle.type='button';handle.className='chart-drag-handle';
  handle.textContent=`⠿  ${title}`;handle.title='Drag to move · arrow keys to move · Home to reset';
  handle.setAttribute('aria-label',`Move ${title.toLowerCase()}. Drag or use arrow keys; Home resets position.`);
  handle.onpointerdown=event=>{
    if(event.button!==0)return;
    event.preventDefault();handle.focus({preventScroll:true});
    chartOverlay.drag={id:event.pointerId,x:event.clientX,y:event.clientY,left:chartOverlay.x,top:chartOverlay.y};
    handle.setPointerCapture(event.pointerId);handle.classList.add('dragging');
  };
  handle.onpointermove=event=>{
    const drag=chartOverlay.drag;if(!drag||drag.id!==event.pointerId)return;
    chartOverlay.x=drag.left+event.clientX-drag.x;chartOverlay.y=drag.top+event.clientY-drag.y;placeChartOverlay();
  };
  const stop=()=>{chartOverlay.drag=null;handle.classList.remove('dragging');};
  handle.onpointerup=event=>{stop();if(handle.hasPointerCapture(event.pointerId))handle.releasePointerCapture(event.pointerId);};
  handle.onpointercancel=stop;handle.onlostpointercapture=stop;
  handle.onkeydown=event=>{
    const steps={ArrowLeft:[-16,0],ArrowRight:[16,0],ArrowUp:[0,-16],ArrowDown:[0,16]};
    if(event.key==='Home'){event.preventDefault();chartOverlay.x=chartOverlay.y=null;placeChartOverlay();}
    else if(steps[event.key]){event.preventDefault();const [x,y]=steps[event.key];chartOverlay.x+=x;chartOverlay.y+=y;placeChartOverlay();}
  };
  const collapse=document.createElement('button');collapse.type='button';collapse.className='chart-collapse';collapse.textContent='−';collapse.title=`Minimize ${title.toLowerCase()}`;collapse.setAttribute('aria-label',collapse.title);collapse.setAttribute('aria-expanded','true');
  collapse.onclick=()=>{
    const minimized=controls.classList.toggle('minimized');collapse.textContent=minimized?'':'−';collapse.title=`${minimized?'Restore':'Minimize'} ${title.toLowerCase()}`;collapse.setAttribute('aria-label',collapse.title);collapse.setAttribute('aria-expanded',String(!minimized));placeChartOverlay();
  };
  header.append(handle,collapse);controls.append(header);
}
function closeFocus(){
  if(!focusedPanel)return;
  const {panel,nodes,placeholder,button}=focusedPanel;
  focusDialog.append(byId('focus-controls'));for(const property of ['left','top','max-width','max-height'])byId('focus-controls').style.removeProperty(property);chartOverlay.drag=null;
  for(const node of nodes)panel.append(node);
  placeholder.remove();byId('focus-controls').replaceChildren();
  focusedPanel=null;focusDialog.close();
  requestAnimationFrame(redraw);button.focus({preventScroll:true});
}
function focusControls(panel){
  const controls=byId('focus-controls');controls.replaceChildren();controls.classList.remove('terminal-focus-controls','chart-focus-controls','minimized');
  if(panel.classList.contains('attitude-panel')){
    controls.classList.add('chart-focus-controls');
    byId('focus-content').append(controls);addChartDragHandle(controls,'Simulation controls');
    const body=document.createElement('div');body.className='simulation-overlay-body';
    const label=document.createElement('label');label.textContent='3D MODEL';label.htmlFor='focus-model-picker';
    const picker=document.createElement('select');picker.setAttribute('aria-label','Expanded 3D model');
    picker.id='focus-model-picker';
    for(const option of byId('model').options)picker.append(option.cloneNode(true));
    picker.value=state.model;
    picker.onchange=()=>{byId('model').value=picker.value;byId('model').onchange();};
    picker.dataset.modelPicker='';
    const reset=document.createElement('button');reset.textContent='Reset attitude  [R]';reset.onclick=resetAttitude;
    body.append(label,picker,reset);
    const status=document.createElement('span');status.dataset.stlStatus='';status.setAttribute('role','status');status.textContent=byId('stl-status').textContent;body.append(status);controls.append(body);
  }else if(panel.classList.contains('plot-panel')){
    controls.classList.add('chart-focus-controls');
    byId('focus-content').append(controls);addChartDragHandle(controls);
    const stream=document.createElement('div');stream.className='focus-control-row';stream.dataset.focusStream='';
    const label=document.createElement('span');label.textContent='STREAM RATE';stream.append(label);
    for(const [interval,title] of [[30,'33 Hz'],[500,'2 Hz'],[1000,'1 Hz'],[2000,'0.5 Hz']]){
      const button=document.createElement('button');button.textContent=title;button.dataset.focusRate=interval;
      button.onclick=()=>setStreamRate(interval);stream.append(button);
    }
    const playback=document.createElement('div');playback.className='focus-replay-panel';playback.dataset.focusReplay='';
    const top=document.createElement('div');top.className='replay-toolbar';
    const identity=document.createElement('div');identity.className='replay-identity';
    const replayLabel=document.createElement('span');replayLabel.className='replay-caption';replayLabel.textContent='SESSION REPLAY';
    const name=document.createElement('strong');name.dataset.replayName='';identity.append(replayLabel,name);
    const actions=document.createElement('div');actions.className='replay-actions';
    for(const [action,title,handler] of [['play','Play ▶',toggleReplay],['restart','Restart ↺',startReplay],['step','Step ▸|',stepReplay]]){
      const button=document.createElement('button');button.dataset.replayAction=action;button.textContent=title;button.onclick=handler;
      if(action==='step'){button.title='Step one frame';button.setAttribute('aria-label','Step one frame');}
      if(action==='play')button.classList.add('primary');actions.append(button);
    }
    const speedLabel=document.createElement('label');speedLabel.className='replay-speed-control';speedLabel.append('Speed');
    const speed=document.createElement('select');speed.dataset.replaySpeedPicker='';speed.setAttribute('aria-label','Expanded chart replay speed');
    replaySpeeds.forEach(value=>{const option=document.createElement('option');option.value=value;option.textContent=`${value}×`;speed.append(option);});
    speed.onchange=()=>{replay.speed=Number(speed.value);anchorReplay();syncReplayControls();};speedLabel.append(speed);
    top.append(identity,actions,speedLabel);
    const timeline=document.createElement('div');timeline.className='replay-timeline';
    const readout=document.createElement('div');readout.className='replay-time-readout';
    const badge=document.createElement('span');badge.dataset.replayState='';badge.className='replay-state';
    const time=document.createElement('span');time.className='replay-time';
    const progress=document.createElement('strong');progress.dataset.replayProgress='';
    const duration=document.createElement('span');duration.dataset.replayDuration='';time.append(progress,' / ',duration);readout.append(badge,time);
    const position=document.createElement('input');position.type='range';position.min=0;position.max=1000;position.value=0;position.dataset.replaySeek='';position.setAttribute('aria-label','Expanded chart replay position');
    position.oninput=()=>{position.dataset.seeking='true';syncReplayControls();};
    position.onchange=()=>{delete position.dataset.seeking;if(replay.loaded)seekReplay(replay.loaded.duration*Number(position.value)/1000);};
    position.onpointercancel=()=>{delete position.dataset.seeking;syncReplayControls();};
    timeline.append(readout,position);playback.append(top,timeline);
    controls.append(stream,playback);
  }else if(panel.classList.contains('terminal-panel')){
    controls.classList.add('chart-focus-controls');
    byId('focus-content').append(controls);addChartDragHandle(controls,'Terminal controls');
    const body=document.createElement('div');body.className='terminal-overlay-body';
    const label=document.createElement('span');label.className='terminal-overlay-label';label.textContent='SHOW MESSAGES';body.append(label);
    const row=document.createElement('div');row.className='terminal-overlay-filters';
    for(const category of ['SYS','TEL','EVT','WARN']){
      const button=document.createElement('button');button.textContent=category;button.dataset.terminalCategory=category;
      button.onclick=()=>toggleTerminalCategory(category);row.append(button);
    }
    const toggles=document.createElement('div');toggles.className='terminal-overlay-toggles';
    const timestamps=document.createElement('button');timestamps.textContent='Timestamps';timestamps.dataset.timestamps='';timestamps.onclick=toggleTimestamps;
    const health=document.createElement('button');health.textContent='Health';health.dataset.terminalHealth='';health.onclick=toggleTerminalHealth;
    toggles.append(timestamps,health);
    const output=document.createElement('div');output.className='terminal-overlay-rate';
    const rateLabel=document.createElement('label');rateLabel.className='terminal-overlay-label';rateLabel.textContent='OUTPUT RATE';rateLabel.htmlFor='focus-terminal-rate';
    const picker=document.createElement('select');picker.dataset.terminalRatePicker='';picker.setAttribute('aria-label','Expanded terminal output rate');
    picker.id='focus-terminal-rate';
    terminalLabels.forEach((text,i)=>{const option=document.createElement('option');option.value=i;option.textContent=text;picker.append(option);});
    picker.onchange=()=>setTerminalRate(picker.value);
    const follow=document.createElement('button');follow.textContent='Follow live';follow.dataset.terminalFollow='';follow.onclick=followTerminal;
    output.append(rateLabel,picker);body.append(row,toggles,output,follow);controls.append(body);syncTerminalControls();
  }
  controls.hidden=controls.children.length===0;updateStreamControls();
}
document.querySelectorAll('.expand').forEach(button=>button.onclick=()=>{
  closeFocus();
  const panel=button.closest('.panel'),nodes=[...panel.children].filter(node=>node.tagName!=='HEADER');
  const canvas=panel.querySelector('canvas');let placeholder;
  if(canvas){placeholder=document.createElement('img');placeholder.src=canvas.toDataURL('image/png');placeholder.alt='Panel expanded above';}
  else{placeholder=document.createElement('div');placeholder.textContent=panel.querySelector('pre')?.textContent||'Panel expanded above';placeholder.classList.add('terminal-placeholder');}
  placeholder.classList.add('focus-placeholder');panel.append(placeholder);
  byId('focus-title').textContent=panel.querySelector('h2').textContent;
  byId('focus-content').append(...nodes);focusedPanel={panel,nodes,placeholder,button};
  focusControls(panel);focusDialog.showModal();byId('focus-close').focus();
  requestAnimationFrame(()=>{redraw();placeChartOverlay();});
});
byId('focus-close').onclick=closeFocus;
focusDialog.addEventListener('cancel',event=>{event.preventDefault();closeFocus();});
focusDialog.addEventListener('click',event=>{
  if(event.target!==focusDialog)return;
  const rect=focusDialog.getBoundingClientRect();
  if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)closeFocus();
});
window.addEventListener('resize',()=>requestAnimationFrame(redraw));
new ResizeObserver(()=>{placeChartOverlay();if(focusDialog.open)requestAnimationFrame(redraw);}).observe(byId('focus-content'));
new ResizeObserver(placeChartOverlay).observe(byId('focus-controls'));
new ResizeObserver(redraw).observe(document.querySelector('.workspace'));
byId('health-toggle').onclick=toggleTerminalHealth;
document.querySelector('.terminal-body pre').replaceChildren();
log('SYS','READY · SELECT DEMO, THEN CONNECT');syncTerminalControls();updateStreamControls();redraw();
