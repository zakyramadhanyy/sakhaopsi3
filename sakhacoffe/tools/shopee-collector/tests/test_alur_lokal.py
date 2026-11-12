"""Uji seluruh alur di server tiruan lokal: tidak menyentuh Shopee maupun data asli."""
import io
import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

import build_products
import collect_detail
import collect_list
import config
import download_images
from common import Blocked, Session, load_state, read_list

SHOP = 9
# 101 dan 102 satu produk beda ukuran; 103 memicu verifikasi; 104 JSON rusak tanpa cadangan DOM.
LISTING = {
    101: ("PROMO KOPI GAYO 200GR ORI", 5000000000),
    102: ("Kopi Gayo 500gr", 11000000000),
    103: ("Sun of Peach 200gr", 7900000000),
    104: ("Item Rusak", 1000000000),
}
state = {"verify_left": 0, "verify_cleared": False}


def image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), (200, 120, 80)).save(buffer, "JPEG")
    return buffer.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, body, kind="text/html", status=200):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/sakha.coffee":
            page = query.get("page", ["0"])[0]
            self.send(f"<body><script>fetch('/api/v4/shop/rcmd_items?page={page}')</script></body>")
        elif url.path == "/api/v4/shop/rcmd_items":
            ids = sorted(LISTING) if query["page"][0] == "0" else []
            items = [{"item_basic": {"itemid": i, "shopid": SHOP, "name": LISTING[i][0], "price": LISTING[i][1], "image": f"c{i}"}} for i in ids]
            self.send(json.dumps({"items": items}), "application/json")
        elif url.path.startswith("/product/"):
            itemid = int(url.path.split("/")[-1])
            if itemid == 103 and state["verify_left"] > 0:
                self.send_response(302)
                self.send_header("Location", "/verify/captcha")
                self.end_headers()
                return
            self.send(f"<body><h1>{LISTING[itemid][0]}</h1><script>fetch('/api/v4/pdp/get_pc?item_id={itemid}')</script></body>")
        elif url.path == "/verify/captcha":
            # Tiruan orang yang menyelesaikan verifikasi: halaman pindah sendiri setelah sebentar.
            state["verify_left"] -= 1
            self.send("<body>Verifikasi<script>setTimeout(()=>{location.href='/selesai'},4000)</script></body>")
        elif url.path == "/selesai":
            self.send("<body>ok</body>")
        elif url.path == "/api/v4/pdp/get_pc":
            itemid = int(query["item_id"][0])
            if itemid == 104:
                self.send(json.dumps({"data": {"item": {"item_id": 104, "title": "Item Rusak", "images": ["x"], "models": [{"price": 1000000000}]}}}), "application/json")
                return
            models = [{"model_id": itemid * 10, "price": LISTING[itemid][1], "stock": 5, "extinfo": {"tier_index": [0]}}]
            node = {"item_id": itemid, "shop_id": SHOP, "title": LISTING[itemid][0], "description": "Asal: Gayo",
                    "images": [f"i{itemid}a", f"i{itemid}b"], "tier_variations": [{"name": "Gilingan", "options": ["Biji"]}], "models": models}
            self.send(json.dumps({"data": {"item": node, "product_review": {"rating_star": 4.5, "total_rating_count": 10, "historical_sold": itemid}}}), "application/json")
        elif url.path.startswith("/file/"):
            self.send(image_bytes(), "image/jpeg")
        else:
            self.send("not found", status=404)


class Alur(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.saved = (config.ROOT, config.SHOP_URL, config.IMAGE_CDN, config.DELAY_RANGE, config.IMAGE_DELAY_RANGE, config.HEADLESS)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        root, config.SHOP_URL, config.IMAGE_CDN, config.DELAY_RANGE, config.IMAGE_DELAY_RANGE, config.HEADLESS = cls.saved
        config.use_root(root)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        config.use_root(Path(self.tmp))
        config.SHOP_URL = f"{self.base}/sakha.coffee"
        config.IMAGE_CDN = f"{self.base}/file"
        config.DELAY_RANGE = (0, 0)
        config.IMAGE_DELAY_RANGE = (0, 0)
        config.HEADLESS = True
        state["verify_left"] = 0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _collect_list(self, session):
        collect_list.collect(session, resume=True)
        for item in read_list():
            item["url"] = f"{self.base}/product/{item['itemid']}"
        items = read_list()
        with config.LIST_FILE.open("w", encoding="utf-8") as f:
            for item in items:
                item["url"] = f"{self.base}/product/{item['itemid']}"
                f.write(json.dumps(item) + "\n")

    def test_alur_penuh(self):
        state["verify_left"] = 1
        with Session() as session:
            self._collect_list(session)
            self.assertEqual({i["itemid"] for i in read_list()}, set(LISTING))
            self.assertTrue(load_state()["list"]["finished"])
            endpoints = json.loads(config.ENDPOINTS.read_text())
            self.assertIn("/api/v4/shop/rcmd_items", "".join(endpoints))
            result = collect_detail.collect(session, resume=True)
            self.assertEqual(session.blocks, 1, "verifikasi harus tercatat dan ditunggu")

        self.assertCountEqual(result["detail"]["done"], [101, 102, 103])
        self.assertIn("104", result["detail"]["failed"])
        self.assertFalse((config.ITEMS / "104.json").exists(), "item rusak tidak boleh ditulis kosong")

        download_images.run()
        self.assertTrue((config.IMAGES / "101" / "2.jpg").exists())
        products = build_products.run()
        self.assertEqual(len(products), 2, "101 dan 102 harus jadi satu produk")
        gayo = next(p for p in products if p["slug"] == "kopi-gayo")
        self.assertEqual(sorted(v["size"] for v in gayo["variants"]), ["200gr", "500gr"])
        self.assertEqual(gayo["priceFrom"], 50000)
        self.assertEqual(gayo["specs"]["origin"], "Gayo")
        self.assertIsNone(gayo["specs"]["process"])
        self.assertEqual(gayo["images"][0], "/products/kopi-gayo/1.webp")
        for suffix, size in (("", 800), ("-400", 400)):
            with Image.open(config.PUBLIC_PRODUCTS / "kopi-gayo" / f"1{suffix}.webp") as image:
                self.assertEqual(image.size, (size, size))
        report = config.REPORT.read_text(encoding="utf-8")
        self.assertIn("Listing di daftar: 4", report)
        self.assertIn("Produk setelah digabung: 2", report)
        self.assertIn("models[].stock", report)
        self.assertTrue(config.PRODUCTS_CSV.exists())

        first = config.PRODUCTS_JSON.read_text(encoding="utf-8")
        build_products.run()
        self.assertEqual(config.PRODUCTS_JSON.read_text(encoding="utf-8"), first, "build harus idempotent")

    def test_tiga_blokir_berhenti_total_lalu_resume(self):
        with Session() as session:
            self._collect_list(session)
        state["verify_left"] = 99
        with self.assertRaises(Blocked):
            with Session() as session:
                session.blocks = 2  # dua blokir sebelumnya
                collect_detail.collect(session, resume=True)
        saved = load_state()
        self.assertCountEqual(saved["detail"]["done"], [101, 102], "progres sebelum blokir harus tersimpan")

        state["verify_left"] = 0
        visited = []
        with Session() as session:
            original = session.visit
            session.visit = lambda url: (visited.append(url), original(url))[1]
            collect_detail.collect(session, resume=True)
        self.assertEqual([u.rsplit("/", 1)[1] for u in visited], ["103", "104"], "resume tidak boleh mengulang item selesai")

    def test_batas_halaman(self):
        from common import BudgetReached
        config_max = config.MAX_PAGES_PER_SESSION
        config.MAX_PAGES_PER_SESSION = 1
        try:
            with self.assertRaises(BudgetReached):
                with Session() as session:
                    collect_list.collect(session, resume=True)
        finally:
            config.MAX_PAGES_PER_SESSION = config_max
        self.assertEqual(load_state()["list"]["next_page"], 1)


if __name__ == "__main__":
    unittest.main()
