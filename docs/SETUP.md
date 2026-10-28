# Ручные шаги (один раз)

Все ключи — только в GitHub Secrets. Никогда не присылай их в чат и не коммить.
Самый удобный способ положить секреты: `scripts/set_secrets.sh abinitiomao-lang/kanal` (спрашивает значения скрыто).

| # | Что | Секрет(ы) | Время |
|---|-----|-----------|-------|
| 1 | Telegram-бот | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_ID` | 3 мин |
| 2 | Anthropic API | `ANTHROPIC_API_KEY` | 3 мин |
| 3 | fal.ai (картинки) | `FAL_KEY` | 3 мин |
| 4 | ElevenLabs Creator | `ELEVENLABS_API_KEY` (+ voice id в config.yaml) | 5 мин |
| 5 | Pexels (опционально, бесплатно) | `PEXELS_API_KEY` | 2 мин |
| 6 | Google Cloud + YouTube OAuth | `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` | 15 мин |
| 7 | Канал: верификация по телефону | — | 2 мин |

## 1. Telegram
1. @BotFather → `/newbot` → имя и username → получишь токен → `TELEGRAM_BOT_TOKEN`.
2. Напиши своему боту `/start` (иначе он не сможет тебе писать).
3. @userinfobot → пришлёт твой числовой id → `TELEGRAM_OWNER_ID`. Бот реагирует только на этот id.

## 2. Anthropic
console.anthropic.com → API Keys → Create. Поставь лимит расходов в Billing → Limits ($15/мес — с запасом).

## 3. fal.ai
fal.ai → Dashboard → Keys → Add key → `FAL_KEY`. Пополни $10 (хватит на ~1500 картинок).

## 4. ElevenLabs
Подписка Creator. Profile → API Keys → создай ключ с правом Text to Speech → `ELEVENLABS_API_KEY`.
Выбери голос в Voice Library (энергичный, для шортсов), скопируй Voice ID → пришли мне, я пропишу в `config.yaml`.

## 5. Pexels
pexels.com/api → Your API Key → `PEXELS_API_KEY`. Без него шортсы будут только из сгенерированных картинок.

## 6. YouTube Data API
1. console.cloud.google.com → новый проект `kanal`.
2. APIs & Services → Library → **YouTube Data API v3** → Enable.
3. OAuth consent screen (Google Auth Platform): External; имя приложения `kanal uploader`; email поддержки — твой;
   Privacy policy URL: `https://github.com/abinitiomao-lang/kanal/blob/main/docs/PRIVACY.md`.
   Scopes: `.../auth/youtube`. Test users: добавь свой Google-аккаунт.
4. **Audience → Publish app → In production.** Важно: в режиме Testing refresh-токен умирает через 7 дней.
   Верификацию Google для личного использования проходить не нужно — при входе будет предупреждение
   «Google hasn’t verified this app» → Advanced → Go to kanal uploader.
5. Credentials → Create credentials → OAuth client ID → **Desktop app** → Download JSON.
6. Локально: `.venv/bin/python scripts/get_youtube_token.py ~/Downloads/client_secret_XXX.json`
   → войди аккаунтом канала, выбери **сам канал** (если он brand account) → скрипт напечатает 3 значения.
7. Удали скачанный JSON.

## 7. Канал
- YouTube → Settings → Channel → Feature eligibility → верифицируй телефон (нужно для кастомных превью
  и видео длиннее 15 минут — без этого 3-часовое видео не загрузится).
- Название канала меняешь вручную после выбора в боте (API не умеет).

## Квота и аудит
- Загрузки: отдельный лимит 100 `videos.insert`/сутки (нам нужно ≤ 4). Остальное — 10 000 ед./сутки, хватает с запасом.
- **Пока проект не прошёл аудит, все загрузки через API залочены как private** — их нельзя сделать публичными,
  только перезалить. Поэтому сейчас `pre_audit_mode: true`: «Опубликовать» в боте присылает файл
  и готовые заголовок/описание/теги для ручной загрузки. Как пройти аудит — `docs/AUDIT.md`.
