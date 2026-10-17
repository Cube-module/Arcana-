from uuid import uuid4
from copy import deepcopy
from pydantic import ValidationError
from app.config import CONFIG
from app.models.orm import User, Reading, DiaryEntry, MonthlyReport, Job, utcnow
from app.models.dto import CreateReading, DiaryInput, Onboarding, normalize
from app.repositories.records import owned, history, actor_profile, by_request, bind_updates, initial_delivery_pending
from app.repositories.jobs import enqueue
from app.services.profile_service import CONSENT, set_state, finish_onboarding, save_reminder_settings, deletion_token, delete_profile
from app.services.reading_service import ReadingService
from app.services.diary_service import DiaryService
from app.services.monthly_report_service import MonthlyReportService
from app.services.formatters import TOPICS
from app.services.time_service import period
from app.services.application_service import ApplicationService, require_data
from app.bot.keyboards import MENU, buttons, topics
from app.errors import AppError


def reply(text, keyboard=None):
    return {"text": text, "reply_markup": keyboard or buttons()}


async def deliver_record(session, user, record, key):
    if record.status in ("ready", "fallback", "empty"):
        await enqueue(session, user_id=user.id, kind="delivery", reading_id=record.id if isinstance(record, Reading) else None, report_id=record.id if isinstance(record, MonthlyReport) else None, dedupe_key=key)
        return None
    return reply("Тяну карты и собираю трактовку…" if isinstance(record, Reading) else "Собираю итоги месяца…")


class Dialog:
    def __init__(self, demo_mode=True):
        self.readings = ReadingService(demo_mode)
        self.api = ApplicationService(demo_mode)

    async def handle(self, session, event):
        actor, action, value = event["actor_id"], event.get("action", ""), event.get("text", "")
        user = await actor_profile(session, actor)
        if event.get("media"):
            return reply("Пока я принимаю только текст. Напиши вопрос сообщением.")
        if user is None:
            if action != "consent":
                return reply(CONSENT, buttons([("Принимаю условия и передачу в ИИ", "consent")], menu=False))
            user = User(telegram_id=actor, chat_id=event["chat_id"], display_name="Гость", interests=["self_development"], timezone="UTC")
            session.add(user)
            await session.flush()
            set_state(user, "onboard_name")
            # Bind all retained events, including the current one, to cascade deletion.
            await bind_updates(session, actor, user.id)
            return reply("Как к тебе обращаться? Введи имя от 1 до 40 символов.")
        user.chat_id = event["chat_id"]
        state, payload = user.fsm_state, deepcopy(user.fsm_payload or {})
        onboarding = bool(state and state.startswith("onboard"))
        if action == "menu" or value in ("/start", "/cancel"):
            if onboarding:
                set_state(user, "onboard_name")
                return reply("Завершим профиль. Как к тебе обращаться? От 1 до 40 символов.")
            set_state(user, None)
            return reply(f"Привет, {user.display_name}. Что разберём сегодня?", MENU)
        if state == "expired" or (user.fsm_expires_at and user.fsm_expires_at < utcnow()):
            set_state(user, None if not onboarding else "onboard_name")
            raise AppError("SESSION_EXPIRED")
        if onboarding:
            return await self.onboarding(session, user, state, payload, value, action)
        if action.startswith("confirm:"):
            key = f"q:{user.id}:{action.split(':', 1)[1]}"
            existing = await by_request(session, Reading, user.id, key)
            if existing:
                return await deliver_record(session, user, existing, f"open:{event['update_id']}")
            if state != "confirm" or payload.get("token") != action.split(":", 1)[1]:
                raise AppError("SESSION_EXPIRED")
            data = CreateReading(request_key=key, **{k: v for k, v in payload.items() if k != "token"})
            result = require_data(await self.api.call(session, actor, "create_reading", data.model_dump(), str(event["update_id"])))
            record = await owned(session, Reading, user.id, result["reading_id"])
            set_state(user, None)
            return await deliver_record(session, user, record, f"open:{event['update_id']}")
        if action == "reading":
            set_state(user, "topic", {"token": uuid4().hex})
            return reply("Выбери тему расклада.", topics("topic"))
        if action.startswith("topic:") and state == "topic":
            topic = action.split(":", 1)[1]
            if topic not in TOPICS:
                raise AppError("VALIDATION_ERROR")
            payload["topic"] = topic
            set_state(user, "question", payload)
            return reply("Напиши вопрос от 1 до 1000 символов.")
        if value and state == "question":
            payload["question"] = normalize(value, CONFIG["input"]["question"])
            set_state(user, "spread", payload)
            return reply("Выбери расклад.", buttons([("1 карта", "spread:one"), ("3 карты", "spread:three")], [("Выбор А или Б", "spread:choice")]))
        if action.startswith("spread:") and state == "spread":
            payload["spread_type"] = action.split(":", 1)[1]
            if payload["spread_type"] not in ("one", "three", "choice"):
                raise AppError("VALIDATION_ERROR")
            if payload["spread_type"] == "choice":
                set_state(user, "option_a", payload)
                return reply("Назови вариант А: от 1 до 200 символов.")
            return self.confirm(user, payload)
        if value and state in ("option_a", "option_b"):
            payload[state] = normalize(value, CONFIG["input"]["option"])
            if state == "option_a":
                set_state(user, "option_b", payload)
                return reply("Назови вариант Б: от 1 до 200 символов.")
            return self.confirm(user, payload)
        if action == "day":
            result = require_data(await self.api.call(session, actor, "get_day_card", {}, str(event["update_id"])))
            record = await owned(session, Reading, user.id, result["reading_id"])
            return await deliver_record(session, user, record, f"open:{event['update_id']}")
        if action == "monthly" or action.startswith("month:"):
            result = require_data(await self.api.call(session, actor, "get_monthly_report", {"month": action[6:] if action.startswith("month:") else None}, str(event["update_id"])))
            record = await owned(session, MonthlyReport, user.id, result["report_id"])
            # Newly created short reports already have their initial delivery.
            if record.created_at < utcnow() and record.status in ("ready", "empty"):
                pending = await initial_delivery_pending(session, record.id)
                if pending:
                    return None
            return await deliver_record(session, user, record, f"open:{event['update_id']}")
        if action == "diary":
            return reply("Твой дневник.", buttons([("Новая запись", "note"), ("Открыть дневник", "diaries")]))
        if action.startswith("month_history:") or action.startswith(("mh_readings", "mh_diary")):
            if action.startswith("month_history:"):
                report = await owned(session, MonthlyReport, user.id, action.split(":", 1)[1])
                payload = {"report_id": str(report.id)}
                set_state(user, "monthly_history", payload)
                action = "mh_readings"
            elif state != "monthly_history":
                raise AppError("SESSION_EXPIRED")
            report = await owned(session, MonthlyReport, user.id, payload["report_id"])
            kind, _, cursor = action.partition(":")
            model = Reading if kind == "mh_readings" else DiaryEntry
            rows, next_cursor = await history(session, user.id, model, cursor or None, bounds=period(report.month, user.timezone))
            kb = [[(f"{r.created_at:%d.%m} · {(r.question or 'Карта дня')[:30] if model is Reading else r.text[:30]}", f"{'open' if model is Reading else 'entry'}:{r.id}")] for r in rows]
            if next_cursor:
                kb.append([("Далее", f"{kind}:{next_cursor}")])
            kb.append([("Расклады месяца", "mh_readings"), ("Дневник месяца", "mh_diary")])
            return reply(f"История за {report.month:%Y-%m}" + (". Записей нет." if not rows else ":"), buttons(*kb))
        if action == "note" or action.startswith("note:"):
            data = {"token": uuid4().hex}
            if action.startswith("note:"):
                record = await owned(session, Reading, user.id, action[5:])
                data["reading_id"] = str(record.id)
            set_state(user, "diary_text", data)
            return reply("Что хочется записать? От 1 до 2000 символов; переносы строк сохраняются.")
        if value and state == "diary_text":
            payload["text"] = normalize(value, CONFIG["input"]["diary"], diary=True)
            set_state(user, "diary_mood", payload)
            return reply("Какое настроение? Оценка необязательна.", buttons([(str(n), f"mood:{n}") for n in range(1, 6)], [("Пропустить оценку", "mood:skip")]))
        if action.startswith("mood:") and state == "diary_mood":
            choice = action.split(":", 1)[1]
            if choice not in ["skip", "1", "2", "3", "4", "5"]:
                raise AppError("VALIDATION_ERROR")
            payload["mood"] = None if choice == "skip" else int(choice)
            set_state(user, "diary_save", payload)
            return reply("Сохранить запись?", buttons([("Сохранить", "save:" + payload["token"]), ("Отмена", "menu")]))
        if action.startswith("save:"):
            key = f"d:{user.id}:{action[5:]}"
            entry = await by_request(session, DiaryEntry, user.id, key)
            if not entry:
                if state != "diary_save" or payload.get("token") != action[5:]:
                    raise AppError("SESSION_EXPIRED")
                result = require_data(await self.api.call(session, actor, "add_diary_entry", {"request_key": key, **{k: v for k, v in payload.items() if k != "token"}}, str(event["update_id"])))
                entry = await owned(session, DiaryEntry, user.id, result["entry_id"])
            set_state(user, None)
            return reply("Мысли сохранены.", buttons([("Открыть запись", f"entry:{entry.id}")]))
        if action in ("history", "diaries", "reports") or action.startswith(("history:", "diaries:", "reports:")):
            kind, _, cursor = action.partition(":")
            model = {"history": Reading, "diaries": DiaryEntry, "reports": MonthlyReport}[kind]
            rows, next_cursor = await history(session, user.id, model, cursor or None)
            if not rows:
                return reply("Здесь появятся твои расклады." if model is Reading else "Здесь пока нет записей.", buttons([("Сделать расклад", "reading"), ("Новая запись", "note")]))
            prefix = {Reading: "open", DiaryEntry: "entry", MonthlyReport: "report"}[model]
            kb = [[(f"{r.created_at:%d.%m.%Y} · {(r.question or 'Карта дня')[:32] if model is Reading else (r.text[:32] if model is DiaryEntry else r.month.strftime('%Y-%m'))}", f"{prefix}:{r.id}")] for r in rows]
            if next_cursor:
                kb.append([("Далее", f"{kind}:{next_cursor}")])
            if model is Reading:
                kb.append([("Отчёты", "reports"), ("Дневник", "diaries")])
            return reply("Твои записи:", buttons(*kb))
        if action.startswith(("open:", "entry:", "report:")):
            prefix, id = action.split(":", 1)
            model = {"open": Reading, "entry": DiaryEntry, "report": MonthlyReport}[prefix]
            record = await owned(session, model, user.id, id)
            if model is DiaryEntry:
                return reply(record.text + f"\n\nНастроение: {record.mood or 'не указано'}")
            return await deliver_record(session, user, record, f"open:{event['update_id']}")
        if action == "settings":
            return reply(f"Часовой пояс: {user.timezone}\nУтренние сообщения: {'включены' if user.reminders_enabled else 'выключены'}, {user.reminder_time:%H:%M}", buttons([("Выключить" if user.reminders_enabled else "Включить", "toggle"), ("Изменить время", "time")], [("Удалить мои данные", "delete")]))
        if action == "toggle":
            user.reminders_enabled = not user.reminders_enabled
            return reply("Утренние сообщения " + ("включены." if user.reminders_enabled else "выключены."))
        if action == "time":
            set_state(user, "reminder_time")
            return reply("Введи время ЧЧ:ММ в своём часовом поясе.")
        if value and state == "reminder_time":
            save_reminder_settings(user, user.reminders_enabled, value)
            set_state(user, None)
            return reply("Время сохранено.")
        if action == "delete":
            token = deletion_token(user)
            return reply("Удалить профиль, историю и дневник? Действие необратимо. Переписка в Telegram и данные у провайдера ИИ не удаляются. Старые резервные копии хранятся до 7 суток.", buttons([("Да, удалить", f"delete_yes:{token}"), ("Отмена", "menu")]))
        if action.startswith("delete_yes:"):
            await delete_profile(session, user, action.split(":", 1)[1])
            return reply("Твои данные удалены. Для нового профиля: /start.", buttons(menu=False))
        if action:
            raise AppError("SESSION_EXPIRED")
        return reply("Выбери действие в меню или отмени ввод командой /cancel.", MENU)

    def confirm(self, user, payload):
        set_state(user, "confirm", payload)
        text = f"Тема: {TOPICS[payload['topic']]}\nВопрос: {payload['question']}\nРасклад: {payload['spread_type']}"
        if "option_a" in payload:
            text += f"\nА: {payload['option_a']}\nБ: {payload['option_b']}"
        return reply(text, buttons([("Подтвердить", "confirm:" + payload["token"]), ("Отмена", "menu")]))

    async def onboarding(self, session, user, state, payload, value, action):
        if state == "onboard_name" and value:
            payload = {"display_name": normalize(value, CONFIG["input"]["name"]), "interests": []}
            set_state(user, "onboard_interests", payload)
            return reply("Выбери от 1 до 4 сфер интересов. Затем нажми «Готово».", self.interest_buttons(payload))
        if state == "onboard_interests" and action.startswith("interest:"):
            topic = action[9:]
            if topic not in TOPICS:
                raise AppError("VALIDATION_ERROR")
            selected = payload["interests"]
            selected.remove(topic) if topic in selected else selected.append(topic)
            set_state(user, state, payload)
            return reply("Выбрано: " + ", ".join(TOPICS[t] for t in selected), self.interest_buttons(payload))
        if state == "onboard_interests" and action == "interests_done":
            if not payload["interests"]:
                raise AppError("VALIDATION_ERROR", "Выбери хотя бы одну сферу интересов.")
            set_state(user, "onboard_zone", payload)
            return reply("Выбери свой часовой пояс.", buttons(*[[(z, f"zone:{i}")] for i, z in enumerate(CONFIG["timezones"])]))
        if state == "onboard_zone" and action.startswith("zone:"):
            try:
                index = int(action[5:])
                if index < 0:
                    raise ValueError()
                payload["timezone"] = CONFIG["timezones"][index]
            except (ValueError, IndexError):
                raise AppError("VALIDATION_ERROR") from None
            set_state(user, "onboard_confirm", payload)
            return reply(f"Имя: {payload['display_name']}\nСферы: {', '.join(TOPICS[t] for t in payload['interests'])}\nЧасовой пояс: {payload['timezone']}. В MVP изменить зону нельзя. Подтверждаешь?", buttons([("Начать", "onboard_done")]))
        if state == "onboard_confirm" and action == "onboard_done":
            await finish_onboarding(session, user, Onboarding(consent=True, **payload))
            return reply(f"Привет, {user.display_name}. Что разберём сегодня?", MENU)
        raise AppError("VALIDATION_ERROR", "Заверши текущий шаг профиля или начни заново: /start.")

    def interest_buttons(self, payload):
        return buttons(*[[(('✓ ' if k in payload['interests'] else '') + label, 'interest:' + k)] for k, label in TOPICS.items()], [("Готово", "interests_done")])
