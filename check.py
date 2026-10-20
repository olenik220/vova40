# -*- coding: utf-8 -*-
import sys; sys.path.insert(0, '.')
from config import config; print('config OK')
from db import db, WAREHOUSES; print('db OK, склады:', [w['name'] for w in WAREHOUSES])
from ai_helper import description_gen; print('ai OK:', config.ai_label)
items = db.get_all_items(); print(f'товаров в БД: {len(items)}')
for i in items[:2]:
    print(f'  #{i["id"]} {i["brand"]} wh={i.get("warehouse","?")} art={i.get("article","")} photos={i.get("photos","")[:30]}')
import app; print('app OK')
print('ВСЁ ПРОВЕРЕНО ✅')