# Technical Debt & Performance Ledger — `uart-fixture-kit`

Source crosscheck: 2026-11-08 WIB. The locations and removed lines match current source. Diffs are independent proposals, not applied changes or measured speedups.

---

## 1. Store the validated snapshot in `Fixture.__post_init__`

- **Location**: `src/uart_fixture_kit/core.py` (lines `83-87`)
- **Category**: Immutability Isolation & Serialization Overhead
- **Severity**: Medium

### Technical Analysis & Impact
In `Fixture.__post_init__` (`src/uart_fixture_kit/core.py:83-87`), `_snapshot_json_value(self.expected)` validates and deep-copies `self.expected`, but the return value is discarded instead of being stored via `object.__setattr__(self, 'expected', ...)`. Consequently, if a caller mutates a dict inside the `expected` list after constructing `Fixture`, the frozen fixture's baseline changes silently.

### Proposed Patch (`diff`)
```diff
diff --git a/src/uart_fixture_kit/core.py b/src/uart_fixture_kit/core.py
--- a/src/uart_fixture_kit/core.py
+++ b/src/uart_fixture_kit/core.py
@@ -84,7 +84,7 @@
             not isinstance(record, dict) for record in self.expected
         ):
             raise ValueError("expected must be a list of JSON objects")
-        _snapshot_json_value(self.expected)
+        object.__setattr__(self, "expected", _snapshot_json_value(self.expected))
 
 
 def _parse_fixture_payload(raw: object) -> Fixture:
```

### Verification
Run `$env:PYTHONPATH='src'; C:\Python314\python.exe -m pytest -q`.

---

## 2. Redundant Per-Vertex Trigonometric Evaluations in `project()` During Canvas Scene Redraws

- **Location**: `src/uart_fixture_kit/assets/twin_ui.mjs` (lines `152-158`)
- **Category**: Frontend Rendering Efficiency
- **Severity**: Medium

### Technical Analysis & Impact
`project()` (`src/uart_fixture_kit/assets/twin_ui.mjs:152-161`) evaluates `Math.cos(yaw)`, `Math.sin(yaw)`, `Math.cos(pitch)`, and `Math.sin(pitch)` twice each per vertex (8 trig calls per vertex across 59 vertices per `drawScene` frame) instead of binding `cosYaw`, `sinYaw`, `cosPitch`, `sinPitch` once.

### Proposed Patch (`diff`)
```diff
diff --git a/src/uart_fixture_kit/assets/twin_ui.mjs b/src/uart_fixture_kit/assets/twin_ui.mjs
--- a/src/uart_fixture_kit/assets/twin_ui.mjs
+++ b/src/uart_fixture_kit/assets/twin_ui.mjs
@@ -152,10 +152,12 @@
 export function project(point, yaw, pitch, width, height, flat = false) {
   const [x, y, z] = point;
   if (flat) return [width / 2 + x * height * 0.23, height * 0.68 - y * height * 0.23, z];
-  const rx = x * Math.cos(yaw) - z * Math.sin(yaw);
-  const rz = x * Math.sin(yaw) + z * Math.cos(yaw);
-  const ry = y * Math.cos(pitch) - rz * Math.sin(pitch);
-  const depth = y * Math.sin(pitch) + rz * Math.cos(pitch);
+  const cosYaw = Math.cos(yaw), sinYaw = Math.sin(yaw);
+  const cosPitch = Math.cos(pitch), sinPitch = Math.sin(pitch);
+  const rx = x * cosYaw - z * sinYaw;
+  const rz = x * sinYaw + z * cosYaw;
+  const ry = y * cosPitch - rz * sinPitch;
+  const depth = y * sinPitch + rz * cosPitch;
   const scale = height * 0.27 * 6 / (6 + depth);
   return [width / 2 + rx * scale, height * 0.64 - ry * scale, depth];
 }
```

### Verification
Run `node --test tests/twin-ui-controller.test.mjs`.

The snapshot proposal isolates caller-owned input. It does not make the exposed expected list recursively immutable. The trigonometric proposal reduces eight calls to four per project() call; it does not cache across vertices or frames.
