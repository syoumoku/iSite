from __future__ import annotations

from pathlib import Path

from isite2.output.excel import write_excel_skeleton

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    out = ROOT / "outputs" / "isite2_excel_skeleton.xlsx"
    print(write_excel_skeleton(out))


if __name__ == "__main__":
    main()
