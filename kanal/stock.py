"""Pexels — бесплатный сток (лицензия Pexels: можно в коммерческих видео без атрибуции). Опционально."""
import os
from pathlib import Path

import requests


def enabled() -> bool:
    return bool(os.environ.get("PEXELS_API_KEY"))


def vertical_clip(query: str, out: Path, min_seconds: float) -> Path | None:
    try:
        r = requests.get("https://api.pexels.com/videos/search",
                         headers={"Authorization": os.environ["PEXELS_API_KEY"]},
                         params={"query": query, "orientation": "portrait", "per_page": 8, "size": "medium"},
                         timeout=30)
        r.raise_for_status()
        for v in r.json().get("videos", []):
            if v["duration"] < min_seconds:
                continue
            files = sorted((f for f in v["video_files"] if f.get("height") and f["height"] >= 1280
                            and f["height"] > f["width"]), key=lambda f: f["height"])
            if files:
                data = requests.get(files[0]["link"], timeout=120).content
                out.write_bytes(data)
                return out
    except Exception as e:
        print(f"WARN pexels: {type(e).__name__}")
    return None
