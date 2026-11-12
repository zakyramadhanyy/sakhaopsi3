"""Tahap 4: gabungkan raw menjadi products.json, products.csv, gambar WebP, dan report.md."""
from __future__ import annotations

import csv
import json
from collections import defaultdict

import pandas as pd
from PIL import Image, ImageOps
from slugify import slugify

import config
from common import load_state, log, write_json
from normalize import base_name, category_from_text, classify_options, clean_name, group_key, parse_specs, size_from_text

SIZES = {"": 800, "-400": 400}
WEBP_QUALITY = 80
MAX_IMAGE_BYTES = 200 * 1024


def read_items() -> list:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(config.ITEMS.glob("*.json"))]


def read_overrides() -> dict:
    if not config.CATEGORY_OVERRIDES.exists():
        return {}
    with config.CATEGORY_OVERRIDES.open(encoding="utf-8-sig", newline="") as f:
        return {row["itemid"].strip(): row["category"].strip() for row in csv.DictReader(f) if row.get("itemid")}


def listing_variants(detail: dict, cleaned: str) -> list:
    variants = []
    for model in detail["models"]:
        kinds = classify_options(detail.get("tier_variations") or [], model.get("options") or [])
        sku = model.get("sku") or f"{detail['itemid']}-{model.get('modelid') or 0}"
        variant = {
            "sku": str(sku),
            "size": kinds["size"] or size_from_text(cleaned),
            "grind": kinds["grind"],
            "price": model["price"],
            "originalPrice": model.get("price_before_discount"),
            "stock": model.get("stock"),
        }
        if kinds["other"]:
            variant["option"] = "; ".join(kinds["other"])
        variants.append(variant)
    return variants


def merge_group(key: str, listings: list, overrides: dict, report: dict) -> dict:
    listings = sorted(listings, key=lambda d: (-(d.get("sold") or 0), d["itemid"]))
    main = listings[0]
    cleaned = clean_name(main["name"])
    name = base_name(cleaned)
    variants, seen = [], {}
    for detail in listings:
        for variant in listing_variants(detail, clean_name(detail["name"])):
            combo = (variant["size"], variant["grind"], variant.get("option"))
            if combo in seen:
                report["conflicts"].append(f"{name}: {combo} ada di lebih dari satu listing, dipakai {seen[combo]}, dilewati {variant['sku']}")
                continue
            seen[combo] = variant["sku"]
            variants.append(variant)
    in_stock = [v for v in variants if v["stock"]]
    pool = in_stock or variants
    cheapest = min(pool, key=lambda v: v["price"])
    original = cheapest.get("originalPrice")
    original = original if original and original > cheapest["price"] else None
    rated = [(d["rating"], d["rating_count"]) for d in listings if d.get("rating") and d.get("rating_count")]
    total_rated = sum(c for _, c in rated)
    solds = [d["sold"] for d in listings if d.get("sold") is not None]
    specs = parse_specs(main.get("attributes"), main.get("description"))
    for detail in listings[1:]:
        extra = parse_specs(detail.get("attributes"), detail.get("description"))
        for field, value in specs.items():
            specs[field] = value or extra[field]
    category = next((overrides[str(d["itemid"])] for d in listings if str(d["itemid"]) in overrides), None) or category_from_text(
        *[d.get("list", {}).get("shop_category") for d in listings], *[d["name"] for d in listings]
    )
    product = {
        "id": str(main["itemid"]),
        "slug": slugify(name) or key,
        "name": name,
        "category": category,
        "description": main.get("description"),
        "specs": specs,
        "images": [],
        "variants": variants,
        "priceFrom": cheapest["price"],
        "originalPriceFrom": original,
        "discountPercent": round((original - cheapest["price"]) / original * 100) if original else None,
        "rating": round(sum(r * c for r, c in rated) / total_rated, 2) if total_rated else None,
        "ratingCount": total_rated or None,
        "soldCount": sum(solds) if solds else None,
        "shopeeUrl": main["url"],
        "isActive": bool(in_stock),
    }
    if len(listings) > 1:
        report["merged"].append(f"{name}: " + " + ".join(d["name"] for d in listings))
    for detail in listings:
        after = clean_name(detail["name"])
        if after != detail["name"]:
            report["renamed"].append((detail["name"], after))
    return product, main


def convert_images(product: dict, main: dict, report: dict) -> None:
    folder = config.PUBLIC_PRODUCTS / product["slug"]
    folder.mkdir(parents=True, exist_ok=True)
    source_dir = config.IMAGES / str(main["itemid"])
    paths = []
    for n in range(1, len(main["images"]) + 1):
        source = source_dir / f"{n}.jpg"
        if not source.exists():
            report["missing_image_files"].append(f"{product['name']}: gambar {n}")
            continue
        for suffix, size in SIZES.items():
            target = folder / f"{n}{suffix}.webp"
            if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
                with Image.open(source) as image:
                    fitted = ImageOps.pad(image.convert("RGB"), (size, size), color=(255, 255, 255))
                    fitted.save(target, "WEBP", quality=WEBP_QUALITY, method=6)
            if target.stat().st_size > MAX_IMAGE_BYTES:
                report["big_images"].append(f"{target.relative_to(config.ROOT)}: {target.stat().st_size // 1024} KB")
        paths.append(f"/products/{product['slug']}/{n}.webp")
    product["images"] = paths


def write_report(products: list, items: list, state: dict, report: dict) -> None:
    listed = len({json.loads(line)["itemid"] for line in config.LIST_FILE.read_text(encoding="utf-8").splitlines() if line.strip()}) if config.LIST_FILE.exists() else 0
    by_cat = defaultdict(int)
    for p in products:
        by_cat[p["category"] or "(tanpa kategori)"] += 1
    empty = defaultdict(list)
    for p in products:
        for field in ("origin", "process", "roastLevel"):
            if not p["specs"][field]:
                empty[f"specs.{field}"].append(p["name"])
        if not p["specs"]["tastingNotes"]:
            empty["specs.tastingNotes"].append(p["name"])
        for field in ("description", "rating", "ratingCount", "soldCount"):
            if p[field] in (None, ""):
                empty[field].append(p["name"])
    other = sorted({p["name"] for p in products for v in p["variants"] if v.get("option")})
    partial = [d["name"] for d in items if d.get("partial")]
    lines = ["# Laporan impor Shopee", "", "## Ringkasan", "",
             f"- Listing di daftar: {listed}", f"- Listing dengan detail: {len(items)}", f"- Produk setelah digabung: {len(products)}",
             f"- Item gagal: {len(state['detail']['failed'])}", f"- Item dari cadangan DOM (variasi dan stok tidak lengkap): {len(partial)}", "",
             "## Produk per kategori", ""]
    lines += [f"- {k}: {v}" for k, v in sorted(by_cat.items())]
    lines += ["", "## Pemetaan kategori lama (perlu konfirmasi)", "", "- Kopi Tanpa Ampas → Specialty Coffee Drip (`specialty-drip`)", "- Commodity Blend → House Blend (`house-blend`)",
              "- Specialty Filter → `specialty-filter`", "- Specialty Espresso → `specialty-espresso`", "",
              "Kategori hanya diisi dari kategori toko, frasa persis di nama listing, atau `category-overrides.csv`.", ""]

    def section(title: str, rows: list) -> None:
        lines.extend([f"## {title} ({len(rows)})", ""])
        lines.extend(f"- {row}" for row in rows) if rows else lines.append("Tidak ada.")
        lines.append("")

    section("Item gagal", [f"{k}: {v}" for k, v in sorted(state["detail"]["failed"].items())])
    section("Gambar gagal diunduh", [f"{k}: {'; '.join(v)}" for k, v in sorted(state["images"]["failed"].items())])
    section("Item memakai cadangan DOM", partial)
    section("Produk tanpa gambar", [p["name"] for p in products if not p["images"]])
    section("Produk tanpa harga", [p["name"] for p in products if not p["priceFrom"]])
    section("Produk tanpa kategori", [p["name"] for p in products if not p["category"]])
    section("Listing yang digabung (mohon dicek)", report["merged"])
    section("Varian kembar antar listing", report["conflicts"])
    section("Produk dengan pilihan di luar ukuran dan gilingan (bisa jadi perlu dipisah)", other)
    section("Gambar lebih dari 200 KB", report["big_images"])
    section("Nama sebelum → sesudah", [f"{a} → {b}" for a, b in report["renamed"]])
    lines.extend(["## Field kosong", ""])
    if empty:
        for field, names in sorted(empty.items()):
            lines.append(f"- {field}: {len(names)} produk")
    else:
        lines.append("Tidak ada.")
    lines.extend(["", "Foto outlet tidak bisa dibedakan otomatis dari foto produk. Mohon tinjau galeri secara visual.", ""])
    config.REPORT.parent.mkdir(parents=True, exist_ok=True)
    config.REPORT.write_text("\n".join(lines), encoding="utf-8")


def run() -> list:
    state = load_state()
    items = read_items()
    if not items:
        raise SystemExit("Belum ada data detail. Jalankan tahap detail dulu.")
    overrides = read_overrides()
    report = defaultdict(list)
    groups = defaultdict(list)
    for detail in items:
        groups[group_key(clean_name(detail["name"]))].append(detail)
    products, used_slugs = [], set()
    for key, listings in groups.items():
        product, main = merge_group(key, listings, overrides, report)
        if product["slug"] in used_slugs:
            product["slug"] = f"{product['slug']}-{product['id']}"
        used_slugs.add(product["slug"])
        convert_images(product, main, report)
        products.append(product)
    products.sort(key=lambda p: (-(p["soldCount"] or 0), p["slug"]))
    write_json(config.PRODUCTS_JSON, products)
    rows = [{"id": p["id"], "slug": p["slug"], "name": p["name"], "category": p["category"], **v,
             "priceFrom": p["priceFrom"], "rating": p["rating"], "ratingCount": p["ratingCount"],
             "soldCount": p["soldCount"], "isActive": p["isActive"], "shopeeUrl": p["shopeeUrl"]}
            for p in products for v in p["variants"]]
    config.PRODUCTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(config.PRODUCTS_CSV, index=False, encoding="utf-8-sig")
    write_report(products, items, state, report)
    log(f"Selesai: {len(items)} listing → {len(products)} produk. Laporan: {config.REPORT}")
    return products
