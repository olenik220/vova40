#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Провайдер OpenAI для генерации описаний.
Не работает из России без VPN, но доступен через него.
"""

import base64
import logging
from typing import Optional

try:
    from config import config
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import config

logger = logging.getLogger(__name__)


class OpenAIProvider:
    """Генерация описаний через OpenAI GPT-4o Vision."""

    def __init__(self):
        from openai import OpenAI as OpenAIClient
        self.client = OpenAIClient(api_key=config.OPENAI_API_KEY)

    def _encode_image(self, image_bytes: bytes) -> str:
        """Кодирует изображение в base64."""
        return base64.b64encode(image_bytes).decode("utf-8")

    def generate(
        self,
        images_bytes: Optional[list[bytes]],
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
        """Генерирует описание через OpenAI GPT-4o Vision. images_bytes — список байт всех фото."""
        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Ты — продавец секонд-хенда на Avito. Напиши описание товара в твоём личном стиле.\n\n"
                        "ТВОЙ СТИЛЬ (строго соблюдай):\n"
                        "1. Начни с названия: [БРЕНД] [модель] — кратко и ёмко.\n"
                        "2. Опиши вещь подробно: материал, цвет, фактура, особенности, фасон. Используй информацию с фото.\n"
                        "3. Если есть интересная история бренда или культурная отсылка — добавь.\n"
                        "4. Укажи размер и добавь «замеры скину».\n"
                        "5. Состояние опиши честно: «Идеальное состояние, без следов использования и нюансов» / «Отличное состояние, без дефектов» / «Состояние на фото».\n"
                        "6. Обязательно добавь: «Оригинал, как и все в профиле.»\n"
                        "7. Обязательно добавь: «! перед оформлением Авито-доставкой свяжитесь в сообщениях !»\n"
                        "8. В конце добавь блок «не [Бренд], не [Бренд]» — 6-12 строк с брендами для исключения путаницы.\n"
                        "9. Если у вещи есть совместимость с другими брендами — добавь «/// подходит для [Бренд]».\n"
                        "10. Максимум 1000 символов. Пиши живым, естественным русским языком.\n\n"
                        "ПРИМЕР СТИЛЯ:\n"
                        "LUKE 1977 New Orleans Jacket Black\n\n"
                        "Классическая парка от британцев United Kingdom of LUKE — увесистая, матовый материал верха, добротно утеплена...\n"
                        "Размер мужской XL, замеры скину.\n"
                        "Идеальное состояние, без следов использования и нюансов.\n"
                        "Оригинал, как и все в профиле.\n"
                        "! перед оформлением Авито-доставкой свяжитесь в сообщениях !\n"
                        ".\nне R71212, не Baracuta\n.\nне Paul, не Shark\n.\nне Barbour, не Orvis\n.\nне Gore-Tex, не Fred Perry"
                    ),
                }
            ]

            user_content = (
                f"Составь описание для Avito:\n" +
                (f"Название: {title}\n" if title else "") +
                f"Категория: {category}\n" +
                f"Бренд: {brand}\n" +
                f"Размер: {size}\n" +
                f"Состояние: {condition}\n" +
                (f"Характеристики: {attrs}\n" if attrs else "") +
                f"Цена: {sale_price:.0f} ₽\n"
            )

            content_parts = [{"type": "text", "text": user_content}]
            if images_bytes:
                content_parts[0]["text"] += "\n\nВот фото товара (первые два снимка с максимальным качеством, остальные — для полноты картины):"
                for ib in images_bytes:
                    try:
                        b64 = self._encode_image(ib)
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"},
                        })
                    except Exception:
                        pass
                user_message = {"role": "user", "content": content_parts}
            else:
                user_message = {
                    "role": "user",
                    "content": user_content + "\n\n(Фото не приложено, опиши из параметров)"
                }

            messages.append(user_message)

            response = self.client.chat.completions.create(
                model="gpt-4o-2024-11-20",
                messages=messages,
                max_tokens=1000,
                temperature=0.7,
            )

            text = response.choices[0].message.content.strip()
            return text or self._fallback(category, brand, size, condition, sale_price)

        except Exception as e:
            logger.error(f"OpenAI ошибка: {e}")
            return self._fallback(category, brand, size, condition, sale_price)

    def improve(self, current_description: str) -> str:
        """Улучшает существующее описание."""
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты — редактор объявлений Avito. Улучши описание товара: "
                            "сделай его более привлекательным, структурированным, "
                            "но сохрани все факты. Максимум 1000 символов."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Улучши это описание:\n\n{current_description}",
                    },
                ],
                max_tokens=1000,
                temperature=0.5,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"OpenAI улучшение ошибка: {e}")
            return current_description

    def _fallback(self, category, brand, size, condition, price) -> str:
        return (
            f"{brand} — {category}\n\n"
            f"Продаю {category.lower()} бренда {brand}, размер {size}. "
            f"Состояние: {condition.lower()}.\n\n"
            f"Размер: {size}\n"
            f"Состояние: {condition}\n"
            f"Цена: {price:.0f} ₽\n\n"
            f"Оригинал, как и все в профиле.\n\n"
            f"! перед оформлением Авито-доставкой свяжитесь в сообщениях !\n\n"
            f"#секондхенд #{brand.lower().replace(' ', '')} "
            f"#{category.lower().replace(' ', '')} #avito"
        )