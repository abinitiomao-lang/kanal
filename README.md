# kanal — автоматический sleep-learning YouTube-канал

Длинные (2–3 ч, раз в 3 дня) и шортсы (3/день) про школьные науки. Всё крутится на GitHub Actions,
управление — через Telegram-бота. Настройка: [docs/SETUP.md](docs/SETUP.md). Аудит API: [docs/AUDIT.md](docs/AUDIT.md).

## Как это работает
```
daily.yml (06:00 UTC) ──► бот присылает 5 тем шортсов (+ 5 тем длинного раз в 3 дня)
bot.yml (каждые 10 мин) ─► нажатие кнопки ──► gh workflow run short.yml / long.yml
short.yml:  сценарий (Haiku) → фактчек (Sonnet) → ElevenLabs Flash → картинки/сток → монтаж → YouTube private → апрув
long.yml:   prepare: план + 12 глав + фактчек каждой + картинки + превью
            render ×6 параллельно: Kokoro TTS + Ken Burns/звёзды по главам
            assemble: склейка без перекодирования + эмбиент + затухание → YouTube private → апрув
```
Апрув: «Опубликовать / Отклонить / Переделать (с комментарием)». До аудита API публикация ручная — бот отдаёт файл и метаданные.

## Где что
- `config.yaml` — все настройки (длительности, модели, бюджет, голоса).
- `kanal/` — код. `writer.py` сценарии и фактчек, `render.py` ffmpeg, `pipeline.py` сборка, `bot.py` бот.
- Ветка `state` — состояние (очередь, расходы, индекс картинок). Пишется только ботом.
- Релиз `library` — сгенерированные картинки для переиспользования.

## Бюджет (оценка на месяц)
| Статья | $ |
|---|---|
| ElevenLabs Creator | 22 |
| Anthropic (Haiku 5.5 пишет, Sonnet 5.5 проверяет) | ~3–6 |
| fal.ai FLUX schnell (~1000 картинок) | ~6 |
| GitHub Actions (публичный репо), Kokoro, Pexels | 0 |
| **Итого** | **~31–34** из 50 |

Расходы пишутся в `state.json`; `/status` в боте показывает месяц; при 80% лимита бот предупреждает, при 100% — останавливает производство.

## Локальный запуск
```
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
FFMPEG=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg .venv/bin/python -m kanal.cli short <item_id>
```
