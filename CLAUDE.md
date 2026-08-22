# tenders — разбор тендерной документации

Автозагружается в начале сессии. Жёсткие правила интерпретации — в
[PROJECT_MEMORY.md](PROJECT_MEMORY.md), политика отбора — [TRIAGE_POLICY.md](TRIAGE_POLICY.md),
обзор — [README.md](README.md).

## Что это
Агент для exinot/flaton: выгрузка тендеров из Seldon → скачивание документов через Playwright →
анализ через LLM → запись в `tender_analysis.xlsx`. Промпт: `prompt_template.md`.

## Главные правила
- **Verified findings:** вывод LLM попадает в Excel-summary только если его цитата реально найдена
  в извлечённом тексте. Summary строится системой из проверяемых фактов, не моделью.
- `tender_analysis.xlsx` — рабочий файл пользователя: новые выгрузки ДОБАВЛЯЮТ строки, не удаляют
  старые и не перетирают пользовательские статусы/комментарии.
- Промпт защищён от prompt injection (документы = данные, не инструкции) и содержит рубрику
  для `decision` (Брать / Не брать / Уточнить) — пороги ориентированы на профиль Flaton
  (разработка ПО, оборот ~30 млн).
- При сетевом или LLM-сбое результат пишется как технический статус, а не как успешный анализ.

## Команды
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_audit_regressions
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python run_regression_pack.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python clean_extraction_cache.py   # после правок extraction
```

## Что НЕ коммитить
`tender_analysis.xlsx`, `artifacts/`, `downloads/`, `manual_downloads/`, `state/`, `logs/`,
`llm_errors.log`, `~$*.xlsx` — всё уже в `.gitignore`.

## В конце сессии
Прогнать оба набора тестов, закоммитить и запушить. Ветка — `codex/extraction-quality-report`,
main домержен.

## Портфель
Решения уровня портфеля — `~/Documents/MASTER`.
