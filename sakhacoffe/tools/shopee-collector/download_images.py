"""Tahap 3: unduh gambar dari URL pada data detail. File yang sudah ada dilewati."""
from __future__ import annotations

import json
import os
import random
import time
import urllib.request

import config
from common import load_state, log, save_state

USER_AGENT = "SakhaCollector/1.0"


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        kind = response.headers.get("Content-Type", "")
        if not kind.startswith("image/"):
            raise ValueError(f"bukan gambar: {kind or 'tanpa Content-Type'}")
        return response.read()


def run() -> dict:
    state = load_state()
    state["images"] = {"failed": {}}
    files = sorted(config.ITEMS.glob("*.json"))
    fetched = skipped = 0
    for position, path in enumerate(files, 1):
        detail = json.loads(path.read_text(encoding="utf-8"))
        itemid = detail["itemid"]
        folder = config.IMAGES / str(itemid)
        folder.mkdir(parents=True, exist_ok=True)
        log(f"Gambar item ke-{position} dari {len(files)}: {itemid} ({len(detail['images'])} gambar)")
        for n, url in enumerate(detail["images"], 1):
            target = folder / f"{n}.jpg"
            if target.exists() and target.stat().st_size > 0:
                skipped += 1
                continue
            try:
                body = fetch(url)
            except Exception as e:
                state["images"]["failed"].setdefault(str(itemid), []).append(f"{n}: {e}")
                log(f"  GAGAL gambar {n}: {e}")
            else:
                tmp = target.with_suffix(".part")
                tmp.write_bytes(body)
                os.replace(tmp, target)
                fetched += 1
            time.sleep(random.uniform(*config.IMAGE_DELAY_RANGE))
        save_state(state)
    log(f"Gambar selesai: {fetched} diunduh, {skipped} dilewati, {len(state['images']['failed'])} item ada yang gagal.")
    return state
