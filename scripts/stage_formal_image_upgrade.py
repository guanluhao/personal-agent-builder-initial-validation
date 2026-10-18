"""Stage the verified image annotation patch against the approved V1 Agent."""

import json
from pathlib import Path

from personal_agent_builder.upgrade import stage_upgrade
from scripts.stage_image_annotation import ROOT, annotated_source


bundle = ROOT / "agents" / "seismic-v1-agent"
stage = ROOT / "artifacts" / "formal_image_annotation_stage"
test_output = ROOT / "artifacts" / "formal_image_annotation_preview"
if stage.exists() or test_output.exists():
    raise FileExistsError("正式候选或其测试图片已存在；拒绝覆盖")
plotting = (bundle / "project" / "seismic" / "plotting.py").read_text(encoding="utf-8")
report = stage_upgrade(bundle, {"seismic/plotting.py": annotated_source(plotting)}, stage,
                       ["{python}", "preview.py", "--config", "config.json", "--output", str(test_output)],
                       timeout=120)
if report["test_exit_code"] != 0:
    raise SystemExit(report)
pngs = sorted((test_output / "plots").glob("*.png"))
assert len(pngs) == 4 and all(p.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for p in pngs)
print(json.dumps({**report, "png_count": len(pngs)}, ensure_ascii=False, indent=2))
