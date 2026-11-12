"""Sesi browser, jeda, batas halaman, penanganan verifikasi, dan progres."""
from __future__ import annotations

import json
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import config


class Stop(Exception):
    """Berhenti dengan rapi. Progres sudah tersimpan."""


class Blocked(Stop):
    pass


class BudgetReached(Stop):
    pass


class FormatChanged(Stop):
    pass


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def write_json(path: Path, data) -> None:
    """Tulis atomik: file lama tetap utuh bila proses terhenti di tengah."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_state() -> dict:
    state = json.loads(config.STATE.read_text(encoding="utf-8")) if config.STATE.exists() else {}
    state.setdefault("list", {"next_page": 0, "finished": False, "shopid": None})
    state.setdefault("detail", {"done": [], "failed": {}})
    state.setdefault("images", {"failed": {}})
    return state


def save_state(state: dict) -> None:
    write_json(config.STATE, state)


def read_list() -> list:
    if not config.LIST_FILE.exists():
        return []
    with config.LIST_FILE.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def pause(label: str = "") -> None:
    wait = random.uniform(*config.DELAY_RANGE)
    log(f"Jeda berikutnya {wait:.1f} detik{' ' + label if label else ''}")
    time.sleep(wait)


BLOCK_URL = re.compile(r"/verify/|/buyer/login|/account/login|captcha|/traffic/error", re.I)
BLOCK_TEXT = ("Masuk Diperlukan", "Login Required")


class Session:
    """Satu jendela browser sungguhan dengan profil login persisten."""

    def __init__(self, resume_budget_used: int = 0):
        self.pages_used = resume_budget_used
        self.blocks = 0
        self.captured = []

    def __enter__(self) -> "Session":
        from playwright.sync_api import sync_playwright

        config.PROFILE.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self.context = self._pw.chromium.launch_persistent_context(
            str(config.PROFILE), headless=config.HEADLESS, viewport=None, locale="id-ID",
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.on("response", self._on_response)
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.context.close()
        except Exception:
            pass
        self._pw.stop()

    def _on_response(self, response) -> None:
        # Hanya mencatat respons yang dimuat halaman. Isinya dibaca kemudian oleh drain().
        if response.request.resource_type in ("xhr", "fetch"):
            self.captured.append(response)

    def drain(self, matcher: Callable[[str], bool]) -> list:
        """Ambil JSON dari respons yang sudah tertangkap dan cocok dengan matcher."""
        found, rest = [], []
        for response in self.captured:
            if not matcher(response.url):
                rest.append(response)
                continue
            if response.status in (403, 429):
                self._count_block(f"HTTP {response.status} dari {response.url.split('?')[0]}")
                continue
            try:
                found.append((response.url.split("?")[0], response.json()))
            except Exception:
                pass  # bukan JSON, atau badannya sudah tidak tersedia
        self.captured = rest
        return found

    def is_verification(self) -> bool:
        if BLOCK_URL.search(self.page.url):
            return True
        if self.page.locator('iframe[src*="captcha"]').count():
            return True
        try:
            text = self.page.locator("body").inner_text(timeout=3000)
        except Exception:
            return False
        return len(text) < 2000 and any(t in text for t in BLOCK_TEXT)

    def _count_block(self, reason: str) -> None:
        self.blocks += 1
        log(f"PERINGATAN: terblokir atau perlu verifikasi ({self.blocks}/{config.MAX_BLOCKS}): {reason}")
        if self.blocks >= config.MAX_BLOCKS:
            raise Blocked(f"Sudah {config.MAX_BLOCKS} kali terblokir dalam sesi ini. Berhenti total, progres tersimpan.")

    def _wait_for_human(self) -> None:
        self._count_block(self.page.url.split("?")[0])
        log(">>> Selesaikan verifikasi atau login di jendela browser. Alat ini menunggu dan tidak menyentuh apa pun.")
        deadline = time.time() + config.VERIFICATION_WAIT_SECONDS
        while self.is_verification():
            if time.time() > deadline:
                raise Blocked("Verifikasi tidak selesai dalam batas waktu. Progres tersimpan.")
            # wait_for_timeout, bukan time.sleep: page.url hanya diperbarui saat Playwright memproses event.
            self.page.wait_for_timeout(3000)
        log("Verifikasi selesai, melanjutkan.")

    def visit(self, url: str) -> None:
        for _ in range(config.MAX_BLOCKS):
            if self.pages_used >= config.MAX_PAGES_PER_SESSION:
                raise BudgetReached(f"Batas {config.MAX_PAGES_PER_SESSION} halaman per sesi tercapai. Lanjutkan dengan --resume.")
            self.captured = []
            response = self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            self.pages_used += 1
            self.page.wait_for_timeout(1500)
            if response is not None and response.status in (403, 429):
                self._count_block(f"HTTP {response.status} pada {url}")
                pause("sebelum mencoba lagi")
                continue
            if not self.is_verification():
                return
            self._wait_for_human()
            pause("setelah verifikasi")
        raise Blocked(f"Halaman tetap tidak bisa dibuka: {url}")

    def wait_for_json(self, matcher: Callable[[str], bool], seconds: float = 15) -> None:
        """Tunggu respons cocok, sambil menggulir halaman seperti biasa."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if any(matcher(r.url) for r in self.captured):
                self.page.wait_for_timeout(1000)
                return
            self.page.mouse.wheel(0, 600)
            self.page.wait_for_timeout(700)
