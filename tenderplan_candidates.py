from __future__ import annotations

from pathlib import Path

from tender_agent.config import Settings
from tender_agent.tenderplan_pipeline import TenderplanPipeline


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    pipeline = TenderplanPipeline(settings)
    candidates = pipeline.fetch_candidates()
    pipeline.write_candidates_to_excel(candidates)
    print(f"Selected {len(candidates)} candidates and wrote them to {settings.output_xlsx}")
    for candidate in candidates[:10]:
        deadline = candidate.deadline_at.strftime("%Y-%m-%d %H:%M") if candidate.deadline_at else "-"
        print(f"{deadline} | {candidate.title}")


if __name__ == "__main__":
    main()
