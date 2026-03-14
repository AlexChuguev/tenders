from __future__ import annotations

from pathlib import Path

from tender_agent.config import Settings
from tender_agent.local_review import LocalTenderReviewer


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    reviewer = LocalTenderReviewer(settings)
    reviewer.run()


if __name__ == "__main__":
    main()
