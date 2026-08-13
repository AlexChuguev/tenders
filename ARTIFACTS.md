# Artifact Layer

Артефакты нужны затем, чтобы `tender_analysis.xlsx` перестал быть единственным источником состояния.

## Что пишется

Каждый обработанный тендер сохраняется как отдельный JSON:

- `artifacts/<source>/<batch_name>/<slug>__<hash>.json`

Дополнительно в той же папке ведётся:

- `_index.jsonl`

## Что лежит в JSON

- `schema_version`
- `artifact_type`
- `written_at`
- `source`
- `batch_name`
- `policy`
- `result`
- `analysis`
- `extra`

`result` — это сериализованный `TenderAnalysisResult`.

`analysis` — это промежуточный результат LLM-анализа, включая:

- решение модели
- confidence модели
- `summary_points`
- `analysis_markdown`
- `facts`
- `llm_raw_text`

`policy` — это версия rule-based triage policy, использованной при анализе:

- `path`
- `sha1`

## Где используется

Артефакты сейчас пишутся из:

- `tender_agent/local_review.py`
- `review_seldon_api_batch.py`
- `tender_agent/agent.py`
- `tender_agent/tenderplan_pipeline.py`

## Зачем это нужно

Это даёт:

- воспроизводимость результатов
- возможность backfill без повторного чтения документов
- rebuild Excel из артефактов
- сравнение старых и новых правил triage
- понимание, на какой версии `triage_policy.json` был получен результат

## Rebuild Excel

Из артефактов можно пересобрать Excel:

```bash
.venv/bin/python rebuild_excel_from_artifacts.py --replace
```

Полезные фильтры:

```bash
.venv/bin/python rebuild_excel_from_artifacts.py --source local_review --replace
.venv/bin/python rebuild_excel_from_artifacts.py --source review_seldon_api_batch --batch Seldon.Pro_2026-04-01_09.10.00 --replace
.venv/bin/python rebuild_excel_from_artifacts.py --policy-sha1 <sha1> --replace
```

По умолчанию берутся:

- `ARTIFACT_DIR` из `.env`, либо `./artifacts`
- `OUTPUT_XLSX` из `.env`

## Ограничение

Excel пока всё ещё остаётся основным пользовательским представлением данных. Но после введения артефактов он больше не является единственным хранилищем результатов.

## Reprocess

Старые артефакты можно пересчитать на новой prompt/policy:

```bash
.venv/bin/python reprocess_artifacts.py --source local_review --policy-sha1 <old_sha1> --replace
```

Скрипт:

- берёт latest-артефакты в выбранной области
- использует `downloaded_files` из результата
- пересчитывает анализ на текущих `prompt_template.md` и `triage_policy.json`
- пишет новые артефакты в `artifacts/reprocess_artifacts/...`
- заново собирает Excel

## Compare

Сравнение old/new результатов:

```bash
.venv/bin/python compare_artifact_runs.py \
  --left-source local_review \
  --left-policy-sha1 <old_sha1> \
  --right-source reprocess_artifacts \
  --right-batch smoke_rerun
```

Сравнение reprocess-артефакта с его embedded `previous_result`:

```bash
.venv/bin/python compare_artifact_runs.py \
  --embedded-previous \
  --right-source reprocess_artifacts \
  --right-batch smoke_rerun
```
