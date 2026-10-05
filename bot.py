#!/usr/bin/env python3
"""WebPing Bot — pemantau perubahan website via Telegram.

Alur pakai:
  1. Kirim link website ke bot (atau /add).
  2. Pilih interval pengecekan lewat tombol: 15 mnt / 1 jam / 6 jam / 24 jam.
  3. Bot menyimpan baseline teks halaman, lalu mengecek berkala.
  4. Kalau isi teks berubah -> notifikasi + cuplikan perbedaannya.

Perintah:
  /start          sambutan & cara pakai
  /add            tambah website (lalu kirim linknya)
  /list           daftar website yang dipantau
  /check <nomor>  cek manual satu website sekarang
  /remove <nomor> berhenti memantau satu website
  /help           bantuan

Bot ini privat: hanya chat pertama yang menyapa (/start) yang jadi owner.
Dijalankan via long-polling; watchdog.sh menjaganya tetap hidup.
Pengaman single-instance: fcntl LOCK_EX|LOCK_NB di bot.lock
(watchdog.sh juga diserialisasi dengan flock).
"""
import difflib
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import traceback
import urllib.parse
from datetime import datetime, timezone

import tg
from fetcher import fetch_page_text, normalize_text

DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(DIR, "state.json")
LOCK_PATH = os.path.join(DIR, "bot.lock")
LOG_PATH = os.path.join(DIR, "bot.log")

INTERVALS = [
    (15, "15 menit"),
    (60, "1 jam"),
    (360, "6 jam"),
    (1440, "24 jam"),
]
INTERVAL_MAP = dict(INTERVALS)

MAX_DIFF_LINES = 20
MAX_MSG = 4000
DOWN_ALERT_AFTER = 3  # gagal beruntun -> beri tahu owner sekali


def log(msg):
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- state ---
def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                st = json.load(f)
        except (OSError, json.JSONDecodeError):
            st = {}
    else:
        st = {}
    st.setdefault("owner_chat_id", None)
    st.setdefault("monitors", [])
    st.setdefault("next_id", 1)
    st.setdefault("pending", {})   # str(chat_id) -> {"url":..., "title":...}
    return st


def save_state(st):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


# --------------------------------------------------------------- helpers ---
def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_url(raw):
    """Kembalikan URL http(s) yang valid, atau None."""
    text = (raw or "").strip()
    if not text or len(text) > 2048:
        return None
    if " " in text:
        return None
    if not re.match(r"^https?://", text, re.IGNORECASE):
        text = "https://" + text
    try:
        p = urllib.parse.urlparse(text)
    except ValueError:
        return None
    if p.scheme not in ("http", "https"):
        return None
    if not p.netloc or "." not in p.netloc:
        return None
    return text


def diff_snippet(old_text, new_text):
    """Cuplikan baris yang berubah: (+N -M) + daftar baris +/-."""
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff = difflib.unified_diff(old_lines, new_lines, lineterm="", n=1)
    changed = [ln for ln in diff
               if (ln.startswith("+") or ln.startswith("-"))
               and not ln.startswith(("+++", "---"))]
    added = sum(1 for ln in changed if ln.startswith("+"))
    removed = sum(1 for ln in changed if ln.startswith("-"))
    head = changed[:MAX_DIFF_LINES]
    out = [f"(+{added} -{removed} baris berubah)", ""]
    out.extend(head)
    if len(changed) > MAX_DIFF_LINES:
        out.append(f"... (+{len(changed) - MAX_DIFF_LINES} baris lagi)")
    snippet = "\n".join(out)
    if len(snippet) > MAX_MSG:
        snippet = snippet[:MAX_MSG] + "\n...(dipotong)"
    return snippet


def interval_label(minutes):
    return INTERVAL_MAP.get(minutes, f"{minutes} mnt")


def monitor_by_number(st, num):
    for m in st["monitors"]:
        if m["id"] == num:
            return m
    return None


def send(chat_id, text, reply_markup=None):
    try:
        tg.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception as e:
        log(f"send_message gagal ke {chat_id}: {e}")


def interval_keyboard():
    return {"inline_keyboard": [
        [{"text": label, "callback_data": f"iv:{mins}"}]
        for mins, label in INTERVALS
    ]}


# ---------------------------------------------------------------- checks ---
def do_check(st, mon, notify=True):
    """Cek satu monitor. Kembalikan True jika konten berubah."""
    chat_id = st["owner_chat_id"]
    ok, title, payload = fetch_page_text(mon["url"])
    mon["last_check"] = datetime.now(timezone.utc).isoformat()

    if not ok:
        mon["fail_count"] = mon.get("fail_count", 0) + 1
        mon["last_status"] = f"galat: {payload}"
        log(f"check #{mon['id']} {mon['url']} GAGAL: {payload} "
            f"(fail_count={mon['fail_count']})")
        if (notify and mon["fail_count"] >= DOWN_ALERT_AFTER
                and not mon.get("down_notified")):
            mon["down_notified"] = True
            send(chat_id,
                 f"⚠️ {mon['name']}\n{mon['url']}\n"
                 f"Tidak bisa diakses {DOWN_ALERT_AFTER}x berturut-turut "
                 f"({payload}). Cek URL-nya — kalau situsnya memang down, "
                 f"pemantauan tetap jalan dan akan kabari saat kembali normal.")
        save_state(st)
        return False

    mon["fail_count"] = 0
    mon["down_notified"] = False
    mon["last_status"] = "ok"
    if title and not mon.get("name"):
        mon["name"] = title

    new_hash = sha256_hex(payload)
    old_hash = mon.get("last_hash")

    if not old_hash:
        # Baseline pertama.
        mon["last_hash"] = new_hash
        mon["last_text"] = payload
        log(f"check #{mon['id']} {mon['url']} baseline tersimpan "
            f"({len(payload)} karakter)")
        if notify:
            send(chat_id,
                 f"✅ Pemantauan dimulai: {mon['name']}\n{mon['url']}\n"
                 f"Dicek tiap {interval_label(mon['interval_min'])}. "
                 f"Aku kabari kalau isi halamannya berubah.")
        save_state(st)
        return False

    if new_hash == old_hash:
        log(f"check #{mon['id']} {mon['url']} tidak berubah")
        save_state(st)
        return False

    # Berubah!
    old_text = mon.get("last_text", "")
    snippet = diff_snippet(old_text, payload) if old_text else "(isi berubah)"
    mon["last_hash"] = new_hash
    mon["last_text"] = payload
    log(f"check #{mon['id']} {mon['url']} BERUBAH")
    if notify:
        send(chat_id,
             f"🔔 Perubahan terdeteksi: {mon['name']}\n{mon['url']}\n\n"
             f"{snippet}")
    save_state(st)
    return True


def check_due(st):
    now = datetime.now(timezone.utc)
    for mon in st["monitors"]:
        try:
            last = datetime.fromisoformat(mon["last_check"]) \
                if mon.get("last_check") else None
        except ValueError:
            last = None
        if last is None:
            due = True
        else:
            elapsed = (now - last).total_seconds()
            due = elapsed >= mon["interval_min"] * 60
        if due:
            try:
                do_check(st, mon)
            except Exception:
                log(f"check_due #{mon['id']} exception:\n{traceback.format_exc()}")
            time.sleep(1)


# --------------------------------------------------------------- handlers ---
HELP_TEXT = (
    "🤖 *WebPing Bot* — pemantau perubahan website.\n"
    "\n"
    "Kirim link website apa pun, pilih interval cek, "
    "nanti aku kabari kalau isi halamannya berubah.\n"
    "\n"
    "Perintah:\n"
    "/add — tambah website pantauan\n"
    "/list — daftar website yang dipantau\n"
    "/check <nomor> — cek manual sekarang\n"
    "/remove <nomor> — berhenti memantau\n"
    "/help — bantuan ini"
)


def cmd_start(st, chat_id):
    if st["owner_chat_id"] is None:
        st["owner_chat_id"] = chat_id
        save_state(st)
        log(f"owner diset: {chat_id}")
    send(chat_id,
         "Halo! Aku WebPing Bot 👀\n\n"
         "Kirim link website yang mau dipantau, "
         "pilih tiap berapa lama aku cek, "
         "dan aku kabari kalau ada perubahan.\n\n"
         "Ketik /help untuk daftar perintah.")


def cmd_add(st, chat_id):
    st["pending"][str(chat_id)] = {"step": "awaiting_url"}
    save_state(st)
    send(chat_id, "Kirim link websitenya (mis. https://contoh.com/artikel).")


def cmd_list(st, chat_id):
    if not st["monitors"]:
        send(chat_id, "Belum ada website yang dipantau. Kirim link untuk mulai.")
        return
    lines = ["📋 Website yang dipantau:\n"]
    for m in st["monitors"]:
        status = "✅" if m.get("last_status") == "ok" else "⚠️"
        lines.append(
            f"{status} *{m['id']}.* {m['name']}\n"
            f"   {m['url']}\n"
            f"   tiap {interval_label(m['interval_min'])}"
            + (f" — {m.get('last_status')}" if m.get("last_status") != "ok" else "")
        )
    lines.append("\n/check <nomor> cek manual • /remove <nomor> hapus")
    send(chat_id, "\n".join(lines), )


def cmd_remove(st, chat_id, arg):
    try:
        num = int((arg or "").strip())
    except ValueError:
        send(chat_id, "Pakai: /remove <nomor> — lihat nomornya di /list.")
        return
    mon = monitor_by_number(st, num)
    if not mon:
        send(chat_id, f"Tidak ada monitor nomor {num}. Cek /list.")
        return
    st["monitors"] = [m for m in st["monitors"] if m["id"] != num]
    save_state(st)
    log(f"monitor #{num} dihapus ({mon['url']})")
    send(chat_id, f"🗑️ Berhenti memantau: {mon['name']}\n{mon['url']}")


def cmd_check(st, chat_id, arg):
    try:
        num = int((arg or "").strip())
    except ValueError:
        send(chat_id, "Pakai: /check <nomor> — lihat nomornya di /list.")
        return
    mon = monitor_by_number(st, num)
    if not mon:
        send(chat_id, f"Tidak ada monitor nomor {num}. Cek /list.")
        return
    send(chat_id, f"🔍 Mengecek {mon['name']}...")
    changed = do_check(st, mon)
    if not changed and mon.get("last_status") == "ok":
        send(chat_id, f"Tidak ada perubahan di {mon['name']}.")


def handle_url(st, chat_id, url):
    """User mengirim URL: validasi, ambil judul, tawarkan interval."""
    send(chat_id, "⏳ Membuka halamannya dulu...")
    ok, title, payload = fetch_page_text(url)
    if not ok:
        send(chat_id,
             f"❌ Gagal membaca halaman itu ({payload}).\n"
             f"Pastikan link-nya benar dan bisa dibuka publik.")
        st["pending"].pop(str(chat_id), None)
        save_state(st)
        return
    name = title or urllib.parse.urlparse(url).netloc
    st["pending"][str(chat_id)] = {"step": "awaiting_interval",
                                   "url": url, "title": name}
    save_state(st)
    send(chat_id,
         f"📄 *{name}*\n{url}\n"
         f"Halaman terbaca ({len(payload)} karakter teks).\n\n"
         f"Tiap berapa lama aku cek?",
         reply_markup=interval_keyboard())


def handle_callback(st, cb):
    chat_id = cb["message"]["chat"]["id"]
    data = cb.get("data", "")
    tg.answer_callback(cb["id"])
    if not data.startswith("iv:"):
        return
    try:
        minutes = int(data.split(":", 1)[1])
    except ValueError:
        return
    if minutes not in INTERVAL_MAP:
        return
    pend = st["pending"].get(str(chat_id))
    if not pend or pend.get("step") != "awaiting_interval":
        send(chat_id, "Sesi tambah monitor sudah kedaluwarsa. Kirim link lagi ya.")
        return
    mon = {
        "id": st["next_id"],
        "url": pend["url"],
        "name": pend.get("title") or pend["url"],
        "interval_min": minutes,
        "last_check": None,
        "last_hash": None,
        "last_text": None,
        "last_status": "baru",
        "fail_count": 0,
        "down_notified": False,
        "created": datetime.now(timezone.utc).isoformat(),
    }
    st["monitors"].append(mon)
    st["next_id"] += 1
    st["pending"].pop(str(chat_id), None)
    save_state(st)
    log(f"monitor #{mon['id']} ditambah: {mon['url']} tiap {minutes} mnt")
    send(chat_id,
         f"👍 Oke, {mon['name']} akan dicek tiap "
         f"{interval_label(minutes)}.\nMenyimpan baseline...")
    do_check(st, mon)


def handle_message(st, msg):
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    if not chat_id:
        return
    text = (msg.get("text") or "").strip()

    # Kunci owner: chat pertama yang /start jadi pemilik.
    if st["owner_chat_id"] is None:
        if text.startswith("/start"):
            cmd_start(st, chat_id)
        else:
            send(chat_id, "Ketik /start dulu untuk memakai bot ini.")
        return
    if chat_id != st["owner_chat_id"]:
        send(chat_id, "Maaf, bot ini privat dan hanya untuk pemiliknya. 🙏")
        return

    if text.startswith("/start"):
        cmd_start(st, chat_id)
    elif text.startswith("/add"):
        cmd_add(st, chat_id)
    elif text.startswith("/list"):
        cmd_list(st, chat_id)
    elif text.startswith("/remove"):
        cmd_remove(st, chat_id, text[len("/remove"):])
    elif text.startswith("/check"):
        cmd_check(st, chat_id, text[len("/check"):])
    elif text.startswith("/help"):
        send(chat_id, HELP_TEXT)
    elif text.startswith("/"):
        send(chat_id, "Perintah tidak dikenal. Ketik /help.")
    else:
        url = normalize_url(text)
        pend = st["pending"].get(str(chat_id))
        if pend and pend.get("step") == "awaiting_url":
            if not url:
                send(chat_id, "Itu bukan link yang valid. Coba lagi ya.")
                return
            handle_url(st, chat_id, url)
        elif url:
            # Link langsung tanpa /add -> langsung ke alur tambah.
            handle_url(st, chat_id, url)
        else:
            send(chat_id,
                 "Kirim link website untuk dipantau, atau ketik /help.")


def main():
    # Pengaman single-instance: hanya satu proses boleh jalan.
    lock_fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Instance lain sedang berjalan, keluar.", flush=True)
        sys.exit(0)

    st = load_state()
    log("WebPing Bot mulai. Menunggu pesan...")
    offset = 0
    while True:
        try:
            resp = tg.get_updates(offset=offset, timeout=25)
            if not resp.get("ok"):
                log(f"getUpdates tidak ok: {resp}")
                time.sleep(5)
                continue
            for upd in resp.get("result", []):
                uid = upd.get("update_id", 0)
                try:
                    if "message" in upd:
                        handle_message(st, upd["message"])
                    elif "callback_query" in upd:
                        handle_callback(st, upd["callback_query"])
                except Exception:
                    log(f"handler exception:\n{traceback.format_exc()}")
                # Offset dimajukan SETELAH diproses agar pesan tak hilang
                # kalau proses crash di tengah jalan.
                offset = uid + 1
        except KeyboardInterrupt:
            break
        except Exception as e:
            # 409 Conflict = instance ganda (seharusnya tak terjadi
            # berkat file-lock); catat dan mundur sejenak.
            log(f"poll error: {e}")
            time.sleep(10)
            continue
        try:
            check_due(st)
        except Exception:
            log(f"check_due exception:\n{traceback.format_exc()}")


if __name__ == "__main__":
    main()
