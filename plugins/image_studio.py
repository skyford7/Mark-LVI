"""
Image Studio — free AI image generation for JARVIS.

  "Make me a picture of a black and gold logo for SkyFord"
  "Draw a cat astronaut, landscape"

Two free providers, tried in order:

  1. Cloudflare Workers AI (FLUX.1 schnell) — when an Account ID and API token
     are filled in under ⚙ → PLUGIN SETTINGS → IMAGE STUDIO. A free Cloudflare
     account includes a daily allowance; on the free plan it simply stops for
     the day when that runs out, it never charges.
  2. Pollinations.ai — no account, no key. Used when Cloudflare is not set up,
     or when Cloudflare fails (allowance used up, network trouble).

Every image is saved to Pictures\\JARVIS, previewed on the HUD and opened in the
default photo viewer. Editing an existing photo is image_edit.py.
"""
from __future__ import annotations

import time

from plugins import _image_common as ic

PLUGIN = {
    "name": "generate_image",
    "description": (
        "Creates a NEW picture from a text description using a free AI image "
        "generator, saves it to the user's Pictures\\JARVIS folder, shows it on "
        "screen and opens it. Call when the user says (in any language): make / "
        "create / generate / draw / design a picture, image, logo, poster, "
        "wallpaper, illustration, icon, etc. Write `prompt` in ENGLISH and make "
        "it detailed: subject, style, colours, lighting, composition — expand a "
        "short request into a rich visual description. Takes 5-30 seconds; tell "
        "the user you are creating it before calling. To CHANGE an existing "
        "photo use edit_image instead. Not for looking at the screen "
        "(screen_process)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "prompt": {"type": "STRING",
                       "description": "Detailed English description of the image to create"},
            "shape": {"type": "STRING",
                      "description": "square (default), landscape (wide, e.g. wallpaper or banner) "
                                     "or portrait (tall, e.g. phone wallpaper or poster)"},
        },
        "required": ["prompt"],
    },
}


def _test(values: dict):
    """The TEST button in ⚙ → PLUGIN SETTINGS: one small real image."""
    acct, tok = ic.cf_creds(values)
    if not acct or not tok:
        return False, "Fill in both the Account ID and the API Token first."
    try:
        t0 = time.time()
        data = ic.cloudflare("a small red apple on a white table", 512, 512,
                             acct, tok, steps=4, timeout=45)
        return True, (f"Working — made a test image in {time.time() - t0:.1f}s "
                      f"({len(data) // 1024} KB).")
    except Exception as e:
        return False, str(e)


PLUGIN_SETTINGS = {
    "namespace": ic.NS,
    "title": "IMAGE STUDIO — Cloudflare (optional, more reliable)",
    "fields": [
        {"key": "cf_account_id", "label": "Cloudflare Account ID", "type": "text",
         "placeholder": "Workers AI → Use REST API → Get Account ID"},
        {"key": "cf_api_token", "label": "Cloudflare API Token", "type": "password",
         "placeholder": "Workers AI → Create a Workers AI API Token"},
    ],
    "action": {"label": "TEST CLOUDFLARE", "run": _test},
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    prompt = str(parameters.get("prompt") or "").strip()
    if not prompt:
        return "Sir, I need a description of the picture you want."
    shape = str(parameters.get("shape") or "square").strip().lower()
    w, h = ic.SIZES.get(shape, ic.SIZES["square"])

    acct, tok = ic.cf_creds()
    data, used, problems = None, "", []
    if acct and tok:
        try:
            data, used = ic.cloudflare(prompt, w, h, acct, tok), "Cloudflare"
        except Exception as e:
            problems.append(str(e))
    if data is None:
        try:
            data, used = ic.pollinations(prompt, w, h), "Pollinations"
        except Exception as e:
            problems.append(str(e))

    if data is None:
        why = "; ".join(problems) or "unknown error"
        ic.log(player, f"SYS: Image generation failed — {why}")
        return f"Sir, I could not create the image: {why}."

    name = f"{time.strftime('%Y%m%d_%H%M%S')}_{ic.slug(prompt)}{ic.ext_for(data)}"
    path = ic.out_dir() / name
    path.write_bytes(data)

    if player is not None:
        try:
            player.show_camera_frame(data)       # quick preview on the HUD
        except Exception:
            pass
    ic.open_file(path)
    ic.log(player, f"SYS: Image saved ({used}) → {path}")
    note = (f" Cloudflare didn't work ({problems[0]}), so I used Pollinations instead."
            if problems and used == "Pollinations" else "")
    return (f"Done. The image is open on screen and saved in your Pictures, "
            f"JARVIS folder.{note}")
