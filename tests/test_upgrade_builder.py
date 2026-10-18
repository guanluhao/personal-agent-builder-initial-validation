import csv
import json
from pathlib import Path
import subprocess
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from personal_agent_builder.generator import generate, proposal_hash
from personal_agent_builder.runtime import invoke
from personal_agent_builder.upgrade import activate_upgrade, rollback
from personal_agent_builder.upgrade_builder import prepare_upgrade
from personal_agent_builder import upgrade_builder, webapp


ROOT = Path(__file__).resolve().parents[1]
NEW_SOURCE = '''"""在表格副本中增加一列，并输出新表格。"""
import argparse
import csv
import json
from pathlib import Path

def add_column(input_file, column, values, output):
    with input_file.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    if column in fields:
        raise ValueError("这一列已经存在")
    items = [value.strip() for value in values.split(",")]
    if len(items) != len(rows):
        raise ValueError("数值个数必须与原表格行数相同")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[*fields, column])
        writer.writeheader()
        for row, value in zip(rows, items):
            writer.writerow({**row, column: value})
    return {"output": str(output), "rows": len(rows), "column": column}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--column", required=True)
    parser.add_argument("--values", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(add_column(args.input, args.column, args.values, args.output), ensure_ascii=False))
'''


def _bundle(tmp_path):
    proposal = json.loads((ROOT / "examples" / "csv_proposal.json").read_text(encoding="utf-8"))
    return generate(ROOT / "examples" / "csv_project", proposal,
                    {"approved_by_user": True, "proposal_sha256": proposal_hash(proposal)}, tmp_path / "agent")


def test_upgrade_asks_for_missing_column_name(tmp_path):
    bundle = _bundle(tmp_path)
    answer = prepare_upgrade(bundle, "再加一列，值是 7、352、789", planner=lambda *_: {
        "status": "clarify", "question": "新列叫什么名字？", "filename": "", "source": "", "test_inputs": []})
    assert answer == {"status": "needs_clarification", "message": "新列叫什么名字？"}
    assert not (bundle / "upgrade_proposals").exists()


def test_upgrade_accepts_nonempty_output_directory(tmp_path):
    bundle = _bundle(tmp_path)
    source = '''"""把结果写入输出目录。"""
import argparse
import json
from pathlib import Path

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result = args.output / "result.json"
    result.write_text(json.dumps({"value": 7}), encoding="utf-8")
    print(json.dumps({"result": str(result)}))
'''
    plan = {"status": "proposal", "filename": "make_report.py", "source": source,
            "modifications": [], "test_inputs": [{"name": "output", "value": "report_folder"}]}
    ready = prepare_upgrade(bundle, "把结果保存到目录", planner=lambda *_: plan)
    assert ready["status"] == "ready" and ready["test_passed"]
    stage = bundle / "upgrade_proposals" / ready["proposal_id"] / "project"
    assert json.loads((stage / "report_folder" / "result.json").read_text(encoding="utf-8")) == {"value": 7}


def test_builder_question_is_available_to_next_agent_reply(tmp_path):
    bundle = _bundle(tmp_path)
    webapp._save_upgrade_followup(bundle, "给结果增加一种输出", "希望输出什么格式？")
    (bundle / "router.py").write_text(
        "def route(message, manifest, context):\n"
        "    assert context['pending_upgrade']['request'] == '给结果增加一种输出'\n"
        "    assert context['pending_upgrade']['question'] == '希望输出什么格式？'\n"
        "    assert context['recent_turns'][-1]['assistant'] == '希望输出什么格式？'\n"
        "    return {'action': 'upgrade_request', 'reason': '需要扩展程序。', "
        "'resolved_request': '给结果增加一份文本格式的输出。'}\n",
        encoding="utf-8")
    run = subprocess.run([sys.executable, str(bundle / "agent.py"), "文本格式"],
                         cwd=bundle, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    answer = json.loads(run.stdout)
    assert answer["upgrade_request"] == "给结果增加一份文本格式的输出。"
    webapp._clear_upgrade_followup(bundle)
    context = json.loads((bundle / "records" / "context.json").read_text(encoding="utf-8"))
    assert "pending_upgrade" not in context


def test_model_prepares_tests_activates_and_rolls_back_new_column(tmp_path):
    bundle = _bundle(tmp_path)
    before = (bundle / "project" / "data.csv").read_bytes()
    plan = {"status": "proposal", "question": "", "filename": "add_column.py", "source": NEW_SOURCE,
            "test_inputs": [{"name": "input", "value": "data.csv"}, {"name": "column", "value": "实验值"},
                            {"name": "values", "value": "7,352,789"}, {"name": "output", "value": "expanded.csv"}]}
    ready = prepare_upgrade(bundle, "新增一列实验值，依次填入 7、352、789", planner=lambda *_: plan)
    assert ready["status"] == "ready" and ready["test_passed"]
    assert "add_column.py" in ready["diff"]
    assert (bundle / "project" / "data.csv").read_bytes() == before
    stage = bundle / "upgrade_proposals" / ready["proposal_id"] / "project"
    with (stage / "expanded.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["实验值"] for row in rows] == ["7", "352", "789"]
    try:
        activate_upgrade(bundle, stage, {})
        assert False, "未经确认却启用了升级"
    except PermissionError:
        pass
    activate_upgrade(bundle, stage, {"approved_by_user": True, "version": ready["version"]})
    called = invoke(bundle, "add_column", {"input": "data.csv", "column": "第二列", "values": "1,2,3", "output": "used.csv"})
    assert called["verified_output"] is True
    assert (bundle / "versions" / ready["version"] / "used.csv").is_file()
    assert "def add_column" in (bundle / "mcp_server.py").read_text(encoding="utf-8")
    assert rollback(bundle) == "base"
    try:
        invoke(bundle, "add_column", {"column": "又一列", "values": "4,5,6"})
        assert False, "回退后仍可调用新能力"
    except ValueError:
        pass
    assert (bundle / "project" / "data.csv").read_bytes() == before


def test_upgrade_can_change_existing_program_and_add_adapter(tmp_path):
    bundle = _bundle(tmp_path)
    original = (bundle / "project" / "csv_task.py").read_text(encoding="utf-8")
    changed = original + '''\n\ndef count_rows(input_file):
    with input_file.open(encoding="utf-8-sig", newline="") as stream:
        return sum(1 for _ in csv.DictReader(stream))
'''
    adapter = '''import argparse
import json
from pathlib import Path
from csv_task import count_rows

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = {"rows": count_rows(args.input)}
    args.output.write_text(json.dumps(result), encoding="utf-8")
    print(json.dumps(result))
'''
    plan = {"status": "proposal", "filename": "count_rows.py", "source": adapter,
            "modifications": [{"path": "csv_task.py", "source": changed}],
            "test_inputs": [{"name": "--input", "value": "data.csv"},
                            {"name": "--output", "value": "rows.json"}]}
    ready = prepare_upgrade(bundle, "增加统计行数的功能", planner=lambda *_: plan)
    assert ready["test_passed"] is True
    assert "csv_task.py" in ready["diff"] and "count_rows.py" in ready["diff"]
    assert (bundle / "project" / "csv_task.py").read_text(encoding="utf-8") == original
    stage = bundle / "upgrade_proposals" / ready["proposal_id"] / "project"
    assert json.loads((stage / "rows.json").read_text(encoding="utf-8"))["rows"] == 3
    assert any(x["name"] == "summarize_csv" and x["status"] == "通过" for x in ready["regression"])
    activate_upgrade(bundle, stage, {"approved_by_user": True, "version": ready["version"]})
    assert invoke(bundle, "count_rows", {"input": "data.csv", "output": "rows_again.json"})["verified_output"] is True
    assert rollback(bundle) == "base"


def test_local_interface_requires_approval_then_exposes_upgrade(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "DATA", tmp_path / "web_data")
    agent = tmp_path / "web_data" / "agents" / ("a" * 32)
    proposal = json.loads((ROOT / "examples" / "csv_proposal.json").read_text(encoding="utf-8"))
    generate(ROOT / "examples" / "csv_project", proposal,
             {"approved_by_user": True, "proposal_sha256": proposal_hash(proposal)}, agent)
    (agent / "router.py").write_text(
        "def route(message, manifest, context):\n"
        "    return {'action': 'invoke', 'tool': 'add_column', "
        "'parameters': {'input': 'data.csv', 'column': '第二列', 'values': '1,2,3', 'output': 'used.csv'}}\n",
        encoding="utf-8")
    plan = {"status": "proposal", "question": "", "filename": "add_column.py", "source": NEW_SOURCE,
            "test_inputs": [{"name": "input", "value": "data.csv"}, {"name": "column", "value": "实验值"},
                            {"name": "values", "value": "7,352,789"}, {"name": "output", "value": "expanded.csv"}]}
    monkeypatch.setattr(webapp, "prepare_upgrade", lambda bundle, request: upgrade_builder.prepare_upgrade(
        bundle, request, planner=lambda *_: plan))
    server = webapp.AppServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def call(path, value=None):
        req = Request(base + path, data=json.dumps(value).encode() if value is not None else None,
                      headers={"X-PAB-Token": server.token}, method="POST" if value is not None else "GET")
        with urlopen(req, timeout=60) as response:
            return json.loads(response.read())

    try:
        prepared = call("/api/upgrade/prepare", {"agent_id": "a" * 32,
                                                 "request": "新增实验值列，依次填入7、352、789"})
        assert prepared["test_passed"] is True
        pending = call("/api/upgrade/pending?agent_id=" + "a" * 32)["proposal"]
        assert pending["proposal_id"] == prepared["proposal_id"] and pending["test_passed"] is True
        with pytest.raises(HTTPError) as refused:
            call("/api/upgrade/activate", {"agent_id": "a" * 32, "proposal_id": prepared["proposal_id"],
                                           "version": prepared["version"], "confirmed": False})
        assert refused.value.code == 400
        call("/api/upgrade/activate", {"agent_id": "a" * 32, "proposal_id": prepared["proposal_id"],
                                       "version": prepared["version"], "confirmed": True})
        assert call("/api/upgrade/pending?agent_id=" + "a" * 32)["proposal"] is None
        active = call("/api/agents")["agents"][0]
        assert active["can_rollback"] is True and "add_column" in active["tools"]
        answer = call("/api/chat", {"agent_id": "a" * 32,
                                    "message": "add_column column=第二列 values=1,2,3 output=used.csv"})
        assert answer["record"]["verified_output"] is True
        call("/api/upgrade/rollback", {"agent_id": "a" * 32, "confirmed": True})
        restored = call("/api/agents")["agents"][0]
        assert restored["can_rollback"] is False and "add_column" not in restored["tools"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
