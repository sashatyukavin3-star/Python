#!/usr/bin/env python3
"""quiz_bot.py — Telegram-бот «подбери аромат» для маленького парфюмерного бренда.

Только стандартная библиотека Python: в Termux достаточно `pkg install python`.
Запуск:

    export TELEGRAM_TOKEN='123456:ABC...'        # токен от @BotFather
    export OWNER_CHAT_ID='123456789'            # куда присылать заявки (необязательно)
    export OWNER_USERNAME='moy_kanal'           # куда вести клиента (без @)
    export LEADS_FILE="$HOME/leads.jsonl"
    python3 quiz_bot.py

Что делает:
  * приветствие + кнопки «подобрать», «цены», «доставка», «написать»;
  * квиз из 5 вопросов с кнопками, без свободного ввода;
  * рекомендация аромата по ответам (см. SCENTS и QUESTIONS ниже);
  * сохраняет каждую заявку в JSONL и присылает владельцу уведомление.

Важно: бот общается только с теми, кто сам ему написал. Никаких рассылок
незнакомым людям и парсинга чатов — это спам и бан аккаунта.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# --------------------------------------------------------------------------
# НАСТРОЙКИ БРЕНДА — правьте только этот блок
# --------------------------------------------------------------------------

BRAND = "PARFUM"

WELCOME = (
    f"Привет! Это бот {BRAND}.\n\n"
    "Помогу подобрать аромат за 5 вопросов — без «понюхайте и решите сами».\n"
    "Отвечайте честно, как вам нравится, а не как «правильно».\n\n"
    "/quiz — начать подбор\n"
    "/price — цены и объёмы\n"
    "/delivery — доставка\n"
    "/help — меню"
)

PRICE_TEXT = (
    "Цены и объёмы:\n"
    "• пробник 5 мл — 500 ₽ (хватает примерно на 3 недели носки)\n"
    "• флакон 30 мл — 3 900 ₽\n"
    "• набор из 3 пробников — 1 200 ₽\n\n"
    "Пробник можно зачесть в стоимость флакона в течение 30 дней."
)

DELIVERY_TEXT = (
    "Доставка:\n"
    "• по городу — курьером 300 ₽, бесплатно от 5 000 ₽\n"
    "• по России — СДЭК/Почта, 300–500 ₽, 2–7 дней\n"
    "• отправляем в день оплаты, если заказ до 15:00\n\n"
    "Упаковка — коробка с открыткой, можно сразу как подарок."
)

# Ароматы в наличии. tags — по ним бот считает совпадения с ответами.
SCENTS: list[dict] = [
    {
        "name": "Северный",
        "tags": ["woody", "warm", "medium", "self"],
        "desc": "сухой кедр, смола, шерсть — тихо и близко к телу",
        "price": "30 мл — 3 900 ₽, пробник 5 мл — 500 ₽",
    },
    {
        "name": "Полынь",
        "tags": ["green", "bitter", "quiet", "medium", "self"],
        "desc": "горькая трава, пыль, тёплый сандал — конец августа",
        "price": "30 мл — 3 900 ₽, пробник 5 мл — 500 ₽",
    },
    {
        "name": "Тихий час",
        "tags": ["powdery", "musky", "quiet", "self"],
        "desc": "мускус, пудра, немного ириса — аромат для себя",
        "price": "30 мл — 3 600 ₽, пробник 5 мл — 500 ₽",
    },
    {
        "name": "Свежий срез",
        "tags": ["fresh", "green", "medium", "self"],
        "desc": "зелёный стебель, цитрус, немного мяты — прохладно",
        "price": "30 мл — 3 600 ₽, пробник 5 мл — 500 ₽",
    },
    {
        "name": "Тёплый вечер",
        "tags": ["warm", "sweet", "loud", "gift"],
        "desc": "смола, ваниль, немного дыма — заметно и надолго",
        "price": "30 мл — 4 200 ₽, пробник 5 мл — 600 ₽",
    },
]

# Вопросы квиза. У каждого варианта — теги, которые он добавляет.
QUESTIONS: list[dict] = [
    {
        "text": "1/5 · Куда чаще всего носите аромат?",
        "options": [
            {"label": "На работу, в люди", "tags": ["fresh", "quiet"]},
            {"label": "Вечером, на выход", "tags": ["warm", "loud"]},
            {"label": "Дома, для себя", "tags": ["quiet", "musky", "self"]},
            {"label": "Ношу всегда, не выбираю", "tags": ["medium"]},
        ],
    },
    {
        "text": "2/5 · Что вам ближе по характеру?",
        "options": [
            {"label": "Свежее и прозрачное", "tags": ["fresh"]},
            {"label": "Травяное и горькое", "tags": ["green", "bitter"]},
            {"label": "Тёплое и смолистое", "tags": ["warm", "woody"]},
            {"label": "Сладкое и пудровое", "tags": ["sweet", "powdery"]},
        ],
    },
    {
        "text": "3/5 · Насколько заметным должен быть шлейф?",
        "options": [
            {"label": "Тихо, только для себя", "tags": ["quiet"]},
            {"label": "Средне, на расстоянии руки", "tags": ["medium"]},
            {"label": "Заметно, чтобы чувствовали", "tags": ["loud"]},
        ],
    },
    {
        "text": "4/5 · Когда будете носить чаще?",
        "options": [
            {"label": "Осень-зима", "tags": ["warm", "woody"]},
            {"label": "Весна-лето", "tags": ["fresh", "green"]},
            {"label": "Круглый год", "tags": ["medium"]},
        ],
    },
    {
        "text": "5/5 · Кому выбираете аромат?",
        "options": [
            {"label": "Себе", "tags": ["self"]},
            {"label": "В подарок", "tags": ["gift"]},
            {"label": "Пока не знаю, выбираю", "tags": ["self"]},
        ],
    },
]


def pick_scent(tags: list[str]) -> tuple[dict, int]:
    """Возвращает аромат с максимальным числом совпадений тегов (и это число)."""
    wanted = set(tags)
    best, best_score = SCENTS[0], -1
    for scent in SCENTS:
        score = len(wanted & set(scent["tags"]))
        if score > best_score:
            best, best_score = scent, score
    return best, best_score


def recommendation_text(scent: dict, answers: list[str], tags: list[str]) -> str:
    lines = [
        "Готово. Ваш аромат — " + scent["name"],
        "",
        scent["desc"],
        scent["price"],
        "",
        "Ваши ответы: " + ", ".join(a.lower() for a in answers) + ".",
    ]
    if "gift" in tags:
        lines += ["", "Как подарок подойдёт: коробка и открытка уже внутри."]
    lines += [
        "",
        "Сомневаетесь — возьмите пробник: его стоимость зачтём при покупке флакона.",
        "Хотите спросить про раскрытие на вашей коже — напишите напрямую.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Обёртка над Telegram Bot API (только stdlib)
# --------------------------------------------------------------------------


class Api:
    """Минимальный клиент Telegram Bot API на urllib."""

    def __init__(self, token: str, timeout: int = 35) -> None:
        self.base = f"https://api.telegram.org/bot{token}/"
        self.timeout = timeout

    def call(self, method: str, **params):
        payload = {
            key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
            for key, value in params.items()
            if value is not None
        }
        data = urllib.parse.urlencode(payload).encode()
        request = urllib.request.Request(self.base + method, data=data)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise RuntimeError(f"{method}: HTTP {error.code} {detail}") from error
        if not body.get("ok"):
            raise RuntimeError(f"{method}: {body.get('description')}")
        return body["result"]

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None):
        return self.call("sendMessage", chat_id=chat_id, text=text, reply_markup=reply_markup)

    def answer_callback_query(self, callback_query_id: str, text: str | None = None):
        return self.call("answerCallbackQuery", callback_query_id=callback_query_id, text=text)

    def get_updates(self, offset: int | None = None, timeout: int = 30) -> list[dict]:
        return self.call("getUpdates", offset=offset, timeout=timeout, allowed_updates=["message", "callback_query"])


# --------------------------------------------------------------------------
# Логика бота
# --------------------------------------------------------------------------


def main_keyboard(owner_username: str) -> dict:
    rows = [
        [{"text": "Подобрать аромат", "callback_data": "start_quiz"}],
        [
            {"text": "Цены", "callback_data": "price"},
            {"text": "Доставка", "callback_data": "delivery"},
        ],
    ]
    if owner_username:
        rows.append([{"text": "Написать напрямую", "url": f"https://t.me/{owner_username}"}])
    return {"inline_keyboard": rows}


class QuizBot:
    def __init__(
        self,
        api,
        leads_file: Path | str = "leads.jsonl",
        owner_chat_id: int | None = None,
        owner_username: str = "",
    ) -> None:
        self.api = api
        self.leads_file = Path(leads_file)
        self.owner_chat_id = owner_chat_id
        self.owner_username = owner_username
        self.states: dict[int, dict] = {}

    # ---------------------------- публичный вход ---------------------------

    def handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
        elif "message" in update:
            self._handle_message(update["message"])

    # ------------------------------- helpers -------------------------------

    def _send(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        self.api.send_message(chat_id, text, reply_markup=reply_markup)

    def _menu(self, chat_id: int) -> None:
        self._send(chat_id, WELCOME, main_keyboard(self.owner_username))

    def _ask(self, chat_id: int, step: int) -> None:
        question = QUESTIONS[step]
        keyboard = {
            "inline_keyboard": [
                [{"text": option["label"], "callback_data": f"q:{step}:{index}"}]
                for index, option in enumerate(question["options"])
            ]
        }
        self._send(chat_id, question["text"], keyboard)

    def _start_quiz(self, chat_id: int) -> None:
        self.states[chat_id] = {"step": 0, "tags": [], "answers": []}
        self._ask(chat_id, 0)

    def _finish_quiz(self, chat_id: int, username: str | None, name: str) -> None:
        state = self.states.pop(chat_id, None)
        if not state:
            self._send(chat_id, "Похоже, подбор устарел. Начнём заново — /quiz")
            return
        scent, score = pick_scent(state["tags"])
        text = recommendation_text(scent, state["answers"], state["tags"])
        self._send(chat_id, text, main_keyboard(self.owner_username))
        self._save_lead(chat_id, username, name, state, scent, score)
        self._notify_owner(chat_id, username, name, state, scent)

    def _save_lead(self, chat_id, username, name, state, scent, score) -> None:
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "user_id": chat_id,
            "username": username,
            "name": name,
            "answers": state["answers"],
            "tags": state["tags"],
            "recommended": scent["name"],
            "score": score,
        }
        try:
            with self.leads_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as error:  # бот не должен падать из-за диска
            print(f"не удалось сохранить заявку: {error}", file=sys.stderr)

    def _notify_owner(self, chat_id, username, name, state, scent) -> None:
        if not self.owner_chat_id:
            return
        who = f"@{username}" if username else name
        text = (
            "Новый подбор аромата\n"
            f"Кто: {who} (id {chat_id})\n"
            f"Ответы: {', '.join(state['answers'])}\n"
            f"Рекомендация: {scent['name']}\n"
            "Написать: t.me/" + (username or "")
        )
        try:
            self._send(self.owner_chat_id, text)
        except RuntimeError as error:  # заблокировал бота или неверный id
            print(f"не удалось уведомить владельца: {error}", file=sys.stderr)

    # ------------------------------ обработчики ----------------------------

    def _handle_message(self, message: dict) -> None:
        chat_id = message["chat"]["id"]
        text = (message.get("text") or "").strip().lower()
        username = (message.get("from") or {}).get("username")
        name = " ".join(
            part
            for part in [
                (message.get("from") or {}).get("first_name"),
                (message.get("from") or {}).get("last_name"),
            ]
            if part
        ) or "клиент"

        if text in {"/start", "/help", "начать", "меню"}:
            self.states.pop(chat_id, None)
            self._menu(chat_id)
        elif text == "/quiz":
            self._start_quiz(chat_id)
        elif text == "/price":
            self._send(chat_id, PRICE_TEXT, main_keyboard(self.owner_username))
        elif text == "/delivery":
            self._send(chat_id, DELIVERY_TEXT, main_keyboard(self.owner_username))
        elif text in {"готово", "ответил", "ответила"}:
            self._finish_quiz(chat_id, username, name)
        else:
            self._send(
                chat_id,
                "Я пока понимаю только кнопки и команды.\n"
                "Подбор — /quiz, цены — /price, доставка — /delivery.",
                main_keyboard(self.owner_username),
            )

    def _handle_callback(self, callback: dict) -> None:
        data = callback.get("data", "")
        chat_id = callback["message"]["chat"]["id"]
        callback_id = callback.get("id", "")
        user = callback.get("from") or {}
        username = user.get("username")
        name = " ".join(part for part in [user.get("first_name"), user.get("last_name")] if part) or "клиент"

        try:
            self.api.answer_callback_query(callback_id)
        except RuntimeError as error:
            print(f"answerCallbackQuery: {error}", file=sys.stderr)

        if data == "start_quiz":
            self._start_quiz(chat_id)
        elif data == "price":
            self._send(chat_id, PRICE_TEXT, main_keyboard(self.owner_username))
        elif data == "delivery":
            self._send(chat_id, DELIVERY_TEXT, main_keyboard(self.owner_username))
        elif data.startswith("q:"):
            self._handle_answer(chat_id, username, name, data)
        else:
            self._menu(chat_id)

    def _handle_answer(self, chat_id: int, username, name, data: str) -> None:
        try:
            _, step_raw, option_raw = data.split(":")
            step, option_index = int(step_raw), int(option_raw)
        except ValueError:
            self._send(chat_id, "Кнопка устарела. Начнём заново — /quiz")
            return

        state = self.states.get(chat_id)
        if not state or state["step"] != step:
            self._send(chat_id, "Кнопка устарела. Начнём заново — /quiz")
            return
        options = QUESTIONS[step]["options"]
        if not 0 <= option_index < len(options):
            self._send(chat_id, "Не понял выбор. Начнём заново — /quiz")
            return

        option = options[option_index]
        state["tags"].extend(option["tags"])
        state["answers"].append(option["label"])
        state["step"] += 1

        if state["step"] < len(QUESTIONS):
            self._ask(chat_id, state["step"])
        else:
            self._finish_quiz(chat_id, username, name)


# --------------------------------------------------------------------------
# Точка входа
# --------------------------------------------------------------------------


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        print(f"{name} должен быть числом, получено: {raw!r}", file=sys.stderr)
        return None


def main() -> int:
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    if not token:
        print(
            "Нужен токен бота.\n"
            "1) Напишите @BotFather → /newbot → получите токен\n"
            "2) export TELEGRAM_TOKEN='123456:ABC...'\n"
            "3) python3 quiz_bot.py",
            file=sys.stderr,
        )
        return 2

    api = Api(token)
    bot = QuizBot(
        api,
        leads_file=Path(os.environ.get("LEADS_FILE", "leads.jsonl")).expanduser(),
        owner_chat_id=_env_int("OWNER_CHAT_ID"),
        owner_username=os.environ.get("OWNER_USERNAME", "").lstrip("@"),
    )
    print(f"Бот {BRAND} запущен. Заявки пишутся в {bot.leads_file}. Ctrl+C — остановить.")
    offset: int | None = None
    while True:
        try:
            updates = api.get_updates(offset, timeout=30)
        except (RuntimeError, urllib.error.URLError, TimeoutError, OSError) as error:
            print(f"сеть: {error} — повтор через 5 секунд", file=sys.stderr)
            time.sleep(5)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            try:
                bot.handle_update(update)
            except Exception as error:  # бот не должен умирать из-за одного апдейта
                print(f"ошибка обработки апдейта {update.get('update_id')}: {error}", file=sys.stderr)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nОстановлено.")
