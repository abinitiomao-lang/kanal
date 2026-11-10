"""YouTube Data API v3. Квота (2026-10): videos.insert — отдельный лимит 100 вызовов/сутки;
videos.update / thumbnails.set / videos.delete — по 50 ед. из 10 000 ед./сутки."""
import time
from datetime import timezone
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


def channel_info() -> dict:
    yt = _yt()
    return yt.channels().list(part="snippet,brandingSettings,status,statistics,contentDetails",
                              mine=True).execute()["items"][0]


def channel_sync() -> dict:
    """Ставит описание, ключевые слова и язык канала из config.yaml (название не трогает)."""
    C = CFG["channel"]
    yt = _yt()
    ch = yt.channels().list(part="brandingSettings", id=C["id"]).execute()["items"][0]["brandingSettings"]["channel"]
    ch.update(description=C["about"][:1000], defaultLanguage=C["language"],
              keywords=" ".join(f'"{k}"' if " " in k else k for k in C["keywords"]))
    out = yt.channels().update(part="brandingSettings",
                               body={"id": C["id"], "brandingSettings": {"channel": ch}}).execute()
    return out["brandingSettings"]["channel"]


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


def schedule(video_id: str, when) -> dict:
    """Ставит отложенную публикацию: видео private до `when` (aware datetime), потом YouTube сам откроет."""
    yt = _yt()
    cur = yt.videos().list(part="status", id=video_id).execute()["items"][0]["status"]
    cur.update(privacyStatus="private", publishAt=when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    try:  # берём ответ update: videos.list сразу после него отдаёт устаревший статус
        out = yt.videos().update(part="status", body={"id": video_id, "status": cur}).execute()["status"]
    except HttpError as e:
        print(f"WARN schedule {video_id}: {e}")
        return {}
    print(f"schedule {video_id}: {out.get('privacyStatus')} until {out.get('publishAt')}")
    return out


def delete(video_id: str) -> None:
    try:
        _yt().videos().delete(id=video_id).execute()
    except HttpError as e:
        print(f"WARN delete: {e.status_code}")


def list_all() -> list[dict]:
    """Все видео канала (включая private) из плейлиста загрузок."""
    yt = _yt()
    check_channel(yt)
    ch = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    out, token = [], None
    while True:
        r = yt.playlistItems().list(part="snippet,status", playlistId=uploads, maxResults=50, pageToken=token).execute()
        out += [{"id": i["snippet"]["resourceId"]["videoId"], "title": i["snippet"]["title"],
                 "privacy": i["status"]["privacyStatus"]} for i in r["items"]]
        token = r.get("nextPageToken")
        if not token:
            return out


def stats(ids: list[str]) -> dict[str, dict]:
    """Просмотры, лайки, комментарии и приватность по videoId (батчами по 50; удалённых в ответе нет)."""
    yt = _yt()
    out = {}
    for i in range(0, len(ids), 50):
        r = yt.videos().list(part="statistics,status", id=",".join(ids[i:i + 50])).execute()
        for v in r["items"]:
            s = v.get("statistics", {})
            out[v["id"]] = {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                            "comments": int(s.get("commentCount", 0)), "privacy": v["status"]["privacyStatus"]}
    return out


def channel_stats() -> dict:
    s = _yt().channels().list(part="statistics", id=CFG["channel"]["id"]).execute()["items"][0]["statistics"]
    return {"subs": int(s.get("subscriberCount", 0)), "views": int(s.get("viewCount", 0)),
            "videos": int(s.get("videoCount", 0))}


def playlists_list() -> list[dict]:
    """Все плейлисты канала (включая private) с числом видео."""
    yt = _yt()
    check_channel(yt)
    out, token = [], None
    while True:
        r = yt.playlists().list(part="snippet,status,contentDetails", mine=True, maxResults=50,
                                pageToken=token).execute()
        out += [{"id": p["id"], "title": p["snippet"]["title"], "privacy": p["status"]["privacyStatus"],
                 "count": p["contentDetails"]["itemCount"]} for p in r["items"]]
        token = r.get("nextPageToken")
        if not token:
            return out


def _playlist_videos(yt, pid: str) -> set[str]:
    out, token = set(), None
    while True:
        r = yt.playlistItems().list(part="contentDetails", playlistId=pid, maxResults=50, pageToken=token).execute()
        out |= {i["contentDetails"]["videoId"] for i in r["items"]}
        token = r.get("nextPageToken")
        if not token:
            return out


def _wanted_playlists(items: dict) -> list[dict]:
    """Плейлисты из config + по одному на многочастную серию длинных видео; у каждого — список videoId."""
    from . import classics, writer
    names = {b["id"]: b["book"] for b in writer.load_series()}
    names |= {f"classic:{b['id']}": b["title"] for b in classics.load()}
    pub = sorted((it for it in items.values() if it.get("status") == "published" and it.get("public_id")),
                 key=lambda it: it.get("created", ""))
    out = []
    for p in CFG.get("playlists", []):
        def ok(it, p=p):
            ser = it.get("series") or ""
            return (it.get("kind") == p["kind"]
                    and (not p.get("books") or it.get("book") in p["books"])
                    and (not p.get("series_prefix") or ser.startswith(p["series_prefix"]))
                    and not (p.get("exclude_series_prefix") and ser.startswith(p["exclude_series_prefix"])))
        out.append({"title": p["title"], "description": p["description"],
                    "videos": [it["public_id"] for it in pub if ok(it)]})
    for ser in sorted({it["series"] for it in pub if it.get("kind") == "long" and it.get("series")}):
        vids = [it["public_id"] for it in pub if it.get("series") == ser]
        if len(vids) < 2:  # плейлист серии — когда вышла хотя бы вторая часть
            continue
        book = names.get(ser, ser)
        out.append({"title": f"{book} — Full Series (Fall Asleep Learning)"[:150],
                    "description": f"All parts of {book}, in order. Calm narration to fall asleep to.",
                    "videos": vids})
    return out


def playlists_sync(items: dict) -> None:
    """Создаёт недостающие плейлисты (public) и добавляет в них опубликованные видео. Ничего не удаляет."""
    yt = _yt()
    check_channel(yt)
    have = {p["title"]: p["id"] for p in playlists_list()}
    for p in _wanted_playlists(items):
        if not p["videos"]:
            continue
        pid = have.get(p["title"])
        if not pid:
            pid = yt.playlists().insert(part="snippet,status", body={
                "snippet": {"title": p["title"], "description": p["description"],
                            "defaultLanguage": CFG["channel"]["language"]},
                "status": {"privacyStatus": "public"}}).execute()["id"]
            print(f"создан плейлист {pid}: {p['title']}")
            time.sleep(10)  # свежий плейлист API отдаёт не сразу (404 playlistNotFound)
        present = _playlist_videos(yt, pid) if p["title"] in have else set()
        for v in p["videos"]:
            if v not in present:
                body = {"snippet": {"playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": v}}}
                for attempt in range(5):
                    try:
                        yt.playlistItems().insert(part="snippet", body=body).execute()
                        break
                    except HttpError as e:
                        if e.status_code != 404 or attempt == 4:
                            raise
                        time.sleep(15)
                print(f"  + {v} → {p['title']}")


def wipe(really: bool) -> None:
    """Печатает все видео канала; при really=True удаляет их и снимает их с очереди в state."""
    from . import state
    vids = list_all()
    print(f"на канале {len(vids)} видео")
    for v in vids:
        print(f"  {v['id']}  {v['privacy']:9}  {v['title']}")
    if not really:
        print("dry run — ничего не удалено")
        return
    for v in vids:
        delete(v["id"])
        print(f"удалено {v['id']}")
    for k, it in state.load()["items"].items():
        if it["status"] not in ("published", "rejected"):
            state.set_item(k, status="rejected")


def edit(match: str, title: str, description: str, tags: list[str]) -> None:
    """Меняет название/описание/теги видео по id или по куску названия. Пустые поля не трогает."""
    every = list_all()
    vids = [v for v in every if match == v["id"] or match.lower() in v["title"].lower()]
    if len(vids) != 1:
        for v in every:
            print(f"  {v['id']}  {v['privacy']:9}  {v['title']}")
        raise SystemExit(f"нашлось {len(vids)} видео по «{match}»")
    yt = _yt()
    vid = vids[0]["id"]
    sn = yt.videos().list(part="snippet", id=vid).execute()["items"][0]["snippet"]
    body = {k: sn[k] for k in ("title", "description", "categoryId", "tags", "defaultLanguage",
                               "defaultAudioLanguage") if k in sn}
    if title:
        body["title"] = title[:100]
    if description:
        body["description"] = description[:4900]
    if tags:
        body["tags"] = _tags(tags)
    yt.videos().update(part="snippet", body={"id": vid, "snippet": body}).execute()
    print(f"обновлено {vid} ({vids[0]['privacy']}): {body['title']}")
