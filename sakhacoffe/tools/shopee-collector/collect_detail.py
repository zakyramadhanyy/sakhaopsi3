"""Tahap 2: detail tiap produk, disimpan per item, bisa dilanjutkan."""
from __future__ import annotations

import config
from common import FormatChanged, Session, load_state, log, pause, read_list, save_state, write_json
from parsers import FormatError, is_detail_endpoint, parse_detail_payloads, parse_dom_detail

DOM_JS = """() => {
  let product = null;
  for (const el of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const j = JSON.parse(el.textContent);
      for (const x of Array.isArray(j) ? j : [j]) if (x['@type'] === 'Product') product = x;
    } catch (e) {}
  }
  const og = document.querySelector('meta[property="og:image"]');
  const h1 = document.querySelector('h1');
  const offers = product && product.offers ? product.offers : {};
  const rating = product && product.aggregateRating ? product.aggregateRating : {};
  let images = product && product.image ? [].concat(product.image) : [];
  if (!images.length && og) images = [og.content];
  return {
    name: (h1 && h1.innerText.trim()) || (product && product.name) || null,
    description: (product && product.description) || null,
    images,
    price: Number(offers.lowPrice || offers.price) || null,
    rating: Number(rating.ratingValue) || null,
    rating_count: Number(rating.ratingCount) || null,
  };
}"""


def collect(session: Session, resume: bool = True, limit=None) -> dict:
    state = load_state()
    if not resume:
        state["detail"] = {"done": [], "failed": {}}
        save_state(state)
    items = read_list()
    if not items:
        raise FormatChanged("data/shopee/raw/list.jsonl kosong. Jalankan tahap list dulu.")
    done = set(state["detail"]["done"])
    todo = [i for i in items if i["itemid"] not in done]
    if limit is not None:
        todo = todo[:limit]
    log(f"Detail: {len(done)} sudah selesai, {len(todo)} akan dikunjungi, total {len(items)}")
    consecutive_errors = 0
    for position, item in enumerate(todo, 1):
        itemid = item["itemid"]
        log(f"Item ke-{len(done) + 1} dari {len(items)} (giliran ini {position}/{len(todo)}): {itemid}")
        session.visit(item["url"])
        session.wait_for_json(is_detail_endpoint)
        payloads = session.drain(is_detail_endpoint)
        try:
            json_error = None
            try:
                detail = parse_detail_payloads(payloads, itemid)
            except FormatError as e:
                json_error = e
                if payloads:
                    write_json(config.DEBUG / f"item-{itemid}.json", [{"endpoint": x, "payload": p} for x, p in payloads])
                log(f"  JSON tidak bisa dipakai ({e}). Mencoba cadangan DOM.")
                detail = parse_dom_detail(session.page.evaluate(DOM_JS), itemid, item["shopid"])
                log("  Memakai DOM: variasi dan stok tidak lengkap, ditandai partial.")
        except FormatError as e:
            reason = f"JSON: {json_error}; DOM: {e}"
            state["detail"]["failed"][str(itemid)] = reason
            save_state(state)
            log(f"  GAGAL: {reason}")
            consecutive_errors += 1
            if consecutive_errors >= config.MAX_CONSECUTIVE_FORMAT_ERRORS:
                raise FormatChanged(
                    f"{consecutive_errors} item berturut-turut gagal dibaca. Format Shopee kemungkinan berubah. "
                    f"Terakhir: {reason}. Contoh ada di {config.DEBUG}."
                )
        else:
            consecutive_errors = 0
            detail["url"] = item["url"]
            detail["list"] = {k: item.get(k) for k in ("price", "price_before_discount", "sold", "sold_text", "rating", "shop_category", "shop_category_ids")}
            write_json(config.ITEMS / f"{itemid}.json", detail)
            state["detail"]["done"].append(itemid)
            state["detail"]["failed"].pop(str(itemid), None)
            done.add(itemid)
            save_state(state)
            log(f"  OK ({detail['source']}): {len(detail['images'])} gambar, {len(detail['models'])} model")
        if position < len(todo):
            pause("sebelum produk berikutnya")
    return state
