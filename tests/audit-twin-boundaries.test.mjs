import test from 'node:test';
import assert from 'node:assert/strict';

import { stateAt, project } from '../src/uart_fixture_kit/assets/twin_ui.mjs';

test('stateAt handles empty timeline, exact stale_after_ms boundary, and cumulative multi-kind injections', () => {
  const scenario = {
    end_ms: 1000,
    stale_after_ms: 250,
    injections: [
      { at_ms: 100, kind: 'drop' },
      { at_ms: 200, kind: 'corrupt' },
      { at_ms: 350, kind: 'delay' },
      { at_ms: 350, kind: 'drop' }
    ],
    timeline: [
      {
        at_ms: 120,
        messages: 1,
        checksum_errors: 0,
        sequence_anomalies: 0,
        diagnostics: 0,
        late_ignored: 0,
        last_sample: { seq: 0, value: 24.5, origin_ms: 100, received_ms: 120 }
      }
    ]
  };

  assert.throws(() => stateAt(scenario, Number.NaN), TypeError);
  assert.throws(() => stateAt(scenario, Number.POSITIVE_INFINITY), TypeError);

  const beforeFirst = stateAt(scenario, 100);
  assert.equal(beforeFirst.status, 'waiting');
  assert.equal(beforeFirst.age, null);
  assert.deepEqual(beforeFirst.injected, { drop: 1, corrupt: 0, delay: 0 });

  // age = 349 - 100 = 249 (< 250) -> fresh
  const justFresh = stateAt(scenario, 349);
  assert.equal(justFresh.status, 'fresh');
  assert.equal(justFresh.age, 249);
  assert.deepEqual(justFresh.injected, { drop: 1, corrupt: 1, delay: 0 });

  // age = 350 - 100 = 250 (=== stale_after_ms) -> stale
  const exactStale = stateAt(scenario, 350);
  assert.equal(exactStale.status, 'stale');
  assert.equal(exactStale.age, 250);
  assert.deepEqual(exactStale.injected, { drop: 2, corrupt: 1, delay: 1 });
});

test('project flat mode preserves z depth and 3D mode matches perspective center invariants', () => {
  const flatOrigin = project([0, 0, 1.25], -0.65, 0.42, 800, 320, true);
  assert.deepEqual(flatOrigin, [400, 320 * 0.68, 1.25]);

  const perspOrigin = project([0, 0, 0], 0, 0, 800, 320, false);
  assert.deepEqual(perspOrigin, [400, 320 * 0.64, 0]);
});
