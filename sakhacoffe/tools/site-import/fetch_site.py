#!/usr/bin/env python3
"""Ambil data produk dari situs resmi sakhacoffee.com ke data/site/products.json.

Daftar produk dari sitemap, detail dari halaman publik tiap produk. Halaman yang sudah diambil disimpan
di data/site/raw/, jadi menjalankan ulang tidak mengunduh lagi kecuali memakai --refresh.
"""
from __future__ import annotations

import argparse
import html
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "site"
RAW = OUT / "raw"
SITEMAP = "https://sakhacoffee.com/wp-sitemap-posts-product-1.xml"
USER_AGENT = "SakhaCatalogImport/1.0"
DELAY = (1.5, 3.0)


def get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=40) as response:
        return response.read().decode("utf-8", "replace")


def grams(value: str):
    """'100-gram', '1 Kg', '150GR', '500r' -> gram. Bukan ukuran berat -> None."""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*-?\s*(kg|kilo|gram|gr|g|r)\b", (value or "").lower())
    if not m:
        return None
    number = float(m.group(1).replace(",", "."))
    return int(number * 1000) if m.group(2) in ("kg", "kilo") else int(number)


def text_of(fragment: str) -> str:
    fragment = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    fragment = re.sub(r"<(br|/p|/li|/h\d|/div)\b[^>]*>", "\n", fragment, flags=re.I)
    fragment = html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    lines = [re.sub(r"[ \t ]+", " ", x).strip() for x in fragment.split("\n")]
    return "\n".join(x for x in lines if x)


def short_description(page: str):
    m = re.search(r'woocommerce-product-details__short-description[^>]*>(.*?)</div>', page, re.S)
    return text_of(m.group(1)) if m else None


def parse_product(page: str, url: str) -> dict:
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
    name = text_of(h1.group(1)) if h1 else None
    body_class = re.search(r'<div[^>]+class="[^"]*\bproduct\b[^"]*\bproduct_cat-[^"]*"', page)
    cats = re.findall(r"product_cat-([a-z0-9-]+)", body_class.group(0)) if body_class else []
    if not cats:
        cats = re.findall(r'product-category/([a-z0-9-]+)/', page)[:3]
    labels = {}
    for tag in re.finditer(r'rel="tag"[^>]*>([^<]+)</a>', page):
        labels[tag.group(1).strip()] = True
    images, seen = [], set()
    for src in re.findall(r'data-large_image="([^"]+)"', page):
        src = html.unescape(src)
        if src not in seen:
            seen.add(src)
            images.append(src)
    variations = []
    m = re.search(r'data-product_variations="([^"]+)"', page)
    if m and m.group(1) != "false":
        for v in json.loads(html.unescape(m.group(1))):
            attrs = v.get("attributes") or {}
            gram = next((grams(x) for x in attrs.values() if grams(x)), None)
            grind = next((x for k, x in attrs.items() if "giling" in k.lower()), None)
            others = {k.replace("attribute_", "").replace("pa_", ""): x for k, x in attrs.items() if not grams(x) and "giling" not in k.lower()}
            variations.append({
                "sku": v.get("sku") or str(v.get("variation_id")), "gram": gram, "grind": grind, "lain": others,
                "price": v.get("display_price"), "regular": v.get("display_regular_price"),
                "in_stock": bool(v.get("is_in_stock")), "max_qty": v.get("max_qty") or None,
            })
    single = {}
    if not variations:
        for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
            try:
                data = json.loads(block)
            except ValueError:
                continue
            for node in data.get("@graph", [data]) if isinstance(data, dict) else data:
                if isinstance(node, dict) and node.get("@type") == "Product" and node.get("offers"):
                    offer = node["offers"][0] if isinstance(node["offers"], list) else node["offers"]
                    spec = offer.get("priceSpecification") or {}
                    spec = spec[0] if isinstance(spec, list) and spec else spec
                    single = {"price": offer.get("price") or spec.get("price"), "in_stock": "InStock" in str(offer.get("availability"))}
    desc = re.search(r'id="tab-description"[^>]*>(.*?)(?:</div>\s*){1,3}<(?:div|section)[^>]+(?:id="tab-|class="[^"]*related)', page, re.S)
    regular_el = re.search(r'<del[^>]*>.*?<bdi>.*?([\d.,]+)\s*</bdi>', page, re.S)
    return {
        "url": url, "slug": url.rstrip("/").rsplit("/", 1)[1], "name": name, "categories": cats, "tags": list(labels),
        "images": images, "variations": variations, "single": single, "description": text_of(desc.group(1)) if desc else short_description(page),
        "regular_el": regular_el.group(1) if regular_el else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="unduh ulang semua halaman")
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    urls = sorted(set(re.findall(r"<loc>(https://sakhacoffee\.com/product/[^<]+)</loc>", get(SITEMAP))))
    print(f"Sitemap: {len(urls)} produk", flush=True)
    products, failed = [], []
    for n, url in enumerate(urls, 1):
        cache = RAW / (url.rstrip("/").rsplit("/", 1)[1][:150] + ".html")
        if cache.exists() and not args.refresh:
            page = cache.read_text(encoding="utf-8")
        else:
            time.sleep(random.uniform(*DELAY))
            try:
                page = get(url)
            except (urllib.error.URLError, TimeoutError) as e:
                failed.append(f"{url}: {e}")
                print(f"{n}/{len(urls)} GAGAL {e}", flush=True)
                continue
            cache.write_text(page, encoding="utf-8")
        product = parse_product(page, url)
        missing = [k for k in ("name", "images") if not product[k]]
        if missing or not (product["variations"] or product["single"].get("price")):
            failed.append(f"{url}: field hilang {missing or ['harga']}")
            print(f"{n}/{len(urls)} GAGAL field hilang {missing or ['harga']}: {url}", flush=True)
            continue
        products.append(product)
        print(f"{n}/{len(urls)} {product['name']}", flush=True)
    (OUT / "raw-products.json").write_text(json.dumps(products, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "failed.txt").write_text("\n".join(failed) + "\n", encoding="utf-8")
    print(f"Selesai: {len(products)} terbaca, {len(failed)} gagal.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
