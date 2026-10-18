"""Command line entry for analysis and approved generation."""

import argparse
import json
from pathlib import Path
from .analysis import save_report
from .generator import generate, proposal_hash


def main():
    ap = argparse.ArgumentParser(prog="personal-agent-builder")
    sub = ap.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="只读分析项目")
    inspect.add_argument("source")
    inspect.add_argument("output")
    digest = sub.add_parser("proposal-hash", help="计算确认所需摘要")
    digest.add_argument("proposal")
    build = sub.add_parser("generate", help="核验用户确认并生成 Agent")
    for name in ("source", "proposal", "approval", "output"):
        build.add_argument(name)
    args = ap.parse_args()
    if args.command == "inspect":
        result = save_report(args.source, args.output)
        print(json.dumps({"python_files": result["python_files"], "output": args.output}, ensure_ascii=False))
    elif args.command == "proposal-hash":
        print(proposal_hash(json.loads(Path(args.proposal).read_text(encoding="utf-8"))))
    else:
        proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
        approval = json.loads(Path(args.approval).read_text(encoding="utf-8"))
        print(generate(args.source, proposal, approval, args.output))


if __name__ == "__main__":
    main()
