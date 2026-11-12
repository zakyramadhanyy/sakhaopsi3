"""Membaca JSON yang dimuat halaman. Tanpa akses jaringan atau browser, jadi bisa diuji langsung.

Bila field wajib hilang, parser melempar FormatError dengan nama field-nya, bukan menulis data kosong.
"""
from __future__ import annotations

import re

import config

PRICE_DIVISOR = 100000  # Shopee menyimpan harga dikali 100.000


class FormatError(Exception):
    def __init__(self, where: str, missing: list):
        self.missing = missing
        super().__init__(f"{where}: field hilang: {', '.join(missing)}")


def walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def dig(node, *paths):
    """Nilai pertama yang ada dari beberapa jalur 'a.b.c'."""
    for path in paths:
        current = node
        for key in path.split("."):
            current = current.get(key) if isinstance(current, dict) else None
        if current not in (None, ""):
            return current
    return None


def money(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(round(value / PRICE_DIVISOR))


def parse_count(value):
    """'1,2RB+ terjual' -> 1200. Tanda + berarti batas bawah, bukan angka tepat."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(rb|jt|k|m)?", value, re.I)
    if not m:
        return None
    unit = (m.group(2) or "").lower()
    if unit in ("rb", "k", "jt", "m"):
        factor = 1000 if unit in ("rb", "k") else 1000000
        return int(round(float(m.group(1).replace(",", ".")) * factor))
    return int(re.sub(r"[.,]", "", m.group(1)))


def image_url(ref):
    if not isinstance(ref, str) or not ref:
        return None
    return ref if ref.startswith("http") else f"{config.IMAGE_CDN}/{ref}"


def product_url(shopid, itemid) -> str:
    return f"https://shopee.co.id/product/{shopid}/{itemid}"


REQUIRED_LIST = ("itemid", "shopid", "name", "price")


def _list_item(d: dict):
    base = d.get("item_basic") if isinstance(d.get("item_basic"), dict) else d
    itemid = dig(base, "itemid", "item_id")
    shopid = dig(base, "shopid", "shop_id")
    if not itemid or not shopid:
        return None
    name = dig(base, "name", "title", "item_card_displayed_asset.name")
    raw_price = dig(base, "item_card_display_price.price", "price", "price_min")
    if name is None and raw_price is None:
        return None  # dict lain yang kebetulan punya id, misalnya data toko
    before = dig(base, "item_card_display_price.strikethrough_price", "price_before_discount", "price_min_before_discount")
    sold_text = dig(base, "item_card_display_sold_count.historical_sold_count_text",
                    "item_card_display_sold_count.display_sold_count_text", "historical_sold_text")
    sold_value = dig(base, "item_card_display_sold_count.historical_sold_count", "historical_sold", "sold")
    return {
        "itemid": int(itemid),
        "shopid": int(shopid),
        "url": product_url(shopid, itemid),
        "name": name,
        "price": money(raw_price),
        "price_before_discount": money(before),
        "discount": dig(base, "item_card_display_price.discount_text", "raw_discount", "discount", "item_card_display_price.discount"),
        "sold_text": sold_text,
        "sold": parse_count(sold_value if sold_value is not None else sold_text),
        "rating": dig(base, "item_rating.rating_star"),
        "cover": image_url(dig(base, "item_card_displayed_asset.image", "image")),
        "shop_category": dig(base, "shop_category_name"),
        "shop_category_ids": dig(base, "shop_category_ids"),
        "source": "json",
    }


def parse_list_payload(payload) -> list:
    items = {}
    for d in walk(payload):
        item = _list_item(d)
        if item:
            known = items.get(item["itemid"])
            if not known or sum(v is not None for v in item.values()) > sum(v is not None for v in known.values()):
                items[item["itemid"]] = item
    for item in items.values():
        missing = [f for f in REQUIRED_LIST if item.get(f) in (None, "")]
        if missing:
            raise FormatError(f"daftar produk, item {item['itemid']}", missing)
    return list(items.values())


def is_list_endpoint(url: str) -> bool:
    u = url.split("?")[0].lower()
    return "/api/" in u and any(k in u for k in ("shop", "search_items", "rcmd_items"))


def is_detail_endpoint(url: str) -> bool:
    u = url.split("?")[0].lower()
    return "/api/" in u and ("pdp" in u or "item/get" in u)


def _names(value) -> list:
    if not isinstance(value, list):
        return []
    out = []
    for c in value:
        if isinstance(c, dict):
            name = c.get("display_name") or c.get("name")
            if name:
                out.append(name)
    return out


def _model(m: dict, tiers: list) -> dict:
    index = dig(m, "extinfo.tier_index", "tier_index") or []
    options = []
    for tier, i in zip(tiers, index):
        opts = tier.get("options") or []
        options.append(opts[i] if isinstance(i, int) and i < len(opts) else None)
    stocks = m.get("price_stocks") if isinstance(m.get("price_stocks"), list) else []
    first = stocks[0] if stocks and isinstance(stocks[0], dict) else {}
    stock = next((v for v in (m.get("stock"), m.get("normal_stock"), first.get("stock")) if isinstance(v, int)), None)
    return {
        "modelid": dig(m, "model_id", "modelid"),
        "name": m.get("name"),
        "sku": m.get("model_sku") or m.get("sku"),
        "options": options,
        "price": money(dig(m, "price", "price_stocks.0.price") or first.get("price")),
        "price_before_discount": money(m.get("price_before_discount") or first.get("price_before_discount")),
        "stock": stock,
        "sold": m.get("sold"),
    }


def parse_detail_payloads(payloads: list, itemid) -> dict:
    """payloads: [(endpoint, json), ...] yang dimuat halaman produk."""
    node, source, endpoint = None, None, None
    for ep, payload in payloads:
        for d in walk(payload):
            if str(dig(d, "item_id", "itemid")) == str(itemid) and (d.get("title") or d.get("name")) and any(
                k in d for k in ("models", "tier_variations", "images")
            ):
                node, source, endpoint = d, payload, ep
                break
        if node:
            break
    if node is None:
        errors = [p.get("error") for _, p in payloads if isinstance(p, dict) and p.get("error")]
        hint = f" (kode error Shopee: {errors})" if errors else ""
        raise FormatError(f"item {itemid}{hint}", ["item (node produk tidak ditemukan di JSON)"])

    def anywhere(*keys):
        for d in walk(source):
            for key in keys:
                if d.get(key) not in (None, "", [], {}):
                    return d[key]
        return None

    images = node.get("images") or dig(source, "data.product_images.images") or anywhere("images")
    images = [u for u in (image_url(x) for x in (images or [])) if u]
    tiers = [
        {"name": t.get("name"), "options": t.get("options") or []}
        for t in (node.get("tier_variations") or [])
        if isinstance(t, dict) and (t.get("name") or t.get("options"))
    ]
    models = [_model(m, tiers) for m in (node.get("models") or []) if isinstance(m, dict)]
    if not models:
        models = [{
            "modelid": None, "name": None, "sku": None, "options": [],
            "price": money(dig(node, "price", "price_min") or anywhere("single_value", "range_min")),
            "price_before_discount": money(node.get("price_before_discount")),
            "stock": node.get("stock") if isinstance(node.get("stock"), int) else None,
            "sold": None,
        }]

    missing = []
    name = node.get("title") or node.get("name")
    if not images:
        missing.append("images")
    for key, label in (("price", "models[].price"), ("stock", "models[].stock")):
        if any(m[key] is None for m in models):
            missing.append(label)
    if missing:
        raise FormatError(f"item {itemid}", missing)

    attributes = [
        {"name": a.get("name"), "value": a.get("value")}
        for a in (node.get("attributes") or anywhere("attributes") or [])
        if isinstance(a, dict) and a.get("name") and a.get("value")
    ]
    rating = dig(node, "item_rating.rating_star") or anywhere("rating_star")
    rating_count = dig(node, "item_rating.rating_count.0", "cmt_count") or anywhere("total_rating_count", "rating_count_total")
    if isinstance(rating_count, list):
        rating_count = rating_count[0] if rating_count else None
    sold = dig(node, "historical_sold", "sold") or anywhere("historical_sold", "global_sold")
    result = {
        "itemid": int(itemid),
        "shopid": dig(node, "shop_id", "shopid"),
        "name": name,
        "description": node.get("description") or anywhere("description"),
        "images": images,
        "tier_variations": tiers,
        "models": models,
        "attributes": attributes,
        "rating": rating,
        "rating_count": rating_count,
        "sold": parse_count(sold),
        "categories": _names(node.get("fe_categories")) or _names(node.get("categories")),
        "status": node.get("item_status") or node.get("status"),
        "source": "json",
        "endpoint": endpoint,
    }
    # Field opsional yang kosong dicatat, supaya jelas tidak ada di sumber, bukan gagal terbaca.
    result["absent"] = [k for k in ("description", "attributes", "rating", "rating_count", "sold", "categories") if not result[k]]
    return result


def parse_dom_detail(dom: dict, itemid, shopid) -> dict:
    """Cadangan dari DOM. Stok per kombinasi tidak tersedia, jadi hasilnya ditandai parsial."""
    missing = [k for k in ("name", "price", "images") if not dom.get(k)]
    if missing:
        raise FormatError(f"item {itemid} (cadangan DOM)", missing)
    return {
        "itemid": int(itemid),
        "shopid": shopid,
        "name": dom["name"],
        "description": dom.get("description"),
        "images": dom["images"],
        "tier_variations": [],
        "models": [{"modelid": None, "name": None, "sku": None, "options": [], "price": dom["price"],
                    "price_before_discount": dom.get("price_before_discount"), "stock": None, "sold": None}],
        "attributes": [],
        "rating": dom.get("rating"),
        "rating_count": dom.get("rating_count"),
        "sold": None,
        "categories": [],
        "status": None,
        "source": "dom",
        "partial": True,
        "absent": ["tier_variations", "models[].stock", "attributes", "sold", "categories"],
    }
