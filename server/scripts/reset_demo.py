"""Clear the demo's data: everything under `DATA_DIR` (reports, files, outbox, the database).

    uv run python scripts/reset_demo.py          # asks first
    uv run python scripts/reset_demo.py --yes

Stop the server first, or restart it afterwards: a running server keeps its database open.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import close_store  # noqa: E402
from app.core.settings import REPO_ROOT, get_settings  # noqa: E402


def reset(data_dir: Path) -> list[str]:
    """Remove everything inside `data_dir` (the folder itself stays). Returns what could not
    be removed, for example a database a running server holds open."""
    close_store()
    left: list[str] = []
    if not data_dir.is_dir():
        return left
    for entry in data_dir.iterdir():
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError:
            left.append(entry.name)
    return left


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear the demo data under DATA_DIR.")
    parser.add_argument("--yes", action="store_true", help="do not ask")
    args = parser.parse_args()
    data_dir = get_settings().data_path.resolve()
    if data_dir in (REPO_ROOT.resolve(), Path(data_dir.anchor)) or len(data_dir.parts) < 3:
        print(f"Refusing to clear {data_dir}: it does not look like a data folder.")
        return 2
    if not args.yes:
        answer = input(f"Delete everything in {data_dir}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Nothing deleted.")
            return 1
    left = reset(data_dir)
    if left:
        print("Could not remove (is the server running?): " + ", ".join(sorted(left)))
        return 1
    print(f"Cleared {data_dir}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
