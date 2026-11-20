"""
Shared helpers for the Shopify plugins (shopify_store.py, shopify_update.py).
The leading underscore keeps the plugin loader from treating this as a tool.

Talks to the Shopify Admin GraphQL API (version 2026-10) with a custom-app
access token. Settings live under ⚙ → PLUGIN SETTINGS → SHOPIFY:
  shop_domain   e.g. skyford  (or skyford.myshopify.com)
  access_token  the Admin API access token from a custom app
  allow_writes  a toggle, OFF by default — until it is ON, nothing can change
                the live store, so a misheard word can never move a price.
"""
from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any

import requests

NS = "shopify"
API_VERSION = "2026-10"
_TIMEOUT = 30

# Currency code → symbol for the few a UK/Nigeria luxury brand is likely to use.
_SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€", "NGN": "₦", "CAD": "C$",
           "AUD": "A$", "JPY": "¥"}

_shop_cache: dict[str, Any] = {}      # {domain: {"name":.., "currency":..}}


# ── config ───────────────────────────────────────────────────────────────────

def config(values: dict | None = None) -> dict:
    if values is None:
        try:
            from memory.config_manager import get_plugin_config
            values = get_plugin_config(NS)
        except Exception:
            values = {}
    return values or {}


def shop_domain(values: dict | None = None) -> str:
    raw = str(config(values).get("shop_domain") or "").strip().lower()
    if not raw:
        return ""
    raw = raw.replace("https://", "").replace("http://", "").strip("/")
    if "." not in raw:
        raw = raw + ".myshopify.com"
    # Accept a full admin URL and keep only the host.
    return raw.split("/")[0]


def access_token(values: dict | None = None) -> str:
    return str(config(values).get("access_token") or "").strip()


def writes_enabled(values: dict | None = None) -> bool:
    return bool(config(values).get("allow_writes"))


def is_configured(values: dict | None = None) -> bool:
    return bool(shop_domain(values) and access_token(values))


# ── GraphQL ──────────────────────────────────────────────────────────────────

class ShopifyError(RuntimeError):
    pass


def gql(query: str, variables: dict | None = None,
        values: dict | None = None) -> dict:
    """Run one GraphQL operation and return its `data`. Raises ShopifyError with
    a plain-English message a non-technical owner can act on."""
    dom, tok = shop_domain(values), access_token(values)
    if not dom or not tok:
        raise ShopifyError("your store isn't connected yet — add your store "
                           "address and access token in Plugin Settings")
    url = f"https://{dom}/admin/api/{API_VERSION}/graphql.json"
    try:
        r = requests.post(url, timeout=_TIMEOUT,
                          headers={"X-Shopify-Access-Token": tok,
                                   "Content-Type": "application/json"},
                          json={"query": query, "variables": variables or {}})
    except requests.RequestException as e:
        raise ShopifyError(f"couldn't reach your store ({type(e).__name__})")

    if r.status_code in (401, 403):
        raise ShopifyError("the store rejected the access token — check it is "
                           "right and the app has the needed permissions")
    if r.status_code == 404:
        raise ShopifyError("that store address wasn't found — check it in "
                           "Plugin Settings (it should look like skyford.myshopify.com)")
    if r.status_code == 429:
        raise ShopifyError("the store is busy (too many requests) — try again in a moment")
    try:
        body = r.json()
    except Exception:
        raise ShopifyError(f"the store returned an unexpected response (HTTP {r.status_code})")
    if body.get("errors"):
        msg = "; ".join(e.get("message", "") for e in body["errors"] if isinstance(e, dict))
        raise ShopifyError(msg or "the store reported an error")
    data = body.get("data")
    if data is None:
        raise ShopifyError("the store returned no data")
    return data


# ── formatting ───────────────────────────────────────────────────────────────

def shop_info(values: dict | None = None) -> dict:
    dom = shop_domain(values)
    if dom in _shop_cache:
        return _shop_cache[dom]
    data = gql("{ shop { name currencyCode } }", None, values)
    shop = data.get("shop") or {}
    info = {"name": shop.get("name") or dom,
            "currency": shop.get("currencyCode") or "GBP"}
    _shop_cache[dom] = info
    return info


def money(amount, currency: str) -> str:
    try:
        n = float(amount)
    except (TypeError, ValueError):
        n = 0.0
    sym = _SYMBOL.get(str(currency).upper(), "")
    body = f"{n:,.2f}"
    return f"{sym}{body}" if sym else f"{body} {currency}"


# ── dates (store-local, simple) ──────────────────────────────────────────────

def range_query(period: str) -> tuple[str, str]:
    """A Shopify `created_at` filter and a human label for a named period.
    Dates are in the machine's local day, which for this owner is the store's."""
    today = _dt.date.today()
    if period == "week":
        start = today - _dt.timedelta(days=today.weekday())   # Monday
        label = "this week"
    elif period == "month":
        start = today.replace(day=1)
        label = "this month"
    elif period == "yesterday":
        start = today - _dt.timedelta(days=1)
        return (f"created_at:>='{start}' created_at:<'{today}'", "yesterday")
    else:
        start = today
        label = "today"
    return (f"created_at:>='{start}'", label)


# ── lookups ──────────────────────────────────────────────────────────────────

_PRODUCT_Q = """
query($q:String!){
  products(first:5, query:$q){
    nodes{
      id title status totalInventory
      featuredImage{ url }
      variants(first:25){
        nodes{ id title price
          inventoryQuantity
          inventoryItem{ id
            inventoryLevels(first:5){ nodes{ location{ id name } } } } } }
    }
  }
}"""


def find_products(term: str, values: dict | None = None) -> list[dict]:
    """Products matching a title term, best-effort. Empty list if none."""
    term = (term or "").strip()
    if not term:
        return []
    # `title:*term*` is a loose contains match; fall back to the raw term.
    q = f"title:*{term}*" if " " not in term else term
    data = gql(_PRODUCT_Q, {"q": q}, values)
    nodes = ((data.get("products") or {}).get("nodes")) or []
    if not nodes and q != term:
        data = gql(_PRODUCT_Q, {"q": term}, values)
        nodes = ((data.get("products") or {}).get("nodes")) or []
    return nodes


def primary_location_id(values: dict | None = None) -> str | None:
    data = gql("{ locations(first:1){ nodes{ id name } } }", None, values)
    nodes = ((data.get("locations") or {}).get("nodes")) or []
    return nodes[0]["id"] if nodes else None


def new_idempotency_key() -> str:
    return str(uuid.uuid4())


def log(player, text: str) -> None:
    if player is not None:
        try:
            player.write_log(text)
        except Exception:
            pass
