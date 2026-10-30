"""
Shared helpers for the Image Studio plugins (image_studio.py, image_edit.py).
The leading underscore keeps the plugin loader from treating this as a tool.
"""
from __future__ import annotations

import base64
import os
import platform
import re
import subprocess
import time
import urllib.parse
from pathlib import Path

import requests

NS = "image_studio"
_CF_MODEL = "@cf/black-forest-labs/flux-1-schnell"
_CF_URL = "https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/" + _CF_MODEL
_POLL_URL = "https://image.pollinations.ai/prompt/{prompt}"
AI_STUDIO_URL = "https://aistudio.google.com/prompts/new_chat"
_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")

SIZES = {
    "square":    (1024, 1024),
    "landscape": (1344, 768),
    "portrait":  (768, 1344),
}


# ── helpers ──────────────────────────────────────────────────────────────────

def cf_creds(values: dict | None = None) -> tuple[str, str]:
    if values is None:
        try:
            from memory.config_manager import get_plugin_config
            values = get_plugin_config(NS)
        except Exception:
            values = {}
    acct = str(values.get("cf_account_id") or "").strip()
    tok = str(values.get("cf_api_token") or "").strip()
    return acct, tok


def out_dir() -> Path:
    home = Path.home()
    for cand in (home / "Pictures", home / "OneDrive" / "Pictures"):
        if cand.is_dir():
            d = cand / "JARVIS"
            break
    else:
        d = home / "Pictures" / "JARVIS"
    d.mkdir(parents=True, exist_ok=True)
    return d


def slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_")
    return s[:40] or "image"


def ext_for(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def _looks_like_image(data: bytes) -> bool:
    return (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


def open_file(path: Path) -> None:
    try:
        if platform.system() == "Windows":
            os.startfile(str(path))                      # default photo viewer
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def log(player, text: str) -> None:
    if player is not None:
        try:
            player.write_log(text)
        except Exception:
            pass


# ── providers ────────────────────────────────────────────────────────────────

def cloudflare(prompt: str, w: int, h: int, acct: str, tok: str,
                steps: int = 6, timeout: float = 60.0) -> bytes:
    r = requests.post(
        _CF_URL.format(acct=acct),
        headers={"Authorization": f"Bearer {tok}"},
        json={"prompt": prompt[:2000], "steps": steps},
        timeout=timeout,
    )
    if r.status_code in (401, 403):
        raise RuntimeError("Cloudflare rejected the token or Account ID — "
                           "check both in ⚙ → PLUGIN SETTINGS")
    if r.status_code == 429:
        raise RuntimeError("today's free Cloudflare allowance is used up")
    ctype = r.headers.get("content-type", "")
    if ctype.startswith("image/") and _looks_like_image(r.content):
        return r.content
    try:
        js = r.json()
    except Exception:
        raise RuntimeError(f"Cloudflare answered HTTP {r.status_code}")
    if not js.get("success", True) or r.status_code >= 400:
        errs = js.get("errors") or []
        msg = (errs[0].get("message") if errs and isinstance(errs[0], dict) else "") or \
              f"HTTP {r.status_code}"
        raise RuntimeError(f"Cloudflare: {msg}")
    b64 = (js.get("result") or {}).get("image")
    if not b64:
        raise RuntimeError("Cloudflare returned no image")
    data = base64.b64decode(b64)
    if not _looks_like_image(data):
        raise RuntimeError("Cloudflare returned something that is not an image")
    return data


def pollinations(prompt: str, w: int, h: int, timeout: float = 90.0) -> bytes:
    url = _POLL_URL.format(prompt=urllib.parse.quote(prompt[:1500], safe=""))
    params = {"width": w, "height": h, "nologo": "true", "model": "flux",
              "seed": int(time.time()) % 1_000_000}
    last = None
    for attempt in range(2):                 # one retry — the free queue is bursty
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200 and _looks_like_image(r.content):
                return r.content
            if r.status_code in (402, 429):
                # Anonymous use is one image at a time, about 45 s apart.
                raise RuntimeError("the free Pollinations service only allows one "
                                   "image every 45 seconds or so, please ask again "
                                   "in a minute")
            last = RuntimeError(f"Pollinations answered HTTP {r.status_code}")
        except requests.RequestException as e:
            last = RuntimeError(f"could not reach Pollinations ({type(e).__name__})")
        time.sleep(3)
    raise last or RuntimeError("Pollinations failed")


def latest_image() -> Path | None:
    """The newest picture in Pictures\\JARVIS — what "edit that picture" means."""
    try:
        files = [p for p in out_dir().iterdir() if p.suffix.lower() in _IMG_EXT]
        return max(files, key=lambda p: p.stat().st_mtime) if files else None
    except Exception:
        return None
