"""Локальный анализ фото товара: палитра с долями, тон, светлота, насыщенность,
выделение объекта на фоне, геометрические эвристики типа вещи.

Работает офлайн (Pillow, numpy), без API-ключей. Максимум, что можно вытащить
из одного фото без мультимодальной ML-модели.
"""
import io
from collections import Counter

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import numpy as np
except ImportError:
    np = None


PALETTE = {
    "белый":      (245, 245, 245),
    "чёрный":     (20, 20, 20),
    "серый":      (128, 128, 128),
    "бежевый":    (220, 205, 180),
    "тёмно-бежевый": (180, 155, 120),
    "коричневый": (140, 90, 50),
    "тёмно-коричневый": (95, 60, 30),
    "красный":    (200, 40, 40),
    "бордовый":   (120, 20, 40),
    "розовый":    (240, 150, 170),
    "нежно-розовый": (250, 210, 220),
    "оранжевый":  (230, 120, 30),
    "жёлтый":     (230, 200, 60),
    "тёмно-жёлтый": (170, 140, 30),
    "салатовый":  (170, 210, 90),
    "зелёный":    (80, 150, 70),
    "тёмно-зелёный": (30, 80, 45),
    "изумрудный": (50, 130, 110),
    "хаки":       (130, 125, 80),
    "голубой":    (110, 170, 220),
    "светло-голубой": (180, 215, 240),
    "синий":      (40, 70, 160),
    "тёмно-синий": (25, 40, 100),
    "фиолетовый": (140, 70, 160),
    "сиреневый":  (200, 170, 220),
    "тёмно-серый": (70, 70, 70),
    "светло-серый": (200, 200, 200),
}

# цвет -> тёплый/холодный/нейтральный
TEMPERATURE = {
    "белый": "нейтральный", "чёрный": "нейтральный", "серый": "нейтральный",
    "светло-серый": "нейтральный", "тёмно-серый": "нейтральный",
    "бежевый": "тёплый", "тёмно-бежевый": "тёплый", "коричневый": "тёплый",
    "тёмно-коричневый": "тёплый", "красный": "тёплый", "бордовый": "тёплый",
    "розовый": "тёплый", "нежно-розовый": "тёплый", "оранжевый": "тёплый",
    "жёлтый": "тёплый", "тёмно-жёлтый": "тёплый", "салатовый": "тёплый",
    "хаки": "нейтральный", "зелёный": "холодный", "тёмно-зелёный": "холодный",
    "изумрудный": "холодный", "голубой": "холодный", "светло-голубой": "холодный",
    "синий": "холодный", "тёмно-синий": "холодный", "фиолетовый": "холодный",
    "сиреневый": "холодный",
}


def _parse(im):
    if Image is not None and isinstance(im, Image.Image):
        return im.convert("RGB")
    if isinstance(im, (bytes, bytearray)):
        return Image.open(io.BytesIO(im)).convert("RGB")
    return None


def _nearest(rgb):
    best, best_d = None, None
    for name, base in PALETTE.items():
        d = (rgb[0]-base[0])**2 + (rgb[1]-base[1])**2 + (rgb[2]-base[2])**2
        if best_d is None or d < best_d:
            best_d = d
            best = name
    return best


def _hsl(r, g, b):
    r, g, b = r/255.0, g/255.0, b/255.0
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) / 2.0
    if mx == mn:
        return 0.0, 0.0, l
    d = mx - mn
    s = d / (2.0 - mx - mn) if l > 0.5 else d / (mx + mn)
    if mx == r:
        h = (g - b) / d + (6 if g < b else 0)
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h/6.0, s, l


def _kmeans_colors(arr, k=5, iters=8, seed=1):
    """Простой k-means по цветам; возвращает список (rgb, доля)."""
    if np is None:
        return None
    pix = arr.reshape(-1, 3).astype(float)
    rng = np.random.RandomState(seed)
    idx = rng.choice(len(pix), size=min(k, len(pix)), replace=False)
    centers = pix[idx].copy()
    for _ in range(iters):
        dist = ((pix[:, None, :] - centers[None, :, :]) ** 2).sum(-1)
        labels = dist.argmin(-1)
        for c in range(len(centers)):
            m = pix[labels == c]
            if len(m):
                centers[c] = m.mean(0)
        if np.all(dist.min(-1).min() == 0):
            pass
    labels = ((pix[:, None, :] - centers[None, :, :]) ** 2).sum(-1).argmin(-1)
    tot = len(pix)
    comps = []
    for c in range(len(centers)):
        n = int((labels == c).sum())
        comps.append((tuple(int(x) for x in centers[c]), n / tot))
    comps.sort(key=lambda t: t[1], reverse=True)
    return comps


def _main_object_mask(arr):
    """Простой выделение объекта: пиксели, заметно отличающиеся от фона по краям."""
    if np is None:
        return None, None
    h, w, _ = arr.shape
    # фон = средний цвет рамки 10px по периметру
    margin = max(4, int(min(h, w) * 0.08))
    ring = np.vstack([arr[:margin].reshape(-1, 3),
                      arr[-margin:].reshape(-1, 3),
                      arr[:, :margin].reshape(-1, 3),
                      arr[:, -margin:].reshape(-1, 3)])
    bg = ring.mean(0)
    diff = np.abs(arr.astype(int) - bg.astype(int)).sum(-1)
    thresh = diff > 90
    # морфологический фильтр: оставить только крупные связные зоны
    mask = thresh
    return mask, bg


def _guess_type(arr, mask):
    """Грубая геометрическая догадка типа вещи. Возвращает (тип, уверенность)."""
    if np is None or mask is None:
        return None, 0
    h, w, _ = arr.shape
    if mask.sum() == 0:
        return None, 0
    ys, xs = np.where(mask)
    x0, x1 = xs.min(), xs.max()
    y0, y1 = ys.min(), ys.max()
    bw, bh = (x1 - x0), (y1 - y0)
    if bh == 0:
        return None, 0
    aspect = bw / bh          # <1 = высокий/узкий (платье, брюки), >1 = широкий (куртка)
    coverage = mask.mean()    # доля фрейма, занятая объектом

    # длина: объект занимает почти весь фрейм по вертикали?
    tall = bh > h * 0.7
    wide = bw > w * 0.7

    if tall and aspect < 0.55:
        return "похоже на длинную вещь (платье/пальто)", 0.5
    if aspect < 0.7 and tall:
        return "похоже на вертикальную вещь (брюки/платье/длинный верх)", 0.45
    if wide and aspect > 1.1:
        return "похоже на широкую/объёмную вещь (куртка/верхняя одежда)", 0.5
    if aspect < 0.8:
        return "вытянутый по вертикали силуэт", 0.3
    return None, 0


def extract_features(image_bytes):
    im = _parse(image_bytes)
    if im is None:
        return {"usable": False}
    small = im.resize((96, 96))
    arr = np.asarray(small).astype(int) if np is not None else None
    px = list(im.resize((64, 64)).getdata())

    # палитра: k-means с квантованием к словарю
    palette = []
    if arr is not None:
        comps = _kmeans_colors(np.asarray(small).astype(float), k=6)
        if comps:
            for rgb, share in comps:
                name = _nearest(rgb)
                if share >= 0.05:
                    palette.append({"name": name, "share": round(share, 3)})
    if not palette:
        cnt = Counter(_nearest(r) for r in px)
        tot = sum(cnt.values())
        palette = [{"name": n, "share": round(c/tot, 3)}
                   for n, c in cnt.most_common(6) if c/tot >= 0.05]

    # средние тон/светлота/насыщенность
    tot = len(px)
    sum_s = sum_l = 0.0
    for rgb in px:
        h, s, l = _hsl(*rgb)
        sum_s += s; sum_l += l
    av_l = sum_l / tot; av_s = sum_s / tot

    mask, _bg = _main_object_mask(arr) if arr is not None else (None, None)
    gtype, gconf = _guess_type(arr, mask) if arr is not None else (None, 0)

    # тёплый/холодный по доминирующему цвету
    dom = palette[0]["name"] if palette else None
    temp = TEMPERATURE.get(dom, "нейтральный")

    top_names = [p["name"] for p in palette[:3]]

    return {
        "usable": True,
        "palette": palette,
        "top_colors": top_names,
        "dominant": dom,
        "temperature": temp,
        "lightness": round(av_l, 2),
        "saturation": round(av_s, 2),
        "bright": av_l > 0.6,
        "dark": av_l < 0.32,
        "vivid": av_s > 0.45,
        "muted": av_s < 0.25,
        "guess": (gtype, gconf) if gtype else (None, 0),
    }


def describe(image_bytes):
    fx = extract_features(image_bytes)
    if not fx.get("usable"):
        return ""

    parts = []
    if fx.get("guess")[0]:
        parts.append(fx["guess"][0])

    if fx.get("dominant"):
        dom = fx["dominant"]
        if fx.get("dark"):
            parts.append(f"тёмное, в основном {dom}")
        elif fx.get("bright"):
            parts.append(f"светлое, в основном {dom}")
        elif fx.get("vivid"):
            parts.append(f"яркое, насыщенное — {dom}")
        elif fx.get("muted"):
            parts.append(f"спокойное, приглушённое — {dom}")
        else:
            parts.append(f"в гамме {dom}")
        temp = fx.get("temperature")
        if temp in ("тёплый", "холодный"):
            parts.append(f"{temp} оттенок")
    else:
        parts.append("тёмное" if fx.get("dark") else "светлое" if fx.get("bright") else "средней тональности")

    # дополнительные цвета (если их несколько заметных)
    extra = [p["name"] for p in fx.get("palette", [])[1:3] if p["share"] >= 0.15]
    if extra:
        parts.append("дополнительно: " + ", ".join(extra))

    return "По фото видно: " + "; ".join(parts) + "."
