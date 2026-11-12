#!/usr/bin/env python3
"""python run.py --stage all|list|detail|images|build|login|verify [--resume] [--max-pages N] [--limit N]"""
from __future__ import annotations

import argparse
import sys

from common import BudgetReached, Blocked, FormatChanged, Session, load_state, log

STAGES = ("all", "list", "detail", "images", "build", "login", "verify")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pengumpul data toko Shopee Sakha Coffee")
    parser.add_argument("--stage", choices=STAGES, default="all")
    parser.add_argument("--resume", action="store_true", help="lanjutkan tanpa mengulang yang sudah selesai")
    parser.add_argument("--max-pages", type=int, help="uji: batasi jumlah halaman toko")
    parser.add_argument("--limit", type=int, help="uji: batasi jumlah produk")
    args = parser.parse_args(argv)
    try:
        if args.stage == "login":
            import login
            login.run()
            return 0
        if args.stage in ("all", "list", "detail"):
            import collect_detail
            import collect_list
            with Session() as session:
                if args.stage in ("all", "list"):
                    collect_list.collect(session, resume=args.resume, max_pages=args.max_pages)
                if args.stage == "all" and not load_state()["list"]["finished"]:
                    log("Daftar produk belum selesai, tahap berikutnya tidak dijalankan.")
                    return 3
                if args.stage in ("all", "detail"):
                    state = collect_detail.collect(session, resume=args.resume or args.stage == "all", limit=args.limit)
                    log(f"Detail: {len(state['detail']['done'])} selesai, {len(state['detail']['failed'])} gagal.")
                log(f"Halaman dibuka di sesi ini: {session.pages_used}")
        if args.stage in ("all", "images"):
            import download_images
            download_images.run()
        if args.stage in ("all", "build"):
            import build_products
            build_products.run()
        if args.stage == "verify":
            import verify_products
            with Session() as session:
                verify_products.run(session)
        return 0
    except BudgetReached as e:
        log(f"BERHENTI: {e}")
        return 3
    except Blocked as e:
        log(f"BERHENTI TOTAL: {e}")
        return 2
    except FormatChanged as e:
        log(f"BERHENTI, FORMAT BERUBAH: {e}")
        return 4
    except Exception as e:
        if type(e).__name__ in ("TargetClosedError", "Error", "TimeoutError") and type(e).__module__.startswith("playwright"):
            log(f"BERHENTI: browser ditutup atau halaman gagal dimuat. Progres tersimpan, lanjutkan dengan --resume. Rincian: {str(e).splitlines()[0]}")
            return 5
        raise


if __name__ == "__main__":
    sys.exit(main())
