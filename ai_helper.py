#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Модуль генерации описаний товаров.
Поддерживает:
  - YandexGPT (работает из России, бесплатный стартовый пакет)
  - OpenAI GPT-4o (не работает из РФ, но можно через VPN)
  - Шаблонное описание (всегда работает, ничего не нужно)
"""

import logging
from typing import Optional

try:
    from config import config
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from config import config

logger = logging.getLogger(__name__)


class DescriptionGenerator:
    """Генератор описаний. Выбирает провайдера автоматически."""

    def __init__(self):
        self._provider = None  # lazy init

    def _get_provider(self):
        """Возвращает подходящего провайдера."""
        if self._provider:
            return self._provider

        if config.AI_PROVIDER == "yandex" and config.has_yandexgpt:
            try:
                from yandex_provider import YandexGPTProvider
                self._provider = YandexGPTProvider()
                logger.info("AI: YandexGPT")
                return self._provider
            except Exception as e:
                logger.warning(f"YandexGPT не загрузился: {e}")

        if config.AI_PROVIDER == "openai" and config.has_openai:
            try:
                from openai_provider import OpenAIProvider
                self._provider = OpenAIProvider()
                logger.info("AI: OpenAI GPT-4o")
                return self._provider
            except Exception as e:
                logger.warning(f"OpenAI не загрузился: {e}")

        self._provider = _TemplateProvider()
        logger.info("AI: шаблон (нет ключа)")
        return self._provider

    def generate_description(
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
        """Генерирует описание товара. image_bytes — список байт всех фото товара.
        title/supplier/attrs/article — дополнительные данные, обогащающие промпт."""
        provider = self._get_provider()
        try:
            return provider.generate(
                images_bytes=image_bytes, category=category, brand=brand, size=size,
                condition=condition, purchase_price=purchase_price, sale_price=sale_price,
                title=title, supplier=supplier, attrs=attrs, article=article)
        except Exception as e:
            logger.error(f"Ошибка AI провайдера: {e}")
            return _TemplateProvider().generate(
                images_bytes=image_bytes, category=category, brand=brand, size=size,
                condition=condition, purchase_price=purchase_price, sale_price=sale_price,
                title=title, supplier=supplier, attrs=attrs, article=article)

    def improve_description(self, current_description: str) -> str:
        """Улучшает существующее описание (если есть AI)."""
        provider = self._get_provider()
        if isinstance(provider, _TemplateProvider):
            return current_description
        try:
            return provider.improve(current_description)
        except Exception as e:
            logger.error(f"Ошибка улучшения: {e}")
            return current_description


class _TemplateProvider:
    """Шаблонный генератор — не требует никаких ключей."""

    def generate(self, images_bytes, category, brand, size,
                 condition, purchase_price, sale_price,
                 title="", supplier="", attrs=None, article=""):
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
                photo_hint = "\nПо фото: " + photo_hints[0]
            else:
                unique = []
                seen = set()
                for h in photo_hints:
                    if h not in seen:
                        unique.append(h); seen.add(h)
                photo_hint = "\nПо фото: " + " | ".join(unique)
        # Парсим attrs для материала/состава
        material = ""
        if attrs:
            ad = {}
            if isinstance(attrs, dict):
                ad = attrs
            elif isinstance(attrs, str):
                try:
                    import json
                    ad = json.loads(attrs)
                except Exception:
                    pass
            material = ad.get('Материал') or ad.get('Состав') or ad.get('material') or ad.get('material', '')
        # Заголовок
        title_part = title.strip() if title.strip() else f"{brand} — {category}".strip(" —")
        header = f"📌 {title_part}\n\n"
        # Блок размера и материала
        size_block = f"📏 Размер: {size}\n" if size else ""
        material_block = f"🧵 Состав: {material}\n" if material else ""
        return (
            header +
            f"Продаю {category.lower()} бренда {brand}" +
            (f", размер {size}" if size else "") +
            f". Состояние: {condition.lower()}.{photo_hint}\n\n"
            f"{size_block}"
            f"{material_block}"
            f"📊 Состояние: {condition}\n"
            f"💰 Цена: {sale_price:.0f} ₽\n\n"
            f"Оригинал, как и все в профиле.\n\n"
            f"! перед оформлением Авито-доставкой свяжитесь в сообщениях !\n\n"
            f"#секондхенд #{brand.lower().replace(' ', '')} "
            f"#{category.lower().replace(' ', '')} #avito"
        )

    def improve(self, current_description):
        return current_description


# Экспорт
description_gen = DescriptionGenerator()