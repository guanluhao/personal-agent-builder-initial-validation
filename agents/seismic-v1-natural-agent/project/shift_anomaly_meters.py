"""Move the V1 density anomaly by physical distances, keeping the source config."""

import argparse
import json
import math
from pathlib import Path


def _grid_steps(distance_m: int, spacing_m: float, label: str) -> int:
    if isinstance(distance_m, bool) or not isinstance(distance_m, int) or distance_m < 0:
        raise ValueError(f"{label}必须是非负整数米")
    if not math.isfinite(spacing_m) or spacing_m <= 0:
        raise ValueError("网格间距必须是有限正数")
    steps = distance_m / spacing_m
    if not math.isclose(steps, round(steps), abs_tol=1e-9):
        raise ValueError(f"{label}未落在当前网格上，网格间距为{spacing_m:g}米")
    return round(steps)


def shift_anomaly(source: Path, output: Path, left_m: int, down_m: int) -> dict:
    if output.exists():
        raise FileExistsError("拒绝覆盖已有配置")
    config = json.loads(source.read_text(encoding="utf-8-sig"))
    grid = config["grid"]
    nz, ny, nx = grid["shape_zyx"]
    oz, _, ox = [float(v) for v in grid["origin_zyx_m"]]
    dz, _, dx = [float(v) for v in grid["spacing_zyx_m"]]
    if ny != 1:
        raise ValueError("只支持二维模型")
    targets = [r for r in config["regions"] if "rho" in r and r.get("rho") != config["background"]["rho"]]
    if len(targets) != 1 or "index_bounds_zyx" not in targets[0]:
        raise ValueError("当前配置无法唯一识别单网格密度异常")
    (z0, z1), (y0, y1), (x0, x1) = targets[0]["index_bounds_zyx"]
    if (z1 - z0, y0, y1, x1 - x0) != (1, 0, 1, 1):
        raise ValueError("密度异常必须恰好占一个二维网格单元")
    new_x = x0 - _grid_steps(left_m, dx, "左移距离")
    new_z = z0 + _grid_steps(down_m, dz, "下移距离")
    if not 0 <= new_x < nx or not 0 <= new_z < nz:
        raise ValueError("移动后异常点超出模型范围")
    targets[0]["index_bounds_zyx"] = [[new_z, new_z + 1], [0, 1], [new_x, new_x + 1]]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"config": str(output), "old_xyz_m": [ox + x0 * dx, 0, oz + z0 * dz],
            "new_xyz_m": [ox + new_x * dx, 0, oz + new_z * dz],
            "left_m": left_m, "down_m": down_m,
            "x_index": new_x, "z_index": new_z}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default="config.json")
    ap.add_argument("--output", type=Path, default="shifted_config.json")
    ap.add_argument("--left-m", required=True, type=int)
    ap.add_argument("--down-m", required=True, type=int)
    args = ap.parse_args()
    print(json.dumps(shift_anomaly(args.source, args.output, args.left_m, args.down_m), ensure_ascii=False))
