from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    purge_all = "--all" in sys.argv
    args = [arg for arg in sys.argv[1:] if arg != "--all"]
    root = Path(args[0]).expanduser().resolve() if args else Path("state/extraction_cache").resolve()
    if not root.exists():
        print(f"Cache directory not found: {root}")
        return
    removed = 0
    kept = 0
    for path in root.rglob("*.txt"):
        if purge_all:
            path.unlink(missing_ok=True)
            removed += 1
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if sum(1 for char in text if not char.isspace()) < 80:
            path.unlink(missing_ok=True)
            removed += 1
        else:
            kept += 1
    print(f"Done: removed={removed} kept={kept} cache={root}")


if __name__ == "__main__":
    main()
