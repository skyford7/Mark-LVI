"""
Shopify — change the store (price, stock, publish). Off by default.

  "Set the gold hoodie to 95 pounds"   "Change velvet blazer stock to 12"
  "Publish the silk scarf"             "Unpublish the sample tee"

Two safety gates stand in front of every change to the LIVE store:
  1. It does nothing until the ALLOW CHANGES toggle is switched on in
     ⚙ → PLUGIN SETTINGS → SHOPIFY. Until then a misheard word can't move a
     price or zero a stock level.
  2. Even then it confirms first: the model is told to call once to hear back
     "about to change X from A to B — say yes", get a spoken yes, then call
     again with confirm=true. Nothing is written on the first call.
"""
from __future__ import annotations

from plugins import _shopify_common as sh

PLUGIN = {
    "name": "shopify_update",
    "description": (
        "Changes the user's LIVE Shopify store: set a product's PRICE, set its "
        "STOCK / inventory level, or PUBLISH / UNPUBLISH a product. Use only "
        "when the user clearly asks to change, set, update, publish or hide "
        "something in the shop. ALWAYS call first with confirm=false, read back "
        "the 'about to change …' reply you get, get a spoken 'yes' from the "
        "user, then call again with confirm=true. Never set confirm=true until "
        "the user has said yes to that specific change. `change` is one of "
        "'price', 'stock', 'publish', 'unpublish'. `product` is the item's "
        "name. `value` is the new price or new stock number (not needed to "
        "publish/unpublish). `variant` picks the size/option when an item has "
        "several."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "change": {"type": "STRING",
                       "description": "'price', 'stock', 'publish' or 'unpublish'."},
            "product": {"type": "STRING", "description": "The product's name."},
            "value": {"type": "STRING",
                      "description": "New price (e.g. '95' or '95.00') or new stock count "
                                     "(e.g. '12'). Leave empty for publish/unpublish."},
            "variant": {"type": "STRING",
                        "description": "Which size/option, if the product has several "
                                       "(e.g. 'Large', 'Gold')."},
            "confirm": {"type": "BOOLEAN",
                        "description": "false to preview the change (default), true to apply it "
                                       "AFTER the user has said yes."},
        },
        "required": ["change", "product"],
    },
}

_PRICE_M = """
mutation($productId:ID!, $variants:[ProductVariantsBulkInput!]!){
  productVariantsBulkUpdate(productId:$productId, variants:$variants){
    productVariants{ id price }
    userErrors{ field message }
  }
}"""

_STATUS_M = """
mutation($product:ProductUpdateInput!){
  productUpdate(product:$product){
    product{ id status }
    userErrors{ field message }
  }
}"""

# The @idempotent key is a directive, not a variable, so it is injected into the
# query text at call time (required by the Admin API from version 2026-04).
_STOCK_M = """
mutation($input:InventorySetQuantitiesInput!){
  inventorySetQuantities(input:$input) @idempotent(key:"%s"){
    inventoryAdjustmentGroup{ createdAt }
    userErrors{ field message }
  }
}"""


def _pick_variant(product: dict, hint: str) -> tuple[dict | None, list[str]]:
    variants = (product.get("variants") or {}).get("nodes") or []
    titles = [v.get("title") or "" for v in variants]
    if len(variants) == 1:
        return variants[0], titles
    hint = (hint or "").strip().lower()
    if hint:
        for v in variants:
            if hint in (v.get("title") or "").lower():
                return v, titles
    return None, titles


def _user_errors(payload: dict, key: str) -> str | None:
    errs = ((payload.get(key) or {}).get("userErrors")) or []
    if errs:
        return "; ".join(e.get("message", "") for e in errs if isinstance(e, dict))
    return None


def run(parameters: dict, player=None, session_memory=None) -> str:
    if not sh.is_configured():
        return ("Your Shopify store isn't connected yet, sir — add it under "
                "Settings, Plugin Settings, Shopify first.")
    if not sh.writes_enabled():
        return ("Making changes to the store is switched off for safety, sir. "
                "To allow it, open Settings, Plugin Settings, Shopify and turn "
                "on Allow Changes. Until then I can look things up but not change them.")

    change = str(parameters.get("change") or "").strip().lower()
    pname = str(parameters.get("product") or "").strip()
    value = str(parameters.get("value") or "").strip()
    vhint = str(parameters.get("variant") or "").strip()
    confirm = bool(parameters.get("confirm"))
    if not pname:
        return "Which product, sir?"

    try:
        products = sh.find_products(pname)
        if not products:
            return f"I couldn't find a product matching '{pname}', sir."
        product = products[0]
        title = product.get("title")
        info = sh.shop_info()
        cur = info["currency"]

        # ── publish / unpublish ──────────────────────────────────────────────
        if change in ("publish", "unpublish", "hide", "show"):
            want = "ACTIVE" if change in ("publish", "show") else "DRAFT"
            now = (product.get("status") or "").upper()
            word = "publish" if want == "ACTIVE" else "unpublish"
            if now == want:
                return f"{title} is already {'published' if want=='ACTIVE' else 'unpublished'}, sir."
            if not confirm:
                return (f"About to {word} {title} (currently {now.lower()}). "
                        f"Say yes to confirm.")
            data = sh.gql(_STATUS_M, {"product": {"id": product["id"], "status": want}})
            err = _user_errors(data, "productUpdate")
            if err:
                return f"The store wouldn't {word} it, sir: {err}."
            sh.log(player, f"SYS: Shopify — {title} set to {want}")
            return f"Done, sir. {title} is now {'published' if want=='ACTIVE' else 'unpublished'}."

        # price / stock both act on a single variant
        variant, titles = _pick_variant(product, vhint)
        if variant is None:
            opts = ", ".join(t for t in titles if t and t != "Default Title")
            return (f"{title} comes in several options ({opts}), sir — which one?")

        vlabel = variant.get("title") or ""
        vtag = "" if vlabel in ("", "Default Title") else f" ({vlabel})"

        # ── price ────────────────────────────────────────────────────────────
        if change in ("price", "cost"):
            try:
                new_price = round(float(value.replace(cur, "").replace("£", "")
                                        .replace("$", "").replace(",", "").strip()), 2)
            except ValueError:
                return f"What price should I set {title} to, sir?"
            if new_price <= 0:
                return "A price has to be above zero, sir."
            old = variant.get("price")
            if not confirm:
                return (f"About to change {title}{vtag} from {sh.money(old, cur)} "
                        f"to {sh.money(new_price, cur)}. Say yes to confirm.")
            data = sh.gql(_PRICE_M, {"productId": product["id"],
                                     "variants": [{"id": variant["id"],
                                                   "price": f"{new_price:.2f}"}]})
            err = _user_errors(data, "productVariantsBulkUpdate")
            if err:
                return f"The store wouldn't change the price, sir: {err}."
            sh.log(player, f"SYS: Shopify — {title}{vtag} price {old} → {new_price}")
            return (f"Done, sir. {title}{vtag} is now {sh.money(new_price, cur)} "
                    f"(was {sh.money(old, cur)}).")

        # ── stock ────────────────────────────────────────────────────────────
        if change in ("stock", "inventory", "quantity", "qty"):
            try:
                new_qty = int(float(value))
            except ValueError:
                return f"What stock number should I set {title} to, sir?"
            if new_qty < 0:
                return "Stock can't be below zero, sir."
            old = variant.get("inventoryQuantity")
            inv_item = (variant.get("inventoryItem") or {})
            inv_id = inv_item.get("id")
            levels = ((inv_item.get("inventoryLevels") or {}).get("nodes")) or []
            loc_id = (levels[0]["location"]["id"] if levels else sh.primary_location_id())
            if not inv_id or not loc_id:
                return "I couldn't find where that item's stock is kept, sir."
            if not confirm:
                return (f"About to change {title}{vtag} stock from {old} to {new_qty}. "
                        f"Say yes to confirm.")
            q = {"inventoryItemId": inv_id, "locationId": loc_id, "quantity": new_qty}
            if isinstance(old, int):
                q["changeFromQuantity"] = old        # compare-and-set guard
            mutation = _STOCK_M % sh.new_idempotency_key()
            data = sh.gql(mutation, {"input": {"name": "available",
                                               "reason": "correction",
                                               "quantities": [q]}})
            err = _user_errors(data, "inventorySetQuantities")
            if err:
                return f"The store wouldn't change the stock, sir: {err}."
            sh.log(player, f"SYS: Shopify — {title}{vtag} stock {old} → {new_qty}")
            return f"Done, sir. {title}{vtag} stock is now {new_qty} (was {old})."

        return ("I can change a price, a stock level, or publish and unpublish, "
                "sir — which did you mean?")
    except sh.ShopifyError as e:
        sh.log(player, f"SYS: Shopify change failed — {e}")
        return f"Sir, I couldn't make that change: {e}."
