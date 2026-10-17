from app.services.formatters import TOPICS


def buttons(*rows, menu=True):
    result = [[{"text": label, "callback_data": action} for label, action in row] for row in rows]
    if menu:
        result.append([{"text": "В меню", "callback_data": "menu"}])
    return {"inline_keyboard": result}


MENU = buttons([("Сделать расклад", "reading"), ("Карта дня", "day")], [("Дневник", "diary"), ("История", "history")], [("Зеркало месяца", "monthly"), ("Настройки", "settings")], menu=False)


def topics(prefix):
    return buttons(*[[(label, f"{prefix}:{key}")] for key, label in TOPICS.items()])
