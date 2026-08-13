from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from tender_agent.config import Settings
from tender_agent.local_review import LocalTenderReviewer


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    if len(sys.argv) >= 2:
        settings = replace(settings, local_files_dir=Path(sys.argv[1]).expanduser().resolve())
    if len(sys.argv) >= 3:
        settings = replace(settings, input_xls=Path(sys.argv[2]).expanduser().resolve())
    if len(sys.argv) >= 4:
        settings = replace(settings, output_xlsx=Path(sys.argv[3]).expanduser().resolve())
    reviewer = LocalTenderReviewer(settings)
    reviewer.run()


if __name__ == "__main__":
    main()
