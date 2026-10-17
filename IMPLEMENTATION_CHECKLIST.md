# Аркана — проверка реализации

Дата: **17.10.2026**, Asia/Novosibirsk. Источник требований:
`Аркана_архитектура.pdf`, версия 1.0 от 13.10.2026, все 15 страниц повторно
прочитаны после реализации. Прототипы на страницах 3, 5 и 11 просмотрены.

**Полный pytest: 60 passed, 0 failed, 0 errors, 0 skipped; 30.63 s.**
Результат: `tests/results/acceptance.xml`, пояснения: `TEST_RESULTS.md`.
Использован настоящий PostgreSQL 17.11. Подменены только внешние Telegram/LLM.
Включён реальный цикл `pg_dump` → `pg_restore` → применение реестра удалений.

Здесь **DONE** означает существующий код и указанную автоматическую проверку
на локальном стенде. Это не утверждение о запуске с настоящими Telegram/LLM.
**PARTIAL** — выполнена только указанная часть; **BLOCKED** — отсутствует
необходимый внешний ресурс. Общий Definition of Done **не закрыт**:
нет токена бота, параметров доступа/имени реальной модели и живой демонстрации.

## Требования и повторный self-audit

| Requirement | Status | Where implemented | How verified |
|-------------|--------|-------------------|--------------|
| §1.1 F1: тема, вопрос, one/three/choice, варианты, результат, история | DONE | `app/bot/handlers/`, `services/reading_service.py`, `workers/generation.py` | `test_ac01_persistent_reading` (3 типа), `test_dialog_transaction_and_duplicate_confirmation` |
| §1.1 F2: одна карта дня, самостоятельный/связанный дневник | DONE | `reading_service.py`, `diary_service.py`, `scheduler_service.py` | AC06–AC08, в том числе конкурентный ручной/автоматический запрос |
| §1.1 F3: пустой, короткий, полный отчёт и SQL fallback | DONE | `monthly_report_service.py`, `repositories/statistics.py`, `formatters.py` | AC09–AC10, `test_ac10_full_monthly_generation_and_delivery` |
| §1.1 Отложенные платные/веб/голосовые функции не добавлены | DONE | Структура `app/`, `requirements.txt`, `compose.yaml` | Просмотр исходников и зависимостей; только app + PostgreSQL |
| §1.2 Вход: только private chat, текст/callback, ограничения длины | DONE | `workers/updates.py`, `models/dto/`, `config.yaml` | `test_ac03_*`, `test_minimal_update_never_retains_media_or_contacts` |
| §1.2 Вывод: ограниченные DTO, абзацы до 3500, отчёт до 6000 | DONE | `models/dto/`, `services/formatters.py` | `test_normalization_and_text_split`, контрактные проверки LLM |
| §1.2 Квоты 10/сутки, 1/неделю; day/history/failed не учитываются | DONE | `services/reading_service.py`, `config.yaml` | `test_queue_quota_storage_and_busy`; проверка фильтров и границ периода в коде |
| §1.2 3 параллельных LLM, очередь 50, одна генерация на профиль | DONE | `integrations/llm/`, `repositories/records.py`, `workers/generation.py` | `test_llm_contract_and_concurrency` (10 вызовов, максимум 3), `test_queue_quota_storage_and_busy` |
| §1.2 Операции без ИИ p95 ≤500 мс; 100 профилей/10 действий | DONE | PostgreSQL и сервисы | `test_ac14_pilot_local_db`: 50 действий, p95 56.5 мс, 100 профилей, пачки по 10 |
| §1.2 Подтверждение ≤1 с и p95 полного ответа ≤15 с на реальном API | BLOCKED | Таймауты реализованы; `app.cli smoke` подготовлен | Нет настоящих BOT_TOKEN, LLM_BASE_URL/KEY/MODEL; локальный p95 не подменяет этот замер |
| §1.2 Согласие и объяснение передачи контекста | DONE | `services/profile_service.py`, обработчик onboarding | `test_onboarding_media_and_durable_dialog`; профиль без завершённого onboarding не допускает генерацию |
| §1.2 Очистка update, TTL 10 мин, квитанции 7 дней, ошибки 14 дней | DONE | `repositories/jobs.py`, `workers/updates.py`, `main.py` | `test_retention_clears_expired_draft_and_receipts`, проверка очистки payload при обработке |
| §1.2 Ежедневный backup, 7-дневное хранение, ограниченный доступ | PARTIAL | `scripts/backup.ps1`, `backup.sh`, README | Реальный dump/restore проверен; расписание ОС и права рабочего сервера нужно настроить при развёртывании |
| §2.1 Модульный монолит, обязательный стек, без брокеров | DONE | `app/`, `requirements.txt`, `compose.yaml` | Импорт/compileall, pip check, проверка структуры Compose |
| §2.1 Интерфейс не выполняет SQL | DONE | `bot/handlers/` использует repository/application service | Повторный просмотр: SQL перенесён в `repositories/records.py`; регрессионные тесты диалогов |
| §2.2 Транзакция до LLM, неизменные карты, сохранение до доставки | DONE | `reading_service.py`, `workers/generation.py`, `delivery_service.py` | AC01, AC04, AC05, AC12 |
| §2.3 Семь таблиц, UUID/BIGINT/UTC, индексы и миграция | DONE | `models/orm/`, `migrations/versions/0001_initial.py` | Миграция на чистой БД, все PostgreSQL-тесты |
| §2.3 CHECK, unique day, уникальные позиции/карты, composite owner FK | DONE | ORM и фиксированная миграция | AC04, AC06, `test_ac11_foreign_objects_and_composite_fk` |
| §2.3 Лимит 5000 readings/entries, история доступна после лимита | DONE | `reading_service.py`, `diary_service.py`, `repositories/records.py` | `test_queue_quota_storage_and_busy`; чтение истории не вызывает проверку storage_limit |
| §3.1 Внутренний API, ok/data/error, Pydantic, запрет лишних полей | DONE | `services/application_service.py`, `models/dto/` | `test_internal_api_contract_and_unknown_fields`, `test_response_has_exactly_one_data_or_error`; API подключён к основным действиям бота |
| §3.1 Изоляция actor_id, история 10, cursor по времени и UUID | DONE | `repositories/records.py`, `application_service.py` | AC02, AC11, `test_monthly_note_sampling_and_history_cursor` |
| §3.1 Ошибки без stacktrace и ложного успеха | DONE | `errors.py`, `workers/updates.py`, `application_service.py` | `test_storage_failure_has_no_success_receipt`, AC03/AC05 |
| §3.2 NFC, whitespace, переносы дневника, управляющие символы | DONE | `models/dto/` | `test_normalization_and_text_split`, сохранение многострочной заметки AC08 |
| §3.2 78 карт, SystemRandom.sample, позиции без повторов | DONE | `reading_service.py`, `assets/deck.json` | AC01; 78 записей, 79 корректных JPEG с рубашкой, максимальный файл 241546 байт |
| §3.2 Готовые иллюстрации и условия использования | DONE | `assets/images/`, `image_sources.json`, `LICENSE.md` | 79 файлов с исходными URL и SHA-256; проверены формат и размер; генерации изображений нет |
| §3.2 Память: 5 раскладов/тема/90 дней, 5 заметок, 6000 символов | DONE | `memory_service.py` | `test_ac02_memory_isolation`, `test_memory_cap_removes_whole_old_records`; приоритет связанных заметок в SQL |
| §3.2 LLM DTO, JSON mode, model из env, параметры и ссылки | DONE | `integrations/llm/`, `prompts/` | Контрактные HTTP-тесты, `test_llm_contract_and_concurrency`, AC05 |
| §3.2 3/12/30 секунд, один допустимый retry, no retry 401/403 | DONE | `integrations/llm/`, `workers/generation.py` | `test_ac05_http_retry`, `test_ac05_timeout_budget`, bad response тесты |
| §3.2 Локальный fallback по тем же картам; SQL fallback отчёта | DONE | `integrations/llm/`, `workers/generation.py`, `formatters.py` | AC05, AC10 |
| §3.2 Кризис/опасные советы, ограничения промпта и выхода | DONE | `services/safety.py`, оба промпта | `test_ac14_safety`; защита минимальная, не гарантирует распознавание любой формулировки |
| §3.2 Проверенные тексты/службы поддержки перед публичным запуском | PARTIAL | Поддерживающий текст без выдуманных номеров | Номера не добавлялись; перед публичным запуском нужна проверка поддержки для целевой страны |
| §3.2 IANA зона, day local_date, 09:00, opt-in, окно 2 часа | DONE | `time_service.py`, `scheduler_service.py`, profile/handlers | AC06–AC07 |
| §3.2 Месячный автозапуск с 09:00 и догон в первые 7 дней | DONE | `scheduler_service.py` | `test_monthly_catchup_window_and_invalid_month` |
| §3.2 jobs: COMMIT до offset, lease, SKIP LOCKED, сериализация | DONE | `repositories/jobs.py`, `workers/updates.py`, `generation.py` | AC04, AC12, `test_update_lease_preserves_actor_order`; проверка порядка ingest/offset в коде |
| §3.2 Доставка image/text, квитанции, повторы, 429, 403 | DONE | `delivery_service.py` | `test_ac12_lease_and_delivery_resume`; обработка retry_after/403 проверена по коду |
| §3.2 Месяц как UTC полуинтервал, ready/fallback, частоты позиций | DONE | `repositories/statistics.py`, `time_service.py` | AC09, проверка включения start и исключения end |
| §3.2 Mood только из оценок; половины месяца; порог 2+2 | DONE | `repositories/statistics.py`, `formatters.py`, monthly prompt | AC09: 3.0 и 4.5; просмотр ветки недостаточного количества оценок |
| §3.2 До 20 заметок равномерно, JSON контекст ≤10000 | DONE | `repositories/statistics.py` | `test_monthly_note_sampling_and_history_cursor` |
| §3.2 Startup: secrets, схема, 78 meanings, пути и рубашка | DONE | `config.py`, `deck_service.py`, `main.py` | `app.cli check`, seed и тест колоды; отсутствующие секреты блокируют live startup |
| §3.3 Онбординг, меню, отмена, FSM, старые callback | DONE | `bot/handlers/`, `profile_service.py` | Онбординг и двойное подтверждение; TTL и токен удаления |
| §3.3 Дневник, открытие записи, история месяца, настройки | DONE | `bot/handlers/`, `formatters.py` | AC08/AC10, тесты history cursor и сохранения; код кнопок соответствует отчёту |
| §3.3 Настоящие снимки интерфейса и ссылка | BLOCKED | Места в `Отчёт_Аркана.docx` | Бот ещё не подключён к Telegram; фиктивные снимки не создавались |
| §4–5 План, README, demo, checklist, пользовательский отчёт | DONE | README, CLI demo, этот файл, заполненный DOCX | Demo-статистика проверена; разделы исходного шаблона сохранены |

## T01–T16

| Requirement | Status | Where implemented | How verified |
|-------------|--------|-------------------|--------------|
| T01 Окружение/config/API smoke | PARTIAL | `.env.example`, config, requirements, `app.cli smoke` | Зависимости и локальное окружение работают; настоящие API недоступны без секретов |
| T02 ORM/migrations/indexes | DONE | ORM, миграция 0001 | Чистая PostgreSQL БД и интеграционные тесты |
| T03 repositories/transactions/owners | DONE | `repositories/`, сервисы | AC02/04/06/11/13 |
| T04 routes/consent/profile/FSM | DONE | handlers, profile service | Онбординг, отмена и устойчивое состояние в БД |
| T05 deck/selection/images | DONE | 78 карт, 79 JPEG, manifest | AC01 и проверка комплектности/форматов |
| T06 LLM adapter/DTO/fallback | DONE | `integrations/llm/`, DTO | HTTP MockTransport: timeout, 429, 401/403, плохой JSON/refs; live API — часть T01 |
| T07 memory/prompts | DONE | `memory_service.py`, prompts | Изоляция/лимит памяти и сборка контекста |
| T08 end-to-end F1 | DONE | dialog → service → job → delivery | AC01, транзакционный диалог, AC12; внешние сервисы заменены тестовыми |
| T09 diary/mood/history | DONE | diary, history, handlers | AC08 и пагинация |
| T10 durable jobs/retry/dedupe | DONE | jobs, workers, delivery | AC04/12, сохранение порядка после lease |
| T11 F2/timezone/reminders | DONE | reading/time/scheduler | AC06/07 |
| T12 monthly periods/SQL | DONE | statistics/time | AC09 и равномерная выборка заметок |
| T13 F3/generation/delivery | DONE | monthly/generation/delivery | AC10, полный месячный результат доставлен тестовым адаптером |
| T14 errors/limits/delete/privacy | DONE | errors/DTO/services/workers | AC03/05/11/13, storage failure, quota/queue/storage, TTL |
| T15 integration/acceptance | PARTIAL | `tests/` | Все 60 тестов прошли; AC14 live-часть ещё не проверена |
| T16 deployment/README/demo/handoff | PARTIAL | Compose, Dockerfile, README, backup/restore, DOCX | Локальный PostgreSQL и реальный restore проверены; Docker отсутствует, рабочий запуск/расписание backup ещё не выполнены |

## AC01–AC14

| Requirement | Status | Where implemented | How verified |
|-------------|--------|-------------------|--------------|
| AC01 | DONE | ReadingService, генерация/история | `test_ac01_selection`, `test_ac01_persistent_reading`: 3 типа, правильные позиции, разные карты, сохранённый текст |
| AC02 | DONE | MemoryService, LLM validation | `test_ac02_memory_isolation`, bad-ref контракт; только свой контекст |
| AC03 | DONE | DTO, minimal_event, handlers | `test_ac03_reading_input`, strict mood, media/onboarding: невалидные входы отклонены |
| AC04 | DONE | request_key/dedupe/transactions | `test_ac04_concurrent_request_deduplication`, `test_dialog_transaction_and_duplicate_confirmation` |
| AC05 | DONE | LLM adapter/fallback | HTTP 429/500/401/403, timeout/budget, пустой/плохой JSON, чужие refs; fallback сохраняет карты |
| AC06 | DONE | get_or_create_day/unique index | `test_ac06_day_race_and_next_day`: scheduler и ручной запрос одновременно, повтор и следующая дата |
| AC07 | DONE | time/scheduler | Граница суток, две зоны, окно 1 ч/3 ч; `test_ac07_*` |
| AC08 | DONE | diary/DB/update worker | Связанная и самостоятельная записи, новая сессия БД, rollback без успешной квитанции |
| AC09 | DONE | SQL statistics | `test_ac09_statistics_boundaries_and_mood`: границы, частоты карт, mood 3.0/4.5 |
| AC10 | DONE | monthly service/formatter | Ноль/два/много данных, SQL fallback, повтор, полный отчёт с доставкой |
| AC11 | DONE | owned/composite FK/FSM | Чужие reading/diary/report отклонены; чужая связь отвергнута самой БД |
| AC12 | DONE | lease/receipts/delivery | Старый lease отклонён, задача восстановлена, отправленная картинка не повторена, ИИ вызван один раз |
| AC13 | DONE | deletion/registry | Отмена/TTL/owner token, поздний LLM отброшен; реальный pg_dump/pg_restore и replay удалений |
| AC14 | PARTIAL | Pilot test, safety, concurrency | 100 профилей, 10 действий одновременно, 50 локальных замеров, p95 56.5 мс. Не измерены 50 полных путей через настоящие Telegram/LLM и их p95 |

## Оставшиеся внешние шаги

1. Создать бота и заполнить `.env`: BOT_TOKEN, LLM_BASE_URL, LLM_API_KEY,
   фактический LLM_MODEL; выбрать рабочий пароль/адрес PostgreSQL.
2. Запустить миграцию/seed/check/smoke и бота по README. Проверить совместимость
   JSON mode с университетским шлюзом; при необходимости выключить параметр
   конфигурацией, сохранив обязательную проверку JSON/DTO.
3. Пройти F1–F3 в настоящем Telegram, выполнить live-часть AC14 (50 полных
   путей, p95 подтверждения/ответа), вставить ссылку и настоящие снимки в DOCX.
4. На рабочем сервере назначить ежедневный backup, ограничить права на секреты,
   копии и реестр удалений; проверить тексты поддержки перед публичным запуском.

Код не содержит обязательных TODO вместо реализации. Оставшиеся ограничения
касаются подключения внешних сервисов, рабочего развёртывания и его приёмки.
