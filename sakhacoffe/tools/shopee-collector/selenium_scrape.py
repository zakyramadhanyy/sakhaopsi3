#!/usr/bin/env python3
"""Ambil gambar, nama produk, gramasi, dan ukuran dari halaman toko dengan Selenium.

Login dilakukan manual. Skrip hanya membaca halaman dan respons yang dimuat browser, tidak menyamarkan
otomasi, dan tidak mencoba melewati verifikasi.

    python selenium_scrape.py --limit 3 --max-list-pages 1     # uji dulu
    python selenium_scrape.py                                  # penuh, melanjutkan yang belum selesai
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from collections import Counter

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from slugify import slugify

import config
from common import log, write_json
from download_images import fetch
from normalize import SIZE, SIZE_TIER, clean_name, size_from_text
from parsers import image_url, is_detail_endpoint, walk

TARGET_URL = "https://shopee.co.id/sakha.coffee?entryPoint=ShopBySearch&searchKeyword=sakha%20coffee%20roastery"
LOGIN_URL = "https://shopee.co.id/buyer/login"
HEADLESS = False  # hanya uji lokal yang mengubah ini
WALL_URL = re.compile(r"/verify/|/buyer/login|/account/login|captcha", re.I)
WALL_TEXT = ("Masuk Diperlukan", "Login Required")
PRODUCT_LINK = re.compile(r"-i\.(\d+)\.(\d+)|/product/(\d+)/(\d+)")

CARDS_JS = """return [...document.querySelectorAll('a[href*="-i."],a[href*="/product/"]')].map(a => {
  const img = a.querySelector('img');
  return { href: a.href, text: a.innerText || '', alt: img ? img.alt : '', img: img ? (img.currentSrc || img.src) : '' };
})"""

DETAIL_JS = """
const hasil = { name: (document.querySelector('h1') || {}).innerText || null, images: [], tiers: [] };
const tambah = (u) => { if (u && !hasil.images.includes(u)) hasil.images.push(u); };
for (const el of document.querySelectorAll('script[type="application/ld+json"]')) {
  try {
    const j = JSON.parse(el.textContent);
    for (const x of Array.isArray(j) ? j : [j]) if (x['@type'] === 'Product' && x.image) [].concat(x.image).forEach(tambah);
  } catch (e) {}
}
const og = document.querySelector('meta[property="og:image"]');
if (og) tambah(og.content);
for (const sec of document.querySelectorAll('section')) {
  const label = sec.firstElementChild ? sec.firstElementChild.innerText.trim() : '';
  if (!/ukuran|size|berat|gilingan|grind|variasi|varian|pilihan|gramasi/i.test(label) || label.length > 40) continue;
  const options = [...sec.querySelectorAll('button')].map(b => b.innerText.trim()).filter(Boolean);
  if (options.length) hasil.tiers.push({ name: label, options });
}
return hasil;
"""


class Stop(Exception):
    pass


def paths() -> dict:
    base = config.DATA / "selenium"
    return {"base": base, "items": base / "items", "images": base / "images", "cards": base / "cards.json",
            "json": base / "products.json", "csv": base / "products.csv", "profile": config.DATA / "profile-selenium"}


def pause(label: str = "") -> None:
    wait = random.uniform(*config.DELAY_RANGE)
    log(f"Jeda berikutnya {wait:.1f} detik{' ' + label if label else ''}")
    time.sleep(wait)


class Browser:
    def __init__(self):
        self.pages = 0
        self.blocks = 0
        self.login_opened = False

    def __enter__(self) -> "Browser":
        options = Options()
        options.add_argument(f"--user-data-dir={paths()['profile']}")
        options.add_argument("--lang=id-ID")
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        if HEADLESS:
            options.add_argument("--headless=new")
        self.driver = webdriver.Chrome(options=options)
        self.driver.execute_cdp_cmd("Network.enable", {})
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass

    def is_wall(self) -> bool:
        if WALL_URL.search(self.driver.current_url):
            return True
        text = self.driver.execute_script("return document.body ? document.body.innerText.slice(0, 2000) : ''") or ""
        return len(text) < 2000 and any(t in text for t in WALL_TEXT)

    def _wait_for_human(self) -> None:
        deadline = time.time() + config.VERIFICATION_WAIT_SECONDS
        while self.is_wall():
            if time.time() > deadline:
                raise Stop("Login atau verifikasi tidak selesai dalam batas waktu. Progres tersimpan.")
            time.sleep(3)
        log("Lanjut.")

    def _handle_wall(self) -> None:
        if not self.login_opened:
            self.login_opened = True
            self.driver.get(LOGIN_URL)
            log(">>> Silakan login manual di jendela Chrome. Skrip menunggu dan tidak menyentuh apa pun.")
        else:
            self.blocks += 1
            log(f"PERINGATAN: Shopee meminta verifikasi atau login lagi ({self.blocks}/{config.MAX_BLOCKS}).")
            if self.blocks >= config.MAX_BLOCKS:
                raise Stop(f"Sudah {config.MAX_BLOCKS} kali terblokir. Berhenti total, progres tersimpan.")
            log(">>> Selesaikan di jendela Chrome. Skrip menunggu dan tidak menyentuh apa pun.")
        self._wait_for_human()
        pause("sebelum kembali ke halaman tujuan")

    def open(self, url: str, ready_js: str, seconds: float = 12) -> bool:
        """Buka halaman. Shopee bisa mengalihkan beberapa detik setelah termuat, jadi dinding dicek berulang."""
        for _ in range(config.MAX_BLOCKS + 2):
            if self.pages >= config.MAX_PAGES_PER_SESSION:
                raise Stop(f"Batas {config.MAX_PAGES_PER_SESSION} halaman per sesi tercapai. Jalankan lagi nanti untuk melanjutkan.")
            self.driver.get_log("performance")
            self.driver.get(url)
            self.pages += 1
            deadline = time.time() + seconds
            walled = ready = False
            while time.time() < deadline:
                time.sleep(1)
                if self.is_wall():
                    walled = True
                    break
                if self.driver.execute_script(ready_js):
                    ready = True
                    time.sleep(2)  # beri waktu pengalihan yang datang terlambat
                    if self.is_wall():
                        walled, ready = True, False
                    break
            if walled:
                self._handle_wall()
                continue
            return ready
        raise Stop(f"Halaman tetap tidak bisa dibuka: {url}")

    def scroll(self, steps: int = 10) -> None:
        for _ in range(steps):
            self.driver.execute_script("window.scrollBy(0, 700)")
            time.sleep(0.7)

    def loaded_json(self, matcher) -> list:
        """Baca respons JSON yang sudah dimuat halaman. Tidak mengirim request sendiri."""
        out = []
        for entry in self.driver.get_log("performance"):
            message = json.loads(entry["message"])["message"]
            if message.get("method") != "Network.responseReceived":
                continue
            response = message["params"]["response"]
            if not matcher(response["url"]):
                continue
            try:
                body = self.driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": message["params"]["requestId"]})
                out.append(json.loads(body["body"]))
            except Exception:
                pass
        return out


def page_url(n: int) -> str:
    return f"{TARGET_URL}&page={n}"


def parse_cards(raw: list) -> list:
    cards = {}
    for card in raw:
        m = PRODUCT_LINK.search(card.get("href") or "")
        if not m:
            continue
        shopid, itemid = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        lines = [x.strip() for x in (card.get("text") or "").split("\n") if x.strip()]
        name = card.get("alt") or (max(lines, key=len) if lines else None)
        known = cards.get(itemid)
        if known and known["cover"] and known["name"]:
            continue
        cards[itemid] = {"itemid": itemid, "shopid": shopid, "url": card["href"].split("?")[0], "name": name, "cover": card.get("img") or None}
    return list(cards.values())


def collect_cards(browser: Browser, max_pages=None) -> list:
    p = paths()
    saved = json.loads(p["cards"].read_text(encoding="utf-8")) if p["cards"].exists() else {"next_page": 0, "finished": False, "cards": []}
    if saved["finished"]:
        log(f"Daftar sudah selesai: {len(saved['cards'])} produk.")
        return saved["cards"]
    cards = {c["itemid"]: c for c in saved["cards"]}
    n, done_now, empty = saved["next_page"], 0, 0
    while max_pages is None or done_now < max_pages:
        log(f"Halaman toko ke-{n + 1}")
        browser.open(page_url(n), "return document.querySelectorAll('a[href*=\"-i.\"],a[href*=\"/product/\"]').length > 0")
        browser.scroll()
        found = parse_cards(browser.driver.execute_script(CARDS_JS))
        if not found and n == 0 and not cards:
            raise Stop("Halaman pertama tidak menampilkan produk. Tampilan Shopee berubah atau akses ditolak.")
        if found:
            main_shop = Counter(c["shopid"] for c in found).most_common(1)[0][0]
            found = [c for c in found if c["shopid"] == main_shop]
        fresh = [c for c in found if c["itemid"] not in cards]
        for card in fresh:
            cards[card["itemid"]] = card
        n += 1
        done_now += 1
        log(f"  {len(fresh)} produk baru, total {len(cards)}")
        empty = 0 if fresh else empty + 1
        finished = empty >= 2
        write_json(p["cards"], {"next_page": n, "finished": finished, "cards": list(cards.values())})
        if finished:
            log(f"Daftar selesai: {len(cards)} produk.")
            break
        pause("sebelum halaman toko berikutnya")
    return list(cards.values())


def from_json(payloads: list, itemid: str):
    for payload in payloads:
        for d in walk(payload):
            if str(d.get("item_id", d.get("itemid"))) == str(itemid) and (d.get("title") or d.get("name")):
                images = d.get("images")
                if not images:
                    for other in walk(payload):
                        if isinstance(other.get("product_images"), dict):
                            images = other["product_images"].get("images")
                            break
                tiers = [{"name": t.get("name"), "options": t.get("options") or []} for t in d.get("tier_variations") or [] if isinstance(t, dict)]
                return {"name": d.get("title") or d.get("name"), "images": [image_url(x) for x in images or []], "tiers": tiers, "sumber": "json"}
    return None


def full_size(url: str) -> str:
    return re.sub(r"(_tn|@resize[^/]*)$", "", url.split("?")[0])


def summarize(card: dict, detail: dict) -> dict:
    name = detail.get("name") or card.get("name") or ""
    sizes, others = [], {}
    for tier in detail.get("tiers") or []:
        options = [o for o in tier["options"] if o]
        if not options:
            continue
        is_size = SIZE_TIER.search(tier.get("name") or "") or all(SIZE.search(o) for o in options)
        if is_size:
            sizes.extend(o for o in options if o not in sizes)
        else:
            others[tier.get("name") or "Variasi"] = options
    # Gramasi dari nama atau pilihan yang tertulis. Tidak tertulis berarti kosong, tanpa menebak.
    grams = size_from_text(name)
    return {
        "itemid": card["itemid"], "url": card["url"], "nama_asli": name, "nama": clean_name(name),
        "gramasi": grams or (size_from_text(sizes[0]) if len(sizes) == 1 else None),
        "ukuran": sizes, "variasi_lain": others, "sumber": detail.get("sumber", "dom"),
    }


def save_images(result: dict, urls: list) -> None:
    folder = paths()["images"] / f"{slugify(result['nama'])[:60] or 'produk'}-{result['itemid']}"
    folder.mkdir(parents=True, exist_ok=True)
    saved, seen = [], set()
    for url in urls:
        url = full_size(url)
        if not url or url in seen:
            continue
        seen.add(url)
        target = folder / f"{len(seen)}.jpg"
        if not target.exists() or not target.stat().st_size:
            try:
                body = fetch(url)
            except Exception as e:
                log(f"  GAGAL gambar {len(seen)}: {e}")
                continue
            tmp = target.with_suffix(".part")
            tmp.write_bytes(body)
            os.replace(tmp, target)
            time.sleep(random.uniform(*config.IMAGE_DELAY_RANGE))
        saved.append(str(target.relative_to(config.ROOT)))
    result["gambar"] = saved


def collect_details(browser: Browser, cards: list, limit=None) -> None:
    p = paths()
    p["items"].mkdir(parents=True, exist_ok=True)
    todo = [c for c in cards if not (p["items"] / f"{c['itemid']}.json").exists()]
    if limit is not None:
        todo = todo[:limit]
    log(f"Detail: {len(cards) - len(todo)} sudah ada atau di luar batas, {len(todo)} akan dikunjungi.")
    for position, card in enumerate(todo, 1):
        log(f"Produk {position}/{len(todo)}: {card.get('name') or card['itemid']}")
        browser.open(card["url"], "return !!document.querySelector('h1')")
        time.sleep(2)
        detail = from_json(browser.loaded_json(is_detail_endpoint), card["itemid"])
        if not detail:
            detail = browser.driver.execute_script(DETAIL_JS)
            detail["sumber"] = "dom"
            log("  JSON tidak tertangkap, memakai DOM.")
        urls = [u for u in (detail.get("images") or []) if u] or ([card["cover"]] if card.get("cover") else [])
        result = summarize(card, detail)
        if not result["nama"]:
            log("  GAGAL: nama produk tidak terbaca, tidak disimpan.")
        else:
            save_images(result, urls)
            write_json(p["items"] / f"{card['itemid']}.json", result)
            log(f"  OK ({result['sumber']}): gramasi {result['gramasi'] or '-'}, ukuran {result['ukuran'] or '-'}, {len(result['gambar'])} gambar")
        if position < len(todo):
            pause("sebelum produk berikutnya")


def write_outputs() -> int:
    p = paths()
    rows = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(p["items"].glob("*.json"))]
    write_json(p["json"], rows)
    with p["csv"].open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["nama", "gramasi", "ukuran", "variasi_lain", "jumlah_gambar", "folder_gambar", "url"])
        for r in rows:
            others = "; ".join(f"{k}: {', '.join(v)}" for k, v in r["variasi_lain"].items())
            folder = str(os.path.dirname(r["gambar"][0])) if r["gambar"] else ""
            writer.writerow([r["nama"], r["gramasi"] or "", ", ".join(r["ukuran"]), others, len(r["gambar"]), folder, r["url"]])
    return len(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Ambil gambar, nama, gramasi, dan ukuran produk dengan Selenium")
    parser.add_argument("--max-list-pages", type=int, help="uji: batasi jumlah halaman toko")
    parser.add_argument("--limit", type=int, help="uji: batasi jumlah produk")
    args = parser.parse_args(argv)
    code = 0
    try:
        with Browser() as browser:
            cards = collect_cards(browser, args.max_list_pages)
            collect_details(browser, cards, args.limit)
            log(f"Halaman dibuka di sesi ini: {browser.pages}")
    except Stop as e:
        log(f"BERHENTI: {e}")
        code = 2
    except WebDriverException as e:
        log(f"BERHENTI: browser ditutup atau gagal dibuka. Progres tersimpan. Rincian: {str(e).splitlines()[0]}")
        code = 5
    if paths()["items"].exists():
        log(f"{write_outputs()} produk tersimpan di {paths()['base']}")
    return code


if __name__ == "__main__":
    sys.exit(main())
