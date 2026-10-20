#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Провайдер YandexGPT для генерации описаний.
Работает из России, нужен API-ключ Yandex Cloud.
"""

import os
import json
import logging
from typing import Optional

import requests

try:
    from config import config
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import config

logger = logging.getLogger(__name__)


class YandexGPTProvider:
    """Генерация описаний через YandexGPT API."""

    # URL для YandexGPT (Foundation Models API)
    API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    def generate(
        self,
        image_bytes: Optional[list[bytes]],
        category: str,
        brand: str,
        size: str,
        condition: str,
        purchase_price: float,
        sale_price: float,
        title: str = "",
        supplier: str = "",
        attrs: Optional[object] = None,
        article: str = "",
    ) -> str:
        """Генерирует описание через YandexGPT. image_bytes — список байт всех фото."""
        prompt = self._build_prompt(category, brand, size, condition, sale_price, images_bytes=image_bytes, title=title, attrs=attrs, supplier=supplier)

        try:
            response = self._call_yandexgpt(prompt)
            if response:
                return response
        except Exception as e:
            logger.error(f"YandexGPT ошибка: {e}")

        return self._fallback(category, brand, size, condition, sale_price)

    def improve(self, current_description: str) -> str:
        """Улучшает описание."""
        prompt = (
            "Ты — редактор объявлений Avito. Улучши описание товара: "
            "сделай его более привлекательным, структурированным, "
            "но сохрани все факты. Максимум 1000 символов.\n\n"
            f"Описание:\n{current_description}"
        )
        try:
            return self._call_yandexgpt(prompt) or current_description
        except Exception:
            return current_description

    def _build_prompt(self, category, brand, size, condition, price, images_bytes=None, title="", attrs=None, supplier="") -> str:

        photo_hints = []
        if images_bytes:
            for ib in images_bytes:
                try:
                    import image_analysis as ian
                    ih = ian.describe(ib)
                    if ih:
                        photo_hints.append(ih)
                except Exception:
                    pass
        photo_hint = ""
        if photo_hints:
            if len(photo_hints) == 1:
                photo_hint = "Дополнительно по фото: " + photo_hints[0] + "\n"
            else:
                # убираем дубликаты
                unique = []
                seen = set()
                for h in photo_hints:
                    if h not in seen:
                        unique.append(h); seen.add(h)
                summary = " | ".join(unique)
                photo_hint = f"Описание по фото (всего {len(unique)} снимков): {summary}\n"
                return (
            "Ты — продавец секонд-хенда на Avito. Напиши описание товара в твоём личном стиле.\n\n"
            "ТВОЙ СТИЛЬ (строго соблюдай):\n"
            "1. Начни с названия: [БРЕНД] [модель] — кратко и ёмко.\n"
            "2. Опиши вещь подробно: материал, цвет, фактура, особенности, фасон. Покажи, что знаешь бренд.\n"
            "3. Если есть интересная история бренда или культурная отсылка — добавь (как про Ben Sherman и модов).\n"
            "4. Укажи размер и добавь «замеры скину».\n"
            "5. Состояние опиши честно: «Идеальное состояние, без следов использования и нюансов» / «Отличное состояние, без дефектов» / «Состояние на фото».\n"
            "6. Обязательно добавь: «Оригинал, как и все в профиле.»\n"
            "7. Обязательно добавь: «! перед оформлением Авито-доставкой свяжитесь в сообщениях !»\n"
            "8. В конце добавь блок «не [Бренд], не [Бренд]» — 6-12 строк с брендами, с которыми товар могут спутать (через запятую или по одному, с точками между).\n"
            "9. Если у вещи есть совместимость/сочетаемость с другими брендами — добавь «/// подходит для [Бренд]» или «работает с [Бренд]».\n"
            "10. Максимум 1000 символов. Пиши живым, естественным русским языком, как человек, а не маркетолог.\n\n"
            f"Категория: {category}\n"
            f"Бренд: {brand}\n"
            f"Размер: {size}\n"
            f"Состояние: {condition}\n"
            f"Цена: {price:.0f} ₽\n\n"
            + (f"Название: {title}\n" if title else "")
            + (f"Характеристики: {attrs}\n" if attrs else "")
            + (f"Поставщик: {supplier}\n" if supplier else "")
            + f"{photo_hint}"
            + "ПРИМЕР ожидаемого стиля (образец):\n"
            + "LUKE 1977 New Orleans Jacket Black\n\n"
            + "Классическая парка от британцев United Kingdom of LUKE — увесистая, матовый материал верха из смеси нейлона и полиестера, добротно утеплена, 6 объёмных наружных карманов + 1 внутренний.\n\n"
            + "Высокий ворот, съемный на молнии капюшон — глубокий, с козырьком и утяжками.\n\n"
            + "Размер мужской XL, замеры скину.\n\n"
            + "Идеальное состояние, без следов использования и нюансов.\n\n"
            + "Оригинал, как и все в профиле.\n\n"
            + "! перед оформлением Авито-доставкой свяжитесь в сообщениях !\n\n"
            + ".\nне R71212, не Baracuta\n.\nне Paul, не Shark\n.\nне Barbour, не Orvis\n.\nне Gore-Tex, не Fred Perry\n"
        )

    def _call_yandexgpt(self, prompt: str) -> Optional[str]:
        """Выполняет запрос к YandexGPT."""
        headers = {
            "Authorization": f"Api-Key {config.YANDEX_API_KEY}",
            "Content-Type": "application/json",
        }

        data = {
            "modelUri": f"gpt://{config.YANDEX_FOLDER_ID}/yandexgpt/latest",
            "completionOptions": {
                "stream": False,
                "temperature": 0.7,
                "maxTokens": 1000,
            },
            "messages": [
                {
                    "role": "system",
                    "text": "Ты — копирайтер для Avito, специалист по секонд-хенду.",
                },
                {"role": "user", "text": prompt},
            ],
        }

        try:
            response = requests.post(
                self.API_URL,
                headers=headers,
                json=data,
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()

            # Парсим ответ
            alternatives = result.get("result", {}).get("alternatives", [])
            if alternatives:
                msg = alternatives[0].get("message", {})
                text = msg.get("text", "") or msg.get("content", "")
                if text:
                    return text.strip()

        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP ошибка YandexGPT: {e}")

        return None

    def _fallback(self, category, brand, size, condition, price) -> str:
        """Запасной вариант."""
        return (
            f"{brand} — {category}\n\n"
            f"Продаю {category.lower()} бренда {brand}" +
            (f", размер {size}" if size else "") +
            f". Состояние: {condition.lower()}.\n\n"
            f"📏 Размер: {size}\n"
            f"📊 Состояние: {condition}\n"
            f"💰 Цена: {price:.0f} ₽\n\n"
            f"Оригинал, как и все в профиле.\n\n"
            f"! перед оформлением Авито-доставкой свяжитесь в сообщениях !\n\n"
            f"#секондхенд #{brand.lower().replace(' ', '')} "
            f"#{category.lower().replace(' ', '')} #avito"
        )