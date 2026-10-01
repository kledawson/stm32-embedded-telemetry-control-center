// Browser simulation of DemoWorker faults; no serial access or firmware commands.
const fault={selected:'checksum',kind:null,phase:'idle',started:0,duration:0,lastBefore:null,firstAfter:null,
  frames:[],rejected:0,resetReason:'—',packet:null,lines:[],timer:null,animationStart:null,animationDuration:0,flowKey:''};
const faultScenarios={
  checksum:{title:'Bit flip + checksum',description:"One bit changes after a simulated packet’s checksum is calculated. The parser rejects it."},
  watchdog:{title:'Watchdog reset',description:'Simulated telemetry stops feeding the watchdog. The model reboots and packets return.'},
  mutex:{title:'UART mutex contention',description:'A simulated status task holds the UART lock. Packets wait and then resume.'}
};
const faultDialog=byId('fault-dialog');
const checksumPayload=payload=>[...payload].reduce((sum,c)=>sum^c.charCodeAt(0),0)&255;
const hex=value=>'0x'+value.toString(16).toUpperCase().padStart(2,'0');
function packetPayload(sample){return ['AX','AY','AZ','GX','GY','GZ'].map((name,i)=>`${name}:${[...sample.a,...sample.g][i].toFixed(i<3?2:1)}`).join('|')+`|SEQ:${state.sequence+1}`;}
function acceptedPacket(payload,checksum){
  const fields=payload.split('|');
  return fields.length===7&&fields.slice(0,6).every((part,i)=>part.startsWith(['AX:','AY:','AZ:','GX:','GY:','GZ:'][i])&&Number.isFinite(Number(part.slice(3))))&&checksumPayload(payload)===checksum;
}
faultBusy=()=>['active','rebooting','recovering'].includes(fault.phase);
function faultEvent(category,message){
  const line=`${new Date().toLocaleTimeString()}  ${category}  ${message}`;
  fault.lines.push(line);if(fault.lines.length>100)fault.lines.shift();
  byId('fault-log').textContent=fault.lines.join('\n');byId('fault-log').scrollTop=byId('fault-log').scrollHeight;
  log(category,message);
}
faultReset=(clear=false)=>{
  if(faultBusy())faultEvent('SYS','DEMO FAULT CANCELLED · SOURCE CHANGED');
  fault.animationStart=null;fault.flowKey='';fault.phase='idle';fault.kind=null;fault.packet=null;fault.frames=[];fault.firstAfter=null;fault.lastBefore=null;
  if(clear){fault.rejected=0;fault.resetReason='—';}
  healthValues[2].textContent=String(fault.rejected);refreshFault();
};
function runFault(){
  if(!state.connected||state.replay||state.paused||faultBusy()||calibrationBusy())return;
  fault.kind=fault.selected;fault.phase='active';fault.started=performance.now()/1000;
  fault.animationStart=fault.kind==='checksum'?fault.started:null;fault.animationDuration=3.1;fault.flowKey='';
  fault.duration=fault.kind==='mutex'?.28+Math.random()*.14:fault.kind==='watchdog'?3:0;
  fault.lastBefore=state.lastAt;fault.firstAfter=null;fault.packet=null;fault.frames=state.frameTimes.slice(-35);
  faultEvent('SYS',`DEMO ${faultScenarios[fault.kind].title.toUpperCase()} · STARTED`);
  if(fault.kind==='watchdog')faultEvent('WARN','DEMO TELEMETRY TASK STALLED · WATCHDOG NOT REFRESHED');
  if(fault.kind==='mutex')faultEvent('WARN','DEMO UART MUTEX HELD · TELEMETRY WAITS');
  updateStreamControls();refreshFault();
}
faultBeforeReceive=(sample,now)=>{
  if(faultBusy()&&fault.kind!=='checksum'){
    if(now-fault.started<fault.duration)return false;
    if(fault.kind==='watchdog'){
      state.start=now;Object.assign(sample,calibrationPrepare(demoSample(0)));fault.resetReason='IWDG (simulated)';
      faultEvent('SYS','DEMO BOOT COMPLETE · RESET REASON IWDG · TELEMETRY RESUMED');
    }else faultEvent('SYS','DEMO UART MUTEX RELEASED · TELEMETRY RESUMED');
    fault.phase='recovering';
  }
  const payload=packetPayload(sample),sent=checksumPayload(payload);
  if(fault.kind==='checksum'&&fault.phase==='active'){
    const index=[...payload].findIndex((c,i)=>i>=3&&/\d/.test(c));
    const changed=String.fromCharCode(payload.charCodeAt(index)^1),damaged=payload.slice(0,index)+changed+payload.slice(index+1);
    const calculated=checksumPayload(damaged);
    if(!acceptedPacket(damaged,sent)){
      fault.packet={original:payload,damaged,sent,calculated,before:payload[index],after:changed};
      fault.rejected++;state.sequence++;fault.phase='recovering';healthValues[2].textContent=String(fault.rejected);
      faultEvent('WARN',`DEMO CHECKSUM MISMATCH · SENT ${hex(sent)} ≠ CALCULATED ${hex(calculated)} · FRAME REJECTED BEFORE PLOTTING`);
      refreshFault();return false;
    }
  }
  if(!acceptedPacket(payload,sent))return false;
  if(fault.phase==='recovering'){
    fault.firstAfter=now;fault.phase='recovered';
    if(fault.packet)fault.packet.next=payload+`|CHK:${hex(sent)}`;
    faultEvent('SYS',fault.kind==='checksum'?'NEXT VALID DEMO PACKET ACCEPTED · STREAM RECOVERED':`DEMO STREAM RECOVERED · ${Math.round((now-fault.lastBefore)*1000)} MS PACKET GAP`);
    updateStreamControls();
  }
  if(fault.kind&&(!fault.firstAfter||now-fault.firstAfter<.7)){
    fault.frames.push(now);if(fault.frames.length>600)fault.frames.shift();
  }
  return true;
};
function flowCard(title,primary,note){
  const card=document.createElement('div'),heading=document.createElement('h4'),value=document.createElement('strong'),detail=document.createElement('p');
  heading.textContent=title;value.textContent=primary;detail.textContent=note;card.append(heading,value,detail);return card;
}
function animationProgress(){
  return fault.animationStart===null?1:Math.min(1,Math.max(0,(performance.now()/1000-fault.animationStart)/fault.animationDuration));
}
function drawFaultTimeline(){
  const svg=byId('fault-timeline');svg.replaceChildren();
  const ns='http://www.w3.org/2000/svg';
  const add=(tag,attrs,text)=>{const el=document.createElementNS(ns,tag);for(const [key,value]of Object.entries(attrs))el.setAttribute(key,value);if(text)el.textContent=text;svg.append(el);return el;};
  if(!fault.kind){add('text',{x:410,y:85,fill:'#a9bbcb','text-anchor':'middle','font-size':13},'Run the demo to capture packet timing');return;}
  const elapsed=(performance.now()/1000-fault.started)*1000,hold=fault.duration*1000;
  const end=fault.kind==='watchdog'?Math.max(3600,hold+600):Math.max(850,hold+450),start=-450;
  const x=ms=>100+(ms-start)/(end-start)*690;
  for(let ms=0;ms<=end;ms+=fault.kind==='watchdog'?600:150){add('line',{x1:x(ms),y1:25,x2:x(ms),y2:130,stroke:'#283846'});add('text',{x:x(ms),y:148,fill:'#a9bbcb','text-anchor':'middle','font-size':11},`${ms}`);}
  add('text',{x:8,y:50,fill:'#a9bbcb','font-size':11},fault.kind==='mutex'?'StatusTask':'Watchdog');
  add('text',{x:8,y:107,fill:'#a9bbcb','font-size':11},'Valid demo');
  add('line',{x1:100,y1:104,x2:790,y2:104,stroke:'#283846'});
  const until=fault.firstAfter?hold:Math.min(hold,elapsed);
  add('rect',{x:x(0),y:33,width:Math.max(1,x(until)-x(0)),height:28,rx:4,fill:'#b9854f'});
  if(until>200)add('text',{x:x(until/2),y:52,fill:'#101820','text-anchor':'middle','font-size':11},fault.kind==='mutex'?'MUTEX HELD':'NO TELEMETRY');
  for(const stamp of fault.frames){const ms=(stamp-fault.started)*1000;if(ms>=start&&ms<=end)add('circle',{cx:x(ms),cy:104,r:3.5,fill:'#55b7cd'});}
  if(fault.firstAfter){const ms=(fault.firstAfter-fault.started)*1000;add('line',{x1:x(ms),y1:22,x2:x(ms),y2:130,stroke:'#86d8a6','stroke-dasharray':'4 4'});}
  add('text',{x:445,y:167,fill:'#a9bbcb','text-anchor':'middle','font-size':11},'Milliseconds from simulated fault command · dots are accepted frames');
}
function refreshFault(){
  if(!faultDialog.open)return;
  const selected=faultScenarios[fault.selected],kind=fault.kind||fault.selected,ready=state.connected&&!state.replay;
  byId('fault-scenario').textContent=selected.title;byId('fault-description').textContent=selected.description;
  byId('fault-start').hidden=ready;byId('fault-start').disabled=state.replay;
  const progress=animationProgress(),animating=fault.kind==='checksum'&&fault.animationStart!==null&&progress<1;
  const elapsed=fault.animationStart===null?Infinity:performance.now()/1000-fault.animationStart;
  const capture=animating&&elapsed<.7;
  const reveal=animating?Math.max(0,Math.min(1,(elapsed-.7)/2.4)):1;
  const stage=kind==='checksum'?(capture?0:reveal<.28?1:reveal<.62?2:animating?3:4):(capture?0:animating?1:2);
  byId('fault-trigger').disabled=!ready||state.paused||faultBusy()||calibrationBusy()||animating;
  document.querySelectorAll('[data-fault]').forEach(button=>{button.disabled=faultBusy();button.classList.toggle('checked',button.dataset.fault===fault.selected);button.setAttribute('aria-pressed',String(button.dataset.fault===fault.selected));});
  const active=faultBusy()||animating,recovered=fault.phase==='recovered'&&!animating;
  byId('fault-banner').className='fault-banner'+(active?' active':recovered?' recovered':'');
  const titles={checksum:active?'Corrupted packet rejected':'Valid packets streaming again',watchdog:fault.phase==='rebooting'?'Watchdog expired · restarting':active?'Telemetry task stalled':'Demo rebooted · recovered',mutex:active?'UART mutex held':'UART mutex released · recovered'};
  byId('fault-status').textContent=animating?(capture?'Capturing the simulated event':kind==='mutex'?'UART timing measured':'Reviewing captured evidence'):fault.kind?(kind==='watchdog'||faultBusy()?titles[kind]:'Fault verified · stream healthy'):ready?'Demo source ready':'Connect demo data to begin';
  byId('fault-detail').textContent=animating?(capture?'The simulated packet and timing evidence appear as they occur.':kind==='mutex'?'The result is frozen for viewing. Telemetry continues normally.':'Slow-motion display of captured evidence; telemetry is not delayed.'):fault.kind?(kind==='checksum'?'The changed packet is rejected before it reaches the graph. The next valid packet restores the stream.':kind==='mutex'?(fault.phase==='recovered'?'UART mutex released; fresh demo packets are streaming.':'The simulated status task owns the UART lock; telemetry waits until release.'):'The browser models watchdog expiry and restart. No hardware reset occurs.'):'Choose a failure and run the demo.';
  document.querySelectorAll('.fault-steps>span').forEach((el,i)=>el.classList.toggle('selected',i===(animating?(capture?0:1):recovered?2:active?1:0)));
  const cards=byId('fault-flow'),p=fault.packet;
  const flowKey=[kind,fault.phase,animating?stage:'complete',p?.original,fault.duration,fault.firstAfter].join('|');
  if(flowKey!==fault.flowKey){
    fault.flowKey=flowKey;cards.replaceChildren();
    if(kind==='checksum'){
      const original=!!p&&!capture,flipped=original&&reveal>=.28,checked=original&&reveal>=.62,rejected=checked;
      cards.append(flowCard('1 SENSOR FRAME',original?p.original.split('|')[0].replace('AX:','AX: '):'Awaiting sensor packet',original?`Original ${p.before} · CHK ${hex(p.sent)}`:'Source computes checksum'),flowCard('2 BIT FLIP ON UART',flipped?p.damaged.split('|')[0].replace('AX:','AX: '):'Waiting for bit flip',flipped?`${p.before} → ${p.after}`:'One byte changes after CHK'),flowCard('3 HOST CHECK',checked?`${hex(p.sent)} ≠ ${hex(p.calculated)}`:'Waiting for host check',rejected?'REJECTED · never graphed':checked?'Mismatch detected':'Host will recalculate checksum'));
      [...cards.children].forEach((card,i)=>{const shown=[original,flipped,checked][i];card.classList.toggle('evidence-revealed',shown);card.dataset.evidence=i;});
    }else{
      const waiting=animating?stage===1:faultBusy(),returned=animating?stage===2:fault.phase==='recovered';
      cards.append(flowCard(kind==='mutex'?'STATUS TASK':'TELEMETRY TASK',waiting?(kind==='mutex'?'Owns UART lock':'Not feeding IWDG'):'Fresh frames',kind==='mutex'?'Simulated shared UART':'Simulated watchdog feeder'),flowCard(kind==='mutex'?'UART MUTEX':'INDEPENDENT WATCHDOG',waiting?(kind==='mutex'?'Telemetry waits':fault.phase==='rebooting'?'Reset: IWDG':'Countdown active'):returned?(kind==='mutex'?'Lock released':'Reset: IWDG'):'Ready',kind==='mutex'?`${Math.round(fault.duration*1000)} ms actual simulated hold`:'1.8 s expiry · 3 s return'),flowCard('DEMO SOURCE',returned?'Sensor data restored':waiting?'No new packets':'Streaming',fault.firstAfter?`${Math.round((fault.firstAfter-fault.lastBefore)*1000)} ms measured demo gap`:'Browser simulation'));

    }
  }
  cards.hidden=kind==='mutex';
  byId('fault-mutex-metrics').hidden=kind!=='mutex';
  byId('fault-gap-value').textContent=fault.firstAfter?`${Math.round((fault.firstAfter-fault.lastBefore)*1000)} ms`:'—';
  byId('fault-hold-value').textContent=fault.kind==='mutex'?`${Math.round(fault.duration*1000)} ms`:'—';
  byId('fault-packet').hidden=kind!=='checksum';
  const accepted=kind==='checksum'&&p?.next&&!animating;
  byId('fault-packet').classList.toggle('accepted',!!accepted);
  byId('fault-packet').textContent=accepted?`✓ NEXT VALID FRAME · ${p.next.split('|').find(v=>v.startsWith('SEQ:'))} · ${p.next.split('|')[0]} → GRAPH`:'Next valid packet can still pass to the graph';
  byId('fault-proof-status').textContent=!fault.kind?'AWAITING TEST':capture?'Collecting…':kind==='checksum'?(animating?(reveal<.62?'REPLAYING CAPTURE':'FRAME REJECTED'):'NEXT ACCEPTED'):faultBusy()?'LIVE CAPTURE':'STREAM RESUMED';
  byId('fault-proof-heading').textContent=kind==='checksum'?'ONE SIMULATED PACKET · BIT FLIP':kind==='mutex'?'SHARED UART · TASK TIMELINE':'WATCHDOG EFFECT · SIMULATED PACKET GAP';
  byId('fault-timeline').toggleAttribute('hidden',kind==='checksum');if(kind!=='checksum')drawFaultTimeline();
  byId('fault-proof-note').textContent=kind==='checksum'?'CHK is computed before the bit flip. The rejected frame never reaches the graph; the next valid one does.':'Orange = simulated interruption · blue dots = accepted demo packets.';
  const age=performance.now()/1000-state.lastAt;
  byId('fault-link').textContent=ready?'Demo':state.replay?'Replay':'Offline';
  byId('fault-sensor').textContent=ready?(faultBusy()&&kind!=='checksum'?'No new data':state.paused?'Paused':'Streaming'):'—';
  byId('fault-age').textContent=ready?(age>=1?age.toFixed(1)+' s':Math.round(age*1000)+' ms'):'—';
  byId('fault-rejected').textContent=fault.rejected;byId('fault-reset').textContent=fault.resetReason;
}
faultRefresh=refreshFault;
byId('fault-open').onclick=()=>{faultDialog.showModal();refreshFault();};
byId('fault-close').onclick=()=>faultDialog.close();
byId('fault-trigger').onclick=runFault;
byId('fault-start').onclick=()=>{if(!state.connected)byId('connect').click();refreshFault();};
document.querySelectorAll('[data-fault]').forEach(button=>button.onclick=()=>{if(faultBusy())return;faultReset();fault.selected=button.dataset.fault;refreshFault();});
fault.timer=setInterval(()=>{
  if(fault.kind==='watchdog'&&fault.phase==='active'&&performance.now()/1000-fault.started>=1.8){fault.phase='rebooting';faultEvent('WARN','DEMO WATCHDOG EXPIRED · TELEMETRY TASK RESTARTING');}
  refreshFault();
},100);
