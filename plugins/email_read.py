"""
Email — read and search your inbox (personal and business).

  "Any new emails?"            "Read my latest emails"
  "What did the supplier send?"  "Search my email for the Skyford order"
  "Check my business email"

Reads over IMAP and summarises. It never marks anything as read and never
deletes. Sending is a separate tool (email_send).
"""
from __future__ import annotations

from plugins import _email_common as em


def _test(values: dict):
    """TEST button: sign in to each configured account and count the inbox."""
    accs = em.accounts(values)
    if not accs:
        return False, "Add at least your personal email and its app password first."
    lines, ok = [], True
    for a in accs:
        try:
            got = em.fetch(a, "ALL", limit=1)
            lines.append(f"{a['label']} ({a['email']}): connected")
        except Exception as e:
            ok = False
            lines.append(f"{a['label']} ({a['email']}): {e}")
    return ok, " · ".join(lines)


PLUGIN_SETTINGS = {
    "namespace": em.NS,
    "title": "EMAIL — connect your inboxes (app passwords, not your login password)",
    "fields": [
        {"key": "personal_email", "label": "Personal email address", "type": "text",
         "placeholder": "you@gmail.com"},
        {"key": "personal_password", "label": "Personal app password", "type": "password",
         "placeholder": "16-character app password from your email provider"},
        {"key": "business_email", "label": "Business email address (optional)", "type": "text",
         "placeholder": "you@yourbrand.com"},
        {"key": "business_password", "label": "Business app password (optional)", "type": "password",
         "placeholder": "app password for the business account"},
        {"key": "business_provider", "label": "Business email provider", "type": "choice",
         "options": ["Gmail / Google Workspace", "Outlook / Office 365", "Yahoo", "iCloud", "Other"],
         "default": "Gmail / Google Workspace"},
        {"key": "business_imap_host", "label": "Business IMAP host (only if 'Other')", "type": "text",
         "placeholder": "e.g. mail.yourhost.com"},
        {"key": "business_smtp_host", "label": "Business SMTP host (only if 'Other')", "type": "text",
         "placeholder": "e.g. smtp.yourhost.com"},
    ],
    "action": {"label": "TEST EMAIL", "run": _test},
}

PLUGIN = {
    "name": "email_read",
    "description": (
        "Reads and searches the user's email inbox(es) over IMAP and summarises "
        "what it finds. Call for any request to check, read, summarise or search "
        "email / mail / inbox (in any language), e.g. 'any new emails', 'read my "
        "latest', 'what did X send me', 'find the email about Y'. `request` is "
        "'unread', 'recent', 'from' or 'search'. Put a sender or keyword in "
        "`query`. `account` picks which inbox: 'personal', 'business'/'work', or "
        "'both' (default). This only READS and never marks mail as read — to "
        "write or send, use email_send."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "request": {"type": "STRING",
                        "description": "'unread' (new mail), 'recent' (latest), 'from' (a sender), "
                                       "or 'search' (by keyword)."},
            "query": {"type": "STRING",
                      "description": "Sender name/address or keyword to look for, when relevant."},
            "account": {"type": "STRING",
                        "description": "'personal', 'business' (a.k.a. work), or 'both' (default)."},
            "limit": {"type": "INTEGER",
                      "description": "How many messages to summarise (default 5, max 15)."},
        },
        "required": ["request"],
    },
}


def _summarise(msgs: list[dict], multi_account: bool) -> str:
    out = []
    for i, m in enumerate(msgs, 1):
        who = m["from"]
        tag = f"[{m['account']}] " if multi_account else ""
        snip = (" — " + m["snippet"]) if m["snippet"] else ""
        out.append(f"{i}. {tag}{who}: {m['subject']}{snip}")
    return "\n".join(out)


def run(parameters: dict, player=None, session_memory=None) -> str:
    if not em.is_configured():
        return ("Your email isn't connected yet, sir. Add your address and an "
                "app password under Settings, Plugin Settings, Email.")
    request = str(parameters.get("request") or "recent").strip().lower()
    query = str(parameters.get("query") or "").strip()
    which = str(parameters.get("account") or "both").strip()
    try:
        limit = max(1, min(15, int(parameters.get("limit") or 5)))
    except (TypeError, ValueError):
        limit = 5

    accs = em.pick_accounts(which, None)
    if not accs:
        return "I couldn't find that email account, sir."
    criteria = em.search_criteria(request, query)

    all_msgs, errors = [], []
    for a in accs:
        try:
            all_msgs.extend(em.fetch(a, criteria, limit))
        except em.EmailError as e:
            errors.append(f"{a['label']}: {e}")

    if not all_msgs:
        if errors:
            em.log(player, "SYS: Email read failed — " + "; ".join(errors))
            return f"Sir, I couldn't check your email: {errors[0]}."
        label = "unread " if criteria == "UNSEEN" else ""
        return f"No {label}emails{(' matching ' + query) if query else ''}, sir."

    # Newest first across accounts; cap to the requested limit.
    all_msgs = all_msgs[: max(limit, len(accs))][:limit] if len(accs) == 1 else all_msgs[:limit]
    em.last_read = all_msgs[0]            # so "reply to that" works
    multi = len(accs) > 1
    n = len(all_msgs)
    head = (f"You have {n} " + ("unread " if criteria == "UNSEEN" else "")
            + ("email" if n == 1 else "emails")
            + (f" matching '{query}'" if query else "") + ", sir:")
    return head + "\n" + _summarise(all_msgs, multi)
