import json

from personal_agent_builder.analysis import capability_report, inspect_project
from personal_agent_builder.generator import generate, proposal_hash
from personal_agent_builder.proposal import propose_tools
from personal_agent_builder.runtime import invoke
from personal_agent_builder.single_file import enrich_single_file


ADAPTER = '''"""计算给定数值的两倍。"""
import argparse
import json
from mystery import compute

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--value", required=True, help="需要计算的数值")
    args = parser.parse_args()
    print(json.dumps({"result": compute(args.value)}))
'''


def test_unknown_single_file_is_described_without_execution_then_generates_agent(tmp_path):
    source = tmp_path / "uploaded"
    source.mkdir()
    (source / "mystery.py").write_text(
        "from pathlib import Path\n"
        "def compute(value):\n"
        "    Path('called').write_text('yes', encoding='utf-8')\n"
        "    return int(value) * 2\n", encoding="utf-8")
    analysis = inspect_project(source)
    assert not analysis["recommended_tools"]
    enriched = enrich_single_file(source, analysis, planner=lambda *_: {
        "purpose": "计算数值的两倍。", "outputs": "返回计算数值。",
        "examples": ["请计算 6 的结果。"], "status": "ready", "reason": "",
        "adapter_source": ADAPTER})
    assert not (source / "called").exists()
    assert "计算数值的两倍" in capability_report(enriched)
    assert [x["file"] for x in enriched["recommended_tools"]] == ["agent_entry.py"]
    proposal, _ = propose_tools(source, enriched, ["agent_entry"])
    bundle = generate(source, proposal,
                      {"approved_by_user": True, "proposal_sha256": proposal_hash(proposal)},
                      tmp_path / "agent")
    assert not (source / "called").exists()
    record = invoke(bundle, "agent_entry", {"value": "6"})
    assert record["exit_code"] == 0 and record["verified_output"] is True
    assert json.loads(record["stdout"])["result"] == 12
    assert (bundle / "project" / "called").read_text(encoding="utf-8") == "yes"
    assert not (source / "called").exists()


def test_single_file_with_import_time_work_is_not_wrapped(tmp_path):
    (tmp_path / "mystery.py").write_text(
        "from pathlib import Path\nPath('called').write_text('yes')\n"
        "def compute(value): return int(value) * 2\n", encoding="utf-8")
    enriched = enrich_single_file(tmp_path, inspect_project(tmp_path), planner=lambda *_: {
        "purpose": "计算数值的两倍。", "outputs": "返回计算数值。",
        "examples": ["请计算 6 的结果。"], "status": "ready", "reason": "",
        "adapter_source": ADAPTER})
    assert not enriched["recommended_tools"]
    assert not (tmp_path / "agent_entry.py").exists()
    assert not (tmp_path / "called").exists()


def test_single_file_parameter_explanations_follow_actual_arguments(tmp_path):
    source = __import__("pathlib").Path(__file__).resolve().parents[1] / "examples" / "csv_project" / "csv_task.py"
    (tmp_path / "csv_task.py").write_bytes(source.read_bytes())
    enriched = enrich_single_file(tmp_path, inspect_project(tmp_path), planner=lambda *_: {
        "purpose": "汇总表格中的数字。", "outputs": "生成结果文件。", "examples": [],
        "status": "ready", "reason": "", "adapter_source": "",
        "parameters": [
            {"name": "input", "label": "要处理的表格文件", "hint": "请选择有表头的表格文件。", "role": "input_file"},
            {"name": "column", "label": "要统计的列名", "hint": "填写表格第一行的列名。", "role": "value"},
            {"name": "output", "label": "结果保存位置", "hint": "运行时会新建这个文件。", "role": "output_path"},
            {"name": "fiction", "label": "不存在", "hint": "不存在", "role": "value"}]})
    proposal, _ = propose_tools(tmp_path, enriched, ["csv_task"])
    params = proposal["tools"][0]["parameters"]
    assert params["input"]["description"] == "要处理的表格文件"
    assert params["input"]["hint"] == "请选择有表头的表格文件。"
    assert params["input"]["role"] == "input_file"
    assert params["output"]["role"] == "output_path"
    assert "fiction" not in params
