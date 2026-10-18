"""汇总表格中指定数值列的记录数、总和与平均值。"""

import argparse
import csv
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path


def summarize(input_file: Path, column: str, output_file: Path) -> dict:
    """Summarize one numeric column from a CSV file."""
    with input_file.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if column not in (reader.fieldnames or []):
            raise ValueError(f"缺少列：{column}")
        values = []
        for number, row in enumerate(reader, start=2):
            try:
                values.append(Decimal(row[column]))
            except (InvalidOperation, TypeError):
                raise ValueError(f"第 {number} 行不是数字") from None
    total = sum(values, Decimal(0))
    result = {"column": column, "count": len(values), "sum": str(total),
              "mean": str(total / len(values)) if values else None}
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--column", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.input, args.column, args.output), ensure_ascii=False))
