"""Create a new V1 configuration with a one-cell density anomaly at x,z."""

import argparse
import json
from pathlib import Path


def move_anomaly(source: Path, output: Path, x: int, z: int) -> dict:
    if output.exists():
        raise FileExistsError("拒绝覆盖已有配置")
    config = json.loads(source.read_text(encoding="utf-8-sig"))
    nz, ny, nx = config["grid"]["shape_zyx"]
    if ny != 1 or not 0 <= x < nx or not 0 <= z < nz:
        raise ValueError("异常点超出二维网格")
    targets = [r for r in config["regions"] if "rho" in r and r.get("rho") != config["background"]["rho"]]
    if len(targets) != 1 or "index_bounds_zyx" not in targets[0]:
        raise ValueError("当前配置无法唯一识别密度异常")
    targets[0]["index_bounds_zyx"] = [[z, z + 1], [0, 1], [x, x + 1]]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"config": str(output), "x_index": x, "z_index": z}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--x", required=True, type=int)
    ap.add_argument("--z", required=True, type=int)
    args = ap.parse_args()
    print(json.dumps(move_anomaly(args.source, args.output, args.x, args.z), ensure_ascii=False))
