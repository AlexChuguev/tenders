from __future__ import annotations

import subprocess
from pathlib import Path


def extract_doc_with_textutil(path: Path) -> str:
    try:
        result = subprocess.run(
            ["/usr/bin/textutil", "-convert", "txt", "-stdout", str(path)],
            check=True,
            capture_output=True,
        )
    except Exception:
        return ""
    for encoding in ("utf-8", "utf-16", "cp1251", "latin-1"):
        try:
            return result.stdout.decode(encoding, errors="ignore").strip()
        except Exception:
            continue
    return ""
