"""Картинки: FLUX schnell через fal.ai + библиотека для переиспользования между видео.

Файлы библиотеки лежат ассетами релиза `library` (публичные — это тот же контент, что уйдёт на YouTube),
индекс (промпт, ключевые слова, url) — в state.json.
"""
import io
import re
import subprocess
import uuid
from pathlib import Path

import requests
from PIL import Image

from . import costs, state
from .config import CFG, secret

STOP = set("a an the of and or in on with to from at by for is are its it into over under".split())
RELEASE = "library"
SIZES = {"landscape": (1824, 1024), "portrait": (1024, 1824)}


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", s.lower()) if w not in STOP and len(w) > 2}


def _score(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / max(1, len(wa | wb))


class Library:
    def __init__(self):
        self.index = state.load()["images"]
        self.new: list[tuple[dict, Path]] = []
        self.spent = 0.0
        self.used: set[str] = set()

    def find(self, prompt: str, orient: str, min_score: float = 0.4) -> dict | None:
        best, best_s = None, min_score
        for e in self.index:
            if e["orient"] != orient or e["id"] in self.used:
                continue
            s = _score(prompt, e["prompt"])
            if s > best_s:
                best, best_s = e, s
        return best

    def get(self, prompt: str, orient: str, out: Path, allow_reuse: bool = True) -> Path:
        hit = self.find(prompt, orient) if allow_reuse else None
        if hit:
            self.used.add(hit["id"])
            r = requests.get(hit["url"], timeout=120)
            r.raise_for_status()
            out.write_bytes(r.content)
            return out
        return self.generate(prompt, orient, out)

    def generate(self, prompt: str, orient: str, out: Path) -> Path:
        w, h = SIZES[orient]
        full = f"{prompt}. {CFG['images']['style']}"
        r = requests.post(
            f"https://fal.run/{CFG['images']['model']}",
            headers={"Authorization": f"Key {secret('FAL_KEY')}"},
            json={"prompt": full, "image_size": {"width": w, "height": h}, "num_inference_steps": 4,
                  "num_images": 1, "enable_safety_checker": True, "output_format": "jpeg"},
            timeout=180,
        )
        if r.status_code != 200:
            raise RuntimeError(f"fal.ai {r.status_code}: {r.text[:300]}")
        img_url = r.json()["images"][0]["url"]
        img = Image.open(io.BytesIO(requests.get(img_url, timeout=120).content)).convert("RGB")
        img.save(out, "JPEG", quality=92)
        self.spent += CFG["images"]["usd_per_image"]
        entry = {"id": uuid.uuid4().hex[:12], "prompt": prompt, "orient": orient}
        self.new.append((entry, out))
        self.used.add(entry["id"])
        return out

    def commit(self, note: str) -> None:
        """Загружает новые картинки в релиз library и дописывает индекс. Ошибки не валят пайплайн."""
        if self.spent:
            costs.add("fal", self.spent, note, images=len(self.new))
        if not self.new:
            return
        try:
            subprocess.run(["gh", "release", "view", RELEASE], capture_output=True, check=True)
        except subprocess.CalledProcessError:
            subprocess.run(["gh", "release", "create", RELEASE, "--title", "Image library",
                            "--notes", "Generated images reused between videos."], check=True)
        files = []
        for entry, path in self.new:
            named = path.with_name(f"{entry['id']}.jpg")
            named.write_bytes(path.read_bytes())
            files.append(str(named))
        try:
            subprocess.run(["gh", "release", "upload", RELEASE, *files, "--clobber"], check=True)
        except subprocess.CalledProcessError as e:
            print(f"WARN: library upload failed: {e}")
            return
        repo = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                              capture_output=True, text=True).stdout.strip()
        for entry, _ in self.new:
            entry["url"] = f"https://github.com/{repo}/releases/download/{RELEASE}/{entry['id']}.jpg"

        def fn(st):
            st["images"].extend(e for e, _ in self.new)
        state.update(fn, f"library +{len(self.new)}")
        self.new = []
