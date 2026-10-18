"""生成二维模型与观测位置示意图。"""

import argparse
import json
from pathlib import Path
from seismic.model import build
from seismic.plotting import plot_inputs


def preview(config: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError("输出目录已经存在，拒绝覆盖")
    case = output / "case"
    build(config, case)
    plot_inputs(case, output / "plots")
    png = sorted((output / "plots").glob("*.png"))
    svg = sorted((output / "plots").glob("*.svg"))
    if not png or len(png) != len(svg):
        raise RuntimeError("预览图输出不完整")
    return {"png": [str(p) for p in png], "svg": [str(p) for p in svg], "case": str(case)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    print(json.dumps(preview(args.config, args.output), ensure_ascii=False))
