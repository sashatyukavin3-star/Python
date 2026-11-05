#!/usr/bin/env python3
"""selfcheck.py — проверка бота перед запуском: токен, конфиг, владелец, запись заявок.

Запуск:  python3 selfcheck.py
Токен и id читаются из окружения (или из .env рядом со скриптом):

    export TELEGRAM_TOKEN='123456789:AA...'
    export OWNER_CHAT_ID='123456789'

Скрипт ничего не публикует и не рассылает: только getMe и одно тестовое
сообщение владельцу (если задан OWNER_CHAT_ID). Токен не печатается.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from engine import Config, ConfigError, load_config
from quiz_bot import Api

NETWORK_MARKERS = ("SSL", "urlopen", "timed out", "Temporary failure", "Connection refused", "Name or service")

CHECK = "✔"
CROSS = "✖"
WARN = "!"


def mask_token(token: str) -> str:
    """Показывает только id бота, сам секрет — точками."""
    bot_id = token.split(":", 1)[0]
    return f"{bot_id}:••••••••" if bot_id else "••••••••"


def token_format_error(token: str) -> str | None:
    """Грубая проверка формы токена без обращения к сети."""
    if not token:
        return "токен пустой"
    if ":" not in token:
        return "в токене нет двоеточия — похоже, скопирована только часть"
    bot_id, _, secret = token.partition(":")
    if not bot_id.isdigit():
        return "до двоеточия должен быть числовой id бота"
    if len(secret) < 30:
        return "секретная часть слишком короткая — токен скопирован не полностью"
    if any(ch.isspace() for ch in token):
        return "в токене есть пробел или перенос строки"
    return None


def unused_scent_tags(config: Config) -> list[str]:
    """Теги ароматов, которых нет ни в одном варианте ответа: они никогда не совпадут."""
    question_tags: set[str] = set()
    for question in config.questions:
        for option in question["options"]:
            question_tags.update(option["tags"])
    scent_tags: set[str] = set()
    for scent in config.scents:
        scent_tags.update(scent["tags"])
    return sorted(scent_tags - question_tags)


def diagnose(api, config: Config, owner_chat_id: int | None = None, leads_file: Path | str | None = None,
             send_test_message: bool = True) -> list[dict]:
    """Возвращает список проверок: {ok, title, detail, hint}."""
    results: list[dict] = []

    def add(ok: bool, title: str, detail: str = "", hint: str = "") -> None:
        results.append({"ok": ok, "title": title, "detail": detail, "hint": hint})

    # 1. Токен и связь с Telegram
    try:
        me = api.call("getMe")
        username = me.get("username", "")
        add(True, "Токен рабочий", f"бот @{username}")
        if not username.endswith("bot"):
            add(False, "Имя бота не заканчивается на «bot»",
                f"@{username}",
                "Telegram требует username с окончанием bot; переименуйте в @BotFather")
    except Exception as error:  # noqa: BLE001 — показываем любую причину человеческим текстом
        message = str(error)
        if any(marker in message for marker in NETWORK_MARKERS):
            add(
                False,
                "Нет связи с Telegram",
                message[:200],
                "проверьте интернет на телефоне; если включён VPN — попробуйте без него и наоборот",
            )
        else:
            hint = "проверьте токен в @BotFather (/mybots → API Token)"
            if "Unauthorized" in message or "401" in message:
                hint = "токен отозван или скопирован с ошибкой: /revoke и новый токен в @BotFather"
            add(False, "Telegram не принял токен", message[:200], hint)

    # 2. Конфиг
    add(True, "Конфиг читается", f"{config.steps} вопросов, {len(config.scents)} ароматов, бренд «{config.brand}»")
    if config.steps < 3:
        add(False, "Слишком короткий квиз", f"{config.steps} вопроса",
            "3–6 вопросов — рабочий диапазон: короче не вовлекает, длиннее утомляет")
    if len(config.scents) < 2:
        add(False, "В конфиге меньше двух ароматов", f"{len(config.scents)}",
            "с одним ароматом подбор бессмысленен — добавьте хотя бы второй")
    empty_options = [q["text"] for q in config.questions if len(q["options"]) < 2]
    if empty_options:
        add(False, "В вопросе меньше двух вариантов", "; ".join(empty_options),
            "вариантов должно быть 2–8, иначе выбор не работает")
    dead_tags = unused_scent_tags(config)
    if dead_tags:
        add(False, "Теги, которые никогда не совпадут", ", ".join(dead_tags),
            "этих тегов нет ни в одном ответе: аромат по ним не выберется — исправьте config.json")

    # 3. Владелец получает заявки
    if owner_chat_id is None:
        add(False, "OWNER_CHAT_ID не задан", "",
            "заявки будут только в файле; чтобы получать их в Telegram, укажите свой id от @userinfobot")
    elif send_test_message:
        try:
            api.send_message(
                owner_chat_id,
                "Проверка связи: бот настроен и готов присылать заявки.\n"
                "Это тестовое сообщение, отвечать не нужно.",
            )
            add(True, "Владельцу можно писать", f"id {owner_chat_id}", "проверьте Telegram — сообщение должно быть там")
        except Exception as error:  # noqa: BLE001
            message = str(error)
            if any(marker in message for marker in NETWORK_MARKERS):
                add(False, "Не удалось написать владельцу", message[:200],
                    "сеть недоступна — проверьте интернет и VPN, затем повторите проверку")
            else:
                add(False, "Не удалось написать владельцу", message[:200],
                    "откройте бота и нажмите /start, затем снова запустите проверку; id должен быть ваш, не канала")

    # 4. Файл заявок
    if leads_file is not None:
        path = Path(leads_file).expanduser()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write("")
            add(True, "Файл заявок доступен для записи", str(path))
        except OSError as error:
            add(False, "Не могу писать файл заявок", f"{path}: {error}",
                "Termux: `termux-setup-storage` или укажите путь в домашней папке, например $HOME/leads.jsonl")

    return results


def _load_dotenv(path: Path) -> None:
    """Читает простой .env (KEY=VALUE), не перезаписывая уже заданные переменные."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def print_results(results: list[dict]) -> int:
    for item in results:
        mark = CHECK if item["ok"] else CROSS
        line = f"{mark} {item['title']}"
        if item["detail"]:
            line += f": {item['detail']}"
        print(line)
        if not item["ok"] and item["hint"]:
            print(f"   → {item['hint']}")
    failed = [item for item in results if not item["ok"]]
    print()
    if failed:
        print(f"Проблем: {len(failed)}. Исправьте их — иначе бот не заработает как надо.")
        return 1
    print("Всё в порядке. Запускайте: python3 quiz_bot.py")
    return 0


def main() -> int:
    here = Path(__file__).resolve().parent
    _load_dotenv(here / ".env")

    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    format_error = token_format_error(token)
    if format_error:
        print(f"{CROSS} Токен: {format_error}")
        print("   → @BotFather → /newbot (или /mybots → API Token), скопируйте строку целиком")
        print("   → затем: export TELEGRAM_TOKEN='...' и повторите проверку")
        return 1
    print(f"{CHECK} Токен по форме похож на настоящий: {mask_token(token)}")

    try:
        config = load_config(os.environ.get("CONFIG_FILE") or None)
    except ConfigError as error:
        print(f"{CROSS} Конфиг: {error}")
        print("   → исправьте config.json; он проверяется до запуска, чтобы бот не падал на клиенте")
        return 1

    raw_owner = os.environ.get("OWNER_CHAT_ID", "").strip()
    owner_chat_id: int | None = None
    if raw_owner:
        try:
            owner_chat_id = int(raw_owner)
        except ValueError:
            print(f"{WARN} OWNER_CHAT_ID не число: {raw_owner!r} — проверка уведомлений пропущена")

    api = Api(token)
    results = diagnose(
        api,
        config,
        owner_chat_id=owner_chat_id,
        leads_file=os.environ.get("LEADS_FILE", "leads.jsonl"),
    )
    return print_results(results)


if __name__ == "__main__":
    sys.exit(main())
