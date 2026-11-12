"""Buka browser dengan profil persisten agar Anda login sendiri. Alat ini tidak mengisi atau membaca sandi."""
from __future__ import annotations

import config
from common import log


def run() -> None:
    from playwright.sync_api import sync_playwright

    config.PROFILE.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(config.PROFILE), headless=False, viewport=None, locale="id-ID")
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(config.SHOP_URL, wait_until="domcontentloaded", timeout=60000)
        log("Login ke Shopee di jendela browser, lalu TUTUP jendelanya. Sesi tersimpan di data/shopee/profile/.")
        while context.pages:
            try:
                context.pages[0].wait_for_timeout(1000)
            except Exception:
                break
        try:
            context.close()
        except Exception:
            pass
    log("Jendela ditutup.")
