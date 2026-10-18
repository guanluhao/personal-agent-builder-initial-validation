"""Small, evidence-bound explanations for follow-up questions."""

from __future__ import annotations

import json
from pathlib import Path

READABLE_SUFFIXES = {".py", ".md", ".json", ".csv", ".tsv", ".txt", ".toml", ".yaml", ".yml"}
HIDDEN_PARTS = {".git", ".venv", "venv", "__pycache__", "output", "results", "artifacts", "records", "versions"}
PRIVATE_NAMES = {".env", "credentials.json", "secrets.json", "id_rsa", "id_ed25519"}
MAX_RECENT_TURNS = 10


def recent_turns(context: dict) -> list[dict]:
    """Keep a small, plain-text dialogue for follow-up requests across processes."""
    turns = context.get("recent_turns", [])
    if not isinstance(turns, list):
        return []
    clean = []
    for turn in turns[-MAX_RECENT_TURNS:]:
        if not isinstance(turn, dict):
            continue
        user, assistant = turn.get("user"), turn.get("assistant")
        if not isinstance(user, str) or not isinstance(assistant, str):
            continue
        files = turn.get("attached_files", [])
        clean.append({"user": user[:1000], "assistant": assistant[:800],
                      "status": str(turn.get("status", ""))[:40],
                      "attached_files": [x[:200] for x in files[:3] if isinstance(x, str)]
                      if isinstance(files, list) else []})
    return clean


def remember_turn(context: dict, message: str, response: dict, attached_files: list[str]) -> None:
    """Save dialogue summaries, never full program output or file contents."""
    assistant = response.get("message")
    if not isinstance(assistant, str) or not assistant.strip():
        if response.get("verified_output") is True:
            assistant = "程序已运行，结果通过检查。"
        elif "exit_code" in response:
            assistant = "程序运行未通过检查。"
        else:
            assistant = "这次请求未完成。"
    turns = recent_turns(context)
    turns.append({"user": message.strip()[:1000], "assistant": assistant.strip()[:800],
                  "status": str(response.get("status", "verified" if response.get("verified_output") else "failed"))[:40],
                  "attached_files": [x[:200] for x in attached_files[:3]]})
    context["recent_turns"] = turns[-MAX_RECENT_TURNS:]


def readable_files(project: Path) -> list[str]:
    """List small, ordinary project files for model-selected read-only inspection."""
    files = []
    for path in sorted(project.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(project)
        if (any(part.startswith(".") or part in HIDDEN_PARTS for part in rel.parts)
                or path.name.lower() in PRIVATE_NAMES or path.suffix.lower() not in READABLE_SUFFIXES
                or path.stat().st_size > 50_000):
            continue
        files.append(rel.as_posix())
        if len(files) >= 60:
            break
    return files


def read_project_file(project: Path, relative: str, allowed: list[str]) -> str:
    if relative not in allowed:
        raise ValueError("只能阅读此项目中列出的普通文件")
    path = (project / relative).resolve()
    if not path.is_relative_to(project.resolve()) or not path.is_file() or path.is_symlink():
        raise ValueError("文件路径无效")
    return path.read_text(encoding="utf-8-sig")[:30_000]


def preview_attached_file(project: Path, relative: str) -> dict:
    """Show a short, bounded data preview to the router without executing the file."""
    path = (project / relative).resolve()
    if (not relative.startswith("user_inputs/") or not path.is_relative_to((project / "user_inputs").resolve())
            or not path.is_file() or path.is_symlink() or path.suffix.lower() not in
            {".csv", ".tsv", ".txt", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"}
            or path.stat().st_size > 1_000_000):
        raise ValueError("本次添加的文件不可读取，请重新选择普通数据文件")
    with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
        preview = stream.read(3000)
    return {"name": relative, "size_bytes": path.stat().st_size, "first_characters": preview,
            "note": "仅为文件开头预览；格式和字段应按用户需求及实际内容核对。"}


def _payload(record: dict) -> dict | None:
    if record.get("exit_code") != 0 or record.get("verified_output") is not True:
        return None
    try:
        value = json.loads(record.get("stdout", "").strip().splitlines()[-1])
    except (IndexError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def last_verified_result(root: Path, context: dict) -> dict | None:
    saved = context.get("last_verified_result")
    if isinstance(saved, dict) and isinstance(saved.get("payload"), dict):
        return saved
    calls = root / "records" / "calls.jsonl"
    if not calls.is_file():
        return None
    for line in reversed(calls.read_text(encoding="utf-8").splitlines()[-100:]):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        payload = _payload(record)
        if payload is not None:
            return {"payload": payload, "tool": record.get("tool", "")}
    return None


def remember_result(context: dict, result: dict) -> None:
    payload = _payload(result)
    if payload is not None:
        context["last_verified_result"] = {"payload": payload, "tool": result.get("tool", "")}
