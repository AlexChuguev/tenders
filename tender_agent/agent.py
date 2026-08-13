from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

from tender_agent.analysis import TenderAnalyzer
from tender_agent.artifact_writer import ArtifactWriter
from tender_agent.config import Settings
from tender_agent.excel_writer import ExcelWriter
from tender_agent.excel_loader import load_tenders
from tender_agent.models import TenderAnalysisResult
from tender_agent.platforms import DocumentAccessBlockedError, SeldonFirstAdapter
from tender_agent.seldon_added_at import _extract_seldon_added_at


class TenderAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.analyzer = TenderAnalyzer(
            provider_name=settings.llm_provider,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            prompt_template_path=settings.prompt_template_path,
            max_chars_per_file=settings.analysis_max_chars_per_file,
            triage_policy_path=settings.triage_policy_path,
        )
        self.sheet_writer = ExcelWriter(output_path=settings.output_xlsx)
        self.artifact_writer = ArtifactWriter(
            root=settings.artifact_dir,
            source="agent",
            batch_name=settings.input_xls.stem or "agent_run",
            policy_metadata=self.analyzer.policy_metadata,
        )
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
            analysis = self.analyzer.analyze_with_context(
                tender_url=tender.url,
                files=downloaded.files,
                tender_title=tender.title,
            )
            seldon_added_at = self._fetch_seldon_added_at(context, tender.url)
            result = TenderAnalysisResult(
                tender_id=tender.tender_id,
                title=tender.title,
                url=tender.url,
                batch_name=self.settings.input_xls.stem or "agent_run",
                batch_path=str(self.settings.download_dir),
                deadline_at=tender.deadline_at,
                nmck_rub=None,
                seldon_added_at=seldon_added_at,
                customer=tender.customer,
                customer_inn=tender.customer_inn,
                decision=analysis.decision,
                confidence_percent=analysis.confidence_percent,
                summary_text="\n".join(analysis.summary_points),
                downloaded_files=[str(path) for path in downloaded.files],
                analysis_markdown=analysis.analysis_markdown,
                document_completeness=analysis.completeness_label,
                facts_stack="",
                facts_requirements="",
                facts_docs="",
                facts_payment="",
            )
            self.artifact_writer.write(
                result=result,
                analysis=analysis,
                extra={"tender_raw": tender.raw},
            )
            return result
        except DocumentAccessBlockedError as exc:
            result = TenderAnalysisResult(
                tender_id=tender.tender_id,
                title=tender.title,
                url=tender.url,
                batch_name=self.settings.input_xls.stem or "agent_run",
                batch_path=str(self.settings.download_dir),
                deadline_at=tender.deadline_at,
                nmck_rub=None,
                seldon_added_at=self._fetch_seldon_added_at(context, tender.url),
                customer=tender.customer,
                customer_inn=tender.customer_inn,
                decision="Уточнить",
                confidence_percent=10,
                summary_text="\n".join(
                    [
                        "1. Документы не получены из-за антибот-защиты площадки.",
                        "2. Содержательная оценка тендера не выполнялась.",
                        "3. Невозможно подтвердить стек и объём работ.",
                        "4. Невозможно проверить требования к участнику.",
                        "5. Нужен ручной доступ к документам для повторной оценки.",
                    ]
                ),
                downloaded_files=[],
                analysis_markdown="",
                document_completeness="низкая",
                error=str(exc),
                facts_stack="",
                facts_requirements="",
                facts_docs="",
                facts_payment="",
            )
            self.artifact_writer.write(
                result=result,
                analysis=None,
                extra={"tender_raw": tender.raw, "error_type": "DocumentAccessBlockedError"},
            )
            return result
        except Exception as exc:
            error_message = str(exc)
            comment = "Не удалось обработать тендер из-за ошибки пайплайна."
            confidence = 0
            if "Documents were not found in Seldon and no external source link was detected" in error_message:
                comment = "Документы не найдены ни в Seldon, ни по внешней ссылке, поэтому содержательная оценка тендера не проводилась."
                confidence = 5
            result = TenderAnalysisResult(
                tender_id=tender.tender_id,
                title=tender.title,
                url=tender.url,
                batch_name=self.settings.input_xls.stem or "agent_run",
                batch_path=str(self.settings.download_dir),
                deadline_at=tender.deadline_at,
                nmck_rub=None,
                seldon_added_at=self._fetch_seldon_added_at(context, tender.url),
                customer=tender.customer,
                customer_inn=tender.customer_inn,
                decision="Уточнить",
                confidence_percent=confidence,
                summary_text="\n".join(
                    [
                        f"1. {comment}",
                        "2. Содержательная оценка тендера не завершена.",
                        "3. Технические и квалификационные требования не подтверждены автоматически.",
                        "4. Условия участия и оплаты не разобраны.",
                        "5. Нужен повторный запуск после получения документов.",
                    ]
                ),
                downloaded_files=[],
                analysis_markdown="",
                document_completeness="низкая",
                error=error_message,
                facts_stack="",
                facts_requirements="",
                facts_docs="",
                facts_payment="",
            )
            self.artifact_writer.write(
                result=result,
                analysis=None,
                extra={"tender_raw": tender.raw, "error_type": type(exc).__name__},
            )
            return result

    def _fetch_seldon_added_at(self, context, url: str):
        page = context.new_page()
        page.set_default_timeout(15000)
        page.set_default_navigation_timeout(20000)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            try:
                page.wait_for_load_state("networkidle", timeout=7000)
            except Exception:
                pass
            body_text = page.locator("body").inner_text(timeout=5000)
            return _extract_seldon_added_at(body_text)
        except Exception:
            return None
        finally:
            page.close()


def _safe_dirname(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)[:100] or "tender"
