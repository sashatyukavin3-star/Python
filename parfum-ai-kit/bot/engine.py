#!/usr/bin/env python3
"""engine.py — вся логика квиза без привязки к Telegram.

Одни и те же функции используют и Telegram-бот (`quiz_bot.py`), и веб-демо
(`demo_server.py`), и тесты. Меняете вопросы и ароматы — в `config.json`,
код трогать не нужно.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")


class ConfigError(Exception):
    """Понятная ошибка в config.json, а не трейсбек на пол-экрана."""


@dataclass(frozen=True)
class Config:
    brand: str
    welcome: str
    price_text: str
    delivery_text: str
    scents: tuple[dict[str, Any], ...]
    questions: tuple[dict[str, Any], ...]
    owner_username: str = ""
    start_button: str = "Подобрать аромат"

    @property
    def steps(self) -> int:
        return len(self.questions)


def _require(data: dict, key: str, where: str) -> Any:
    value = data.get(key)
    if value in (None, "", [], {}):
        raise ConfigError(f"{where}: не заполнено поле «{key}»")
    return value


def load_config(path: Path | str | None = None) -> Config:
    """Читает и проверяет config.json. Ошибки — текстом, без трейсбека."""
    config_path = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(f"нет файла конфигурации: {config_path}") from error
    except json.JSONDecodeError as error:
        raise ConfigError(
            f"{config_path}: ошибка JSON в строке {error.lineno}, колонка {error.colno} — "
            f"частая причина: лишняя запятая или незакрытая кавычка"
        ) from error

    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path}: ожидался объект {{...}} на верхнем уровне")

    brand = str(_require(raw, "brand", "config"))
    scents_raw = _require(raw, "scents", "config")
    questions_raw = _require(raw, "questions", "config")

    scents: list[dict] = []
    for index, scent in enumerate(scents_raw, start=1):
        where = f"scents[{index}]"
        if not isinstance(scent, dict):
            raise ConfigError(f"{where}: ожидался объект с name/tags/desc/price")
        tags = _require(scent, "tags", where)
        if not isinstance(tags, list):
            raise ConfigError(f"{where}.tags: нужен список тегов, например [\"warm\", \"woody\"]")
        scents.append(
            {
                "name": str(_require(scent, "name", where)),
                "tags": [str(tag) for tag in tags],
                "desc": str(_require(scent, "desc", where)),
                "price": str(_require(scent, "price", where)),
                "family": str(scent.get("family", "")),
            }
        )

    questions: list[dict] = []
    for index, question in enumerate(questions_raw, start=1):
        where = f"questions[{index}]"
        if not isinstance(question, dict):
            raise ConfigError(f"{where}: ожидался объект с text/options")
        options_raw = _require(question, "options", where)
        options: list[dict] = []
        for opt_index, option in enumerate(options_raw, start=1):
            opt_where = f"{where}.options[{opt_index}]"
            if not isinstance(option, dict):
                raise ConfigError(f"{opt_where}: ожидался объект с label/tags")
            if not isinstance(option.get("tags"), list):
                raise ConfigError(f"{opt_where}.tags: нужен список тегов")
            options.append(
                {"label": str(_require(option, "label", opt_where)), "tags": [str(t) for t in option["tags"]]}
            )
        questions.append({"text": str(_require(question, "text", where)), "options": options})

    return Config(
        brand=brand,
        welcome=str(_require(raw, "welcome", "config")).replace("{brand}", brand),
        price_text=str(_require(raw, "price_text", "config")),
        delivery_text=str(_require(raw, "delivery_text", "config")),
        scents=tuple(scents),
        questions=tuple(questions),
        owner_username=str(raw.get("owner_username", "")).lstrip("@"),
        start_button=str(raw.get("start_button") or "Подобрать аромат"),
    )


def pick_scent(scents: tuple[dict, ...] | list[dict], tags: list[str]) -> tuple[dict, int]:
    """Аромат с максимальным числом совпавших тегов. При равенстве — первый по списку."""
    if not scents:
        raise ConfigError("в конфиге нет ни одного аромата")
    wanted = set(tags)
    best, best_score = scents[0], -1
    for scent in scents:
        score = len(wanted & set(scent["tags"]))
        if score > best_score:
            best, best_score = scent, score
    return best, best_score


def recommendation_text(config: Config, scent: dict, answers: list[str], tags: list[str]) -> str:
    lines = [
        f"Готово. Ваш аромат — {scent['name']}",
        "",
        scent["desc"] + (f" ({scent['family']})" if scent["family"] else ""),
        scent["price"],
        "",
        "Ваши ответы: " + ", ".join(answer.lower() for answer in answers) + ".",
    ]
    if "gift" in tags:
        lines += ["", "Как подарок подойдёт: коробка и открытка уже внутри."]
    lines += [
        "",
        "Сомневаетесь — возьмите пробник: его стоимость зачтём при покупке флакона.",
        "Про раскрытие на вашей коже лучше спросить напрямую — отвечу голосом, а не шаблоном.",
    ]
    return "\n".join(lines)


class Session:
    """Состояние одного человека: где он в квизе и что уже ответил."""

    OK = "ok"
    STALE = "stale"
    BAD_OPTION = "bad_option"

    def __init__(self, config: Config) -> None:
        self.config = config
        self.step = 0
        self.tags: list[str] = []
        self.answers: list[str] = []
        self.finished = False

    @property
    def active(self) -> bool:
        return not self.finished

    def reset(self) -> None:
        self.step = 0
        self.tags = []
        self.answers = []
        self.finished = False

    def question(self) -> dict | None:
        if self.finished or self.step >= self.config.steps:
            return None
        return self.config.questions[self.step]

    def accept(self, step: int, option_index: int) -> str:
        """Принимает ответ. Возвращает OK / STALE (кнопка устарела) / BAD_OPTION."""
        if self.finished or step != self.step or self.step >= self.config.steps:
            return self.STALE
        options = self.config.questions[step]["options"]
        if not 0 <= option_index < len(options):
            return self.BAD_OPTION
        option = options[option_index]
        self.tags.extend(option["tags"])
        self.answers.append(option["label"])
        self.step += 1
        if self.step >= self.config.steps:
            self.finished = True
        return self.OK

    def result(self) -> tuple[dict, int, str]:
        """Возвращает (аромат, совпадения, готовый текст рекомендации)."""
        if not self.finished:
            raise RuntimeError("результат запрошен до окончания квиза")
        scent, score = pick_scent(self.config.scents, self.tags)
        return scent, score, recommendation_text(self.config, scent, self.answers, self.tags)

    def progress(self) -> str:
        return f"{min(self.step + 1, self.config.steps)}/{self.config.steps}"
