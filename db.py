#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Локальная БД SQLite."""

import os, sqlite3, logging
from typing import Optional
from config import config, BASE_PATH

logger = logging.getLogger(__name__)

WAREHOUSES = [
    {"id": "base", "name": "База", "icon": "🏭"},
    {"id": "apartment", "name": "Квартира", "icon": "🏠"},
    {"id": "brother", "name": "Брат", "icon": "👤"},
    {"id": "coffee", "name": "Кофейня", "icon": "☕"},
]


class Database:
    def __init__(self):
        self.db_path = os.path.join(BASE_PATH, "data.db")
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'worker',
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    updated_at TEXT DEFAULT (datetime('now','localtime')),
                    warehouse TEXT NOT NULL DEFAULT 'base',
                    article TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT 'Другое',
                    brand TEXT NOT NULL DEFAULT 'Неизвестный бренд',
                    size TEXT NOT NULL DEFAULT 'Не указан',
                    condition TEXT NOT NULL DEFAULT 'Хорошее',
                    purchase_price REAL NOT NULL DEFAULT 0,
                    sale_price REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'В закупке',
                    photos TEXT NOT NULL DEFAULT '',
                    tech_photos TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    avito_link TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    delivery_eta TEXT NOT NULL DEFAULT ''
                );
            """)
            # Проверка и доавление новых колонок
            existing = set()
            cur = conn.execute("PRAGMA table_info(items)")
            for r in cur.fetchall():
                existing.add(r["name"])
            if "warehouse" not in existing:
                conn.execute("ALTER TABLE items ADD COLUMN warehouse TEXT NOT NULL DEFAULT 'base'")
            if "article" not in existing:
                conn.execute("ALTER TABLE items ADD COLUMN article TEXT NOT NULL DEFAULT ''")
            if "created_by" not in existing:
                conn.execute("ALTER TABLE items ADD COLUMN created_by TEXT NOT NULL DEFAULT ''")
            if "photos" not in existing:
                conn.execute("ALTER TABLE items ADD COLUMN photos TEXT NOT NULL DEFAULT ''")
            if "tech_photos" not in existing:
                conn.execute("ALTER TABLE items ADD COLUMN tech_photos TEXT NOT NULL DEFAULT ''")
            if "subcategory" not in existing:
                conn.execute("ALTER TABLE items ADD COLUMN subcategory TEXT NOT NULL DEFAULT ''")
            if "attrs" not in existing:
                conn.execute("ALTER TABLE items ADD COLUMN attrs TEXT NOT NULL DEFAULT ''")
            if "title" not in existing:
                conn.execute("ALTER TABLE items ADD COLUMN title TEXT NOT NULL DEFAULT ''")
            if "delivery_eta" not in existing:
                conn.execute("ALTER TABLE items ADD COLUMN delivery_eta TEXT NOT NULL DEFAULT ''")
            if "sold_at" not in existing:
                conn.execute("ALTER TABLE items ADD COLUMN sold_at TEXT NOT NULL DEFAULT ''")
                # для уже проданных товаров датой продажи считаем updated_at
                conn.execute("UPDATE items SET sold_at=updated_at WHERE status='Продано' AND sold_at=''")
            conn.commit()
        except Exception as e:
            logger.error(f"DB init: {e}")
            raise
        finally:
            conn.close()

    def add_item(self, warehouse="base", article="", category="Другое",
                 subcategory="", attrs=None, brand="",
                 size="", condition="Хорошее", purchase_price=0, sale_price=0,
                 photos=None, tech_photos=None, description="", note="", title="",
                 created_by="", supplier=""):
        if photos is None:
            photos = []
        if tech_photos is None:
            tech_photos = []
        conn = self._get_conn()
        try:
            if attrs is None:
                attrs = {}
            if isinstance(attrs, dict):
                import json as _json
                attrs_json = _json.dumps(attrs, ensure_ascii=False)
            else:
                attrs_json = str(attrs)
            cur = conn.execute(
                """INSERT INTO items (warehouse,article,category,subcategory,brand,size,condition,
                   purchase_price,sale_price,photos,tech_photos,description,note,status,attrs,title,created_by,supplier)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'В закупке',?,?,?,?)""",
                (warehouse, article, category, subcategory, brand, size, condition,
                 purchase_price, sale_price, ",".join(photos), ",".join(tech_photos),
                 description, note, attrs_json, title, created_by, supplier))
            conn.commit()
            return cur.lastrowid
        except Exception as e:
            logger.error(f"add_item: {e}")
            raise
        finally:
            conn.close()

    def update_item(self, item_id, **kw):
        allowed = {"warehouse","article","category","subcategory","brand","size","condition",
                    "purchase_price","sale_price","description","avito_link","note","photos","tech_photos","attrs","title","supplier"}
        up = {k: v for k, v in kw.items() if k in allowed}
        if "attrs" in up and isinstance(up["attrs"], dict):
            import json as _j
            up["attrs"] = _j.dumps(up["attrs"], ensure_ascii=False)
        if not up:
            return False
        conn = self._get_conn()
        try:
            clause = ", ".join(f"{k}=?" for k in up)
            conn.execute(f"UPDATE items SET {clause}, updated_at=datetime('now','localtime') WHERE id=?",
                         (*up.values(), item_id))
            conn.commit()
            return conn.total_changes > 0
        except Exception as e:
            logger.error(f"update_item: {e}")
            return False
        finally:
            conn.close()

    def update_status(self, item_id, status, sale_price=None, avito_link=None, delivery_eta=None):
        conn = self._get_conn()
        try:
            sets = ["status=?", "updated_at=datetime('now','localtime')"]
            vals = [status]
            if status == 'Продано':
                sets.append("sold_at=datetime('now','localtime')")
            else:
                sets.append("sold_at=''")
            if sale_price is not None:
                sets.append("sale_price=?")
                vals.append(sale_price)
            if avito_link is not None:
                sets.append("avito_link=?")
                vals.append(avito_link)
            if delivery_eta is not None:
                sets.append("delivery_eta=?")
                vals.append(delivery_eta)
            vals.append(item_id)
            conn.execute("UPDATE items SET " + ", ".join(sets) + " WHERE id=?", vals)
            conn.commit()
            return conn.total_changes > 0
        except Exception as e:
            logger.error(f"update_status: {e}")
            return False
        finally:
            conn.close()

    def update_avito_link(self, item_id, link):
        conn = self._get_conn()
        try:
            conn.execute("UPDATE items SET avito_link=?, status='На Avito', updated_at=datetime('now','localtime') WHERE id=?", (link, item_id))
            conn.commit()
            return conn.total_changes > 0
        except Exception as e:
            return False
        finally:
            conn.close()

    def update_description(self, item_id, desc):
        conn = self._get_conn()
        try:
            conn.execute("UPDATE items SET description=?, updated_at=datetime('now','localtime') WHERE id=?", (desc, item_id))
            conn.commit()
            return conn.total_changes > 0
        except Exception as e:
            return False
        finally:
            conn.close()

    def get_user(self, username):
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def add_user(self, username, password_hash, role='worker'):
        conn = self._get_conn()
        try:
            conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
                         (username, password_hash, role))
            conn.commit()
        finally:
            conn.close()

    def get_items_by_warehouse(self, warehouse, article="", category="", status=None, q=""):
        conn = self._get_conn()
        try:
            conds = []
            vals = []
            if warehouse and warehouse != 'all':
                conds.append("warehouse=?")
                vals.append(warehouse)
            if q:
                conds.append("(title LIKE ? OR brand LIKE ?)")
                vals.append(f"%{q}%")
                vals.append(f"%{q}%")
            if article:
                conds.append("article LIKE ?")
                vals.append(f"%{article}%")
            if category:
                conds.append("category=?")
                vals.append(category)
            if status:
                if status == 'Другое':
                    conds.append("status NOT IN ('В закупке','В наличии','На Avito','В доставке','Продано')")
                else:
                    conds.append("status=?")
                    vals.append(status)
            q = "SELECT * FROM items"
            if conds:
                q += " WHERE " + " AND ".join(conds)
            q += " ORDER BY id DESC"
            cur = conn.execute(q, vals)
            return [self._row_to_dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"get_items: {e}")
            return []
        finally:
            conn.close()

    def get_all_items(self):
        conn = self._get_conn()
        try:
            cur = conn.execute("SELECT * FROM items ORDER BY id DESC")
            return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            return []
        finally:
            conn.close()

    def _row_to_dict(self, row):
        d = dict(row)
        if "attrs" in d and isinstance(d.get("attrs"), str) and d["attrs"]:
            import json as _j
            try:
                d["attrs"] = _j.loads(d["attrs"])
            except:
                pass
        if "subcategory" not in d:
            d["subcategory"] = ""
        return d

    def get_item_by_id(self, item_id):
        conn = self._get_conn()
        try:
            cur = conn.execute("SELECT * FROM items WHERE id=?", (item_id,))
            r = cur.fetchone()
            return self._row_to_dict(r) if r else None
        except Exception as e:
            return None
        finally:
            conn.close()

    def _in_period(self, sold_at, period, date_from, date_to):
        """Проверяет, попадает ли дата продажи в выбранный период."""
        if not sold_at:
            return False
        import datetime
        day = sold_at[:10]  # YYYY-MM-DD
        today = datetime.date.today()
        try:
            if period == "7d":
                return day >= (today - datetime.timedelta(days=7)).isoformat()
            if period == "30d":
                return day >= (today - datetime.timedelta(days=30)).isoformat()
            if period == "month":
                return day >= today.replace(day=1).isoformat()
            if period == "custom" and (date_from or date_to):
                if date_from and day < date_from[:10]:
                    return False
                if date_to and day > date_to[:10]:
                    return False
                return True
        except Exception:
            return True  # при ошибке даты не фильтруем
        return True  # all

    def get_statistics(self, warehouse="", period="all", date_from="", date_to="", created_by=""):
        conn = self._get_conn()
        try:
            s = {"total": 0, "in_stock": 0, "in_work": 0, "on_avito": 0, "in_delivery": 0,
                 "sold": 0, "written_off": 0,
                 "total_purchase": 0.0, "total_sale": 0.0,
                 "sold_revenue": 0.0, "sold_purchase": 0.0, "sold_count": 0,
                 "invested": 0.0, "potential_revenue": 0.0,
                 "by_category": {}, "by_month": {}, "by_author": {}, "authors_added": 0}
            q = "SELECT * FROM items"
            p = ()
            if warehouse:
                q += " WHERE warehouse=?"
                p = (warehouse,)
            if created_by:
                q += " AND created_by=?" if warehouse else " WHERE created_by=?"
                p += (created_by,)
            for r in conn.execute(q, p).fetchall():
                s["total"] += 1
                st = r["status"] or ""
                if st == "В наличии": s["in_stock"] += 1
                elif st == "В закупке": s["in_work"] += 1
                elif st == "На Avito": s["on_avito"] += 1
                elif st == "В доставке": s["in_delivery"] += 1
                elif st == "Продано": s["sold"] += 1
                elif st == "Списано": s["written_off"] += 1

                purchase = r["purchase_price"] or 0
                sale = r["sale_price"] or 0
                s["total_purchase"] += purchase
                s["total_sale"] += sale

                # категория: количество + выручка
                cat = r["category"] or "Другое"
                c = s["by_category"].setdefault(cat, {"count": 0, "revenue": 0.0, "sold": 0})
                c["count"] += 1
                if st == "Продано":
                    if self._in_period(r["sold_at"], period, date_from, date_to):
                        s["sold_revenue"] += sale
                        s["sold_purchase"] += purchase
                        s["sold_count"] += 1
                        c["sold"] += 1
                        c["revenue"] += sale
                        # продажи по месяцам (YYYY-MM)
                        sm = (r["sold_at"] or "")[:7]
                        if sm:
                            m = s["by_month"].setdefault(sm, {"count": 0, "revenue": 0.0})
                            m["count"] += 1
                            m["revenue"] += sale

                # заморожено в товарах (закупка всего, что не продано и не списано)
                if st not in ("Продано", "Списано"):
                    s["invested"] += purchase
                    if st in ("В наличии", "На Avito", "В доставке"):
                        s["potential_revenue"] += sale

                # автор: сколько товаров добавил за выбранный период
                if self._in_period(r["created_at"], period, date_from, date_to):
                    a = r["created_by"].strip() or "Без автора"
                    s["by_author"][a] = s["by_author"].get(a, 0) + 1
                    s["authors_added"] += 1

            # средний чек
            s["avg_check"] = round(s["sold_revenue"] / s["sold_count"], 0) if s["sold_count"] else 0
            # фактическая прибыль до вычетов
            s["sold_profit"] = s["sold_revenue"] - s["sold_purchase"]
            return s
        except Exception as e:
            logger.error(f"get_statistics: {e}")
            return s
        finally:
            conn.close()
    def get_warehouse_name(self, wid):
        for w in WAREHOUSES:
            if w["id"] == wid:
                return f"{w['icon']} {w['name']}"
        return wid

    def add_photos(self, item_id, new_photos):
        """Добавить фото к существующему товару (до 10 шт)."""
        if not new_photos:
            return False
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT photos FROM items WHERE id=?", (item_id,)).fetchone()
            if not row:
                return False
            current = [p.strip() for p in row["photos"].split(",") if p.strip()] if row["photos"] else []
            current = current + new_photos
            current = current[:10]
            conn.execute("UPDATE items SET photos=? WHERE id=?", (",".join(current), item_id))
            conn.commit()
            return True
        finally:
            conn.close()

    def delete_photo(self, item_id, idx):
        """Удалить фото по индексу из списка photos товара."""
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT photos FROM items WHERE id=?", (item_id,)).fetchone()
            if not row or not row["photos"]:
                return False
            photos = [p.strip() for p in row["photos"].split(",") if p.strip()]
            if idx < 0 or idx >= len(photos):
                return False
            photos.pop(idx)
            conn.execute("UPDATE items SET photos=? WHERE id=?", (",".join(photos), item_id))
            conn.commit()
            return True
        finally:
            conn.close()


    def delete_item_row(self, item_id):
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM items WHERE id=?", (item_id,))
            conn.commit()
            return conn.total_changes > 0
        except Exception as e:
            print(f"db delete_item_row error: {e}")
            return False
    def set_main_photo(self, item_id, idx):
        """Сделать фото с индексом idx главным (переместить в начало списка photos)"""
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT photos FROM items WHERE id=?", (item_id,)).fetchone()
            if not row or not row["photos"]:
                return False
            photos = [p.strip() for p in row["photos"].split(",") if p.strip()]
            if idx < 0 or idx >= len(photos):
                return False
            photo = photos.pop(idx)
            photos.insert(0, photo)
            conn.execute("UPDATE items SET photos=? WHERE id=?", (",".join(photos), item_id))
            conn.commit()
            return True
        finally:
            conn.close()


db = Database()