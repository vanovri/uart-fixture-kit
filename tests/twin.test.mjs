import test from 'node:test';
import assert from 'node:assert/strict';
import {stateAt, project} from '../src/uart_fixture_kit/assets/twin_ui.mjs';

const scenario = {end_ms:1000, stale_after_ms:300, injections:[{at_ms:50,kind:'drop'}], timeline:[
  {at_ms:2,messages:1,last_sample:{seq:0,origin_ms:0,value:22}},
  {at_ms:852,messages:9,late_ignored:1,last_sample:{seq:8,origin_ms:800,value:24.8}},
]};
test('waiting, exact arrival boundary, and generation-based freshness',()=>{
  assert.equal(stateAt(scenario,1).status,'waiting');
  assert.equal(stateAt(scenario,2).status,'fresh');
  assert.equal(stateAt(scenario,299).status,'fresh');
  assert.equal(stateAt(scenario,300).status,'stale');
  assert.equal(stateAt(scenario,852).last_sample.seq,8);
  assert.equal(stateAt(scenario,852).age,52);
});
test('seek clamp, injection boundary, and immutable selection',()=>{
  assert.equal(stateAt(scenario,-10).time,0);
  assert.equal(stateAt(scenario,2000).time,1000);
  assert.equal(stateAt(scenario,49).injected.drop,0);
  assert.equal(stateAt(scenario,50).injected.drop,1);
  assert.throws(()=>stateAt(scenario,NaN),TypeError);
  assert.throws(()=>stateAt(scenario,Infinity),TypeError);
});
test('2D and projected 3D remain finite at supported camera limits',()=>{
  for(const pitch of [-.2,.42,.9])for(const yaw of [-100,0,100])for(const flat of [false,true]){
    const p=project([1.6,1.5,1.6],yaw,pitch,800,320,flat);
    assert.equal(p.length,3); assert.ok(p.every(Number.isFinite));
  }
  assert.deepEqual(project([0,0,0],0,0,800,320,true),[400,217.60000000000002,0]);
});
