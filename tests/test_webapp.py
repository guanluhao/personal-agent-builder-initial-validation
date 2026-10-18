import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import pytest

from personal_agent_builder import webapp


ROOT = Path(__file__).resolve().parents[1]


def _stub_planning(monkeypatch):
    monkeypatch.setattr(webapp, "ask_questions", lambda *_: ["每次会提供什么资料？"])
    monkeypatch.setattr(webapp, "finalize_plan", lambda *_: {
        "title": "我的资料助手", "welcome": "请添加文件并说出需求。", "usage": "可以反复添加资料。",
        "behavior": "不确定时先问。", "limits": "额外功能需要升级。", "upload_label": "添加资料"})


def _planned(call, imported, selected):
    intent = "希望每次处理新资料，并在不确定时先问我"
    questions = call(f"/api/import/{imported}/plan", "POST",
                     {"stage": "questions", "selected": selected, "intent": intent})["questions"]
    assert len(questions) == 1
    return call(f"/api/import/{imported}/plan", "POST", {
        "stage": "finalize", "selected": selected, "intent": intent,
        "answers": ["每次都会给一份新文件"]})["plan_id"]


def _model_choice(web_data: Path, agent_id: str, tool: str, params: dict | None = None) -> None:
    (web_data / "agents" / agent_id / "router.py").write_text(
        "def route(message, manifest, context):\n"
        f"    return {{'action': 'invoke', 'tool': {tool!r}, 'parameters': {params or {}!r}}}\n",
        encoding="utf-8")


def test_windows_launcher_uses_script_host_unicode():
    script = (ROOT / "start_app.vbs").read_bytes()
    assert script.startswith(b"\xff\xfe")
    assert "个人智能体构建器" in script.decode("utf-16")


def test_reuses_existing_chinese_window(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(webapp, "DATA", tmp_path)
    url = "http://127.0.0.1:12345/"
    (tmp_path / "server.json").write_text(json.dumps({"url": url, "pid": 1}), encoding="utf-8")

    class ExistingWindow:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self, *_):
            return "<title>个人智能体构建器</title>".encode("utf-8")

    monkeypatch.setattr(webapp, "urlopen", lambda *_args, **_kwargs: ExistingWindow())
    monkeypatch.setattr(webapp, "AppServer", lambda: pytest.fail("重复启动了后台服务"))
    webapp.main(open_browser=False)
    assert capsys.readouterr().out.strip() == url


def test_blank_builder_import_approval_generate_chat_and_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "DATA", tmp_path / "web_data")
    _stub_planning(monkeypatch)
    server = webapp.AppServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def call(path, method="GET", value=None, token=True, headers=None):
        body = json.dumps(value).encode() if isinstance(value, dict) else value
        extra = {"X-PAB-Token": server.token} if token else {}
        extra.update(headers or {})
        req = Request(base + path, data=body, headers=extra, method=method)
        with urlopen(req, timeout=120) as response:
            raw = response.read()
            return json.loads(raw) if response.headers.get_content_type() == "application/json" else raw

    try:
        assert call("/api/agents")["agents"] == []
        assert "个人智能体构建器".encode() in call("/")
        with pytest.raises(HTTPError) as wrong_host:
            call("/", headers={"Host": "untrusted.example"})
        assert wrong_host.value.code == 403
        with pytest.raises(HTTPError) as unauth:
            call("/api/import/start", "POST", {}, token=False)
        assert unauth.value.code == 403

        imported = call("/api/import/start", "POST", {"project_name": "Friendly CSV"})["import_id"]
        with pytest.raises(HTTPError) as traversal:
            call(f"/api/import/{imported}/file", "PUT", b"bad", headers={"X-Relative-Path": "../outside.py"})
        assert traversal.value.code == 400
        source = ROOT / "examples" / "csv_project"
        for name in ("csv_task.py", "data.csv", "README.md"):
            call(f"/api/import/{imported}/file", "PUT", (source / name).read_bytes(),
                 headers={"X-Relative-Path": "csv_project/" + name})
        analysis = call(f"/api/import/{imported}/finish", "POST", {})
        assert analysis["analysis"]["root"].endswith("csv_project")
        assert "csv_task" in [x["name"] for x in analysis["candidates"]]
        assert "根据源码推断，尚未运行验证" in analysis["report"]
        assert analysis["report"].count("## ") == 3
        assert not (source / "agent_output.json").exists()

        request = {"import_id": imported, "selected": ["csv_task"],
                   "test_params": {"csv_task": {"input": "data.csv", "column": "amount", "output": "result.json"}},
                   "confirmed": False}
        with pytest.raises(HTTPError) as denied:
            call("/api/generate", "POST", request)
        assert denied.value.code == 400
        request["confirmed"] = True
        with pytest.raises(HTTPError) as no_plan:
            call("/api/generate", "POST", request)
        assert no_plan.value.code == 400
        request["plan_id"] = _planned(call, imported, ["csv_task"])
        created = call("/api/generate", "POST", request)
        assert created["agent"]["name"] == "friendly-csv-agent"
        assert created["tests"]["csv_task"]["record"]["verified_output"] is True
        assert created["tests"]["csv_task"]["artifacts"]
        agent_id = created["agent"]["id"]
        assert len(call("/api/agents")["agents"]) == 1

        _model_choice(tmp_path / "web_data", agent_id, "csv_task", {"input": "data.csv", "column": "amount"})
        reply = call("/api/chat", "POST", {"agent_id": agent_id, "message": "csv_task"})
        assert reply["record"]["exit_code"] == 0 and reply["record"]["verified_output"] is True
        assert call("/api/agents")["agents"][0]["recent_turns"][-1]["user"] == "csv_task"
        artifact = reply["artifacts"][0]
        saved = call(artifact["url"])
        assert saved["sum"] == "10" and saved["count"] == 3

        with pytest.raises(HTTPError) as unauthenticated_delete:
            call(f"/api/agents/{agent_id}", "DELETE", token=False)
        assert unauthenticated_delete.value.code == 403
        assert len(call("/api/agents")["agents"]) == 1
        assert call(f"/api/agents/{agent_id}", "DELETE")["status"].startswith("已删除")
        assert call("/api/agents")["agents"] == []
        assert not (tmp_path / "web_data" / "agents" / agent_id).exists()
        assert (source / "csv_task.py").is_file()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_single_file_can_receive_data_after_explanation_and_use_it(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "DATA", tmp_path / "web_data")
    monkeypatch.setattr(webapp, "enrich_single_file", lambda root, analysis: analysis)
    _stub_planning(monkeypatch)
    server = webapp.AppServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def call(path, method="GET", value=None, headers=None):
        body = json.dumps(value).encode() if isinstance(value, dict) else value
        req = Request(base + path, data=body,
                      headers={"X-PAB-Token": server.token, **(headers or {})}, method=method)
        with urlopen(req, timeout=120) as response:
            return json.loads(response.read())

    try:
        imported = call("/api/import/start", "POST", {"project_name": "One File"})["import_id"]
        source = ROOT / "examples" / "csv_project" / "csv_task.py"
        call(f"/api/import/{imported}/file", "PUT", source.read_bytes(),
             {"X-Relative-Path": "csv_task.py"})
        explained = call(f"/api/import/{imported}/finish", "POST", {})
        assert explained["prepared"]["csv_task"]["tool"]["parameters"]["input"]["role"] == "input_file"
        assert not (source.parent / "data-later.csv").exists()
        with pytest.raises(HTTPError) as bad:
            call(f"/api/import/{imported}/support-file", "PUT", b"print('bad')",
                 {"X-Relative-Path": "extra.py"})
        assert bad.value.code == 400
        added = call(f"/api/import/{imported}/support-file", "PUT", b"amount\n7\n3\n",
                     {"X-Relative-Path": "data-later.csv"})
        assert added["name"] == "data-later.csv"
        plan_id = _planned(call, imported, ["csv_task"])
        created = call("/api/generate", "POST", {
            "import_id": imported, "plan_id": plan_id, "selected": ["csv_task"], "confirmed": True,
            "test_params": {"csv_task": {"input": "data-later.csv", "column": "amount",
                                         "output": "result.json"}}})
        record = created["tests"]["csv_task"]["record"]
        assert record["verified_output"] is True
        assert json.loads(record["stdout"])["sum"] == "10"
        assert not (source.parent / "data-later.csv").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_one_agent_accepts_different_later_files_without_baked_column(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "DATA", tmp_path / "web_data")
    monkeypatch.setattr(webapp, "enrich_single_file", lambda root, analysis: analysis)
    _stub_planning(monkeypatch)
    monkeypatch.setattr(webapp, "discuss_change", lambda *_: {
        "kind": "interaction", "reason": "调整说话方式", "questions": ["希望怎样开场？"]})
    monkeypatch.setattr(webapp, "finalize_change", lambda *_: {
        "change_summary": "改成更简短的开场和说明",
        "interaction": {"title": "我的资料助手新版", "welcome": "先把资料发给我。",
                        "usage": "可反复处理新文件。", "behavior": "不清楚时先问。",
                        "limits": "新功能须修改并测试。", "upload_label": "添加新资料"}})
    server = webapp.AppServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def call(path, method="GET", value=None, filename=None):
        body = json.dumps(value).encode() if isinstance(value, dict) else value
        headers = {"X-PAB-Token": server.token}
        if filename:
            headers["X-Relative-Path"] = filename
        req = Request(base + path, data=body, headers=headers, method=method)
        with urlopen(req, timeout=120) as response:
            return json.loads(response.read())

    try:
        imported = call("/api/import/start", "POST", {"project_name": "Different Files"})["import_id"]
        source = ROOT / "examples" / "csv_project" / "csv_task.py"
        call(f"/api/import/{imported}/file", "PUT", source.read_bytes(), "csv_task.py")
        call(f"/api/import/{imported}/finish", "POST", {})
        created = call("/api/generate", "POST", {
            "import_id": imported, "plan_id": _planned(call, imported, ["csv_task"]),
            "selected": ["csv_task"], "confirmed": True, "test_params": {}})
        assert created["tests"]["csv_task"]["status"] == "skipped"
        agent_id = created["agent"]["id"]
        agent = tmp_path / "web_data" / "agents" / agent_id
        tool = json.loads((agent / "agent.json").read_text(encoding="utf-8"))["tools"][0]
        assert "default" not in tool["parameters"]["input"]
        assert "default" not in tool["parameters"]["column"]
        (agent / "router.py").write_text(
            "def route(message, manifest, context):\n"
            "    if message == '现在叫什么':\n"
            "        return {'action': 'clarify', 'reason': manifest['interaction']['title']}\n"
            "    if message == '以后请先问我':\n"
            "        return {'action': 'personalize_request', 'reason': '可以调整提问方式'}\n"
            "    file = context['attached_files'][0]\n"
            "    preview = context['attached_previews'][0]['first_characters']\n"
            "    column = preview.splitlines()[0].split(',')[1]\n"
            "    if message == '再处理这一份':\n"
            "        return {'action': 'clarify', 'reason': '请确认要统计哪一列'}\n"
            "    return {'action': 'invoke', 'tool': 'csv_task',\n"
            "            'parameters': {'input': file, 'column': column}}\n", encoding="utf-8")
        first = call(f"/api/agents/{agent_id}/file", "PUT", b"item,hhh\nA,11\nB,1\n", "test.CSV")
        first_run = call("/api/chat", "POST", {"agent_id": agent_id, "message": "汇总这份资料",
                                                 "attached_files": [first["path"]]})["record"]
        assert first_run["verified_output"] is True
        assert json.loads(first_run["stdout"])["sum"] == "12"
        second = call(f"/api/agents/{agent_id}/file", "PUT", b"name,score\nA,3\nB,8\n", "other.CSV")
        question = call("/api/chat", "POST", {"agent_id": agent_id, "message": "再处理这一份",
                                               "attached_files": [second["path"]]})["record"]
        assert question["status"] == "needs_clarification"
        second_run = call("/api/chat", "POST", {"agent_id": agent_id,
                                                  "message": "是的，统计分数那一列"})["record"]
        assert second_run["verified_output"] is True
        assert json.loads(second_run["stdout"])["sum"] == "11"
        assert second_run["params"]["input"].endswith("other.CSV")
        assert (agent / "project" / first["path"]).is_file()
        assert not (source.parent / "other.CSV").exists()
        before = call("/api/agents")["agents"][0]
        discussed = call("/api/revision/discuss", "POST", {
            "agent_id": agent_id, "request": "开场请更简短一点"})
        assert discussed["questions"] == ["希望怎样开场？"]
        draft = call("/api/revision/finalize", "POST", {
            "revision_id": discussed["revision_id"], "answers": ["开场直接请我添加资料"]})
        assert draft["plan"]["interaction"]["title"] == "我的资料助手新版"
        assert call("/api/agents")["agents"][0]["interaction"] == before["interaction"]
        with pytest.raises(HTTPError):
            call("/api/revision/activate", "POST", {"revision_id": discussed["revision_id"], "confirmed": False})
        call("/api/revision/activate", "POST", {"revision_id": discussed["revision_id"], "confirmed": True})
        revised = call("/api/agents")["agents"][0]
        assert revised["interaction"]["title"] == "我的资料助手新版"
        assert revised["tools"] == before["tools"]
        assert call("/api/chat", "POST", {"agent_id": agent_id, "message": "现在叫什么"})["record"]["message"] == "我的资料助手新版"
        call("/api/revision/rollback", "POST", {"agent_id": agent_id, "confirmed": True})
        assert call("/api/agents")["agents"][0]["interaction"] == before["interaction"]
        conversational = call("/api/chat", "POST", {"agent_id": agent_id,
                                                      "message": "以后请先问我"})["record"]
        assert conversational["status"] == "personalization_requested"
        assert not (agent / "records" / "upgrade_requests.jsonl").exists()
        def unavailable(*_):
            raise RuntimeError("模型额度暂时不可用")
        monkeypatch.setattr(webapp, "discuss_change", unavailable)
        pending = call("/api/revision/discuss", "POST", {
            "agent_id": agent_id, "request": "以后开场先问我想看什么"})
        assert pending["status"] == "waiting_for_service"
        assert call("/api/agents")["agents"][0]["pending_revision"] == "以后开场先问我想看什么"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_web_builder_generates_metre_based_seismic_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "DATA", tmp_path / "web_data")
    _stub_planning(monkeypatch)
    monkeypatch.setattr(webapp, "enrich_project", lambda _root, analysis: analysis)
    server = webapp.AppServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def call(path, method="GET", value=None, headers=None):
        body = json.dumps(value).encode() if isinstance(value, dict) else value
        req = Request(base + path, data=body,
                      headers={"X-PAB-Token": server.token, **(headers or {})}, method=method)
        with urlopen(req, timeout=120) as response:
            return json.loads(response.read())

    try:
        imported = call("/api/import/start", "POST", {"project_name": "Metre Demo"})["import_id"]
        source = ROOT / "examples" / "seismic_v1_snapshot"
        proposal = json.loads((ROOT / "examples" / "seismic_natural_proposal.json").read_text(encoding="utf-8"))
        for name in proposal["copy_paths"]:
            call(f"/api/import/{imported}/file", "PUT", (source / name).read_bytes(),
                 headers={"X-Relative-Path": "seismic_demo/" + name})
        inspected = call(f"/api/import/{imported}/finish", "POST", {})
        names = {item["name"] for item in inspected["candidates"]}
        assert {"preview", "shift_anomaly_meters", "extend_tunnel"} <= names
        selected = ["shift_anomaly_meters", "extend_tunnel", "preview"]
        created = call("/api/generate", "POST", {
            "import_id": imported, "plan_id": _planned(call, imported, selected), "selected": selected,
            "test_params": {"shift_anomaly_meters": {"left_m": 5, "down_m": 4},
                            "extend_tunnel": {"meters": 10},
                            "preview": {"config": "config.json", "output": "preview_test"}}, "confirmed": True})
        assert all(item["record"]["verified_output"] for item in created["tests"].values())
        agent_id = created["agent"]["id"]
        _model_choice(tmp_path / "web_data", agent_id, "shift_anomaly_meters", {"left_m": 5, "down_m": 4})
        shifted = call("/api/chat", "POST", {"agent_id": agent_id,
                                              "message": "shift_anomaly_meters left_m=5 down_m=4"})
        assert shifted["record"]["verified_output"] is True
        _model_choice(tmp_path / "web_data", agent_id, "extend_tunnel", {"meters": 10})
        extended = call("/api/chat", "POST", {"agent_id": agent_id,
                                               "message": "extend_tunnel meters=10"})
        assert extended["record"]["verified_output"] is True
        assert extended["record"]["params"]["source"].endswith("tunnel_config.json")
        _model_choice(tmp_path / "web_data", agent_id, "preview")
        preview = call("/api/chat", "POST", {"agent_id": agent_id, "message": "preview"})
        assert preview["record"]["verified_output"] is True
        assert preview["record"]["params"]["config"] == extended["record"]["params"]["output"]
        assert len([a for a in preview["artifacts"] if a["name"].endswith(".png")]) == 4
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
