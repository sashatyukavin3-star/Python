#!/usr/bin/env bash
# Запуск бота одной командой: ./run_bot.sh
# Настройки берутся из окружения или из файла .env рядом со скриптом.
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Нет python3. В Termux: pkg install python"
  exit 1
fi

if [ -z "${TELEGRAM_TOKEN:-}" ]; then
  echo "Нет TELEGRAM_TOKEN."
  echo "1) @BotFather → /newbot → скопируйте токен"
  echo "2) создайте .env по образцу .env.example и впишите токен"
  echo "3) запустите снова: ./run_bot.sh"
  exit 1
fi

# В Termux не даём Android усыплять процесс
if [ -n "${PREFIX:-}" ] && [ -d "${PREFIX:-}" ] && command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock && echo "wakelock включён — Android не будет усыплять бота"
fi

echo "Проверка настроек…"
python3 selfcheck.py || echo "Проверка нашла проблемы, но запускаю: часть из них не критична."
echo
echo "Для остановки: Ctrl+C"
exec python3 quiz_bot.py
