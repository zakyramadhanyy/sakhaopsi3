#!/usr/bin/env python3
"""Ubah data/site/raw-products.json menjadi data/site/products.json dan foto WebP di produk/<slug>/.

Spesifikasi hanya dari baris berlabel di deskripsi situs. Yang tidak tertulis dibiarkan kosong.
Rating dan terjual tidak ada di situs, jadi tidak diisi. Bisa dijalankan ulang; file yang sudah ada dilewati.
"""
from __future__ import annotations

import csv
import io
import json
import random
import shutil
import re
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "data" / "site"
CACHE = SITE / "images"
PHOTOS = ROOT / "produk"
USER_AGENT = "SakhaCatalogImport/1.0"
GRINDS = {"biji-kopi": "Biji Utuh", "bubuk-kasar": "Kasar (French Press)", "bubuk-sedang": "Sedang (V60)", "bubuk-halus": "Halus (Espresso)"}
GRIND_ORDER = list(GRINDS.values())

# id lama dipertahankan agar pesanan contoh, B2B, dan flash sale tetap menunjuk produk yang sama.
OLD_IDS = (("Moonlime", "p1"), ("Alexander", "p2"), ("Blueberry Well", "p3"), ("SUN OF PEACH", "p4"), ("Milkinho", "p5"),
           ("Bleubailess", "p6"), ("75% Arabika", "p7"), ("50% Arabika", "p8"), ("Fruit Basket", "p9"), ("VICTORIA", "p10"),
           ("Honey Berry", "p11"), ("Drip Bag Arabica", "p12"))
SPECIAL_NAMES = (("SUN OF PEACH", "Sun of Peach"), ("VICTORIA LA MARQUEESA", "Victoria La Marqueesa"), ("FROOTHIOUS SHOT", "The Froothious Shot"),
                 ("SWEET BOUQUET", "Sweet Bouquet"), ("Breakfast Blend", "Breakfast Blend"), ("Gayo Blend", "Gayo Blend"), ("Gregory Blend", "Gregory Blend"),
                 ("Kintamani Natural", "Arabika Bali Kintamani Natural"), ("Paket Hemat Kopi V60", "Paket Hemat Kopi V60 Manual Brew Set"),
                 ("Fruit Basket", "7 Fruit Basket Drip Bag"), ("Drip Bag Arabica", "Drip Bag Arabica Specialty"), ("Hampers Special Edition", "Hampers Special Edition 2"),
                 ("Tubruk Hitam", "Arabika Tubruk Hitam Tanpa Gula"), ("Glory Chery Burst", "Glory Cherry Burst"))
CATEGORY_LABELS = {"specialty-filter": "Specialty Filter", "specialty-espresso": "Specialty Espresso", "commodity-blend": "Commodity Blend",
                   "kopi-tanpa-ampas": "Kopi Tanpa Ampas", "house-blend": "House Blend", "single-origin": "Single Origin", "hampers": "Hampers", "lainnya": "Lainnya"}
DRIP_FIX = {"victoria-la-marque": "Victoria La Marqueesa", "sun-of-peach": "Sun of Peach"}
ROAST_LEVELS = (("light to medium", 2), ("medium to light", 2), ("medium to dark", 4), ("dark to medium", 4), ("full city", 4), ("light", 1), ("medium", 3), ("city", 3), ("dark", 5))
CATEGORY_OVERRIDES = {"Arabika Bali Kintamani Natural": "single-origin"}  # kosong di situs, seri yang sama berkategori single origin
LABELS = {"origin": r"origin", "process": r"process|proses|proces", "roast": r"profil\s+roast(ing)?|roast\s+profile|roast\s+level|level\s+roast|profil\s*/\s*level\s+roasting",
          "notes": r"flavou?rs?\s+notes?(\s*\(bias(\s*/\s*semu)?\))?|tasting\s+notes?", "acidity": r"acidity", "body": r"body", "packaging": r"packaging", "catatan": r"note", "variety": r"variety|varietas", "altitude": r"altitude|ketinggian", "jenis": r"jenis"}
NOT_STORY = re.compile(r"^(Description|Deskripsi|Halo,? Sobat Brewer!?|Dibalik Nama\s*:?|Tentang .{1,40}|NO\. SERTIFIKAT.*|DINAS KESEHATAN.*|Sakha Coffee Sudah Tersertifikasi.*|\d+/B\.\d+.*|Roast Date.*|Expired Date.*|->.*|Komposisi\s*:?|[–\-]?\s*Kami menjamin produk.*|[–\-]?\s*Masa Kadaluarsa.*|Halo,? Teman Sakha,?|Semua Produk Sakha Coffee Sudah Tersertifikasi.*|Pastikan untuk memilih.*|[–\-]?\s*(BIJI KOPI|GILING (KASAR|SEDANG|HALUS))\s*:.*)$", re.I)
GRAM = re.compile(r"(\d+(?:[.,]\d+)?)\s*-?\s*(kg|kilo|grm|gram|gr|g|r)\b", re.I)


def gram_of(value):
    m = GRAM.search(str(value or ""))
    if not m:
        return None
    number = float(m.group(1).replace(",", "."))
    return int(number * 1000) if m.group(2).lower() in ("kg", "kilo") else int(number)


def clean_name(raw: str) -> str:
    for needle, name in SPECIAL_NAMES:
        if needle.lower() in raw.lower():
            return name
    name = re.sub(r"\s+by Sakha Coffee Roastery", "", raw, flags=re.I)
    name = re.sub(r"^Biji Kopi\s+", "", name, flags=re.I)
    name = re.sub(r"\s*Arabica Biji Kopi\b|\s+Biji Kopi( Komersil| Arabika| Single Origin| House Blend)?\b", " ", name, flags=re.I)
    name = re.sub(r"\bSingle Origin\b", " ", name, flags=re.I)
    return re.sub(r"\s{2,}", " ", name).strip()


def parse_description(text: str) -> dict:
    found = {"story": "", "origin": "", "process": "", "roast": "", "notes": [], "variety": "", "altitude": "", "jenis": "", "komposisi": "", "acidity": "", "body": "", "packaging": "", "catatan": "", "sertHalal": "", "pirt": "", "ketahanan": []}
    joined = text or ""
    m_halal = re.search(r"SERTIFIKAT HALAL\s*:?\s*(ID\d+)", joined, re.I)
    m_pirt = re.search(r"P-IRT\s*(?:Nomor)?\s*:?\s*([0-9][0-9A-Za-z/.\-]+)", joined)
    found["sertHalal"] = m_halal.group(1) if m_halal else ""
    found["pirt"] = m_pirt.group(1) if m_pirt else ""
    found["ketahanan"] = [x.strip()[2:].strip() for x in joined.split("\n") if re.match(r"^(->|[–\-]\s*(Kami menjamin produk|Masa Kadaluarsa))", x.strip())]
    found["ketahanan"] = [re.sub(r"^(->|[–\-])\s*", "", x.strip()) for x in joined.split("\n") if re.match(r"^(->|[–\-]\s*(Kami menjamin produk|Masa Kadaluarsa))", x.strip())]
    lines = (text or "").split("\n")
    story, in_story = [], True
    for i, line in enumerate(lines):
        if re.fullmatch(r"Komposisi\s*:?", line.strip(), re.I) and i + 1 < len(lines):
            found["komposisi"] = lines[i + 1].strip()
        m = re.match(r"^[^A-Za-z0-9]*([A-Za-z ()]{3,30}?)\s*:\s*(.+)$", line)
        label = None
        if m:
            label = next((k for k, pat in LABELS.items() if re.fullmatch(pat, m.group(1).strip(), re.I)), None)
        if label:
            in_story = False
            value = m.group(2).strip()
            if label == "notes":
                found["notes"] = found["notes"] or [x.strip().capitalize() for x in re.split(r",|&|/", value) if x.strip()]
            elif not found[label]:
                found[label] = value
        elif in_story and not NOT_STORY.match(line.strip()) and line.strip() != found["komposisi"] and not line.strip().isupper() and " | " not in line:
            if re.match(r"^(Petunjuk|Catatan|Note\b|Silikan|Silakan pilih|Disclaimer)", line.strip(), re.I):
                in_story = False
            else:
                story.append(line.strip())
    found["story"] = "\n".join(story).strip()
    if len(found["story"]) < 100:  # sapaan atau pertanyaan tanpa isi, bukan deskripsi
        found["story"] = ""
    return found


def roast_level(text: str):
    t = (text or "").lower().split("/")[0]
    return next((level for needle, level in ROAST_LEVELS if needle in t), None)


def slug_of(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("%", "")).strip("-")[:48]


def download(url: str, target: Path) -> None:
    if target.exists() and target.stat().st_size:
        return
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    time.sleep(random.uniform(0.3, 0.6))


def convert(source: Path, target: Path, size: int) -> None:
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        ImageOps.pad(image.convert("RGB"), (size, size), color=(255, 255, 255)).save(target, "WEBP", quality=80, method=6)


def build_one(raw: dict, used: set, report: dict):
    variations = raw["variations"]
    name = clean_name(raw["name"])
    if not variations:
        report["excluded"].append(f"{name}: harga kosong atau stok habis di situs")
        return None
    prices, grinds, others, stock = {}, [], [], 0
    for v in variations:
        lain = dict(v.get("lain") or {})
        gram = v.get("gram") or next((gram_of(x) for x in lain.values() if gram_of(x)), None)
        grind = v.get("grind")
        for key, value in list(lain.items()):
            if value in GRINDS:
                grind = value
                del lain[key]
            elif gram_of(value):
                del lain[key]
        if grind in GRINDS and GRINDS[grind] not in grinds:
            grinds.append(GRINDS[grind])
        for key, value in lain.items():
            if key == "varian-drip":
                label = DRIP_FIX.get(value) or value.replace("-", " ").title()
                if label not in others:
                    others.append(label)
        if gram:
            old = v.get("regular") if (v.get("regular") or 0) > (v.get("price") or 0) else None
            known = prices.get(gram)
            if known and known["p"] != v["price"]:
                report["price_conflicts"].append(f"{name} {gram}gr: {known['p']} dan {v['price']}")
            prices[gram] = {"p": v["price"], "o": old}
        stock = max(stock, v.get("max_qty") or 0)
    first = variations[0]
    sizes = sorted(prices)
    if sizes:
        price, old = prices[sizes[0]]["p"], prices[sizes[0]]["o"]
    else:
        price = min(v["price"] for v in variations)
        cheapest = next(v for v in variations if v["price"] == price)
        old = cheapest["regular"] if (cheapest.get("regular") or 0) > price else None
    options = [g for g in GRIND_ORDER if g in grinds] or (others if len(others) > 1 else None)
    cats = [c for c in raw["categories"] if c not in ("newrelease", "uncategorized")]
    if name in CATEGORY_OVERRIDES:
        cat = CATEGORY_OVERRIDES[name]
    elif cats:
        cat = cats[0]
    elif "single origin" in raw["name"].lower():
        cat = "single-origin"
    else:
        cat = "lainnya"
        report["no_category"].append(name)
    spec = parse_description(raw.get("description"))
    upload = re.search(r"/uploads/(\d{4})/(\d{2})/", raw["images"][0])
    pid = next((i for needle, i in OLD_IDS if needle.lower() in raw["name"].lower()), None)
    slug = slug_of(name)
    if slug in used:
        slug = f"{slug}-2"
    used.add(slug)
    gallery = []
    for n, url in enumerate(raw["images"], 1):
        ext = url.rsplit(".", 1)[-1].split("?")[0][:4]
        source = CACHE / slug / f"{n}.{ext}"
        try:
            download(url, source)
            convert(source, PHOTOS / slug / f"{n}.webp", 800)
            if n == 1:
                convert(source, PHOTOS / slug / "1-400.webp", 400)
        except Exception as e:
            report["image_errors"].append(f"{name} gambar {n}: {e}")
            continue
        gallery.append(f"produk/{slug}/{n}.webp")
    for f in (PHOTOS / slug).glob("*.webp"):
        if f.stat().st_size > 200 * 1024:
            report["big_images"].append(f"{f.relative_to(ROOT)}: {f.stat().st_size // 1024} KB")
    if not gallery:
        report["excluded"].append(f"{name}: semua foto gagal diunduh")
        return None
    for field in ("origin", "process", "roast"):
        if not spec[field]:
            report["empty_" + field].append(name)
    if not spec["notes"]:
        report["empty_notes"].append(name)
    return {
        "id": pid or slug, "slug": slug, "name": name, "sourceName": raw["name"], "url": raw["url"], "cat": cat, "catLabel": CATEGORY_LABELS[cat],
        "price": price, "oldPrice": old, "img": f"produk/{slug}/1-400.webp", "galeri": gallery,
        "added": f"{upload.group(1)}-{upload.group(2)}-01" if upload else None, "badge": "new" if "newrelease" in raw["categories"] else None,
        "weight": sizes[0] if sizes else 0, "sizes": sizes or None, "harga": {str(k): v for k, v in prices.items()} or None,
        "grinds": options, "opsiLabel": "Varian" if options and options[0] not in GRIND_ORDER else None, "stock": stock,
        "origin": spec["origin"], "process": spec["process"], "roast": spec["roast"].title() if spec["roast"].islower() else spec["roast"], "komposisi": spec["komposisi"], "roastLevel": roast_level(spec["roast"]),
        "notes": spec["notes"], "variety": spec["variety"], "altitude": spec["altitude"], "jenis": spec["jenis"], "acidity": spec["acidity"], "body": spec["body"], "packaging": spec["packaging"], "catatan": spec["catatan"], "sertHalal": spec["sertHalal"], "pirt": spec["pirt"], "ketahanan": spec["ketahanan"], "desc": spec["story"],
        "rating": None, "reviews": None, "sold": None, "halal": "halal" in (raw.get("description") or "").lower(),
    }


CATEGORY_ORDER = ["specialty-filter", "specialty-espresso", "house-blend", "commodity-blend", "single-origin", "kopi-tanpa-ampas", "hampers", "lainnya"]
START, END = "/*PRODUK_SITUS_MULAI*/", "/*PRODUK_SITUS_SELESAI*/"


def inject(products: list) -> None:
    """Tulis data ke index.html di antara dua penanda, supaya halaman tetap satu file tanpa proses build."""
    page = ROOT / "index.html"
    text = page.read_text(encoding="utf-8")
    if START not in text:
        return
    ordered = sorted(products, key=lambda p: (CATEGORY_ORDER.index(p["cat"]), p["badge"] != "new", p["name"]))
    rows = [json.dumps({k: v for k, v in p.items() if k not in ("slug", "sourceName")}, ensure_ascii=False).replace("</", "<\\/") for p in ordered]
    a, b = text.index(START) + len(START), text.index(END)
    page.write_text(text[:a] + "[\n" + ",\n".join(rows) + "\n]" + text[b:], encoding="utf-8")


def main() -> int:
    raws = json.loads((SITE / "raw-products.json").read_text(encoding="utf-8"))
    used, products = set(), []
    report = {k: [] for k in ("excluded", "price_conflicts", "no_category", "image_errors", "big_images", "empty_origin", "empty_process", "empty_roast", "empty_notes")}
    report["excluded"].extend(f"{re.search(r'/product/([^/]+)/', line).group(1)}: stok habis, harga tidak tersedia di situs" for line in
                              (SITE / "failed.txt").read_text(encoding="utf-8").splitlines() if line.strip())
    for n, raw in enumerate(raws, 1):
        product = build_one(raw, used, report)
        print(f"{n}/{len(raws)} {product['id'] + ' | ' + product['name'] if product else 'dilewati: ' + raw['name']}", flush=True)
        if product:
            products.append(product)
    keep = {p["slug"] for p in products}
    for base in (PHOTOS, CACHE):
        for folder in base.iterdir():
            if folder.is_dir() and folder.name not in keep:
                shutil.rmtree(folder)
    ids = [p["id"] for p in products]
    assert len(ids) == len(set(ids)), "id produk kembar"
    missing = [i for _, i in OLD_IDS if i not in ids]
    assert not missing, f"id lama tidak terpetakan: {missing}"
    (SITE / "products.json").write_text(json.dumps(products, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (SITE / "products.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "nama", "kategori", "gramasi", "harga", "harga_coret", "pilihan", "jumlah_foto", "sumber"])
        for p in products:
            rows = [(k, v["p"], v["o"]) for k, v in sorted((p["harga"] or {}).items(), key=lambda x: int(x[0]))] or [("", p["price"], p["oldPrice"])]
            for gram, price, old in rows:
                writer.writerow([p["id"], p["name"], p["catLabel"], gram, price, old or "", ", ".join(p["grinds"] or []), len(p["galeri"]), p["url"]])
    (SITE / "import-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    inject(products)
    print(f"Selesai: {len(products)} produk.", {k: len(v) for k, v in report.items()}, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
