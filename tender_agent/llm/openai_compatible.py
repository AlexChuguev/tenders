from __future__ import annotations

from pathlib import Path

from openai import OpenAI


class OpenAICompatibleProvider:
    def __init__(self, api_key: str, model: str, base_url: str | None = None) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url or None) if api_key else None
        self.model = model

    def analyze_documents(self, prompt: str, files: list[Path]) -> str:
        if self.client is None:
            raise RuntimeError("LLM API key is not configured.")

        uploaded_ids: list[str] = []
        try:
            content = [{"type": "input_text", "text": prompt}]
            for file_path in files:
                with file_path.open("rb") as fh:
                    uploaded = self.client.files.create(file=fh, purpose="user_data")
                uploaded_ids.append(uploaded.id)
                content.append({"type": "input_file", "file_id": uploaded.id})

            response = self.client.responses.create(
                model=self.model,
                input=[{"role": "user", "content": content}],
            )
            return response.output_text.strip()
        finally:
            for file_id in uploaded_ids:
                try:
                    self.client.files.delete(file_id)
                except Exception:
                    pass
