import json
import subprocess
import sys

import pytest

from uart_fixture_kit import Event, chunk_bytes, drop_bytes, flip_byte, schedule


def test_explicit_mutations_and_schedule():
    assert chunk_bytes(b"abcdefg",[1,2]) == (b"a",b"bc",b"d",b"ef",b"g")
    assert chunk_bytes(b"",[1]) == ()
    assert schedule(b"abc",[1],start_ms=10,interval_ms=2) == (Event(10,b"a"),Event(12,b"b"),Event(14,b"c"))
    assert flip_byte(b"abc",1) == b"acc"
    assert drop_bytes(b"abc",1) == b"ac"


@pytest.mark.parametrize("sizes", [[],[0],[-1],[True],[1.5]])
def test_invalid_chunks(sizes):
    with pytest.raises(ValueError):
        chunk_bytes(b"abc",sizes)


@pytest.mark.parametrize("offset,mask", [(-1,1),(3,1),(True,1),(0,0),(0,256)])
def test_invalid_flip(offset,mask):
    with pytest.raises(ValueError):
        flip_byte(b"abc",offset,mask)


@pytest.mark.parametrize("offset,count", [(0,0),(-1,1),(2,2),(3,1),(True,1)])
def test_invalid_drop(offset,count):
    with pytest.raises(ValueError):
        drop_bytes(b"abc",offset,count)


def run_cli(*args):
    return subprocess.run([sys.executable,"-m","uart_fixture_kit",*args],capture_output=True,text=True)


def test_demo_json_in_installed_package():
    result = run_cli("demo","--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["passed"] is True
    assert len(report["fixtures"]) == 15


@pytest.mark.parametrize("expected,status", [([],0),([{"unexpected":True}],1)])
def test_custom_factory_cli(tmp_path,expected,status):
    path=tmp_path/"empty.json"
    path.write_text(json.dumps({"version":1,"name":"empty","events":[],"end_ms":0,"expected":expected}),encoding="utf-8")
    result=run_cli("run",str(path),"--parser","uart_fixture_kit.examples:JsonLineParser","--json")
    assert result.returncode == status, result.stderr
    assert json.loads(result.stdout)["passed"] == (status==0)


def test_cli_bad_fixture_and_bad_factory(tmp_path):
    path=tmp_path/"bad.json"
    path.write_text("not JSON",encoding="utf-8")
    result=run_cli("run",str(path),"--parser","uart_fixture_kit.examples:JsonLineParser","--json")
    assert result.returncode == 2
    assert "error" in json.loads(result.stdout)
    result=run_cli("run",str(path),"--parser","no-colon")
    assert result.returncode == 2
    assert "MODULE:FACTORY" in result.stderr
