from pathlib import Path
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
