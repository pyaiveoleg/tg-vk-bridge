#!/usr/bin/env bash
# run.sh — запуск моста на виртуалке.
# Работает из любой папки: все пути считаются от расположения самого скрипта,
# поэтому docker-compose.yml, .env и код находятся независимо от текущего cwd.
set -euo pipefail

# каталог, где лежит этот скрипт (рядом с docker-compose.yml, .env и кодом)
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# выбираем доступный вариант compose (plugin "docker compose" или старый "docker-compose")
if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    echo "Не найден docker compose. Поставь Docker (см. README)." >&2
    exit 1
fi

echo "→ сборка и запуск в $DIR"
$COMPOSE up -d --build

echo "→ статус:"
$COMPOSE ps

echo
echo "Готово. Логи:   $COMPOSE -f \"$DIR/docker-compose.yml\" logs -f"
echo "Показываю логи (Ctrl-C — выйти; контейнер продолжит работать)…"
$COMPOSE logs -f
