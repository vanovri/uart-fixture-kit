// Pure view selection and projection are exported for Node contract tests.
export function stateAt(scenario, milliseconds) {
  if (!Number.isFinite(milliseconds)) throw new TypeError('Replay time must be finite');
  const time = Math.max(0, Math.min(scenario.end_ms, milliseconds));
  let low = 0, high = scenario.timeline.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (scenario.timeline[middle].at_ms <= time) low = middle + 1;
    else high = middle;
  }
  const state = low ? scenario.timeline[low - 1] : {
    messages: 0, checksum_errors: 0, sequence_anomalies: 0,
    diagnostics: 0, late_ignored: 0, last_sample: null,
  };
  const age = state.last_sample ? time - state.last_sample.origin_ms : null;
  const injected = {drop: 0, corrupt: 0, delay: 0};
  for (const event of scenario.injections) if (event.at_ms <= time) injected[event.kind] += 1;
  return {...state, time, age, injected,
    status: age === null ? 'waiting' : age >= scenario.stale_after_ms ? 'stale' : 'fresh'};
}

export function project(point, yaw, pitch, width, height, flat = false) {
  const [x, y, z] = point;
  if (flat) return [width / 2 + x * height * 0.23, height * 0.68 - y * height * 0.23, z];
  const rx = x * Math.cos(yaw) - z * Math.sin(yaw);
  const rz = x * Math.sin(yaw) + z * Math.cos(yaw);
  const ry = y * Math.cos(pitch) - rz * Math.sin(pitch);
  const depth = y * Math.sin(pitch) + rz * Math.cos(pitch);
  const scale = height * 0.27 * 6 / (6 + depth);
  return [width / 2 + rx * scale, height * 0.64 - ry * scale, depth];
}

function startTwin(bank) {
  const $ = id => document.getElementById(id);
  const canvas = $('scene'), context = canvas.getContext('2d');
  let scenario = bank.scenarios[0], time = 0, playing = false, lastFrame = null;
  let yaw = -0.65, pitch = 0.42, flat = false, drag = null, lastLogKey = '';
  let cssWidth = 800, cssHeight = 320;
  const text = (id, value) => {const node = $(id); if (node.textContent !== String(value)) node.textContent = String(value);};
  $('parser-hash').textContent = bank.parser_sha256;

  function resize() {
    const box = canvas.getBoundingClientRect(), ratio = Math.min(window.devicePixelRatio || 1, 2);
    cssWidth = box.width; cssHeight = box.height;
    canvas.width = Math.round(cssWidth * ratio); canvas.height = Math.round(cssHeight * ratio);
    if (context) context.setTransform(ratio, 0, 0, ratio, 0, 0);
    render();
  }
  function line(points, color = '#d3d8cf', thickness = 1) {
    context.beginPath();
    points.forEach((point, i) => {const p = project(point, yaw, pitch, cssWidth, cssHeight, flat); i ? context.lineTo(p[0], p[1]) : context.moveTo(p[0], p[1]);});
    context.strokeStyle = color; context.lineWidth = thickness; context.stroke();
  }
  function cube(x, y, z, sx, sy, sz, palette) {
    const vertices = [[x-sx/2,y,z-sz/2],[x+sx/2,y,z-sz/2],[x+sx/2,y,z+sz/2],[x-sx/2,y,z+sz/2],
      [x-sx/2,y+sy,z-sz/2],[x+sx/2,y+sy,z-sz/2],[x+sx/2,y+sy,z+sz/2],[x-sx/2,y+sy,z+sz/2]];
    const projected = vertices.map(v => project(v,yaw,pitch,cssWidth,cssHeight,flat));
    const faces = [[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7],[4,5,6,7]];
    faces.map((face,index)=>({face,index,depth:face.reduce((sum,v)=>sum+projected[v][2],0)/4}))
      .sort((a,b)=>b.depth-a.depth).forEach(({face,index})=>{
        context.beginPath();face.forEach((v,i)=>i?context.lineTo(projected[v][0],projected[v][1]):context.moveTo(projected[v][0],projected[v][1]));
        context.closePath();context.fillStyle=palette[index % palette.length];context.fill();
        context.strokeStyle='#78917b';context.lineWidth=.8;context.stroke();
      });
  }
  function drawScene(state) {
    if (!context) return;
    context.clearRect(0,0,cssWidth,cssHeight);
    for(let i=-4;i<=4;i++){
      const t=i*.4;line([[-1.6,-.65,t],[1.6,-.65,t]]);line([[t,-.65,-1.6],[t,-.65,1.6]]);
    }
    line([[-1.6,-.65,0],[1.6,-.65,0]],'#a8b5a6',1.4);
    cube(0,-.65,0,1.25,.13,.85,['#c3cbc0','#aebcae','#cad2c7','#b5c1b0','#dce3d8']);
    const value=state.last_sample?.value;
    const height = value === undefined ? 0.16 : 0.25 + (Math.max(20,Math.min(32,value))-20)/12*1.7;
    const palette=state.status==='fresh'?['#8eae86','#668d62','#80a478','#6c9566','#bdd6b3']:
      state.status==='stale'?['#b6b092','#938d70','#c6c0a4','#aaa385','#dbd5bd']:['#c1c7be','#aab4a8','#bec8ba','#aebda9','#e1e7dc'];
    cube(0,-.52,0,.68,height,.47,palette);
    line([[1.17,-.52,0],[1.17,1.43,0]],'#94a18e',1.1);
    context.fillStyle='#55644f';context.font='11px "Segoe UI",sans-serif';
    for(const [n,y]of [[20,-.27],[26,.58],[32,1.43]]){
      line([[1.11,y,0],[1.23,y,0]],'#94a18e');const p=project([1.32,y,0],yaw,pitch,cssWidth,cssHeight,flat);context.fillText(`${n} °C`,p[0],p[1]+4);
    }
    const p=project([-.55,-.8,.6],yaw,pitch,cssWidth,cssHeight,flat);
    context.fillText('SYNTHETIC / TEMPERATURE',p[0]-35,p[1]+18);
  }
  function renderLog() {
    const visible=scenario.records.filter(r=>r.received_ms<=time).slice(-12);
    const key=`${scenario.id}:${visible.map(r=>`${r.received_ms}:${r.record.kind}:${r.record.seq??r.record.code}`).join(',')}`;
    if(key===lastLogKey)return;lastLogKey=key;
    const body=$('log');body.replaceChildren();
    if(!visible.length){const row=body.insertRow(),cell=row.insertCell();cell.colSpan=4;cell.textContent='No decoded records yet.';return;}
    for(const item of visible.reverse()){
      const row=body.insertRow();
      const description=item.record.kind==='message'?`seq ${item.record.seq}, ${item.record.value.toFixed(2)} °C`:item.record.code;
      const decision=item.record.kind==='diagnostic'?'diagnostic':item.displayed?'displayed':'held back: older sample';
      for(const value of [item.received_ms,description,item.origin_ms??'--',decision])row.insertCell().textContent=String(value);
    }
  }
  function render() {
    const state=stateAt(scenario,time),sample=state.last_sample;
    text('value',sample?sample.value.toFixed(2):'--');text('seq',sample?sample.seq:'--');
    text('age',state.age===null?'--':`${Math.round(state.age)} ms`);
    text('latency',sample?`${sample.received_ms-sample.origin_ms} ms`:'--');
    const label=state.status==='fresh'?'Fresh sample':state.status==='stale'?'Stale / holding last value':'Waiting for data';
    text('status',label);$('status').className=`status ${state.status}`;
    for(const [id,key]of [['messages','messages'],['checksum','checksum_errors'],['anomalies','sequence_anomalies'],['late','late_ignored']])text(id,state[key]);
    for(const kind of ['drop','corrupt','delay'])text(`injected-${kind}`,state.injected[kind]);
    text('clock',`${(time/1000).toFixed(3)} s`);$('seek').value=String(Math.round(time));
    text('play',playing?'Pause replay':'Play replay');text('hash',scenario.sha256);
    renderLog();drawScene(state);
  }
  function pause(){playing=false;lastFrame=null;render();}
  function choose() {
    pause();const id=Number($('drop').checked)+2*Number($('corrupt').checked)+4*Number($('delay').checked);
    scenario=bank.scenarios.find(s=>s.id===id);time=0;lastLogKey='';$('seek').max=String(scenario.end_ms);render();
  }
  for(const kind of ['drop','corrupt','delay'])$(kind).addEventListener('change',choose);
  $('play').addEventListener('click',()=>{if(time>=scenario.end_ms)time=0;playing=!playing;lastFrame=null;render();});
  $('restart').addEventListener('click',()=>{time=0;pause();});
  $('seek').addEventListener('input',()=>{time=Number($('seek').value);pause();});
  $('late-example').addEventListener('click',()=>{
    $('drop').checked=false;$('corrupt').checked=false;$('delay').checked=true;choose();time=852;render();
  });
  $('rotate-left').addEventListener('click',()=>{yaw-=.18;render();});
  $('rotate-right').addEventListener('click',()=>{yaw+=.18;render();});
  $('reset-camera').addEventListener('click',()=>{yaw=-.65;pitch=.42;render();});
  $('toggle-view').addEventListener('click',()=>{flat=!flat;text('toggle-view',flat?'3D view':'2D view');render();});
  canvas.addEventListener('pointerdown',event=>{if(flat)return;drag={x:event.clientX,y:event.clientY};canvas.setPointerCapture(event.pointerId);});
  canvas.addEventListener('pointermove',event=>{if(!drag)return;yaw+=(event.clientX-drag.x)*.008;pitch=Math.max(-.2,Math.min(.9,pitch+(event.clientY-drag.y)*.006));drag={x:event.clientX,y:event.clientY};render();});
  const stopDrag=()=>{drag=null;};canvas.addEventListener('pointerup',stopDrag);canvas.addEventListener('pointercancel',stopDrag);
  $('export').addEventListener('click',()=>{
    const url=URL.createObjectURL(new Blob([JSON.stringify(scenario,null,2)],{type:'application/json'}));
    const link=document.createElement('a');link.href=url;link.download=`uart-twin-scenario-${scenario.id}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  document.addEventListener('visibilitychange',()=>{if(document.hidden)pause();});
  function tick(timestamp){
    if(playing){if(lastFrame!==null)time=Math.min(scenario.end_ms,time+(timestamp-lastFrame)*Number($('speed').value));lastFrame=timestamp;if(time>=scenario.end_ms)playing=false;render();}
    requestAnimationFrame(tick);
  }
  window.addEventListener('resize',resize);
  $('seek').max=String(scenario.end_ms);
  resize();requestAnimationFrame(tick);
}

if(typeof document!=='undefined'){
  const bank=JSON.parse(document.getElementById('twin-data').textContent);
  startTwin(bank);
}
