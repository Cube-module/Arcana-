import re
from app.errors import AppError

# Deliberately small local guard, supplemented by the system prompt and output checks.
CRISIS = re.compile(r"(хочу|собираюсь|планирую)\s+(убить себя|покончить с собой|убить его|убить её|убить человека)|суицид|самоубийств|порезать вены|прыгнуть с крыши", re.I)
HIGH_STAKES = re.compile(r"диагноз|лечени|лекарств|таблетк|инвестиц|акци[июй]|кредит|судебн|юридическ", re.I)
DANGEROUS = re.compile(r"(прекрати|отмени)\s+лечени|не обращайся к врачу|гарантированн.{0,20}(прибыль|вылеч)|ты (точно|обязательно) умр[её]шь|убей себя", re.I)


def guard_question(question):
    if CRISIS.search(question):
        raise AppError("SAFETY_RESTRICTED")
    if HIGH_STAKES.search(question):
        raise AppError("SAFETY_RESTRICTED", "Карты не дают советов по лечению, диагнозам, сделкам или юридическим решениям. Переформулируй вопрос о своих чувствах и доступной поддержке.")


def valid_output(text):
    return not DANGEROUS.search(text) and not CRISIS.search(text)
