#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Автосинхронизация статусов объявлений Avito -> data.db (для cron).

Запуск:  ./venv/bin/python avito_sync.py [--dry-run]
Логи:    печатает в stdout (cron пишет в /var/log/vova40/avito_sync.log)
"""
import os
import sys
import json
import time
import sqlite3
import urllib.parse
import urllib.request
import urllib.error

BASE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE, ".env")
DB_PATH = os.path.join(BASE, "data.db")
DRY = "--dry-run" in sys.argv

AVITO_STATUS_MAP = {
    "active": "На Avito",
    "delivery": "В доставке",
    "sold": "Продано",
    "inactive": "Продано",
    "removed": "Продано",
    "unavailable": "Продано",
    # объявление снято/архивировано (истёк срок или заменено новой версией)
    "old": "Архив",
}
# статусы, которые только логируем и не меняем (требуют ручного разбора)
UNMAPPED_LOGGED = ("blocked", "rejected", "moderation", "draft")
FINAL = ("Продано", "Списано")
DEFAULT_USER_ID = 40205075


def log(msg):
    print("[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def load_env():
    env = {}
    with open(ENV_PATH, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def save_env_values(values):
    """Аккуратно переписать значения ключей в .env, не трогая остальные строки."""
    try:
        with open(ENV_PATH, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return
    remaining = dict(values)
    out = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in remaining:
                out.append("%s=%s" % (k, remaining.pop(k)))
                continue
        out.append(line)
    for k, v in remaining.items():
        out.append("%s=%s" % (k, v))
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def http(url, headers=None, data=None, method=None, timeout=25):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()


def get_token(env):
    """Берёт сохранённый токен или обновляет по client_credentials."""
    tok = env.get("AVITO_ACCESS_TOKEN", "")
    try:
        exp = int(env.get("AVITO_TOKEN_EXPIRES_AT") or 0)
    except ValueError:
        exp = 0
    if tok and exp > time.time() + 120:
        return tok

    cid, secret = env.get("AVITO_CLIENT_ID", ""), env.get("AVITO_CLIENT_SECRET", "")
    if not cid or not secret:
        log("НЕТ КЛЮЧЕЙ AVITO_CLIENT_ID/SECRET — синхронизация невозможна")
        return ""

    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": cid,
        "client_secret": secret,
    }).encode()
    code, body = http("https://api.avito.ru/token", {"Content-Type": "application/x-www-form-urlencoded"}, data)
    if code != 200:
        log("не удалось получить токен: %s %s" % (code, body[:150]))
        return ""
    js = json.loads(body.decode("utf-8"))
    tok = js.get("access_token", "")
    exp = int(time.time()) + int(js.get("expires_in", 86400))
    save_env_values({"AVITO_ACCESS_TOKEN": tok, "AVITO_TOKEN_EXPIRES_AT": str(exp)})
    log("токен обновлён, действует до %s" % time.strftime("%Y-%m-%d %H:%M", time.localtime(exp)))
    return tok


def get_account_id(tok):
    code, body = http("https://api.avito.ru/core/v1/accounts/self", {"Authorization": "Bearer " + tok})
    if code == 200:
        try:
            return str(json.loads(body.decode("utf-8")).get("id") or DEFAULT_USER_ID)
        except Exception:
            pass
    return str(DEFAULT_USER_ID)


def fetch_avito_status(tok, user_id, avito_id):
    url = "https://api.avito.ru/core/v1/accounts/%s/items/%s/" % (user_id, avito_id)
    return http(url, {"Authorization": "Bearer " + tok}, timeout=15)


def update_status(conn, item_id, status, avito_link=None, avito_status=None, avito_finish=None):
    if avito_status is not None or avito_finish is not None:
        sets = []
        vals = []
        if avito_status is not None:
            sets.append("avito_status=?"); vals.append(avito_status)
        if avito_finish is not None:
            sets.append("avito_finish=?"); vals.append(avito_finish)
        vals.append(item_id)
        try:
            conn.execute("UPDATE items SET " + ", ".join(sets) + " WHERE id=?", vals)
        except Exception as e:
            log("#%s: не сохранил статус Авито: %s" % (item_id, e))
    sets = ["status=?", "updated_at=datetime('now','localtime')"]
    vals = [status]
    if status == "Продано":
        # дату продажи не перезаписываем, если она уже есть
        sets.append("sold_at=coalesce(nullif(sold_at,''), datetime('now','localtime'))")
    if avito_link:
        sets.append("avito_link=?")
        vals.append(avito_link)
    vals.append(item_id)
    conn.execute("UPDATE items SET " + ", ".join(sets) + " WHERE id=?", vals)


def main():
    env = load_env()
    tok = get_token(env)
    if not tok:
        return 1
    user_id = get_account_id(tok)
    log("аккаунт Avito: %s%s" % (user_id, " (dry-run)" if DRY else ""))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id, title, status, avito_link FROM items WHERE coalesce(avito_link,'') <> ''")]

    if not rows:
        log("нет товаров с привязкой к Avito")
        conn.close()
        return 0

    changed = errors = 0
    for it in rows:
        link = (it["avito_link"] or "").strip()
        m = None
        import re as _re
        for pat in (r"_(\d+)$", r"/autoload/(\d+)", r"/item/(\d+)", r"(\d{6,})$"):
            m = _re.search(pat, link)
            if m:
                break
        if not m:
            log("#%s: не смог вытащить id объявления из %s" % (it["id"], link[:60]))
            errors += 1
            continue
        avito_id = m.group(1)

        code, body = fetch_avito_status(tok, user_id, avito_id)
        if code == 200:
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception:
                log("#%s: не разобрал ответ API" % it["id"])
                errors += 1
                continue
            avito_status = data.get("status", "")
            avito_finish = data.get("finish_time", "") or ""
            new_status = AVITO_STATUS_MAP.get(avito_status, "")

            # если API когда-нибудь отдаст срок доставки — подхватим
            eta = ""
            for key in ("delivery_date", "deliveryDate", "delivery_eta"):
                if data.get(key):
                    eta = str(data.get(key))
                    break
            if not eta and isinstance(data.get("delivery"), dict):
                eta = str(data["delivery"].get("date") or data["delivery"].get("eta") or "")

            if not DRY and (new_status or avito_status):
                # статус и дату окончания объявления сохраняем всегда
                update_status(conn, it["id"], new_status or (it["status"] or ""),
                              avito_status=avito_status, avito_finish=avito_finish)
                if eta:
                    conn.execute("UPDATE items SET delivery_eta=? WHERE id=?", (eta, it["id"]))

            cur = it["status"] or ""
            if not new_status:
                log("#%s: неизвестный статус Avito «%s» (сохранён как есть)" % (it["id"], avito_status))
                continue
            if new_status == cur:
                continue
            if cur in FINAL and new_status == "На Avito":
                log("#%s: пропуск отката %s -> На Avito" % (it["id"], cur))
                continue
            log("#%s: %s -> %s (Avito: %s, до %s)" % (it["id"], cur, new_status, avito_status, avito_finish or "-"))
            if not DRY:
                update_status(conn, it["id"], new_status)
            changed += 1
        elif code in (404, 410, 422):
            cur = it["status"] or ""
            if cur in FINAL:
                continue
            log("#%s: объявление не найдено (HTTP %s) -> Продано" % (it["id"], code))
            if not DRY:
                update_status(conn, it["id"], "Продано")
            changed += 1
        else:
            log("#%s: HTTP %s %s" % (it["id"], code, body[:120]))
            errors += 1

    if not DRY:
        conn.commit()
    conn.close()
    log("итого: проверено %d, изменено %d, ошибок %d" % (len(rows), changed, errors))
    return 0


if __name__ == "__main__":
    sys.exit(main())
