"""Pengambil teks halaman web untuk WebPing Bot.

fetch_page_text(url) -> (ok: bool, title: str|None, text_or_error: str)

- Hanya memproses konten HTML/teks; PDF/gambar/dll ditolak dengan pesan jelas.
- <script>/<style>/<noscript> dibuang; teks dinormalisasi
  (spasi digabung, baris kosong dibuang).
"""
import re

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)

# Tag yang tidak membawa konten bacaan dan hanya menambah noise diff.
JUNK_TAGS = ("script", "style", "noscript", "svg", "canvas", "template")


def normalize_text(raw: str) -> str:
    """Gabung spasi berlebih, buang baris kosong."""
    lines = []
    for ln in raw.splitlines():
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln:
            lines.append(ln)
    return "\n".join(lines)


def fetch_page_text(url: str, timeout: int = 30):
    """Ambil halaman dan kembalikan teks bacaannya.

    Returns:
        (True, title, normalized_text) saat sukses.
        (False, None, pesan_error) saat gagal.
    """
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT,
                     "Accept": "text/html,application/xhtml+xml"},
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.exceptions.Timeout:
        return False, None, "timeout (tidak merespons dalam 30 dtk)"
    except requests.exceptions.ConnectionError:
        return False, None, "tidak bisa terhubung (DNS/koneksi)"
    except requests.exceptions.RequestException as e:
        return False, None, f"galat jaringan: {e.__class__.__name__}"

    if resp.status_code != 200:
        return False, None, f"HTTP {resp.status_code}"

    ctype = resp.headers.get("Content-Type", "").lower()
    if "html" not in ctype and "text" not in ctype:
        return False, None, f"tipe konten tak didukung ({ctype or 'tak dikenal'})"

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        return False, None, f"gagal parsing HTML: {e}"

    for tag in soup(JUNK_TAGS):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = normalize_text(soup.title.string)[:120]

    # <body> kalau ada, kalau tidak seluruh dokumen.
    root = soup.body if soup.body else soup
    text = normalize_text(root.get_text(separator="\n"))

    if not text:
        return False, None, "halaman kosong / tidak ada teks terbaca"

    return True, title, text
