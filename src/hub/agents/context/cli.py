import argparse
import sys

from .config import DEFAULT_MODE, MODES
from .graph import build_context

MENU = """Chọn mức ngữ cảnh:
  1. map       Bản đồ nhanh (cây thư mục + skeleton)
  2. balanced  Cân bằng (bản đồ + full code vài file chính)   [mặc định]
  3. full      Đầy đủ (toàn bộ code, đã bỏ comment/output notebook)"""


def _ask_mode() -> str:
    print(MENU)
    choice = input("Chọn [2]: ").strip() or "2"
    return {"1": "map", "2": "balanced", "3": "full"}.get(choice, DEFAULT_MODE)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="repo-context", description=__doc__)
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--mode", choices=list(MODES), help="bỏ qua menu nếu đã chọn")
    ap.add_argument("--budget", type=int, help="token budget cho full code (ghi đè mode)")
    ap.add_argument("--pin", action="append", default=[], help="luôn gửi full code file này")
    ap.add_argument("--exclude", action="append", default=[], help="glob loại file, vd 'tests/*'")
    ap.add_argument("-o", "--output", default="PROJECT_CONTEXT.md", help="'-' để in ra stdout")
    a = ap.parse_args(argv)

    mode = a.mode or (_ask_mode() if sys.stdin.isatty() else DEFAULT_MODE)
    text = build_context(a.root, mode, budget=a.budget if a.budget is not None else ..., pin=a.pin, exclude=a.exclude)
    if a.output == "-":
        print(text)
    else:
        with open(a.output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Wrote {a.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
