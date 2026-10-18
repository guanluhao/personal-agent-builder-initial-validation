"""Reproducible, image-only upgrade example. Never activates the candidate."""

import json
from pathlib import Path
import shutil

from personal_agent_builder.generator import generate
from personal_agent_builder.upgrade import stage_upgrade


ROOT = Path(__file__).resolve().parents[1]


def annotated_source(plotting: str) -> str:
    anchor = "        ax.set(title=f'{key}: y={y0+j*dy:g}',xlabel='x / m',ylabel='z / m');fig.colorbar(im,ax=ax)"
    if plotting.count(anchor) != 1 or "ax.annotate(f'x={x_index}, z={z_index}'" in plotting:
        raise ValueError("源文件已有标注或与预期不符")
    annotation = """        if key == 'rho':
            rho_slice = data[:, j, :]
            background_rho = float(np.median(rho_slice))
            for z_index, x_index in np.argwhere(~np.isclose(rho_slice, background_rho)):
                ax.annotate(f'x={x_index}, z={z_index}',
                            (x0 + x_index * dx, z0 + z_index * dz),
                            xytext=(8, -10), textcoords='offset points', fontsize=8,
                            color='white', bbox={'facecolor': 'black', 'alpha': 0.65, 'pad': 2})
"""
    return plotting.replace(anchor, anchor + "\n" + annotation.rstrip())


if __name__ == "__main__":
    source = ROOT / "examples" / "seismic_v1_snapshot"
    proposal = json.loads((ROOT / "examples" / "seismic_proposal.json").read_text(encoding="utf-8"))
    approval = json.loads((ROOT / "approvals" / "seismic-v1-agent.json").read_text(encoding="utf-8"))
    bundle = ROOT / "artifacts" / "image_annotation_source_bundle"
    stage = ROOT / "artifacts" / "image_annotation_stage"
    if bundle.exists() or stage.exists():
        raise FileExistsError("候选目录已存在；拒绝覆盖已有升级证据")
    generate(source, proposal, approval, bundle)
    shutil.copy2(ROOT / "scripts" / "verify_image_annotation.py", bundle / "project" / "verify_image_annotation.py")
    plotting = (source / "seismic" / "plotting.py").read_text(encoding="utf-8")
    report = stage_upgrade(bundle, {"seismic/plotting.py": annotated_source(plotting)}, stage,
                           ["{python}", "verify_image_annotation.py"], timeout=120)
    if report["test_exit_code"] != 0:
        raise SystemExit(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
