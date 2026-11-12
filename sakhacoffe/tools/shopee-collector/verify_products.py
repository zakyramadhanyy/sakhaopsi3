"""Cocokkan beberapa produk acak dengan halaman Shopee: nama, harga, varian, gambar pertama."""
from __future__ import annotations

import json
import random
import re

import config
from common import Session, log, pause

PAGE_JS = """() => ({
  title: (document.querySelector('h1') || {}).innerText || null,
  text: document.body.innerText,
  og: (document.querySelector('meta[property="og:image"]') || {}).content || null,
})"""


def rupiah(n: int) -> str:
    return "{:,}".format(n).replace(",", ".")


def image_hash(url) -> str:
    return re.sub(r"_tn$", "", (url or "").split("/")[-1].split("?")[0])


def run(session: Session, count: int = 5) -> list:
    products = json.loads(config.PRODUCTS_JSON.read_text(encoding="utf-8"))
    config.VERIFY_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for position, product in enumerate(random.sample(products, min(count, len(products))), 1):
        log(f"Verifikasi {position}/{count}: {product['name']}")
        raw = json.loads((config.ITEMS / f"{product['id']}.json").read_text(encoding="utf-8"))
        session.visit(product["shopeeUrl"])
        session.page.wait_for_timeout(3000)
        page = session.page.evaluate(PAGE_JS)
        options = sorted({o for m in raw["models"] for o in m["options"] if o})
        checks = {
            "nama": (page["title"] or "").strip() == raw["name"].strip(),
            "harga": rupiah(min(m["price"] for m in raw["models"])) in page["text"],
            "varian": all(o in page["text"] for o in options),
            "gambar pertama": image_hash(page["og"]) == image_hash(raw["images"][0]),
        }
        shot = config.VERIFY_DIR / f"{product['slug']}.png"
        session.page.screenshot(path=str(shot))
        rows.append((product["name"], checks, product["shopeeUrl"], shot))
        if position < count:
            pause("sebelum produk berikutnya")
    lines = ["# Pencocokan dengan halaman Shopee", "", "| Produk | Nama | Harga | Varian | Gambar pertama |", "|---|---|---|---|---|"]
    for name, checks, _, _ in rows:
        lines.append(f"| {name} | " + " | ".join("cocok" if v else "BEDA" for v in checks.values()) + " |")
    lines.extend(["", "Harga bisa berbeda bila Shopee mengubahnya setelah data diambil. Tangkapan layar ada di folder `verify/`.", ""])
    config.VERIFY_REPORT.write_text("\n".join(lines), encoding="utf-8")
    log(f"Hasil: {config.VERIFY_REPORT}")
    return rows
