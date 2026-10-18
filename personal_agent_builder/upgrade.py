"""Stage and verify upgrades in copies; activate only after exact approval."""

from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys


def _hash_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name not in {"upgrade_report.json", "upgrade.diff"}:
            digest.update(p.relative_to(root).as_posix().encode())
            digest.update(p.read_bytes())
    return digest.hexdigest()


def _version_dir(bundle: Path, version: str) -> Path:
    if not isinstance(version, str) or not re.fullmatch(r"[0-9a-f]{64}", version):
        raise ValueError("版本指针无效")
    return bundle / "versions" / version


def stage_upgrade(bundle: str | Path, replacements: dict[str, str], stage: str | Path,
                  test_args: list[str], timeout: int = 60) -> dict:
    bundle, stage = Path(bundle).resolve(), Path(stage).resolve()
    active = bundle / "versions" / "active.json"
    current = _version_dir(bundle, json.loads(active.read_text(encoding="utf-8"))["version"]) if active.exists() else bundle / "project"
    if not current.is_dir() or stage.exists():
        raise ValueError("原版不存在或暂存目录已存在")
    if stage.is_relative_to(current):
        raise ValueError("暂存目录不能放入原版")
    shutil.copytree(current, stage, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "output", "*.png", "*.svg", "*.pdf"))
    diff = []
    for rel, content in replacements.items():
        old = (current / rel).resolve()
        new = (stage / rel).resolve()
        if (not old.is_relative_to(current) or not new.is_relative_to(stage) or old.is_symlink()
                or (not old.is_file() and Path(rel).suffix != ".py" and rel != "agent.json")):
            raise ValueError("修改目标不在原版中")
        before = old.read_text(encoding="utf-8") if old.is_file() else ""
        new.parent.mkdir(parents=True, exist_ok=True)
        new.write_text(content, encoding="utf-8")
        diff.extend(difflib.unified_diff(before.splitlines(keepends=True), content.splitlines(keepends=True),
                                         fromfile=f"a/{rel}" if old.is_file() else "/dev/null", tofile=f"b/{rel}"))
    if len(test_args) < 2 or test_args[0] != "{python}" or not (stage / test_args[1]).is_file():
        raise ValueError("验证命令必须调用暂存副本中的 Python 文件")
    argv = [sys.executable, str(stage / test_args[1]), *test_args[2:]]
    process = subprocess.run(argv, cwd=stage, capture_output=True, text=True, timeout=timeout, shell=False)
    report = {"version": _hash_tree(stage), "test_exit_code": process.returncode,
              "test_stdout": process.stdout[-4000:], "test_stderr": process.stderr[-4000:],
              "stage": str(stage), "activated": False}
    (stage / "upgrade_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (stage / "upgrade.diff").write_text("".join(diff), encoding="utf-8")
    return report


def activate_upgrade(bundle: str | Path, stage: str | Path, approval: dict) -> str:
    bundle, stage = Path(bundle).resolve(), Path(stage).resolve()
    report = json.loads((stage / "upgrade_report.json").read_text(encoding="utf-8"))
    version = report["version"]
    if (report["test_exit_code"] != 0 or (stage / "agent.json").is_file() and not report.get("verified_output")
            or approval.get("approved_by_user") is not True or approval.get("version") != version):
        raise PermissionError("测试未通过或用户尚未确认此升级版本")
    if _hash_tree(stage) != version:
        raise ValueError("待启用的文件与通过测试的版本不一致")
    versions = bundle / "versions"
    versions.mkdir(exist_ok=True)
    target = versions / version
    if target.exists():
        raise FileExistsError("此版本已经登记")
    shutil.copytree(stage, target)
    active = versions / "active.json"
    previous = json.loads(active.read_text(encoding="utf-8"))["version"] if active.exists() else "base"
    if previous != "base":
        _version_dir(bundle, previous)
    active.write_text(json.dumps({"version": version, "previous": previous}, indent=2) + "\n", encoding="utf-8")
    _sync_mcp(bundle)
    return version


def rollback(bundle: str | Path) -> str:
    bundle = Path(bundle).resolve()
    active = bundle / "versions" / "active.json"
    state = json.loads(active.read_text(encoding="utf-8"))
    previous = state["previous"]
    if previous == "base":
        active.unlink()
    else:
        _version_dir(bundle, previous)
        active.write_text(json.dumps({"version": previous, "previous": "base"}, indent=2) + "\n", encoding="utf-8")
    _sync_mcp(bundle)
    return previous


def _sync_mcp(bundle: Path) -> None:
    from .generator import _mcp_source

    active = bundle / "versions" / "active.json"
    project = _version_dir(bundle, json.loads(active.read_text(encoding="utf-8"))["version"]) if active.exists() else bundle / "project"
    manifest_file = project / "agent.json"
    manifest = json.loads((manifest_file if manifest_file.is_file() else bundle / "agent.json").read_text(encoding="utf-8"))
    (bundle / "mcp_server.py").write_text(_mcp_source(manifest["tools"]), encoding="utf-8")
