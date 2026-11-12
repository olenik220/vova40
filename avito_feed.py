"""Сборка единого XML-фида автозагрузки Авито по всем активным товарам.

Запуск вручную/по cron:  ./venv/bin/python avito_feed.py
Пишет /opt/vova40/static/avito_feeds/feed.xml и лог avito_feed.log.
"""
import os
import re
import sys
import json
import sqlite3
import logging
from xml.sax.saxutils import escape as xmlesc

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from config import config as cfg   # noqa: E402
import avito_map        # noqa: E402
import avito_enums      # noqa: E402

DB_PATH = os.path.join(BASE_DIR, "data.db")
FEED_PATH = os.path.join(BASE_DIR, "static", "avito_feeds", "feed.xml")
LOG_PATH = os.path.join(BASE_DIR, "avito_feed.log")

# Какие статусы попадают в фид. Всё остальное Авито воспримет как «удалено» и заархивирует.
FEED_STATUSES = ("В работе", "На Avito")

# лог может быть недоступен для записи (например, создан root'ом) — тогда пишем в stderr
try:
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                        format="[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    logging.getLogger().info("--- запуск сборки фида ---")
except Exception:
    logging.basicConfig(level=logging.INFO,
                        format="[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")


def photo_urls(item):
    """Абсолютные https-ссылки на фото товара (максимум 10)."""
    base = (getattr(cfg, "AVITO_PUBLIC_URL", None) or "https://vova40.ru").rstrip("/")
    out = []
    for p in (item.get("photos") or "").split(","):
        p = p.strip()
        if not p:
            continue
        if p.startswith("http"):
            out.append(p)
        else:
            out.append(base + "/" + p.lstrip("/"))
    return out[:10]


# категории, в которых Авито требует бренд и размер
NEEDS_BRAND_SIZE = {"Одежда, обувь, аксессуары", "Велосипеды"}


def problems(item, am):
    """Причины, по которым товар нельзя отправить в Авито.

    Набор обязательных полей повторяет то, что Авито реально требуют в категории:
    у аксессуаров может не быть размера и бренда — это нормально.
    """
    errs = []
    if not (item.get("title") or "").strip():
        errs.append("нет названия")
    if not (item.get("description") or "").strip():
        errs.append("нет описания")
    try:
        price = int(item.get("sale_price") or 0)
    except Exception:
        price = 0
    if price <= 0:
        errs.append("цена 0")
    if not photo_urls(item):
        errs.append("нет фото")
    if not (am.get("category") or "").strip():
        errs.append("не определена категория Авито")
    if not (am.get("goods_type") or "").strip():
        errs.append("не определён GoodsType")

    # бренд, размер и цвет НЕ требуем: если их нет в выгрузке Авито,
    # значит для этой категории они не нужны (проверено на 870 объявлениях)
    return errs


def render_ad(item, am):
    """Один блок <Ad> по схеме Авито (формат v3)."""
    item_id = item["id"]
    article = (item.get("article") or "").strip()
    link = (item.get("avito_link") or "").strip()
    m = re.search(r"_(\d{6,})$", link) or re.search(r"/(\d{6,})/?$", link)
    avito_id = m.group(1) if m else ""
    # Id не должен меняться между выгрузками: для размещённого объявления это его номер на Авито
    xml_id = avito_id or article or "vova40_%s" % item_id

    city = (item.get("city") or "").strip() or getattr(cfg, "DEFAULT_CITY", "Москва")
    address = (item.get("address") or "").strip()
    country = (item.get("country") or "").strip() or getattr(cfg, "DEFAULT_COUNTRY", "Россия")
    title = (item.get("title") or "").strip()[:50]
    desc = (item.get("description") or "").strip()[:7500]
    price = int(item.get("sale_price") or 0)

    parts = ["  <Ad>", "    <Id>%s</Id>" % xmlesc(xml_id)]
    if avito_id:
        parts.append("    <AvitoId>%s</AvitoId>" % avito_id)
    parts.append("    <Category>%s</Category>" % xmlesc(am["category"]))
    if (am.get("goods_type") or "").strip():
        parts.append("    <GoodsType>%s</GoodsType>" % xmlesc(am["goods_type"]))
    if (am.get("apparel") or "").strip():
        parts.append("    <Apparel>%s</Apparel>" % xmlesc(am["apparel"]))
    if (am.get("apparel_type") or "").strip():
        parts.append("    <ApparelType>%s</ApparelType>" % xmlesc(am["apparel_type"]))
    if (am.get("ad_type") or "").strip():
        parts.append("    <AdType>%s</AdType>" % xmlesc(am["ad_type"]))
    parts += [
        "    <Title>%s</Title>" % xmlesc(title),
        "    <Description>%s</Description>" % xmlesc(desc),
        "    <Price>%d</Price>" % price,
        "    <Currency>RUR</Currency>",
        "    <Address>%s</Address>" % xmlesc(address or city),
        "    <Condition>%s</Condition>" % xmlesc(am["condition"]),
    ]
    brand = (item.get("brand") or "").strip()
    if brand:
        parts.append("    <Brand>%s</Brand>" % xmlesc(brand))
    if (am.get("size") or "").strip():
        parts.append("    <Size>%s</Size>" % xmlesc(am["size"]))
    if (am.get("color") or "").strip():
        parts.append("    <Color>%s</Color>" % xmlesc(am["color"]))
    if (am.get("material") or "").strip():
        parts.append("    <Material>%s</Material>" % xmlesc(am["material"]))
    if (am.get("season") or "").strip():
        parts.append("    <SeasonName>%s</SeasonName>" % xmlesc(am["season"]))
    if country:
        parts.append("    <CountryOfOrigin>%s</CountryOfOrigin>" % xmlesc(country))
    parts.append("    <Images>")
    for u in photo_urls(item):
        parts.append('      <Image url="%s">%s</Image>' % (xmlesc(u), xmlesc(u)))
    parts.append("    </Images>")
    parts.append("    <AdStatus>Free</AdStatus>")
    parts.append("  </Ad>")
    return "\n".join(parts)


ACCOUNT_CACHE = os.path.join(BASE_DIR, "static", "avito_feeds", "account_ads.json")


def account_ads(max_age=600):
    """Объявления в аккаунте Авито через API (для сверки с фидом). Кэш 10 минут."""
    import time as _t
    import json as _json
    try:
        if os.path.exists(ACCOUNT_CACHE) and (_t.time() - os.path.getmtime(ACCOUNT_CACHE) < max_age):
            with open(ACCOUNT_CACHE, encoding="utf-8") as f:
                return _json.load(f)
    except Exception:
        pass

    result = {"total": 0, "ids": [], "titles": {}, "error": ""}
    try:
        import avito_sync as asy
        env = asy.load_env()
        tok = asy.get_token(env)
        if not tok:
            result["error"] = "нет токена Авито"
        else:
            ids, titles, page = [], {}, 1
            while page <= 30:
                code, body = asy.http(
                    "https://api.avito.ru/core/v1/items?per_page=100&page=%d" % page,
                    headers={"Authorization": "Bearer " + tok})
                if code != 200:
                    if page == 1:
                        result["error"] = "HTTP %s" % code
                    break
                data = _json.loads(body.decode("utf-8", "replace"))
                res = data.get("resources") or []
                if not res:
                    break
                for x in res:
                    sid = str(x.get("id"))
                    ids.append(sid)
                    titles[sid] = x.get("title") or ""
                if len(res) < 100:
                    break
                page += 1
            result.update({"total": len(ids), "ids": ids, "titles": titles})
    except Exception as e:
        result["error"] = str(e)

    try:
        os.makedirs(os.path.dirname(ACCOUNT_CACHE), exist_ok=True)
        with open(ACCOUNT_CACHE, "w", encoding="utf-8") as f:
            _json.dump(result, f, ensure_ascii=False)
    except Exception:
        pass
    return result


def build(db_path=DB_PATH):
    """Собирает XML и статистику. Возвращает (xml, stats)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    q = "select * from items where status in (%s) order by id" % ",".join("?" * len(FEED_STATUSES))
    rows = [dict(r) for r in conn.execute(q, FEED_STATUSES)]
    conn.close()

    ads, skipped, seen_ids = [], [], {}
    for item in rows:
        am = avito_map.build(item)
        errs = problems(item, am)
        if errs:
            skipped.append({"id": item["id"], "title": item.get("title") or "",
                            "reasons": errs})
            continue
        xml_id = (item.get("article") or "").strip() or "vova40_%s" % item["id"]
        if xml_id in seen_ids:
            skipped.append({"id": item["id"], "title": item.get("title") or "",
                            "reasons": ["Id «%s» уже занят товаром #%s" % (xml_id, seen_ids[xml_id])]})
            continue
        seen_ids[xml_id] = item["id"]
        ads.append(render_ad(item, am))

    # сверка с аккаунтом Авито: чего не хватает в фиде и что появится заново
    acc = account_ads()
    feed_avito_ids = set()
    for item in rows:
        m = re.search(r"_(\d{6,})$", (item.get("avito_link") or "").strip())
        if m:
            feed_avito_ids.add(m.group(1))
    acc_ids = set(acc.get("ids") or [])

    # все объявления, которые вообще есть в нашей базе (любой статус)
    conn2 = sqlite3.connect(DB_PATH)
    db_avito_ids = set()
    for (link,) in conn2.execute("select avito_link from items where coalesce(avito_link,'')<>''"):
        m = re.search(r"_(\d{6,})$", (link or "").strip())
        if m:
            db_avito_ids.add(m.group(1))
    conn2.close()

    ignore_path = os.path.join(BASE_DIR, "avito_feed_ignore.txt")
    ignored = set()
    if os.path.exists(ignore_path):
        with open(ignore_path, encoding="utf-8") as f:
            for line in f:
                line = line.split("#")[0].strip()
                if line:
                    ignored.add(line)

    not_in_feed = (acc_ids - feed_avito_ids) - ignored
    # лежат в базе (например «Продано») — уйдут в архив осознанно
    planned_archive = sorted(not_in_feed & db_avito_ids)
    # про них мы вообще ничего не знаем — это опасно
    unknown = sorted(not_in_feed - db_avito_ids)
    missing = [{"id": a, "title": (acc.get("titles") or {}).get(a, "")} for a in unknown]
    extra = sorted(feed_avito_ids - acc_ids)

    xml = "\n".join(['<?xml version="1.0" encoding="UTF-8"?>',
                     '<Ads formatVersion="3" target="Avito.ru">'] + ads + ["</Ads>"])
    stats = {
        "total_in_db": len(rows),
        "in_feed": len(ads),
        "skipped": skipped,
        "statuses": list(FEED_STATUSES),
        "generated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "avito_total": acc.get("total", 0),
        "avito_error": acc.get("error", ""),
        "missing_from_feed": missing[:50],
        "missing_count": len(missing),
        "planned_archive_count": len(planned_archive),
        "new_for_avito": len(extra),
        # фид можно отдавать только когда в нём нет «потерянных» объявлений
        "safe": (not acc.get("error")) and len(missing) == 0,
    }
    return xml, stats


def write_feed():
    xml, stats = build()
    os.makedirs(os.path.dirname(FEED_PATH), exist_ok=True)
    try:
        with open(FEED_PATH, "w", encoding="utf-8") as f:
            f.write(xml)
        stats_path = FEED_PATH.replace(".xml", "_stats.json")
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=1)
    except Exception as e:
        logging.error("не смог записать фид: %s", e)
    logging.info("фид собран: товаров %d, в фиде %d, отброшено %d",
                 stats["total_in_db"], stats["in_feed"], len(stats["skipped"]))
    for s in stats["skipped"]:
        logging.info("  пропуск #%s %s — %s", s["id"], s["title"][:40], "; ".join(s["reasons"]))
    return xml, stats


def item_state(item):
    """Состояние товара относительно фида: попадёт ли он в выгрузку и почему нет."""
    am = avito_map.build(item)
    errs = problems(item, am)
    status = (item.get("status") or "").strip()
    in_status = status in FEED_STATUSES
    return {
        "in_feed": in_status and not errs,
        "status_ok": in_status,
        "issues": errs,
        "status": status,
        "category": am.get("category", ""),
        "goods_type": am.get("goods_type", ""),
        "apparel": am.get("apparel", ""),
        "apparel_type": am.get("apparel_type", ""),
        "condition": am.get("condition", ""),
        "size": am.get("size", ""),
    }


def push_to_avito():
    """Пересобирает фид и просит Авито обработать его немедленно.

    Возвращает (ok, текст). Авито обрабатывает ВЕСЬ файл, а не один товар —
    так устроена автозагрузка. Расписание определяет, сколько объявлений
    публикуется за раз.
    """
    import avito_sync as asy
    xml, stats = write_feed()
    if not stats.get("safe"):
        return False, ("Фид не отправлен: состав не совпадает с аккаунтом "
                       "(неизвестных объявлений: %s)" % stats.get("missing_count"))
    env = asy.load_env()
    tok = asy.get_token(env)
    if not tok:
        return False, "Нет токена Авито — проверьте настройки"
    code, body = asy.http("https://api.avito.ru/autoload/v1/upload",
                          headers={"Authorization": "Bearer " + tok}, method="POST")
    text = body.decode("utf-8", "replace")[:300]
    if code in (200, 201, 202):
        return True, "Авито принял команду на выгрузку (%s). Отчёт появится в кабинете в течение часа." % code
    return False, "Авито ответил %s: %s" % (code, text)


if __name__ == "__main__":
    xml, st = write_feed()
    print("в базе активных: %d, в фиде: %d, отброшено: %d"
          % (st["total_in_db"], st["in_feed"], len(st["skipped"])))
    for s in st["skipped"][:30]:
        print("  #%s %s — %s" % (s["id"], s["title"][:40], "; ".join(s["reasons"])))
    print("файл:", FEED_PATH, os.path.getsize(FEED_PATH), "байт")
