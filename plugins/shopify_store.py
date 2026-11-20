"""
Shopify — ask about your store (read only).

  "How many orders today?"            "What sold this week?"
  "How much have I made this month?"  "What's running low?"
  "How many of the gold hoodie are in stock?"
  "What's the price of the velvet blazer?"

Reads sales, orders, inventory and product info from the Skyford store. Making
changes is a separate tool (shopify_update) that is off until switched on.
"""
from __future__ import annotations

from plugins import _shopify_common as sh


def _test(values: dict):
    """TEST button: confirm the store address and token work."""
    if not sh.shop_domain(values) or not sh.access_token(values):
        return False, "Enter your store address and access token first."
    try:
        sh._shop_cache.clear()
        info = sh.shop_info(values)
        extra = " Changes are ON." if sh.writes_enabled(values) else " Changes are off (read-only)."
        return True, f"Connected to {info['name']} ({info['currency']})." + extra
    except Exception as e:
        return False, str(e)


PLUGIN_SETTINGS = {
    "namespace": sh.NS,
    "title": "SHOPIFY — connect your store",
    "fields": [
        {"key": "shop_domain", "label": "Store address", "type": "text",
         "placeholder": "skyford.myshopify.com"},
        {"key": "access_token", "label": "Admin API access token", "type": "password",
         "placeholder": "shpat_…  (from a custom app in Shopify admin)"},
        {"key": "allow_writes", "label": "Allow changes (prices, stock, publishing)",
         "type": "toggle", "default": False},
    ],
    "action": {"label": "TEST CONNECTION", "run": _test},
}

PLUGIN = {
    "name": "shopify_store",
    "description": (
        "Looks up live information from the user's Shopify store: sales and "
        "revenue (today / this week / this month / yesterday), recent orders, "
        "how many of a product are in stock, which items are running low, "
        "product prices and details, and totals. Call this for any question "
        "about the shop, orders, sales, takings, revenue, stock, inventory or "
        "products. This only READS — to change a price, stock level or publish "
        "a product, use shopify_update instead. Put the period the user asked "
        "about in `request` (e.g. 'sales today', 'orders this week', 'revenue "
        "this month', 'recent orders', 'low stock') and, when they name a "
        "product, put it in `name`."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "request": {"type": "STRING",
                        "description": "What to look up: 'sales today', 'sales this week', "
                                       "'sales this month', 'yesterday', 'recent orders', "
                                       "'low stock', 'stock', 'product', or 'summary'."},
            "name": {"type": "STRING",
                     "description": "A product name, when the user asks about one specific item."},
        },
        "required": ["request"],
    },
}


# ── orders / sales ───────────────────────────────────────────────────────────

_ORDERS_Q = """
query($q:String!, $cursor:String){
  orders(first:100, query:$q, sortKey:CREATED_AT, reverse:true, after:$cursor){
    nodes{
      name createdAt displayFinancialStatus
      currentTotalPriceSet{ shopMoney{ amount currencyCode } }
      lineItems(first:25){ nodes{ title quantity } }
    }
    pageInfo{ hasNextPage endCursor }
  }
}"""


def _collect_orders(query: str, values, cap: int = 250) -> list[dict]:
    out, cursor = [], None
    while len(out) < cap:
        data = sh.gql(_ORDERS_Q, {"q": query, "cursor": cursor}, values)
        conn = data.get("orders") or {}
        out.extend(conn.get("nodes") or [])
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
    return out


def _sales(period: str, values) -> str:
    q, label = sh.range_query(period)
    orders = _collect_orders(q, values)
    info = sh.shop_info(values)
    cur = info["currency"]
    if not orders:
        return f"No orders yet {label}, sir."
    total, items, tally = 0.0, 0, {}
    for o in orders:
        try:
            total += float(((o.get("currentTotalPriceSet") or {})
                            .get("shopMoney") or {}).get("amount") or 0)
        except (TypeError, ValueError):
            pass
        for li in ((o.get("lineItems") or {}).get("nodes") or []):
            qty = int(li.get("quantity") or 0)
            items += qty
            t = li.get("title") or "item"
            tally[t] = tally.get(t, 0) + qty
    n = len(orders)
    line = (f"{label.capitalize()}: {n} order{'s' if n != 1 else ''}, "
            f"{sh.money(total, cur)} in sales, {items} item{'s' if items != 1 else ''} sold.")
    if tally:
        top = sorted(tally.items(), key=lambda kv: kv[1], reverse=True)[:3]
        line += " Top: " + ", ".join(f"{t} (×{c})" for t, c in top) + "."
    if len(orders) >= 250:
        line += " (counting the 250 most recent.)"
    return line


def _recent_orders(values, limit: int = 5) -> str:
    data = sh.gql(_ORDERS_Q, {"q": "", "cursor": None}, values)
    orders = ((data.get("orders") or {}).get("nodes") or [])[:limit]
    if not orders:
        return "There are no orders yet, sir."
    info = sh.shop_info(values)
    cur = info["currency"]
    parts = []
    for o in orders:
        amt = ((o.get("currentTotalPriceSet") or {}).get("shopMoney") or {}).get("amount")
        status = (o.get("displayFinancialStatus") or "").lower().replace("_", " ")
        parts.append(f"{o.get('name')} — {sh.money(amt, cur)}"
                     + (f" ({status})" if status else ""))
    return f"Last {len(parts)} orders: " + "; ".join(parts) + "."


# ── inventory ────────────────────────────────────────────────────────────────

_LOW_Q = """
query($q:String!){
  productVariants(first:50, query:$q){
    nodes{ id title inventoryQuantity product{ title } }
  }
}"""


def _low_stock(values, threshold: int = 5) -> str:
    data = sh.gql(_LOW_Q, {"q": f"inventory_quantity:<={threshold}"}, values)
    nodes = ((data.get("productVariants") or {}).get("nodes") or [])
    nodes = [n for n in nodes if (n.get("inventoryQuantity") or 0) is not None]
    nodes.sort(key=lambda n: n.get("inventoryQuantity") or 0)
    if not nodes:
        return f"Nothing is at or below {threshold} in stock, sir — you're well stocked."
    parts = []
    for n in nodes[:12]:
        pt = (n.get("product") or {}).get("title") or "item"
        vt = n.get("title") or ""
        name = pt if vt in ("", "Default Title") else f"{pt} ({vt})"
        parts.append(f"{name}: {n.get('inventoryQuantity')}")
    extra = "" if len(nodes) <= 12 else f" …and {len(nodes) - 12} more"
    return f"Running low (≤{threshold}): " + "; ".join(parts) + extra + "."


def _product_report(term: str, values) -> str:
    products = sh.find_products(term, values)
    if not products:
        return f"I couldn't find a product matching '{term}', sir."
    info = sh.shop_info(values)
    cur = info["currency"]
    p = products[0]
    variants = (p.get("variants") or {}).get("nodes") or []
    status = (p.get("status") or "").lower()
    head = f"{p.get('title')} — {status}, {p.get('totalInventory')} in stock total."
    lines = []
    for v in variants[:8]:
        vt = v.get("title") or ""
        tag = "" if vt in ("", "Default Title") else f"{vt}: "
        lines.append(f"{tag}{sh.money(v.get('price'), cur)}, "
                     f"{v.get('inventoryQuantity')} in stock")
    detail = (" " + "; ".join(lines) + ".") if lines else ""
    more = ""
    if len(products) > 1:
        more = f" (also matched {len(products) - 1} other product"\
               f"{'s' if len(products) - 1 != 1 else ''}.)"
    return head + detail + more


# ── counts / summary ─────────────────────────────────────────────────────────

def _summary(values) -> str:
    info = sh.shop_info(values)
    try:
        data = sh.gql("{ productsCount{ count } }", None, values)
        pc = ((data.get("productsCount") or {}).get("count"))
    except sh.ShopifyError:
        pc = None
    today = _sales("today", values)
    tail = f" You have {pc} products published." if pc is not None else ""
    return f"{info['name']}: {today}{tail}"


# ── router ───────────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    if not sh.is_configured():
        return ("Your Shopify store isn't connected yet, sir. Add your store "
                "address and access token under Settings, Plugin Settings, Shopify.")
    req = str(parameters.get("request") or "").strip().lower()
    name = str(parameters.get("name") or "").strip()

    try:
        # A named product with a stock/price question → product report.
        if name and ("stock" in req or "inventor" in req or "price" in req
                     or "product" in req or req in ("", "summary")):
            return _product_report(name, values=None)

        if "low" in req or "running low" in req or "restock" in req:
            return _low_stock(None)
        if "recent" in req or "latest" in req or ("order" in req and "how many" not in req
                                                  and "today" not in req and "week" not in req
                                                  and "month" not in req):
            return _recent_orders(None)
        if "yesterday" in req:
            return _sales("yesterday", None)
        if "week" in req:
            return _sales("week", None)
        if "month" in req:
            return _sales("month", None)
        if any(k in req for k in ("sale", "order", "revenue", "takings",
                                  "made", "today", "sold", "earn")):
            return _sales("today", None)
        if name:
            return _product_report(name, None)
        if "summary" in req or req == "":
            return _summary(None)
        # Anything else: treat the words as a product search.
        return _product_report(req, None)
    except sh.ShopifyError as e:
        sh.log(player, f"SYS: Shopify read failed — {e}")
        return f"Sir, I couldn't get that from the store: {e}."
