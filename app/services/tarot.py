"""Колода Таро на 78 карт и расклады.

Карты тянутся честным случайным выбором без повторов внутри расклада.
Положение (прямое/перевёрнутое) — с вероятностью 30% перевёрнутое.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# ---------- Старшие арканы ----------
MAJORS: list[str] = [
    "Шут",
    "Маг",
    "Верховная Жрица",
    "Императрица",
    "Император",
    "Иерофант",
    "Влюблённые",
    "Колесница",
    "Сила",
    "Отшельник",
    "Колесо Фортуны",
    "Справедливость",
    "Повешенный",
    "Смерть",
    "Умеренность",
    "Дьявол",
    "Башня",
    "Звезда",
    "Луна",
    "Солнце",
    "Суд",
    "Мир",
]

# ---------- Младшие арканы ----------
SUITS: list[tuple[str, str]] = [
    ("Жезлов", "🔥"),
    ("Кубков", "💧"),
    ("Мечей", "🌬"),
    ("Пентаклей", "🪨"),
]

RANKS: list[str] = [
    "Ас",
    "Двойка",
    "Тройка",
    "Четвёрка",
    "Пятёрка",
    "Шестёрка",
    "Семёрка",
    "Восьмёрка",
    "Девятка",
    "Десятка",
    "Паж",
    "Рыцарь",
    "Королева",
    "Король",
]

REVERSED_CHANCE = 0.3


@dataclass(frozen=True)
class Card:
    name: str
    is_major: bool
    suit: str | None = None

    def title(self, reversed_: bool) -> str:
        suffix = " (перевёрнутая)" if reversed_ else ""
        return f"{self.name}{suffix}"


def build_deck() -> list[Card]:
    deck = [Card(name=name, is_major=True) for name in MAJORS]
    for suit, _ in SUITS:
        for rank in RANKS:
            deck.append(Card(name=f"{rank} {suit}", is_major=False, suit=suit))
    return deck


DECK: list[Card] = build_deck()
assert len(DECK) == 78, f"в колоде должно быть 78 карт, а не {len(DECK)}"


@dataclass
class DrawnCard:
    position: str
    card: Card
    reversed: bool

    @property
    def label(self) -> str:
        return self.card.title(self.reversed)


@dataclass(frozen=True)
class Spread:
    key: str
    name: str
    positions: list[str]
    label: str                      # надпись на кнопке
    premium: bool = False
    needs_question: bool = False

    @property
    def size(self) -> int:
        return len(self.positions)

    @property
    def words_per_card(self) -> int:
        """Чем больше карт, тем короче трактовка каждой — иначе не влезем."""
        if self.size >= 10:
            return 45
        if self.size >= 7:
            return 65
        return 90


WEEKDAYS = [
    "Понедельник", "Вторник", "Среда", "Четверг",
    "Пятница", "Суббота", "Воскресенье",
]

MONTHS = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


SPREADS: dict[str, Spread] = {
    # ---------- бесплатные ----------
    "day": Spread(
        key="day",
        name="Карта дня",
        label="🌗 Карта дня",
        positions=["Карта дня"],
    ),
    "three": Spread(
        key="three",
        name="Прошлое — Настоящее — Будущее",
        label="🕰 Прошлое · Настоящее · Будущее",
        positions=["Прошлое", "Настоящее", "Будущее"],
    ),
    "yesno": Spread(
        key="yesno",
        name="Да или нет",
        label="⚖️ Да или нет",
        positions=["За", "Против", "Итог"],
        needs_question=True,
    ),
    "love": Spread(
        key="love",
        name="Отношения",
        label="💗 Отношения",
        positions=[
            "Ты в этих отношениях",
            "Партнёр в этих отношениях",
            "Что вас связывает",
            "Что мешает",
            "К чему идёт",
        ],
    ),
    "situation": Spread(
        key="situation",
        name="Разбор ситуации",
        label="🔍 Разбор ситуации",
        positions=[
            "Суть ситуации",
            "Что скрыто от тебя",
            "Главное препятствие",
            "Что поможет",
            "Вероятный итог",
        ],
        needs_question=True,
    ),

    # ---------- для подписчиков ----------
    "week": Spread(
        key="week",
        name="Расклад на неделю",
        label="📅 Неделя по дням",
        positions=list(WEEKDAYS),
        premium=True,
    ),
    "horseshoe": Spread(
        key="horseshoe",
        name="Подкова",
        label="🧲 Подкова",
        positions=[
            "Прошлое ситуации",
            "Настоящее",
            "Скрытые влияния",
            "Твоя позиция",
            "Окружение и люди",
            "Что стоит сделать",
            "Итог",
        ],
        premium=True,
        needs_question=True,
    ),
    "choice": Spread(
        key="choice",
        name="Выбор из двух",
        label="🔀 Выбор из двух",
        positions=[
            "Первый вариант — суть",
            "Первый вариант — что даст",
            "Первый вариант — чем обернётся",
            "Второй вариант — суть",
            "Второй вариант — что даст",
            "Второй вариант — чем обернётся",
            "Совет: что выбрать",
        ],
        premium=True,
        needs_question=True,
    ),
    "love_deep": Spread(
        key="love_deep",
        name="Отношения в глубину",
        label="💞 Отношения в глубину",
        positions=[
            "Твои чувства",
            "Твои мысли",
            "Твои страхи",
            "Чувства партнёра",
            "Мысли партнёра",
            "Страхи партнёра",
            "Что вас связывает",
            "Что мешает",
            "К чему всё идёт",
        ],
        premium=True,
    ),
    "cross": Spread(
        key="cross",
        name="Кельтский крест",
        label="✨ Кельтский крест",
        positions=[
            "Текущая ситуация",
            "Что мешает или помогает",
            "Осознанная цель",
            "Корень, основа",
            "Уходящее прошлое",
            "Ближайшее будущее",
            "Ты сам в ситуации",
            "Окружение и люди",
            "Надежды и страхи",
            "Итог",
        ],
        premium=True,
        needs_question=True,
    ),
    "year": Spread(
        key="year",
        name="Расклад на год",
        label="🗓 Расклад на год",
        positions=list(MONTHS) + ["Главная тема года"],
        premium=True,
    ),
}


def draw(spread: Spread, *, seed: int | None = None) -> list[DrawnCard]:
    """Тянет карты для расклада. seed нужен, чтобы карта дня была одна на сутки."""
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    picked = rng.sample(DECK, spread.size)
    return [
        DrawnCard(
            position=position,
            card=card,
            reversed=rng.random() < REVERSED_CHANCE,
        )
        for position, card in zip(spread.positions, picked)
    ]


def cards_preview(cards: list[DrawnCard], numbered: bool = False) -> str:
    """Список карт для показа пользователю до трактовки.

    numbered=True — с номерами, они совпадают с кружками на картинке расклада.
    """
    lines = []
    for index, item in enumerate(cards, 1):
        prefix = f"{index}. " if numbered else ("🔄 " if item.reversed else "▫️ ")
        turned = " 🔄" if item.reversed and numbered else ""
        lines.append(f"{prefix}<b>{item.position}</b> — {item.card.name}{turned}")
    return "\n".join(lines)
