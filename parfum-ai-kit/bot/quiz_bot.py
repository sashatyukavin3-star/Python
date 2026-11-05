#!/usr/bin/env python3
"""quiz_bot.py — Telegram-бот «подбери аромат». Только стандартная библиотека Python.

Вся логика — в `engine.py`, вопросы и ароматы — в `config.json`.
Здесь только адаптер: Telegram API, кнопки, сохранение заявок.

Запуск:

    export TELEGRAM_TOKEN='123456:ABC...'     # токен от @BotFather
    export OWNER_CHAT_ID='123456789'         # куда присылать заявки (необязательно)
    export LEADS_FILE="$HOME/leads.jsonl"
    export CONFIG_FILE="$HOME/config.json"   # необязательно, по умолчанию рядом
    python3 quiz_bot.py

Бот общается только с теми, кто сам ему написал. Никаких рассылок незнакомым
людям и парсинга чатов: это спам и блокировка аккаунта.
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

from engine import Config, ConfigError, Session, load_config


# --------------------------------------------------------------------------
# Telegram Bot API на urllib (без сторонних библиотек)
# --------------------------------------------------------------------------


class Api:
    def __init__(self, token: str, timeout: int = 35) -> None:
        self.base = f"https://api.telegram.org/bot{token}/"
        self.timeout = timeout

    def call(self, method: str, **params):
        payload = {
            key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
            for key, value in params.items()
            if value is not None
        }
        request = urllib.request.Request(self.base + method, data=urllib.parse.urlencode(payload).encode())
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
# Клавиатуры
# --------------------------------------------------------------------------


def main_keyboard(config: Config) -> dict:
    rows = [[{"text": config.start_button, "callback_data": "start_quiz"}]]
    rows.append(
        [
            {"text": "Цены", "callback_data": "price"},
            {"text": "Доставка", "callback_data": "delivery"},
        ]
    )
    if config.owner_username:
        rows.append([{"text": "Написать напрямую", "url": f"https://t.me/{config.owner_username}"}])
    return {"inline_keyboard": rows}


def question_keyboard(step: int, question: dict) -> dict:
    return {
        "inline_keyboard": [
            [{"text": option["label"], "callback_data": f"q:{step}:{index}"}]
            for index, option in enumerate(question["options"])
        ]
    }


# --------------------------------------------------------------------------
# Бот
# --------------------------------------------------------------------------


class QuizBot:
    def __init__(self, api, config: Config, leads_file: Path | str = "leads.jsonl", owner_chat_id: int | None = None):
        self.api = api
        self.config = config
        self.leads_file = Path(leads_file)
        self.owner_chat_id = owner_chat_id
        self.sessions: dict[int, Session] = {}

    # ------------------------------- вход --------------------------------

    def handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
        elif "message" in update:
            self._handle_message(update["message"])

    # ------------------------------ helpers ------------------------------

    def _send(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        self.api.send_message(chat_id, text, reply_markup=reply_markup)

    def _menu(self, chat_id: int) -> None:
        self._send(chat_id, self.config.welcome, main_keyboard(self.config))

    def _ask(self, chat_id: int, session: Session) -> None:
        question = session.question()
        if question is None:
            return
        self._send(chat_id, question["text"], question_keyboard(session.step, question))

    def _start_quiz(self, chat_id: int) -> None:
        session = self.sessions.setdefault(chat_id, Session(self.config))
        session.reset()
        self._ask(chat_id, session)

    def _finish(self, chat_id: int, username: str | None, name: str) -> None:
        session = self.sessions.pop(chat_id, None)
        if not session or not session.finished:
            self._send(chat_id, "Похоже, подбор устарел. Начнём заново — /quiz", main_keyboard(self.config))
            return
        scent, score, text = session.result()
        self._send(chat_id, text, main_keyboard(self.config))
        self._save_lead(chat_id, username, name, session, scent, score)
        self._notify_owner(chat_id, username, name, session, scent)

    # --------------------------- заявки и уведомления ---------------------

    def _save_lead(self, chat_id, username, name, session: Session, scent, score) -> None:
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "user_id": chat_id,
            "username": username,
            "name": name,
            "answers": session.answers,
            "tags": session.tags,
            "recommended": scent["name"],
            "score": score,
        }
        try:
            self.leads_file.parent.mkdir(parents=True, exist_ok=True)
            with self.leads_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as error:
            print(f"не удалось сохранить заявку: {error}", file=sys.stderr)

    def _notify_owner(self, chat_id, username, name, session: Session, scent) -> None:
        if not self.owner_chat_id:
            return
        who = f"@{username}" if username else name
        text = (
            "Новый подбор аромата\n"
            f"Кто: {who} (id {chat_id})\n"
            f"Ответы: {', '.join(session.answers)}\n"
            f"Рекомендация: {scent['name']}"
        )
        if username:
            text += f"\nНаписать: t.me/{username}"
        try:
            self._send(self.owner_chat_id, text)
        except RuntimeError as error:
            print(f"не удалось уведомить владельца: {error}", file=sys.stderr)

    # ------------------------------ сообщения ----------------------------

    def _handle_message(self, message: dict) -> None:
        chat_id = message["chat"]["id"]
        text = (message.get("text") or "").strip().lower()
        sender = message.get("from") or {}
        username = sender.get("username")
        name = " ".join(part for part in [sender.get("first_name"), sender.get("last_name")] if part) or "клиент"

        if text in {"/start", "/help", "начать", "меню"}:
            self.sessions.pop(chat_id, None)
            self._menu(chat_id)
        elif text == "/quiz":
            self._start_quiz(chat_id)
        elif text == "/price":
            self._send(chat_id, self.config.price_text, main_keyboard(self.config))
        elif text == "/delivery":
            self._send(chat_id, self.config.delivery_text, main_keyboard(self.config))
        else:
            self._send(
                chat_id,
                "Понимаю кнопки и команды:\n/quiz — подбор, /price — цены, /delivery — доставка.",
                main_keyboard(self.config),
            )

    # ------------------------------ колбэки ------------------------------

    def _handle_callback(self, callback: dict) -> None:
        data = callback.get("data", "")
        chat_id = callback["message"]["chat"]["id"]
        sender = callback.get("from") or {}
        username = sender.get("username")
        name = " ".join(part for part in [sender.get("first_name"), sender.get("last_name")] if part) or "клиент"

        try:
            self.api.answer_callback_query(callback.get("id", ""))
        except RuntimeError as error:
            print(f"answerCallbackQuery: {error}", file=sys.stderr)

        if data == "start_quiz":
            self._start_quiz(chat_id)
        elif data == "price":
            self._send(chat_id, self.config.price_text, main_keyboard(self.config))
        elif data == "delivery":
            self._send(chat_id, self.config.delivery_text, main_keyboard(self.config))
        elif data.startswith("q:"):
            self._handle_answer(chat_id, username, name, data)
        else:
            self._menu(chat_id)

    def _handle_answer(self, chat_id: int, username, name, data: str) -> None:
        try:
            _, step_raw, option_raw = data.split(":")
            step, option_index = int(step_raw), int(option_raw)
        except ValueError:
            self._send(chat_id, "Кнопка устарела. Начнём заново — /quiz", main_keyboard(self.config))
            return

        session = self.sessions.get(chat_id)
        if session is None:
            self._send(chat_id, "Кнопка устарела. Начнём заново — /quiz", main_keyboard(self.config))
            return

        status = session.accept(step, option_index)
        if status == Session.STALE:
            self._send(chat_id, "Кнопка устарела. Начнём заново — /quiz", main_keyboard(self.config))
        elif status == Session.BAD_OPTION:
            self._send(chat_id, "Не понял выбор. Начнём заново — /quiz", main_keyboard(self.config))
        elif session.finished:
            self._finish(chat_id, username, name)
        else:
            self._ask(chat_id, session)


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
            "Нужен токен бота:\n"
            "1) @BotFather → /newbot → скопируйте токен\n"
            "2) export TELEGRAM_TOKEN='123456:ABC...'\n"
            "3) python3 quiz_bot.py",
            file=sys.stderr,
        )
        return 2

    try:
        config = load_config(os.environ.get("CONFIG_FILE") or None)
    except ConfigError as error:
        print(f"ошибка конфигурации: {error}", file=sys.stderr)
        return 2

    api = Api(token)
    bot = QuizBot(
        api,
        config,
        leads_file=Path(os.environ.get("LEADS_FILE", "leads.jsonl")).expanduser(),
        owner_chat_id=_env_int("OWNER_CHAT_ID"),
    )
    print(f"Бот «{config.brand}» запущен: {config.steps} вопросов, {len(config.scents)} ароматов.")
    print(f"Заявки пишутся в {bot.leads_file}. Ctrl+C — остановить.")

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
            except Exception as error:
                print(f"ошибка обработки апдейта {update.get('update_id')}: {error}", file=sys.stderr)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nОстановлено.")
