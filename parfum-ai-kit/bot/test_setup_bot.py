#!/usr/bin/env python3
"""Тесты настройки профиля бота: план, применение, multipart, проверка лимитов."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engine import ConfigError, load_config
from setup_bot import apply_plan, build_plan, encode_multipart, render_plan, verify

RAW_DEFAULT = json.loads((Path(__file__).with_name("config.json")).read_text(encoding="utf-8"))


class FakeApi:
    def __init__(self, fail_methods: set[str] | None = None) -> None:
        self.base = "https://api.telegram.org/botTEST/"
        self.fail_methods = fail_methods or set()
        self.calls: list[tuple[str, dict]] = []

    def call(self, method, **params):
        self.calls.append((method, params))
        if method in self.fail_methods:
            raise RuntimeError(f"{method}: HTTP 400 Bad Request")
        if method == "getMe":
            return {"id": 1, "username": "brand_bot"}
        if method == "getMyName":
            return {"name": "PARFUM · подбор аромата"}
        if method == "getMyShortDescription":
            return {"short_description": "Подберу аромат за 5 вопросов"}
        if method == "getMyDescription":
            return {"description": "Привет! Это бот PARFUM."}
        if method == "getMyCommands":
            return {"commands": [{"command": "quiz", "description": "Подобрать"}]}
        if method == "getChatMenuButton":
            return {"menu_button": {"type": "commands"}}
        return True


def make_config(**profile_overrides) -> object:
    """Собирает временный config.json на основе настоящего."""
    raw = json.loads(json.dumps(RAW_DEFAULT))
    raw["profile"].update(profile_overrides)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(raw, tmp, ensure_ascii=False)
    tmp.close()
    return load_config(tmp.name)


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_plan_covers_profile(self):
        plan = build_plan(self.config)
        methods = [step["method"] for step in plan]
        self.assertEqual(
            methods,
            ["setMyName", "setMyShortDescription", "setMyDescription", "setMyCommands", "setChatMenuButton"],
        )

    def test_photo_goes_last(self):
        plan = build_plan(self.config, Path("avatar.png"))
        self.assertEqual(plan[-1]["method"], "setChatPhoto")
        self.assertEqual(plan[-1]["photo"], Path("avatar.png"))

    def test_empty_profile_leaves_only_menu_button(self):
        config = make_config(name="", short_description="", description="", commands=[])
        plan = build_plan(config)
        self.assertEqual([step["method"] for step in plan], ["setChatMenuButton"])

    def test_render_shows_limits_and_commands(self):
        lines = render_plan(self.config, build_plan(self.config))
        self.assertTrue(any("/64)" in line for line in lines), lines)
        self.assertTrue(any("/120)" in line for line in lines), lines)
        self.assertTrue(any("/quiz" in line and "/price" in line for line in lines), lines)


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_all_steps_applied_in_order(self):
        api = FakeApi()
        results = apply_plan(api, build_plan(self.config), verbose=False)
        self.assertTrue(all(item["ok"] for item in results))
        self.assertEqual(
            [method for method, _ in api.calls],
            ["setMyName", "setMyShortDescription", "setMyDescription", "setMyCommands", "setChatMenuButton"],
        )
        self.assertEqual(api.calls[3][1]["commands"][0]["command"], "start")

    def test_failure_does_not_stop_others(self):
        api = FakeApi(fail_methods={"setMyDescription"})
        results = apply_plan(api, build_plan(self.config), verbose=False)
        self.assertEqual(len([item for item in results if not item["ok"]]), 1)
        self.assertIn("setChatMenuButton", [method for method, _ in api.calls])

    def test_verify_reads_profile(self):
        snapshot = verify(FakeApi())
        self.assertEqual(snapshot["username"], "brand_bot")
        self.assertIn("PARFUM", snapshot["name"])
        self.assertEqual(snapshot["menu_button"], {"type": "commands"})


class MultipartTest(unittest.TestCase):
    def test_body_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            photo = Path(tmp) / "avatar.png"
            photo.write_bytes(b"\x89PNG\r\n\x1a\nfakebytes")
            body, content_type = encode_multipart({"chat_id": "1"}, "photo", photo)

        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        boundary = content_type.split("boundary=")[1].encode()
        self.assertIn(b"--" + boundary, body)
        self.assertIn(b'name="photo"; filename="avatar.png"', body)
        self.assertIn(b"Content-Type: image/png", body)
        self.assertIn(b"fakebytes", body)
        self.assertTrue(body.rstrip().endswith(b"--" + boundary + b"--"))


class LimitsTest(unittest.TestCase):
    def test_short_description_too_long(self):
        with self.assertRaises(ConfigError) as ctx:
            make_config(short_description="я" * 121)
        self.assertIn("120", str(ctx.exception))

    def test_description_too_long(self):
        with self.assertRaises(ConfigError) as ctx:
            make_config(description="я" * 513)
        self.assertIn("512", str(ctx.exception))

    def test_long_name_is_truncated_not_fatal(self):
        config = make_config(name="я" * 100)
        self.assertEqual(len(config.profile_name), 64)

    def test_bad_command_name(self):
        with self.assertRaises(ConfigError) as ctx:
            make_config(commands=[{"command": "Quiz 1", "description": "x"}])
        self.assertIn("команд", str(ctx.exception))

    def test_command_limits(self):
        with self.assertRaises(ConfigError) as ctx:
            make_config(commands=[{"command": "a" * 33, "description": "x"}])
        self.assertIn("32", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
