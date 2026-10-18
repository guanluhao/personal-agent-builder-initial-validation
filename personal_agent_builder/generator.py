"""Approval-gated generator for independently runnable agent bundles."""

from __future__ import annotations

import hashlib
import json
import keyword
from pathlib import Path
import shutil


def _canonical(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def proposal_hash(proposal: dict) -> str:
    return hashlib.sha256(_canonical(proposal).encode("utf-8")).hexdigest()


def _validate(proposal: dict, source: Path) -> None:
    if not isinstance(proposal.get("tools"), list) or not proposal["tools"]:
        raise ValueError("至少选择一个能力")
    names = set()
    for tool in proposal["tools"]:
        name = tool["name"]
        if not name.isidentifier() or keyword.iskeyword(name) or name in names:
            raise ValueError("能力名称无效或重复")
        names.add(name)
        if not tool.get("description") or not tool.get("command") or tool["command"][0] != "{python}":
            raise ValueError("能力缺少说明或合法命令")
        for key, rule in tool["parameters"].items():
            if not key.isidentifier() or keyword.iskeyword(key) or rule["type"] not in {"string", "path", "integer"}:
                raise ValueError("参数定义无效")
    for rel in proposal["copy_paths"]:
        parts = Path(rel).parts
        name = Path(rel).name.lower()
        if (any(part.startswith(".") for part in parts)
                or name in {"id_rsa", "id_ed25519", "credentials", "credentials.json", "secrets.json"}
                or Path(rel).suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".npy", ".npz", ".dds"}):
            raise ValueError(f"拒绝复制凭据、隐藏路径或大型计算数据：{rel}")
        p = (source / rel).resolve()
        if not p.is_relative_to(source) or not p.is_file() or p.is_symlink():
            raise ValueError(f"项目文件无效：{rel}")
        if p.stat().st_size > 1_000_000:
            raise ValueError(f"项目文件过大：{rel}")


def _mcp_source(tools: list[dict], interaction: dict | None = None) -> str:
    interaction = interaction or {}
    usage = ("只调用已注册且经过用户批准的能力；信息不足时先向用户提问。"
             f"使用方式：{str(interaction.get('usage', '按用户当前需求使用。'))[:1000]} "
             f"交互约定：{str(interaction.get('behavior', ''))[:1000]} "
             f"当前限制：{str(interaction.get('limits', ''))[:1000]} "
             "缺失能力须由构建器在副本中准备、验证，并经用户确认后启用。")
    body = ["from pathlib import Path", "import json", "from mcp.server import MCPServer",
            "from runtime import invoke", "", "ROOT = Path(__file__).resolve().parent", "mcp = MCPServer('Personal Agent')", ""]
    for tool in tools:
        params = tool["parameters"]
        args = []
        for name, rule in params.items():
            typ = "int" if rule["type"] == "integer" else "str"
            if "default" in rule:
                args.append(f"{name}: {typ} = {rule['default']!r}")
            else:
                args.append(f"{name}: {typ}")
        kwargs = ", ".join(f"{name!r}: {name}" for name in params)
        signature = "*, " + ", ".join(args) if args else ""
        body.extend(["@mcp.tool()", f"def {tool['name']}({signature}) -> dict:",
                     f"    {tool['description']!r}",
                     f"    return invoke(ROOT, {tool['name']!r}, {{{kwargs}}})", ""])
    body.extend(["@mcp.resource('agent://capabilities')", "def capabilities() -> str:",
                 "    return (ROOT / 'agent.json').read_text(encoding='utf-8')", "",
                 "@mcp.prompt()", "def usage() -> str:",
                 "    override = ROOT / 'interaction.json'",
                 "    if override.is_file():",
                 "        info = json.loads(override.read_text(encoding='utf-8'))",
                 "        if info.get('usage'):",
                 "            return '；'.join(str(info.get(key, '')) for key in ('usage', 'behavior', 'limits'))",
                 f"    return {usage!r}", "",
                 "if __name__ == '__main__':", "    mcp.run()", ""])
    return "\n".join(body)


def _bundle_readme(manifest: dict) -> str:
    tool_lines = "\n".join(f"- `{tool['name']}`：{tool['description']}" for tool in manifest["tools"])
    interaction = manifest.get("interaction", {})
    personal_guide = ("\n## 使用方式\n\n"
                      f"{interaction.get('usage', '根据需要描述任务。')}\n\n"
                      f"{interaction.get('behavior', '信息不足时会先询问。')}\n\n"
                      f"当前限制：{interaction.get('limits', '以已批准的程序功能为准。')}\n")
    return (f"# {manifest['name']}\n\n"
            "这是经用户确认范围后生成的独立 Agent。项目副本在 `project/`，调用记录保存在 `records/`。\n\n"
            "## 启动\n\n"
            "安装本项目 README 所列 Python 依赖后，在此目录运行 `python agent.py` 进入终端对话，"
            "或运行 `python agent.py <需求>` 执行一次请求。`python mcp_server.py` 启动 MCP stdio 服务。\n\n"
            "每条对话都会由本机已登录的官方语言模型分析；无需 API Key。"
            "当前 Agent 只会执行已确认的程序功能，不会自行安装项目依赖。\n\n"
            f"## 已注册能力\n\n{tool_lines}\n"
            f"{personal_guide}\n"
            "新需求会写入 `records/upgrade_requests.jsonl`，经 Builder 隔离修改、测试和用户确认后才能启用。\n")


AGENT_SCRIPT = '''from pathlib import Path
import json
import re
import subprocess
import sys
from datetime import datetime
from runtime import invoke, request_upgrade
from router import route
from conversation import last_verified_result, remember_result, recent_turns, remember_turn, readable_files, read_project_file, preview_attached_file

ROOT = Path(__file__).resolve().parent

def active_project():
    pointer = ROOT / "versions" / "active.json"
    if pointer.exists():
        version = json.loads(pointer.read_text(encoding="utf-8"))["version"]
        if not re.fullmatch(r"[0-9a-f]{64}", version):
            raise ValueError("版本指针无效")
        return ROOT / "versions" / version
    return ROOT / "project"

def ask(message: str, attached_files=None):
    project = active_project()
    manifest_path = project / "agent.json"
    manifest = json.loads((manifest_path if manifest_path.is_file() else ROOT / "agent.json").read_text(encoding="utf-8"))
    interaction_file = ROOT / "interaction.json"
    if interaction_file.is_file():
        manifest["interaction"] = json.loads(interaction_file.read_text(encoding="utf-8"))
    context_path = ROOT / "records" / "context.json"
    context = json.loads(context_path.read_text(encoding="utf-8")) if context_path.exists() else {}
    previous = last_verified_result(ROOT, context)
    if previous is not None:
        context["last_verified_result"] = previous
    new_files = bool(attached_files)
    attached_files = attached_files or context.get("pending_attached_files", [])
    if not isinstance(attached_files, list) or len(attached_files) > 3 or any(not isinstance(x, str) for x in attached_files):
        raise ValueError("本次附加文件无效")
    missing_attached_files = []
    try:
        previews = [preview_attached_file(project, name) for name in attached_files]
    except ValueError as exc:
        if new_files:
            response = {"status": "needs_clarification", "message": str(exc)}
            remember_turn(context, message, response, [])
            context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
            return response
        missing_attached_files = attached_files
        attached_files, previews = [], []
        context.pop("pending_attached_files", None)
    if attached_files:
        context["pending_attached_files"] = attached_files
        context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    routing_context = {**context, "available_files": readable_files(project), "file_evidence": {},
                       "attached_files": attached_files, "attached_previews": previews,
                       "recent_turns": recent_turns(context),
                       "missing_attached_files": missing_attached_files}
    def respond(response):
        remember_turn(context, message, response, attached_files)
        context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
        return response
    for _ in range(4):
        try:
            choice = route(message, manifest, routing_context)
        except RuntimeError as exc:
            return respond({"status": "service_unavailable", "message": str(exc)})
        if choice["action"] != "read_file":
            break
        filename = choice.get("file", "")
        if filename in routing_context["file_evidence"]:
            return respond({"status": "needs_clarification", "message": "这次无法继续查看同一文件，请换一种问法。"})
        try:
            routing_context["file_evidence"][filename] = read_project_file(project, filename, routing_context["available_files"])
        except (OSError, UnicodeError, ValueError) as exc:
            return respond({"status": "needs_clarification", "message": str(exc)})
    else:
        return respond({"status": "needs_clarification", "message": "需要查看的文件较多，请把问题说得更具体一些。"})
    if choice["action"] == "clarify":
        return respond({"status": "needs_clarification", "message": choice.get("reason", "请再说具体一些。")})
    if choice["action"] == "answer":
        if previous is None and choice.get("answer_kind") in {"history", "result"} and not routing_context["file_evidence"]:
            return respond({"status": "needs_clarification", "message": "目前还没有可以解释的运行结果。请先让程序完成一次任务。"})
        return respond({"status": "answered", "message": choice.get("reason", "请查看已批准的功能和运行结果。"),
                        "source_verified": previous is not None, "file_reviewed": bool(routing_context["file_evidence"])})
    if choice["action"] == "personalize_request":
        return respond({"status": "personalization_requested",
                        "message": choice.get("reason", "请核对这个智能体的调整方案。")})
    if choice["action"] != "invoke":
        resolved = choice.get("resolved_request", "")
        resolved = resolved.strip()[:1000] if isinstance(resolved, str) else ""
        resolved = resolved or message.strip()
        path = request_upgrade(ROOT, resolved)
        return respond({"status": "upgrade_requested", "record": str(path),
                        "message": choice.get("reason", "未找到已注册能力"), "upgrade_request": resolved})
    tool_name, params = choice["tool"], {k: v for k, v in choice["parameters"].items() if v is not None}
    tool = next(t for t in manifest["tools"] if t["name"] == tool_name)
    if "config" in tool["parameters"] and "config" not in params and context.get("latest_config"):
        params["config"] = context["latest_config"]
    if "source" in tool["parameters"] and "source" not in params:
        group = tool.get("context_group")
        previous = context.get("latest_config_by_group", {}).get(group) if group else context.get("latest_config")
        if previous:
            params["source"] = previous
    if "output" in tool["parameters"]:
        wanted = params.get("output", tool["parameters"]["output"].get("default"))
        if wanted and (project / wanted).exists():
            path = Path(wanted)
            params["output"] = str(path.with_name(path.stem + "_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + path.suffix))
    result = invoke(ROOT, tool_name, params)
    if result["exit_code"] == 0 and result.get("verified_output"):
        remember_result(context, result)
        context.pop("pending_attached_files", None)
        if attached_files:
            context["last_attached_files"] = attached_files
        try:
            output = json.loads(result["stdout"].strip().splitlines()[-1])
            if isinstance(output, dict) and "config" in output:
                latest = str(Path(output["config"]).resolve().relative_to(project.resolve()))
                context["latest_config"] = latest
                if tool.get("context_group"):
                    context.setdefault("latest_config_by_group", {})[tool["context_group"]] = latest
        except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError):
            pass
        try:
            from router import describe_result
            payload = json.loads(result["stdout"].strip().splitlines()[-1])
            if isinstance(payload, dict):
                result["message"] = describe_result(payload, tool["description"])
        except (ImportError, OSError, ValueError, RuntimeError, KeyError, IndexError, TypeError,
                subprocess.TimeoutExpired):
            result["message"] = "本次运行已完成，结果已通过检查。可展开查看原始结果。"
    return respond(result)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--request-json":
        request = json.load(sys.stdin)
        print(json.dumps(ask(request["message"], request.get("attached_files", [])), ensure_ascii=False, indent=2))
    elif len(sys.argv) > 1:
        print(json.dumps(ask(" ".join(sys.argv[1:])), ensure_ascii=False, indent=2))
    else:
        print("个人智能体：请直接说出你的需求；输入“退出”结束。")
        while True:
            try:
                message = input("> ").strip()
            except EOFError:
                break
            if message.lower() in {"quit", "exit", "退出"}:
                break
            try:
                print(json.dumps(ask(message), ensure_ascii=False, indent=2))
            except Exception as exc:
                print(f"请求失败：{exc}")
'''


def generate(source: str | Path, proposal: dict, approval: dict, output: str | Path) -> Path:
    source = Path(source).resolve()
    output = Path(output).resolve()
    _validate(proposal, source)
    digest = proposal_hash(proposal)
    if approval.get("proposal_sha256") != digest or approval.get("approved_by_user") is not True:
        raise PermissionError("用户尚未确认此能力范围")
    if output.exists():
        raise FileExistsError("目标 Agent 目录已存在；拒绝覆盖")
    output.mkdir(parents=True)
    (output / "project").mkdir()
    for rel in proposal["copy_paths"]:
        dest = output / "project" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, dest)
    manifest = {"name": proposal["name"], "source": source.name, "proposal_sha256": digest,
                "tools": proposal["tools"], "interaction": proposal.get("interaction", {}),
                "status": "approved"}
    (output / "agent.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "interaction.json").write_text(json.dumps(manifest["interaction"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "README.md").write_text(_bundle_readme(manifest), encoding="utf-8")
    (output / "approval.json").write_text(json.dumps(approval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "agent.py").write_text(AGENT_SCRIPT, encoding="utf-8")
    (output / "mcp_server.py").write_text(_mcp_source(proposal["tools"], manifest["interaction"]), encoding="utf-8")
    shutil.copy2(Path(__file__).with_name("runtime.py"), output / "runtime.py")
    shutil.copy2(Path(__file__).with_name("router.py"), output / "router.py")
    shutil.copy2(Path(__file__).with_name("conversation.py"), output / "conversation.py")
    (output / "records").mkdir()
    return output
