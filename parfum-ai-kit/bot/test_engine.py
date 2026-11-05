#!/usr/bin/env python3
"""Тесты движка и конфигурации: python3 -m unittest discover -s . -p "test_*.py" -v"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engine import ConfigError, Session, load_config, pick_scent, recommendation_text


class LoadConfigTest(unittest.TestCase):
    def test_default_config_is_valid(self):
        config = load_config()
        self.assertTrue(config.brand)
        self.assertEqual(config.steps, len(config.questions))
        self.assertGreaterEqual(len(config.scents), 3)
        for scent in config.scents:
            self.assertTrue(scent["name"] and scent["tags"] and scent["price"])

    def test_brand_substituted_in_welcome(self):
        config = load_config()
        self.assertNotIn("{brand}", config.welcome)

    def test_missing_file(self):
        with self.assertRaises(ConfigError) as ctx:
            load_config("нет-такого-файла.json")
        self.assertIn("нет файла", str(ctx.exception))

    def test_broken_json_message_is_human(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text('{"brand": "X",}', encoding="utf-8")
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertIn("ошибка JSON", str(ctx.exception))

    def test_missing_scents(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps({"brand": "X", "welcome": "hi", "price_text": "p", "delivery_text": "d",
                                        "questions": [{"text": "q", "options": [{"label": "a", "tags": ["x"]}]}]}),
                            encoding="utf-8")
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertIn("scents", str(ctx.exception))

    def test_tags_must_be_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps({
                "brand": "X", "welcome": "hi", "price_text": "p", "delivery_text": "d",
                "scents": [{"name": "A", "tags": "warm", "desc": "x", "price": "y"}],
                "questions": [{"text": "q", "options": [{"label": "a", "tags": ["x"]}]}],
            }), encoding="utf-8")
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertIn("нужен список тегов", str(ctx.exception))

    def test_owner_username_without_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps({
                "brand": "X", "welcome": "hi", "price_text": "p", "delivery_text": "d",
                "owner_username": "@brand",
                "scents": [{"name": "A", "tags": ["warm"], "desc": "x", "price": "y"}],
                "questions": [{"text": "q", "options": [{"label": "a", "tags": ["x"]}]}],
            }), encoding="utf-8")
            self.assertEqual(load_config(path).owner_username, "brand")


class ScoringTest(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_green_bitter_prefers_polyn(self):
        scent, score = pick_scent(self.config.scents, ["green", "bitter", "quiet", "medium"])
        self.assertEqual(scent["name"], "Полынь")
        self.assertGreater(score, 0)

    def test_fresh_prefers_fresh_cut(self):
        scent, _ = pick_scent(self.config.scents, ["fresh", "green"])
        self.assertEqual(scent["name"], "Свежий срез")

    def test_empty_tags_gives_first_scent(self):
        scent, score = pick_scent(self.config.scents, [])
        self.assertEqual(score, 0)
        self.assertEqual(scent["name"], self.config.scents[0]["name"])

    def test_recommendation_contains_price_and_family(self):
        scent = self.config.scents[0]
        text = recommendation_text(self.config, scent, ["Себе"], ["self"])
        self.assertIn(scent["name"], text)
        self.assertIn("₽", text)
        self.assertIn("пробник", text.lower())

    def test_gift_adds_packaging(self):
        scent = self.config.scents[-1]
        text = recommendation_text(self.config, scent, ["В подарок"], ["gift"])
        self.assertIn("подарок", text.lower())


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def answer_all(self, session: Session, choices):
        for step, choice in enumerate(choices):
            self.assertEqual(session.accept(step, choice), Session.OK)

    def test_full_pass(self):
        session = Session(self.config)
        self.assertEqual(session.progress(), "1/5")
        self.answer_all(session, [0, 2, 1, 0, 0])
        self.assertTrue(session.finished)
        self.assertFalse(session.active)
        self.assertIsNone(session.question())
        scent, score, text = session.result()
        self.assertIn(scent["name"], text)
        self.assertEqual(len(session.answers), self.config.steps)

    def test_wrong_step_is_stale(self):
        session = Session(self.config)
        self.assertEqual(session.accept(3, 0), Session.STALE)

    def test_out_of_range_option(self):
        session = Session(self.config)
        self.assertEqual(session.accept(0, 99), Session.BAD_OPTION)

    def test_answer_after_finish_is_stale(self):
        session = Session(self.config)
        self.answer_all(session, [0, 0, 0, 0, 0])
        self.assertEqual(session.accept(4, 0), Session.STALE)

    def test_result_before_finish_raises(self):
        session = Session(self.config)
        with self.assertRaises(RuntimeError):
            session.result()

    def test_reset_clears_state(self):
        session = Session(self.config)
        self.answer_all(session, [0, 0, 0, 0, 0])
        session.reset()
        self.assertFalse(session.finished)
        self.assertEqual(session.step, 0)
        self.assertEqual(session.tags, [])
        self.assertIsNotNone(session.question())


if __name__ == "__main__":
    unittest.main()
