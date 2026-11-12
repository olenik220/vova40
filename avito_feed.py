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

import config           # noqa: E402
import avito_map        # noqa: E402
import avito_enums      # noqa: E402

DB_PATH = os.path.join(BASE_DIR, "data.db")
FEED_PATH = os.path.join(BASE_DIR, "static", "avito_feeds", "feed.xml")
LOG_PATH = os.path.join(BASE_DIR, "avito_feed.log")

# Какие статусы попадают в фид. Всё остальное Авито воспримет как «удалено» и заархивирует.
FEED_STATUSES = ("В работе", "На Avito")

logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format="[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")


def photo_urls(item):
    """Абсолютные https-ссылки на фото товара (максимум 10)."""
    base = (getattr(config, "AVITO_PUBLIC_URL", None) or "https://vova40.ru").rstrip("/")
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


def problems(item, am):
    """Причины, по которым товар нельзя отправить в Авито."""
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
    if not (item.get("brand") or "").strip() or item.get("brand") == "Неизвестный бренд":
        errs.append("нет бренда")
    if not am.get("size"):
        errs.append("нет размера из справочника Авито")
    if not am.get("color"):
        errs.append("нет цвета")
    if not am.get("apparel_type"):
        errs.append("не определён ApparelType (нужен шаблон категории)")
    return errs


def render_ad(item, am):
    """Один блок <Ad> по схеме Авито (формат v3)."""
    item_id = item["id"]
    article = (item.get("article") or "").strip()
    xml_id = article or "vova40_%s" % item_id

    m = re.search(r"_(\d{6,})$", (item.get("avito_link") or "").strip())
    avito_id = m.group(1) if m else ""

    city = (item.get("city") or "").strip() or config.DEFAULT_CITY
    country = (item.get("country") or "").strip() or config.DEFAULT_COUNTRY
    title = (item.get("title") or "").strip()[:50]
    desc = (item.get("description") or "").strip()[:7500]
    price = int(item.get("sale_price") or 0)

    parts = ["  <Ad>", "    <Id>%s</Id>" % xmlesc(xml_id)]
    if avito_id:
        parts.append("    <AvitoId>%s</AvitoId>" % avito_id)
    parts += [
        "    <Category>%s</Category>" % xmlesc(am["category"]),
        "    <GoodsType>%s</GoodsType>" % xmlesc(am["goods_type"]),
        "    <Apparel>%s</Apparel>" % xmlesc(am["apparel"]),
        "    <ApparelType>%s</ApparelType>" % xmlesc(am["apparel_type"]),
        "    <AdType>%s</AdType>" % xmlesc(am["ad_type"]),
        "    <Title>%s</Title>" % xmlesc(title),
        "    <Description>%s</Description>" % xmlesc(desc),
        "    <Price>%d</Price>" % price,
        "    <Currency>RUR</Currency>",
        "    <Address>%s</Address>" % xmlesc(city),
        "    <Condition>%s</Condition>" % xmlesc(am["condition"]),
        "    <Brand>%s</Brand>" % xmlesc((item.get("brand") or "").strip()),
        "    <Size>%s</Size>" % xmlesc(am["size"]),
    ]
    if am.get("color"):
        parts.append("    <Color>%s</Color>" % xmlesc(am["color"]))
    if am.get("material"):
        parts.append("    <Material>%s</Material>" % xmlesc(am["material"]))
    if am.get("season"):
        parts.append("    <SeasonName>%s</SeasonName>" % xmlesc(am["season"]))
    if country:
        parts.append("    <CountryOfOrigin>%s</CountryOfOrigin>" % xmlesc(country))
    for k, v in (am.get("extra_attrs") or {}).items():
        parts.append('    <Attr name="%s">%s</Attr>' % (xmlesc(k), xmlesc(str(v))))
    parts.append("    <Images>")
    for u in photo_urls(item):
        parts.append('      <Image url="%s">%s</Image>' % (xmlesc(u), xmlesc(u)))
    parts.append("    </Images>")
    parts.append("    <AdStatus>Free</AdStatus>")
    parts.append("  </Ad>")
    return "\n".join(parts)


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

    xml = "\n".join(['<?xml version="1.0" encoding="UTF-8"?>',
                     '<Ads formatVersion="3" target="Avito.ru">'] + ads + ["</Ads>"])
    stats = {
        "total_in_db": len(rows),
        "in_feed": len(ads),
        "skipped": skipped,
        "statuses": list(FEED_STATUSES),
        "generated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return xml, stats


def write_feed():
    xml, stats = build()
    os.makedirs(os.path.dirname(FEED_PATH), exist_ok=True)
    with open(FEED_PATH, "w", encoding="utf-8") as f:
        f.write(xml)
    stats_path = FEED_PATH.replace(".xml", "_stats.json")
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    logging.info("фид собран: товаров %d, в фиде %d, отброшено %d",
                 stats["total_in_db"], stats["in_feed"], len(stats["skipped"]))
    for s in stats["skipped"]:
        logging.info("  пропуск #%s %s — %s", s["id"], s["title"][:40], "; ".join(s["reasons"]))
    return xml, stats


if __name__ == "__main__":
    xml, st = write_feed()
    print("в базе активных: %d, в фиде: %d, отброшено: %d"
          % (st["total_in_db"], st["in_feed"], len(st["skipped"])))
    for s in st["skipped"][:30]:
        print("  #%s %s — %s" % (s["id"], s["title"][:40], "; ".join(s["reasons"])))
    print("файл:", FEED_PATH, os.path.getsize(FEED_PATH), "байт")
