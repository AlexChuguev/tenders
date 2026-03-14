from __future__ import annotations

from pathlib import Path

from tender_agent.agent import TenderAgent
from tender_agent.config import Settings


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    agent = TenderAgent(settings)
    agent.run()


if __name__ == "__main__":
    main()
