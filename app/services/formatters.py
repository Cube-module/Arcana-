TOPICS = {"relationships": "Отношения", "work": "Работа", "study": "Учёба", "self_development": "Саморазвитие"}


def split_messages(value, maximum=3500):
    result, current = [], ""
    for paragraph in value.split("\n\n"):
        while len(paragraph) > maximum:
            if current:
                result.append(current)
                current = ""
            cut = paragraph.rfind(" ", 0, maximum + 1)
            cut = cut if cut > 0 else maximum
            result.append(paragraph[:cut])
            paragraph = paragraph[cut:].lstrip()
        if current and len(current) + len(paragraph) + 2 > maximum:
            result.append(current)
            current = ""
        current += ("\n\n" if current else "") + paragraph
    if current:
        result.append(current)
    return result


def reading_text(reading, cards):
    result = reading.result_json
    return "\n".join(f"{c['label']}: {c['name']}" for c in cards) + "\n\n" + result["interpretation"] + "\n\n" + result["reflection_question"]


def monthly_text(report):
    if report.status == "empty":
        return "За этот месяц пока нет раскладов и записей. Попробуй карту дня или дневник."
    s = report.statistics_json
    text = f"Зеркало месяца — {report.month:%Y-%m}\n\nРаскладов по вопросу: {s['question_count']}. Карт дня: {s['day_count']}. Записей: {s['diary_count']}."
    if s["topics"]:
        text += "\n\nТемы: " + "; ".join(f"{TOPICS.get(k, k)} — {n}" for k, n in s["topics"])
    if s["cards"]:
        text += "\n\nВыпадения карт: " + "; ".join(f"{k} — {n}" for k, n in s["cards"])
    moods = s["mood"]
    if any(x["count"] for x in moods):
        text += "\n\nНастроение (только твои оценки): " + "; ".join(f"{i + 1}-я половина: {x['count']} оценок, среднее {x['average'] if x['average'] is not None else '—'}" for i, x in enumerate(moods))
        if all(x["count"] >= 2 for x in moods):
            text += f". Разница средних: {moods[1]['average'] - moods[0]['average']:+.1f}."
        else:
            text += ". Недостаточно оценок для сравнения."
    if sum(s[k] for k in ("question_count", "day_count", "diary_count")) < 3:
        text += "\n\nПока мало наблюдений для выводов о закономерностях."
    if report.status == "fallback":
        text += "\n\nИИ временно недоступен. Это фактическая сводка без персонализации."
    result = report.result_json or {}
    for item in result.get("observations", []) + result.get("reflection_questions", []):
        text += "\n\n" + item
    return text[:6000]
