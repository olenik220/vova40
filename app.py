#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Vova40 — учёт секонд-хенда. Локальный сервер.
"""

import os, sys, uuid, logging, socket, re, re
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import urllib.parse, urllib.request, ssl
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))
sys.path.insert(0, BASE_DIR)

from config import config
from db import db, WAREHOUSES
from ai_helper import description_gen
from sheets import sheets_sync
from PIL import Image, ImageOps
try:
    import ftplib
except ImportError:
    ftplib = None

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24).hex())
app.config['MAX_CONTENT_LENGTH'] = 300 * 1024 * 1024
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False  # True если будет HTTPS

# ---- Security headers ----
@app.after_request
def add_security_headers(resp):
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options'] = 'DENY'
    resp.headers['X-XSS-Protection'] = '1; mode=block'
    resp.headers['Referrer-Policy'] = 'same-origin'
    # CSP: запрещаем инлайн-скрипты без nonce (пока разрешены 'unsafe-inline' для совместимости)
    resp.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'"
    )
    return resp

# ---- Rate limiter (in-memory) ----
from collections import defaultdict
import time
import threading
_login_attempts = defaultdict(list)
LOGIN_RATE_LIMIT = 5          # макс попыток
LOGIN_RATE_WINDOW = 300       # за 5 минут

def is_login_rate_limited(ip):
    now = time.time()
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < LOGIN_RATE_WINDOW]
    if len(_login_attempts[ip]) >= LOGIN_RATE_LIMIT:
        return True
    _login_attempts[ip].append(now)
    return False

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
for w in WAREHOUSES:
    os.makedirs(os.path.join(UPLOAD_FOLDER, w['id']), exist_ok=True)


# ---- Блокировка доступа к .env и БД через static ----
@app.route('/static/uploads/../<path:bad_path>')
@app.route('/static/../<path:bad_path>')
def block_path_traversal(bad_path):
    return 'Forbidden', 403

@app.route('/.env')
@app.route('/data.db')
def block_sensitive():
    return 'Forbidden', 403

# ---- Защита от path traversal в uploads ----
import werkzeug.exceptions
@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('500.html'), 500

@app.context_processor
def inject_back_url():
    """Добавляет в шаблоны back_url из request.referrer (если referrer с того же домена)."""
    ref = request.referrer or ''
    if ref:
        from urllib.parse import urlparse
        ref_host = urlparse(ref).netloc
        cur_host = urlparse(request.url).netloc
        if ref_host == cur_host and ref != request.url:
            return {'back_url': ref}
    return {'back_url': ''}




def ensure_default_users():
    """Создаёт пользователей при первом запуске. Админ — это WEB_PASSWORD из .env
    (вход с пустым именем или «admin»), отдельно добавляем сотрудника Max."""
    # миграция: раньше сотрудник назывался «Мах»
    legacy = db.get_user('Мах')
    if legacy:
        conn = sqlite3.connect(db.db_path)
        try:
            conn.execute("UPDATE users SET username='Max' WHERE username='Мах'")
            conn.commit()
        finally:
            conn.close()
    if not db.get_user('Max'):
        db.add_user('Max', generate_password_hash('max13'), role='worker')


ensure_default_users()


def save_uploaded_photo(f, dest_dir):
    """Сохраняет загруженное фото с обработкой под требования Avito:
    - любой формат (JPEG/PNG/GIF/WebP/HEIC/BMP) конвертируется в JPG;
    - применяется EXIF-ориентация (иначе фото с телефона повёрнуты);
    - PNG с прозрачностью кладётся на белый фон;
    - длинная сторона уменьшается до 1920 px (Avito: не больше 1920x1080,
      фото выше рекомендуемого разрешения сжимаются сильнее);
    - качество JPEG 85.
    Возвращает (path, url) или (None, None), если файл не передан.
    При ошибке чтения/обработки сохраняет файл как есть (fallback).
    """
    if not f or not f.filename:
        return None, None
    try:
        img = Image.open(f)
        img = ImageOps.exif_transpose(img)
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGBA')
            bg = Image.new('RGB', img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        w, h = img.size
        max_side = 1920
        if max(w, h) > max_side:
            r = max_side / float(max(w, h))
            img = img.resize((int(round(w * r)), int(round(h * r))), Image.LANCZOS)
        name = uuid.uuid4().hex + '.jpg'
        path = os.path.join(dest_dir, name)
        os.makedirs(dest_dir, exist_ok=True)
        img.save(path, 'JPEG', quality=85, optimize=True, progressive=True)
    except Exception:
        # не картинка или повреждённый файл — сохраняем оригинал как есть
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else 'jpg'
        if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'):
            ext = 'jpg'
        name = uuid.uuid4().hex + '.' + ext
        path = os.path.join(dest_dir, name)
        os.makedirs(dest_dir, exist_ok=True)
        f.save(path)
    url = '/static/uploads/' + os.path.relpath(path, UPLOAD_FOLDER).replace('\\', '/')
    if config.AVITO_FTP_READY:
        # заливаем на FTP-сервер, чтобы фото было доступно по публичному URL
        rel = os.path.relpath(path, UPLOAD_FOLDER).replace('\\', '/')
        ftp_upload_file(path, rel)
    return path, url

def delete_unused_photo_files(paths):
    """Удаляет с диска файлы фото, которые больше не используются ни одним
    товаром в БД (проверка по всем items.photos). Возвращает число удалённых.
    Файлы, на которые ссылается хотя бы один товар, НЕ трогаются."""
    if not paths:
        return 0
    deleted = 0
    try:
        import sqlite3 as _sq
        con = _sq.connect(os.path.join(BASE_DIR, 'data.db'))
        used = set()
        for (iid, ph, tph) in con.execute("SELECT id, photos, tech_photos FROM items"):
            for csv_str in (ph, tph):
                if csv_str:
                    for p in csv_str.split(','):
                        p = p.strip()
                        if p:
                            used.add(p)
        con.close()
        static_root = os.path.normpath(os.path.join(BASE_DIR, 'static'))
        for url in paths:
            if not url or not url.startswith('/static/uploads/'):
                continue
            if url in used:            # ещё используется каким-то товаром
                continue
            rel = url[len('/static/'):].replace('/', os.sep)
            fp = os.path.normpath(os.path.join(BASE_DIR, 'static', rel))
            # защита от выхода за пределы static/
            if os.path.commonpath([static_root, fp]) != static_root:
                continue
            if not os.path.isfile(fp):
                continue
            try:
                os.remove(fp)
                deleted += 1
            except OSError as e:
                logging.warning('Не удалось удалить фото %s: %s', fp, e)
    except Exception as e:
        logging.error('delete_unused_photo_files: %s', e)
    return deleted


WEB_PASSWORD = os.getenv('WEB_PASSWORD', '')
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

def login_required(f):
    @wraps(f)
    def d(*a, **kw):
        if WEB_PASSWORD and not session.get('user'):
            # совместимость со старыми сессиями (один пароль)
            if session.get('logged_in'):
                session['user'] = {'username': 'admin', 'role': 'admin'}
            else:
                return redirect(url_for('login'))
        return f(*a, **kw)
    return d


def admin_required(f):
    @wraps(f)
    def d(*a, **kw):
        user = session.get('user') or {}
        if user.get('role') != 'admin':
            flash('Нет доступа: действие доступно только администратору', 'danger')
            return redirect(url_for('warehouse_choice'))
        return f(*a, **kw)
    return d


def editable_required(f):
    """Админ может всё; сотрудник может править только свои товары
    (created_by == его логин) и только пока статус «В закупке»."""
    @wraps(f)
    def d(item_id, *a, **kw):
        user = session.get('user') or {}
        if user.get('role') == 'admin':
            return f(item_id, *a, **kw)
        item = db.get_item_by_id(item_id)
        username = user.get('username', '')
        if (item and item.get('created_by') == username
                and item.get('status') == 'В закупке'):
            return f(item_id, *a, **kw)
        flash('Править можно только свои товары в статусе «В закупке»', 'danger')
        return redirect(url_for('warehouse_choice'))
    return d


@app.context_processor
def inject_current_user():
    user = session.get('user') or {}
    return {'current_user': user, 'is_admin': user.get('role') == 'admin'}

@app.route('/login', methods=['GET', 'POST'])
def login():
    if not WEB_PASSWORD:
        return redirect(url_for('warehouse_choice'))
    if request.method == 'POST':
        # Rate limit по IP
        ip = request.remote_addr or request.headers.get('X-Forwarded-For', 'unknown').split(',')[0].strip()
        if is_login_rate_limited(ip):
            flash(f'Слишком много попыток. Подождите {LOGIN_RATE_WINDOW//60} минуты.', 'danger')
            return render_template('login.html')
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = None
        if username in ('', 'admin'):
            # администратор: имя пустое или «admin» + пароль из .env
            if password == WEB_PASSWORD:
                user = {'username': 'admin', 'role': 'admin'}
        else:
            u = db.get_user(username)
            if u and check_password_hash(u['password_hash'], password):
                user = {'username': u['username'], 'role': u['role']}
        if user:
            session['user'] = user
            return redirect(url_for('warehouse_choice'))
        flash('Неверный логин или пароль', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    session.pop('logged_in', None)
    return redirect(url_for('login'))

# ───── ВЫБОР СКЛАДА ─────
@app.route('/')
@login_required
def warehouse_choice():
    return render_template('warehouses.html', warehouses=WAREHOUSES)


@app.route('/search')
@login_required
def search():
    """Поиск товаров по артикулу или названию/бренду на всех складах сразу."""
    article = request.args.get('article', '').strip()
    q = request.args.get('q', '').strip()
    items = db.get_items_by_warehouse('all', article, q=q) if (article or q) else []
    wh_names = {w['id']: w['name'] for w in WAREHOUSES}
    return render_template('search_results.html',
        items=items, article=article, q=q, wh_names=wh_names)

# ───── ТОВАРЫ ИЗ СКЛАДА ─────
@app.route('/warehouse/<wh_id>')
@login_required
def warehouse_categories(wh_id):
    """Выбор категории склада — только те, где есть товары"""
    items = db.get_items_by_warehouse(wh_id)
    counter = {}
    for it in items:
        cat = (it.get('category') or '').strip()
        if cat:
            counter[cat] = counter.get(cat, 0) + 1
    avito = config.AVITO_CATEGORIES
    cats = []
    for cat, cnt in sorted(counter.items(), key=lambda x: -x[1]):
        icon = None
        if cat in avito:
            icon = avito[cat].get('icon')
        else:
            for k, v in avito.items():
                if cat in v.get('subcategories', []):
                    icon = v.get('icon')
                    break
        cats.append({'name': cat, 'count': cnt, 'icon': icon or '\U0001F4E6'})
    wh_name = db.get_warehouse_name(wh_id)
    return render_template('warehouse_categories.html',
        wh_id=wh_id, wh_name=wh_name, cats=cats)

@app.route('/warehouse/<wh_id>/items')
@login_required
def warehouse_items(wh_id):
    """Список товаров на складе, опционально с фильтром по категории и статусу"""
    article = request.args.get('article', '').strip()
    category = request.args.get('category', '').strip()
    status = request.args.get('status', '').strip()
    q = request.args.get('q', '').strip()
    items = db.get_items_by_warehouse(wh_id, article, category, status or None, q=q)
    wh_name = 'Все склады' if wh_id == 'all' else db.get_warehouse_name(wh_id)
    return render_template('warehouse_items.html',
        items=items, wh_id=wh_id, wh_name=wh_name,
        article=article, category=category, status=status, q=q)

def ftp_upload_file(local_path, remote_rel_path):
    """Заливает файл на FTP-сервер. remote_rel_path — относительно AVITO_FTP_PATH.
    Возвращает True при успехе. Поддерживает FTPS (TLS) и обычный FTP."""
    if ftplib is None or not config.AVITO_FTP_READY:
        return False
    try:
        if config.AVITO_FTP_TLS:
            ftp = ftplib.FTP_TLS()
        else:
            ftp = ftplib.FTP()
        ftp.connect(config.AVITO_FTP_HOST, config.AVITO_FTP_PORT, timeout=30)
        ftp.login(config.AVITO_FTP_USER, config.AVITO_FTP_PASS)
        if config.AVITO_FTP_TLS:
            ftp.prot_p()
        # уходим в нужную папку, создавая уровни при необходимости
        parts = []
        if config.AVITO_FTP_PATH:
            parts += config.AVITO_FTP_PATH.split('/')
        parts += remote_rel_path.split('/')[:-1]
        cur = ''
        for part in parts:
            if not part:
                continue
            cur += '/' + part
            try:
                ftp.cwd(cur)
            except Exception:
                try:
                    ftp.mkd(cur)
                    ftp.cwd(cur)
                except Exception:
                    pass
        with open(local_path, 'rb') as fh:
            ftp.storbinary(f'STOR {remote_rel_path.split("/")[-1]}', fh)
        ftp.quit()
        return True
    except Exception as e:
        print(f'[ftp] ошибка заливки {remote_rel_path}: {e}')
        return False


def photo_remote_url(photo_path):
    """Публичный URL фото: если задан AVITO_PUBLIC_URL — https://домен/static/...,
    иначе просто имя файла (локальный режим)."""
    p = (photo_path or '').strip()
    if not p:
        return ''
    if config.AVITO_PUBLIC_URL and p.startswith('/'):
        return config.AVITO_PUBLIC_URL + p
    return os.path.basename(p)


def photo_url(photo_path):
    """Полный URL фото: если задан AVITO_PUBLIC_URL — https://домен/static/...,
    иначе просто имя файла (локальный режим)."""
    p = (photo_path or '').strip()
    if not p:
        return ''
    if config.AVITO_PUBLIC_URL and p.startswith('/'):
        return config.AVITO_PUBLIC_URL + p
    return os.path.basename(p)


@app.route('/item/<int:item_id>/export-avito')
@login_required
@admin_required
def export_avito_item(item_id):
    """Залить товар на Avito через API: обновить цену / автозагрузка."""
    item = db.get_item_by_id(item_id)
    if not item:
        flash('Товар не найден', 'danger')
        return redirect(url_for('warehouse_choice'))

    avito_link = (item.get('avito_link') or '').strip()
    sale_price = item.get('sale_price') or 0
    m = re.search(r'_(\d+)$', avito_link)
    avito_id = m.group(1) if m else None

    # --- 1. Если товар уже на Avito — обновляем цену через API ---
    if avito_id and sale_price > 0:
        tok = avito_get_token()
        if not tok:
            flash('Нет токена Avito API — проверьте настройки', 'danger')
            return redirect(url_for('avito_setup'))
        try:
            body = json.dumps({"price": int(sale_price)}).encode()
            req = urllib.request.Request(
                f'https://api.avito.ru/core/v1/items/{avito_id}/update_price',
                data=body,
                headers={
                    'Authorization': f'Bearer {tok}',
                    'Content-Type': 'application/json',
                },
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
            flash(f'Цена {int(sale_price)} руб обновлена на Avito (id {avito_id})', 'success')
            db.update_status(item_id, 'На Avito', sale_price=sale_price, avito_link=avito_link)
            return redirect(url_for('item_card', item_id=item_id))
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors='replace')[:300]
            flash(f'Ошибка Avito API: {e.code} — {body}', 'danger')
            return redirect(url_for('item_card', item_id=item_id))
        except Exception as e:
            flash(f'Ошибка: {e}', 'danger')
            return redirect(url_for('item_card', item_id=item_id))

    # --- 2. Нет avito_link — формируем XML Автозагрузки ---
    from xml.sax.saxutils import escape as xmlesc
    import os as _os, uuid as _uuid, json as _json

    brand = (item.get('brand') or '').strip() or 'Неизвестный бренд'
    size = (item.get('size') or '').strip()
    if size.lower() in ('', 'не указан'):
        size = ''
    condition = (item.get('condition') or '').strip()
    title = (item.get('title') or '').strip() or f"{brand} {size}".strip()
    desc = (item.get('description') or '').strip()
    photos_str = (item.get('photos') or '').strip()
    photo_urls = []
    for p in photos_str.split(',') if photos_str else []:
        p = p.strip()
        if p:
            purl = photo_remote_url(p)
            if purl:
                photo_urls.append(purl)
    subcat = (item.get('subcategory') or '').strip()

    # --- Атрибуты из attrs (JSON в БД) ---
    raw_attrs = item.get('attrs', {})
    if isinstance(raw_attrs, str):
        try:
            raw_attrs = _json.loads(raw_attrs)
        except Exception:
            raw_attrs = {}
    if not isinstance(raw_attrs, dict):
        raw_attrs = {}

    color = (raw_attrs.get('Цвет') or '').strip()
    material = (raw_attrs.get('Материал', '') or raw_attrs.get('Состав/материал', '') or
                raw_attrs.get('Материал основной части', '') or raw_attrs.get('Материал верха', '') or '').strip()
    season = (raw_attrs.get('Сезон') or '').strip()
    # Остальные атрибуты — как <Attr name="...">...</Attr>
    ignore_keys = {'Цвет', 'Материал', 'Состав/материал', 'Материал основной части', 'Материал верха', 'Сезон',
                   'Бренд', 'Размер', 'Состояние', 'Тип', 'Пол', 'Количество предметов в комплекте', 'Вид спорта'}
    extra_attrs = {}
    for k, v in raw_attrs.items():
        if k not in ignore_keys and v and str(v).strip():
            extra_attrs[k] = str(v).strip()

    cat = item.get('category', '').strip()
    avito_cat = {
        'Женская одежда': 'Одежда, обувь, аксессуары',
        'Мужская одежда': 'Одежда, обувь, аксессуары',
        'Детская одежда': 'Детская одежда',
        'Женская обувь': 'Одежда, обувь, аксессуары',
        'Мужская обувь': 'Одежда, обувь, аксессуары',
        'Аксессуары': 'Одежда, обувь, аксессуары',
    }.get(cat, 'Одежда, обувь, аксессуары')

    cond_map = {
        'Новое с биркой': 'Новое',
        'Новое': 'Новое',
        'Хорошее': 'Хорошее',
        'Удовлетворительное': 'Удовлетворительное',
    }
    xml_cond = cond_map.get(condition, 'Хорошее')

    xml_id = f"local_{item_id}_{_uuid.uuid4().hex[:8]}"
    safe_title = xmlesc(title)
    safe_desc = xmlesc(desc)
    safe_brand = xmlesc(brand)
    safe_size = xmlesc(size) if size else ''
    safe_subcat = xmlesc(subcat)
    safe_color = xmlesc(color) if color else ''
    safe_material = xmlesc(material) if material else ''
    safe_season = xmlesc(season) if season else ''

    xml_parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<Ads formatVersion="3" target="Avito.ru">',
        f'  <Ad>',
        f'    <Id>{xml_id}</Id>',
        f'    <Category>{avito_cat}</Category>',
    ]
    if safe_subcat:
        xml_parts.append(f'    <GoodsType>{safe_subcat}</GoodsType>')
    xml_parts.extend([
        f'    <Title>{safe_title}</Title>',
        f'    <Description>{safe_desc}</Description>',
        f'    <Price>{int(sale_price)}</Price>',
        f'    <Currency>RUR</Currency>',
        f'    <Condition>{xml_cond}</Condition>',
        f'    <Brand>{safe_brand}</Brand>',
    ])
    if safe_size:
        xml_parts.append(f'    <Size>{safe_size}</Size>')
    if safe_color:
        xml_parts.append(f'    <Color>{safe_color}</Color>')
    if safe_material:
        xml_parts.append(f'    <Material>{safe_material}</Material>')
    if safe_season:
        xml_parts.append(f'    <SeasonName>{safe_season}</SeasonName>')
    for _ek, _ev in extra_attrs.items():
        xml_parts.append(f'    <Attr name="{xmlesc(_ek)}">{xmlesc(_ev)}</Attr>')
    if photo_urls:
        xml_parts.append('    <Images>')
        for pu in photo_urls:
            xml_parts.append(f'      <Image url="{xmlesc(pu)}">{xmlesc(pu)}</Image>')
        xml_parts.append('    </Images>')
    xml_parts.append('    <AdStatus>Free</AdStatus>')
    xml_parts.extend([
        f'  </Ad>',
        f'</Ads>',
    ])
    xml_content = '\n'.join(xml_parts)

    feeds_dir = _os.path.join(config.BASE_PATH, 'static', 'avito_feeds')
    _os.makedirs(feeds_dir, exist_ok=True)
    safe_name2 = re.sub(r'[^\w\-]+', '_', title)[:40].strip('_') or str(item_id)
    xml_filename = f"avito_{safe_name2}.xml"
    xml_local = _os.path.join(feeds_dir, xml_filename)
    with open(xml_local, 'w', encoding='utf-8') as f:
        f.write(xml_content)

    if config.AVITO_FTP_READY:
        # 1) Заливаем фото на FTP (чтобы были доступны по публичному URL)
        uploaded_photos = 0
        for _p in photos_str.split(',') if photos_str else []:
            _p = _p.strip()
            if _p:
                _local = _os.path.join(BASE_DIR, _p.lstrip('/'))
                if _os.path.exists(_local):
                    _rel = _os.path.relpath(_local, UPLOAD_FOLDER).replace('\\', '/')
                    if ftp_upload_file(_local, _rel):
                        uploaded_photos += 1
        # 2) Заливаем XML фид
        ftp_ok = ftp_upload_file(xml_local, f"feeds/{xml_filename}")
        if not ftp_ok:
            flash(f'XML сформирован, фото залито {uploaded_photos}, но XML не удалось загрузить на FTP', 'warning')
            return redirect(url_for('item_card', item_id=item_id))
        tok = avito_get_token()
        if tok:
            try:
                req = urllib.request.Request(
                    'https://api.avito.ru/autoload/v1/upload',
                    headers={'Authorization': f'Bearer {tok}'},
                    method='POST',
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    pass
                flash('XML загружен на FTP, автозагрузка запущена!', 'success')
                db.update_status(item_id, 'На Avito', sale_price=sale_price, avito_link=f'https://www.avito.ru/autoload/{xml_id}')
            except Exception as e:
                flash(f'XML на FTP, но автозагрузка не запустилась: {e}', 'warning')
        else:
            flash('XML на FTP, но токен Avito не получен — запустите автозагрузку вручную', 'warning')
        return redirect(url_for('item_card', item_id=item_id))
    else:
        from flask import send_file
        flash('FTP не настроен — скачайте XML и загрузите вручную в Автозагрузку Avito', 'info')
        return send_file(
            xml_local,
            mimetype='application/xml',
            as_attachment=True,
            download_name=xml_filename
        )

@app.route('/sync-photos')
@login_required
@admin_required
def sync_photos():
    """Залить все фото на FTP-сервер (для старых товаров, добавленных до настройки FTP).
    Возвращает отчёт: сколько залито / уже было / ошибок."""
    if not config.AVITO_FTP_READY:
        flash('FTP не настроен: укажите AVITO_FTP_HOST/USER/PASS в .env', 'warning')
        return redirect(url_for('warehouse_choice'))

    items = db.get_all_items()
    uploaded, skipped, failed, missing = [], [], [], []
    for it in items:
        for p in (it.get('photos') or '').split(','):
            p = p.strip()
            if not p:
                continue
            if p.startswith('http'):
                skipped.append(p)  # уже полный URL
                continue
            # /static/uploads/base/xxx.jpg -> локальный файл
            local = os.path.join(BASE_DIR, p.lstrip('/'))
            if not os.path.exists(local):
                missing.append(p)
                continue
            rel = os.path.relpath(local, UPLOAD_FOLDER).replace('\\', '/')
            if ftp_upload_file(local, rel):
                uploaded.append(rel)
            else:
                failed.append(rel)

    msg = (f'Фото на FTP: залито {len(uploaded)}, уже было {len(skipped)}, '
           f'ошибок {len(failed)}, не найдено локально {len(missing)}.')
    flash(msg, 'success' if not failed and not missing else 'warning')
    print('[sync-photos]', msg)
    if failed:
        print('[sync-photos] ошибки:', failed[:10])
    if missing:
        print('[sync-photos] нет локально:', missing[:10])
    return redirect(url_for('warehouse_choice'))


@app.route('/add/<wh_id>', methods=['GET'])
@login_required
def choose_category(wh_id):
    category = request.args.get('category', '').strip()
    subcategory = request.args.get('subcategory', '').strip()
    if category:
        cat_info = config.AVITO_CATEGORIES.get(category)
        if not cat_info:
            flash('Неизвестная категория', 'danger')
            return redirect(url_for('choose_category', wh_id=wh_id))
        # если подкатегория не выбрана — показать сетку подкатегорий
        if not subcategory:
            return render_template('choose_subcategory.html',
                wh_id=wh_id, category=category, icon=cat_info['icon'],
                subcategories=cat_info['subcategories'])
        # категория и подкатегория выбраны — форма добавления
        return render_template('add.html',
            wh_id=wh_id, category=category, subcategory=subcategory,
            attrs=cat_info['attrs'], avatar_cats=config.AVITO_CATEGORIES,
            conditions=config.CONDITIONS)
    return render_template('choose_category.html',
        wh_id=wh_id, wh_name=db.get_warehouse_name(wh_id), avito_cats=config.AVITO_CATEGORIES)

# ───── ДОБАВЛЕНИЕ ТОВАРА ─────
@app.route('/add/<wh_id>', methods=['POST'])
@login_required
def add_item(wh_id):
    if request.method == 'POST':
        category = request.form.get('category', '').strip()
        subcategory = request.form.get('subcategory', '').strip()
        if not category:
            flash('Выбери категорию', 'danger')
            return redirect(url_for('choose_category', wh_id=wh_id))
        title = request.form.get('title', '').strip()
        article = request.form.get('article', '').strip()
        brand = request.form.get('brand', '').strip() or 'Неизвестный бренд'
        size = request.form.get('size', '').strip() or 'Не указан'
        condition = request.form.get('condition', 'Хорошее')
        purchase_price = float(request.form.get('purchase_price', 0) or 0)
        sale_price = float(request.form.get('sale_price', 0) or 0)
        # сотрудник (не админ): закупку не видит и не заполняет, статус всегда «В закупке»
        if (session.get('user') or {}).get('role') != 'admin':
            purchase_price = 0
        note = request.form.get('note', '').strip()
        supplier = request.form.get('supplier', '').strip()
        if supplier == 'Другое':
            supplier = request.form.get('supplier_custom', '').strip() or 'Другое'
        # собираем характеристики (attrs) — атрибуты категории, не вошедшие в отдельные поля
        cat_info = config.AVITO_CATEGORIES.get(category)
        attrs = {}
        if cat_info:
            for idx, aname in enumerate(cat_info['attrs']):
                if aname in ('Состояние', 'Бренд', 'Размер', 'Размер (RU)'):
                    continue  # эти уже сохранены в condition / brand / size
                v = request.form.get(f'av_{idx}', '').strip()
                if v:
                    attrs[aname] = v
        photos = []
        ai_image_bytes = []
        # собираем до 10 файлов: из multiple-поля 'photos' (новое), плюс legacy photo / photo_N
        files = []
        seen_names = set()
        if 'photos' in request.files:
            for f in request.files.getlist('photos'):
                if f and f.filename:
                    files.append(f)
        for i in range(10):
            key = f'photo_{i}'
            if key in request.files and request.files[key].filename:
                files.append(request.files[key])
        if 'photo' in request.files and request.files['photo'].filename:
            files.append(request.files['photo'])
        files = files[:10]
        for f in files:
            path, url = save_uploaded_photo(f, os.path.join(UPLOAD_FOLDER, wh_id))
            if not path:
                continue
            photos.append(url)
            if len(ai_image_bytes) < 3:
                with open(path, 'rb') as img_f:
                    ai_image_bytes.append(img_f.read())
        desc = description_gen.generate_description(
            image_bytes=ai_image_bytes if ai_image_bytes else None, category=category, brand=brand, size=size,
            condition=condition, purchase_price=purchase_price, sale_price=sale_price,
            title=title, supplier=supplier, attrs=attrs, article=article)
        tech_photos = []
        for f in request.files.getlist('tech_photos'):
            if f and f.filename:
                path, url = save_uploaded_photo(f, os.path.join(UPLOAD_FOLDER, wh_id, 'tech'))
                if path:
                    tech_photos.append(url)
        tech_photos = tech_photos[:10]
        item_author = (session.get('user') or {}).get('username', '')
        item_id = db.add_item(
            warehouse=wh_id, article=article, category=category,
            subcategory=subcategory, attrs=attrs, brand=brand,
            size=size, condition=condition, purchase_price=purchase_price,
            sale_price=sale_price, photos=photos, tech_photos=tech_photos,
            description=desc, note=note, title=title, created_by=item_author,
            supplier=supplier)
        flash(f'Товар #{item_id} добавлен!', 'success')
        return redirect(url_for('warehouse_items', wh_id=wh_id))

# ───── КАРТОЧКА ТОВАРА ─────
@app.route('/item/<int:item_id>')
@login_required
def item_card(item_id):
    item = db.get_item_by_id(item_id)
    if not item:
        flash('Товар не найден', 'danger')
        return redirect(url_for('warehouse_choice'))
    wh_name = db.get_warehouse_name(item.get('warehouse', 'base'))
    photos = item.get('photos', '').split(',') if item.get('photos') else []
    user = session.get('user') or {}
    can_edit = (user.get('role') == 'admin'
                or (item.get('created_by') == user.get('username')
                    and item.get('status') == 'В закупке'))
    return render_template('card.html',
        item=item, wh_name=wh_name, photos=photos, can_edit=can_edit,
        statuses=config.STATUSES,
        categories=config.CATEGORIES, conditions=config.CONDITIONS,
        warehouses=WAREHOUSES, avito_cats=config.AVITO_CATEGORIES,
        avito_comm=config.AVITO_COMMISSION, tax_pct=config.TAX_RATE)

# ───── РЕДАКТИРОВАНИЕ ТОВАРА ─────
@app.route('/item/<int:item_id>/edit', methods=['GET', 'POST'])
@login_required
@editable_required
def edit_item(item_id):
    item = db.get_item_by_id(item_id)
    if not item:
        flash('Товар не найден', 'danger')
        return redirect(url_for('warehouse_choice'))

    if request.method == 'GET':
        photos = item.get('photos', '').split(',') if item.get('photos') else []
        wh_name = db.get_warehouse_name(item.get('warehouse', 'base'))
        # парсим сохранённые attrs
        attrs_dict = {}
        raw = item.get('attrs', '')
        if raw:
            import json as _j
            try:
                attrs_dict = _j.loads(raw) if isinstance(raw, str) else raw
            except:
                attrs_dict = {}
        return render_template('edit.html',
            item=item, wh_name=wh_name, photos=photos,
            categories=config.CATEGORIES, conditions=config.CONDITIONS,
            warehouses=WAREHOUSES, avito_cats=config.AVITO_CATEGORIES,
            attrs_dict=attrs_dict)

    # POST — сохранить изменения
    brand = request.form.get('brand', '').strip() or 'Неизвестный бренд'
    size = request.form.get('size', '').strip() or 'Не указан'
    condition = request.form.get('condition', 'Хорошее')
    purchase_price = float(request.form.get('purchase_price', 0) or 0)
    sale_price = float(request.form.get('sale_price', 0) or 0)
    # сотрудник: закупку не видит и не меняет
    if (session.get('user') or {}).get('role') != 'admin':
        purchase_price = 0
    article = request.form.get('article', '').strip()
    warehouse = request.form.get('warehouse', 'base')
    supplier = request.form.get('supplier', '').strip()
    if supplier == 'Другое':
        supplier = request.form.get('supplier_custom', '').strip() or 'Другое'
    description = request.form.get('description', '').strip()
    avito_link = request.form.get('avito_link', '').strip()
    title = request.form.get('title', '').strip()
    category = request.form.get('category', '').strip()
    subcategory = request.form.get('subcategory', '').strip()
    note = request.form.get('note', '').strip()

    # attrs — динамические характеристики категории
    cat_info = config.AVITO_CATEGORIES.get(category)
    attrs = {}
    if cat_info:
        for idx, aname in enumerate(cat_info['attrs']):
            if aname in ('Состояние', 'Бренд', 'Размер', 'Размер (RU)'):
                continue
            v = request.form.get(f'av_{idx}', '').strip()
            if v:
                attrs[aname] = v

    # фото: порядок существующих берём из photo_order (JSON от JS-галереи;
    # удалённые пользователем фото в него НЕ попадают), новые файлы добавляются в конец.
    # Если поле photo_order отсутствует в запросе (браузер без JS) — сохраняем все старые фото.
    old_photos = [p.strip() for p in (item.get('photos') or '').split(',') if p.strip()]
    old_set = set(old_photos)
    had_order = 'photo_order' in request.form
    pics_with_order = []
    if had_order:
        raw_order = request.form.get('photo_order') or ''
        if raw_order.strip():
            import json as _json
            try:
                ordered = _json.loads(raw_order)
                if isinstance(ordered, list):
                    pics_with_order = [p for p in ordered if isinstance(p, str) and p in old_set]
            except Exception:
                pics_with_order = []
        else:
            pics_with_order = []
    photos = pics_with_order[:10] if had_order else old_photos[:10]
    for f in request.files.getlist('photos') if 'photos' in request.files else []:
        path, url = save_uploaded_photo(f, os.path.join(UPLOAD_FOLDER, warehouse))
        if path:
            photos.append(url)
    photos = photos[:10]

    # техгалерея «Замеры и косяки»: tech_order (JSON путей оставшихся) + новые файлы;
    # без поля tech_order (браузер без JS) — сохраняем все старые техфото
    old_tech = [p.strip() for p in (item.get('tech_photos') or '').split(',') if p.strip()]
    old_tech_set = set(old_tech)
    tech_photos = list(old_tech)
    if 'tech_order' in request.form:
        raw_tech = request.form.get('tech_order') or ''
        if raw_tech.strip():
            import json as _json
            try:
                ordered = _json.loads(raw_tech)
                if isinstance(ordered, list):
                    tech_photos = [p for p in ordered if isinstance(p, str) and p in old_tech_set]
            except Exception:
                tech_photos = list(old_tech)
        else:
            tech_photos = []
    for f in request.files.getlist('tech_photos') if 'tech_photos' in request.files else []:
        path, url = save_uploaded_photo(f, os.path.join(UPLOAD_FOLDER, warehouse, 'tech'))
        if path:
            tech_photos.append(url)
    tech_photos = tech_photos[:10]

    kw = dict(brand=brand, size=size, condition=condition,
              purchase_price=purchase_price, sale_price=sale_price,
              article=article, warehouse=warehouse,
              description=description, avito_link=avito_link,
              title=title, category=category, subcategory=subcategory,
              note=note, attrs=attrs, supplier=supplier,
              photos=",".join(photos),
              tech_photos=",".join(tech_photos))
    db.update_item(item_id, **kw)
    # фото, которые убрали из товара: удаляем с диска, если нигде больше не используются
    old_paths = [p.strip() for p in old_photos if p.strip()]
    final_set = set(photos)
    removed = [p for p in old_paths if p not in final_set]
    if removed:
        delete_unused_photo_files(removed)
    # техфото, убранные из галереи «Замеры и косяки»
    removed_tech = [p for p in old_tech if p not in set(tech_photos)]
    if removed_tech:
        delete_unused_photo_files(removed_tech)
    flash('Товар обновлён!', 'success')
    return redirect(url_for('item_card', item_id=item_id))

@app.route('/item/<int:item_id>/description', methods=['POST'])
@login_required
@admin_required
def update_description(item_id):
    description = request.form.get('description', '').strip()
    db.update_item(item_id, description=description)
    flash('Описание сохранено!', 'success')
    return redirect(url_for('item_card', item_id=item_id))

@app.route('/item/<int:item_id>/avito', methods=['POST'])
@login_required
@admin_required
def update_avito(item_id):
    avito_link = request.form.get('avito_link', '').strip()
    db.update_item(item_id, avito_link=avito_link)
    flash('Ссылка Avito сохранена!', 'success')
    return redirect(url_for('item_card', item_id=item_id))

# ───── СТАТУС ─────

# ───── ОБНОВЛЕНИЕ НАЗВАНИЯ ─────
@app.route('/item/<int:item_id>/title', methods=['POST'])
@login_required
@admin_required
def update_title(item_id):
    title = request.form.get('title', '').strip()
    db.update_item(item_id, title=title)
    flash('Название обновлено', 'success')
    return redirect(url_for('item_card', item_id=item_id))

# ───── ОБНОВЛЕНИЕ ЦЕНЫ ─────
@app.route('/item/<int:item_id>/price', methods=['POST'])
@login_required
@admin_required
def update_price(item_id):
    sale_price = float(request.form.get('sale_price', 0) or 0)
    db.update_item(item_id, sale_price=sale_price)
    flash('Цена обновлена', 'success')
    return redirect(url_for('item_card', item_id=item_id))


@app.route('/item/<int:item_id>/status', methods=['POST'])
@login_required
@admin_required
def update_status(item_id):
    status = request.form.get('status', '')
    if status in config.STATUSES:
        sale_price = request.form.get('sale_price', type=int)
        avito_link = request.form.get('avito_link', '').strip()
        if avito_link and not avito_link.startswith(('http://', 'https://')):
            avito_link = 'https://' + avito_link
        delivery_eta = request.form.get('delivery_eta', '').strip()
        db.update_status(item_id, status, sale_price=sale_price,
                         avito_link=avito_link or None, delivery_eta=delivery_eta or None)
        msg = f'Статус: {status}'
        if status == 'Продано' and sale_price is not None:
            msg += f', цена продажи {sale_price} ₽'
        if status == 'На Avito' and avito_link:
            msg += ', ссылка сохранена'
        if status == 'В доставке' and delivery_eta:
            msg += f', срок: {delivery_eta}'
        flash(msg, 'success')
    return redirect(url_for('item_card', item_id=item_id))

# ───── AI ─────
@app.route('/item/<int:item_id>/ai-generate', methods=['POST'])
@login_required
@editable_required
def ai_generate(item_id):
    item = db.get_item_by_id(item_id)
    if not item:
        flash('Товар не найден', 'danger')
        return redirect(url_for('warehouse_choice'))
    image_bytes_list = []
    photos = item.get('photos', '').split(',') if item.get('photos') else []
    for ph in photos[:3]:
        ph = ph.strip()
        if not ph:
            continue
        try:
            with open(os.path.join(BASE_DIR, ph[1:]), 'rb') as f:
                image_bytes_list.append(f.read())
        except Exception:
            pass
    desc = description_gen.generate_description(
        image_bytes=image_bytes_list if image_bytes_list else None, category=item.get('category',''),
        brand=item.get('brand',''), size=item.get('size',''),
        condition=item.get('condition',''),
        purchase_price=item.get('purchase_price',0),
        sale_price=item.get('sale_price',0),
        title=item.get('title',''), supplier=item.get('supplier',''),
        attrs=item.get('attrs',''), article=item.get('article',''))
    db.update_description(item_id, desc)
    flash('Описание сгенерировано AI!', 'success')
    return redirect(url_for('edit_item', item_id=item_id))

# ───── ЗАГРУЗИТЬ ФОТО ─────
@app.route('/item/<int:item_id>/upload-photos', methods=['POST'])
@login_required
@editable_required
def upload_photos(item_id):
    item = db.get_item_by_id(item_id)
    if not item:
        flash('Товар не найден', 'danger')
        return redirect(url_for('warehouse_choice'))
    wh_id = item.get('warehouse', 'base')
    new_photos = []
    files = request.files.getlist('photos') if 'photos' in request.files else []
    for f in files[:10]:
        path, url = save_uploaded_photo(f, os.path.join(UPLOAD_FOLDER, wh_id))
        if path:
            new_photos.append(url)
    if new_photos:
        db.add_photos(item_id, new_photos)
        flash(f'Добавлено {len(new_photos)} фото', 'success')
    else:
        flash('Не выбраны файлы', 'danger')
    return redirect(url_for('item_card', item_id=item_id))

# ───── УДАЛИТЬ ФОТО ─────
@app.route('/item/<int:item_id>/delete-photo/<int:idx>', methods=['POST'])
@login_required
@editable_required
def delete_photo(item_id, idx):
    if db.delete_photo(item_id, idx):
        flash('Фото удалено', 'success')
    else:
        flash('Ошибка удаления фото', 'danger')
    return redirect(url_for('item_card', item_id=item_id))

# ───── УДАЛИТЬ ТОВАР (физически) ─────
@app.route('/item/<int:item_id>/really-delete', methods=['POST'])
@login_required
@admin_required
def really_delete_item(item_id):
    item = db.get_item_by_id(item_id)
    wh = item.get('warehouse') if item else None
    old_photos = [p.strip() for p in (item.get('photos') or '').split(',') if p.strip()] if item else []
    old_photos += [p.strip() for p in (item.get('tech_photos') or '').split(',') if p.strip()] if item else []
    if db.delete_item_row(item_id):
        flash('Товар полностью удалён из базы', 'danger')
        n = delete_unused_photo_files(old_photos)
        if n:
            logging.info('При удалении товара #%s удалено фото с диска: %d', item_id, n)
    else:
        flash('Ошибка удаления товара', 'error')
    if wh:
        return redirect(url_for('warehouse_items', wh_id=wh))
    return redirect(url_for('warehouse_choice'))

# ───── СПИСАТЬ ─────
@app.route('/item/<int:item_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_item(item_id):
    item = db.get_item_by_id(item_id)
    wh = item.get('warehouse') if item else None
    db.update_status(item_id, 'Списано')
    flash('Товар списан', 'warning')
    if wh:
        return redirect(url_for('warehouse_items', wh_id=wh))
    return redirect(url_for('warehouse_choice'))

# ───── СДЕЛАТЬ ФОТО ГЛАВНЫМ ─────
@app.route('/item/<int:item_id>/main-photo/<int:idx>', methods=['POST'])
@login_required
@editable_required
def set_main_photo(item_id, idx):
    if db.set_main_photo(item_id, idx):
        flash('Главное фото обновлено', 'success')
    else:
        flash('Ошибка: фото не найдено', 'error')
    return redirect(url_for('item_card', item_id=item_id))

# ───── СТАТИСТИКА ─────
@app.route('/stats')
@login_required
def stats_all():
    period = request.args.get('period', 'all')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    user = session.get('user') or {}
    created_by = '' if user.get('role') == 'admin' else user.get('username', '')
    s = db.get_statistics('', period=period, date_from=date_from, date_to=date_to,
                          created_by=created_by)
    return render_template('stats.html', stats=s, wh_name='Все склады', wh_id='all',
                           avito_comm=config.AVITO_COMMISSION, tax_pct=config.TAX_RATE,
                           period=period, date_from=date_from, date_to=date_to)

@app.route('/stats/<wh_id>')
@login_required
def stats(wh_id):
    period = request.args.get('period', 'all')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    user = session.get('user') or {}
    created_by = '' if user.get('role') == 'admin' else user.get('username', '')
    s = db.get_statistics(wh_id, period=period, date_from=date_from, date_to=date_to,
                          created_by=created_by)
    wh_name = db.get_warehouse_name(wh_id)
    return render_template('stats.html', stats=s, wh_name=wh_name, wh_id=wh_id,
        avito_comm=config.AVITO_COMMISSION, tax_pct=config.TAX_RATE,
        period=period, date_from=date_from, date_to=date_to)


# ───── ПОДКЛЮЧЕНИЕ AVITO API ─────
def env_set(key, value):
    """Обновить/добавить строку KEY=value в .env."""
    path = os.path.join(BASE_DIR, '.env')
    lines = []
    if os.path.exists(path):
        lines = open(path, encoding='utf-8').read().splitlines()
    found = False
    for i, l in enumerate(lines):
        if l.startswith(key + '='):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    open(path, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')



@app.route('/avito/setup', methods=['GET', 'POST'])
@login_required
@admin_required
def avito_setup():
    if request.method == 'POST':
        cid = request.form.get('client_id', '').strip()
        csec = request.form.get('client_secret', '').strip()
        if cid:
            env_set('AVITO_CLIENT_ID', cid)
        if csec:
            env_set('AVITO_CLIENT_SECRET', csec)
        flash('Ключи Avito сохранены', 'success')
        return redirect(url_for('avito_setup'))
    return render_template('avito_setup.html', cfg=config)


@app.route('/avito/token', methods=['POST'])
@login_required
@admin_required
def avito_token():
    code = request.form.get('code', '').strip()
    if not config.AVITO_CLIENT_ID or not config.AVITO_CLIENT_SECRET:
        flash('Сначала сохраните Client ID и Client Secret', 'danger')
        return redirect(url_for('avito_setup'))
    if not code:
        flash('Вставьте code из адресной строки', 'danger')
        return redirect(url_for('avito_setup'))
    import urllib.parse, urllib.request, ssl
    data = urllib.parse.urlencode({
        'grant_type': 'authorization_code',
        'client_id': config.AVITO_CLIENT_ID,
        'client_secret': config.AVITO_CLIENT_SECRET,
        'code': code,
        'redirect_uri': 'https://api.avito.ru/',
    }).encode()
    req = urllib.request.Request('https://api.avito.ru/token/', data=data)
    try:
        resp = urllib.request.urlopen(req, timeout=20)
        js = json.loads(resp.read().decode('utf-8'))
        at = js.get('access_token', '')
        rt = js.get('refresh_token', '')
        if at:
            env_set('AVITO_ACCESS_TOKEN', at)
            env_set('AVITO_REFRESH_TOKEN', rt)
            exp = int(js.get('expires_in', 86400))
            config.AVITO_ACCESS_TOKEN = at
            config.AVITO_TOKEN_EXPIRES_AT = int(time.time()) + exp
            env_set('AVITO_TOKEN_EXPIRES_AT', str(config.AVITO_TOKEN_EXPIRES_AT))
            flash('Токен Avito получен и сохранён!', 'success')
        else:
            flash('Ответ Avito без access_token: ' + str(js)[:200], 'danger')
    except Exception as e:
        flash('Ошибка получения токена: ' + str(e)[:300], 'danger')
    return redirect(url_for('avito_setup'))


def avito_refresh_token():
    """Обновить access_token по client_credentials (не нужен refresh_token)."""
    if not config.AVITO_CLIENT_ID or not config.AVITO_CLIENT_SECRET:
        return None
    try:
        data = urllib.parse.urlencode({
            'grant_type': 'client_credentials',
            'client_id': config.AVITO_CLIENT_ID,
            'client_secret': config.AVITO_CLIENT_SECRET,
        }).encode()
        req = urllib.request.Request('https://api.avito.ru/token', data=data)
        resp = urllib.request.urlopen(req, timeout=20)
        js = json.loads(resp.read().decode('utf-8'))
        at = js.get('access_token', '')
        if not at:
            print('avito_refresh_token: ответ без access_token:', str(js)[:200])
            return None
        exp = int(js.get('expires_in', 86400))
        config.AVITO_ACCESS_TOKEN = at
        config.AVITO_TOKEN_EXPIRES_AT = int(time.time()) + exp
        env_set('AVITO_ACCESS_TOKEN', at)
        env_set('AVITO_TOKEN_EXPIRES_AT', str(config.AVITO_TOKEN_EXPIRES_AT))
        print('avito_refresh_token: токен обновлён, expires_in', exp)
        return at
    except Exception as e:
        print('avito_refresh_token: ошибка:', str(e)[:200])
        return None


def avito_get_token():
    """Актуальный токен: продлевает, если нет или истекает через 5 минут."""
    if not config.AVITO_CLIENT_ID or not config.AVITO_CLIENT_SECRET:
        return config.AVITO_ACCESS_TOKEN or None
    exp = getattr(config, 'AVITO_TOKEN_EXPIRES_AT', 0) or 0
    if config.AVITO_ACCESS_TOKEN and exp and time.time() < exp - 300:
        return config.AVITO_ACCESS_TOKEN
    return avito_refresh_token()


@app.route('/avito/check', methods=['POST'])
@login_required
@admin_required
def avito_check():
    if not config.AVITO_ACCESS_TOKEN:
        flash('Нет сохранённого токена — получите его сначала', 'danger')
        return redirect(url_for('avito_setup'))
    tok = avito_get_token()
    if not tok:
        flash('Нет токена: проверьте Client ID/Client Secret на странице Avito API', 'danger')
        return redirect(url_for('avito_setup'))
    req = urllib.request.Request('https://api.avito.ru/core/v1/accounts/self',
                                 headers={'Authorization': 'Bearer ' + tok})
    try:
        resp = urllib.request.urlopen(req, timeout=20)
        js = json.loads(resp.read().decode('utf-8'))
        name = js.get('name') or js.get('email') or js.get('id') or js
        flash('Токен работает. Аккаунт Avito: ' + str(name)[:100], 'success')
    except Exception as e:
        flash('Токен не работает: ' + str(e)[:300], 'danger')
    return redirect(url_for('avito_setup'))


# ---- СИНХРОНИЗАЦИЯ СТАТУСОВ С AVITO ----

AVITO_STATUS_MAP = {
    'sold': 'Продано',
    'inactive': 'Продано',
    'removed': 'Продано',
    'unavailable': 'Продано',
    'delivery': 'В доставке',
    'active': 'На Avito',
}

@app.route('/avito/sync-statuses', methods=['POST'])
@login_required
@admin_required
def avito_sync_statuses():
    """Синхронизировать статусы: проверить товары на Авито и обновить локальные."""
    tok = avito_get_token()
    if not tok:
        flash('Нет токена Avito API — получите / обновите его', 'danger')
        return redirect(url_for('avito_setup'))

    results = _sync_avito_statuses(tok)

    if results:
        flash('Синхронизация статусов завершена:\n' + '\n'.join(results), 'info')
    else:
        flash('Статусы синхронизированы — изменений нет', 'success')
    return redirect(url_for('warehouse_choice'))


def _sync_avito_statuses(tok):
    """
    Синхронизирует статусы всех товаров с привязкой к Avito (без Flask-контекста,
    можно вызывать из фонового потока). Возвращает список строк-логов.
    """
    from db import db
    user_id = 40205075
    all_items = db.get_all_items()
    linked = [it for it in all_items if it.get('avito_link', '').strip()]
    if not linked:
        return []

    results = []
    for item in linked:
        item_id = item['id']
        link = item['avito_link'].strip()
        m = re.search(r'_(\d+)$', link)
        avito_id = m.group(1) if m else None
        if not avito_id:
            continue

        try:
            req = urllib.request.Request(
                f'https://api.avito.ru/core/v1/accounts/{user_id}/items/{avito_id}/',
                headers={'Authorization': f'Bearer {tok}'}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())

            avito_status = data.get('status', '')
            new_status = AVITO_STATUS_MAP.get(avito_status, '')
            cur_status = item.get('status', '')

            if not new_status:
                results.append(f'#{item_id}: неизвестный статус Avito «{avito_status}»')
                continue
            if cur_status == new_status:
                continue

            # Не откатываем финальные статусы (Продано/Списано) обратно в «На Avito»
            if cur_status in ('Продано', 'Списано') and new_status == 'На Avito':
                results.append(f'#{item_id}: пропущен откат {cur_status} → «На Avito» (объявление снова active)')
                continue

            db.update_status(item_id, new_status, avito_link=link)
            results.append(f'#{item_id}: {item.get("title") or "?"} → {new_status} (с Avito: {avito_status})')

        except urllib.error.HTTPError as e:
            if e.code in (404, 422, 410):
                if item.get('status', '') not in ('Продано', 'Списано'):
                    db.update_status(item_id, 'Продано', avito_link=link)
                    results.append(f'#{item_id}: объявление не найдено на Avito → Продано')
            else:
                body = e.read().decode(errors='replace')[:100]
                results.append(f'#{item_id}: HTTP {e.code} — {body}')
        except Exception as e:
            results.append(f'#{item_id}: ошибка — {e}')

    return results


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    local_ip = '127.0.0.1'
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    if config.AVITO_API_READY:
        try:
            _t = avito_get_token()
            if _t:
                print("   Avito API: подключено, токен готов")
            else:
                print("   Avito API: ключи есть, но токен не получен (client_credentials)")
        except Exception as e:
            print("   Avito API: сбой при старте:", str(e)[:120])

        # ── Фоновая синхронизация статусов с Avito (каждые 10 минут) ──
        def _avito_sync_worker():
            time.sleep(60)  # первый запуск — через минуту после старта
            while True:
                try:
                    tok = avito_get_token()
                    if tok:
                        logs = _sync_avito_statuses(tok)
                        changed = [l for l in logs if '→' in l]
                        if changed:
                            print(f"[avito-sync] {len(changed)} изменений статусов:")
                            for l in changed:
                                print(f"   {l}")
                        elif logs:
                            print("[avito-sync] предупреждения:", '; '.join(logs[:5]))
                    else:
                        print("[avito-sync] нет токена, пропуск")
                except Exception as e:
                    print(f"[avito-sync] ошибка: {e}")
                time.sleep(600)

        threading.Thread(target=_avito_sync_worker, daemon=True).start()
        print("   Avito: фоновая синхронизация статусов запущена (каждые 10 мин)")
    print(f"40 Vova40 — учёт секонд-хенда")
    print(f"   На этом ПК:  http://127.0.0.1:{port}")
    print(f"   С телефона:  http://{local_ip}:{port}")
    print(f"   AI: {config.ai_label}")
    print(f"   Нажми Ctrl+C для остановки.")
    app.run(host='0.0.0.0', port=port, debug=debug)