#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Модуль для работы с Google Sheets.
Теперь это опциональная синхронизация поверх локальной БД.
Если Google Sheets настроена — туда дублируются данные.
Если нет — всё работает только локально.
"""

import os
import datetime
import pickle
import logging
from typing import Optional

try:
    from config import config
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from config import config

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class GoogleSheetsSync:
    """
    Синхронизация с Google Sheets.
    Вызывается из bot.py после изменения локальной БД.
    """

    def __init__(self):
        self.service = None
        self._available = False
        self._try_init()

    def _try_init(self):
        """Пытается инициализировать Google API. Не падает при ошибке."""
        if not config.use_google_sheets:
            logger.info("Google Sheets не настроена. Работаем локально.")
            return

        try:
            self._authenticate()
            self._ensure_headers()
            self._available = True
            logger.info("Google Sheets подключена ✅")
        except Exception as e:
            logger.warning(f"Google Sheets НЕ подключена: {e}. Работаем локально.")
            self._available = False

    @property
    def is_available(self) -> bool:
        """Доступна ли синхронизация."""
        return self._available and self.service is not None

    def _authenticate(self):
        """Аутентификация в Google API."""
        creds = None
        token_file = config.TOKEN_PICKLE

        if os.path.exists(token_file):
            try:
                with open(token_file, "rb") as token:
                    creds = pickle.load(token)
            except Exception:
                creds = None

        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(config.GOOGLE_CREDENTIALS_FILE):
                    raise FileNotFoundError("credentials.json не найден")

                flow = InstalledAppFlow.from_client_secrets_file(
                    config.GOOGLE_CREDENTIALS_FILE, SCOPES
                )
                creds = flow.run_local_server(port=0, open_browser=False)

            with open(token_file, "wb") as token:
                pickle.dump(creds, token)

        self.service = build("sheets", "v4", credentials=creds)

    def _ensure_headers(self):
        """Проверяет заголовки в таблице."""
        if not self.service:
            return
        try:
            from googleapiclient.errors import HttpError
            result = self.service.spreadsheets().values().get(
                spreadsheetId=config.GOOGLE_SHEETS_ID,
                range="Лист1!A1:M1",
            ).execute()
            values = result.get("values", [])
            if not values:
                self.service.spreadsheets().values().update(
                    spreadsheetId=config.GOOGLE_SHEETS_ID,
                    range="Лист1!A1:M1",
                    valueInputOption="RAW",
                    body={"values": [config.SHEET_HEADERS]},
                ).execute()
                logger.info("Заголовки Google Sheets созданы")
        except Exception as e:
            logger.error(f"Ошибка заголовков: {e}")

    def sync_add_item(self, item: dict):
        """Синхронизирует один добавленный товар в Google Sheets."""
        if not self.is_available:
            return

        try:
            row_data = [
                str(item.get("id", "")),
                item.get("created_at", ""),
                item.get("category", ""),
                item.get("brand", ""),
                item.get("size", ""),
                item.get("condition", ""),
                str(item.get("purchase_price", 0)),
                str(item.get("sale_price", 0)),
                item.get("status", "В наличии"),
                item.get("photo_file_id", ""),
                item.get("description", ""),
                item.get("avito_link", ""),
                item.get("note", ""),
            ]
            self.service.spreadsheets().values().append(
                spreadsheetId=config.GOOGLE_SHEETS_ID,
                range="Лист1!A:N",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [row_data]},
            ).execute()
            logger.info(f"Google Sheets: товар #{item.get('id')} синхронизирован")
        except Exception as e:
            logger.error(f"Ошибка синхронизации в Google Sheets: {e}")

    def sync_update_item(self, item_id: int, field_name: str, value: str, column_letter: str):
        """Синхронизирует изменение одного поля."""
        if not self.is_available:
            return
        try:
            row = item_id + 1  # +1 из-за заголовка
            self.service.spreadsheets().values().update(
                spreadsheetId=config.GOOGLE_SHEETS_ID,
                range=f"Лист1!{column_letter}{row}",
                valueInputOption="USER_ENTERED",
                body={"values": [[value]]},
            ).execute()
        except Exception as e:
            logger.error(f"Ошибка синхронизации поля {field_name}: {e}")

    def sync_avito(self, item_id: int, link: str):
        """Синхронизирует ссылку Avito и обновляет статус."""
        if not self.is_available:
            return
        try:
            row = item_id + 1
            # Ссылка Avito — колонка M (13)
            self.service.spreadsheets().values().update(
                spreadsheetId=config.GOOGLE_SHEETS_ID,
                range=f"Лист1!M{row}",
                valueInputOption="USER_ENTERED",
                body={"values": [[link]]},
            ).execute()
            # Статус — I (9)
            self.service.spreadsheets().values().update(
                spreadsheetId=config.GOOGLE_SHEETS_ID,
                range=f"Лист1!I{row}",
                valueInputOption="USER_ENTERED",
                body={"values":[["На Avito"]]},
            ).execute()
        except Exception as e:
            logger.error(f"Ошибка синхронизации Avito: {e}")

    def sync_status(self, item_id: int, status: str):
        """Синхронизирует статус."""
        if not self.is_available:
            return
        try:
            row = item_id + 1
            self.service.spreadsheets().values().update(
                spreadsheetId=config.GOOGLE_SHEETS_ID,
                range=f"Лист1!I{row}",
                valueInputOption="USER_ENTERED",
                body={"values": [[status]]},
            ).execute()
        except Exception as e:
            logger.error(f"Ошибка синхронизации статуса: {e}")


# Глобальный экземпляр
sheets_sync = GoogleSheetsSync()