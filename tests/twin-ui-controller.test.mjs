import test from 'node:test';
import assert from 'node:assert/strict';

import * as twinUi from '../src/uart_fixture_kit/assets/twin_ui.mjs';
import { stateAt, project } from '../src/uart_fixture_kit/assets/twin_ui.mjs';

function makeMockBank() {
  const baseScenario = (id, faults) => ({
    id,
    faults,
    frames: 12,
    end_ms: 1000,
    stale_after_ms: 300,
    unit: 'C',
    data: 'synthetic',
    sha256: `sha256-scenario-${id}`,
    injections: id === 4 ? [{ kind: 'delay', seq: 5, at_ms: 500 }] : [
      { kind: 'drop', seq: 4, at_ms: 100 },
      { kind: 'corrupt', seq: 6, at_ms: 200 },
      { kind: 'delay', seq: 5, at_ms: 500 },
    ],
    timeline: [
      {
        at_ms: 2,
        messages: 1,
        checksum_errors: 0,
        sequence_anomalies: 0,
        diagnostics: 0,
        late_ignored: 0,
        last_sample: { seq: 0, value: 22.0, unit: 'C', origin_ms: 0, received_ms: 2 },
      },
      {
        at_ms: 852,
        messages: 9,
        checksum_errors: 1,
        sequence_anomalies: 1,
        diagnostics: 2,
        late_ignored: 1,
        last_sample: { seq: 8, value: 24.8, unit: 'C', origin_ms: 800, received_ms: 802 },
      },
    ],
    records: [
      {
        received_ms: 2,
        origin_ms: 0,
        latency_ms: 2,
        displayed: true,
        record: { kind: 'message', seq: 0, value: 22.0, unit: 'C' },
      },
      {
        received_ms: 602,
        record: { kind: 'diagnostic', code: 'bad_checksum' },
      },
      {
        received_ms: 852,
        origin_ms: 500,
        latency_ms: 352,
        displayed: false,
        record: { kind: 'message', seq: 5, value: 23.75, unit: 'C' },
      },
    ],
  });

  const scenarios = [];
  for (let id = 0; id < 8; id++) {
    scenarios.push(baseScenario(id, {
      drop: Boolean(id & 1),
      corrupt: Boolean(id & 2),
      delay: Boolean(id & 4),
    }));
  }
  return {
    schema_version: 1,
    parser_sha256: 'parser-sha256-test',
    scenarios,
  };
}

function makeMockEnvironment() {
  const elements = new Map();
  const rafQueue = [];
  let nextRafId = 1;
  const createdUrls = [];
  const revokedUrls = [];
  const timeouts = [];
  let lastCreatedLink = null;

  function makeElement(id) {
    const listeners = new Map();
    const el = {
      id,
      textContent: '',
      className: '',
      value: id === 'speed' ? '1' : '0',
      max: '1000',
      checked: false,
      width: 800,
      height: 320,
      rows: [],
      href: '',
      download: '',
      clicked: false,
      capturedPointer: null,
      addEventListener(type, fn) {
        if (!listeners.has(type)) listeners.set(type, []);
        listeners.get(type).push(fn);
      },
      dispatch(type, event = {}) {
        for (const fn of listeners.get(type) || []) fn(event);
      },
      getBoundingClientRect() {
        return { width: 800, height: 320 };
      },
      setPointerCapture(pointerId) {
        el.capturedPointer = pointerId;
      },
      getContext() {
        return {
          setTransform() {},
          clearRect() {},
          beginPath() {},
          moveTo() {},
          lineTo() {},
          closePath() {},
          stroke() {},
          fill() {},
          fillText() {},
          strokeStyle: '',
          fillStyle: '',
          lineWidth: 1,
          font: '',
        };
      },
      replaceChildren() {
        el.rows = [];
      },
      insertRow() {
        const row = {
          cells: [],
          insertCell() {
            const cell = { colSpan: 1, textContent: '' };
            row.cells.push(cell);
            return cell;
          },
        };
        el.rows.push(row);
        return row;
      },
      click() {
        el.clicked = true;
      },
    };
    return el;
  }

  const docListeners = new Map();
  const winListeners = new Map();

  const mockDocument = {
    hidden: false,
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, makeElement(id));
      return elements.get(id);
    },
    createElement(tag) {
      const link = makeElement(`created-${tag}`);
      lastCreatedLink = link;
      return link;
    },
    addEventListener(type, fn) {
      if (!docListeners.has(type)) docListeners.set(type, []);
      docListeners.get(type).push(fn);
    },
    dispatch(type, event = {}) {
      for (const fn of docListeners.get(type) || []) fn(event);
    },
  };

  const mockWindow = {
    devicePixelRatio: 2,
    addEventListener(type, fn) {
      if (!winListeners.has(type)) winListeners.set(type, []);
      winListeners.get(type).push(fn);
    },
    dispatch(type, event = {}) {
      for (const fn of winListeners.get(type) || []) fn(event);
    },
    requestAnimationFrame(cb) {
      const id = nextRafId++;
      rafQueue.push({ id, cb });
      return id;
    },
    URL: {
      createObjectURL(blob) {
        const url = `blob:mock-${createdUrls.length}`;
        createdUrls.push({ url, blob });
        return url;
      },
      revokeObjectURL(url) {
        revokedUrls.push(url);
      },
    },
    setTimeout(fn, delay) {
      timeouts.push({ fn, delay });
      return timeouts.length;
    },
  };

  // Pre-populate standard IDs expected by startTwin
  for (const id of [
    'scene', 'parser-hash', 'status', 'value', 'seq', 'age', 'latency',
    'messages', 'checksum', 'anomalies', 'late',
    'injected-drop', 'injected-corrupt', 'injected-delay',
    'clock', 'seek', 'speed', 'play', 'restart', 'hash', 'log',
    'drop', 'corrupt', 'delay', 'late-example',
    'rotate-left', 'rotate-right', 'reset-camera', 'toggle-view', 'export',
  ]) {
    mockDocument.getElementById(id);
  }

  return {
    document: mockDocument,
    window: mockWindow,
    elements,
    rafQueue,
    createdUrls,
    revokedUrls,
    timeouts,
    getLastCreatedLink: () => lastCreatedLink,
    stepRaf(timestamp) {
      assert.ok(rafQueue.length > 0, 'Expected queued RAF callback');
      const next = rafQueue.shift();
      next.cb(timestamp);
    },
  };
}

test('stateAt rejects non-finite or non-numeric time inputs and handles dense timelines', () => {
  const timeline = Array.from({ length: 150 }, (_, idx) => ({
    at_ms: (idx + 1) * 10,
    messages: idx + 1,
    checksum_errors: 0,
    sequence_anomalies: 0,
    diagnostics: 0,
    late_ignored: 0,
    last_sample: { seq: idx, value: 20 + idx * 0.1, unit: 'C', origin_ms: idx * 10, received_ms: (idx + 1) * 10 },
  }));
  const injections = Array.from({ length: 50 }, (_, idx) => ({
    at_ms: idx * 30,
    kind: idx % 3 === 0 ? 'drop' : idx % 3 === 1 ? 'corrupt' : 'delay',
    seq: idx,
  }));
  const denseScenario = { end_ms: 2000, stale_after_ms: 300, timeline, injections, records: [] };

  for (const bad of [Number.NEGATIVE_INFINITY, '100', null, undefined, true, false]) {
    assert.throws(() => stateAt(denseScenario, /** @type {any} */ (bad)), TypeError);
  }

  const mid = stateAt(denseScenario, 755);
  assert.equal(mid.messages, 75);
  assert.equal(mid.last_sample.seq, 74);
  assert.equal(mid.status, 'fresh');
  assert.equal(mid.injected.drop + mid.injected.corrupt + mid.injected.delay, 26);
});

test('upperBoundBy, visibleRecordsAt, and formatRecordRow slice and format log entries in O(log R)', () => {
  assert.equal(typeof twinUi.upperBoundBy, 'function');
  assert.equal(typeof twinUi.visibleRecordsAt, 'function');
  assert.equal(typeof twinUi.formatRecordRow, 'function');

  const bank = makeMockBank();
  const scen = bank.scenarios[4];
  assert.equal(twinUi.visibleRecordsAt(scen, 1).length, 0);
  assert.equal(twinUi.visibleRecordsAt(scen, 602).length, 2);
  assert.equal(twinUi.visibleRecordsAt(scen, 1000, 2).length, 2);

  assert.deepEqual(twinUi.formatRecordRow(scen.records[0]), ['2', 'seq 0, 22.00 °C', '0', 'displayed']);
  assert.deepEqual(twinUi.formatRecordRow(scen.records[1]), ['602', 'bad_checksum', '--', 'diagnostic']);
  assert.deepEqual(twinUi.formatRecordRow(scen.records[2]), ['852', 'seq 5, 23.75 °C', '500', 'held back: older sample']);
});

test('startTwin avoids idle 60 Hz RAF spin while paused and drives full UI lifecycle deterministically', () => {
  assert.equal(typeof twinUi.startTwin, 'function');
  const bank = makeMockBank();
  const env = makeMockEnvironment();

  twinUi.startTwin(bank, { document: env.document, window: env.window });

  // Must NOT schedule requestAnimationFrame while paused at startup
  assert.equal(env.rafQueue.length, 0);
  assert.equal(env.elements.get('parser-hash').textContent, 'parser-sha256-test');
  assert.equal(env.elements.get('status').textContent, 'Waiting for data');
  assert.equal(env.elements.get('log').rows[0].cells[0].textContent, 'No decoded records yet.');

  // Play schedules exactly one RAF; stepping advances time and stops RAF at end_ms
  env.elements.get('play').dispatch('click');
  assert.equal(env.elements.get('play').textContent, 'Pause replay');
  assert.equal(env.rafQueue.length, 1);

  env.stepRaf(1000); // establishes lastFrame = 1000
  assert.equal(env.rafQueue.length, 1);

  env.elements.get('speed').value = '2';
  env.stepRaf(1100); // advances +200 ms -> time = 200
  assert.equal(env.elements.get('clock').textContent, '0.200 s');
  assert.equal(env.elements.get('status').textContent, 'Fresh sample');
  assert.equal(env.rafQueue.length, 1);

  env.stepRaf(1600); // advances +1000 ms -> clamps to end_ms = 1000 and auto-pauses
  assert.equal(env.elements.get('clock').textContent, '1.000 s');
  assert.equal(env.elements.get('play').textContent, 'Play replay');
  assert.equal(env.rafQueue.length, 0); // Zero idle RAF after playback finishes!

  // Clicking play at end_ms rewinds to 0 and plays; visibilitychange hidden auto-pauses
  env.elements.get('play').dispatch('click');
  assert.equal(env.elements.get('clock').textContent, '0.000 s');
  assert.equal(env.rafQueue.length, 1);
  env.document.hidden = true;
  env.document.dispatch('visibilitychange');
  env.stepRaf(2000); // drained callback sees playing === false and schedules NO further RAF
  assert.equal(env.rafQueue.length, 0);

  // Late arrival inspection, seek, restart, fault switches, camera, 2D toggle, and export
  env.elements.get('late-example').dispatch('click');
  assert.equal(env.elements.get('delay').checked, true);
  assert.equal(env.elements.get('clock').textContent, '0.852 s');
  assert.equal(env.elements.get('late').textContent, '1');
  assert.equal(env.elements.get('log').rows.length, 3);

  env.elements.get('seek').value = '500';
  env.elements.get('seek').dispatch('input');
  assert.equal(env.elements.get('clock').textContent, '0.500 s');

  env.elements.get('rotate-left').dispatch('click');
  env.elements.get('rotate-right').dispatch('click');
  env.elements.get('reset-camera').dispatch('click');

  const scene = env.elements.get('scene');
  scene.dispatch('pointerdown', { clientX: 100, clientY: 100, pointerId: 7 });
  assert.equal(scene.capturedPointer, 7);
  scene.dispatch('pointermove', { clientX: 140, clientY: 130 });
  scene.dispatch('pointerup');

  // Toggle 2D flat mode ignores pointerdown drag
  env.elements.get('toggle-view').dispatch('click');
  assert.equal(env.elements.get('toggle-view').textContent, '3D view');
  scene.capturedPointer = null;
  scene.dispatch('pointerdown', { clientX: 50, clientY: 50, pointerId: 9 });
  assert.equal(scene.capturedPointer, null);

  env.window.dispatch('resize');
  env.elements.get('restart').dispatch('click');
  assert.equal(env.elements.get('clock').textContent, '0.000 s');

  env.elements.get('export').dispatch('click');
  assert.equal(env.createdUrls.length, 1);
  assert.equal(env.getLastCreatedLink().download, 'uart-twin-scenario-4.json');
  assert.equal(env.getLastCreatedLink().clicked, true);
  env.timeouts[0].fn();
  assert.deepEqual(env.revokedUrls, ['blob:mock-0']);
});
