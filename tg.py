"""Telegram Bot API wrapper untuk WebPing Bot.

Token resolution order:
  1. WEBPING_BOT_TOKEN env var (raw bot token).
  2. The stored custom.telegram-webping credential (Muse environment):
     the returned value is a surrogate the egress proxy replaces
     with the real token.

NOTE: url_with_surrogate_path_segment() percent-encodes the surrogate
(hsurr%3A...) which the egress proxy does not recognise, so the literal
surrogate reaches Telegram and every call 404s. We insert the surrogate
unencoded instead (it is only `hsurr:` + hex, path-safe).
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

HOST = "api.telegram.org"
_surrogate_val = None
_surrogate_ts = 0


def _get_surrogate():
    global _surrogate_val, _surrogate_ts
    env_tok = os.environ.get("WEBPING_BOT_TOKEN", "").strip()
    if env_tok:
        return env_tok
    if _surrogate_val and time.time() - _surrogate_ts < 600:
        return _surrogate_val
    # Muse environment: token dari kredensial custom.telegram-webping
    # (lazy import agar modul ini tetap bisa dipakai di luar Muse)
    sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
    from dynamic_credentials import dynamic_credential_entry
    _surrogate_val = dynamic_credential_entry("custom.telegram-webping")["surrogate"].strip()
    _surrogate_ts = time.time()
    return _surrogate_val


def api(method, payload=None, timeout=30, _retry=True):
    global _surrogate_val
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    url = f"https://{HOST}/bot{_get_surrogate()}/{method}"
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        if e.code in (401, 403) and _retry:
            # token mungkin di-rotate: buang cache lalu coba sekali lagi
            _surrogate_val = None
            _surrogate_ts = 0
            return api(method, payload, timeout, _retry=False)
        raise RuntimeError(f"Telegram API {method} -> HTTP {e.code}: {body}")
    except Exception as e:
        raise RuntimeError(f"Telegram API {method} gagal: {e}")


def send_message(chat_id, text, reply_markup=None, parse_mode=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return api("sendMessage", payload)


def answer_callback(callback_id, text=None):
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    return api("answerCallbackQuery", payload)


def get_updates(offset=0, timeout=25):
    return api("getUpdates", {"offset": offset, "timeout": timeout},
               timeout=timeout + 15)


def get_me():
    return api("getMe")
