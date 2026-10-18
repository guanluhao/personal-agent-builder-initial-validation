"""Local-only, initially empty Builder UI. Imported project code runs only after approval."""

from __future__ import annotations

import io
from itertools import islice
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import subprocess
import sys
import threading
from urllib.parse import parse_qs, quote, unquote, urlsplit
from urllib.request import urlopen
from uuid import uuid4
import webbrowser
import zipfile

from .analysis import capability_report, inspect_project
from .conversation import recent_turns, remember_turn
from .generator import generate, proposal_hash
from .proposal import COPY_SUFFIXES, propose_tools
from .planning import ask_questions, finalize_plan
from .revision import (apply_change, current_interaction, discuss_change, finalize_change,
                       interaction_digest, rollback_change)
from .runtime import invoke
from .single_file import enrich_single_file
from .project_review import enrich_project
from .upgrade import activate_upgrade, rollback
from .upgrade_builder import prepare_upgrade

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / "pyproject.toml").is_file():
    ROOT = Path.cwd()
DATA = ROOT / "artifacts" / "web_builder"
WEB = Path(__file__).with_name("web")
MAX_UPLOAD_FILE = 1_000_000
MAX_UPLOAD_TOTAL = 20_000_000
MAX_UPLOAD_COUNT = 250
DENY_PARTS = {".git", ".venv", "venv", "__pycache__", "node_modules", "artifacts", "records", "versions"}
DENY_NAMES = {".env", "credentials.json", "secrets.json", "id_rsa", "id_ed25519"}
ALLOWED = COPY_SUFFIXES | {".png", ".svg", ".jpg", ".jpeg"}


def _safe_relative(raw: str) -> PurePosixPath:
    raw = unquote(raw).replace("\\", "/")
    path = PurePosixPath(raw)
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if (not raw or raw.startswith("/") or len(raw) > 240 or ":" in raw or
            any(part in {"", ".", ".."} or part.startswith(".") or part.lower() in DENY_PARTS for part in path.parts) or
            path.name.lower() in DENY_NAMES or path.suffix.lower() not in ALLOWED or
            any(part.split(".")[0].upper() in reserved for part in path.parts) or
            any(part.endswith((" ", ".")) for part in path.parts) or
            path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".npy", ".npz", ".dds"}):
        raise ValueError(f"拒绝不安全或不支持的文件：{raw[:100]}")
    return path


def _valid_id(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("无效的会话或 Agent 编号")
    return value


def _project_name(value: object) -> str:
    """Keep an imported archive's human-readable name out of generated paths."""
    if not isinstance(value, str) or len(value) > 80:
        return "python-project"
    return re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")[:48] or "python-project"


def _project_dir(upload: Path) -> Path:
    items = [p for p in upload.iterdir() if p.name != "analysis"]
    if len(items) == 1 and items[0].is_dir():
        return items[0]
    return upload


def _artifact_list(agent: Path, record: dict) -> list[dict]:
    active = agent / "versions" / "active.json"
    if active.exists():
        version = json.loads(active.read_text(encoding="utf-8"))["version"]
        if not re.fullmatch(r"[0-9a-f]{64}", version):
            return []
        project = agent / "versions" / version
    else:
        project = agent / "project"
    candidates = []
    try:
        payload = json.loads(record.get("stdout", "").strip().splitlines()[-1])
    except (ValueError, IndexError, TypeError):
        payload = {}

    def visit(value):
        if isinstance(value, str):
            candidates.append(value)
        elif isinstance(value, list):
            for item in value[:30]:
                visit(item)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)

    visit(payload)
    visit({k: v for k, v in record.get("params", {}).items() if k in {"output", "out", "result", "save"}})
    found = {}
    for value in candidates:
        p = (project / value).resolve()
        if not p.is_relative_to(project.resolve()):
            continue
        paths = [p] if p.is_file() else (list(islice(p.rglob("*"), 25)) if p.is_dir() else [])
        for item in paths:
            if (item.is_file() and item.stat().st_size <= 10_000_000 and
                    (item.suffix.lower() in {".png", ".jpg", ".jpeg", ".svg", ".json", ".csv", ".tsv", ".txt"} or
                     (item == p and value in record.get("params", {}).values()))):
                rel = item.relative_to(project).as_posix()
                found[rel] = {"name": rel, "size": item.stat().st_size,
                              "url": f"/api/artifact?agent={agent.name}&path={quote(rel)}"}
        if len(found) >= 30:
            break
    return list(found.values())[:30]


def _agent_manifest(agent: Path) -> tuple[dict, bool]:
    active = agent / "versions" / "active.json"
    versioned = False
    if active.is_file():
        version = json.loads(active.read_text(encoding="utf-8"))["version"]
        if not re.fullmatch(r"[0-9a-f]{64}", version):
            raise ValueError("智能体版本无效")
        path = agent / "versions" / version / "agent.json"
        versioned = True
    else:
        path = agent / "agent.json"
    if not path.is_file():
        path = agent / "agent.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["interaction"] = current_interaction(agent, manifest)
    return manifest, versioned


def _save_upgrade_followup(agent: Path, request: str, question: str) -> None:
    """Make Builder clarification visible to the agent's next conversation turn."""
    path = agent / "records" / "context.json"
    context = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    context["pending_upgrade"] = {"request": request[:1000], "question": question[:1000]}
    remember_turn(context, request, {"status": "needs_clarification", "message": question}, [])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_upgrade_followup(agent: Path) -> None:
    path = agent / "records" / "context.json"
    if path.is_file():
        context = json.loads(path.read_text(encoding="utf-8"))
        if context.pop("pending_upgrade", None) is not None:
            path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")


def _latest_pending_upgrade(agent: Path) -> dict | None:
    """Reopen a tested proposal after the browser or local server restarts."""
    base = agent / "upgrade_proposals"
    if not base.is_dir():
        return None
    active = agent / "versions" / "active.json"
    active_version = json.loads(active.read_text(encoding="utf-8"))["version"] if active.is_file() else ""
    reports = sorted(base.glob("*/project/upgrade_report.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in reports[:30]:
        if not re.fullmatch(r"[0-9a-f]{32}", path.parents[1].name):
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        if not report.get("verified_output") or report.get("activated") or report.get("version") == active_version:
            continue
        stage = path.parent
        manifest = json.loads((stage / "agent.json").read_text(encoding="utf-8"))
        tool = next((item for item in manifest["tools"] if item["name"] == report.get("new_tool")), None)
        if not tool:
            continue
        return {"status": "ready", "proposal_id": path.parents[1].name,
                "version": report["version"], "new_ability": tool["description"],
                "test_passed": True, "assumptions": report.get("assumptions", []),
                "regression": report.get("regression", []),
                "test_result": report.get("test_stdout", "")[-1200:],
                "diff": (stage / "upgrade.diff").read_text(encoding="utf-8")[:30_000]}
    return None


class Handler(BaseHTTPRequestHandler):
    server: "AppServer"

    def log_message(self, fmt: str, *args):
        with (DATA / "server.log").open("a", encoding="utf-8") as f:
            f.write((fmt % args)[:500] + "\n")

    def _json(self, code: int, value: dict | list):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self, limit: int = 100_000) -> bytes:
        size = int(self.headers.get("Content-Length", "0"))
        if size < 0 or size > limit:
            raise ValueError("请求内容过大")
        return self.rfile.read(size)

    def _input_json(self) -> dict:
        value = json.loads(self._body().decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("请求必须是 JSON 对象")
        return value

    def _authorized(self) -> bool:
        origin = self.headers.get("Origin")
        expected = f"http://127.0.0.1:{self.server.server_port}"
        return (self.headers.get("X-PAB-Token") == self.server.token and
                (not origin or origin == expected))

    def _valid_host(self) -> bool:
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        if not self._valid_host():
            self._json(403, {"error": "仅允许本机地址访问"})
            return
        try:
            path = urlsplit(self.path)
            if path.path == "/":
                html = (WEB / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", self.server.token)
                self._bytes(200, html.encode("utf-8"), "text/html; charset=utf-8")
            elif path.path in {"/app.js", "/style.css"}:
                name = path.path.lstrip("/")
                kind = "text/javascript" if name.endswith(".js") else "text/css"
                self._bytes(200, (WEB / name).read_bytes(), kind + "; charset=utf-8")
            elif path.path == "/api/agents":
                agents = []
                for folder in sorted((DATA / "agents").glob("*")):
                    if (folder / "agent.json").is_file():
                        manifest, can_rollback = _agent_manifest(folder)
                        interaction = manifest.get("interaction", {})
                        context_file = folder / "records" / "context.json"
                        context = json.loads(context_file.read_text(encoding="utf-8")) if context_file.is_file() else {}
                        agents.append({"id": folder.name, "name": manifest["name"],
                                       "tools": [x["name"] for x in manifest["tools"]],
                                       "tool_descriptions": {x["name"]: x["description"] for x in manifest["tools"]},
                                       "interaction": interaction,
                                       "recent_turns": recent_turns(context),
                                       "pending_revision": json.loads((folder / "records" / "pending_revision.json").read_text(encoding="utf-8")).get("request", "")
                                       if (folder / "records" / "pending_revision.json").is_file() else "",
                                       "can_revert_interaction": bool(json.loads((folder / "records" / "interaction_stack.json").read_text(encoding="utf-8")))
                                       if (folder / "records" / "interaction_stack.json").is_file() else False,
                                       "can_rollback": can_rollback})
                self._json(200, {"agents": agents})
            elif path.path == "/api/upgrade/pending":
                agent = DATA / "agents" / _valid_id(parse_qs(path.query).get("agent_id", [""])[0])
                if not (agent / "agent.json").is_file():
                    raise ValueError("没有找到这个智能体")
                self._json(200, {"proposal": _latest_pending_upgrade(agent)})
            elif path.path == "/api/artifact":
                query = parse_qs(path.query)
                agent_id = _valid_id(query.get("agent", [""])[0])
                rel = PurePosixPath(query.get("path", [""])[0])
                if not rel.parts or any(x in {"", ".", ".."} for x in rel.parts):
                    raise ValueError("文件路径无效")
                agent = DATA / "agents" / agent_id
                active = agent / "versions" / "active.json"
                if active.exists():
                    version = json.loads(active.read_text(encoding="utf-8"))["version"]
                    if not re.fullmatch(r"[0-9a-f]{64}", version):
                        raise ValueError("版本无效")
                    project = agent / "versions" / version
                else:
                    project = agent / "project"
                file = (project / rel).resolve()
                if not file.is_relative_to(project.resolve()) or not file.is_file() or file.stat().st_size > 10_000_000:
                    raise ValueError("输出文件不可读取")
                content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                if content_type == "application/octet-stream" and file.stat().st_size <= 1_000_000:
                    try:
                        json.loads(file.read_text(encoding="utf-8-sig"))
                        content_type = "application/json"
                    except (UnicodeError, json.JSONDecodeError):
                        pass
                self._bytes(200, file.read_bytes(), content_type)
            else:
                self._json(404, {"error": "未找到页面"})
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)[:300]})

    def do_DELETE(self):
        if not self._valid_host() or not self._authorized():
            self._json(403, {"error": "仅允许在本机工作台中操作"})
            return
        try:
            match = re.fullmatch(r"/api/agents/([0-9a-f]{32})", urlsplit(self.path).path)
            if not match:
                raise ValueError("要删除的智能体无效")
            base = (DATA / "agents").resolve()
            target = base / match.group(1)
            if (not target.is_dir() or target.is_symlink() or
                    target.resolve().parent != base or not (target / "agent.json").is_file()):
                raise ValueError("没有找到这个智能体")
            shutil.rmtree(target)
            self._json(200, {"status": "已删除智能体及其运行记录；原始程序未受影响"})
        except (OSError, ValueError) as exc:
            self._json(400, {"error": str(exc)[:300]})

    def _bytes(self, code: int, body: bytes, kind: str):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        if kind == "image/svg+xml":
            self.send_header("Content-Disposition", "attachment")
            self.send_header("Content-Security-Policy", "sandbox")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self._valid_host() or not self._authorized():
            self._json(403, {"error": "本地访问令牌或来源无效"})
            return
        try:
            path = urlsplit(self.path).path
            if path == "/api/import/start":
                options = self._input_json()
                import_id = uuid4().hex
                source = DATA / "imports" / import_id
                source.mkdir(parents=True)
                self.server.sessions[import_id] = {"source": source, "count": 0, "size": 0,
                                                   "finished": False,
                                                   "project_name": (_project_name(options["project_name"])
                                                                    if "project_name" in options else None)}
                self._json(200, {"import_id": import_id})
            elif re.fullmatch(r"/api/import/[0-9a-f]{32}/archive", path):
                import_id = path.split("/")[3]
                session = self.server.sessions[import_id]
                if session["finished"] or session["count"]:
                    raise ValueError("压缩包导入必须在空会话开始")
                archive = self._body(MAX_UPLOAD_TOTAL)
                with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                    members = [info for info in zf.infolist() if not info.is_dir()]
                    if len(members) > MAX_UPLOAD_COUNT or sum(info.file_size for info in members) > MAX_UPLOAD_TOTAL:
                        raise ValueError("压缩包文件数量或解压大小超限")
                    checked = []
                    skipped = 0
                    for info in members:
                        if info.file_size > MAX_UPLOAD_FILE or ((info.external_attr >> 16) & 0o170000) == 0o120000:
                            raise ValueError("压缩包含大文件或符号链接")
                        try:
                            checked.append((info, _safe_relative(info.filename)))
                        except ValueError:
                            skipped += 1
                    for info, rel in checked:
                        target = session["source"].joinpath(*rel.parts)
                        if target.exists():
                            raise ValueError("压缩包含重复路径")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(zf.read(info))
                    session["count"] = len(checked)
                    session["size"] = sum(info.file_size for info, _ in checked)
                self._json(200, {"count": session["count"], "skipped": skipped})
            elif path.startswith("/api/import/") and path.endswith("/finish"):
                import_id = _valid_id(path.split("/")[3])
                session = self.server.sessions[import_id]
                if session["finished"] or not session["count"]:
                    raise ValueError("导入为空或已经完成")
                root = _project_dir(session["source"])
                analysis = inspect_project(root)
                if analysis["python_files"] == 1:
                    analysis = enrich_single_file(root, analysis)
                elif analysis["python_files"] > 1:
                    analysis = enrich_project(root, analysis)
                report = capability_report(analysis)
                session.update({"finished": True, "root": root, "analysis": analysis})
                prepared = {}
                for candidate in analysis["recommended_tools"]:
                    proposal, samples = propose_tools(root, analysis, [candidate["name"]])
                    prepared[candidate["name"]] = {"tool": proposal["tools"][0], "sample": samples[candidate["name"]]}
                self._json(200, {"import_id": import_id, "analysis": analysis, "report": report,
                                 "candidates": analysis["recommended_tools"], "prepared": prepared})
            elif re.fullmatch(r"/api/import/[0-9a-f]{32}/plan", path):
                import_id = path.split("/")[3]
                session = self.server.sessions[import_id]
                if not session["finished"]:
                    raise ValueError("请先导入并阅读程序说明")
                data = self._input_json()
                selected, intent = data.get("selected"), data.get("intent")
                if not isinstance(selected, list) or not all(isinstance(x, str) for x in selected):
                    raise ValueError("请先选择需要的功能")
                if data.get("stage") == "questions":
                    questions = ask_questions(session["analysis"], selected, intent)
                    session["discussion"] = {"selected": selected, "intent": intent, "questions": questions}
                    session.pop("plan", None)
                    self._json(200, {"questions": questions})
                elif data.get("stage") == "finalize":
                    discussion = session.get("discussion", {})
                    if selected != discussion.get("selected") or intent != discussion.get("intent"):
                        raise ValueError("需求或功能已变化，请重新讨论")
                    answers = data.get("answers")
                    if not isinstance(answers, list):
                        raise ValueError("请回答澄清问题")
                    plan = finalize_plan(session["analysis"], selected, intent,
                                         discussion["questions"], answers)
                    plan_id = uuid4().hex
                    session["plan"] = {"id": plan_id, "selected": selected, "details": plan,
                                       "intent": intent, "answers": answers}
                    self._json(200, {"plan_id": plan_id, "plan": plan})
                else:
                    raise ValueError("讨论阶段无效")
            elif path == "/api/generate":
                data = self._input_json()
                session = self.server.sessions[_valid_id(str(data.get("import_id", "")))]
                if not session["finished"] or data.get("confirmed") is not True:
                    raise PermissionError("请先阅读能力说明并明确确认生成与低成本测试")
                selected = data.get("selected", [])
                if not isinstance(selected, list) or not all(isinstance(x, str) for x in selected):
                    raise ValueError("候选能力列表无效")
                plan = session.get("plan", {})
                if not plan or data.get("plan_id") != plan.get("id") or selected != plan.get("selected"):
                    raise PermissionError("请先回答使用方式问题并确认创建方案")
                proposal, suggested = propose_tools(session["root"], session["analysis"], selected)
                if session["project_name"]:
                    proposal["name"] = session["project_name"] + "-agent"
                proposal["interaction"] = plan["details"]
                agent_id = uuid4().hex
                bundle = DATA / "agents" / agent_id
                approval = {"approved_by_user": True, "proposal_sha256": proposal_hash(proposal),
                            "note": "用户在本地界面确认候选工具及项目副本执行"}
                generate(session["root"], proposal, approval, bundle)
                tests = {}
                provided = data.get("test_params", {})
                if not isinstance(provided, dict):
                    raise ValueError("测试参数必须是对象")
                for name in selected:
                    params = dict(suggested[name])
                    user_values = provided.get(name, {})
                    if not isinstance(user_values, dict):
                        raise ValueError("测试参数格式无效")
                    params.update({k: v for k, v in user_values.items() if v not in (None, "")})
                    tool = next(item for item in proposal["tools"] if item["name"] == name)
                    missing = [rule.get("description") or key for key, rule in tool["parameters"].items()
                               if rule.get("required") and key not in params]
                    if missing:
                        tests[name] = {"status": "skipped", "reason": "尚未提供本次试运行需要的资料：" + "、".join(missing)}
                        continue
                    try:
                        record = invoke(bundle, name, params)
                        tests[name] = {"record": record, "artifacts": _artifact_list(bundle, record)}
                    except (OSError, ValueError, TypeError) as exc:
                        tests[name] = {"error": str(exc)[:500]}
                self._json(200, {"agent": {"id": agent_id, "name": proposal["name"], "tools": selected,
                                           "interaction": plan["details"]},
                                 "tests": tests, "status": "生成完成；请核对实际测试结果，失败项不能称为已验证"})
            elif path == "/api/chat":
                data = self._input_json()
                agent_id = _valid_id(str(data.get("agent_id", "")))
                message = data.get("message", "")
                attached_files = data.get("attached_files", [])
                if not isinstance(message, str) or not message.strip() or len(message) > 1000:
                    raise ValueError("请输入 1–1000 字的需求")
                if (not isinstance(attached_files, list) or len(attached_files) > 3 or
                        any(not isinstance(x, str) or not x.startswith("user_inputs/") for x in attached_files)):
                    raise ValueError("本次添加的文件无效")
                agent = DATA / "agents" / agent_id
                if not (agent / "agent.py").is_file():
                    raise ValueError("Agent 不存在")
                run = subprocess.run([sys.executable, str(agent / "agent.py"), "--request-json"], cwd=agent,
                                     input=json.dumps({"message": message, "attached_files": attached_files}, ensure_ascii=False),
                                     capture_output=True, text=True, timeout=600, shell=False,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if run.returncode:
                    raise RuntimeError((run.stderr or run.stdout)[-500:])
                record = json.loads(run.stdout)
                self._json(200, {"record": record, "artifacts": _artifact_list(agent, record)})
            elif path == "/api/revision/discuss":
                data = self._input_json()
                agent_id = _valid_id(str(data.get("agent_id", "")))
                agent = DATA / "agents" / agent_id
                if not (agent / "agent.json").is_file():
                    raise ValueError("没有找到这个智能体")
                request = data.get("request", "")
                if not isinstance(request, str) or not request.strip() or len(request) > 2000:
                    raise ValueError("请说明希望怎样调整这个智能体")
                pending = agent / "records" / "pending_revision.json"
                pending.parent.mkdir(exist_ok=True)
                pending.write_text(json.dumps({"request": request}, ensure_ascii=False), encoding="utf-8")
                manifest, _ = _agent_manifest(agent)
                try:
                    discussed = discuss_change(manifest, request)
                except (RuntimeError, subprocess.TimeoutExpired):
                    self._json(200, {"status": "waiting_for_service",
                                     "message": "已保存这次调整想法。语言服务暂时不可用，之后可继续。"})
                    return
                if discussed["kind"] == "program":
                    pending.unlink(missing_ok=True)
                    self._json(200, {"status": "program_change", "message": discussed["reason"]})
                    return
                revision_id = uuid4().hex
                self.server.revisions[revision_id] = {"agent_id": agent_id, "request": request,
                                                      "questions": discussed["questions"],
                                                      "base_digest": interaction_digest(manifest["interaction"])}
                self._json(200, {"status": "questions", "revision_id": revision_id,
                                 "questions": discussed["questions"], "message": discussed["reason"]})
            elif path == "/api/revision/finalize":
                data = self._input_json()
                revision_id = _valid_id(str(data.get("revision_id", "")))
                draft = self.server.revisions[revision_id]
                answers = data.get("answers", [])
                if not isinstance(answers, list):
                    raise ValueError("请回答调整方案中的问题")
                agent = DATA / "agents" / draft["agent_id"]
                manifest, _ = _agent_manifest(agent)
                if interaction_digest(manifest["interaction"]) != draft["base_digest"]:
                    raise ValueError("智能体设置已改变，请重新讨论")
                plan = finalize_change(manifest, draft["request"], draft["questions"], answers)
                draft["plan"] = plan
                self._json(200, {"status": "proposed", "revision_id": revision_id, "plan": plan})
            elif path == "/api/revision/activate":
                data = self._input_json()
                if data.get("confirmed") is not True:
                    raise PermissionError("请先核对调整方案再确认")
                revision_id = _valid_id(str(data.get("revision_id", "")))
                draft = self.server.revisions[revision_id]
                if "plan" not in draft:
                    raise ValueError("调整方案尚未准备好")
                agent = DATA / "agents" / draft["agent_id"]
                manifest, _ = _agent_manifest(agent)
                result = apply_change(agent, manifest, draft["plan"]["interaction"], draft["base_digest"])
                (agent / "records" / "pending_revision.json").unlink(missing_ok=True)
                del self.server.revisions[revision_id]
                self._json(200, {"status": "已应用调整", "interaction": result})
            elif path == "/api/revision/rollback":
                data = self._input_json()
                if data.get("confirmed") is not True:
                    raise PermissionError("请先确认恢复上次使用方式")
                agent = DATA / "agents" / _valid_id(str(data.get("agent_id", "")))
                if not (agent / "agent.json").is_file():
                    raise ValueError("没有找到这个智能体")
                self._json(200, {"status": "已恢复上次使用方式", "interaction": rollback_change(agent)})
            elif path == "/api/upgrade/prepare":
                data = self._input_json()
                agent = DATA / "agents" / _valid_id(str(data.get("agent_id", "")))
                request = data.get("request", "")
                if not (agent / "agent.json").is_file() or not isinstance(request, str):
                    raise ValueError("请选择有效的智能体并写明新需求")
                prepared = prepare_upgrade(agent, request)
                if prepared.get("status") == "needs_clarification":
                    _save_upgrade_followup(agent, request, prepared.get("message", "请补充必要信息。"))
                else:
                    _clear_upgrade_followup(agent)
                self._json(200, prepared)
            elif path == "/api/upgrade/activate":
                data = self._input_json()
                agent = DATA / "agents" / _valid_id(str(data.get("agent_id", "")))
                proposal_id = _valid_id(str(data.get("proposal_id", "")))
                version = str(data.get("version", ""))
                if data.get("confirmed") is not True or not re.fullmatch(r"[0-9a-f]{64}", version):
                    raise PermissionError("请先核对修改与测试结果，再明确确认")
                stage = agent / "upgrade_proposals" / proposal_id / "project"
                if not (agent / "agent.json").is_file() or not (stage / "upgrade_report.json").is_file():
                    raise ValueError("没有找到待确认的升级")
                activated = activate_upgrade(agent, stage, {"approved_by_user": True, "version": version})
                report_path = stage / "upgrade_report.json"
                report = json.loads(report_path.read_text(encoding="utf-8"))
                report["activated"] = True
                report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                manifest, _ = _agent_manifest(agent)
                self._json(200, {"status": "已启用升级", "version": activated,
                                 "tools": [x["name"] for x in manifest["tools"]]})
            elif path == "/api/upgrade/rollback":
                data = self._input_json()
                agent = DATA / "agents" / _valid_id(str(data.get("agent_id", "")))
                if data.get("confirmed") is not True or not (agent / "versions" / "active.json").is_file():
                    raise ValueError("没有可回退的升级")
                previous = rollback(agent)
                manifest, _ = _agent_manifest(agent)
                self._json(200, {"status": "已恢复上一版本", "version": previous,
                                 "tools": [x["name"] for x in manifest["tools"]]})
            elif path == "/api/shutdown":
                self._input_json()
                self._json(200, {"status": "closing"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._json(404, {"error": "接口不存在"})
        except (OSError, ValueError, TypeError, KeyError, PermissionError, RuntimeError, zipfile.BadZipFile,
                subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)[:500]})

    def do_PUT(self):
        if not self._valid_host() or not self._authorized():
            self._json(403, {"error": "本地访问令牌或来源无效"})
            return
        try:
            path = urlsplit(self.path).path
            agent_match = re.fullmatch(r"/api/agents/([0-9a-f]{32})/file", path)
            if agent_match:
                agent = DATA / "agents" / agent_match.group(1)
                if not (agent / "agent.json").is_file():
                    raise ValueError("没有找到这个智能体")
                rel = _safe_relative(self.headers.get("X-Relative-Path", ""))
                if len(rel.parts) != 1 or rel.suffix.lower() not in {
                        ".csv", ".tsv", ".txt", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"}:
                    raise ValueError("请添加常见的小型数据或配置文件")
                data = self._body(MAX_UPLOAD_FILE)
                active = agent / "versions" / "active.json"
                if active.is_file():
                    version = json.loads(active.read_text(encoding="utf-8"))["version"]
                    if not re.fullmatch(r"[0-9a-f]{64}", version):
                        raise ValueError("智能体版本无效")
                    project = agent / "versions" / version
                else:
                    project = agent / "project"
                folder = project / "user_inputs"
                existing = [p for p in folder.iterdir() if p.is_file()] if folder.is_dir() else []
                if len(existing) >= 100 or sum(p.stat().st_size for p in existing) + len(data) > MAX_UPLOAD_TOTAL:
                    raise ValueError("此智能体的数据文件数量或总量已达上限")
                folder.mkdir(exist_ok=True)
                name = f"{uuid4().hex[:8]}_{rel.name}"
                (folder / name).write_bytes(data)
                self._json(200, {"name": rel.name, "path": f"user_inputs/{name}"})
                return
            match = re.fullmatch(r"/api/import/([0-9a-f]{32})/(file|support-file)", path)
            if not match:
                raise ValueError("上传路径无效")
            session = self.server.sessions[match.group(1)]
            supplement = match.group(2) == "support-file"
            if session["finished"] != supplement or session["count"] >= MAX_UPLOAD_COUNT:
                raise ValueError("当前不能添加此文件或文件数量超限")
            rel = _safe_relative(self.headers.get("X-Relative-Path", ""))
            if supplement and (len(rel.parts) != 1 or rel.suffix.lower() not in
                               {".csv", ".tsv", ".txt", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"}):
                raise ValueError("这里只能添加常见的小型数据或配置文件")
            data = self._body(MAX_UPLOAD_FILE)
            if session["size"] + len(data) > MAX_UPLOAD_TOTAL:
                raise ValueError("项目总大小超过 20 MB")
            target = (session["root"] if supplement else session["source"]).joinpath(*rel.parts)
            if target.exists():
                raise ValueError("重复文件路径")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            session["count"] += 1
            session["size"] += len(data)
            self._json(200, {"count": session["count"], "name": rel.as_posix()})
        except (OSError, ValueError, KeyError) as exc:
            self._json(400, {"error": str(exc)[:300]})


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address=("127.0.0.1", 0)):
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / "imports").mkdir(exist_ok=True)
        (DATA / "agents").mkdir(exist_ok=True)
        super().__init__(address, Handler)
        self.token = secrets.token_urlsafe(32)
        self.sessions: dict[str, dict] = {}
        self.revisions: dict[str, dict] = {}


def main(open_browser: bool = True):
    session_file = DATA / "server.json"
    if session_file.exists():
        try:
            previous = json.loads(session_file.read_text(encoding="utf-8"))["url"]
            if urlsplit(previous).hostname == "127.0.0.1":
                with urlopen(previous, timeout=1) as response:
                    if response.status == 200 and "个人智能体构建器".encode("utf-8") in response.read(1000):
                        if open_browser:
                            webbrowser.open(previous)
                        print(previous, flush=True)
                        return
        except (OSError, ValueError, KeyError):
            pass
    server = AppServer()
    url = f"http://127.0.0.1:{server.server_port}/"
    session_file.write_text(json.dumps({"url": url, "pid": os.getpid()}), encoding="utf-8")
    print(url, flush=True)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if session_file.exists():
            try:
                if json.loads(session_file.read_text(encoding="utf-8")).get("pid") == os.getpid():
                    session_file.unlink()
            except (OSError, ValueError):
                pass


if __name__ == "__main__":
    main()
