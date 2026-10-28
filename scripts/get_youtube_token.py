"""Запускается ОДИН раз локально: получает refresh token для YouTube Data API.

    .venv/bin/python scripts/get_youtube_token.py path/to/client_secret.json

Откроется браузер → войди в Google-аккаунт канала → выбери нужный канал → разреши доступ.
Скрипт напечатает три значения для GitHub Secrets. Никуда их больше не сохраняй.
"""
import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube"]

path = sys.argv[1]
flow = InstalledAppFlow.from_client_secrets_file(path, SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
client = json.load(open(path))["installed"]
print("\nДобавь в GitHub → Settings → Secrets and variables → Actions:\n")
print(f"YT_CLIENT_ID      = {client['client_id']}")
print(f"YT_CLIENT_SECRET  = {client['client_secret']}")
print(f"YT_REFRESH_TOKEN  = {creds.refresh_token}")
