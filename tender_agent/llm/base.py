from __future__ import annotations

from pathlib import Path
from typing import Protocol


class LLMProvider(Protocol):
    def analyze_documents(self, prompt: str, files: list[Path]) -> str:
        """Return raw model output for the given prompt and files."""
