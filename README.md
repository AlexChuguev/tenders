# Tender Agent

Python-агент для обработки выгрузки тендеров из Excel, скачивания документов через браузер, анализа документов через OpenAI API и записи результата в локальный `xlsx`.

## Что уже реализовано

- Импорт тендеров из `.xls`
- Логин в Seldon через Playwright
- Проверка документов сначала в Seldon, затем fallback на внешнюю площадку
- Скачивание документов по настраиваемым CSS-селекторам и эвристикам по ссылкам
- Анализ документов в OpenAI Responses API по вашему шаблону промпта
- Поддержка разных LLM-провайдеров через единый слой провайдеров
- Запись результата в локальный `xlsx`

## Ограничения

- Автологин и fallback на внешнюю площадку зависят от конкретной вёрстки. Для запуска лучше настроить `platform_selectors.json` под вашу страницу логина и карточку тендера.
- Входной файл должен содержать колонку со ссылкой на тендер. Для выгрузки Seldon.Pro агент уже умеет читать Excel hyperlinks из колонки `Номер извещения (ссылка на источник)`.
- Выходной Excel создаётся локально в папке проекта.

## Быстрый старт

1. Создайте виртуальное окружение и установите зависимости:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m playwright install chromium
```

2. Подготовьте конфиг:

```bash
cp .env.example .env
cp platform_selectors.example.json platform_selectors.json
```

3. Заполните `.env`:

- `LLM_PROVIDER`
- `LLM_API_KEY`
- `LLM_MODEL`
- при необходимости `LLM_BASE_URL` для OpenAI-compatible API
- `MAX_FILES_PER_TENDER` — ограничение числа файлов на один тендер, чтобы не упираться в лимиты модели
- `TENDER_INPUT_XLS`
- `OUTPUT_XLSX`
- `PLATFORM_LOGIN_URL`
- `PLATFORM_USERNAME`
- `PLATFORM_PASSWORD`
- при необходимости названия колонок `PLATFORM_TENDER_*_COLUMN`
- при необходимости `TENDER_LIMIT` для тестового прогона на первых N тендерах

4. При необходимости настройте `platform_selectors.json`:

- `login.username`: селектор поля логина
- `login.password`: селектор поля пароля
- `login.submit`: селектор кнопки входа
- `login.success_wait_for`: селектор или текст, который появляется после успешного входа
- `documents.links`: список CSS-селекторов ссылок на документы

Сначала агент пытается найти документы прямо на странице Seldon по всем ссылкам на странице. Если не находит, он ищет внешнюю ссылку на источник закупки и уже для неё использует `platform_selectors.json`.

5. Запустите:

```bash
python3 main.py
```

## Локальный режим без скачивания через площадки

Если документы вы скачиваете вручную, используйте локальный режим:

1. Сложите файлы в папку `manual_downloads`.
2. Поддерживаются два варианта:

- надёжный: отдельная папка на каждый тендер с именем `tender_id`
- упрощённый: все файлы в одной общей папке, тогда агент попробует сам сопоставить их по `tender_id`, названию и содержимому `pdf/docx`

Лучший вариант:

```bash
manual_downloads/1813130021-3/file1.pdf
manual_downloads/1813130021-3/file2.docx
```

Упрощённый вариант:

```bash
manual_downloads/file1.pdf
manual_downloads/file2.docx
manual_downloads/file3.docx
```

Если внутри папки лежат `zip`-архивы, агент автоматически распакует их перед анализом в служебные подпапки `__extracted`.

3. Укажите дату в `.env`:

- `REVIEW_TARGET_DATE=yesterday` — взять тендеры за вчера
- или `REVIEW_TARGET_DATE=2026-03-11` — конкретная дата

4. Запустите:

```bash
python3 review_local.py
```

Агент возьмёт тендеры из входного `xls`, отфильтрует их по дате окончания приёма заявок, найдёт локальные файлы по `tender_id`, выполнит анализ через OpenAI и запишет результат в `tender_analysis.xlsx`.

## LLM архитектура

Слой анализа теперь отделён от конкретной модели. Поддерживаются провайдеры:

- `openai` — обычный OpenAI API
- `openai_compatible` — любой совместимый API с `base_url`
- `stub` — тестовый провайдер без реального вызова модели

Примеры:

```bash
LLM_PROVIDER=openai
LLM_API_KEY=...
LLM_MODEL=gpt-4.1
```

```bash
LLM_PROVIDER=openai_compatible
LLM_API_KEY=...
LLM_MODEL=your-model
LLM_BASE_URL=https://your-endpoint.example.com/v1
```

```bash
LLM_PROVIDER=stub
LLM_MODEL=test
```

Чтобы добавить новый провайдер, достаточно реализовать интерфейс `analyze_documents(prompt, files)` в новом модуле внутри `tender_agent/llm/` и зарегистрировать его в `factory.py`.

## Структура проекта

- `main.py` — точка входа
- `review_local.py` — локальный режим анализа вручную скачанных файлов
- `prepare_folders.py` — создание папок по выгрузке в порядке дедлайнов
- `tender_agent/excel_loader.py` — чтение `.xls`
- `tender_agent/platforms.py` — логин в Seldon, проверка документов и fallback на внешнюю площадку
- `tender_agent/analysis.py` — отправка документов в OpenAI
- `tender_agent/llm/` — слой LLM-провайдеров
- `tender_agent/excel_writer.py` — запись результатов в `xlsx`

## Подготовка папок для ручной раскладки файлов

Если документы скачиваются вручную, можно заранее создать папки по тендерам из выгрузки:

```bash
python3 prepare_folders.py /path/to/Seldon.Pro_2026-03-13_17.44.27.xls
```

Папки будут созданы в `manual_downloads/<имя_файла_выгрузки>/` и отсортированы по ближайшей дате окончания приёма заявок.  
Чтобы порядок совпадал с Finder, папки получают числовой префикс: `001. ...`, `002. ...`, `003. ...`.

Рядом создаётся `manual_downloads/<...>/_manifest.csv`, где лежат:

- порядок
- `tender_id`
- полное название тендера
- имя папки
- дедлайн
- ссылка на тендер

Для очень длинных названий хвост имени папки может быть обрезан из-за ограничений файловой системы macOS. Полное название при этом сохраняется в `manifest`.

## Деплой

Проект подготовлен под такой сценарий:

1. Локально пушим в GitHub: `origin -> git@github.com:AlexChuguev/tenders.git`
2. GitHub Actions по SSH заходит на VPS.
3. На VPS запускается `deploy/deploy_from_github.sh`.
4. Скрипт на сервере сам делает:

```bash
git fetch origin main
git checkout main
git reset --hard origin/main
```

Файлы деплоя:

- `.github/workflows/deploy.yml`
- `deploy/deploy_from_github.sh`

Что нужно на GitHub:

- secrets `VPS_HOST`
- secrets `VPS_USER`
- secrets `VPS_PORT`
- secrets `VPS_SSH_KEY`

Что нужно на VPS:

- рабочая папка `/var/www/tenders`
- обычный git-репозиторий внутри неё
- deploy key для GitHub, по умолчанию `/root/.ssh/tenders_github`

Тест деплоя:

- для проверки GitHub Actions можно сделать любой небольшой коммит в `main`
- workflow подключится к VPS и выполнит `bash ./deploy/deploy_from_github.sh`

При необходимости значения можно переопределить через переменные окружения:

- `PROJECT_DIR`
- `REPO_SSH_URL`
- `DEPLOY_KEY_PATH`
- `BRANCH`

## Что нужно донастроить под вашу площадку

- URL логина
- селекторы формы входа
- селекторы ссылок на документы
- при необходимости логику перехода по карточке тендера

Если пришлёте URL конкретной площадки или HTML/скрин страницы логина и карточки тендера, можно быстро довести адаптер до полностью рабочего состояния.
