// Health metrics describe the browser source; firmware-only values remain unavailable.
const calibrationDialog=byId('calibration-dialog');
function refreshDeviceHealth(){
  if(!calibrationDialog.open)return;
  refreshCalibration();
  const now=performance.now()/1000,connected=state.connected,recorded=connected&&state.replay;
  const stamps=state.frameTimes.filter(t=>now-t<=5),intervals=stamps.slice(1).map((t,i)=>t-stamps[i]);
  const fresh=connected&&now-state.lastAt<=Math.max(1.5,state.interval/1000*3);
  const rate=!state.paused&&fresh&&intervals.length?intervals.length/(stamps.at(-1)-stamps[0]):0;
  const jitter=recorded?[]:intervals.map(dt=>Math.abs(dt-state.interval/1000)*1000).sort((a,b)=>a-b);
  const p95=jitter.length?jitter[Math.min(jitter.length-1,Math.ceil(jitter.length*.95)-1)]:null;
  const errors=recorded?null:fault.rejected,missing=recorded?null:fault.rejected;
  const frames=recorded?replay.index+1:Math.max(0,state.sequence-fault.rejected);
  const total=frames+(missing||0),loss=total&&missing!==null?missing/total*100:null;
  const warning=connected&&!recorded&&(errors>0||(p95!==null&&p95>Math.max(15,state.interval*.5))||faultBusy());
  byId('cal-source').textContent=recorded?'Replay · recorded session':connected?'Demo · simulated sensor':'No device connected';
  byId('cal-health-state').textContent=!connected?'Offline':state.paused?'Paused':warning?'Needs attention':fresh?(recorded?'Replay active':'Demo healthy'):'No new data';
  byId('cal-health-state').style.color=warning?'#ebc66d':'#86d8a6';
  byId('cal-checksum').textContent=connected&&errors!==null?String(errors):'—';
  byId('cal-loss').textContent=connected&&loss!==null?loss.toFixed(2)+'%':'—';
  byId('cal-jitter').textContent=connected&&p95!==null?p95.toFixed(1)+' ms':'—';
  byId('cal-throughput').textContent=connected?rate.toFixed(1)+' frames/s':'—';
  byId('cal-frames').textContent=connected?String(frames):'—';
  byId('cal-missing').textContent=connected&&missing!==null?String(missing):'—';
  byId('cal-malformed').textContent=connected&&!recorded?'0':'—';
  byId('cal-reset').textContent=connected&&!recorded?fault.resetReason:'—';
  byId('cal-identity').textContent=recorded?'Recorded session':connected?'Demo · no hardware UID':'—';
  byId('cal-health-note').textContent=recorded?'Replay throughput describes browser playback. Serial checksum, packet loss, firmware and device identity are unavailable.':'Metrics describe accepted simulated packets and browser scheduling. Injected checksum errors count as rejected/missing frames. Firmware stack, command drops and hardware identity require a connected board in the desktop app.';
}
byId('calibration-open').onclick=()=>{calibrationDialog.showModal();refreshDeviceHealth();};
byId('calibration-close').onclick=()=>calibrationDialog.close();
setInterval(refreshDeviceHealth,250);

// Virtual MPU6050 calibration: these coefficients and samples belong only to this demo.
const calFaces=['+X','-X','+Y','-Y','+Z','-Z'];
const cal={stage:'idle',selected:null,lastFace:'+Z',turn:null,hold:.8,order:[],window:[],faces:{},gyro:null,profile:null,enabled:false,feedback:'Select Start simulated calibration to begin.',saved:false};
function randomFaceOrder(previous){
  const order=[...calFaces];
  for(let i=order.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[order[i],order[j]]=[order[j],order[i]];}
  // A restart should visibly change the sequence even if the shuffle repeats.
  if(order.every((face,i)=>face===previous[i]))[order[0],order[1]]=[order[1],order[0]];
  return order;
}
const virtualSensor={offset:[.04,-.03,.02],scale:[1.02,.98,1.03],gyro:[.7,-.5,.3]};
const calStoreKey='stm32-browser-simulated-calibration-v1';
function validSimProfile(profile){
  return profile?.simulated===true&&profile.device_key==='browser:virtual-mpu6050'&&['gyro_bias','accel_offset','accel_scale'].every(key=>Array.isArray(profile[key])&&profile[key].length===3&&profile[key].every(Number.isFinite))&&profile.accel_scale.every(v=>v>.7&&v<1.3);
}
try{const stored=JSON.parse(localStorage.getItem(calStoreKey)||'null');if(validSimProfile(stored)){cal.profile=stored;cal.enabled=true;cal.saved=true;cal.feedback='Saved simulated profile restored. It applies only to this browser demo.';}}catch{}
calibrationBusy=()=>['gyro','faces','ready'].includes(cal.stage);
function faceVector(face){const vector=[0,0,0];vector['XYZ'.indexOf(face[1])]=face[0]==='+'?1:-1;return vector;}
function virtualGravity(now=performance.now()/1000){
  if(!cal.turn)return faceVector(cal.lastFace);
  const t=Math.min(1,Math.max(0,(now-cal.turn.started)/cal.turn.duration)),from=cal.turn.from,to=cal.turn.to;
  if(t>=1)return to;
  const dot=Math.max(-1,Math.min(1,from.reduce((sum,v,i)=>sum+v*to[i],0)));
  if(dot>.999)return to;
  if(dot<-.999){
    const perpendicular=Math.abs(from[2])<.9?[-from[1],from[0],0]:[0,from[2],-from[1]],length=Math.hypot(...perpendicular);
    return from.map((v,i)=>v*Math.cos(Math.PI*t)+perpendicular[i]/length*Math.sin(Math.PI*t));
  }
  const angle=Math.acos(dot),denominator=Math.sin(angle);
  return from.map((v,i)=>(v*Math.sin((1-t)*angle)+to[i]*Math.sin(t*angle))/denominator);
}
function turnVirtualBoard(face){
  const from=virtualGravity();cal.turn={from,to:faceVector(face),started:performance.now()/1000,duration:.3+Math.random()*.25};cal.hold=.75+Math.random()*.25;
  cal.selected=face;cal.lastFace=face;cal.window=[];cal.feedback=`Rotating the virtual board to ${face}, then holding steady…`;
}
calibrationPrepare=sample=>{
  if(!cal.enabled)return sample;
  let a=sample.a,g=sample.g;
  if(calibrationBusy()){
    a=cal.stage==='gyro'?faceVector('+Z'):virtualGravity();g=[0,0,0];
  }
  const noise=()=>Math.random()*.008-.004;
  return {...sample,a:a.map((v,i)=>Number((v/virtualSensor.scale[i]+virtualSensor.offset[i]+noise()).toFixed(2))),g:g.map((v,i)=>Number((v+virtualSensor.gyro[i]+noise()).toFixed(1)))};
};
calibrationCorrect=(sample,profile=cal.profile)=>validSimProfile(profile)?{...sample,a:sample.a.map((v,i)=>(v-profile.accel_offset[i])*profile.accel_scale[i]),g:sample.g.map((v,i)=>v-profile.gyro_bias[i])}:sample;
calibrationMetadata=()=>cal.profile?{calibration_id:cal.profile.created_at,calibration_device_key:cal.profile.device_key,calibration_profile:cal.profile}:{};
function trimmedMean(values){const sorted=[...values].sort((a,b)=>a-b),trim=Math.max(1,Math.floor(sorted.length/10)),central=sorted.slice(trim,-trim);return central.reduce((sum,v)=>sum+v,0)/central.length;}
function robustSpan(values){const sorted=[...values].sort((a,b)=>a-b);return sorted[Math.floor((sorted.length-1)*.9)]-sorted[Math.floor((sorted.length-1)*.1)];}
calibrationFeed=(sample,now)=>{
  if(!['gyro','faces'].includes(cal.stage))return;
  if(cal.stage==='faces'&&!cal.selected)return;
  if(cal.stage==='faces'&&cal.turn&&now-cal.turn.started<cal.turn.duration){cal.window=[];return;}
  if(cal.stage==='faces')cal.feedback=`Holding ${cal.selected} upward while its tile fills…`;
  if(cal.window.length&&now-cal.window.at(-1).time>.15)cal.window=[];
  const magnitude=Math.hypot(...sample.a),limit=cal.stage==='gyro'?6.5:10;
  if(magnitude<.72||magnitude>1.28||Math.max(...sample.g.map(Math.abs))>limit){cal.window=[];cal.feedback='Pause the virtual board while its tile fills.';return;}
  cal.window.push({time:now,a:sample.a,g:sample.g});if(cal.window.length>90)cal.window.shift();
  const duration=cal.window.at(-1).time-cal.window[0].time,required=cal.stage==='gyro'?1.5:cal.hold,count=cal.stage==='gyro'?35:22;
  if(duration<required||cal.window.length<count)return;
  const stable=[0,1,2].every(i=>robustSpan(cal.window.map(s=>s.a[i]))<=.18&&robustSpan(cal.window.map(s=>s.g[i]))<=(cal.stage==='gyro'?4.5:7));
  if(!stable){cal.window=[];cal.feedback='Hold this virtual orientation a little longer.';return;}
  if(cal.stage==='gyro'){
    cal.gyro=[0,1,2].map(i=>trimmedMean(cal.window.map(s=>s.g[i])));cal.stage='faces';turnVirtualBoard(cal.order[0]);
  }else{
    cal.faces[cal.selected]=[0,1,2].map(i=>trimmedMean(cal.window.map(s=>s.a[i])));
    cal.lastFace=cal.selected;cal.selected=null;
    const next=cal.order.find(face=>!cal.faces[face]);if(next)turnVirtualBoard(next);
    if(Object.keys(cal.faces).length===6){cal.stage='ready';cal.feedback='All six virtual faces passed the checks. Save the simulated profile to apply it to the demo.';}
  }
  cal.window=[];refreshCalibration();
};
function startSimCalibration(){
  if(recording.active||state.replay||state.paused||faultBusy())return;
  if(!state.connected)byId('connect').click();
  if(state.interval!==30)setStreamRate(30);
  Object.assign(cal,{stage:'gyro',selected:null,lastFace:'+Z',turn:null,order:randomFaceOrder(cal.order),window:[],faces:{},gyro:null,enabled:true,saved:false,feedback:'Virtual board is held still. Capturing gyro bias…'});
  log('SYS','SIMULATED CALIBRATION STARTED · VIRTUAL MPU6050 ONLY');updateStreamControls();refreshCalibration();
}
calibrationCancel=message=>{
  if(!calibrationBusy())return;
  cal.stage='idle';cal.selected=null;cal.window=[];cal.feedback=message||'Simulated calibration stopped. The previous saved profile is unchanged.';
  log('SYS','SIMULATED CALIBRATION STOPPED');updateStreamControls();refreshCalibration();
};
function saveSimCalibration(){
  if(cal.stage!=='ready')return;
  const offset=[],scale=[];
  for(let i=0;i<3;i++){
    const axis='XYZ'[i],high=cal.faces['+'+axis][i],low=cal.faces['-'+axis][i],span=high-low;
    if(span<1.65||span>2.35||Math.abs((high+low)/2)>.22){cal.feedback=`${axis} failed validation. Start over and capture all faces.`;refreshCalibration();return;}
    offset.push((high+low)/2);scale.push(2/span);
  }
  cal.profile={simulated:true,device_key:'browser:virtual-mpu6050',created_at:new Date().toISOString(),sensor_model:'Virtual MPU6050',gyro_bias:cal.gyro,accel_offset:offset,accel_scale:scale,faces_completed:6};
  cal.stage='idle';cal.saved=true;cal.selected=null;cal.feedback='Simulated profile saved and applied to the browser demo. No hardware sensor was calibrated.';
  try{localStorage.setItem(calStoreKey,JSON.stringify(cal.profile));}catch{cal.feedback='Simulated profile applied for this tab. Browser storage is unavailable.';}
  resetAttitude();log('SYS','SIMULATED CALIBRATION SAVED · BROWSER DEMO PROFILE ACTIVE');updateStreamControls();refreshCalibration();
}
function refreshCalibration(){
  const busy=calibrationBusy(),blocked=recording.active||state.replay||state.paused||faultBusy();
  const stages={idle:['Ready when you are','Try the calibration workflow with a virtual MPU6050. No physical sensor is connected.'],gyro:['1 / 2 · Brief pause','The virtual board is held still while the short gyro check fills.'],faces:['2 / 2 · Turn the board','The virtual board rotates through all six faces in a random order. Each captured face turns green.'],ready:['Ready to save','Your six virtual faces passed the checks. Save to use these corrections in the demo.']};
  byId('cal-step').textContent=stages[cal.stage][0];byId('cal-instruction').textContent=stages[cal.stage][1];
  byId('cal-start').hidden=busy;byId('cal-start').disabled=blocked;byId('cal-start').textContent=cal.profile?'Recalibrate simulated sensor':'Start simulated calibration';
  byId('cal-save').hidden=cal.stage!=='ready';byId('cal-save').disabled=blocked;
  byId('cal-restart').hidden=!busy;byId('cal-restart').disabled=blocked;
  byId('cal-faces').hidden=!['faces','ready'].includes(cal.stage);
  const fraction=cal.window.length?Math.min(1,(cal.window.at(-1).time-cal.window[0].time)/(cal.stage==='gyro'?1.5:cal.hold),cal.window.length/(cal.stage==='gyro'?35:22)):0;
  const completed=(cal.gyro?1:0)+Object.keys(cal.faces).length;
  byId('cal-progress').value=cal.saved&&!busy?100:(completed+fraction)/7*100;
  document.querySelectorAll('[data-cal-face]').forEach(button=>{
    const face=button.dataset.calFace,done=!!cal.faces[face];button.disabled=cal.stage!=='faces'||done||blocked;
    button.textContent=done?`${face} ✓`:cal.selected===face?`${face} · ${Math.round(fraction*100)}%`:face;
    button.setAttribute('aria-label',`${face} face: ${done?'captured':cal.selected===face?'capturing':'waiting'}`);
    button.classList.toggle('done',done);button.classList.toggle('capturing',cal.selected===face);button.setAttribute('aria-pressed',String(cal.selected===face||done));
  });
  byId('cal-feedback').textContent=recording.active?'Stop recording before simulated calibration.':state.replay?'Switch to the demo to try simulated calibration.':state.paused?'Resume telemetry to try simulated calibration.':faultBusy()?'Finish fault recovery before simulated calibration.':cal.feedback;
  const vector=values=>values.map(v=>(v>=0?'+':'')+v.toFixed(4)).join(' / ');
  byId('cal-profile-text').textContent=cal.profile?`SIMULATED PROFILE · Virtual MPU6050 · 6 faces\nGyro bias: ${vector(cal.profile.gyro_bias)}\nAcceleration offset: ${vector(cal.profile.accel_offset)}\nAcceleration scale: ${vector(cal.profile.accel_scale)}\nApplied only to this browser’s simulated sensor. Hardware and imported desktop sessions are unaffected.`:'No simulated calibration profile is saved.';
}
byId('cal-start').onclick=startSimCalibration;
byId('cal-save').onclick=saveSimCalibration;
byId('cal-restart').onclick=()=>{calibrationCancel('Starting over.');startSimCalibration();};
document.querySelectorAll('[data-cal-face]').forEach(button=>button.onclick=()=>{
  if(cal.stage!=='faces'||cal.faces[button.dataset.calFace])return;
  turnVirtualBoard(button.dataset.calFace);refreshCalibration();
});
calibrationDialog.addEventListener('close',()=>calibrationCancel());
refreshCalibration();
