"""Membersihkan nama, memisahkan ukuran dan gilingan, dan membaca spesifikasi yang tertulis eksplisit."""
from __future__ import annotations

import re

from slugify import slugify

PROMO = re.compile(r"\bBY\s+SAKHA\s+COFFEE\s+ROASTERY\b|\bPROMO\b|\bORI\b", re.I)
SIZE = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*(kg|kilo|gr|gram|g|ml|l|liter|sachet|pcs)\b", re.I)
GRIND_TAIL = re.compile(
    r"[\s\-|/(\[]*\b(biji(?:\s+utuh)?|bubuk|whole\s*beans?|ground|gilingan\s+\w+)\b[\s)\]]*$", re.I
)
KEEP_UPPER = {"BPOM", "MUI", "V60"}


def _case(token: str) -> str:
    letters = re.sub(r"[^A-Za-z]", "", token)
    if len(letters) < 2 or not letters.isupper() or token in KEEP_UPPER or re.search(r"\d", token):
        return token
    return re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize(), token)


def clean_name(raw: str) -> str:
    """Hanya membuang kata promo dan kapital berlebih. Nama varian rasa tidak diubah."""
    name = re.sub(r"[\[(][^\])]*\b(promo|ori)\b[^\])]*[\])]", " ", raw or "", flags=re.I)
    name = PROMO.sub(" ", name)
    name = " ".join(_case(token) for token in name.split())
    name = re.sub(r"\s+([,.|])", r"\1", name)
    name = re.sub(r"\(\s*\)|\[\s*\]", " ", name)
    return re.sub(r"\s{2,}", " ", name).strip(" -|,/")


def format_size(number: str, unit: str) -> str:
    unit = unit.lower()
    unit = {"gram": "gr", "g": "gr", "kilo": "kg", "liter": "l"}.get(unit, unit)
    return f"{number.replace(',', '.')}{unit}"


def size_from_text(text: str):
    m = SIZE.search(text or "")
    return format_size(m.group(1), m.group(2)) if m else None


def base_name(name: str) -> str:
    """Nama produk tanpa ukuran dan gilingan di ekor, dipakai untuk menggabungkan listing."""
    out = SIZE.sub(" ", name)
    out = re.sub(r"\(\s*\)|\[\s*\]", " ", out)
    out = re.sub(r"\s{2,}", " ", out).strip(" -|,/")
    previous = None
    while previous != out:
        previous = out
        out = GRIND_TAIL.sub("", out).strip(" -|,/")
    return out or name


def group_key(name: str) -> str:
    return slugify(base_name(name))


SPEC_LABELS = {
    "origin": r"origin|asal(?:\s+kopi)?|region|daerah(?:\s+asal)?",
    "process": r"process(?:ing)?|proses(?:\s+pasca\s+panen)?|pengolahan",
    "roastLevel": r"roast(?:\s+level|\s+profile|ing)?|level\s+sangrai|tingkat\s+sangrai|sangrai",
    "tastingNotes": r"tasting\s+notes?|flavou?r\s+notes?|flavou?r|catatan\s+rasa|notes|cita\s+rasa",
}


def parse_specs(attributes: list, description) -> dict:
    """Hanya nilai yang berlabel. Tidak ada label berarti kosong, tanpa menebak dari nama produk."""
    specs = {"origin": None, "process": None, "roastLevel": None, "tastingNotes": []}
    sources = [(a.get("name") or "", a.get("value") or "") for a in attributes or []]
    for line in (description or "").splitlines():
        m = re.match(r"^[\s\-•*]*([^:：=]{2,40})\s*[:：=]\s*(.+?)\s*$", line)
        if m:
            sources.append((m.group(1), m.group(2)))
    for label, value in sources:
        for field, pattern in SPEC_LABELS.items():
            if not re.fullmatch(pattern, label.strip(), re.I):
                continue
            if field == "tastingNotes":
                if not specs[field]:
                    specs[field] = [x.strip() for x in re.split(r",|/|&|\bdan\b", value) if x.strip()]
            elif not specs[field]:
                specs[field] = value.strip()
    return specs


GRIND_TIER = re.compile(r"giling|grind|\btipe\b|\bjenis\b|\btype\b", re.I)
SIZE_TIER = re.compile(r"ukuran|size|berat|\bgram\b|\bisi\b|netto|weight", re.I)
GRIND_VALUE = re.compile(r"biji|bubuk|whole|ground|giling|kasar|sedang|halus|espresso|french|v60|tubruk|moka", re.I)


def classify_options(tiers: list, options: list) -> dict:
    """Pecah pilihan model menjadi ukuran, gilingan, dan pilihan lain (misalnya rasa)."""
    size = grind = None
    other = []
    for tier, value in zip(tiers, options):
        if not value:
            continue
        label = tier.get("name") or ""
        if SIZE_TIER.search(label) or (not GRIND_TIER.search(label) and SIZE.search(value)):
            size = size_from_text(value) or value
        elif GRIND_TIER.search(label) or GRIND_VALUE.search(value):
            grind = value
        else:
            other.append(f"{label}: {value}" if label else value)
    return {"size": size, "grind": grind, "other": other}


CATEGORY_NAMES = (
    (re.compile(r"specialty\s+coffee\s+drip|coffee\s+drip|drip\s+bag", re.I), "specialty-drip"),
    (re.compile(r"specialty\s+filter", re.I), "specialty-filter"),
    (re.compile(r"specialty\s+espresso", re.I), "specialty-espresso"),
    (re.compile(r"house\s+blend", re.I), "house-blend"),
)


def category_from_text(*texts) -> str | None:
    """Kategori hanya dari frasa yang tertulis persis di kategori toko atau nama listing."""
    for text in texts:
        for pattern, category in CATEGORY_NAMES:
            if text and pattern.search(text):
                return category
    return None
