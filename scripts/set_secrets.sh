#!/usr/bin/env bash
# Интерактивно кладёт секреты в GitHub Secrets (значения не попадают в историю shell и в репо).
# Использование: scripts/set_secrets.sh abinitiomao-lang/kanal
set -euo pipefail
REPO="$1"
for name in ANTHROPIC_API_KEY FAL_KEY ELEVENLABS_API_KEY TELEGRAM_BOT_TOKEN TELEGRAM_OWNER_ID \
            YT_CLIENT_ID YT_CLIENT_SECRET YT_REFRESH_TOKEN PEXELS_API_KEY; do
  read -r -s -p "$name (Enter — пропустить): " val; echo
  [ -n "$val" ] && printf '%s' "$val" | gh secret set "$name" -R "$REPO"
done
echo "Готово. Проверка: gh secret list -R $REPO"
