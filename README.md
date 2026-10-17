# Аркана

Учебный Telegram-бот по «Аркана_архитектура.pdf», версия ТЗ 1.0.
Python 3.12+, aiogram, PostgreSQL 17, SQLAlchemy/asyncpg, Alembic,
Pydantic и HTTP API модели. Один процесс, long polling, задания в PostgreSQL.
F1: персональный расклад; F2: карта дня и дневник; F3: зеркало прошлого месяца.

Все команды ниже выполняются из папки `first`. Для живого запуска нужны настоящий
токен Telegram, доступ к DeepSeek-совместимому API и фактическое имя модели.
Секреты не включены в поставку. `DEMO_MODE=true` задаёт учебную квоту 10 вопросов
в локальные сутки, а не подменяет внешние API. При `false` квота — один вопрос
в календарную неделю с понедельника. Карта дня и история квоту не расходуют.

## Запуск через Docker Compose

Установите Docker с поддержкой Compose. В PowerShell:

```powershell
Copy-Item .env.example .env
```

В `.env` заполните `BOT_TOKEN`, `LLM_BASE_URL` (HTTPS, базовый путь до
`/chat/completions`, например с `/v1`), `LLM_API_KEY`, `LLM_MODEL`.
Установите собственный `POSTGRES_PASSWORD` из букв и цифр. Для локального
Python также укажите этот пароль в `DATABASE_URL`. Compose формирует свой
адрес БД с именем хоста `db`. Не отправляйте `.env` в Git.

```powershell
docker compose build app
docker compose up -d db
docker compose run --rm app python -m alembic upgrade head
docker compose run --rm app python -m app.cli seed
docker compose run --rm app python -m app.cli check
docker compose run --rm app python -m app.cli smoke
docker compose up -d app
```

`smoke` проверяет getMe и один действительный запрос к модели (расход API).
Он не отправляет сообщения пользователям. При отсутствии JSON mode у шлюза
задайте `llm.json_mode: false` в `config.yaml` и пересоберите образ.
Пустой/невалидный ответ, неверные ссылки памяти, таймаут или отказ API дают
помеченный локальный fallback. 401/403 не повторяются. Внешние HTTP-запросы
идут вне транзакций БД.

Откройте бота по ссылке из результата `smoke`, нажмите `/start`, примите
условия, введите имя, выберите интересы, часовой пояс и подтвердите профиль.
Напоминания первоначально выключены; настройте их в меню «Настройки».
Для одного токена запускайте только один экземпляр приложения.

```powershell
docker compose logs --tail 100 app
docker compose stop
```

`docker compose down` удаляет контейнеры и сеть, сохраняя volume БД.
Не добавляйте `-v`, если хотите сохранить историю.

## Локальный Python

Нужен доступный PostgreSQL и отдельная БД `arcana`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
# Заполнить .env, DATABASE_URL должен указывать на локальный PostgreSQL.
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m app.cli seed
.\.venv\Scripts\python.exe -m app.cli check
.\.venv\Scripts\python.exe -m app.cli smoke
.\.venv\Scripts\python.exe -m app.main
```

Остановка — Ctrl+C. На Linux используйте `.venv/bin/python`.
Поставляемое окружение `.venv` служит локальной проверке; на другой машине
создайте его заново по командам выше. `requirements.txt` закрепляет версии
прямых runtime-зависимостей, `requirements-dev.txt` — инструментов проверки.

## Колода

`assets/deck.json` содержит 78 карт, уникальные ID 0–77, названия, значения
и пути. Генератор этого справочника — `scripts/build_deck.py`; запускать его
при обычной эксплуатации не нужно. Выбор делает `SystemRandom.sample`,
а не модель. Перевёрнутых карт нет.

`assets/images/` содержит готовые сканы. Источники и SHA-256 находятся в
`assets/image_sources.json`, сведения об использовании — в `assets/LICENSE.md`.
Для повторной загрузки отсутствующих файлов:

```powershell
.\.venv\Scripts\python.exe scripts/fetch_deck_images.py
```

После изменения справочника снова выполните `app.cli seed` и `app.cli check`.
Изображения PNG/JPEG должны занимать не более 1 МиБ каждое. При отсутствии
индивидуального изображения используется `assets/images/back.jpg` с подписью
карты. Если нет и рубашки, запуск блокируется. Отсутствие значений карт или
несовместимая версия миграции также блокируют запуск.

## Демонстрация прошлого месяца

На отдельном тестовом Telegram-профиле сначала завершите `/start`.
Подставьте его числовой Telegram ID (это идентификатор, не токен).

```powershell
docker compose run --rm app python -m app.cli demo 123456789
```

Локальный эквивалент: `.\.venv\Scripts\python.exe -m app.cli demo 123456789`.
Команда при `DEMO_MODE=true` добавляет 4 явно помеченных демонстрационных
расклада и 4 заметки с оценками прошлого месяца; повтор команды не дублирует их.
Загружайте историю **до первого открытия** «Зеркала месяца»: отчёт сохраняется
один раз и повторно не пересчитывается. Ожидаются 4 вопроса, 0 карт дня,
4 заметки, «Сила» дважды, среднее настроение 3.0 и 4.5 в половинах месяца.

## Тесты и приёмка

Быстрые контрактные проверки не требуют секретов или БД:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_contracts.py -q
```

Интеграционные проверки используют настоящий PostgreSQL и очищают только
явно выбранную тестовую БД. Её имя обязано оканчиваться на `_test`.
Не указывайте рабочую БД. Для Compose:

```powershell
docker compose exec -T db createdb -U arcana arcana_test
$env:TEST_DATABASE_URL = 'postgresql+asyncpg://arcana:YOUR_PASSWORD@localhost:5432/arcana_test'
.\.venv\Scripts\python.exe -m pytest -q -s
```

Замените `YOUR_PASSWORD` паролем из своего `.env`. Без `TEST_DATABASE_URL`
интеграционные проверки пропускаются, что **не означает прохождение приёмки**.
В тестах подменяются только внешние Telegram/LLM-сервисы; транзакции,
ограничения, агрегаты и очередь выполняются на PostgreSQL.
Реальные p95 Telegram/модели и снимки интерфейса требуют подключённых API.
Статус по T01–T16 и AC01–AC14 фиксируется в `IMPLEMENTATION_CHECKLIST.md`.

## Резервирование и восстановление

`app.cli seed` создаёт пустой `runtime/deletions.log`. Каждое подтверждённое
удаление добавляет внутренний UUID перед удалением профиля; файл не содержит
вопросов, заметок и Telegram ID. Храните его отдельно от восстанавливаемых
снимков и копируйте на защищённый носитель. При аварии между записью реестра
и COMMIT UUID уже попадает в реестр: повтор подтверждения завершает удаление,
а восстановление не возвращает такой профиль.

Ежедневный backup в PowerShell:

```powershell
powershell -NoProfile -File scripts/backup.ps1
```

На Linux: `sh scripts/backup.sh`. Настройте ежедневный вызов в планировщике
ОС/cron. Скрипты оставляют снимки за последние 7 суток. Ограничьте доступ к
`backups/`, `runtime/` и `.env` владельцем приложения; не публикуйте эти папки.

Восстановление заменяет текущую БД выбранным снимком. Остановите обычную
работу и используйте актуальный реестр удалений, а не старую копию из backup:

```powershell
powershell -NoProfile -File scripts/restore.ps1 -BackupFile arcana-20261016-090000.dump
docker compose up -d app
```

Подставьте существующее имя файла. Скрипт останавливает app, выполняет
`pg_restore --clean --if-exists --single-transaction`, применяет реестр командой
`app.cli apply-deletions`, проверяет схему/колоду. При ошибке app остаётся
остановленным. На Linux эквивалентные команды:

```sh
docker compose stop app
docker compose exec -T db pg_restore -U arcana -d arcana --clean --if-exists --single-transaction /backups/EXISTING_BACKUP.dump
docker compose run --rm app python -m app.cli apply-deletions
docker compose run --rm app python -m app.cli check
docker compose up -d app
```

Переходите к следующей команде только после успешного завершения предыдущей.
Telegram-переписка и ранее переданный провайдеру ИИ текст не удаляются командой
удаления профиля.

## Поведение при сбоях

События сначала сохраняются в `jobs`, затем повышается offset Telegram.
Обработка события и изменение диалога/создание записи атомарны. Карты
сохраняются до обращения к модели. Lease заданий — 60 секунд, просроченные
задания восстанавливаются; результат со старым lease отбрасывается.
Доставка хранит message_id успешных шагов, повтор не перегенерирует расклад.
После потерянного сетевого ответа Telegram возможен повтор сообщения:
Bot API не предоставляет ключ строго однократной отправки.

Смена времени уведомления не меняет карту дня. Догоняющая утренняя отправка
возможна в пределах 2 часов; месячная — в первые 7 дней следующего месяца.
Месячный отчёт доступен вручную независимо от этого окна.

Логи содержат только идентификатор запроса, действие, длительность и код
ошибки. Обработанные update payload очищаются; квитанции — через 7 суток,
метаданные ошибок — через 14. Черновики истекают через 10 минут.
Локальные защитные правила не распознают все возможные опасные формулировки;
перед публичным запуском нужны проверки реальных примеров и проверенные
контакты поддержки для выбранной аудитории. Номера служб бот не придумывает.

Технические ссылки: [aiogram getUpdates](https://docs.aiogram.dev/en/latest/api/methods/get_updates.html),
[SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html),
[DeepSeek JSON mode](https://api-docs.deepseek.com/guides/json_mode/).
