import hashlib
import json
import subprocess
import sys

import pytest

from uart_fixture_kit.twin import build_twin_bank, render_twin_html, scenario


@pytest.mark.parametrize('frames', [12, 120, 240])
@pytest.mark.parametrize('mask', range(8))
def test_receiver_matches_independent_fault_schedule(frames, mask):
    drop, corrupt, delay = (bool(mask & flag) for flag in (1, 2, 4))
    result = scenario(frames, drop=drop, corrupt=corrupt, delay=delay)
    expected = []
    for seq in range(frames):
        if drop and seq % 5 == 4:
            continue
        if corrupt and seq % 7 == 6:
            continue
        expected.append((seq * 100 + (350 if delay and seq % 6 == 5 else 0) + 2, seq))
    expected.sort()
    messages = [entry for entry in result['records'] if entry['record']['kind'] == 'message']
    assert [(entry['received_ms'], entry['record']['seq']) for entry in messages] == expected
    for entry in messages:
        seq = entry['record']['seq']
        assert entry['record']['value'] == (2200 + min(seq % 40, 40 - seq % 40) * 35) / 100
        assert entry['record']['unit'] == 'C'
        assert entry['origin_ms'] == seq * 100
        assert entry['latency_ms'] == 2 + (350 if delay and seq % 6 == 5 else 0)
    corrupt_count = sum(not (drop and n % 5 == 4) and corrupt and n % 7 == 6 for n in range(frames))
    assert result['timeline'][-1]['checksum_errors'] == corrupt_count
    assert result['timeline'][-1]['messages'] == len(expected)
    assert [item['at_ms'] for item in result['timeline']] == sorted(item['at_ms'] for item in result['timeline'])
    shown = [entry['origin_ms'] for entry in messages if entry['displayed']]
    assert shown == sorted(shown)
    digest = result.pop('sha256')
    assert hashlib.sha256(json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest() == digest


def test_late_frame_is_logged_but_cannot_refresh_newer_value():
    result = scenario(12, delay=True)
    late = next(entry for entry in result['records'] if entry['record'].get('seq') == 5 and entry['record']['kind'] == 'message')
    assert late['received_ms'] == 852 and late['origin_ms'] == 500 and not late['displayed']
    state = next(entry for entry in result['timeline'] if entry['at_ms'] == 852)
    assert state['last_sample']['seq'] == 8 and state['last_sample']['origin_ms'] == 800
    assert state['late_ignored'] == 1
    # Advancing the receiver cannot mutate older snapshots.
    assert result['timeline'][2]['messages'] == 1
    assert result['timeline'][2]['last_sample']['seq'] == 0


@pytest.mark.parametrize('frames', [True, 0, 11, 241, 12.5, '12'])
def test_invalid_frames_are_rejected(frames):
    with pytest.raises(ValueError):
        build_twin_bank(frames)


def test_fault_flags_are_not_truthiness_converted():
    with pytest.raises(ValueError):
        scenario(drop=1)


def test_bank_reproducible_and_html_self_contained():
    bank = build_twin_bank(12)
    assert bank == build_twin_bank(12)
    assert [item['id'] for item in bank['scenarios']] == list(range(8))
    html = render_twin_html(bank)
    assert '/* TWIN_' not in html
    assert '<script src=' not in html and 'fetch(' not in html
    assert 'synthetic' in html and bank['parser_sha256'] in html
    assert '\\u003c/script' in render_twin_html({'label': '</script>'})


def test_cli_exports_both_files_and_rejects_same_target(tmp_path):
    html, data = tmp_path / 'twin.html', tmp_path / 'twin.json'
    command = [sys.executable, '-m', 'uart_fixture_kit', 'twin', '--frames', '12', '--output', str(html)]
    result = subprocess.run(command + ['--json-output', str(data)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(data.read_text()) == build_twin_bank(12)
    assert html.read_text(encoding='utf-8') == render_twin_html(build_twin_bank(12))
    original = html.read_bytes()
    rejected = subprocess.run(command + ['--json-output', str(html)], capture_output=True, text=True)
    assert rejected.returncode == 2 and html.read_bytes() == original
