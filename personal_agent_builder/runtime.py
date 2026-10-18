"""Runtime copied into generated agents. No shell or arbitrary command input."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone
from jsonschema import validate as validate_json, ValidationError


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def validate_params(tool: dict, params: dict, project: Path) -> dict:
    schema = tool["parameters"]
    if set(params) - set(schema):
        raise ValueError("含未声明参数")
    clean = {}
    for name, rule in schema.items():
        if name not in params:
            if rule.get("required", False):
                raise ValueError(f"缺少参数：{name}")
            if "default" not in rule:
                continue
            value = rule["default"]
        else:
            value = params[name]
        kind = rule["type"]
        if kind == "integer":
            if isinstance(value, bool):
                raise ValueError(f"{name} 必须是整数")
            value = int(value)
            if "minimum" in rule and value < rule["minimum"] or "maximum" in rule and value > rule["maximum"]:
                raise ValueError(f"{name} 超出范围")
        elif kind in {"string", "path"}:
            if not isinstance(value, str) or len(value) > rule.get("max_length", 200):
                raise ValueError(f"{name} 必须是短文本")
            if kind == "path":
                p = (project / value).resolve()
                if not _inside(p, project):
                    raise ValueError(f"{name} 越出项目副本")
                value = str(p)
        else:
            raise ValueError(f"不支持参数类型：{kind}")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(f"{name} 不在允许值中")
        if kind == "string" and "pattern" in rule and not re.fullmatch(rule["pattern"], value):
            raise ValueError(f"{name} 格式错误")
        clean[name] = value
    return clean


def invoke(bundle: str | Path, tool_name: str, params: dict) -> dict:
    bundle = Path(bundle).resolve()
    active_file = bundle / "versions" / "active.json"
    if active_file.exists():
        active = json.loads(active_file.read_text(encoding="utf-8"))["version"]
        if not re.fullmatch(r"[0-9a-f]{64}", active):
            raise ValueError("版本指针无效")
        project = bundle / "versions" / active
    else:
        project = bundle / "project"
    manifest_path = project / "agent.json"
    manifest = json.loads((manifest_path if manifest_path.is_file() else bundle / "agent.json").read_text(encoding="utf-8"))
    tools = {t["name"]: t for t in manifest["tools"]}
    if tool_name not in tools:
        raise ValueError("未注册能力，请向 Builder 提交升级请求")
    tool = tools[tool_name]
    clean = validate_params(tool, params, project)
    argv = []
    for token in tool["command"]:
        if token == "{python}":
            argv.append(sys.executable)
        elif token.startswith("{") and token.endswith("}"):
            key = token[1:-1]
            if key not in clean:
                raise ValueError(f"命令参数缺失：{key}")
            argv.append(str(clean[key]))
        else:
            argv.append(token)
    if not argv or argv[0] != sys.executable or len(argv) < 2:
        raise ValueError("命令必须使用当前 Python 解释器")
    script = (project / argv[1]).resolve()
    if not _inside(script, project) or not script.is_file() or script.suffix != ".py":
        raise ValueError("执行入口必须是项目副本中的 Python 文件")
    argv[1] = str(script)
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE"}}
    env["PYTHONNOUSERSITE"] = "1"
    started = datetime.now(timezone.utc).isoformat()
    try:
        result = subprocess.run(argv, cwd=project, env=env, capture_output=True, text=True,
                                timeout=tool.get("timeout_seconds", 30), shell=False)
        record = {"tool": tool_name, "params": clean, "started_utc": started, "exit_code": result.returncode,
                  "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:], "verified_output": False}
        if result.returncode == 0 and "output_schema" in tool:
            try:
                output = json.loads(result.stdout.strip().splitlines()[-1])
                validate_json(output, tool["output_schema"])
                for field in tool.get("artifact_fields", []):
                    items = output[field] if isinstance(output[field], list) else [output[field]]
                    for item in items:
                        artifact = (project / item).resolve()
                        if not _inside(artifact, project) or not artifact.is_file() or artifact.stat().st_size == 0:
                            raise ValueError(f"输出文件无效：{item}")
                for field in tool.get("artifact_params", []):
                    artifact = Path(clean[field]).resolve()
                    if not _inside(artifact, project) or not artifact.is_file() or artifact.stat().st_size == 0:
                        raise ValueError(f"输出文件无效：{artifact}")
                record["verified_output"] = True
            except (json.JSONDecodeError, ValidationError, ValueError, KeyError, TypeError, IndexError) as exc:
                record["validation_error"] = str(exc)[:500]
    except subprocess.TimeoutExpired:
        record = {"tool": tool_name, "params": clean, "started_utc": started, "exit_code": None,
                  "stdout": "", "stderr": "执行超时", "verified_output": False}
    logs = bundle / "records"
    logs.mkdir(exist_ok=True)
    with (logs / "calls.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def request_upgrade(bundle: str | Path, request: str) -> Path:
    bundle = Path(bundle)
    target = bundle / "records" / "upgrade_requests.jsonl"
    target.parent.mkdir(exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"created_utc": datetime.now(timezone.utc).isoformat(), "request": request}, ensure_ascii=False) + "\n")
    return target
