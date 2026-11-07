// @ts-check
// Pure view selection, binary-search indexing, and projection are exported for Node contract tests.

/**
 * @typedef {{ seq: number, value: number, unit?: string, origin_ms: number, received_ms?: number }} SampleSnapshot
 * @typedef {{
 *   at_ms: number,
 *   messages: number,
 *   checksum_errors?: number,
 *   sequence_anomalies?: number,
 *   diagnostics?: number,
 *   late_ignored?: number,
 *   last_sample: SampleSnapshot | null
 * }} TimelineEntry
 * @typedef {'drop' | 'corrupt' | 'delay'} FaultKind
 * @typedef {{ at_ms: number, kind: FaultKind, seq?: number }} InjectionEvent
 * @typedef {{ kind: 'message', seq: number, value: number, unit?: string } | { kind: 'diagnostic', code: string, seq?: number, previous?: number }} EmittedRecord
 * @typedef {{
 *   received_ms: number,
 *   origin_ms?: number,
 *   latency_ms?: number,
 *   displayed?: boolean,
 *   record: EmittedRecord
 * }} ReceiverLogItem
 * @typedef {{
 *   id?: number,
 *   end_ms: number,
 *   stale_after_ms: number,
 *   sha256?: string,
 *   injections: InjectionEvent[],
 *   timeline: TimelineEntry[],
 *   records?: ReceiverLogItem[]
 * }} TwinScenario
 * @typedef {{
 *   parser_sha256: string,
 *   scenarios: TwinScenario[]
 * }} TwinBank
 * @typedef {{
 *   messages: number,
 *   checksum_errors: number,
 *   sequence_anomalies: number,
 *   diagnostics: number,
 *   late_ignored: number,
 *   last_sample: SampleSnapshot | null,
 *   time: number,
 *   age: number | null,
 *   injected: { drop: number, corrupt: number, delay: number },
 *   status: 'waiting' | 'fresh' | 'stale'
 * }} TwinViewState
 * @typedef {[number, number, number]} Point3D
 */

/**
 * Upper-bound binary search returning the first index where `getTime(items[index]) > time`.
 * @template T
 * @param {readonly T[]} items
 * @param {number} time
 * @param {(item: T) => number} getTime
 * @returns {number}
 */
export function upperBoundBy(items, time, getTime) {
  let low = 0;
  let high = items.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (getTime(items[middle]) <= time) low = middle + 1;
    else high = middle;
  }
  return low;
}

/**
 * Select receiver state at a clamped virtual replay timestamp in O(log T + I_active).
 * @param {TwinScenario} scenario
 * @param {number} milliseconds
 * @returns {TwinViewState}
 */
export function stateAt(scenario, milliseconds) {
  if (typeof milliseconds !== 'number' || !Number.isFinite(milliseconds)) {
    throw new TypeError('Replay time must be finite');
  }
  const time = Math.max(0, Math.min(scenario.end_ms, milliseconds));
  const low = upperBoundBy(scenario.timeline, time, item => item.at_ms);
  const raw = low ? scenario.timeline[low - 1] : null;
  const messages = raw ? raw.messages : 0;
  const checksum_errors = raw && raw.checksum_errors !== undefined ? raw.checksum_errors : 0;
  const sequence_anomalies = raw && raw.sequence_anomalies !== undefined ? raw.sequence_anomalies : 0;
  const diagnostics = raw && raw.diagnostics !== undefined ? raw.diagnostics : 0;
  const late_ignored = raw && raw.late_ignored !== undefined ? raw.late_ignored : 0;
  const last_sample = raw ? raw.last_sample : null;
  const age = last_sample ? time - last_sample.origin_ms : null;
  const injected = { drop: 0, corrupt: 0, delay: 0 };
  const injEnd = upperBoundBy(scenario.injections, time, item => item.at_ms);
  for (let i = 0; i < injEnd; i++) {
    injected[scenario.injections[i].kind] += 1;
  }
  const status = age === null ? 'waiting' : age >= scenario.stale_after_ms ? 'stale' : 'fresh';
  return {
    ...(raw || {}),
    messages,
    checksum_errors,
    sequence_anomalies,
    diagnostics,
    late_ignored,
    last_sample,
    time,
    age,
    injected,
    status,
  };
}

/**
 * Return up to `limit` decoded receiver records with `received_ms <= time` using binary search.
 * @param {TwinScenario} scenario
 * @param {number} time
 * @param {number} [limit=12]
 * @returns {ReceiverLogItem[]}
 */
export function visibleRecordsAt(scenario, time, limit = 12) {
  const records = scenario.records || [];
  const end = upperBoundBy(records, time, item => item.received_ms);
  return records.slice(Math.max(0, end - limit), end);
}

/**
 * Format a decoded receiver log item into four display cell strings.
 * @param {ReceiverLogItem} item
 * @returns {[string, string, string, string]}
 */
export function formatRecordRow(item) {
  const description = item.record.kind === 'message'
    ? `seq ${item.record.seq}, ${item.record.value.toFixed(2)} °C`
    : item.record.code;
  const decision = item.record.kind === 'diagnostic'
    ? 'diagnostic'
    : item.displayed ? 'displayed' : 'held back: older sample';
  const originText = item.origin_ms !== undefined ? String(item.origin_ms) : '--';
  return [String(item.received_ms), description, originText, decision];
}

/**
 * Project a 3D glyph vertex onto 2D canvas coordinates plus depth.
 * @param {Point3D} point
 * @param {number} yaw
 * @param {number} pitch
 * @param {number} width
 * @param {number} height
 * @param {boolean} [flat=false]
 * @returns {Point3D}
 */
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

/**
 * Create a Canvas 2D glyph renderer bound to a drawing context.
 * @param {CanvasRenderingContext2D | null} context
 * @param {() => { yaw: number, pitch: number, flat: boolean, cssWidth: number, cssHeight: number }} getCamera
 * @returns {(state: TwinViewState) => void}
 */
function createSceneRenderer(context, getCamera) {
  /**
   * @param {Point3D[]} points
   * @param {string} [color='#d3d8cf']
   * @param {number} [thickness=1]
   */
  function line(points, color = '#d3d8cf', thickness = 1) {
    if (!context) return;
    const { yaw, pitch, flat, cssWidth, cssHeight } = getCamera();
    context.beginPath();
    points.forEach((pt, i) => {
      const p = project(pt, yaw, pitch, cssWidth, cssHeight, flat);
      if (i) context.lineTo(p[0], p[1]);
      else context.moveTo(p[0], p[1]);
    });
    context.strokeStyle = color;
    context.lineWidth = thickness;
    context.stroke();
  }

  /**
   * @param {number} x
   * @param {number} y
   * @param {number} z
   * @param {number} sx
   * @param {number} sy
   * @param {number} sz
   * @param {string[]} palette
   */
  function cube(x, y, z, sx, sy, sz, palette) {
    if (!context) return;
    const { yaw, pitch, flat, cssWidth, cssHeight } = getCamera();
    /** @type {Point3D[]} */
    const vertices = [
      [x - sx / 2, y, z - sz / 2], [x + sx / 2, y, z - sz / 2],
      [x + sx / 2, y, z + sz / 2], [x - sx / 2, y, z + sz / 2],
      [x - sx / 2, y + sy, z - sz / 2], [x + sx / 2, y + sy, z - sz / 2],
      [x + sx / 2, y + sy, z + sz / 2], [x - sx / 2, y + sy, z + sz / 2],
    ];
    const projected = vertices.map(v => project(v, yaw, pitch, cssWidth, cssHeight, flat));
    const faces = [[0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7], [4, 5, 6, 7]];
    faces
      .map((face, index) => ({
        face,
        index,
        depth: face.reduce((sum, v) => sum + projected[v][2], 0) / 4,
      }))
      .sort((a, b) => b.depth - a.depth)
      .forEach(({ face, index }) => {
        context.beginPath();
        face.forEach((v, i) => {
          if (i) context.lineTo(projected[v][0], projected[v][1]);
          else context.moveTo(projected[v][0], projected[v][1]);
        });
        context.closePath();
        context.fillStyle = palette[index % palette.length];
        context.fill();
        context.strokeStyle = '#78917b';
        context.lineWidth = 0.8;
        context.stroke();
      });
  }

  return function drawScene(state) {
    if (!context) return;
    const { yaw, pitch, flat, cssWidth, cssHeight } = getCamera();
    context.clearRect(0, 0, cssWidth, cssHeight);
    for (let i = -4; i <= 4; i++) {
      const t = i * 0.4;
      line([[-1.6, -0.65, t], [1.6, -0.65, t]]);
      line([[t, -0.65, -1.6], [t, -0.65, 1.6]]);
    }
    line([[-1.6, -0.65, 0], [1.6, -0.65, 0]], '#a8b5a6', 1.4);
    cube(0, -0.65, 0, 1.25, 0.13, 0.85, ['#c3cbc0', '#aebcae', '#cad2c7', '#b5c1b0', '#dce3d8']);
    const value = state.last_sample ? state.last_sample.value : undefined;
    const barHeight = value === undefined
      ? 0.16
      : 0.25 + (Math.max(20, Math.min(32, value)) - 20) / 12 * 1.7;
    const palette = state.status === 'fresh'
      ? ['#8eae86', '#668d62', '#80a478', '#6c9566', '#bdd6b3']
      : state.status === 'stale'
        ? ['#b6b092', '#938d70', '#c6c0a4', '#aaa385', '#dbd5bd']
        : ['#c1c7be', '#aab4a8', '#bec8ba', '#aebda9', '#e1e7dc'];
    cube(0, -0.52, 0, 0.68, barHeight, 0.47, palette);
    line([[1.17, -0.52, 0], [1.17, 1.43, 0]], '#94a18e', 1.1);
    context.fillStyle = '#55644f';
    context.font = '11px "Segoe UI",sans-serif';
    /** @type {Array<[number, number]>} */
    const ticks = [[20, -0.27], [26, 0.58], [32, 1.43]];
    for (const [n, y] of ticks) {
      line([[1.11, y, 0], [1.23, y, 0]], '#94a18e');
      const p = project([1.32, y, 0], yaw, pitch, cssWidth, cssHeight, flat);
      context.fillText(`${n} °C`, p[0], p[1] + 4);
    }
    const labelPos = project([-0.55, -0.8, 0.6], yaw, pitch, cssWidth, cssHeight, flat);
    context.fillText('SYNTHETIC / TEMPERATURE', labelPos[0] - 35, labelPos[1] + 18);
  };
}

/**
 * Initialize Twin Bench interactive replay UI. Schedules RAF only while actively playing.
 * @param {TwinBank} bank
 * @param {{ document?: any, window?: any }} [env]
 */
export function startTwin(bank, env) {
  const doc = env && env.document ? env.document : document;
  const win = env && env.window ? env.window : window;
  /** @param {string} id */
  const $ = id => /** @type {any} */ (doc.getElementById(id));
  const canvas = /** @type {HTMLCanvasElement} */ ($('scene'));
  const context = canvas.getContext('2d');

  let scenario = bank.scenarios[0];
  let time = 0;
  let playing = false;
  /** @type {number | null} */
  let lastFrame = null;
  let rafId = 0;
  let yaw = -0.65;
  let pitch = 0.42;
  let flat = false;
  /** @type {{ x: number, y: number } | null} */
  let drag = null;
  let lastLogKey = '';
  let cssWidth = 800;
  let cssHeight = 320;

  /**
   * @param {string} id
   * @param {string | number} value
   */
  const setText = (id, value) => {
    const node = $(id);
    const next = String(value);
    if (node && node.textContent !== next) node.textContent = next;
  };

  setText('parser-hash', bank.parser_sha256);
  const drawScene = createSceneRenderer(context, () => ({ yaw, pitch, flat, cssWidth, cssHeight }));

  function renderLog() {
    const visible = visibleRecordsAt(scenario, time, 12);
    const keyParts = visible.map(r => {
      const tag = r.record.kind === 'message' ? r.record.seq : r.record.code;
      return `${r.received_ms}:${r.record.kind}:${tag}`;
    });
    const key = `${scenario.id}:${keyParts.join(',')}`;
    if (key === lastLogKey) return;
    lastLogKey = key;
    const body = /** @type {HTMLTableSectionElement} */ ($('log'));
    body.replaceChildren();
    if (!visible.length) {
      const row = body.insertRow();
      const cell = row.insertCell();
      cell.colSpan = 4;
      cell.textContent = 'No decoded records yet.';
      return;
    }
    for (let i = visible.length - 1; i >= 0; i--) {
      const row = body.insertRow();
      for (const cellText of formatRecordRow(visible[i])) {
        row.insertCell().textContent = cellText;
      }
    }
  }

  function render() {
    const state = stateAt(scenario, time);
    const sample = state.last_sample;
    setText('value', sample ? sample.value.toFixed(2) : '--');
    setText('seq', sample ? sample.seq : '--');
    setText('age', state.age === null ? '--' : `${Math.round(state.age)} ms`);
    setText(
      'latency',
      sample && sample.received_ms !== undefined ? `${sample.received_ms - sample.origin_ms} ms` : '--'
    );
    const label = state.status === 'fresh'
      ? 'Fresh sample'
      : state.status === 'stale' ? 'Stale / holding last value' : 'Waiting for data';
    setText('status', label);
    $('status').className = `status ${state.status}`;
    /** @type {Array<[string, 'messages' | 'checksum_errors' | 'sequence_anomalies' | 'late_ignored']>} */
    const metricMap = [
      ['messages', 'messages'],
      ['checksum', 'checksum_errors'],
      ['anomalies', 'sequence_anomalies'],
      ['late', 'late_ignored'],
    ];
    for (const [id, key] of metricMap) setText(id, state[key]);
    /** @type {FaultKind[]} */
    const kinds = ['drop', 'corrupt', 'delay'];
    for (const kind of kinds) setText(`injected-${kind}`, state.injected[kind]);
    setText('clock', `${(time / 1000).toFixed(3)} s`);
    $('seek').value = String(Math.round(time));
    setText('play', playing ? 'Pause replay' : 'Play replay');
    setText('hash', scenario.sha256 || '');
    renderLog();
    drawScene(state);
  }

  function resize() {
    const box = canvas.getBoundingClientRect();
    const ratio = Math.min(win.devicePixelRatio || 1, 2);
    cssWidth = box.width;
    cssHeight = box.height;
    canvas.width = Math.round(cssWidth * ratio);
    canvas.height = Math.round(cssHeight * ratio);
    if (context) context.setTransform(ratio, 0, 0, ratio, 0, 0);
    render();
  }

  function scheduleTick() {
    if (playing && !rafId) {
      rafId = win.requestAnimationFrame(tick);
    }
  }

  /** @param {number} timestamp */
  function tick(timestamp) {
    rafId = 0;
    if (!playing) return;
    if (lastFrame !== null) {
      const speed = Number($('speed').value) || 1;
      time = Math.min(scenario.end_ms, time + (timestamp - lastFrame) * speed);
    }
    lastFrame = timestamp;
    if (time >= scenario.end_ms) playing = false;
    render();
    scheduleTick();
  }

  function pause() {
    playing = false;
    lastFrame = null;
    render();
  }

  function choose() {
    pause();
    const id = Number($('drop').checked) + 2 * Number($('corrupt').checked) + 4 * Number($('delay').checked);
    const nextScenario = bank.scenarios.find(s => s.id === id);
    if (nextScenario) scenario = nextScenario;
    time = 0;
    lastLogKey = '';
    $('seek').max = String(scenario.end_ms);
    render();
  }

  for (const kind of ['drop', 'corrupt', 'delay']) {
    $(kind).addEventListener('change', choose);
  }
  $('play').addEventListener('click', () => {
    if (time >= scenario.end_ms) time = 0;
    playing = !playing;
    lastFrame = null;
    render();
    scheduleTick();
  });
  $('restart').addEventListener('click', () => {
    time = 0;
    pause();
  });
  $('seek').addEventListener('input', () => {
    time = Number($('seek').value);
    pause();
  });
  $('late-example').addEventListener('click', () => {
    $('drop').checked = false;
    $('corrupt').checked = false;
    $('delay').checked = true;
    choose();
    time = 852;
    render();
  });
  $('rotate-left').addEventListener('click', () => {
    yaw -= 0.18;
    render();
  });
  $('rotate-right').addEventListener('click', () => {
    yaw += 0.18;
    render();
  });
  $('reset-camera').addEventListener('click', () => {
    yaw = -0.65;
    pitch = 0.42;
    render();
  });
  $('toggle-view').addEventListener('click', () => {
    flat = !flat;
    setText('toggle-view', flat ? '3D view' : '2D view');
    render();
  });
  canvas.addEventListener('pointerdown', (/** @type {PointerEvent} */ event) => {
    if (flat) return;
    drag = { x: event.clientX, y: event.clientY };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('pointermove', (/** @type {PointerEvent} */ event) => {
    if (!drag) return;
    yaw += (event.clientX - drag.x) * 0.008;
    pitch = Math.max(-0.2, Math.min(0.9, pitch + (event.clientY - drag.y) * 0.006));
    drag = { x: event.clientX, y: event.clientY };
    render();
  });
  const stopDrag = () => {
    drag = null;
  };
  canvas.addEventListener('pointerup', stopDrag);
  canvas.addEventListener('pointercancel', stopDrag);
  $('export').addEventListener('click', () => {
    const urlCreator = win.URL || URL;
    const timer = win.setTimeout || setTimeout;
    const url = urlCreator.createObjectURL(
      new Blob([JSON.stringify(scenario, null, 2)], { type: 'application/json' })
    );
    const link = doc.createElement('a');
    link.href = url;
    link.download = `uart-twin-scenario-${scenario.id}.json`;
    link.click();
    timer(() => urlCreator.revokeObjectURL(url), 1000);
  });
  doc.addEventListener('visibilitychange', () => {
    if (doc.hidden) pause();
  });
  win.addEventListener('resize', resize);
  $('seek').max = String(scenario.end_ms);
  resize();
}

if (typeof document !== 'undefined') {
  const dataElement = document.getElementById('twin-data');
  if (dataElement && dataElement.textContent) {
    startTwin(JSON.parse(dataElement.textContent));
  }
}
