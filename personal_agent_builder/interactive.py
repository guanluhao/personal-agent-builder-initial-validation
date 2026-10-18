"""Simple local entrypoint for projects and generated agents."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

from .analysis import save_report, capability_report
from .generator import generate


ROOT = Path(__file__).resolve().parents[1]


def _path_answer(prompt: str) -> Path:
    return Path(input(prompt).strip().strip('"')).expanduser().resolve()


def _inspect() -> None:
    source = _path_answer("请输入 Python 项目文件夹路径：")
    output = ROOT / "artifacts" / ("analysis_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    result = save_report(source, output)
    print(capability_report(result))
    print(f"说明与结构记录已保存到：{output}")
    print("以上候选只来自静态分析；确认工具及实际测试后才能生成新 Agent。")


def _start_agent(script: Path) -> None:
    if not script.is_file():
        raise FileNotFoundError(f"未找到已生成 Agent：{script}")
    print("输入自然语言需求；输入 quit 结束该 Agent 对话并返回 Builder 菜单。")
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=False)


def _generate() -> None:
    print("高级入口：需要已审查的能力清单 JSON 和与其摘要匹配的用户确认 JSON。")
    source = _path_answer("Python 项目目录：")
    proposal_path = _path_answer("能力清单 JSON：")
    approval_path = _path_answer("确认记录 JSON：")
    output = _path_answer("新 Agent 输出目录（必须不存在）：")
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    result = generate(source, proposal, approval, output)
    print(f"Agent 已生成：{result}")


def main() -> None:
    print("智能体构建器｜本地版")
    while True:
        agents = sorted((ROOT / "agents").glob("*/agent.py"))
        print("\n1 分析新的 Python 项目  2 根据已确认能力清单生成智能体")
        for number, script in enumerate(agents, start=3):
            print(f"{number} 使用已生成的智能体：{script.parent.name}")
        print("0 退出")
        try:
            choice = input("请选择：").strip().lower()
        except EOFError:
            break
        if choice in {"0", "退出", "quit", "exit"}:
            break
        try:
            if choice in {"1", "分析"}:
                _inspect()
            elif choice in {"2", "生成"}:
                _generate()
            elif choice.isdigit() and 3 <= int(choice) < 3 + len(agents):
                _start_agent(agents[int(choice) - 3])
            else:
                print("请选择菜单中显示的序号。")
        except (OSError, ValueError, KeyError, PermissionError) as exc:
            print(f"未完成：{exc}")


if __name__ == "__main__":
    main()
