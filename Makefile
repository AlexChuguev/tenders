.PHONY: help test test-regressions test-pack clean-cache status

help:  ## Показать доступные команды
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-18s %s\n", $$1, $$2}'

test: test-regressions test-pack  ## Полная минимальная проверка перед коммитом

test-regressions:  ## Юнит-тесты аудит-регрессий
	PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_audit_regressions

test-pack:  ## Regression pack
	PYTHONDONTWRITEBYTECODE=1 .venv/bin/python run_regression_pack.py

clean-cache:  ## Очистить кэш извлечения (после правок .doc/.pdf/.xlsx логики)
	PYTHONDONTWRITEBYTECODE=1 .venv/bin/python clean_extraction_cache.py

status:  ## Состояние репозитория
	@git status --short && git log --oneline -3
