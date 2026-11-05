#!/usr/bin/env python3
"""Тесты логики бота без сети: FakeApi вместо Telegram.

Запуск: python3 -m unittest discover -s . -p "test_*.py" -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from quiz_bot import QUESTIONS, QuizBot, pick_scent


class FakeApi:
    """Запоминает вызовы вместо реальных запросов к Telegram."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.answered: list[str] = []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((text, {"chat_id": chat_id, "reply_markup": reply_markup}))
        return {"message_id": len(self.sent)}

    def answer_callback_query(self, callback_query_id, text=None):
        self.answered.append(callback_query_id)
        return True

    def get_updates(self, offset=None, timeout=30):
        return []

    def last_text(self) -> str:
        return self.sent[-1][0]

    def texts_for(self, chat_id: int) -> list[str]:
        return [text for text, meta in self.sent if meta["chat_id"] == chat_id]

    def last_text_for(self, chat_id: int) -> str:
        return self.texts_for(chat_id)[-1]


def message(chat_id: int, text: str, username: str = "client") -> dict:
    return {
        "message": {
            "chat": {"id": chat_id},
            "from": {"id": chat_id, "username": username, "first_name": "Анна"},
            "text": text,
        }
    }


def callback(chat_id: int, data: str, username: str = "client") -> dict:
    return {
        "callback_query": {
            "id": f"cb-{data}",
            "data": data,
            "from": {"id": chat_id, "username": username, "first_name": "Анна"},
            "message": {"chat": {"id": chat_id}},
        }
    }


class PickScentTest(unittest.TestCase):
    def test_green_bitter_goes_to_polyn(self):
        scent, score = pick_scent(["green", "bitter", "quiet", "medium"])
        self.assertEqual(scent["name"], "Полынь")
        self.assertGreater(score, 0)

    def test_fresh_goes_to_fresh_cut(self):
        scent, _ = pick_scent(["fresh", "green", "medium"])
        self.assertEqual(scent["name"], "Свежий срез")

    def test_empty_tags_returns_something(self):
        scent, score = pick_scent([])
        self.assertEqual(score, 0)
        self.assertIn(scent["name"], {"Северный", "Полынь", "Тихий час", "Свежий срез", "Тёплый вечер"})


class QuizFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.leads = Path(self.tmp.name) / "leads.jsonl"
        self.api = FakeApi()
        self.bot = QuizBot(self.api, leads_file=self.leads, owner_chat_id=999, owner_username="brand")

    def tearDown(self):
        self.tmp.cleanup()

    def run_quiz(self, choices=(0, 2, 1, 0, 0), chat_id=111):
        self.bot.handle_update(message(chat_id, "/start"))
        self.bot.handle_update(callback(chat_id, "start_quiz"))
        for step, choice in enumerate(choices):
            self.bot.handle_update(callback(chat_id, f"q:{step}:{choice}"))
        return chat_id

    def test_full_quiz_gives_recommendation_and_lead(self):
        chat_id = self.run_quiz()
        text = self.api.last_text_for(chat_id)
        self.assertIn("Ваш аромат", text)
        self.assertIn("₽", text)
        self.assertEqual(len(self.api.answered), 6)  # start_quiz + 5 ответов

        lines = self.leads.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        lead = json.loads(lines[0])
        self.assertEqual(lead["user_id"], chat_id)
        self.assertEqual(lead["username"], "client")
        self.assertEqual(len(lead["answers"]), len(QUESTIONS))
        self.assertIn(lead["recommended"], {"Северный", "Полынь", "Тихий час", "Свежий срез", "Тёплый вечер"})

    def test_owner_gets_notification(self):
        self.run_quiz()
        owner_messages = [text for text, meta in self.api.sent if meta["chat_id"] == 999]
        self.assertEqual(len(owner_messages), 1)
        self.assertIn("Новый подбор", owner_messages[0])
        self.assertIn("@client", owner_messages[0])

    def test_state_cleared_after_finish(self):
        chat_id = self.run_quiz()
        self.assertNotIn(chat_id, self.bot.states)

    def test_stale_callback_does_not_crash(self):
        self.bot.handle_update(callback(222, "q:3:0"))
        self.assertIn("устарела", self.api.last_text())

    def test_wrong_step_does_not_crash(self):
        chat_id = 333
        self.bot.handle_update(callback(chat_id, "start_quiz"))
        self.bot.handle_update(callback(chat_id, "q:4:0"))  # перескочил вопросы
        self.assertIn("устарела", self.api.last_text())

    def test_garbage_callback_data(self):
        self.bot.handle_update(callback(444, "q:не:число"))
        self.assertIn("устарела", self.api.last_text())
        self.bot.handle_update(callback(444, "что-то-странное"))
        self.assertIn("Привет", self.api.last_text())

    def test_option_out_of_range(self):
        chat_id = 555
        self.bot.handle_update(callback(chat_id, "start_quiz"))
        self.bot.handle_update(callback(chat_id, "q:0:99"))
        self.assertIn("Не понял выбор", self.api.last_text())

    def test_commands(self):
        self.bot.handle_update(message(10, "/price"))
        self.assertIn("пробник 5 мл", self.api.last_text())
        self.bot.handle_update(message(10, "/delivery"))
        self.assertIn("Доставка", self.api.last_text())
        self.bot.handle_update(message(10, "привет"))
        self.assertIn("/quiz", self.api.last_text())

    def test_gift_answer_adds_packaging_line(self):
        chat_id = self.run_quiz(choices=(1, 3, 2, 0, 1))  # вечер, сладкое, заметно, осень, подарок
        self.assertIn("как подарок", self.api.last_text_for(chat_id).lower())

    def test_two_users_independent(self):
        self.run_quiz(choices=(0, 0, 0, 1, 0), chat_id=1001)
        self.run_quiz(choices=(1, 3, 2, 0, 1), chat_id=1002)
        lines = self.leads.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual({json.loads(line)["user_id"] for line in lines}, {1001, 1002})


if __name__ == "__main__":
    unittest.main()
