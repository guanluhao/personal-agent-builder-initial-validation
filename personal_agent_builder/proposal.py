"""Conservative tool proposals for simple, statically recognized argparse CLIs."""

from __future__ import annotations

from pathlib import Path
import re

from .analysis import MAX_FILE, _safe_files

COPY_SUFFIXES = {".py", ".json", ".csv", ".tsv", ".txt", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".md"}
OUTPUT_NAMES = {"output", "out", "result", "save"}


def _suggest_default(arg: dict) -> str | int | None:
    if arg["default"] is not None:
        return arg["default"]
    if arg["type"] == "path" and arg["name"] in OUTPUT_NAMES:
        return "agent_output"
    return None


def propose_tools(root: str | Path, analysis: dict, selected_names: list[str]) -> tuple[dict, dict]:
    """Return an unverified proposal and suggested test parameters; never execute code."""
    root = Path(root).resolve()
    candidates = {x["name"]: x for x in analysis.get("recommended_tools", [])}
    if not selected_names or len(selected_names) != len(set(selected_names)) or set(selected_names) - set(candidates):
        raise ValueError("请选择报告中确实存在的候选能力")
    files = [p for p in _safe_files(root) if p.suffix.lower() in COPY_SUFFIXES and p.stat().st_size <= MAX_FILE]
    copy_paths = [p.relative_to(root).as_posix() for p in files]
    tools, sample = [], {}
    for name in selected_names:
        candidate = candidates[name]
        params = {}
        argv = ["{python}", candidate["file"]]
        suggested = {}
        artifact_params = []
        for arg in candidate["arguments"]:
            rule = {"type": arg["type"], "description": arg.get("help", "")}
            if arg.get("hint"):
                rule["hint"] = arg["hint"]
            rule["role"] = arg.get("role") or (
                "output_path" if arg["type"] == "path" and arg["name"] in OUTPUT_NAMES else
                "input_file" if arg["type"] == "path" else "value")
            default = _suggest_default(arg)
            if default is not None:
                rule["default"] = default
                suggested[arg["name"]] = default
            else:
                rule["required"] = True
            params[arg["name"]] = rule
            argv.extend([arg["flag"], "{" + arg["name"] + "}"])
            if arg["name"] in OUTPUT_NAMES and arg["type"] == "path" and isinstance(default, str) and Path(default).suffix:
                artifact_params.append(arg["name"])
        tool = {"name": name, "aliases": [candidate["file"]],
                "description": candidate["description"], "parameters": params,
                "command": argv, "output_schema": {"type": "object"},
                "artifact_params": artifact_params, "timeout_seconds": 30}
        source_default = params.get("source", {}).get("default")
        if isinstance(source_default, str):
            tool["context_group"] = f"source:{source_default}"
        tools.append(tool)
        sample[name] = suggested
    proposal = {"name": re.sub(r"[^a-z0-9_-]+", "-", root.name.lower()).strip("-") + "-agent",
                "copy_paths": copy_paths, "tools": tools}
    return proposal, sample
