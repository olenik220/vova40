"""Импорт выгрузки автозагрузки Авито (Excel) в базу vova40.

Запуск на сервере:  ./venv/bin/python import_avito_export.py /tmp/export.xlsx
Идемпотентен: повторный запуск не создаёт дублей (сверка по номеру объявления).
"""
import sys
import os
import re
import json
import html as htmllib
import sqlite3
import zipfile
import xml.etree.ElementTree as ET
import urllib.request
import urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
DB = os.path.join(BASE_DIR, "data.db")
ENV = os.path.join(BASE_DIR, ".env")
M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

CATEGORY_MAP = {
    "Мужская одежда": "Мужская одежда",
    "Женская одежда": "Женская одежда",
    "Детская одежда": "Детская одежда",
    "Мужская обувь": "Мужская обувь",
    "Женская обувь": "Женская обувь",
    "Детская обувь": "Детская обувь",
    "Аксессуары": "Аксессуары",
    "Сумки, рюкзаки и чемоданы": "Сумки и аксессуары",
    "Часы и украшения": "Часы и украшения",
    "Велосипеды": "Велосипеды",
}
DESC_LIMIT = 7500


def env_values():
    out = {}
    for line in open(ENV, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def api_items():
    """id -> {url, address, status, price} по всем объявлениям аккаунта."""
    e = env_values()
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": e.get("AVITO_CLIENT_ID", ""),
        "client_secret": e.get("AVITO_CLIENT_SECRET", ""),
    }).encode()
    tok = json.loads(urllib.request.urlopen(
        urllib.request.Request("https://api.avito.ru/token", data=data), timeout=30).read())["access_token"]
    out, page = {}, 1
    while page <= 30:
        req = urllib.request.Request(
            f"https://api.avito.ru/core/v1/items?per_page=100&page={page}",
            headers={"Authorization": "Bearer " + tok})
        res = json.loads(urllib.request.urlopen(req, timeout=30).read()).get("resources") or []
        if not res:
            break
        for x in res:
            out[str(x["id"])] = {
                "url": x.get("url") or "",
                "address": x.get("address") or "",
                "status": x.get("status") or "",
                "price": (x.get("price") or {}).get("value") if isinstance(x.get("price"), dict) else x.get("price"),
            }
        if len(res) < 100:
            break
        page += 1
    return out


def html_to_text(raw):
    """HTML описания -> текст с абзацами (как в приложении)."""
    if not raw:
        return ""
    s = raw.replace("\r", "")
    s = re.sub(r"(?i)</p>\s*<p[^>]*>", "\n\n", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</?(p|div|ul|ol|li|strong|b|em|i|span)[^>]*>", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()[:DESC_LIMIT]


def photo_urls(raw):
    """imageSlug из выгрузки -> публичные https-ссылки CDN Авито."""
    if not raw:
        return []
    slugs = re.findall(r"/image/[^|\s,;]+", raw)
    return ["https://00.img.avito.st" + s for s in slugs][:10]


def read_xlsx(path):
    z = zipfile.ZipFile(path)
    shared = ["".join(t.text or "" for t in si.iter(M + "t"))
              for si in ET.fromstring(z.read("xl/sharedStrings.xml"))]
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    sheets = [(s.get("name"), rels.get(s.get(RNS + "id"))) for s in wb.iter(M + "sheet")]

    rows_out = []
    for name, tgt in sheets:
        if "Спр-" in name or name == "Инструкция":
            continue
        root = ET.fromstring(z.read("xl/" + tgt))
        header, data = None, []
        for row in root.iter(M + "row"):
            num = int(row.get("r"))
            vals = {}
            for c in row.iter(M + "c"):
                col = re.match(r"([A-Z]+)", c.get("r")).group(1)
                t = c.get("t")
                v = c.find(M + "v")
                if t == "s" and v is not None:
                    val = shared[int(v.text)]
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(M + "t"))
                elif v is not None:
                    val = v.text
                else:
                    val = ""
                if val:
                    vals[col] = val
            if num == 2:
                header = vals
                continue
            if num < 5 or not header:
                continue
            if not vals.get("A"):
                continue
            rec = {header.get(k, k): vv for k, vv in vals.items()}
            rec["_sheet"] = name
            data.append(rec)
        rows_out.extend(data)
    return rows_out


def main(path):
    ads = read_xlsx(path)
    api = api_items()
    print(f"в выгрузке: {len(ads)} объявлений, в API: {len(api)}")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    existing = set()
    for (link,) in conn.execute("select avito_link from items where coalesce(avito_link,'')<>''"):
        m = re.search(r"_(\d{6,})$", link or "") or re.search(r"/(\d{6,})/?$", link or "")
        if m:
            existing.add(m.group(1))
    for (art,) in conn.execute("select article from items where coalesce(article,'')<>''"):
        existing.add(str(art).strip())

    created = skipped = 0
    no_photo = no_price = 0
    per_cat = {}
    for a in ads:
        avito_id = (a.get("Номер объявления на Авито") or "").strip()
        if not avito_id:
            continue
        if avito_id in existing:
            skipped += 1
            continue

        api_rec = api.get(avito_id, {})
        address = (a.get("Адрес") or api_rec.get("address") or "").strip()
        city = address.split(",")[0].strip() if address else ""
        goods_type = (a.get("Вид одежды") or a.get("Вид велосипеда") or "").strip()
        apparel = (a.get("Тип товара") or "").strip()
        apparel_type = (a.get("Подвид товара") or a.get("Вид одежды, обуви, аксессуаров")
                        or a.get("Вид товара") or a.get("Подтип товара") or "").strip()
        app_category = CATEGORY_MAP.get(goods_type, "Прочее")

        photos = photo_urls(a.get("Ссылки на фото"))
        try:
            price = float(str(a.get("Цена") or api_rec.get("price") or 0).replace(" ", "").replace("\xa0", ""))
        except Exception:
            price = 0
        if not photos:
            no_photo += 1
        if price <= 0:
            no_price += 1

        known = {"Уникальный идентификатор объявления", "Способ размещения", "Номер объявления на Авито",
                 "Номер телефона", "Адрес", "Ссылки на фото", "Способ связи", "Название объявления",
                 "Описание объявления", "Категория", "Цена", "Состояние", "Страна происхождения товара",
                 "Название компании", "Почта", "Вид одежды", "Бренд одежды", "Тип товара", "Цвет",
                 "Размер", "Сезон", "Материал основной части", "Подвид товара",
                 "Вид одежды, обуви, аксессуаров", "Материал", "Вид объявления", "Вид товара",
                 "Подтип товара", "Вид велосипеда", "AvitoStatus", "AvitoDateEnd"}
        extra = {k: v for k, v in a.items() if k not in known and not k.startswith("_")}

        av_status = (a.get("AvitoStatus") or "").strip()
        status = "На Avito" if av_status.lower() in ("активно", "active") else "Архив"

        conn.execute("""insert into items
            (created_at, updated_at, warehouse, article, category, subcategory, brand, size, condition,
             purchase_price, sale_price, status, photos, tech_photos, description, avito_link, note,
             delivery_eta, created_by, attrs, title, sold_at, supplier, avito_status, avito_finish,
             color, material, season, country, city, avito_goods_type, avito_apparel,
             avito_apparel_type, address, avito_params)
            values (datetime('now'), datetime('now'), 'base', ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, '', ?, ?, '',
                    '', 'import', '{}', ?, '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (avito_id, app_category, apparel or apparel_type, (a.get("Бренд одежды") or "").strip(),
             (a.get("Размер") or "").strip(), (a.get("Состояние") or "").strip() or "Хорошее",
             price, status, ",".join(photos), html_to_text(a.get("Описание объявления")),
             api_rec.get("url") or f"https://www.avito.ru/{avito_id}",
             (a.get("Название объявления") or "").strip(),
             "active" if status == "На Avito" else "archived",
             (a.get("AvitoDateEnd") or "").strip(),
             (a.get("Цвет") or "").strip(),
             (a.get("Материал основной части") or a.get("Материал") or "").strip(),
             (a.get("Сезон") or "").strip(),
             (a.get("Страна происхождения товара") or "").strip(),
             city, goods_type, apparel, apparel_type, address,
             json.dumps(extra, ensure_ascii=False)))
        created += 1
        per_cat[app_category] = per_cat.get(app_category, 0) + 1

    conn.commit()
    total = conn.execute("select count(*) from items").fetchone()[0]
    conn.close()
    print(f"создано: {created}, пропущено (уже есть): {skipped}, всего в базе: {total}")
    print(f"без фото: {no_photo}, без цены: {no_price}")
    print("по категориям приложения:", dict(sorted(per_cat.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/export.xlsx")
