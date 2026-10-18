"""Static analysis only: never import or execute the inspected project."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re

SKIP = {".git", ".venv", "venv", "__pycache__", "node_modules", "output", "results", "artifacts"}
MAX_FILE = 500_000
MAX_FILES = 500


def _cli_candidate(tree: ast.Module, rel: str) -> dict | None:
    """Recognize a simple argparse entrypoint without evaluating user code."""
    main_blocks = [node for node in tree.body if isinstance(node, ast.If)
                   and isinstance(node.test, ast.Compare)
                   and any(isinstance(x, ast.Constant) and x.value == "__main__" for x in ast.walk(node.test))]
    if len(main_blocks) != 1:
        return None
    block = main_blocks[0]
    scan = block
    if not any(isinstance(node, ast.Attribute) and node.attr == "ArgumentParser" for node in ast.walk(block)):
        called = {node.id for call in ast.walk(block) if isinstance(call, ast.Call)
                  for node in [call.func] if isinstance(node, ast.Name)}
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in called]
        if len(functions) == 1:
            scan = functions[0]
    parser_names = {target.id for node in ast.walk(scan) if isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr == "ArgumentParser"
                    for target in node.targets if isinstance(target, ast.Name)}
    if len(parser_names) != 1 or any(isinstance(node, ast.Attribute) and node.attr == "add_subparsers"
                                    for node in ast.walk(scan)):
        return None
    parser_name = next(iter(parser_names))
    calls = [node for node in ast.walk(scan) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument"
             and isinstance(node.func.value, ast.Name) and node.func.value.id == parser_name]
    if not calls:
        return None
    arguments = []
    for call in sorted(calls, key=lambda node: node.lineno):
        if not call.args or not isinstance(call.args[0], ast.Constant) or not isinstance(call.args[0].value, str):
            return None
        flag = call.args[0].value
        if not re.fullmatch(r"--[a-z][a-z0-9-]*", flag):
            return None
        kw = {item.arg: item.value for item in call.keywords if item.arg}
        if any(key in kw for key in ("action", "nargs", "dest", "const")):
            return None
        type_node = kw.get("type")
        typename = (type_node.id if isinstance(type_node, ast.Name) else "str") if type_node else "str"
        if typename not in {"Path", "int", "str"}:
            return None
        required = isinstance(kw.get("required"), ast.Constant) and kw["required"].value is True
        default = None
        if "default" in kw:
            try:
                default = ast.literal_eval(kw["default"])
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                expr = kw["default"]
                if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "Path"
                        and len(expr.args) == 1 and not expr.keywords and isinstance(expr.args[0], ast.Constant)
                        and isinstance(expr.args[0].value, str)):
                    default = expr.args[0].value
                else:
                    return None
        arguments.append({"flag": flag, "name": flag[2:].replace("-", "_"),
                          "type": {"Path": "path", "int": "integer", "str": "string"}[typename],
                          "required": required, "default": default,
                          "help": (kw["help"].value[:100] if isinstance(kw.get("help"), ast.Constant)
                                   and isinstance(kw["help"].value, str) else "")})
    name = re.sub(r"[^A-Za-z0-9_]+", "_", Path(rel).with_suffix("").as_posix()).strip("_")
    if not name or name[0].isdigit():
        return None
    return {"name": name, "file": rel, "line": block.lineno,
            "description": (ast.get_docstring(tree) or "Python 命令入口").splitlines()[0][:200],
            "arguments": arguments, "evidence": "AST 中的 argparse 命令入口",
            "status": "仅为候选；输入输出及副作用需审查和实际测试"}


def _safe_files(root: Path):
    count = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file() or any(part in SKIP for part in path.relative_to(root).parts):
            continue
        if path.stat().st_size > MAX_FILE:
            continue
        count += 1
        if count > MAX_FILES:
            break
        yield path


def inspect_project(root: str | Path) -> dict:
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"项目目录不存在：{root}")
    files = list(_safe_files(root))
    pyfiles = [p for p in files if p.suffix == ".py"]
    docs = [p for p in files if p.name.lower() in {"readme.md", "pyproject.toml", "requirements.txt"}]
    functions, entrypoints, errors, candidates = [], [], [], []
    for path in pyfiles:
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=rel)
        except (SyntaxError, UnicodeError) as exc:
            errors.append({"file": rel, "error": str(exc)[:160]})
            continue
        candidate = _cli_candidate(tree, rel)
        if candidate:
            candidates.append(candidate)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                functions.append({"name": node.name, "file": rel, "line": node.lineno,
                                  "doc": (ast.get_docstring(node) or "").splitlines()[0][:200] if ast.get_docstring(node) else "",
                                  "parameters": [a.arg for a in node.args.args]})
            if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
                if any(isinstance(x, ast.Constant) and x.value == "__main__" for x in ast.walk(node.test)):
                    entrypoints.append({"file": rel, "line": node.lineno, "evidence": "if __name__ == '__main__'"})
    dependencies = []
    req = root / "requirements.txt"
    if req.is_file() and not req.is_symlink() and req.stat().st_size < MAX_FILE:
        dependencies = [line.strip() for line in req.read_text(encoding="utf-8-sig").splitlines()
                        if line.strip() and not line.lstrip().startswith("#")]
    readme = root / "README.md"
    summary = "未找到 README；程序用途需要人工核实。"
    output_hint = ""
    if readme.is_file() and readme.stat().st_size < MAX_FILE:
        for line in readme.read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.startswith("#"):
                if summary.startswith("未找到 README"):
                    summary = line.strip()[:300]
                if line.strip().startswith(("输出：", "结果：")):
                    output_hint = line.strip().split("：", 1)[1].strip()[:300]
    return {"root": str(root), "summary": summary, "output_hint": output_hint,
            "file_count": len(files), "python_files": len(pyfiles),
            "documents": [p.relative_to(root).as_posix() for p in docs], "dependencies": dependencies,
            "entrypoints": entrypoints, "functions": functions, "parse_errors": errors,
            "verification": "静态分析；未导入模块、未运行功能", "recommended_tools": candidates,
            "risks": ["项目代码未经信任审查，不应自动安装依赖或执行任意入口。"]}


def capability_report(analysis: dict) -> str:
    candidates = analysis.get("recommended_tools", [])
    purpose = analysis.get("summary") or "用途需要进一步核对。"
    examples = analysis.get("examples") or ["请" + item["description"].rstrip("。") + "。" for item in candidates[:3]
                                            if any("\u4e00" <= ch <= "\u9fff" for ch in item["description"])]
    if not examples:
        examples = ["请使用项目中已确认的功能处理我的资料。"] if candidates else ["尚无可保证执行的示例指令。"]
    result = analysis.get("output_hint") or "具体输出形式要等确认功能并实际测试后才能确定。"
    lines = ["# 项目能力说明", "", "以下根据源码推断，尚未运行验证；确认生成后才会试运行所选功能。", "",
             "## 这个项目能完成什么", purpose, "", "## 你可以发送哪些指令（示例）"]
    lines.extend(f"- {example}" for example in examples)
    lines += ["", "## 最后会得到什么结果", result]
    return "\n".join(lines) + "\n"


def save_report(root: str | Path, output: str | Path) -> dict:
    result = inspect_project(root)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "项目能力说明.md").write_text(capability_report(result), encoding="utf-8")
    return result
