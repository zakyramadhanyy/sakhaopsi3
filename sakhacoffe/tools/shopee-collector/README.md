# Pengumpul data Shopee Sakha Coffee

Mengumpulkan data toko dari browser sungguhan dengan sesi login Anda sendiri. Alat ini hanya membaca respons JSON yang dimuat halaman secara normal, dengan DOM sebagai cadangan.

Alat ini tidak membuat request API sendiri, tidak menyalin token, tidak memutar proxy atau user-agent, dan tidak mencoba melewati CAPTCHA.

## Batas kerja

- Jeda acak 6–12 detik antar halaman, maksimal 300 halaman per sesi. Keduanya tidak punya opsi CLI.
- Halaman verifikasi, CAPTCHA, atau login: alat berhenti dan menunggu Anda menyelesaikannya di jendela browser.
- Terblokir 3 kali dalam satu sesi: berhenti total, progres tersimpan.
- Toko sekitar 392 listing butuh lebih dari satu sesi. Jalankan ulang dengan `--resume`, sebaiknya tidak langsung.

Pengumpulan otomatis tetap bisa melanggar ketentuan Shopee dan memicu pembatasan akun. Jangan login dengan akun penjual utama. Bila ekspor Seller Centre sudah tersedia, gunakan ekspor itu.

## Instalasi

Mac ini memakai Python 3.9.6. Kode berjalan di 3.9 maupun 3.11.

```bash
cd tools/shopee-collector
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Login sekali

```bash
python run.py --stage login
```

Login di jendela yang terbuka, lalu tutup jendelanya. Sesi tersimpan di `data/shopee/profile/`.

## Menjalankan

Uji dulu, lalu periksa hasilnya sebelum menjalankan penuh:

```bash
python run.py --stage list --max-pages 1      # lihat data/shopee/raw/list.jsonl dan endpoints.json
python run.py --stage detail --limit 3        # lihat data/shopee/raw/items/
```

Tanpa `--resume`, tahap list dan detail mengulang dari awal.

Jalankan penuh:

```bash
python run.py --stage all --resume
```

Tahap terpisah: `list`, `detail`, `images`, `build`. Untuk mencocokkan 5 produk acak: `python run.py --stage verify`.

## Hasil

| File | Isi |
|---|---|
| `data/shopee/raw/list.jsonl` | Daftar produk, satu baris per item |
| `data/shopee/raw/items/{itemid}.json` | Detail per item |
| `data/shopee/state.json` | Progres untuk `--resume` |
| `data/shopee/images/{itemid}/{n}.jpg` | Gambar asli |
| `data/shopee/products.csv` | Satu baris per varian, untuk dicek tim |
| `data/shopee/report.md` | Laporan: listing → produk, gagal, kosong, nama berubah |
| `data/shopee/verify.md` | Hasil pencocokan 5 produk |
| `src/data/products.json` | Data produk akhir |
| `public/products/{slug}/` | `{n}.webp` 800×800 dan `{n}-400.webp` 400×400 |

Kategori yang tidak tertulis di sumber dibiarkan kosong. Untuk mengisinya manual, buat `data/shopee/category-overrides.csv` dengan kolom `itemid,category`, lalu jalankan `--stage build`.

## Bila format Shopee berubah

Parser berhenti dengan nama field yang hilang dan menyimpan contoh JSON di `data/shopee/raw/debug/`. Tidak ada data kosong yang ditulis diam-diam. Sesuaikan `parsers.py`, lalu jalankan ulang dengan `--resume`.

## Uji

Memakai server tiruan lokal, tanpa menyentuh Shopee dan data asli:

```bash
python -m unittest discover -s tests -v
```

## Selenium: gambar, nama, gramasi, ukuran

Skrip terpisah, memakai Google Chrome yang terpasang. Bila perlu login, skrip membuka halaman login dan menunggu Anda login manual. Jeda, batas halaman, dan batas blokirnya sama dengan alat di atas.

```bash
python selenium_scrape.py --limit 3 --max-list-pages 1   # uji dulu
python selenium_scrape.py                                # penuh, melanjutkan yang belum selesai
```

Hasil ada di `data/shopee/selenium/`: `products.csv`, `products.json`, dan `images/{nama}-{itemid}/{n}.jpg`. `gramasi` dibaca dari nama produk (atau dari satu-satunya pilihan ukuran), dan `ukuran` dari pilihan variasi. Yang tidak tertulis dibiarkan kosong.
