#!/usr/bin/env python3
"""Тесты диагностики selfcheck.py — без сети и без реального токена."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engine import load_config
from selfcheck import diagnose, mask_token, print_results, token_format_error, unused_scent_tags


class FakeApi:
    def __init__(self, username: str = "brand_bot", fail_get_me: bool = False, fail_send: bool = False) -> None:
        self.username = username
        self.fail_get_me = fail_get_me
        self.fail_send = fail_send
        self.calls: list[str] = []

    def call(self, method, **params):
        self.calls.append(method)
        if method == "getMe":
            if self.fail_get_me:
                raise RuntimeError("getMe: HTTP 401 Unauthorized")
            return {"id": 1, "username": self.username, "first_name": "Test"}
        raise RuntimeError(f"unexpected method {method}")

    def send_message(self, chat_id, text, reply_markup=None):
        self.calls.append("sendMessage")
        if self.fail_send:
            raise RuntimeError("sendMessage: HTTP 400 chat not found")
        return {"message_id": 1}


class TokenTest(unittest.TestCase):
    def test_valid_token(self):
        self.assertIsNone(token_format_error("123456789:" + "A" * 35))

    def test_no_colon(self):
        self.assertIn("двоеточия", token_format_error("123456789AAA"))

    def test_non_digit_id(self):
        self.assertIn("числовой id", token_format_error("abc:" + "A" * 35))

    def test_short_secret(self):
        self.assertIn("короткая", token_format_error("123456789:short"))

    def test_space_inside(self):
        self.assertIn("пробел", token_format_error("123456789:" + "A" * 20 + " " + "B" * 15))

    def test_masking_hides_secret(self):
        masked = mask_token("123456789:" + "SECRETSECRETSECRET")
        self.assertIn("123456789", masked)
        self.assertNotIn("SECRET", masked)
        self.assertIn("•", masked)


class TagsTest(unittest.TestCase):
    def test_no_dead_tags_in_default_config(self):
        self.assertEqual(unused_scent_tags(load_config()), [])

    def test_dead_tag_detected(self):
        config = load_config()
        broken = list(config.scents)
        broken[0] = dict(broken[0], tags=list(broken[0]["tags"]) + ["barbershop"])
        patched = type(config)(**{**config.__dict__, "scents": tuple(broken)})
        self.assertEqual(unused_scent_tags(patched), ["barbershop"])


class DiagnoseTest(unittest.TestCase):
    def setUp(self):
        self.config = load_config()
        self.tmp = tempfile.TemporaryDirectory()
        self.leads = Path(self.tmp.name) / "leads.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def titles(self, results):
        return [item["title"] for item in results]

    def test_all_good(self):
        results = diagnose(FakeApi(), self.config, owner_chat_id=123, leads_file=self.leads)
        self.assertTrue(all(item["ok"] for item in results), [item for item in results if not item["ok"]])
        self.assertTrue(self.leads.exists())

    def test_bad_token_gives_revoke_hint(self):
        results = diagnose(FakeApi(fail_get_me=True), self.config, owner_chat_id=None, leads_file=self.leads)
        failed = [item for item in results if not item["ok"]]
        token_fail = next(item for item in failed if item["title"] == "Telegram не принял токен")
        self.assertIn("revoke", token_fail["hint"])

    def test_network_error_is_not_blamed_on_token(self):
        class OfflineApi(FakeApi):
            def call(self, method, **params):
                raise RuntimeError("<urlopen error TLS/SSL connection has been closed (EOF)>")

        results = diagnose(OfflineApi(), self.config, owner_chat_id=None, leads_file=self.leads)
        titles = self.titles(results)
        self.assertIn("Нет связи с Telegram", titles)
        self.assertNotIn("Telegram не принял токен", titles)
        offline = next(item for item in results if item["title"] == "Нет связи с Telegram")
        self.assertIn("VPN", offline["hint"])

    def test_missing_owner_is_reported(self):
        results = diagnose(FakeApi(), self.config, owner_chat_id=None, leads_file=self.leads)
        self.assertIn("OWNER_CHAT_ID не задан", self.titles(results))

    def test_owner_send_failure_is_reported(self):
        results = diagnose(FakeApi(fail_send=True), self.config, owner_chat_id=123, leads_file=self.leads)
        self.assertIn("Не удалось написать владельцу", self.titles(results))

    def test_bad_username_warning(self):
        results = diagnose(FakeApi(username="perfume"), self.config, owner_chat_id=123, leads_file=self.leads)
        self.assertIn("Имя бота не заканчивается на «bot»", self.titles(results))

    def test_print_results_exit_codes(self):
        ok = diagnose(FakeApi(), self.config, owner_chat_id=123, leads_file=self.leads)
        bad = diagnose(FakeApi(fail_get_me=True), self.config, owner_chat_id=None, leads_file=self.leads)
        self.assertEqual(print_results(ok), 0)
        self.assertEqual(print_results(bad), 1)


if __name__ == "__main__":
    unittest.main()
