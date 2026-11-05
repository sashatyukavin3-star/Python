#!/usr/bin/env python3
"""Генератор контент-плана для Telegram-канала парфюмерного бренда.

Примеры:
    python3 content_plan.py
    python3 content_plan.py --start 2026-11-09 --days 30 --posts-per-week 5 --out plan
    python3 content_plan.py --start 2027-01-05 --days 60 --posts-per-week 3

На выходе в папке --out: content_plan.csv и content_plan.md.
Рубрики подобраны так, чтобы продающих постов было не больше ~25%.
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


@dataclass(frozen=True)
class Rubric:
    name: str
    goal: str
    formats: tuple[str, ...]
    ideas: tuple[str, ...]
    cta: str
    prompt_ref: str


RUBRICS: dict[str, Rubric] = {
    "Разбор аромата": Rubric(
        name="Разбор аромата",
        goal="охват + доверие",
        formats=("фото флакона", "макро крышки", "фото на коже"),
        ideas=(
            "как раскрывается в первые 10 минут",
            "один аромат в трёх ситуациях: утро, работа, вечер",
            "честно: кому этот аромат не подойдёт",
            "сравнение двух наших ароматов одного семейства",
            "что слышат люди вокруг, когда вы в нём",
        ),
        cta="«Возьмите пробник, если сомневаетесь»",
        prompt_ref="3.2",
    ),
    "Польза": Rubric(
        name="Польза",
        goal="сохранения + репосты",
        formats=("текст", "карусель", "короткий ролик"),
        ideas=(
            "5 ошибок при нанесении парфюма",
            "почему один аромат на разных людях звучит по-разному",
            "как хранить, чтобы не испортился за год",
            "как выбрать аромат в подарок, не зная вкуса",
            "чем отличается концентрация: туалетная вода, парфюмерная, духи",
        ),
        cta="«Сохраните, чтобы не потерять»",
        prompt_ref="3.1 / 4.3",
    ),
    "Вовлечение": Rubric(
        name="Вовлечение",
        goal="комментарии + данные о клиенте",
        formats=("опрос", "квиз «угадай ноту»", "пост-вопрос"),
        ideas=(
            "запах из детства, которого больше нет",
            "выбери один из двух ароматов на вечер",
            "угадай ноту по трём описаниям",
            "что для вас хуже: сладкое или горькое",
            "покажите свой флакон на полке",
        ),
        cta="«Ответьте одним словом в комментариях»",
        prompt_ref="3.6",
    ),
    "За кадром": Rubric(
        name="За кадром",
        goal="доверие к продукту",
        formats=("фото процесса", "кружок", "видео 15 сек"),
        ideas=(
            "почему партия делается три недели",
            "как выбираем сырьё и что забраковали в этом месяце",
            "руки в работе: разлив и упаковка",
            "одна ошибка в композиции, которую пришлось переделывать",
            "что лежит у нас на столе прямо сейчас",
        ),
        cta="«Спросите, что интересно — отвечу»",
        prompt_ref="3.3",
    ),
    "Продажа/оффер": Rubric(
        name="Продажа/оффер",
        goal="заказы",
        formats=("фото + текст", "подборка", "сторис-анонс"),
        ideas=(
            "новинка: что, сколько, для кого, цена",
            "набор пробников, чтобы выбрать свой",
            "последние флаконы партии — честный дефицит",
            "подарочный комплект к празднику",
            "сет «флакон + дорожный атомайзер»",
        ),
        cta="«Напишите в личку — подскажу, подойдёт ли»",
        prompt_ref="2.3 / 3.3",
    ),
    "Отзыв/UGC": Rubric(
        name="Отзыв/UGC",
        goal="социальное доказательство",
        formats=("скрин отзыва", "фото клиента", "цитата"),
        ideas=(
            "отзыв, в котором человек узнал свой аромат по описанию",
            "клиентская фотография флакона",
            "цитата из переписки: «думала, будет сладко, а оказалось…»",
            "история повторной покупки",
            "подборка трёх отзывов об одном аромате",
        ),
        cta="«Расскажите, как раскрылся у вас»",
        prompt_ref="5.4",
    ),
    "Сезон/подборка": Rubric(
        name="Сезон/подборка",
        goal="навигация по ассортименту",
        formats=("подборка", "карусель", "мудборд"),
        ideas=(
            "3 аромата под текущую погоду",
            "что взять в поездку на 3 дня",
            "аромат месяца: почему именно он",
            "подборка под настрой: тихое / громкое",
            "один аромат — три разных человека",
        ),
        cta="«Скажите свой город и погоду — подберу»",
        prompt_ref="3.7",
    ),
}

# Порядок ротации: 7 слотов, из которых берём первые N постов недели.
CYCLE = [
    "Разбор аромата",
    "Польза",
    "Вовлечение",
    "За кадром",
    "Продажа/оффер",
    "Отзыв/UGC",
    "Сезон/подборка",
]


def week_weekdays(count: int) -> list[int]:
    """Равномерно распределяет count постов по 7 дням недели."""
    if count >= 7:
        return list(range(7))
    if count == 1:
        return [0]
    return sorted({round(i * 6 / (count - 1)) for i in range(count)})


def build_plan(start: date, days: int, per_week: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    weeks = (days + 6) // 7
    offsets = {name: 0 for name in RUBRICS}
    days_slots = week_weekdays(per_week)
    rows: list[dict] = []

    for w in range(weeks):
        week_start = start + timedelta(days=7 * w)
        order = [CYCLE[(i + 2 * w) % len(CYCLE)] for i in range(per_week)]
        if per_week < 7:
            # не даём одной рубрике повториться внутри недели
            seen: set[str] = set()
            for i, name in enumerate(order):
                if name in seen:
                    for cand in CYCLE:
                        if cand not in seen:
                            order[i] = cand
                            break
                seen.add(order[i])
        for i, name in enumerate(order):
            day = week_start + timedelta(days=days_slots[i % len(days_slots)])
            if (day - start).days >= days:
                continue
            r = RUBRICS[name]
            idea = r.ideas[offsets[name] % len(r.ideas)]
            offsets[name] += 1
            rows.append(
                {
                    "date": day,
                    "week": w + 1,
                    "rubric": r.name,
                    "goal": r.goal,
                    "format": rng.choice(r.formats),
                    "idea": idea,
                    "cta": r.cta,
                    "prompt": r.prompt_ref,
                }
            )

    rows.sort(key=lambda row: row["date"])
    return rows


def to_csv(rows: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(
            ["Дата", "День", "Неделя", "Рубрика", "Цель", "Формат", "Идея поста", "Призыв к действию", "Промпт в prompts_RU.md"]
        )
        for row in rows:
            writer.writerow(
                [
                    row["date"].strftime("%d.%m.%Y"),
                    WEEKDAYS[row["date"].weekday()],
                    row["week"],
                    row["rubric"],
                    row["goal"],
                    row["format"],
                    row["idea"],
                    row["cta"],
                    f"№{row['prompt']}",
                ]
            )


def to_markdown(rows: list[dict], path: Path, start: date, days: int, per_week: int) -> None:
    end = start + timedelta(days=days - 1)
    selling = sum(1 for r in rows if r["rubric"] == "Продажа/оффер")
    share = 100 * selling / len(rows) if rows else 0
    lines = [
        "# Контент-план Telegram-канала",
        "",
        f"Период: **{start.strftime('%d.%m.%Y')} — {end.strftime('%d.%m.%Y')}** · "
        f"постов: **{len(rows)}** · частота: **{per_week}/неделю** · "
        f"продающих: {selling} ({share:.0f}%)",
        "",
        "Рубрики чередуются так, чтобы одна не повторялась два дня подряд, "
        "а продающих постов было не больше четверти. Формат — черновик: "
        "берёте идею и прогоняете через промпт из `prompts_RU.md`.",
        "",
    ]
    current_week = None
    for row in rows:
        if row["week"] != current_week:
            current_week = row["week"]
            lines += ["", f"## Неделя {current_week}", "",
                      "| Дата | День | Рубрика | Формат | Идея поста | CTA | Промпт |",
                      "|---|---|---|---|---|---|---|"]
        lines.append(
            "| {d} | {wd} | {rubric} | {fmt} | {idea} | {cta} | №{prompt} |".format(
                d=row["date"].strftime("%d.%m"),
                wd=WEEKDAYS[row["date"].weekday()],
                rubric=row["rubric"],
                fmt=row["format"],
                idea=row["idea"],
                cta=row["cta"],
                prompt=row["prompt"],
            )
        )
    lines += [
        "",
        "## Как этим пользоваться",
        "",
        "1. Уберите 1–2 поста, которые не подходят под месяц, и поставьте свои поводы (пополнение, эфир, коллаборация).",
        "2. На каждый пост берите промпт из таблицы и подставляйте бриф бренда.",
        "3. Пишите сразу пачкой на неделю вперёд — так канал не умирает в загруженный день.",
        "4. Раз в две недели смотрите: какие посты дали комментарии и заказы, а какие просто «висели».",
        "5. Опросы и квизы не удаляйте — это данные о клиенте, а не просто развлечение.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Контент-план для Telegram-канала парфюмерного бренда")
    parser.add_argument("--start", default=None, help="дата старта ГГГГ-ММ-ДД (по умолчанию ближайший понедельник)")
    parser.add_argument("--days", type=int, default=30, help="длина плана в днях (по умолчанию 30)")
    parser.add_argument("--posts-per-week", type=int, default=5, help="постов в неделю, 1–7 (по умолчанию 5)")
    parser.add_argument("--out", default="plan", help="папка для файлов (по умолчанию plan)")
    parser.add_argument("--seed", type=int, default=11, help="seed для выбора форматов")
    args = parser.parse_args()

    if not 1 <= args.posts_per_week <= 7:
        parser.error("--posts-per-week должен быть от 1 до 7")
    if args.days < 1:
        parser.error("--days должен быть положительным")

    if args.start:
        start = date.fromisoformat(args.start)
    else:
        today = date.today()
        start = today + timedelta(days=(7 - today.weekday()) % 7 or 7)

    rows = build_plan(start, args.days, args.posts_per_week, args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    to_csv(rows, out / "content_plan.csv")
    to_markdown(rows, out / "content_plan.md", start, args.days, args.posts_per_week)
    print(f"Готово: {out/'content_plan.md'} и {out/'content_plan.csv'} ({len(rows)} постов)")


if __name__ == "__main__":
    main()
