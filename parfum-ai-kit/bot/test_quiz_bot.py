#!/usr/bin/env python3
"""Тесты Telegram-адаптера без сети: FakeApi вместо api.telegram.org."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import demo_server
from engine import Session, load_config
from quiz_bot import QuizBot, question_keyboard


class FakeApi:
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


class AdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.leads = Path(self.tmp.name) / "leads.jsonl"
        self.config = load_config()
        self.api = FakeApi()
        self.bot = QuizBot(self.api, self.config, leads_file=self.leads, owner_chat_id=999)

    def tearDown(self):
        self.tmp.cleanup()

    def run_quiz(self, choices=(0, 2, 1, 0, 0), chat_id=111):
        self.bot.handle_update(message(chat_id, "/start"))
        self.bot.handle_update(callback(chat_id, "start_quiz"))
        for step, choice in enumerate(choices):
            self.bot.handle_update(callback(chat_id, f"q:{step}:{choice}"))
        return chat_id

    def test_welcome_has_keyboard(self):
        self.bot.handle_update(message(10, "/start"))
        self.assertIn(self.config.brand, self.api.last_text_for(10))
        markup = self.api.sent[-1][1]["reply_markup"]
        self.assertIn("inline_keyboard", markup)

    def test_full_quiz_saves_lead(self):
        chat_id = self.run_quiz()
        text = self.api.last_text_for(chat_id)
        self.assertIn("Ваш аромат", text)
        self.assertIn("₽", text)
        self.assertEqual(len(self.api.answered), 6)  # старт + 5 ответов

        lines = self.leads.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        lead = json.loads(lines[0])
        self.assertEqual(lead["user_id"], chat_id)
        self.assertEqual(len(lead["answers"]), self.config.steps)
        self.assertIn(lead["recommended"], [scent["name"] for scent in self.config.scents])

    def test_owner_notified(self):
        self.run_quiz()
        owner = self.api.texts_for(999)
        self.assertEqual(len(owner), 1)
        self.assertIn("Новый подбор", owner[0])
        self.assertIn("@client", owner[0])

    def test_session_removed_after_finish(self):
        chat_id = self.run_quiz()
        self.assertNotIn(chat_id, self.bot.sessions)

    def test_restart_clears_progress(self):
        chat_id = 222
        self.bot.handle_update(callback(chat_id, "start_quiz"))
        self.bot.handle_update(callback(chat_id, "q:0:0"))
        self.bot.handle_update(message(chat_id, "/start"))
        self.assertEqual(self.bot.sessions, {})
        self.bot.handle_update(callback(chat_id, "start_quiz"))
        self.assertIn("1/5", self.api.last_text_for(chat_id))

    def test_stale_callback(self):
        self.bot.handle_update(callback(333, "q:3:0"))
        self.assertIn("устарела", self.api.last_text_for(333))

    def test_garbage_data(self):
        self.bot.handle_update(callback(444, "q:не:число"))
        self.assertIn("устарела", self.api.last_text_for(444))
        self.bot.handle_update(callback(444, "что-то-странное"))
        self.assertIn(self.config.brand, self.api.last_text_for(444))

    def test_out_of_range_option(self):
        chat_id = 555
        self.bot.handle_update(callback(chat_id, "start_quiz"))
        self.bot.handle_update(callback(chat_id, f"q:0:{len(self.config.questions[0]['options']) + 5}"))
        self.assertIn("Не понял выбор", self.api.last_text_for(chat_id))

    def test_commands(self):
        self.bot.handle_update(message(10, "/price"))
        self.assertIn("пробник", self.api.last_text_for(10).lower())
        self.bot.handle_update(message(10, "/delivery"))
        self.assertIn("Доставка", self.api.last_text_for(10))
        self.bot.handle_update(message(10, "привет"))
        self.assertIn("/quiz", self.api.last_text_for(10))

    def test_delivery_and_price_dont_break_quiz(self):
        chat_id = 666
        self.bot.handle_update(callback(chat_id, "start_quiz"))
        self.bot.handle_update(callback(chat_id, "q:0:0"))
        self.bot.handle_update(callback(chat_id, "price"))
        self.bot.handle_update(callback(chat_id, "q:1:2"))
        self.assertIn("3/5", self.api.last_text_for(chat_id))

    def test_two_users_independent(self):
        self.run_quiz(choices=(0, 0, 0, 1, 0), chat_id=1001)
        self.run_quiz(choices=(1, 3, 2, 0, 1), chat_id=1002)
        lines = self.leads.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual({json.loads(line)["user_id"] for line in lines}, {1001, 1002})

    def test_keyboard_matches_question(self):
        markup = question_keyboard(2, self.config.questions[2])
        labels = [row[0]["text"] for row in markup["inline_keyboard"]]
        self.assertEqual(labels, [option["label"] for option in self.config.questions[2]["options"]])
        self.assertEqual(markup["inline_keyboard"][0][0]["callback_data"], "q:2:0")


class DemoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = load_config()
        demo_server.CONFIG = self.config
        demo_server.LEADS_FILE = Path(self.tmp.name) / "demo_leads.jsonl"
        demo_server.STATES.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_state_has_welcome(self):
        sid, state = demo_server.get_state(None)
        self.assertTrue(sid)
        self.assertIn(self.config.brand, state.transcript[0][1])

    def test_same_sid_restores_state(self):
        sid, _ = demo_server.get_state(None)
        sid2, state2 = demo_server.get_state(sid)
        self.assertEqual(sid, sid2)
        self.assertEqual(len(state2.transcript), 1)

    def test_unknown_sid_creates_new(self):
        sid, _ = demo_server.get_state("несуществующий")
        self.assertIn(sid, demo_server.STATES)

    def test_full_flow_and_lead(self):
        _, state = demo_server.get_state(None)
        demo_server.apply_action(state, {"c": ["quiz"]})
        for step in range(self.config.steps):
            demo_server.apply_action(state, {"a": [f"{step}:0"]})
        self.assertTrue(state.session.finished)
        self.assertIn("Ваш аромат", state.transcript[-1][1])
        self.assertEqual(len(demo_server.LEADS_FILE.read_text(encoding="utf-8").strip().splitlines()), 1)

    def test_unfinished_lead_not_saved(self):
        _, state = demo_server.get_state(None)
        demo_server.apply_action(state, {"c": ["quiz"]})
        demo_server.apply_action(state, {"a": ["0:0"]})
        self.assertFalse(demo_server.LEADS_FILE.exists())

    def test_price_and_delivery(self):
        _, state = demo_server.get_state(None)
        demo_server.apply_action(state, {"c": ["price"]})
        self.assertIn("пробник", state.transcript[-1][1].lower())
        demo_server.apply_action(state, {"c": ["delivery"]})
        self.assertIn("Доставка", state.transcript[-1][1])

    def test_restart_resets(self):
        _, state = demo_server.get_state(None)
        demo_server.apply_action(state, {"c": ["quiz"]})
        demo_server.apply_action(state, {"a": ["0:0"]})
        demo_server.apply_action(state, {"c": ["restart"]})
        self.assertEqual(len(state.transcript), 1)
        self.assertFalse(state.session.finished)

    def test_initial_page_shows_menu_not_question(self):
        sid, state = demo_server.get_state(None)
        page = demo_server.render_page(sid, state).decode()
        self.assertIn(self.config.start_button, page)
        self.assertNotIn(self.config.questions[0]["options"][0]["label"], page)

    def test_render_contains_question(self):
        sid, state = demo_server.get_state(None)
        demo_server.apply_action(state, {"c": ["quiz"]})
        page = demo_server.render_page(sid, state).decode()
        self.assertIn(self.config.questions[0]["text"], page)
        self.assertIn(self.config.questions[0]["options"][0]["label"], page)

    def test_finished_quiz_shows_menu_again(self):
        sid, state = demo_server.get_state(None)
        demo_server.apply_action(state, {"c": ["quiz"]})
        for step in range(self.config.steps):
            demo_server.apply_action(state, {"a": [f"{step}:1"]})
        page = demo_server.render_page(sid, state).decode()
        self.assertFalse(state.started)
        self.assertIn(self.config.start_button, page)


if __name__ == "__main__":
    unittest.main()
