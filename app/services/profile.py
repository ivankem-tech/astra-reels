"""Сборка данных пользователя в текст для промпта и вспомогательные парсеры."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from dateutil.relativedelta import relativedelta

from app.services import zodiac

MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

WEEKDAYS_RU = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]


def parse_date(text: str) -> dt.date | None:
    """Понимает 01.01.1990, 1.1.1990, 01/01/1990, 01-01-1990, 1990-01-01."""
    text = text.strip()

    iso = re.fullmatch(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", text)
    if iso:
        year, month, day = (int(g) for g in iso.groups())
    else:
        match = re.fullmatch(r"(\d{1,2})[.\-/\s](\d{1,2})[.\-/\s](\d{4})", text)
        if not match:
            return None
        day, month, year = (int(g) for g in match.groups())

    try:
        date = dt.date(year, month, day)
    except ValueError:
        return None

    today = dt.date.today()
    if date > today or date.year < 1900:
        return None
    return date


def parse_time(text: str) -> dt.time | None:
    """Понимает 14:30, 14.30, 14 30, 9:05."""
    match = re.fullmatch(r"(\d{1,2})[:.\s-](\d{2})", text.strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return dt.time(hour, minute)


def offset_from_local_time(local: dt.time) -> int:
    """Вычисляет часовой пояс по тому, который сейчас час у человека.

    Спрашивать «выбери свой пояс» неудобно: не все знают свой UTC-сдвиг.
    А «сколько у тебя на часах» отвечает кто угодно. Сравниваем с UTC
    и округляем до целого часа.

    Оговорка: по одним часам пояса за краем ±12 неразличимы — UTC+13 и UTC-11
    показывают одно и то же время. Выбираем положительный вариант: русскоязычная
    аудитория живёт в UTC+2…+12, и для неё это всегда верно. Кто окажется
    в Полинезии — поправит пояс в профиле.
    """
    now = dt.datetime.now(dt.timezone.utc)
    minutes_local = local.hour * 60 + local.minute
    minutes_utc = now.hour * 60 + now.minute

    diff = minutes_local - minutes_utc
    # Через полночь разница уезжает на сутки — возвращаем в диапазон поясов.
    if diff > 14 * 60:
        diff -= 24 * 60
    elif diff < -12 * 60:
        diff += 24 * 60

    offset = round(diff / 60)
    return max(-12, min(14, offset))


def format_offset(offset: int) -> str:
    return f"UTC{offset:+d}"


def format_date_ru(date: dt.date) -> str:
    return f"{date.day} {MONTHS_RU[date.month - 1]} {date.year}"


def format_today_ru(date: dt.date | None = None) -> str:
    date = date or dt.date.today()
    return f"{date.day} {MONTHS_RU[date.month - 1]}, {WEEKDAYS_RU[date.weekday()]}"


def format_week_ru(start: dt.date) -> str:
    end = start + dt.timedelta(days=6)
    if start.month == end.month:
        return f"{start.day}–{end.day} {MONTHS_RU[start.month - 1]}"
    return (
        f"{start.day} {MONTHS_RU[start.month - 1]} — "
        f"{end.day} {MONTHS_RU[end.month - 1]}"
    )


# ---------------------------------------------------------------------------
# Как обращаться к человеку
#
# В анкете просим ФИО, а в русском формате это «Фамилия Имя Отчество» —
# то есть первое слово как раз НЕ имя. Разбираем по нескольким признакам.
# ---------------------------------------------------------------------------

_PATRONYMIC_ENDINGS = ("ич", "овна", "евна", "ична", "инична", "кызы", "оглы")

_SURNAME_ENDINGS = (
    "ов", "ев", "ёв", "ин", "ын", "ский", "цкий", "ой", "ых", "их",
    "ова", "ева", "ёва", "ина", "ына", "ская", "цкая",
    "ко", "ук", "юк", "ян", "швили", "дзе", "енко", "чук",
)


def _is_patronymic(word: str) -> bool:
    return word.lower().endswith(_PATRONYMIC_ENDINGS)


def _looks_like_surname(word: str) -> bool:
    return word.lower().endswith(_SURNAME_ENDINGS)


def _pretty(word: str) -> str:
    """иван → Иван, ИВАН → Иван, а «МакДональд» оставляем как есть."""
    return word.capitalize() if word.isupper() or word.islower() else word


def short_name(full_name: str | None, telegram_name: str | None = None) -> str:
    """Достаёт из ФИО то имя, которым уместно обращаться."""
    words = (full_name or "").split()

    if not words:
        return _pretty(telegram_name.split()[0]) if telegram_name else "друг"
    if len(words) == 1:
        return _pretty(words[0])

    # 1) Самый надёжный признак: имя из профиля Telegram встречается в ФИО.
    if telegram_name:
        wanted = telegram_name.strip().lower()
        for word in words:
            if word.lower() == wanted:
                return _pretty(word)

    # 2) Нашли отчество — имя стоит прямо перед ним.
    for index, word in enumerate(words):
        if index > 0 and _is_patronymic(word):
            return _pretty(words[index - 1])

    # 3) Три слова без явного отчества — считаем, что это Фамилия Имя Отчество.
    if len(words) >= 3:
        return _pretty(words[1])

    # 4) Два слова: если первое похоже на фамилию, а второе нет — имя второе.
    if _looks_like_surname(words[0]) and not _looks_like_surname(words[1]):
        return _pretty(words[1])

    return _pretty(words[0])


# Служебные слова внутри названий: «Ростов-на-Дону», «Комсомольск-на-Амуре».
_CITY_SMALL_WORDS = {"на", "в", "у", "при", "под", "за", "им", "и", "де", "ла", "дель"}


def pretty_city(city: str) -> str:
    """«алма-ата» → «Алма-Ата», «РОСТОВ-НА-ДОНУ» → «Ростов-на-Дону».

    Если человек уже написал со смешанным регистром — не трогаем,
    он мог знать лучше нас.
    """
    city = city.strip()
    if not city or not (city.islower() or city.isupper()):
        return city

    def fix(chunk: str, first: bool) -> str:
        low = chunk.lower()
        if not first and low in _CITY_SMALL_WORDS:
            return low
        return low.capitalize()

    words = []
    for word_index, word in enumerate(city.split()):
        parts = word.split("-")
        words.append(
            "-".join(
                fix(part, first=(word_index == 0 and part_index == 0))
                for part_index, part in enumerate(parts)
            )
        )
    return " ".join(words)


def name_candidates(full_name: str) -> list[str]:
    """Слова ФИО, из которых можно выбрать имя для обращения."""
    seen: list[str] = []
    for word in full_name.split():
        pretty = _pretty(word)
        if len(pretty) >= 2 and pretty not in seen:
            seen.append(pretty)
    return seen[:4]


def name_is_certain(full_name: str, telegram_name: str | None = None) -> bool:
    """Понятно ли без вопросов, каким словом называть человека.

    Уверены, когда: слово одно, либо имя совпало с профилем Telegram,
    либо нашлось отчество — тогда имя стоит прямо перед ним.
    Во всех остальных случаях лучше спросить, чем угадать.
    """
    words = (full_name or "").split()
    if len(words) <= 1:
        return True

    if telegram_name:
        wanted = telegram_name.strip().lower()
        if any(word.lower() == wanted for word in words):
            return True

    return any(_is_patronymic(word) for word in words[1:])


def display_name(user: dict[str, Any]) -> str:
    """Имя для обращения. Выбор человека важнее любых догадок."""
    chosen = (user.get("call_name") or "").strip()
    if chosen:
        return chosen
    return short_name(user.get("full_name"), user.get("first_name"))


def birth_date(user: dict[str, Any]) -> dt.date | None:
    raw = user.get("birth_date")
    if not raw:
        return None
    if isinstance(raw, dt.date):
        return raw
    try:
        return dt.date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def birth_time(user: dict[str, Any]) -> dt.time | None:
    raw = user.get("birth_time")
    if not raw:
        return None
    if isinstance(raw, dt.time):
        return raw
    try:
        return dt.time.fromisoformat(str(raw)[:8])
    except ValueError:
        return None


# Сервис заявлен как 18+, и это записано в условиях использования.
MIN_AGE = 18


def age_for(date: dt.date) -> int:
    """Полных лет на сегодня."""
    return relativedelta(dt.date.today(), date).years


def age(user: dict[str, Any]) -> int | None:
    date = birth_date(user)
    if not date:
        return None
    return age_for(date)


def is_complete(user: dict[str, Any] | None) -> bool:
    """Анкета заполнена настолько, что можно делать разборы."""
    return bool(user and user.get("birth_date"))


def as_prompt(user: dict[str, Any]) -> str:
    """Данные пользователя в виде текста для промпта."""
    lines: list[str] = []

    # Модели важно знать оба: обращаться по имени, но видеть полное ФИО —
    # по нему считаются нумерологические числа.
    lines.append(f"Обращаться по имени: {display_name(user)}")
    full = user.get("full_name")
    if full and len(full.split()) > 1:
        lines.append(f"Полное имя: {full}")

    date = birth_date(user)
    if date:
        lines.append(f"Дата рождения: {format_date_ru(date)}")
        years = age(user)
        if years is not None:
            lines.append(f"Возраст: {years}")

    time_value = birth_time(user)
    lines.append(
        f"Время рождения: {time_value.strftime('%H:%M')}"
        if time_value
        else "Время рождения: не указано"
    )

    city = user.get("birth_city")
    lines.append(f"Место рождения: {city}" if city else "Место рождения: не указано")

    sign = user.get("zodiac")
    if sign:
        lines.append(f"Стихия знака: {zodiac.element_for(sign)}")

    return "\n".join(lines) if lines else "Данных нет."
