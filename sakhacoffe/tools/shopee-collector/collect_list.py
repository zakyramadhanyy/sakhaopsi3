"""Tahap 1: daftar produk toko, urutan Terpopuler, halaman demi halaman."""
from __future__ import annotations

import json
import re
from collections import Counter

import config
from common import FormatChanged, Session, load_state, log, pause, read_list, save_state, write_json
from parsers import FormatError, is_list_endpoint, parse_list_payload, product_url

CARD_JS = """() => [...document.querySelectorAll('a[href*="-i."]')].map(a => ({
  href: a.getAttribute('href'),
  text: a.innerText || '',
  img: (a.querySelector('img') || {}).src || null,
}))"""
CARD_ID = re.compile(r"-i\.(\d+)\.(\d+)")


def parse_cards(cards: list) -> list:
    out = {}
    for card in cards:
        m = CARD_ID.search(card.get("href") or "")
        if not m:
            continue
        shopid, itemid = int(m.group(1)), int(m.group(2))
        lines = [x.strip() for x in (card.get("text") or "").split("\n") if x.strip()]
        price = next((re.sub(r"\D", "", x) for x in lines if re.search(r"^(Rp)?\s*\d[\d.]*$", x) and len(re.sub(r"\D", "", x)) >= 4), None)
        sold = next((x for x in lines if "terjual" in x.lower()), None)
        out[itemid] = {
            "itemid": itemid, "shopid": shopid, "url": product_url(shopid, itemid),
            "name": max(lines, key=len) if lines else None,
            "price": int(price) if price else None, "price_before_discount": None, "discount": None,
            "sold_text": sold, "sold": None, "rating": None, "cover": card.get("img"),
            "shop_category": None, "shop_category_ids": None, "source": "dom",
        }
    return list(out.values())


def page_url(n: int) -> str:
    return f"{config.SHOP_URL}?page={n}&sortBy=pop&tab=0#product_list"


def collect(session: Session, resume: bool = True, max_pages=None) -> dict:
    config.RAW.mkdir(parents=True, exist_ok=True)
    state = load_state()
    if not resume:
        state["list"] = {"next_page": 0, "finished": False, "shopid": None}
        if config.LIST_FILE.exists():
            config.LIST_FILE.unlink()
        save_state(state)
    if state["list"]["finished"]:
        log("Daftar produk sudah selesai sebelumnya, dilewati.")
        return state
    known = {item["itemid"] for item in read_list()}
    endpoints = Counter(json.loads(config.ENDPOINTS.read_text(encoding="utf-8")) if config.ENDPOINTS.exists() else {})
    empty_pages, done_now = 0, 0
    n = state["list"]["next_page"]
    while True:
        if max_pages is not None and done_now >= max_pages:
            log(f"Batas uji --max-pages {max_pages} tercapai.")
            return state
        log(f"Halaman toko ke-{n + 1}")
        session.visit(page_url(n))
        session.wait_for_json(is_list_endpoint)
        items = {}
        for endpoint, payload in session.drain(is_list_endpoint):
            try:
                found = parse_list_payload(payload)
            except FormatError as e:
                write_json(config.DEBUG / f"list-page-{n}.json", {"endpoint": endpoint, "payload": payload})
                raise FormatChanged(f"Format JSON berubah pada {endpoint}. {e}. Contoh disimpan di {config.DEBUG}.")
            if found:
                endpoints[endpoint] += 1
            for item in found:
                items.setdefault(item["itemid"], item)
        if not items:
            log("Tidak ada JSON produk tertangkap, memakai kartu di DOM sebagai cadangan.")
            for item in parse_cards(session.page.evaluate(CARD_JS)):
                items.setdefault(item["itemid"], item)
        if n == 0 and not items and not known:
            raise FormatChanged("Halaman pertama tidak menghasilkan produk, baik dari JSON maupun DOM.")
        if items and not state["list"]["shopid"]:
            state["list"]["shopid"] = Counter(i["shopid"] for i in items.values()).most_common(1)[0][0]
        fresh = [i for i in items.values() if i["shopid"] == state["list"]["shopid"] and i["itemid"] not in known]
        with config.LIST_FILE.open("a", encoding="utf-8") as f:
            for item in fresh:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
                known.add(item["itemid"])
        n += 1
        done_now += 1
        state["list"]["next_page"] = n
        save_state(state)
        write_json(config.ENDPOINTS, dict(endpoints))
        log(f"  {len(fresh)} item baru, total {len(known)}")
        empty_pages = 0 if fresh else empty_pages + 1
        if empty_pages >= 2:
            state["list"]["finished"] = True
            save_state(state)
            log(f"Daftar selesai: {len(known)} item.")
            return state
        pause("sebelum halaman toko berikutnya")
