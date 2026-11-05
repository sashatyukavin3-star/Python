#!/usr/bin/env python3
"""demo_server.py — веб-демо бота без Telegram и без токена.

Тот же движок (`engine.py`) и тот же config.json, что у настоящего бота, только
вместо Telegram — простой HTTP-сервер на stdlib. Нужен, чтобы:
  * показать бота клиенту до регистрации в @BotFather;
  * прогнать все ветки квиза самому;
  * проверить тексты и подобранные ароматы.

Запуск:  python3 demo_server.py            (порт 8000)
         PORT=9000 python3 demo_server.py
"""

from __future__ import annotations

import html
import json
import os
import secrets
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from engine import Config, ConfigError, Session, load_config

MAX_STATES = 500
LEADS_FILE = Path(os.environ.get("DEMO_LEADS_FILE", Path(__file__).with_name("demo_leads.jsonl")))


class DemoState:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.session = Session(config)
        self.transcript: list[tuple[str, str]] = [("bot", config.welcome)]
        self.touched = time.time()
        self.started = False  # квиз начался только после нажатия кнопки

    def say(self, text: str) -> None:
        self.transcript.append(("bot", text))

    def hear(self, text: str) -> None:
        self.transcript.append(("user", text))

    def menu_buttons(self) -> list[tuple[str, str]]:
        buttons = [(self.config.start_button, "c=quiz")]
        buttons += [("Цены", "c=price"), ("Доставка", "c=delivery")]
        return buttons


STATES: dict[str, DemoState] = {}
CONFIG: Config | None = None


def get_state(sid: str | None) -> tuple[str, DemoState]:
    if sid and sid in STATES:
        STATES[sid].touched = time.time()
        return sid, STATES[sid]
    sid = secrets.token_urlsafe(6)
    state = DemoState(CONFIG)  # type: ignore[arg-type]
    STATES[sid] = state
    if len(STATES) > MAX_STATES:  # не копим вечность
        oldest = min(STATES, key=lambda key: STATES[key].touched)
        STATES.pop(oldest, None)
    return sid, state


def save_lead(state: DemoState) -> None:
    scent, score, _ = state.session.result()
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "user_id": "demo",
        "username": None,
        "answers": state.session.answers,
        "tags": state.session.tags,
        "recommended": scent["name"],
        "score": score,
    }
    try:
        with LEADS_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as error:
        print(f"не удалось записать демо-заявку: {error}")


def apply_action(state: DemoState, params: dict) -> None:
    """Обрабатывает клик по кнопке так же, как это сделал бы Telegram-бот."""
    if "a" in params:
        raw = params["a"][0]
        try:
            step_raw, option_raw = raw.split(":")
            step, option_index = int(step_raw), int(option_raw)
        except ValueError:
            state.say("Кнопка устарела. Начнём заново — /quiz")
            return
        question = state.session.question()
        status = state.session.accept(step, option_index)
        if status == Session.OK and question is not None:
            state.hear(question["options"][option_index]["label"])
        if status == Session.OK and not state.session.finished:
            state.say(state.session.question()["text"])  # type: ignore[index]
        elif status == Session.OK and state.session.finished:
            scent, _, text = state.session.result()
            state.say(text)
            state.started = False
            save_lead(state)
        elif status == Session.STALE:
            state.say("Кнопка устарела. Начнём заново — /quiz")
        else:
            state.say("Не понял выбор. Начнём заново — /quiz")
        return

    command = params.get("c", [""])[0]
    if command == "quiz":
        state.session.reset()
        state.started = True
        state.hear("/quiz")
        state.say(state.session.question()["text"])  # type: ignore[index]
    elif command == "price":
        state.hear("/price")
        state.say(CONFIG.price_text)  # type: ignore[union-attr]
    elif command == "delivery":
        state.hear("/delivery")
        state.say(CONFIG.delivery_text)  # type: ignore[union-attr]
    elif command == "restart":
        state.session.reset()
        state.started = False
        state.transcript = [("bot", CONFIG.welcome)]  # type: ignore[union-attr]
    else:
        state.say(CONFIG.welcome)  # type: ignore[union-attr]


CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24px 12px;
  background: radial-gradient(circle at 20% 0%, #1c2b3a 0%, #0d1117 55%, #080a0d 100%);
  font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  color: #e9edf2; display: flex; justify-content: center; min-height: 100vh;
}
.phone {
  width: 100%; max-width: 430px; height: min(760px, calc(100vh - 48px));
  background: #17212b; border-radius: 22px; overflow: hidden;
  display: flex; flex-direction: column;
  box-shadow: 0 24px 60px rgba(0,0,0,.55), 0 0 0 1px rgba(255,255,255,.06);
}
.header {
  padding: 12px 16px; background: #202b36; display: flex; align-items: center; gap: 10px;
  border-bottom: 1px solid rgba(255,255,255,.06);
}
.avatar {
  width: 34px; height: 34px; border-radius: 50%;
  background: linear-gradient(135deg, #7d5a3c, #c9a227);
  display: grid; place-items: center; font-size: 15px; font-weight: 700; color: #1b1206;
}
.title { font-size: 15px; font-weight: 600; line-height: 1.2; }
.subtitle { font-size: 11px; color: #8fa3b5; margin-top: 2px; }
.chat { flex: 1; overflow-y: auto; padding: 14px 12px 6px; display: flex; flex-direction: column; gap: 8px; }
.bubble {
  max-width: 84%; padding: 9px 12px; border-radius: 14px; font-size: 13.5px; line-height: 1.45;
  white-space: pre-wrap; word-wrap: break-word; background: #212d3b; border-bottom-left-radius: 5px;
  box-shadow: 0 1px 1px rgba(0,0,0,.2);
}
.bubble.user { align-self: flex-end; background: #2b5278; border-radius: 14px; border-bottom-right-radius: 5px; }
.footer { padding: 10px 12px 14px; background: #17212b; border-top: 1px solid rgba(255,255,255,.05); }
.buttons { display: flex; flex-direction: column; gap: 6px; }
.row { display: flex; gap: 6px; }
.row a { flex: 1; }
.btn {
  display: block; text-align: center; text-decoration: none; color: #e9edf2;
  background: #2b3b4b; border: 1px solid rgba(255,255,255,.06);
  padding: 9px 12px; border-radius: 10px; font-size: 13px; transition: background .15s, transform .1s;
}
.btn:hover { background: #35485b; }
.btn:active { transform: scale(.99); }
.btn.primary { background: #2b5278; }
.btn.primary:hover { background: #33608c; }
.note { font-size: 10.5px; color: #7d8f9f; text-align: center; margin-top: 8px; line-height: 1.4; }
.note a { color: #8ab4d8; }
.reset { background: transparent; border-color: rgba(255,255,255,.08); color: #9fb2c2; font-size: 12px; }
.badge {
  font-size: 10px; text-transform: uppercase; letter-spacing: .6px; color: #9fb2c2;
  background: rgba(255,255,255,.06); padding: 3px 7px; border-radius: 20px; margin-left: auto;
}
"""


def render_page(sid: str, state: DemoState) -> bytes:
    config = CONFIG
    assert config is not None

    bubbles = []
    for role, text in state.transcript:
        css = "bubble user" if role == "user" else "bubble"
        bubbles.append(f'<div class="{css}">{html.escape(text)}</div>')

    if state.started and state.session.question() is not None and state.session.active:
        question = state.session.question()
        rows = [
            f'<div class="row"><a class="btn primary" href="/?s={sid}&a={state.session.step}:{index}">'
            f"{html.escape(option['label'])}</a></div>"
            for index, option in enumerate(question["options"])  # type: ignore[index]
        ]
        buttons = "".join(rows)
    else:
        rows = [
            f'<div class="row"><a class="btn primary" href="/?s={sid}&c=quiz">'
            f"{html.escape(config.start_button)}</a></div>",
            f'<div class="row"><a class="btn" href="/?s={sid}&c=price">Цены</a>'
            f'<a class="btn" href="/?s={sid}&c=delivery">Доставка</a></div>',
        ]
        if config.owner_username:
            rows.append(
                f'<div class="row"><a class="btn" target="_blank" rel="noopener" '
                f'href="https://t.me/{html.escape(config.owner_username)}">Написать напрямую</a></div>'
            )
        rows.append(f'<div class="row"><a class="btn reset" href="/?s={sid}&c=restart">Начать сначала</a></div>')
        buttons = "".join(rows)

    page = f"""<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(config.brand)} — демо бота</title>
<style>{CSS}</style>
</head><body>
<div class="phone">
  <div class="header">
    <div class="avatar">{html.escape(config.brand[:1].upper())}</div>
    <div>
      <div class="title">{html.escape(config.brand)} · подбор аромата</div>
      <div class="subtitle">бот онлайн · {config.steps} вопроса · {len(config.scents)} аромата</div>
    </div>
    <div class="badge">демо</div>
  </div>
  <div class="chat">{''.join(bubbles)}</div>
  <div class="footer">
    <div class="buttons">{buttons}</div>
    <div class="note">Это тот же код, что у Telegram-бота, только без токена.<br>
    Ссылка на реального бота — <a href="README.md">bot/README.md</a></div>
  </div>
</div>
</body></html>"""
    return page.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "PerfumeDemo/1.0"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._respond(HTTPStatus.OK, b"ok", "text/plain; charset=utf-8")
            return
        if parsed.path not in {"/", "/index.html"}:
            self._respond(HTTPStatus.NOT_FOUND, "Не найдено".encode(), "text/plain; charset=utf-8")
            return

        params = parse_qs(parsed.query)
        sid, state = get_state(params.get("s", [None])[0])
        if "a" in params or "c" in params:
            apply_action(state, params)
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", f"/?s={sid}")
            self.end_headers()
            return
        self._respond(HTTPStatus.OK, render_page(sid, state), "text/html; charset=utf-8")

    def _respond(self, status, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} — {fmt % args}")


def main() -> int:
    global CONFIG
    try:
        CONFIG = load_config(os.environ.get("CONFIG_FILE") or None)
    except ConfigError as error:
        print(f"ошибка конфигурации: {error}")
        return 2

    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Демо бота «{CONFIG.brand}»: http://0.0.0.0:{port}")
    print(f"Заявки демо пишутся в {LEADS_FILE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
