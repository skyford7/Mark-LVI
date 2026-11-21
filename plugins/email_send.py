"""
Email — write and send (personal or business).

  "Email jane@acme.com, subject Order update, say the parcel ships Monday"
  "Reply to that and tell them thanks"

Always previews first and waits for a spoken yes before anything leaves the
outbox, so a misheard address or line can't send on its own.
"""
from __future__ import annotations

import re

from plugins import _email_common as em

_EMAIL_RX = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

PLUGIN = {
    "name": "email_send",
    "description": (
        "Writes and sends an email, or replies to the last email read. Use when "
        "the user asks to email, send, write to, or reply to someone. ALWAYS "
        "call first with confirm=false to preview; read back the 'about to send…' "
        "reply, get a spoken 'yes', then call again with confirm=true. Never set "
        "confirm=true until the user has said yes. For a new email give `to` "
        "(address), `subject` and `body`. For a reply set reply=true and give "
        "`body` (the `to` and subject are taken from the last email read). "
        "`account` picks which address to send from: 'personal' or 'business'. "
        "Write `body` as clean prose in the user's voice."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "to": {"type": "STRING", "description": "Recipient email address (new emails)."},
            "subject": {"type": "STRING", "description": "Subject line (new emails)."},
            "body": {"type": "STRING", "description": "The message text."},
            "reply": {"type": "BOOLEAN",
                      "description": "true to reply to the last email read instead of a new one."},
            "account": {"type": "STRING",
                        "description": "Send from 'personal' or 'business'. Defaults sensibly."},
            "confirm": {"type": "BOOLEAN",
                        "description": "false to preview (default); true to actually send, "
                                       "only after the user says yes."},
        },
        "required": ["body"],
    },
}


def _account_for(which: str, reply_label: str = "") -> dict | None:
    accs = em.accounts(None)
    if not accs:
        return None
    if reply_label:
        for a in accs:
            if a["label"] == reply_label:
                return a
    w = (which or "").lower().strip()
    if "business" in w or "work" in w or "store" in w:
        for a in accs:
            if a["key"] == "business":
                return a
    if "personal" in w or "private" in w:
        for a in accs:
            if a["key"] == "personal":
                return a
    return accs[0]        # default: personal (first configured)


def run(parameters: dict, player=None, session_memory=None) -> str:
    if not em.is_configured():
        return ("Your email isn't connected yet, sir — add it under Settings, "
                "Plugin Settings, Email first.")
    body = str(parameters.get("body") or "").strip()
    if not body:
        return "What would you like the email to say, sir?"
    is_reply = bool(parameters.get("reply"))
    confirm = bool(parameters.get("confirm"))

    if is_reply:
        last = em.last_read
        if not last or not last.get("from_addr"):
            return "I don't have an email to reply to, sir — shall I read your inbox first?"
        to_addr = last["from_addr"]
        subject = last["subject"]
        if not subject.lower().startswith("re:"):
            subject = "Re: " + subject
        in_reply_to = last.get("message_id", "")
        acc = _account_for(str(parameters.get("account") or ""), last.get("account", ""))
    else:
        to_addr = str(parameters.get("to") or "").strip()
        subject = str(parameters.get("subject") or "").strip() or "(no subject)"
        in_reply_to = ""
        if not _EMAIL_RX.search(to_addr):
            return "What's the email address I'm sending to, sir?"
        to_addr = _EMAIL_RX.search(to_addr).group(0)
        acc = _account_for(str(parameters.get("account") or ""))

    if acc is None:
        return "I couldn't find which account to send from, sir."

    if not confirm:
        preview = body if len(body) <= 160 else body[:160] + "…"
        verb = "reply to" if is_reply else "email"
        return (f"About to {verb} {to_addr} from your {acc['label']} address, "
                f"subject '{subject}': \"{preview}\". Say yes to send.")

    try:
        em.send(acc, to_addr, subject, body, in_reply_to)
    except em.EmailError as e:
        em.log(player, f"SYS: Email send failed — {e}")
        return f"Sir, I couldn't send it: {e}."
    em.log(player, f"SYS: Email sent ({acc['label']}) → {to_addr}")
    return f"Sent, sir — your {acc['label']} email to {to_addr} is on its way."
