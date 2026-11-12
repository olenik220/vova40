"""Маппинг данных приложения Vova40 -> Avito Автозагрузка (format v3)."""
import re
import avito_enums

CATEGORY_MAP = {
    "Женская одежда": "Одежда, обувь, аксессуары",
    "Мужская одежда": "Одежда, обувь, аксессуары",
    "Детская одежда": "Одежда, обувь, аксессуары",
    "Женская обувь": "Одежда, обувь, аксессуары",
    "Мужская обувь": "Одежда, обувь, аксессуары",
    "Детская обувь": "Одежда, обувь, аксессуары",
    "Сумки и аксессуары": "Одежда, обувь, аксессуары",
    "Аксессуары": "Одежда, обувь, аксессуары",
    "Часы и украшения": "Часы и украшения",
    "Велосипеды": "Велосипеды",
    "Экипировка": "Спорт и отдых",
    "Оборудование для бизнеса": "Оборудование для бизнеса",
    "Прочее": "Одежда, обувь, аксессуары",
}
DEFAULT_CATEGORY = "Одежда, обувь, аксессуары"

ALLOWED_GOODS_TYPES = {
    "Одежда, обувь, аксессуары": {
        "Верхняя одежда", "Пальто", "Куртки", "Пуховики", "Плащи",
        "Блузки и рубашки", "Рубашки", "Платья и сарафаны", "Юбки",
        "Брюки и шорты", "Джинсы", "Костюмы", "Костюмы и комплекты",
        "Свитера и кардиганы", "Свитера и кофты", "Толстовки и свитшоты", "Толстовки и худи",
        "Футболки и майки", "Поло", "Спортивная одежда",
        "Белье и купальники", "Белье и носки", "Носки и колготки",
        "Одежда для дома", "Головные уборы", "Перчатки и варежки", "Прочая одежда",
        "Пижамы и одежда для дома", "Колготки и носки",
        "Сапоги и ботильоны", "Ботинки и полуботинки", "Туфли", "Босоножки и сандалии",
        "Кроссовки и кеды", "Мокасины и лоферы", "Домашняя обувь", "Спортивная обувь",
        "Стельки и аксессуары", "Прочая обувь", "Сандалии и босоножки", "Тапки и сандалии", "Сапоги",
        "Женские сумки", "Мужские сумки", "Рюкзаки", "Клатчи и кошельки",
        "Чемоданы и дорожные сумки", "Ремни и пояса", "Перчатки и шарфы", "Прочее",
        "Для новорожденных", "Для девочек", "Для мальчиков", "Для подростков",
    },
    "Часы и украшения": {
        "Женские часы", "Мужские часы", "Браслеты", "Серьги", "Кольца",
        "Кулоны и подвески", "Цепочки", "Бижутерия", "Прочее",
    },
    "Велосипеды": {"Горные", "Городские", "Шоссейные", "Детские", "BMX", "Прочие", "Прочее"},
    "Спорт и отдых": {"Экипировка", "Прочее", "Мотоэкипировка"},
    "Оборудование для бизнеса": {"Прочее"},
}
DEFAULT_GOODS_TYPE = {"Одежда, обувь, аксессуары": "Прочая одежда"}

ITEM_TO_GOODS_TYPE = {
    "футболка": "Футболки и майки", "майка": "Футболки и майки", "лонгслив": "Футболки и майки",
    "tee": "Футболки и майки", "t-shirt": "Футболки и майки", "tshirt": "Футболки и майки",
    "поло": "Поло", "polo": "Поло",
    "рубашка": "Рубашки", "shirt": "Рубашки",
    "блузка": "Блузки и рубашки", "blouse": "Блузки и рубашки",
    "кофта": "Свитера и кардиганы", "кофточка": "Свитера и кардиганы",
    "свитер": "Свитера и кардиганы", "sweater": "Свитера и кардиганы",
    "кардиган": "Свитера и кардиганы", "cardigan": "Свитера и кардиганы",
    "толстовка": "Толстовки и свитшоты", "свитшот": "Толстовки и свитшоты",
    "худи": "Толстовки и худи", "hoodie": "Толстовки и худи",
    "куртка": "Куртки", "куртк": "Куртки", "jacket": "Куртки", "овершот": "Куртки",
    "пуховик": "Пуховики", "down": "Пуховики",
    "пальто": "Пальто", "coat": "Пальто",
    "плащ": "Плащи", "ветровка": "Куртки",
    "платье": "Платья и сарафаны", "dress": "Платья и сарафаны",
    "сарафан": "Платья и сарафаны",
    "юбка": "Юбки", "skirt": "Юбки",
    "брюки": "Брюки и шорты", "штаны": "Брюки и шорты", "pants": "Брюки и шорты",
    "брюк": "Брюки и шорты", "шорты": "Брюки и шорты", "shorts": "Брюки и шорты",
    "джинсы": "Джинсы", "jeans": "Джинсы",
    "костюм": "Костюмы и комплекты", "комплект": "Костюмы и комплекты", "suit": "Костюмы",
    "кроссовки": "Кроссовки и кеды", "кеды": "Кроссовки и кеды", "sneakers": "Кроссовки и кеды",
    "ботинки": "Ботинки и полуботинки", "полуботинки": "Ботинки и полуботинки", "boots": "Ботинки и полуботинки",
    "сапоги": "Сапоги и ботильоны", "ботильоны": "Сапоги и ботильоны",
    "туфли": "Туфли", "босоножки": "Босоножки и сандалии", "сандалии": "Сандалии и босоножки",
    "тапки": "Тапки и сандалии", "мокасины": "Мокасины и лоферы", "лоферы": "Мокасины и лоферы",
    "сумка": "Женские сумки", "рюкзак": "Рюкзаки", "backpack": "Рюкзаки",
    "кошелек": "Клатчи и кошельки", "ремень": "Ремни и пояса", "пояс": "Ремни и пояса",
    "шарф": "Перчатки и шарфы", "перчатки": "Перчатки и варежки",
    "шапка": "Головные уборы", "шапк": "Головные уборы", "кепка": "Головные уборы",
    "hat": "Головные уборы", "cap": "Головные уборы",
}

# Состояние в Авито зависит от категории:
#  - одежда/обувь/аксессуары: «Новое с биркой», «Отличное», «Хорошее», «Удовлетворительное»
#  - остальные категории: «Новое» или «Б/у»
CLOTHING_CATEGORIES = {"Одежда, обувь, аксессуары"}
CONDITION_MAP_CLOTHING = {
    "новое с биркой": "Новое с биркой", "новое": "Новое с биркой",
    "б/у как новое": "Отличное", "отличное": "Отличное",
    "хорошее": "Хорошее", "удовлетворительное": "Удовлетворительное",
}
CONDITION_MAP_OTHER = {
    "новое с биркой": "Новое", "новое": "Новое",
    "б/у как новое": "Б/у", "отличное": "Б/у", "хорошее": "Б/у",
    "удовлетворительное": "Б/у",
}
# совместимость со старым кодом
CONDITION_MAP = CONDITION_MAP_CLOTHING
DEFAULT_CONDITION = "Хорошее"


def map_condition(condition, avito_category):
    """Приводит состояние приложения к допустимому значению Авито для категории."""
    key = _norm(condition)
    table = CONDITION_MAP_CLOTHING if avito_category in CLOTHING_CATEGORIES else CONDITION_MAP_OTHER
    return table.get(key, "Хорошее" if avito_category in CLOTHING_CATEGORIES else "Б/у")

SEASON_MAP = {
    "любой": "Всесезон", "всесезон": "Всесезон", "всесезонный": "Всесезон",
    "зима": "Зима", "весна": "Весна", "лето": "Лето", "осень": "Осень",
    "демисезон": "Демисезон", "зима, любой": "Зима", "лето, любой": "Лето",
    "демисезон, любой": "Демисезон", "весна, осень": "Демисезон",
}

SKIP_ATTR_KEYS = {
    "цвет", "материал", "состав/материал", "материал основной части", "материал верха",
    "сезон", "бренд", "размер", "размер (ru)", "состояние",
    "предмет одежды", "тип одежды",
}
EXTRA_ATTR_NAMES = {
    "пол": "Пол",
    "возрастная категория": "Возрастная категория",
    "возраст": "Возраст",
    "стиль": "Стиль",
    "тип рукава": "Тип рукава",
    "тип застёжки": "Тип застёжки",
    "тип транспорта": "Тип транспорта",
    "вид спорта": "Вид спорта",
    "размер производителя": "Размер производителя",
    "количество предметов в комплекте": "Количество предметов в комплекте",
}

SIZE_RE = re.compile(r"(\d{2,3})")


def normalize_size(raw):
    if not raw:
        return ""
    m = SIZE_RE.search(str(raw))
    return m.group(1) if m else ""


def _norm(value):
    return str(value or "").strip().lower().replace("ё", "е")


def _match_goods(text, allowed):
    h = _norm(text)
    if not h:
        return None
    for word in sorted(ITEM_TO_GOODS_TYPE, key=len, reverse=True):
        if word in h:
            goods = ITEM_TO_GOODS_TYPE[word]
            if goods in allowed:
                return goods
    return None


def pick_goods_type(category, subcategory, attrs, title=""):
    avito_cat = CATEGORY_MAP.get(category, DEFAULT_CATEGORY)
    allowed = ALLOWED_GOODS_TYPES.get(avito_cat, set())

    sub = (subcategory or "").strip()
    if sub and sub in allowed:
        return sub

    for key in ("Предмет одежды", "Тип одежды", "Тип"):
        hit = _match_goods(attrs.get(key), allowed)
        if hit:
            return hit

    hit = _match_goods(sub, allowed)
    if hit:
        return hit

    hit = _match_goods(title, allowed)
    if hit:
        return hit

    return DEFAULT_GOODS_TYPE.get(avito_cat, "Прочее")


def build(item):
    attrs = item.get("attrs") or {}
    if isinstance(attrs, str):
        import json
        try:
            attrs = json.loads(attrs)
        except Exception:
            attrs = {}
    if not isinstance(attrs, dict):
        attrs = {}

    category = (item.get("category") or "").strip()
    avito_cat = CATEGORY_MAP.get(category, DEFAULT_CATEGORY)

    condition = map_condition(item.get("condition"), avito_cat)

    size_raw = (item.get("size") or "").strip()
    if _norm(size_raw) in ("не указан", ""):
        size_raw = ""
    size = avito_enums.normalize_size(size_raw or attrs.get("Размер", "")) or normalize_size(size_raw or attrs.get("Размер", ""))

    color = (str(item.get("color") or "").strip()
             or str(attrs.get("Цвет") or "").strip())
    material = (str(item.get("material") or "").strip()
                or str(attrs.get("Материал основной части") or attrs.get("Состав/материал")
                       or attrs.get("Материал") or attrs.get("Материал верха") or "").strip())
    season_raw = (str(item.get("season") or "").strip()
                  or str(attrs.get("Сезон") or "").strip())
    season = SEASON_MAP.get(_norm(season_raw), season_raw)

    extra = {}
    for k, v in attrs.items():
        key = _norm(k)
        if not v or key in SKIP_ATTR_KEYS:
            continue
        extra[EXTRA_ATTR_NAMES.get(key, k)] = str(v).strip()

    # новая схема Авито: GoodsType — верхний уровень (Мужская одежда),
    # Apparel — тип товара (Верхняя одежда), ApparelType — вид (Лёгкие куртки и ветровки)
    subcategory = (item.get("subcategory") or "").strip()
    goods_type_top = (str(item.get("avito_goods_type") or "").strip()
                      or avito_enums.GOODS_TYPE_BY_CATEGORY.get(category))
    stored_apparel = str(item.get("avito_apparel") or "").strip()
    stored_type = str(item.get("avito_apparel_type") or "").strip()
    if stored_apparel:
        # значения пришли из самой выгрузки Авито — используем как есть
        apparel, apparel_type, apparel_confirmed = stored_apparel, stored_type, True
    else:
        apparel, apparel_type, apparel_confirmed = avito_enums.apparel_for(subcategory)
    if goods_type_top:
        goods_type_value = goods_type_top
    else:
        goods_type_value = pick_goods_type(category, subcategory, attrs, item.get("title"))
    color = avito_enums.normalize_color(color) or color

    return {
        "category": avito_cat,
        "goods_type": goods_type_value,
        "apparel": apparel,
        "apparel_type": apparel_type,
        "apparel_confirmed": apparel_confirmed,
        "ad_type": avito_enums.DEFAULT_AD_TYPE,
        "country": (str(item.get("country") or "").strip()
                    or str(attrs.get("Страна происхождения товара") or "").strip()),
        "condition": condition,
        "size": size,
        "size_raw": size_raw,
        "color": color,
        "material": material,
        "season": season,
        "extra_attrs": extra,
        "brand": (item.get("brand") or "").strip() or "Неизвестный бренд",
    }
