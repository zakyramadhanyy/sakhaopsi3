"""Uji skrip Selenium di server tiruan lokal: tidak menyentuh Shopee maupun data asli."""
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

import config
import selenium_scrape as ss

CARDS = {"page0": [101, 102, 103], "page1": []}
state = {"wall": True, "dom_only": False}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, body, kind="text/html"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/sakha.coffee":
            if state["wall"]:
                # Seperti Shopee: halaman termuat dulu, lalu dialihkan beberapa detik kemudian.
                self.send("<body>memuat<script>setTimeout(()=>{location.href='/verify/traffic/error'},1500)</script></body>")
                return
            page = query.get("page", ["0"])[0]
            links = "".join(f"<a href='/kopi-{i}-i.9.{i}?sp=1'><img alt='KOPI {i} 200GR' src='/file/c{i}_tn'>Kopi {i}</a>" for i in CARDS.get(f"page{page}", []))
            self.send(f"<body>{links}</body>")
        elif url.path == "/verify/traffic/error":
            self.send("<body>Masuk Diperlukan</body>")
        elif url.path == "/login":
            # Tiruan orang yang login manual: pindah sendiri setelah sebentar.
            state["wall"] = False
            self.send("<body>Login<script>setTimeout(()=>{location.href='/selesai'},3500)</script></body>")
        elif url.path == "/selesai":
            self.send("<body>beranda toko ok</body>")
        elif "-i.9." in url.path:
            itemid = int(url.path.rsplit(".", 1)[1])
            if state["dom_only"]:
                self.send(f"""<head><meta property='og:image' content='/file/o{itemid}'></head><body><h1>Kopi {itemid} 500gr</h1>
                <section><label>Ukuran</label><button>250gr</button><button>1kg</button></section></body>""".replace("'/file", f"'http://{self.headers['Host']}/file"))
            else:
                self.send(f"<body><h1>Kopi {itemid}</h1><script>fetch('/api/v4/pdp/get_pc?item_id={itemid}')</script></body>")
        elif url.path == "/api/v4/pdp/get_pc":
            itemid = int(query["item_id"][0])
            node = {"item_id": itemid, "title": f"PROMO KOPI {itemid} 200GR", "images": [f"http://{self.headers['Host']}/file/a{itemid}", f"http://{self.headers['Host']}/file/b{itemid}"],
                    "tier_variations": [{"name": "Ukuran", "options": ["200gr", "500gr"]}, {"name": "Gilingan", "options": ["Biji", "Halus"]}]}
            self.send(json.dumps({"data": {"item": node}}), "application/json")
        elif url.path.startswith("/file/"):
            buffer = io.BytesIO()
            Image.new("RGB", (64, 64), (200, 120, 80)).save(buffer, "JPEG")
            self.send(buffer.getvalue(), "image/jpeg")
        else:
            self.send("<body>tidak ada</body>")


class AlurSelenium(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.saved = (config.ROOT, config.DELAY_RANGE, config.IMAGE_DELAY_RANGE, ss.TARGET_URL, ss.LOGIN_URL, ss.HEADLESS)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        root, config.DELAY_RANGE, config.IMAGE_DELAY_RANGE, ss.TARGET_URL, ss.LOGIN_URL, ss.HEADLESS = cls.saved
        config.use_root(root)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        config.use_root(Path(self.tmp))
        config.DELAY_RANGE = (0, 0)
        config.IMAGE_DELAY_RANGE = (0, 0)
        ss.TARGET_URL = f"{self.base}/sakha.coffee?x=1"
        ss.LOGIN_URL = f"{self.base}/login"
        ss.HEADLESS = True
        state.update(wall=True, dom_only=False)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_login_manual_lalu_ambil_data_dan_resume(self):
        self.assertEqual(ss.main(["--limit", "2"]), 0)
        p = ss.paths()
        cards = json.loads(p["cards"].read_text())
        self.assertEqual(len(cards["cards"]), 3)
        self.assertTrue(cards["finished"])
        rows = json.loads(p["json"].read_text())
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["nama"], "Kopi 101 200GR")
        self.assertEqual(first["gramasi"], "200gr")
        self.assertEqual(first["ukuran"], ["200gr", "500gr"])
        self.assertEqual(first["variasi_lain"], {"Gilingan": ["Biji", "Halus"]})
        self.assertEqual(first["sumber"], "json")
        self.assertEqual(len(first["gambar"]), 2)
        self.assertTrue((config.ROOT / first["gambar"][0]).exists())
        self.assertIn("Kopi 101 200GR", p["csv"].read_text(encoding="utf-8-sig"))
        # Jalan lagi: hanya produk yang belum ada yang dikunjungi.
        self.assertEqual(ss.main([]), 0)
        self.assertEqual(len(json.loads(p["json"].read_text())), 3)

    def test_cadangan_dom(self):
        state.update(wall=False, dom_only=True)
        self.assertEqual(ss.main(["--limit", "1", "--max-list-pages", "1"]), 0)
        row = json.loads(ss.paths()["json"].read_text())[0]
        self.assertEqual(row["sumber"], "dom")
        self.assertEqual(row["gramasi"], "500gr")
        self.assertEqual(row["ukuran"], ["250gr", "1kg"])
        self.assertEqual(len(row["gambar"]), 1)

    def test_tanpa_data_gramasi_dibiarkan_kosong(self):
        hasil = ss.summarize({"itemid": "1", "url": "u", "name": "Kopi Gayo"}, {"name": "Kopi Gayo", "tiers": []})
        self.assertIsNone(hasil["gramasi"])
        self.assertEqual(hasil["ukuran"], [])


if __name__ == "__main__":
    unittest.main()
