"""Локальная авторизация YouTube: секрет клиента берётся из env, refresh token сразу уходит в GitHub Secrets.

    YT_CLIENT_ID=... YT_CLIENT_SECRET=... .venv/bin/python scripts/youtube_auth.py

Токены не печатаются.
"""
import os
import subprocess

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube"]
cfg = {"installed": {"client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
                     "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                     "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}}
flow = InstalledAppFlow.from_client_config(cfg, SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline", open_browser=os.environ.get("NO_BROWSER") is None,
                              success_message="Готово! Можно закрыть вкладку и вернуться в Claude Code.")
ch = build("youtube", "v3", credentials=creds).channels().list(part="snippet", mine=True).execute()["items"]
print("Авторизован канал:", [(c["id"], c["snippet"]["title"]) for c in ch])
for name, val in (("YT_CLIENT_SECRET", os.environ["YT_CLIENT_SECRET"]), ("YT_REFRESH_TOKEN", creds.refresh_token)):
    subprocess.run(["gh", "secret", "set", name, "-R", "abinitiomao-lang/kanal"], input=val, text=True, check=True)
print("Секреты YT_CLIENT_SECRET и YT_REFRESH_TOKEN сохранены в GitHub.")
