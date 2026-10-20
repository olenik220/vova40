"""
Конфигурация бота для учета товаров секонд-хенда.
Загружает настройки из .env файла и переменных окружения.
Работает как из .py, так и из .exe (PyInstaller).
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv


def _get_base_path() -> str:
    """
    Определяет базовую папку приложения.
    Для .exe — папка с исполняемым файлом.
    Для .py — папка с этим скриптом.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))


BASE_PATH = _get_base_path()
ENV_FILE = os.path.join(BASE_PATH, ".env")

# .env не обязателен — на Render переменные из Dashboard
if os.path.exists(ENV_FILE):
    load_dotenv(ENV_FILE)


class Config:
    """Настройки приложения."""

    # ---- Telegram ----
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    ALLOWED_USER_ID: int = int(os.getenv("ALLOWED_USER_ID", "0"))

    # ---- Google Sheets (опционально) ----
    GOOGLE_SHEETS_ID: str = os.getenv("GOOGLE_SHEETS_ID", "")
    GOOGLE_CREDENTIALS_FILE: str = os.path.join(BASE_PATH, "credentials.json")

    # ---- AI ----
    # ai_provider: "yandex" | "openai" | ""  (пусто = шаблон)
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "").lower().strip()

    # YandexGPT
    YANDEX_FOLDER_ID: str = os.getenv("YANDEX_FOLDER_ID", "")
    YANDEX_API_KEY: str = os.getenv("YANDEX_API_KEY", "")
    YANDEX_CATALOG_ID: str = os.getenv("YANDEX_CATALOG_ID", "")

    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # ---- Avito API (автопубликация) ----
    # Публичный адрес фото (напр. "https://fotos.ski-school.ru" или
    # "https://ski-school.ru/avito-photos") — тогда фото в экспорте будут
    # полными URL, и их сможет скачать Avito API при автопубликации.
    AVITO_PUBLIC_URL: str = os.getenv("AVITO_PUBLIC_URL", "").strip().rstrip('/')

    # FTP-сервер для автозаливки фото (можно свой хостинг ski-school.ru)
    # Публичная папка на сервере должна соответствовать AVITO_PUBLIC_URL.
    AVITO_FTP_HOST: str = os.getenv("AVITO_FTP_HOST", "").strip()
    AVITO_FTP_PORT: int = int(os.getenv("AVITO_FTP_PORT", "21"))
    AVITO_FTP_USER: str = os.getenv("AVITO_FTP_USER", "").strip()
    AVITO_FTP_PASS: str = os.getenv("AVITO_FTP_PASS", "").strip()
    # Папка на сервере, куда класть фото (напр. "/public_html/avito-photos" или пусто = корень FTP)
    AVITO_FTP_PATH: str = os.getenv("AVITO_FTP_PATH", "").strip().strip('/')
    # Использовать FTPS (шифрование). Многие хостинги требуют TLS.
    AVITO_FTP_TLS: bool = os.getenv("AVITO_FTP_TLS", "1") not in ("0", "false", "no")
    AVITO_FTP_READY: bool = bool(os.getenv("AVITO_FTP_HOST")) and bool(os.getenv("AVITO_FTP_USER"))

    # Клиентские ключи Avito (заполняется, когда Иван получит доступ на https://api.avito.ru/)
    AVITO_CLIENT_ID: str = os.getenv("AVITO_CLIENT_ID", "")
    AVITO_CLIENT_SECRET: str = os.getenv("AVITO_CLIENT_SECRET", "")
    AVITO_ACCESS_TOKEN: str = os.getenv("AVITO_ACCESS_TOKEN", "")
    AVITO_REFRESH_TOKEN: str = os.getenv("AVITO_REFRESH_TOKEN", "")
    AVITO_TOKEN_EXPIRES_AT: int = int(os.getenv("AVITO_TOKEN_EXPIRES_AT", "0") or 0)
    AVITO_API_READY: bool = bool(os.getenv("AVITO_CLIENT_ID")) and bool(os.getenv("AVITO_CLIENT_SECRET"))

    # ---- Комиссии и налоги ----
    # Процент комиссии Avito (по умолчанию 7%)
    AVITO_COMMISSION: float = float(os.getenv("AVITO_COMMISSION", "7"))
    # Ставка налога (по умолчанию 6% для самозанятых/НПД)
    TAX_RATE: float = float(os.getenv("TAX_RATE", "6"))

    # Пути
    ENV_FILE: str = ENV_FILE
    TOKEN_PICKLE: str = os.path.join(BASE_PATH, "token.pickle")
    DB_PATH: str = os.path.join(BASE_PATH, "data.db")

    # Заголовки колонок (для Google Sheets, если подключена)
    SHEET_HEADERS = [
        "ID", "Дата", "Категория", "Бренд", "Размер", "Состояние",
        "Цена закупки", "Цена продажи", "Статус",
        "Фото (Telegram File ID)", "Описание для Авито",
        "Ссылка на Avito", "Примечание",
    ]

    # Категории, состояния, статусы
    # Категории — иерархия Avito «Личные вещи»: категория -> подкатегории.
    # Каждая категория: icon (emoji), subcategories (list), attrs (список характеристик объявления Avito).
    AVITO_CATEGORIES = {
        "Женская одежда": {
            "icon": "👗",
            "subcategories": [
                "Верхняя одежда", "Пальто", "Куртки", "Пуховики", "Плащи",
                "Блузки и рубашки", "Платья и сарафаны", "Юбки",
                "Брюки и шорты", "Джинсы", "Костюмы и комплекты",
                "Свитера и кардиганы", "Толстовки и свитшоты",
                "Спортивная одежда", "Белье и купальники", "Носки и колготки",
                "Одежда для дома", "Головные уборы", "Перчатки и варежки", "Прочая одежда",
            ],
            "attrs": ["Состояние", "Бренд", "Размер", "Размер производителя", "Цвет", "Состав/материал", "Сезон"],
        },
        "Мужская одежда": {
            "icon": "👔",
            "subcategories": [
                "Верхняя одежда", "Пальто", "Куртки", "Пуховики", "Плащи",
                "Рубашки", "Футболки и майки", "Поло", "Брюки и шорты", "Джинсы",
                "Костюмы", "Свитера и кофты", "Толстовки и худи",
                "Спортивная одежда", "Белье и носки", "Головные уборы", "Прочая одежда",
            ],
            "attrs": ["Состояние", "Бренд", "Размер", "Размер производителя", "Цвет", "Состав/материал", "Сезон"],
        },
        "Детская одежда": {
            "icon": "🧒",
            "subcategories": [
                "Для новорожденных", "Для девочек", "Для мальчиков", "Для подростков",
                "Верхняя одежда", "Платья и сарафаны", "Брюки и джинсы",
                "Свитера и кофты", "Футболки и рубашки", "Обувь детская",
                "Колготки и носки", "Пижамы и одежда для дома", "Прочее",
            ],
            "attrs": ["Состояние", "Бренд", "Размер", "Возраст", "Цвет", "Состав/материал", "Сезон", "Пол"],
        },
        "Женская обувь": {
            "icon": "👠",
            "subcategories": [
                "Сапоги и ботильоны", "Ботинки и полуботинки", "Туфли",
                "Босоножки и сандалии", "Кроссовки и кеды", "Мокасины и лоферы",
                "Домашняя обувь", "Спортивная обувь", "Стельки и аксессуары", "Прочая обувь",
            ],
            "attrs": ["Состояние", "Бренд", "Размер (RU)", "Размер производителя", "Цвет", "Материал верха", "Сезон"],
        },
        "Мужская обувь": {
            "icon": "👞",
            "subcategories": [
                "Кроссовки и кеды", "Ботинки и полуботинки", "Туфли", "Сапоги",
                "Тапки и сандалии", "Домашняя обувь", "Спортивная обувь", "Прочая обувь",
            ],
            "attrs": ["Состояние", "Бренд", "Размер (RU)", "Размер производителя", "Цвет", "Материал верха", "Сезон"],
        },
        "Детская обувь": {
            "icon": "👟",
            "subcategories": [
                "Кроссовки и кеды", "Ботинки и полуботинки", "Сапоги",
                "Сандалии и босоножки", "Туфли", "Домашняя обувь", "Прочая обувь",
            ],
            "attrs": ["Состояние", "Бренд", "Размер (RU)", "Возраст", "Цвет", "Сезон"],
        },
        "Сумки и аксессуары": {
            "icon": "👜",
            "subcategories": [
                "Женские сумки", "Мужские сумки", "Рюкзаки", "Клатчи и кошельки",
                "Чемоданы и дорожные сумки", "Ремни и пояса", "Перчатки и шарфы", "Прочее",
            ],
            "attrs": ["Состояние", "Бренд", "Цвет", "Материал", "Тип застёжки"],
        },
        "Часы и украшения": {
            "icon": "⌚",
            "subcategories": [
                "Женские часы", "Мужские часы", "Браслеты", "Серьги", "Кольца",
                "Кулоны и подвески", "Цепочки", "Бижутерия", "Прочее",
            ],
            "attrs": ["Состояние", "Бренд", "Цвет", "Материал", "Состав"],
        },
        "Прочее": {
            "icon": "📦",
            "subcategories": ["Разное"],
            "attrs": ["Состояние", "Бренд"],
        },
    }

    # Плоский список категорий для обратной совместимости (select в карточке)
    CATEGORIES = list(AVITO_CATEGORIES.keys())

    # Значения типового селекта «Состояние» (как на Avito)
    CONDITIONS = [
        "Новое с биркой", "Новое", "Б/у как новое", "Отличное", "Хорошее", "Удовлетворительное",
    ]

    STATUSES = ["В закупке", "В наличии", "На Avito", "В доставке", "Продано", "Списано"]

    # ---- Свойства-помощники ----

    @property
    def is_configured(self) -> bool:
        """Проверяет, хватает ли для запуска."""
        return bool(self.TELEGRAM_BOT_TOKEN)

    @property
    def has_google_credentials(self) -> bool:
        return os.path.exists(self.GOOGLE_CREDENTIALS_FILE)

    @property
    def use_google_sheets(self) -> bool:
        """Google Sheets используется, если есть ID и credentials."""
        return bool(self.GOOGLE_SHEETS_ID) and self.has_google_credentials

    @property
    def has_openai(self) -> bool:
        return bool(self.OPENAI_API_KEY)

    @property
    def has_yandexgpt(self) -> bool:
        return bool(self.YANDEX_API_KEY)

    @property
    def ai_label(self) -> str:
        """Название текущего AI провайдера (для отображения)."""
        if self.AI_PROVIDER == "yandex" and self.has_yandexgpt:
            return "YandexGPT"
        elif self.AI_PROVIDER == "openai" and self.has_openai:
            return "OpenAI GPT-4o"
        return "нет (шаблон)"


config = Config()