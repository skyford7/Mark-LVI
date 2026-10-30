"""
Image edit hand-off — "edit that picture", "make the background a beach".

Free APIs are poor at editing an existing photo, while Google AI Studio does it
well and free on the website. So this opens AI Studio in the browser, copies
the instruction to the clipboard and highlights the photo in File Explorer, so
editing is: drag the photo in, Ctrl+V, Enter.
"""
from __future__ import annotations

import platform
import subprocess
import webbrowser
from pathlib import Path

from plugins import _image_common as ic

PLUGIN = {
    "name": "edit_image",
    "description": (
        "Helps the user EDIT or CHANGE an existing picture or photo (change the "
        "background, remove an object, change colours, restyle it, add text). "
        "Opens Google AI Studio in the browser, which edits images for free, "
        "copies the instruction to the clipboard and highlights the photo in "
        "File Explorer so the user can drag it in. With no file_path it uses the "
        "most recent picture JARVIS generated. Write `instruction` in ENGLISH as "
        "a clear edit request, e.g. 'Replace the background with a sunny beach'. "
        "For creating a brand-new picture use generate_image instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "instruction": {"type": "STRING",
                            "description": "The edit to make, in English"},
            "file_path": {"type": "STRING",
                          "description": "Full path of the photo to edit, if the user named one. "
                                         "Leave empty for the last generated picture."},
        },
        "required": ["instruction"],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    instruction = str(parameters.get("instruction") or "").strip()
    target = str(parameters.get("file_path") or "").strip()
    if not target and player is not None:
        target = str(getattr(player, "current_file", "") or "")  # file dropped in the HUD
    path = Path(target) if target else ic.latest_image()
    if path is not None and not path.is_file():
        path = ic.latest_image()

    copied = False
    if instruction:
        try:
            import pyperclip
            pyperclip.copy(instruction)
            copied = True
        except Exception:
            pass
    try:
        webbrowser.open(ic.AI_STUDIO_URL)
    except Exception:
        pass
    if path is not None and platform.system() == "Windows":
        try:
            subprocess.Popen(["explorer", "/select,", str(path)])
        except Exception:
            pass

    ic.log(player, f"SYS: Image edit handed to Google AI Studio"
                   + (f" — {path}" if path else ""))
    steps = ["I've opened Google AI Studio in your browser"]
    if path is not None:
        steps.append("and highlighted the picture in File Explorer. Drag it into the chat box")
    else:
        steps.append(". Attach your photo with the plus button")
    if copied:
        steps.append("then press Control V to paste your instruction and send it")
    return ", ".join(steps).replace(", .", ".") + ". Editing there is free."
