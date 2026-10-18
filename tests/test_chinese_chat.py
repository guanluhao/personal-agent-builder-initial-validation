import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import pytest

from personal_agent_builder.generator import generate, proposal_hash
from personal_agent_builder import router
from personal_agent_builder.runtime import invoke
from personal_agent_builder.conversation import readable_files, read_project_file, recent_turns, remember_turn


ROOT = Path(__file__).resolve().parents[1]


def _bundle(tmp_path):
    proposal = json.loads((ROOT / "examples" / "csv_proposal.json").read_text(encoding="utf-8"))
    approval = {"approved_by_user": True, "proposal_sha256": proposal_hash(proposal)}
    return generate(ROOT / "examples" / "csv_project", proposal, approval, tmp_path / "agent")


def _ask(bundle, message):
    run = subprocess.run([sys.executable, str(bundle / "agent.py"), message],
                         cwd=bundle, text=True, capture_output=True, timeout=35)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_finds_desktop_install_when_path_is_missing(tmp_path, monkeypatch):
    executable = tmp_path / "OpenAI" / "Codex" / "bin" / "version" / "codex.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"test fixture")
    monkeypatch.setattr(router.shutil, "which", lambda _: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert router.find_language_service() == str(executable)


def test_language_service_has_no_console_window(monkeypatch):
    manifest = json.loads((ROOT / "examples" / "csv_proposal.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(router, "find_language_service", lambda: "codex.exe")

    def fake_run(argv, **kwargs):
        assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
        assert "已批准的程序功能" in kwargs["input"]
        Path(argv[argv.index("--output-last-message") + 1]).write_text(
            json.dumps({"action": "answer", "answer_kind": "capabilities", "tool": "",
                        "parameters": {"input": None, "column": None, "output": None},
                        "reason": "可以汇总表格。"}), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(router.subprocess, "run", fake_run)
    answer = router.route("能做什么", manifest)
    assert answer["action"] == "answer"


def test_language_model_explains_a_real_previous_result_without_new_execution(tmp_path):
    bundle = _bundle(tmp_path)
    assert invoke(bundle, "summarize_csv", {})["verified_output"] is True
    (bundle / "router.py").write_text(
        "def route(message, manifest, context):\n"
        "    payload = context['last_verified_result']['payload']\n"
        "    return {'action': 'answer', 'answer_kind': 'result', 'reason': f\"真实结果：{payload['count']} 行，合计 {payload['sum']}。\"}\n",
        encoding="utf-8")
    call_log = bundle / "records" / "calls.jsonl"
    before = len(call_log.read_text(encoding="utf-8").splitlines())
    answer = _ask(bundle, "请帮我解释这个结果")
    assert answer["status"] == "answered"
    assert "3 行" in answer["message"] and "合计 10" in answer["message"]
    assert len(call_log.read_text(encoding="utf-8").splitlines()) == before
    assert not (bundle / "records" / "upgrade_requests.jsonl").exists()


def test_model_routes_plain_chinese_calculation_to_approved_program(tmp_path):
    bundle = _bundle(tmp_path)
    request = "劳驾把文件里的数字平均一下"
    (bundle / "router.py").write_text(
        "def route(message, manifest, context):\n"
        f"    assert message == {request!r}\n"
        "    return {'action': 'invoke', 'tool': 'summarize_csv', 'parameters': {}}\n",
        encoding="utf-8")
    answer = _ask(bundle, request)
    assert answer["verified_output"] is True
    assert json.loads(answer["stdout"])["sum"] == "10"


def test_model_cannot_explain_unverified_result(tmp_path):
    bundle = _bundle(tmp_path)
    (bundle / "router.py").write_text(
        "def route(message, manifest, context):\n"
        "    return {'action': 'answer', 'answer_kind': 'result', 'reason': '编造的解释'}\n", encoding="utf-8")
    answer = _ask(bundle, "请解释这个结果")
    assert answer["status"] == "needs_clarification"
    assert "编造" not in answer["message"]


def test_can_describe_approved_capability_before_first_run(tmp_path):
    bundle = _bundle(tmp_path)
    (bundle / "router.py").write_text(
        "def route(message, manifest, context):\n"
        "    return {'action': 'answer', 'answer_kind': 'capabilities', 'reason': '我可以汇总表格中的数字。'}\n",
        encoding="utf-8")
    answer = _ask(bundle, "你能帮我做哪些事情")
    assert answer == {"status": "answered", "message": "我可以汇总表格中的数字。",
                      "source_verified": False, "file_reviewed": False}


def test_language_service_failure_is_not_an_upgrade_request(tmp_path):
    bundle = _bundle(tmp_path)
    (bundle / "router.py").write_text("def route(*args):\n    raise RuntimeError('语言服务暂时不可用')\n", encoding="utf-8")
    answer = _ask(bundle, "请把表格按姓名分类")
    assert answer["status"] == "service_unavailable"
    assert "语言服务暂时不可用" in answer["message"]
    assert not (bundle / "records" / "upgrade_requests.jsonl").exists()


def test_every_message_is_interpreted_and_model_can_read_project_file(tmp_path):
    bundle = _bundle(tmp_path)
    (bundle / "router.py").write_text(
        "def route(message, manifest, context):\n"
        "    assert message == 'summarize_csv'\n"
        "    if not context['file_evidence']:\n"
        "        assert 'csv_task.py' in context['available_files']\n"
        "        return {'action': 'read_file', 'file': 'csv_task.py'}\n"
        "    assert 'def summarize' in context['file_evidence']['csv_task.py']\n"
        "    return {'action': 'answer', 'answer_kind': 'capabilities', 'reason': '它可以汇总数值列。'}\n",
        encoding="utf-8")
    answer = _ask(bundle, "summarize_csv")
    assert answer["status"] == "answered" and "汇总数值列" in answer["message"]
    assert answer["file_reviewed"] is True and answer["source_verified"] is False
    assert not (bundle / "records" / "calls.jsonl").exists()


def test_model_selected_file_read_stays_inside_small_ordinary_files(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def work(): return 1\n", encoding="utf-8")
    (project / ".env").write_text("secret", encoding="utf-8")
    (project / "large.txt").write_text("x" * 50_001, encoding="utf-8")
    allowed = readable_files(project)
    assert allowed == ["main.py"]
    assert "def work" in read_project_file(project, "main.py", allowed)
    with pytest.raises(ValueError):
        read_project_file(project, "../outside.py", allowed)
    with pytest.raises(ValueError):
        read_project_file(project, ".env", allowed)


def test_follow_up_after_clarification_remembers_question_and_attached_file(tmp_path):
    bundle = _bundle(tmp_path)
    data = bundle / "project" / "user_inputs" / "new.csv"
    data.parent.mkdir()
    data.write_text("measure\n7\n12\n", encoding="utf-8")
    (bundle / "router.py").write_text(
        "def route(message, manifest, context):\n"
        "    if message == '帮我汇总刚上传的表格':\n"
        "        return {'action': 'clarify', 'reason': '要统计哪一列？'}\n"
        "    assert message == '就用刚才那份的 measure 列'\n"
        "    assert context['recent_turns'][-1]['user'] == '帮我汇总刚上传的表格'\n"
        "    assert context['recent_turns'][-1]['assistant'] == '要统计哪一列？'\n"
        "    assert context['attached_files'] == ['user_inputs/new.csv']\n"
        "    return {'action': 'invoke', 'tool': 'summarize_csv', "
        "'parameters': {'input': 'user_inputs/new.csv', 'column': 'measure'}}\n",
        encoding="utf-8")
    first = subprocess.run([sys.executable, str(bundle / "agent.py"), "--request-json"],
                           input=json.dumps({"message": "帮我汇总刚上传的表格",
                                             "attached_files": ["user_inputs/new.csv"]}),
                           cwd=bundle, text=True, capture_output=True, timeout=35)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["status"] == "needs_clarification"
    second = _ask(bundle, "就用刚才那份的 measure 列")
    assert second["verified_output"] is True
    assert json.loads(second["stdout"])["sum"] == "19"
    context = json.loads((bundle / "records" / "context.json").read_text(encoding="utf-8"))
    assert [x["user"] for x in context["recent_turns"]] == ["帮我汇总刚上传的表格", "就用刚才那份的 measure 列"]
    assert "pending_attached_files" not in context


def test_follow_up_upgrade_uses_resolved_request_and_bounds_history(tmp_path):
    bundle = _bundle(tmp_path)
    (bundle / "router.py").write_text(
        "def route(message, manifest, context):\n"
        "    if message == '给刚才说的结果增加一种输出':\n"
        "        return {'action': 'clarify', 'reason': '具体希望增加什么输出？'}\n"
        "    assert context['recent_turns'][-1]['assistant'] == '具体希望增加什么输出？'\n"
        "    return {'action': 'upgrade_request', 'reason': '当前功能需要扩展。', "
        "'resolved_request': '为汇总结果新增一份文本报告，保留原有数值结果。'}\n",
        encoding="utf-8")
    assert _ask(bundle, "给刚才说的结果增加一种输出")["status"] == "needs_clarification"
    answer = _ask(bundle, "文本报告，保留原来的数值")
    assert answer["status"] == "upgrade_requested"
    assert answer["upgrade_request"] == "为汇总结果新增一份文本报告，保留原有数值结果。"
    request = json.loads((bundle / "records" / "upgrade_requests.jsonl").read_text(encoding="utf-8"))
    assert request["request"] == answer["upgrade_request"]
    context = {}
    for number in range(15):
        remember_turn(context, f"第{number}轮", {"status": "answered", "message": "已说明"}, [])
    assert len(recent_turns(context)) == 10
    assert recent_turns(context)[0]["user"] == "第5轮"


def test_missing_old_attachment_does_not_block_unrelated_question(tmp_path):
    bundle = _bundle(tmp_path)
    context_file = bundle / "records" / "context.json"
    context_file.write_text(json.dumps({"pending_attached_files": ["user_inputs/old.csv"]}), encoding="utf-8")
    (bundle / "router.py").write_text(
        "def route(message, manifest, context):\n"
        "    assert context['attached_files'] == []\n"
        "    assert context['missing_attached_files'] == ['user_inputs/old.csv']\n"
        "    return {'action': 'answer', 'answer_kind': 'capabilities', 'reason': '我可以汇总数值列。'}\n",
        encoding="utf-8")
    reply = _ask(bundle, "你能做什么？")
    assert reply["status"] == "answered"
    assert "汇总数值列" in reply["message"]
    context = json.loads(context_file.read_text(encoding="utf-8"))
    assert "pending_attached_files" not in context
