"""
Shared helpers for the email plugins (email_read.py, email_send.py).
The leading underscore keeps the plugin loader from treating this as a tool.

Two accounts — personal and business — each reached over standard IMAP (to
read) and SMTP (to send) with an app password. No OAuth: the owner pastes an
app password once in ⚙ → PLUGIN SETTINGS → EMAIL. Only the Python standard
library is used (imaplib, smtplib, email), so there is nothing to install.
"""
from __future__ import annotations

import email as _email
import email.policy as _policy
import imaplib
import re
import smtplib
import ssl
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr, formataddr, make_msgid

NS = "email"

# host_imap, port_imap, host_smtp, port_smtp, smtp_mode
PROVIDERS = {
    "gmail":   ("imap.gmail.com", 993, "smtp.gmail.com", 465, "ssl"),
    "outlook": ("outlook.office365.com", 993, "smtp.office365.com", 587, "starttls"),
    "yahoo":   ("imap.mail.yahoo.com", 993, "smtp.mail.yahoo.com", 465, "ssl"),
    "icloud":  ("imap.mail.me.com", 993, "smtp.mail.me.com", 587, "starttls"),
}

_DOMAIN_PROVIDER = {
    "gmail.com": "gmail", "googlemail.com": "gmail",
    "outlook.com": "outlook", "hotmail.com": "outlook", "live.com": "outlook",
    "msn.com": "outlook", "hotmail.co.uk": "outlook", "outlook.co.uk": "outlook",
    "yahoo.com": "yahoo", "yahoo.co.uk": "yahoo", "ymail.com": "yahoo",
    "icloud.com": "icloud", "me.com": "icloud",
}

_CHOICE_PROVIDER = {
    "gmail / google workspace": "gmail", "gmail": "gmail", "google workspace": "gmail",
    "outlook / office 365": "outlook", "outlook": "outlook", "office 365": "outlook",
    "yahoo": "yahoo", "icloud": "icloud",
}

# The most recently read message, so "reply to that" has something to reply to.
last_read: dict | None = None


# ── config ───────────────────────────────────────────────────────────────────

def _cfg(values: dict | None = None) -> dict:
    if values is None:
        try:
            from memory.config_manager import get_plugin_config
            values = get_plugin_config(NS)
        except Exception:
            values = {}
    return values or {}


def _provider_for(addr: str, choice: str = "", custom_imap: str = "",
                  custom_smtp: str = "") -> tuple:
    """Resolve (imap_host, imap_port, smtp_host, smtp_port, smtp_mode)."""
    dom = addr.split("@")[-1].lower().strip() if "@" in addr else ""
    key = _DOMAIN_PROVIDER.get(dom)
    if not key and choice:
        key = _CHOICE_PROVIDER.get(choice.lower().strip())
    if key and key in PROVIDERS:
        return PROVIDERS[key]
    # Unknown provider: a best-effort guess, overridable by custom hosts.
    imap_h = custom_imap.strip() or (f"imap.{dom}" if dom else "")
    smtp_h = custom_smtp.strip() or (f"smtp.{dom}" if dom else "")
    return (imap_h, 993, smtp_h, 587, "starttls")


def accounts(values: dict | None = None) -> list[dict]:
    """The configured accounts, personal first. Empty list if none set up."""
    c = _cfg(values)
    out = []
    for key, label, pfx, choice_key in (
            ("personal", "personal", "personal", ""),
            ("business", "business", "business", "business_provider")):
        addr = str(c.get(f"{pfx}_email") or "").strip()
        pw = str(c.get(f"{pfx}_password") or "").strip()
        if not addr or not pw:
            continue
        imap_h, imap_p, smtp_h, smtp_p, mode = _provider_for(
            addr, str(c.get(choice_key) or "") if choice_key else "",
            str(c.get("business_imap_host") or "") if key == "business" else "",
            str(c.get("business_smtp_host") or "") if key == "business" else "")
        out.append({"key": key, "label": label, "email": addr, "password": pw,
                    "imap_host": imap_h, "imap_port": imap_p,
                    "smtp_host": smtp_h, "smtp_port": smtp_p, "smtp_mode": mode})
    return out


def pick_accounts(which: str, values: dict | None = None) -> list[dict]:
    """Accounts matching a spoken selector ('personal', 'business'/'work', 'both')."""
    accs = accounts(values)
    w = (which or "").lower().strip()
    if any(k in w for k in ("business", "work", "store", "skyford", "company")):
        return [a for a in accs if a["key"] == "business"] or accs
    if any(k in w for k in ("personal", "private", "gmail", "my own")):
        return [a for a in accs if a["key"] == "personal"] or accs
    return accs       # default: all configured accounts


def is_configured(values: dict | None = None) -> bool:
    return bool(accounts(values))


class EmailError(RuntimeError):
    pass


# ── decoding ─────────────────────────────────────────────────────────────────

def _dh(raw) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return str(raw)


def _sender_name(from_hdr: str) -> str:
    name, addr = parseaddr(from_hdr)
    name = _dh(name)
    return name or addr or from_hdr


def _body_snippet(msg, limit: int = 240) -> str:
    text = ""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain" and \
                        "attachment" not in str(part.get("Content-Disposition") or ""):
                    text = part.get_content()
                    break
            if not text:
                for part in msg.walk():
                    if part.get_content_type() == "text/html":
                        text = re.sub(r"<[^>]+>", " ", part.get_content())
                        break
        else:
            text = msg.get_content()
            if msg.get_content_type() == "text/html":
                text = re.sub(r"<[^>]+>", " ", text)
    except Exception:
        text = ""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text[:limit]


# ── IMAP read ────────────────────────────────────────────────────────────────

def _imap_connect(acc: dict) -> imaplib.IMAP4_SSL:
    try:
        M = imaplib.IMAP4_SSL(acc["imap_host"], acc["imap_port"],
                              ssl_context=ssl.create_default_context(), timeout=30)
    except Exception as e:
        raise EmailError(f"couldn't reach the {acc['label']} mail server "
                         f"({acc['imap_host']}): {type(e).__name__}")
    try:
        M.login(acc["email"], acc["password"])
    except imaplib.IMAP4.error:
        raise EmailError(f"the {acc['label']} email rejected the sign-in — the "
                         f"app password is wrong, or app passwords aren't enabled yet")
    return M


def fetch(acc: dict, criteria: str, limit: int = 5) -> list[dict]:
    """Return up to `limit` messages (newest first) for an IMAP search string."""
    M = _imap_connect(acc)
    try:
        M.select("INBOX", readonly=True)        # readonly => nothing marked read
        typ, data = M.search(None, criteria)
        if typ != "OK":
            return []
        ids = (data[0] or b"").split()
        ids = ids[-limit:][::-1]                 # newest first
        out = []
        for num in ids:
            typ, raw = M.fetch(num, "(BODY.PEEK[])")
            if typ != "OK" or not raw or not raw[0]:
                continue
            msg = _email.message_from_bytes(raw[0][1], policy=_policy.default)
            out.append({
                "account": acc["label"],
                "from": _sender_name(msg.get("From", "")),
                "from_addr": parseaddr(msg.get("From", ""))[1],
                "subject": _dh(msg.get("Subject", "")) or "(no subject)",
                "date": _dh(msg.get("Date", "")),
                "snippet": _body_snippet(msg),
                "message_id": msg.get("Message-ID", ""),
            })
        return out
    finally:
        try:
            M.logout()
        except Exception:
            pass


def search_criteria(request: str, query: str) -> str:
    r = (request or "").lower()
    q = (query or "").strip()
    if q:
        safe = q.replace('"', "")
        if "from" in r or "@" in q:
            return f'FROM "{safe}"'
        return f'TEXT "{safe}"'
    if "unread" in r or "new" in r or "unseen" in r:
        return "UNSEEN"
    return "ALL"


# ── SMTP send ────────────────────────────────────────────────────────────────

def send(acc: dict, to_addr: str, subject: str, body: str,
         in_reply_to: str = "") -> None:
    msg = EmailMessage()
    msg["From"] = formataddr((acc["email"].split("@")[0], acc["email"]))
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    host, port, mode = acc["smtp_host"], acc["smtp_port"], acc["smtp_mode"]
    ctx = ssl.create_default_context()
    try:
        if mode == "ssl":
            s = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
        else:
            s = smtplib.SMTP(host, port, timeout=30)
            s.starttls(context=ctx)
        with s:
            s.login(acc["email"], acc["password"])
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise EmailError(f"the {acc['label']} email rejected the sign-in — check "
                         f"the app password")
    except Exception as e:
        raise EmailError(f"couldn't send through {host}: {type(e).__name__}")


def log(player, text: str) -> None:
    if player is not None:
        try:
            player.write_log(text)
        except Exception:
            pass
