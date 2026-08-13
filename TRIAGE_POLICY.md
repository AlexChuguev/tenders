# Triage Policy

`triage_policy.json` — это внешний конфиг для rule-based логики до и после LLM.

## Что вынесено в policy

- классификация `procurement_type`
- low-priority web/site правило
- enterprise hard/soft tokens
- infosec hard/medium tokens
- строительство / монтаж
- аутстаффинг
- core stack
- non-core stack
- confidence policy

## Где используется

- [tender_agent/policy.py](/Users/alexchuguev/Documents/tenders/tender_agent/policy.py)
- [tender_agent/document_facts.py](/Users/alexchuguev/Documents/tenders/tender_agent/document_facts.py)
- [tender_agent/triage_rules.py](/Users/alexchuguev/Documents/tenders/tender_agent/triage_rules.py)
- [tender_agent/analysis.py](/Users/alexchuguev/Documents/tenders/tender_agent/analysis.py)

## Как подключается

По умолчанию анализатор читает:

- [triage_policy.json](/Users/alexchuguev/Documents/tenders/triage_policy.json)

Путь можно переопределить через:

- `TRIAGE_POLICY_PATH`

## Структура

Основные секции:

- `procurement_types`
- `non_profile_procurement_types`
- `priority_hint`
- `enterprise_hard_tokens`
- `enterprise_soft_tokens`
- `development_context_tokens`
- `infosec_license_tokens`
- `infosec_hard_tokens`
- `infosec_medium_tokens`
- `construction_tokens`
- `outstaffing_tokens`
- `core_stack`
- `non_core_stack_tokens`
- `confidence`

## Зачем это нужно

Это убирает бизнес-логику фильтрации из жёстко прошитого кода. Теперь:

- фильтры можно править без переписывания `triage_rules.py`
- можно сравнивать несколько policy-профилей
- можно запускать повторный анализ старых batch через другой policy
- версия policy фиксируется в JSON-артефактах через `policy.sha1`

## Ограничение

Policy уже привязывается к артефактам, но rebuild пока не фильтрует результаты по `policy.sha1` автоматически. Если в одной папке артефактов смешаны результаты от разных policy-версий, это надо учитывать явно.
