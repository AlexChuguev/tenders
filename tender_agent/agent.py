from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

from tender_agent.analysis import TenderAnalyzer
from tender_agent.config import Settings
from tender_agent.excel_writer import ExcelWriter
from tender_agent.excel_loader import load_tenders
from tender_agent.models import TenderAnalysisResult
from tender_agent.platforms import DocumentAccessBlockedError, SeldonFirstAdapter


class TenderAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.analyzer = TenderAnalyzer(
            provider_name=settings.llm_provider,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            prompt_template_path=settings.prompt_template_path,
        )
        self.sheet_writer = ExcelWriter(output_path=settings.output_xlsx)
        self.adapter = SeldonFirstAdapter(
            login_url=settings.platform_login_url,
            username=settings.platform_username,
            password=settings.platform_password,
            selectors_path=settings.selector_config.config_path,
        )

    def run(self) -> None:
        tenders = load_tenders(
            xls_path=self.settings.input_xls,
            url_column=self.settings.platform_tender_url_column,
            id_column=self.settings.platform_tender_id_column,
            title_column=self.settings.platform_tender_title_column,
        )
        tenders.sort(key=lambda item: (item.deadline_at is None, item.deadline_at))
        if self.settings.tender_skip:
            tenders = tenders[self.settings.tender_skip :]
        if self.settings.tender_limit is not None:
            tenders = tenders[: self.settings.tender_limit]
        self.sheet_writer.ensure_header()

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.settings.playwright_headless)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            self.adapter.login(page)
            page.close()

            for tender in tenders:
                result = self._process_one(context, tender)
                self.sheet_writer.append_result(result)

            context.close()
            browser.close()

    def _process_one(self, context, tender) -> TenderAnalysisResult:
        tender_dir = self.settings.download_dir / _safe_dirname(tender.tender_id)
        try:
            downloaded = self.adapter.download_documents(context, tender, tender_dir)
            analysis = self.analyzer.analyze(tender.url, downloaded.files)
            return TenderAnalysisResult(
                tender_id=tender.tender_id,
                title=tender.title,
                url=tender.url,
                deadline_at=tender.deadline_at,
                customer=tender.customer,
                customer_inn=tender.customer_inn,
                status="ok",
                classification_tag=analysis.classification_tag,
                confidence_percent=analysis.confidence_percent,
                classification_comment=analysis.classification_comment,
                downloaded_files=[str(path) for path in downloaded.files],
                analysis_markdown=analysis.analysis_markdown,
            )
        except DocumentAccessBlockedError as exc:
            return TenderAnalysisResult(
                tender_id=tender.tender_id,
                title=tender.title,
                url=tender.url,
                deadline_at=tender.deadline_at,
                customer=tender.customer,
                customer_inn=tender.customer_inn,
                status="blocked",
                classification_tag="Не изучено",
                confidence_percent=10,
                classification_comment="Документы не получены из-за антибот-защиты площадки, поэтому содержательная оценка тендера не проводилась.",
                downloaded_files=[],
                analysis_markdown="",
                error=str(exc),
            )
        except Exception as exc:
            error_message = str(exc)
            comment = "Не удалось обработать тендер из-за ошибки пайплайна."
            confidence = 0
            if "Documents were not found in Seldon and no external source link was detected" in error_message:
                comment = "Документы не найдены ни в Seldon, ни по внешней ссылке, поэтому содержательная оценка тендера не проводилась."
                confidence = 5
            return TenderAnalysisResult(
                tender_id=tender.tender_id,
                title=tender.title,
                url=tender.url,
                deadline_at=tender.deadline_at,
                customer=tender.customer,
                customer_inn=tender.customer_inn,
                status="error",
                classification_tag="Не изучено",
                confidence_percent=confidence,
                classification_comment=comment,
                downloaded_files=[],
                analysis_markdown="",
                error=error_message,
            )


def _safe_dirname(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)[:100] or "tender"
