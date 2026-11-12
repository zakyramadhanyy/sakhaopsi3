"""Lokasi file dan batas kerja pengumpul. Jeda dan batas halaman sengaja tidak punya opsi CLI."""
from __future__ import annotations

from pathlib import Path

SHOP_URL = "https://shopee.co.id/sakha.coffee"
IMAGE_CDN = "https://down-id.img.susercontent.com/file"

DELAY_RANGE = (6.0, 12.0)            # detik, antar halaman
IMAGE_DELAY_RANGE = (0.5, 1.0)       # detik, antar gambar
MAX_PAGES_PER_SESSION = 300
MAX_BLOCKS = 3                       # verifikasi ke-3 dalam satu sesi: berhenti total
VERIFICATION_WAIT_SECONDS = 1800     # batas menunggu Anda menyelesaikan verifikasi
MAX_CONSECUTIVE_FORMAT_ERRORS = 5    # format JSON kemungkinan berubah
HEADLESS = False                     # hanya uji lokal yang mengubah ini


def use_root(root: Path) -> None:
    """Tetapkan akar proyek. Uji memakai folder sementara agar data asli tidak tersentuh."""
    global ROOT, DATA, RAW, ITEMS, IMAGES, PROFILE, STATE, LIST_FILE, ENDPOINTS, DEBUG
    global PRODUCTS_CSV, REPORT, VERIFY_DIR, VERIFY_REPORT, CATEGORY_OVERRIDES, PRODUCTS_JSON, PUBLIC_PRODUCTS
    ROOT = Path(root)
    DATA = ROOT / "data" / "shopee"
    RAW = DATA / "raw"
    ITEMS = RAW / "items"
    IMAGES = DATA / "images"
    PROFILE = DATA / "profile"
    STATE = DATA / "state.json"
    LIST_FILE = RAW / "list.jsonl"
    ENDPOINTS = RAW / "endpoints.json"
    DEBUG = RAW / "debug"
    PRODUCTS_CSV = DATA / "products.csv"
    REPORT = DATA / "report.md"
    VERIFY_DIR = DATA / "verify"
    VERIFY_REPORT = DATA / "verify.md"
    CATEGORY_OVERRIDES = DATA / "category-overrides.csv"
    PRODUCTS_JSON = ROOT / "src" / "data" / "products.json"
    PUBLIC_PRODUCTS = ROOT / "public" / "products"


use_root(Path(__file__).resolve().parents[2])
