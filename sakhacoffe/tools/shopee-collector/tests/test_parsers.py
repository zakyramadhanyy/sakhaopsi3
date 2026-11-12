"""Uji fungsi murni. Bentuk JSON di sini tiruan, belum tentu sama dengan respons Shopee saat ini."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from normalize import base_name, category_from_text, classify_options, clean_name, group_key, parse_specs, size_from_text
from parsers import FormatError, is_detail_endpoint, is_list_endpoint, money, parse_count, parse_detail_payloads, parse_list_payload


class Harga(unittest.TestCase):
    def test_money(self):
        self.assertEqual(money(9200000000), 92000)
        self.assertIsNone(money(-1))
        self.assertIsNone(money(None))

    def test_parse_count(self):
        self.assertEqual(parse_count("1,2RB+ terjual"), 1200)
        self.assertEqual(parse_count("10RB+"), 10000)
        self.assertEqual(parse_count("87 terjual"), 87)
        self.assertEqual(parse_count(45), 45)
        self.assertIsNone(parse_count("habis"))


class Daftar(unittest.TestCase):
    def test_format_lama_dan_baru(self):
        payload = {"items": [
            {"item_basic": {"itemid": 1, "shopid": 9, "name": "A", "price": 5000000000, "price_before_discount": 6000000000,
                            "historical_sold": 12, "image": "abc", "item_rating": {"rating_star": 4.9}}},
            {"item_data": {"itemid": 2, "shopid": 9, "item_card_displayed_asset": {"name": "B", "image": "def"},
                           "item_card_display_price": {"price": 7000000000},
                           "item_card_display_sold_count": {"historical_sold_count_text": "1RB+"}}},
        ]}
        items = {i["itemid"]: i for i in parse_list_payload(payload)}
        self.assertEqual(items[1]["price"], 50000)
        self.assertEqual(items[1]["price_before_discount"], 60000)
        self.assertEqual(items[1]["rating"], 4.9)
        self.assertEqual(items[2]["name"], "B")
        self.assertEqual(items[2]["sold"], 1000)

    def test_harga_hilang_gagal_jelas(self):
        with self.assertRaises(FormatError) as ctx:
            parse_list_payload({"items": [{"itemid": 1, "shopid": 9, "name": "A"}]})
        self.assertIn("price", ctx.exception.missing)

    def test_data_toko_bukan_item(self):
        self.assertEqual(parse_list_payload({"data": {"shopid": 9, "itemid": 1}}), [])

    def test_matcher(self):
        self.assertTrue(is_list_endpoint("https://shopee.co.id/api/v4/shop/rcmd_items?x=1"))
        self.assertFalse(is_list_endpoint("https://shopee.co.id/sakha.coffee/shop.js"))
        self.assertTrue(is_detail_endpoint("https://shopee.co.id/api/v4/pdp/get_pc?item_id=1"))
        self.assertTrue(is_detail_endpoint("https://shopee.co.id/api/v4/item/get?itemid=1"))


class Detail(unittest.TestCase):
    PAYLOAD = {"data": {
        "item": {
            "item_id": 7, "shop_id": 9, "title": "Kopi X", "description": "Asal: Gayo",
            "images": ["h1", "h2"],
            "tier_variations": [{"name": "Ukuran", "options": ["200gr", "500gr"]}, {"name": "Gilingan", "options": ["Biji", "Halus"]}],
            "models": [
                {"model_id": 1, "price": 5000000000, "price_before_discount": 6000000000, "stock": 3, "extinfo": {"tier_index": [0, 1]}},
                {"model_id": 2, "price": 9000000000, "stock": 0, "extinfo": {"tier_index": [1, 0]}},
            ],
            "attributes": [{"name": "Merek", "value": "Sakha"}],
        },
        "product_review": {"rating_star": 4.8, "total_rating_count": 20, "historical_sold": 100},
    }}

    def test_variasi_dan_harga(self):
        d = parse_detail_payloads([("/api/v4/pdp/get_pc", self.PAYLOAD)], 7)
        self.assertEqual(d["models"][0]["options"], ["200gr", "Halus"])
        self.assertEqual(d["models"][0]["price"], 50000)
        self.assertEqual(d["models"][0]["price_before_discount"], 60000)
        self.assertEqual(d["models"][1]["stock"], 0)
        self.assertEqual(d["rating"], 4.8)
        self.assertEqual(d["rating_count"], 20)
        self.assertEqual(d["sold"], 100)
        self.assertTrue(d["images"][0].endswith("/h1"))

    def test_stok_hilang_gagal_jelas(self):
        import copy
        broken = copy.deepcopy(self.PAYLOAD)
        del broken["data"]["item"]["models"][0]["stock"]
        with self.assertRaises(FormatError) as ctx:
            parse_detail_payloads([("e", broken)], 7)
        self.assertEqual(ctx.exception.missing, ["models[].stock"])

    def test_node_tidak_ada(self):
        with self.assertRaises(FormatError):
            parse_detail_payloads([("e", {"error": 90309999})], 7)

    def test_opsional_kosong_tercatat(self):
        import copy
        p = copy.deepcopy(self.PAYLOAD)
        del p["data"]["product_review"]
        d = parse_detail_payloads([("e", p)], 7)
        self.assertIsNone(d["rating"])
        self.assertIn("rating", d["absent"])


class Nama(unittest.TestCase):
    def test_bersihkan(self):
        self.assertEqual(clean_name("PROMO KOPI MILKINHO DA CERRA 200GR ORI BY SAKHA COFFEE ROASTERY"), "Kopi Milkinho Da Cerra 200GR")
        self.assertEqual(clean_name("Moonlime Tenjifer"), "Moonlime Tenjifer")
        self.assertEqual(clean_name("Bleubailess Chocotart [PROMO ORI]"), "Bleubailess Chocotart")

    def test_ori_dalam_kata_aman(self):
        self.assertEqual(clean_name("Kopi Original Blend"), "Kopi Original Blend")

    def test_gabung_ukuran(self):
        self.assertEqual(group_key("Kopi Gayo 200gr"), group_key("Kopi Gayo 1 kg"))
        self.assertEqual(group_key("Kopi Gayo 200gr Biji"), group_key("Kopi Gayo 500gr"))
        self.assertNotEqual(group_key("Sun of Peach 200gr"), group_key("Sweet Bouquet 200gr"))
        self.assertEqual(base_name("Kopi Gayo 200gr"), "Kopi Gayo")

    def test_ukuran(self):
        self.assertEqual(size_from_text("Kopi 200 gram"), "200gr")
        self.assertEqual(size_from_text("Kopi 1Kg"), "1kg")
        self.assertIsNone(size_from_text("75% Arabica"))


class Spesifikasi(unittest.TestCase):
    def test_hanya_berlabel(self):
        s = parse_specs([], "Asal: Gayo\nProcess: Natural\nRoast Level: Medium\nTasting Notes: Peach, Melati & Madu\nKopi ini enak dari Bali")
        self.assertEqual(s["origin"], "Gayo")
        self.assertEqual(s["process"], "Natural")
        self.assertEqual(s["roastLevel"], "Medium")
        self.assertEqual(s["tastingNotes"], ["Peach", "Melati", "Madu"])

    def test_tanpa_label_kosong(self):
        s = parse_specs([], "Kopi natural dari Gayo dengan rasa peach")
        self.assertEqual(s, {"origin": None, "process": None, "roastLevel": None, "tastingNotes": []})

    def test_atribut_didahulukan(self):
        self.assertEqual(parse_specs([{"name": "Asal", "value": "Toraja"}], "Asal: Gayo")["origin"], "Toraja")


class Varian(unittest.TestCase):
    def test_klasifikasi(self):
        tiers = [{"name": "Ukuran"}, {"name": "Gilingan"}]
        self.assertEqual(classify_options(tiers, ["200gr", "Halus"]), {"size": "200gr", "grind": "Halus", "other": []})

    def test_rasa_tidak_hilang(self):
        self.assertEqual(classify_options([{"name": "Rasa"}], ["Peach"])["other"], ["Rasa: Peach"])

    def test_kategori_tidak_menebak(self):
        self.assertEqual(category_from_text("Specialty Filter"), "specialty-filter")
        self.assertEqual(category_from_text(None, "Paket Drip Bag Sakha"), "specialty-drip")
        self.assertIsNone(category_from_text("Kopi Gayo 200gr"))


if __name__ == "__main__":
    unittest.main()
