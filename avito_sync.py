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


def fetch_all_items(tok):
    """Все объявления аккаунта одним списком: 9 запросов вместо 886.

    Возвращает {avito_id: {status, title, price, url}} либо {} при ошибке.
    """
    out = {}
    page = 1
    while page <= 40:
        code, body = http("https://api.avito.ru/core/v1/items?per_page=100&page=%d" % page,
                          headers={"Authorization": "Bearer " + tok}, timeout=40)
        if code != 200:
            log("список объявлений: HTTP %s %s" % (code, body[:120]))
            return {}
        data = json.loads(body.decode("utf-8", "replace"))
        res = data.get("resources") or []
        if not res:
            break
        for x in res:
            price = x.get("price")
            price = price.get("value") if isinstance(price, dict) else price
            out[str(x.get("id"))] = {
                "status": x.get("status") or "",
                "title": x.get("title") or "",
                "price": price,
                "url": x.get("url") or "",
            }
        if len(res) < 100:
            break
        page += 1
        time.sleep(0.3)          # вежливая пауза, чтобы не ловить 429
    return out


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
        "SELECT id, title, status, avito_link, avito_status, avito_finish "
        "FROM items WHERE coalesce(avito_link,'') <> ''")]

    if not rows:
        log("нет товаров с привязкой к Avito")
        conn.close()
        return 0

    remote = fetch_all_items(tok)
    if not remote:
        log("не удалось получить список объявлений — выхожу без изменений")
        conn.close()
        return 1

    import re as _re

    def extract_id(link):
        link = (link or "").strip()
        for pat in (r"_(\d+)$", r"/autoload/(\d+)", r"/item/(\d+)", r"(\d{6,})$"):
            m = _re.search(pat, link)
            if m:
                return m.group(1)
        return ""

    changed = errors = missing = 0
    for it in rows:
        avito_id = extract_id(it["avito_link"])
        if not avito_id:
            log("#%s: не смог вытащить id объявления из %s" % (it["id"], (it["avito_link"] or "")[:60]))
            errors += 1
            continue

        rec = remote.get(avito_id)
        if rec is None:
            # в списке его нет — уточняем точечно, чтобы не поставить неверный статус
            missing += 1
            code, body = fetch_avito_status(tok, user_id, avito_id)
            detail_status = ""
            if code == 200:
                try:
                    detail_status = json.loads(body.decode("utf-8", "replace")).get("status", "")
                except Exception:
                    detail_status = ""
            elif code in (404, 410, 422):
                detail_status = "removed"

            cur = it["status"] or ""
            guessed = AVITO_STATUS_MAP.get(detail_status, "")
            if not detail_status:
                log("#%s: нет в списке и статус не получен (HTTP %s) — не меняю" % (it["id"], code))
                errors += 1
                continue
            if cur in FINAL:
                if not DRY:
                    update_status(conn, it["id"], cur, avito_status=detail_status)
                continue
            if guessed and guessed == cur:
                continue          # статус уже такой же — не шумим в логе каждые 10 минут
            log("#%s: нет в списке, Авито сообщает «%s» -> %s"
                % (it["id"], detail_status, guessed or "без изменений"))
            if guessed and not DRY:
                update_status(conn, it["id"], guessed, avito_status=detail_status)
            if guessed:
                changed += 1
            continue

        avito_status = rec["status"]
        new_status = AVITO_STATUS_MAP.get(avito_status, "")
        cur = it["status"] or ""

        if not DRY:
            update_status(conn, it["id"], new_status or cur,
                          avito_status=avito_status,
                          avito_finish=it.get("avito_finish") or "")

        if not new_status:
            log("#%s: неизвестный статус Avito «%s» (сохранён как есть)" % (it["id"], avito_status))
            continue
        if new_status == cur:
            continue
        if cur in FINAL and new_status == "На Avito":
            log("#%s: пропуск отката %s -> На Avito" % (it["id"], cur))
            continue
        log("#%s: %s -> %s (Avito: %s)" % (it["id"], cur, new_status, avito_status))
        if not DRY:
            update_status(conn, it["id"], new_status)
        changed += 1

    if not DRY:
        conn.commit()
    conn.close()
    log("итого: в аккаунте %d, проверено %d, изменено %d, нет в аккаунте %d, ошибок %d"
        % (len(remote), len(rows), changed, missing, errors))
    return 0


if __name__ == "__main__":
    sys.exit(main())
