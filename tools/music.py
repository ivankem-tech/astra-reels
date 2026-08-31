"""Своя музыка для роликов: синтез, права наши целиком.

Зачем своя. Трендовый звук из приложения снимает права на ролик и мешает
платному продвижению. Бесплатные библиотеки вроде Pixabay помогают, но
часть треков зарегистрирована в YouTube Content ID — заявка прилетит
даже на бесплатный трек. Синтезированное таких вопросов не создаёт.

Чем мерили. Три скачанных трека разобраны по числам (tools/analyse):
        центр спектра   низ <120 Гц   воздух 4–11 кГц   пульс
  сухой     2747 Гц        12,5%          28,1%        ~100
  густой     695 Гц        43,5%           3,4%        ~150
  просторный 356 Гц         0,2%           0,2%        нет

Отсюда правило, которое раньше формулировалось на слух как «давит»:
**низа ниже 120 Гц держим не больше 5%.** Телефонный динамик его не
воспроизводит, а в наушниках он давит на грудь — то есть вреда больше,
чем пользы. Размах громкости у всех троих 5–6 дБ: музыка дышит, но
не делает волн, и это правильно для двадцатисекундного ролика.

Запуск:  python3 tools/music.py
"""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import numpy as np

SR = 44100
OUT = Path(__file__).resolve().parent.parent / "Астра ролики" / "музыка"


def _env(n: int, attack: float, decay: float) -> np.ndarray:
    """Мягкая атака и экспоненциальный спад — как у струны."""
    t = np.arange(n) / SR
    rise = np.minimum(1.0, t / max(attack, 1e-4))
    return rise * np.exp(-t / decay)


def piano(freq: float, seconds: float, gain: float = 1.0) -> np.ndarray:
    """Фортепианная нота — ближе к живому роялю, чем к синтезатору.

    Что отличает рояль от синтетики и что мы воспроизводим:
    - удар молоточка: короткий шумовой щелчок в самом начале ноты;
    - почти мгновенная атака (2 мс). Плавное нарастание звучит как оргАн;
    - обертоны слегка расстроены вверх из-за жёсткости струны (коэф. B);
    - амплитуда обертонов падает круто, а верхние гаснут быстрее нижних —
      поэтому высокие ноты не «звенят» электронно;
    - чем выше нота, тем короче звук и тем меньше слышно обертонов, как
      на настоящем рояле: верхние клавиши гаснут почти мгновенно, нижние
      тянутся долго.
    """
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    B = 0.0005                                    # жёсткость струны
    # Ниже нота — дольше звучит: спад примерно удваивается на октаву вниз.
    base_decay = 2.4 * (261.63 / max(freq, 1.0)) ** 0.55
    # У высоких нот слышно меньше обертонов.
    n_partials = int(np.clip(22000.0 / max(freq, 1.0), 6, 18))
    for k in range(1, n_partials + 1):
        f = freq * k * math.sqrt(1 + B * k * k)
        if f > 15000:
            break
        amp = 1.0 / (k ** 1.35)                    # крутой спад по обертонам
        dk = base_decay / (1 + 0.9 * k)            # верх гаснет быстрее низа
        phase = np.random.rand() * 0.2
        out += amp * np.sin(2 * math.pi * f * t + phase) * _env(n, 0.002, dk)
    # Молоточек: шумовой щелчок в первые ~10 мс. Без него атака звучит
    # как «дунули в трубу», а не «ударили по клавише».
    hlen = min(int(0.010 * SR), n)
    if hlen > 0:
        hnoise = np.random.randn(hlen)
        henv = np.exp(-np.arange(hlen) / (0.0025 * SR))
        out[:hlen] += hnoise * henv * 0.22
    return out * gain / 2.2


def bell(freq: float, seconds: float, gain: float = 1.0) -> np.ndarray:
    """Колокольчик: неполные обертоны, долгий спад."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for mult, amp in ((1.0, 1.0), (2.76, .5), (5.4, .25), (8.9, .12)):
        out += amp * np.sin(2 * math.pi * freq * mult * t) * _env(n, .002, 1.6 / mult)
    return out * gain / 2.0


def pad(freqs: list[float], seconds: float, gain: float = 1.0) -> np.ndarray:
    """Подложка: несколько расстроенных голосов, медленное дыхание."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for f in freqs:
        for detune in (-6.0, 0.0, 6.0):          # центы
            ff = f * 2 ** (detune / 1200)
            out += np.sin(2 * math.pi * ff * t + np.random.rand() * 6.283)
    out /= len(freqs) * 3
    breath = .72 + .28 * np.sin(2 * math.pi * t / 7.5)
    fade = np.minimum(1.0, t / 2.2) * np.minimum(1.0, (seconds - t) / 2.2)
    return out * breath * fade * gain


def air(seconds: float, gain: float = 1.0) -> np.ndarray:
    """Воздух: шум, из которого вырезан весь низ и середина.

    Не «ветер» и не «птицы» — обе попытки в прошлый раз вышли неудачно.
    Просто дыхание в верхнем регистре, чтобы кадр не звучал стерильно.
    """
    n = int(seconds * SR)
    noise = np.random.randn(n)
    spec = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1 / SR)
    mask = np.clip((freqs - 2500) / 3000, 0, 1) * np.exp(-freqs / 12000)
    shaped = np.fft.irfft(spec * mask, n)
    t = np.arange(n) / SR
    swell = .55 + .45 * np.sin(2 * math.pi * t / 5.3 + 1.1)
    return shaped / (np.max(np.abs(shaped)) + 1e-9) * swell * gain


def reverb(x: np.ndarray, mix: float = .35) -> np.ndarray:
    """Простое эхо несколькими отражениями — даёт объём без библиотек."""
    out = x.copy()
    for delay_ms, level in ((37, .5), (61, .38), (89, .3), (131, .22), (191, .15)):
        d = int(delay_ms * SR / 1000)
        tail = np.zeros_like(x)
        tail[d:] = x[:-d] * level
        out += tail * mix
    return out


def highpass(x: np.ndarray, cutoff: float = 110.0) -> np.ndarray:
    """Срез низа. Главный вывод из разбора чужих треков."""
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    spec *= np.clip((freqs - cutoff * .55) / (cutoff * .8), 0, 1)
    return np.fft.irfft(spec, len(x))


def loopable(x: np.ndarray, tail: float = 1.5) -> np.ndarray:
    """Сшивает конец с началом, чтобы повтор не щёлкал."""
    k = int(tail * SR)
    head, body = x[:k], x[k:]
    fade = np.linspace(0, 1, k)
    body[-k:] = body[-k:] * (1 - fade) + head * fade
    return body


def soften(x: np.ndarray, ratio: float = 3.0, attack: float = .02,
           release: float = .45) -> np.ndarray:
    """Мягкое сжатие: подтягивает тихие места к громким.

    Зачем. Первая сборка дала размах 9–15 дБ против 5–6 у образцов:
    редкие ноты с тишиной между ними. В двадцатисекундном ролике такие
    провалы читаются как «музыка кончилась», человек думает, что видео
    зависло. Ровное дыхание важнее выразительности — фон не должен
    привлекать к себе внимание ни громкостью, ни её отсутствием.
    """
    # огибающая с разной скоростью на подъём и спад
    env = np.abs(x)
    a = math.exp(-1 / (attack * SR))
    r = math.exp(-1 / (release * SR))
    follow = np.zeros_like(env)
    prev = 0.0
    for i in range(len(env)):
        coeff = a if env[i] > prev else r
        prev = coeff * prev + (1 - coeff) * env[i]
        follow[i] = prev
    level = np.maximum(follow, 1e-5)
    gain = (level / np.percentile(level, 85)) ** (1 / ratio - 1)
    return x * np.clip(gain, 0.3, 3.5)


CEILING = 0.89          # −1 дБ, запас до потолка


def limit(x: np.ndarray, ceiling: float = CEILING) -> np.ndarray:
    """Мягкий потолок вместо обрезки топором.

    `np.clip` рубит верхушку волны прямой линией, и это слышно как
    хрип. Пока пианино играло по одной ноте, пики были низкие и обрезка
    почти не включалась. Три ноты разом складываются, упираются в
    потолок — и версия с аккордами захрипела на первой же сборке.

    Гиперболический тангенс поджимает пики плавно: тихое проходит как
    есть, громкое мягко прижимается, изломов не возникает.
    """
    return ceiling * np.tanh(x / ceiling)


def normalise(x: np.ndarray, target_db: float = -20.0) -> np.ndarray:
    """Выставляет среднюю громкость и следит, чтобы пики не били в потолок.

    Средняя громкость важнее пиковой: по ней слышно, громко играет
    музыка или тихо. Но если после выравнивания пики всё равно выше
    потолка — опускаем всё целиком. Лучше играть чуть тише, чем хрипеть.
    """
    rms = np.sqrt(np.mean(x ** 2)) + 1e-9
    x = x * (10 ** (target_db / 20) / rms)
    peak = np.max(np.abs(x))
    if peak > CEILING:
        x = x * (CEILING / peak)
    return x


def save(x: np.ndarray, name: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    data = (x * 32767).astype(np.int16)
    stereo = np.repeat(data[:, None], 2, axis=1).ravel()
    with wave.open(str(path), "w") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())
    return path


# ---------- три варианта ----------
#
# Ноты — ля-минорная пентатоника: ничего не заимствовано, просто самый
# спокойный из ладов, который не тянет ни в грусть, ни в бодрость.

def note(semitones: float) -> float:
    return 220.0 * 2 ** (semitones / 12)


LEN = 26.0


# Мелодия «Рассвета»: двенадцать нот ля-минорной пентатоники,
# по одной каждые две секунды. Нота звучит до 3,2 секунды, поэтому
# хвост предыдущей ещё тянется, когда бьёт следующая, — от этого
# кажется, что нот больше, чем есть.
#
# Устройство: пять нот вверх лесенкой, откат, прыжок через большую
# сексту — единственный резкий шаг во всей линии, он и запоминается, —
# и спуск обратно. Из пяти нот пентатоники звучат четыре: Ре не занято.
#
# Аккорды пробовали и отказались: плотнее, но теснее. Одна нота
# оставляет воздух, а воздух здесь и есть смысл.
MELODY = [0, 3, 7, 10, 12, 7, 3, 12, 15, 12, 7, 3]

# Сила удара по клавише. Подобрана замером, а не расчётом — и это
# важно помнить. Однажды правку «пианино на десятую тише» применили
# арифметически (0.55 → 0.495) и одновременно поменяли одноголосие
# на аккорды. Три ноты вместе громче одной примерно на пять децибел,
# правки погасили друг друга, и соотношение не сдвинулось ни на волос.
#
# Поэтому силу подбираем так: синтезируем пианино и подложку порознь,
# сравниваем их среднюю громкость и ищем значение, при котором разрыв
# равен нужному. Путь от начала: 0.55 (разрыв 6,0 дБ) → 0.435 аккордами
# (4,2 дБ) → 0.40 одной нотой (2,4 дБ) → 0.395: новый тембр
# пианино сам по себе тише старого, поэтому число почти не изменилось,
# а на слух пианино стало ровно на ~10% (0,9 дБ) тише прежнего — разрыв
# с подложкой 1,5 дБ вместо 2,4.
PIANO_GAIN = 0.395

# Подложка. Тоже подобрана на слух Иваном, на десятую громче исходной.
PAD_GAIN = 0.286


def dawn() -> np.ndarray:
    """«Рассвет»: фортепиано по одной ноте и подложка. Ни дрона, ни птиц."""
    n = int(LEN * SR)
    out = np.zeros(n)
    for i, semitone in enumerate(MELODY):
        start = int((0.8 + i * 2.0) * SR)
        if start >= n:
            break
        piece = piano(note(semitone + 12), min(3.2, (n - start) / SR), PIANO_GAIN)
        out[start:start + len(piece)] += piece
    out += pad([note(0), note(7), note(12)], LEN, PAD_GAIN)
    return out


def desert() -> np.ndarray:
    """«Сухой воздух»: светлее и просторнее, с колокольчиками.

    Целимся в профиль первого трека: высокий центр тяжести и заметный
    воздух наверху. Пульс задаёт не барабан, а частота нот.
    """
    n = int(LEN * SR)
    out = np.zeros(n)
    steps = [12, 15, 19, 15, 12, 19, 22, 19, 15, 12, 19, 15, 12, 15]
    for i, s in enumerate(steps):
        start = int((0.4 + i * 1.75) * SR)
        if start >= n:
            break
        piece = bell(note(s + 12), min(2.8, (n - start) / SR), .38)
        out[start:start + len(piece)] += piece
    out += pad([note(0), note(7)], LEN, .18)
    out += air(LEN, .05)
    return out


def still() -> np.ndarray:
    """«Просторно»: одна середина, без низа и без пульса.

    Профиль третьего трека: 96% энергии в середине, ритма нет.
    Самый ненавязчивый — под ролики, где много текста.
    """
    n = int(LEN * SR)
    out = pad([note(0), note(3), note(7), note(12)], LEN, .30)
    for i, s in enumerate([12, 7, 15, 10]):
        start = int((2.5 + i * 6.0) * SR)
        if start >= n:
            break
        piece = piano(note(s + 12), min(4.0, (n - start) / SR), .22)
        out[start:start + len(piece)] += piece
    return out


VARIANTS = {"рассвет": dawn, "сухой_воздух": desert, "просторно": still}


def build() -> None:
    np.random.seed(7)                    # одинаковый результат при пересборке
    for name, make in VARIANTS.items():
        x = make()
        x = reverb(x, .30)
        x = highpass(x, 115.0)
        x = soften(x)
        x = limit(x)
        x = loopable(x, 1.5)
        x = normalise(x, -20.0)
        path = save(x, f"астра_{name}.wav")
        rms = 20 * math.log10(np.sqrt(np.mean(x ** 2)) + 1e-9)
        peak = 20 * math.log10(np.max(np.abs(x)) + 1e-9)
        print(f"{path.name}: {len(x)/SR:.1f} с, "
              f"средняя {rms:.1f} дБ, пик {peak:.1f} дБ")


if __name__ == "__main__":
    build()
