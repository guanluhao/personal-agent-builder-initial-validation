"""把二维隧道示意中的目标空腔向右延长指定米数，原配置不覆盖。"""

import argparse
import json
import math
from pathlib import Path


def extend_tunnel(source: Path, output: Path, meters: int) -> dict:
    if output.exists():
        raise FileExistsError("拒绝覆盖已有配置")
    if isinstance(meters, bool) or not isinstance(meters, int) or meters <= 0:
        raise ValueError("延长距离必须是正整数米")
    config = json.loads(source.read_text(encoding="utf-8-sig"))
    if config.get("engine") != "acoustic2d" or config["grid"]["shape_zyx"][1] != 1:
        raise ValueError("只支持二维声学模型")
    targets = [r for r in config.get("regions", []) if r.get("role") == "target" and r.get("enabled", True)]
    if len(targets) != 1 or "bounds_xyz_m" not in targets[0]:
        raise ValueError("当前配置无法唯一识别带物理边界的隧道区域")
    grid = config["grid"]
    dx = float(grid["spacing_zyx_m"][2])
    ox = float(grid["origin_zyx_m"][2])
    nx = int(grid["shape_zyx"][2])
    if not math.isfinite(dx) or dx <= 0 or not math.isclose(meters / dx, round(meters / dx), abs_tol=1e-9):
        raise ValueError("延长距离必须与水平网格间距对齐")
    x_bounds = targets[0]["bounds_xyz_m"][0]
    old_end = float(x_bounds[1])
    new_end = old_end + meters
    # V1 treats physical regions as [start, end), so the final point must stay within the grid.
    if new_end > ox + nx * dx + 1e-9:
        raise ValueError("延长后隧道超出模型范围")
    x_bounds[1] = new_end
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"config": str(output), "old_length_m": old_end - float(x_bounds[0]),
            "new_length_m": new_end - float(x_bounds[0]), "extended_m": meters}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default="tunnel_config.json")
    ap.add_argument("--output", type=Path, default="extended_tunnel_config.json")
    ap.add_argument("--meters", required=True, type=int)
    args = ap.parse_args()
    print(json.dumps(extend_tunnel(args.source, args.output, args.meters), ensure_ascii=False))
