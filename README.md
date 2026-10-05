# WebPing Bot 🤖👀

Bot Telegram pemantau perubahan website — seperti Visualping, tapi versi teks.
Kirim link website apa pun, pilih interval pengecekan, dan dapatkan notifikasi
saat isi halamannya berubah, lengkap dengan cuplikan baris yang berubah.

## ✨ Fitur

- **Tambah pantauan via chat** — kirim link langsung, atau `/add` lalu kirim linknya
- **Pilih interval** lewat tombol: 15 menit / 1 jam / 6 jam / 24 jam
- **Notifikasi perubahan** — berisi cuplikan diff `(+N -M baris berubah)`
- **Cek manual** kapan saja (`/check <nomor>`)
- **Peringatan situs down** — notifikasi sekali jika situs tak bisa diakses 3x beruntun
- **Privat** — hanya pemilik (chat pertama yang `/start`) yang bisa memakai bot
- **Anti double-instance** — file-lock (`fcntl`) di bot + `flock` di watchdog,
  sehingga tidak pernah ada dua proses berebut pesan Telegram

## 🚀 Cara pakai

1. Chat ke bot di Telegram, kirim `/start`
2. Kirim link website yang mau dipantau, mis. `https://contoh.com/promo`
3. Pilih interval pengecekan lewat tombol yang muncul
4. Selesai — bot menyimpan baseline isi halaman dan mengecek berkala

Perintah yang tersedia:

| Perintah | Fungsi |
|---|---|
| `/start` | Mulai / sambutan |
| `/add` | Tambah website (lalu kirim linknya) |
| `/list` | Daftar website yang dipantau + statusnya |
| `/check <nomor>` | Cek satu website sekarang juga |
| `/remove <nomor>` | Berhenti memantau satu website |
| `/help` | Bantuan |

Contoh notifikasi perubahan:

```
🔔 Perubahan terdeteksi: Promo Tokoku
https://tokoku.com/promo

(+2 -1 baris berubah)

-Diskon 10% semua produk
+Diskon 25% semua produk
+Promo berakhir Minggu!
```

## 🛠️ Instalasi & menjalankan sendiri

Kebutuhan: Python 3.10+.

```bash
git clone https://github.com/ncuyyGans/bot-telegram-pantau-website.git
cd bot-telegram-pantau-website
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 1. Buat bot Telegram & siapkan token

- Chat ke [@BotFather](https://t.me/BotFather) → `/newbot` → ikuti langkahnya → dapat **token**.
- Token dibaca dengan urutan prioritas:
  1. Environment variable `WEBPING_BOT_TOKEN`
  2. (khusus lingkungan Muse) kredensial vault `custom.telegram-webping`

```bash
export WEBPING_BOT_TOKEN="123456789:AAH..."
```

> Token **tidak pernah** disimpan di file atau kode — hanya via env var / vault.

### 2. Jalankan bot

```bash
./.venv/bin/python bot.py
```

Bot memakai long-polling Telegram. Untuk jalan terus di server, daftarkan
`watchdog.sh` ke cron tiap 5 menit:

```bash
*/5 * * * * /path/ke/bot-telegram-pantau-website/watchdog.sh
```

`watchdog.sh` menyalakan ulang bot jika mati. `flock` di watchdog +
`fcntl` lock di `bot.py` menjamin hanya satu instance yang berjalan.

### 3. Verifikasi

```bash
./.venv/bin/python -c "import tg; print(tg.get_me()['result']['username'])"
```

Harus mencetak username botmu.

## 🧠 Cara kerja

```
bot.py        long-polling Telegram + menangani perintah & tombol
              tiap putaran juga mengecek monitor yang jatuh tempo
fetcher.py    unduh halaman (requests + User-Agent browser),
              buang <script>/<style>/<noscript>, normalisasi teks
tg.py         wrapper Bot API (urllib stdlib, tanpa dependensi)
watchdog.sh   penjaga proses untuk cron
state.json    data monitor: url, interval, hash baseline, status
              (dibuat otomatis, TIDAK di-commit)
```

Deteksi perubahan: teks halaman dinormalisasi (spasi digabung, baris kosong
dibuang) lalu di-hash SHA-256 dan dibandingkan dengan baseline. Jika beda,
`difflib` membuat cuplikan baris `+`/`-` untuk notifikasi.

## 🔒 Keamanan

- Token bot hanya lewat env var / secure vault — tidak ada secret di repo ini.
- `state.json` berisi chat ID pemilik → di-`gitignore`, jangan di-commit.
- Bot menolak chat selain pemiliknya.

## ⚠️ Batasan

- Hanya memantau **teks** halaman HTML (bukan screenshot seperti Visualping).
- Halaman yang butuh login, JavaScript berat (SPA), atau tantangan
  Cloudflare tidak bisa dipantau — kontennya tidak terbaca dari server.
- Halaman sangat dinamis (mis. tulisan "x menit lalu" yang selalu berubah)
  bisa memicu notifikasi sering — hapus saja monitornya jika berisik.
- PDF/gambar/dokumen non-HTML ditolak dengan pesan yang jelas.

## 📄 Lisensi

Bebas dipakai untuk keperluan pribadi.
