from __future__ import annotations

import json
from pathlib import Path


class StubLLMProvider:
    def __init__(self, model: str) -> None:
        self.model = model

    def analyze_documents(self, prompt: str, files: list[Path]) -> str:
        file_names = ", ".join(path.name for path in files) or "без файлов"
        return json.dumps(
            {
                "decision": "Уточнить",
                "confidence_percent": 1,
                "summary_points": [
                    f"1. Stub provider вернул тестовый ответ для файлов: {file_names}.",
                    "2. Это не реальный анализ тендера.",
                    "3. Проверяется только пайплайн обработки файлов.",
                    "4. Итоговое решение по участию не сформировано.",
                    "5. Для содержательной оценки нужен реальный LLM provider.",
                ],
                "analysis_markdown": (
                    "Это тестовый ответ stub-провайдера. "
                    "Алгоритм обработки, запись в Excel и маршрутизация файлов работают, "
                    "но реальный LLM API в этом запуске не использовался."
                ),
            },
            ensure_ascii=False,
        )
