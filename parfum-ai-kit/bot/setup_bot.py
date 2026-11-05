#!/usr/bin/env python3
"""setup_bot.py — полная настройка профиля бота одним запуском.

Что применяет (всё берётся из `config.json` → блок `profile`):
  * имя бота (setMyName);
  * краткое описание — то, что видно до /start (setMyShortDescription);
  * описание в профиле (setMyDescription);
  * список команд в меню (setMyCommands);
  * кнопка меню = команды (setChatMenuButton);
  * аватар, если передан --photo (setChatPhoto).

Запуск:

    export TELEGRAM_TOKEN='123456789:AA...'
    python3 setup_bot.py                     # применить всё
    python3 setup_bot.py --dry-run           # только показать план
    python3 setup_bot.py --photo avatar.png  # ещё и аватар
    python3 setup_bot.py --verify-only       # что уже стоит в Telegram

Скрипт идемпотентен: повторный запуск ничего не ломает, просто перезаписывает
профиль теми же значениями. Пользователям ничего не отправляется.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

from engine import (
    MAX_COMMAND,
    MAX_DESCRIPTION,
    MAX_NAME,
    MAX_SHORT_DESCRIPTION,
    Config,
    ConfigError,
    load_config,
)
from quiz_bot import Api
from selfcheck import _load_dotenv, token_format_error


def build_plan(config: Config, photo: Path | None = None) -> list[dict]:
    """Список шагов настройки: что и каким методом Bot API менять."""
    plan: list[dict] = []
    if config.profile_name:
        plan.append({"method": "setMyName", "title": "Имя бота", "params": {"name": config.profile_name}})
    if config.short_description:
        plan.append(
            {
                "method": "setMyShortDescription",
                "title": "Краткое описание (до /start)",
                "params": {"short_description": config.short_description},
            }
        )
    if config.description:
        plan.append(
            {
                "method": "setMyDescription",
                "title": "Описание в профиле",
                "params": {"description": config.description},
            }
        )
    if config.commands:
        plan.append(
            {
                "method": "setMyCommands",
                "title": "Команды в меню",
                "params": {"commands": list(config.commands)},
            }
        )
    plan.append(
        {
            "method": "setChatMenuButton",
            "title": "Кнопка меню",
            "params": {"menu_button": {"type": "commands"}},
        }
    )
    if photo is not None:
        plan.append({"method": "setChatPhoto", "title": "Аватар", "params": {}, "photo": photo})
    return plan


def render_plan(config: Config, plan: list[dict]) -> list[str]:
    """Человекочитаемый план — показывается в --dry-run и перед применением."""
    lines: list[str] = []
    for index, step in enumerate(plan, start=1):
        method = step["method"]
        if method == "setMyName":
            value = f"{config.profile_name} ({len(config.profile_name)}/{MAX_NAME})"
        elif method == "setMyShortDescription":
            value = f"{config.short_description} ({len(config.short_description)}/{MAX_SHORT_DESCRIPTION})"
        elif method == "setMyDescription":
            value = f"{len(config.description)} знаков из {MAX_DESCRIPTION}"
        elif method == "setMyCommands":
            value = ", ".join(f"/{cmd['command']}" for cmd in config.commands)
        elif method == "setChatMenuButton":
            value = "список команд"
        elif method == "setChatPhoto":
            value = str(step["photo"])
        else:
            value = ""
        lines.append(f"{index}. {step['title']}: {value}")
    return lines


# --------------------------------------------------------------------------
# multipart — для загрузки аватара (urlencode картинку не отправит)
# --------------------------------------------------------------------------


def encode_multipart(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = "----perfume" + secrets.token_hex(8)
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        chunks.append(str(value).encode() + b"\r\n")
    filename = file_path.name
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    chunks.append(f"--{boundary}\r\n".encode())
    chunks.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode()
    )
    chunks.append(f"Content-Type: {mime}\r\n\r\n".encode())
    chunks.append(file_path.read_bytes() + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def post_multipart(base: str, method: str, body: bytes, content_type: str, timeout: int = 60) -> dict:
    request = urllib.request.Request(base + method, data=body, headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method}: HTTP {error.code} {detail}") from error
    if not payload.get("ok"):
        raise RuntimeError(f"{method}: {payload.get('description')}")
    return payload["result"]


# --------------------------------------------------------------------------
# применение и проверка
# --------------------------------------------------------------------------


def apply_plan(api, plan: list[dict], verbose: bool = True) -> list[dict]:
    """Выполняет шаги. Падение одного шага не останавливает остальные."""
    results: list[dict] = []
    for index, step in enumerate(plan, start=1):
        method = step["method"]
        try:
            if "photo" in step:
                body, content_type = encode_multipart({}, "photo", Path(step["photo"]))
                post_multipart(api.base, method, body, content_type)
            else:
                api.call(method, **step["params"])
            results.append({"ok": True, "title": step["title"], "method": method, "error": ""})
            if verbose:
                print(f"✔ {index}. {step['title']}")
        except Exception as error:  # noqa: BLE001 — показываем причину человеческим текстом
            results.append({"ok": False, "title": step["title"], "method": method, "error": str(error)})
            if verbose:
                print(f"✖ {index}. {step['title']}: {error}")
    return results


def verify(api) -> dict:
    """Читает то, что реально стоит в профиле сейчас."""
    me = api.call("getMe")
    snapshot = {"username": me.get("username", ""), "id": me.get("id")}
    for method, key in (
        ("getMyName", "name"),
        ("getMyShortDescription", "short_description"),
        ("getMyDescription", "description"),
        ("getMyCommands", "commands"),
        ("getChatMenuButton", "menu_button"),
    ):
        try:
            result = api.call(method)
            snapshot[key] = result.get(key) if isinstance(result, dict) else result
        except Exception as error:  # noqa: BLE001
            snapshot[key] = f"не удалось получить: {error}"
    return snapshot


def print_snapshot(snapshot: dict) -> None:
    print()
    print("Что сейчас в Telegram:")
    print(f"  бот: @{snapshot.get('username')}")
    print(f"  имя: {snapshot.get('name')}")
    print(f"  краткое описание: {snapshot.get('short_description')}")
    description = str(snapshot.get("description") or "")
    print(f"  описание: {description[:100]}{'…' if len(description) > 100 else ''}")
    commands = snapshot.get("commands") or []
    if isinstance(commands, list) and commands:
        print("  команды: " + ", ".join(f"/{item.get('command')}" for item in commands))
    else:
        print(f"  команды: {commands}")
    print(f"  кнопка меню: {snapshot.get('menu_button')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Полная настройка профиля Telegram-бота по config.json")
    parser.add_argument("--dry-run", action="store_true", help="только показать план, ничего не менять")
    parser.add_argument("--photo", type=Path, default=None, help="файл аватара (png/jpg)")
    parser.add_argument("--verify-only", action="store_true", help="прочитать текущий профиль и выйти")
    parser.add_argument("--config", default=None, help="путь к config.json")
    args = parser.parse_args()

    _load_dotenv(Path(__file__).resolve().parent / ".env")

    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    format_error = token_format_error(token)
    if format_error:
        print(f"✖ Токен: {format_error}")
        print("  → @BotFather → /mybots → API Token, затем export TELEGRAM_TOKEN='...'")
        return 1

    try:
        config = load_config(args.config or os.environ.get("CONFIG_FILE") or None)
    except ConfigError as error:
        print(f"✖ Конфиг: {error}")
        return 1

    if args.photo is not None and not args.photo.exists():
        print(f"✖ Нет файла аватара: {args.photo}")
        return 1

    plan = build_plan(config, args.photo)
    print(f"План настройки бота «{config.brand}»:")
    for line in render_plan(config, plan):
        print("  " + line)
    print()

    if args.dry_run:
        print("--dry-run: ничего не изменено. Уберите флаг, чтобы применить.")
        return 0

    api = Api(token)
    if args.verify_only:
        try:
            print_snapshot(verify(api))
        except Exception as error:  # noqa: BLE001
            print(f"✖ Telegram не ответил: {error}")
            return 1
        return 0

    results = apply_plan(api, plan)
    failed = [item for item in results if not item["ok"]]
    print()
    if failed:
        print(f"Не применилось: {len(failed)} из {len(results)}.")
        for item in failed:
            print(f"  • {item['title']}: {item['error']}")
        print("Частые причины: бот заблокирован владельцем, неверный токен, слишком длинный текст,"
              " неподдерживаемый формат картинки (нужен png/jpg до 10 МБ).")
        return 1

    try:
        print_snapshot(verify(api))
    except Exception as error:  # noqa: BLE001
        print(f"Профиль применён, но проверить не удалось: {error}")
        return 0
    print()
    print("Профиль настроен. Дальше: python3 selfcheck.py && ./run_bot.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
