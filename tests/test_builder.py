import json
from pathlib import Path
import subprocess
import sys

import pytest

from personal_agent_builder.analysis import inspect_project, capability_report
from personal_agent_builder.generator import generate, proposal_hash
from personal_agent_builder.proposal import propose_tools
from personal_agent_builder.runtime import invoke, request_upgrade
from personal_agent_builder.upgrade import stage_upgrade, activate_upgrade, rollback

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "examples" / "csv_project"


def _stub_model_choice(bundle, tool):
    (bundle / "router.py").write_text(
        "def route(message, manifest, context):\n"
        f"    return {{'action': 'invoke', 'tool': {tool!r}, 'parameters': {{}}}}\n",
        encoding="utf-8")


def test_static_analysis_does_not_run_project(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("# Tool\nReads files.\n", encoding="utf-8")
    (source / "danger.py").write_text("from pathlib import Path\nPath('executed').write_text('yes')\ndef calculate(x): return x\n", encoding="utf-8")
    result = inspect_project(source)
    assert result["functions"][0]["name"] == "calculate"
    assert not (source / "executed").exists()
    assert "根据源码推断，尚未运行验证" in capability_report(result)


def test_static_analysis_discovers_cli_candidates_without_claiming_verification():
    csv = inspect_project(CSV)
    candidate = next(tool for tool in csv["recommended_tools"] if tool["file"] == "csv_task.py")
    assert [(arg["flag"], arg["type"]) for arg in candidate["arguments"]] == [
        ("--input", "path"), ("--column", "string"), ("--output", "path")]
    assert "未运行功能" in csv["verification"]
    report = capability_report(csv)
    assert "尚未运行验证" in report and "汇总表格中指定数值列" in report
    assert report.count("## ") == 3
    seismic = inspect_project(ROOT / "examples" / "seismic_v1_snapshot")
    assert {tool["file"] for tool in seismic["recommended_tools"]} >= {"preview.py", "move_anomaly.py"}


def test_nested_cli_candidates_have_distinct_names(tmp_path):
    content = "import argparse\nif __name__ == '__main__':\n    parser = argparse.ArgumentParser()\n    parser.add_argument('--input', required=True)\n    args = parser.parse_args()\n"
    for folder in ("first", "second"):
        target = tmp_path / folder
        target.mkdir()
        (target / "cli.py").write_text(content, encoding="utf-8")
    analysis = inspect_project(tmp_path)
    assert {x["name"] for x in analysis["recommended_tools"]} == {"first_cli", "second_cli"}


def test_unfamiliar_project_report_uses_its_own_description(tmp_path):
    (tmp_path / "README.md").write_text(
        "# 新项目\n读取一份文本并统计字符。\n\n输出：一个结果文件和统计数值。\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        '"""统计文本中的字符数量。"""\nimport argparse\n'
        'if __name__ == "__main__":\n'
        '    parser = argparse.ArgumentParser()\n'
        '    parser.add_argument("--input", required=True)\n'
        '    args = parser.parse_args()\n', encoding="utf-8")
    analysis = inspect_project(tmp_path)
    report = capability_report(analysis)
    assert analysis["summary"] == "读取一份文本并统计字符。"
    assert analysis["output_hint"] == "一个结果文件和统计数值。"
    assert "请统计文本中的字符数量。" in report
    assert "一个结果文件和统计数值。" in report


def test_new_text_project_uses_same_discovery_generation_and_execution(tmp_path):
    source = tmp_path / "unfamiliar-text-program"
    source.mkdir()
    (source / "sample.txt").write_text("alpha beta\n", encoding="utf-8")
    (source / "README.md").write_text("# 文本程序\n统计文本的字符数。\n输出：结果文件和字符数。\n", encoding="utf-8")
    (source / "main.py").write_text(
        '"""统计文本的字符数。"""\n'
        'import argparse\nimport json\nfrom pathlib import Path\n'
        'if __name__ == "__main__":\n'
        '    parser = argparse.ArgumentParser()\n'
        '    parser.add_argument("--input", type=Path, help="需要读取的文本")\n'
        '    parser.add_argument("--output", type=Path, help="结果保存位置")\n'
        '    args = parser.parse_args()\n'
        '    count = len(args.input.read_text(encoding="utf-8"))\n'
        '    args.output.write_text(str(count), encoding="utf-8")\n'
        '    print(json.dumps({"count": count, "output": str(args.output)}))\n', encoding="utf-8")
    analysis = inspect_project(source)
    proposal, samples = propose_tools(source, analysis, ["main"])
    assert proposal["tools"][0]["parameters"]["input"]["description"] == "需要读取的文本"
    bundle = generate(source, proposal,
                      {"approved_by_user": True, "proposal_sha256": proposal_hash(proposal)},
                      tmp_path / "generated-agent")
    result = invoke(bundle, "main", {**samples["main"], "input": "sample.txt"})
    assert result["exit_code"] == 0 and result["verified_output"] is True
    assert (bundle / "project" / "agent_output").read_text(encoding="utf-8") == "11"


def test_static_cli_reads_literal_path_default_without_running_code(tmp_path):
    (tmp_path / "main.py").write_text(
        'import argparse\nfrom pathlib import Path\n'
        'def main():\n    parser = argparse.ArgumentParser()\n'
        '    parser.add_argument("--input", type=Path, default=Path("sample.txt"))\n'
        '    args = parser.parse_args()\n'
        'if __name__ == "__main__":\n    main()\n', encoding="utf-8")
    candidate = inspect_project(tmp_path)["recommended_tools"][0]
    assert candidate["arguments"][0]["default"] == "sample.txt"


def test_approval_gate_and_real_csv_execution(tmp_path):
    proposal = json.loads((ROOT / "examples" / "csv_proposal.json").read_text(encoding="utf-8"))
    with pytest.raises(PermissionError):
        generate(CSV, proposal, {}, tmp_path / "denied")
    digest = proposal_hash(proposal)
    bundle = generate(CSV, proposal, {"approved_by_user": True, "proposal_sha256": digest,
                                      "note": "test fixture only"}, tmp_path / "csv-agent")
    assert json.loads((bundle / "agent.json").read_text(encoding="utf-8"))["source"] == "csv_project"
    assert "summarize_csv" in (bundle / "README.md").read_text(encoding="utf-8")
    result = invoke(bundle, "summarize_csv", {})
    assert result["exit_code"] == 0, result
    assert json.loads((bundle / "project" / "summary.json").read_text(encoding="utf-8"))["sum"] == "10"
    assert (bundle / "records" / "calls.jsonl").exists()
    assert "def summarize_csv" in (bundle / "mcp_server.py").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="越出"):
        invoke(bundle, "summarize_csv", {"input": "../../outside.csv"})
    assert request_upgrade(bundle, "增加按分类汇总") .exists()
    _stub_model_choice(bundle, "summarize_csv")
    agent = subprocess.run([sys.executable, str(bundle / "agent.py"), "请汇总CSV"], capture_output=True, text=True)
    assert agent.returncode == 0 and "\"exit_code\": 0" in agent.stdout, agent.stderr
    compiled = subprocess.run([sys.executable, "-m", "py_compile", str(bundle / "mcp_server.py")], capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stderr


def test_generator_rejects_secret_and_large_data_copy(tmp_path):
    proposal = json.loads((ROOT / "examples" / "csv_proposal.json").read_text(encoding="utf-8"))
    for forbidden in [".env", "private.pem", "array.npy"]:
        (tmp_path / forbidden).write_text("placeholder", encoding="utf-8")
        candidate = {**proposal, "copy_paths": [forbidden]}
        with pytest.raises(ValueError, match="拒绝复制"):
            generate(tmp_path, candidate,
                     {"approved_by_user": True, "proposal_sha256": proposal_hash(candidate)},
                     tmp_path / f"agent-{forbidden}")


def test_upgrade_requires_approval_and_can_rollback(tmp_path):
    proposal = json.loads((ROOT / "examples" / "csv_proposal.json").read_text(encoding="utf-8"))
    bundle = generate(CSV, proposal, {"approved_by_user": True, "proposal_sha256": proposal_hash(proposal),
                                      "note": "test fixture only"}, tmp_path / "agent")
    old = (bundle / "project" / "csv_task.py").read_text(encoding="utf-8")
    changed = old.replace('"mean": str(total / len(values)) if values else None}',
                          '"mean": str(total / len(values)) if values else None, "average": str(total / len(values)) if values else None}')
    assert changed != old
    report = stage_upgrade(bundle, {"csv_task.py": changed}, tmp_path / "stage",
                           ["{python}", "csv_task.py", "--input", "data.csv", "--column", "amount", "--output", "stage.json"])
    assert report["test_exit_code"] == 0
    with pytest.raises(PermissionError):
        activate_upgrade(bundle, tmp_path / "stage", {})
    activate_upgrade(bundle, tmp_path / "stage", {"approved_by_user": True, "version": report["version"]})
    assert invoke(bundle, "summarize_csv", {})["exit_code"] == 0
    _stub_model_choice(bundle, "summarize_csv")
    launched = subprocess.run([sys.executable, str(bundle / "agent.py"), "summarize_csv"],
                              capture_output=True, text=True)
    assert launched.returncode == 0, launched.stderr
    agent_record = json.loads(launched.stdout)
    assert agent_record["verified_output"] is True
    assert str(bundle / "versions" / report["version"]) in agent_record["params"]["output"]
    assert "average" in json.loads((bundle / "versions" / report["version"] / "summary.json").read_text(encoding="utf-8"))
    assert rollback(bundle) == "base"
    assert invoke(bundle, "summarize_csv", {})["exit_code"] == 0
    assert "average" not in json.loads((bundle / "project" / "summary.json").read_text(encoding="utf-8"))
