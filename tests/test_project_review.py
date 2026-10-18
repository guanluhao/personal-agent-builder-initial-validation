from personal_agent_builder.analysis import inspect_project, capability_report
from personal_agent_builder.project_review import _evidence, enrich_project


def test_multi_file_review_uses_current_files_instead_of_readme_guess(tmp_path):
    (tmp_path / "README.md").write_text("# 资料\n预先写好的说法。\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "changes.md").write_text("过去测试的现成答案", encoding="utf-8")
    (tmp_path / "worker.py").write_text('def calculate(values):\n    return sum(values)\n', encoding="utf-8")
    (tmp_path / "entry.py").write_text('''"""处理数字。"""
import argparse
import json
from pathlib import Path
from worker import calculate

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = {"total": calculate([1, 2])}
    args.output.write_text(json.dumps(result), encoding="utf-8")
    print(json.dumps(result))
''', encoding="utf-8")
    analysis = inspect_project(tmp_path)
    assert "预先写好的说法" in analysis["summary"]
    assert "def calculate" in _evidence(tmp_path, analysis)
    assert "过去测试的现成答案" not in _evidence(tmp_path, analysis)
    checked = enrich_project(tmp_path, analysis, planner=lambda root, current: {
        "purpose": "根据本次上传代码计算数值合计。", "outputs": "生成合计结果文件。",
        "examples": ["请计算这份数字的合计。"],
        "tools": [{"name": "entry", "description": "计算数字合计并保存结果。",
                   "parameters": [{"name": "input", "label": "输入文件", "hint": "选择要处理的文件。", "role": "input_file"},
                                  {"name": "output", "label": "结果位置", "hint": "程序会创建结果文件。", "role": "output_path"}]}]})
    assert checked["model_review"] is True
    assert checked["summary"] == "根据本次上传代码计算数值合计。"
    assert "预先写好的说法" not in capability_report(checked)
    assert checked["recommended_tools"][0]["arguments"][0]["help"] == "输入文件"
    assert checked["recommended_tools"][0]["arguments"][1]["role"] == "output_path"
