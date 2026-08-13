from __future__ import annotations

import sys
from pathlib import Path

from tender_agent.config import Settings
from tender_agent.search_profile import SearchProfile, evaluate_text


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: .venv/bin/python search_profile_check.py 'text to evaluate'")

    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    profile = SearchProfile.load(settings.search_profile_path)
    text = " ".join(sys.argv[1:])
    result = evaluate_text(profile, text)

    print(f"profile: {profile.name}")
    print(f"is_candidate: {result.is_candidate}")
    print(f"include_hits ({len(result.include_hits)}): {result.include_hits[:15]}")
    print(f"exclude_hits ({len(result.exclude_hits)}): {result.exclude_hits[:15]}")
    print(f"secondary_hits ({len(result.secondary_hits)}): {result.secondary_hits[:15]}")


if __name__ == "__main__":
    main()
