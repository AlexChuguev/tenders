from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from tender_agent.llm import create_llm_provider


CLASSIFICATION_TAGS = [
    "Не наш тип работ",
    "Частично наш стек",
    "Не наш стек",
    "Не подходим по требованиям",
    "Мало опыта / кейсов",
    "Предварительно подходит",
    "В работу",
    "Подано",
    "Не успели обработать",
    "Не изучено",
]


@dataclass
class AnalysisPayload:
    classification_tag: str
    confidence_percent: int
    classification_comment: str
    analysis_markdown: str


class TenderAnalyzer:
    def __init__(
        self,
        provider_name: str,
        api_key: str,
        model: str,
        base_url: str,
        prompt_template_path: Path,
    ) -> None:
        self.provider_name = provider_name
        self.provider = create_llm_provider(
            provider_name=provider_name,
            api_key=api_key,
            model=model,
            base_url=base_url or None,
        )
        self.prompt_template = prompt_template_path.read_text(encoding="utf-8")

    def analyze(self, tender_url: str, files: list[Path]) -> AnalysisPayload:
        if self.provider_name != "stub" and not files:
            return AnalysisPayload(
                classification_tag="Не изучено",
                confidence_percent=0,
                classification_comment="Документы не скачаны, поэтому классификация невозможна.",
                analysis_markdown="Документы не были скачаны, анализ невозможен.",
            )
        try:
            prompt = (
                f"Ссылка на тендер: {tender_url}\n\n"
                f"{self.prompt_template}\n\n"
                "Дополнительное требование к формату ответа:\n"
                "Верни только JSON-объект без markdown-обёртки со следующими полями:\n"
                '{'
                '"classification_tag": "один из заранее разрешённых тегов", '
                '"confidence_percent": "целое число от 0 до 100", '
                '"classification_comment": "одно предложение, почему выбран этот тег", '
                '"analysis_markdown": "полный структурированный анализ по пунктам 1-6"'
                '}\n'
                f"Разрешённые теги: {', '.join(CLASSIFICATION_TAGS)}.\n"
                "Если уверенность низкая, всё равно выбери самый подходящий тег и отрази сомнение в classification_comment."
            )
            raw_text = self.provider.analyze_documents(prompt=prompt, files=files)
            return self._parse_response(raw_text)
        except Exception as exc:
            return AnalysisPayload(
                classification_tag="Не изучено",
                confidence_percent=0,
                classification_comment=f"LLM provider не был вызван: {exc}",
                analysis_markdown=str(exc),
            )

    def _parse_response(self, text: str) -> AnalysisPayload:
        try:
            payload = json.loads(text)
            tag = str(payload.get("classification_tag", "Не изучено")).strip()
            if tag not in CLASSIFICATION_TAGS:
                tag = "Не изучено"
            confidence = int(payload.get("confidence_percent", 0))
            confidence = max(0, min(100, confidence))
            comment = str(payload.get("classification_comment", "")).strip() or "Комментарий не был возвращён моделью."
            analysis = str(payload.get("analysis_markdown", "")).strip() or text.strip()
            return AnalysisPayload(
                classification_tag=tag,
                confidence_percent=confidence,
                classification_comment=comment,
                analysis_markdown=analysis,
            )
        except Exception:
            return AnalysisPayload(
                classification_tag="Не изучено",
                confidence_percent=0,
                classification_comment="Модель вернула ответ не в JSON-формате, поэтому классификация не распознана автоматически.",
                analysis_markdown=text.strip(),
            )
