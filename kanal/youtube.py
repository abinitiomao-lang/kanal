"""YouTube Data API v3. Квота (2026-10): videos.insert — отдельный лимит 100 вызовов/сутки;
videos.update / thumbnails.set / videos.delete — по 50 ед. из 10 000 ед./сутки."""
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from .config import CFG, secret

SCOPES = ["https://www.googleapis.com/auth/youtube"]


def _yt():
    creds = Credentials(
        None, refresh_token=secret("YT_REFRESH_TOKEN"), token_uri="https://oauth2.googleapis.com/token",
        client_id=secret("YT_CLIENT_ID"), client_secret=secret("YT_CLIENT_SECRET"), scopes=SCOPES)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def check_channel(yt) -> None:
    """Токен должен принадлежать нашему каналу — иначе видео уйдёт не туда (brand account)."""
    items = yt.channels().list(part="id,snippet", mine=True).execute().get("items", [])
    ids = [c["id"] for c in items]
    if CFG["channel"]["id"] not in ids:
        raise RuntimeError(f"OAuth-токен выдан для канала {ids}, а нужен {CFG['channel']['id']}. "
                           "Перевыпусти YT_REFRESH_TOKEN, выбрав правильный канал.")


def upload(path: Path, title: str, description: str, tags: list[str]) -> str:
    """Загружает видео как PRIVATE. Возвращает videoId."""
    C = CFG["channel"]
    yt = _yt()
    check_channel(yt)
    body = {
        "snippet": {"title": title[:100], "description": description[:4900], "tags": _tags(tags),
                    "categoryId": C["category_id"], "defaultLanguage": C["language"],
                    "defaultAudioLanguage": C["language"]},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": C["made_for_kids"],
                   "containsSyntheticMedia": C["synthetic_media"], "embeddable": True},
    }
    media = MediaFileUpload(str(path), chunksize=64 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media, notifySubscribers=True)
    resp = None
    while resp is None:
        status, resp = req.next_chunk(num_retries=5)
        if status:
            print(f"upload {int(status.progress() * 100)}%")
    return resp["id"]


def _tags(tags: list[str]) -> list[str]:
    out, total = [], 0
    for t in tags:
        t = t.replace("<", "").replace(">", "").strip()
        if t and total + len(t) + 3 < 480:
            out.append(t)
            total += len(t) + 3
    return out


def set_thumbnail(video_id: str, image: Path) -> bool:
    try:
        _yt().thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(image), mimetype="image/jpeg")).execute()
        return True
    except HttpError as e:  # канал не верифицирован по телефону → кастомные превью недоступны
        print(f"WARN thumbnail: {e.status_code}")
        return False


def status(video_id: str) -> dict:
    items = _yt().videos().list(part="status,processingDetails", id=video_id).execute()["items"]
    return items[0] if items else {}


def make_public(video_id: str) -> str:
    """Возвращает итоговый privacyStatus (у неаудированного проекта останется private)."""
    yt = _yt()
    cur = yt.videos().list(part="status", id=video_id).execute()["items"][0]["status"]
    cur["privacyStatus"] = "public"
    yt.videos().update(part="status", body={"id": video_id, "status": cur}).execute()
    return yt.videos().list(part="status", id=video_id).execute()["items"][0]["status"]["privacyStatus"]


def delete(video_id: str) -> None:
    try:
        _yt().videos().delete(id=video_id).execute()
    except HttpError as e:
        print(f"WARN delete: {e.status_code}")
