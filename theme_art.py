"""主题配色与界面美术素材。

三套主题共用一套语义色板：界面只认 ACCENT / BG / CARD 之类的角色，
切换主题时由 GUI 把旧颜色按角色换成新颜色。这里同时负责画启动横幅、
窗口图标、设置入口小图标和主题预览图。

线条小狗角色图不再代码绘制：只从用户提供的
assets/user_provided/original/ 读取，缺失时退回纯文字/通用图形。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageOps
except ImportError:  # pragma: no cover  打包缺 Pillow 时界面退回纯色
    Image = None
    ImageDraw = None
    ImageFilter = None
    ImageOps = None


THEME_ORDER = ('light', 'dark', 'dog')
THEME_LABELS = {
    'light': '浅色',
    'dark': '深色',
    'dog': '线条小狗',
}
THEME_DESCRIPTIONS = {
    'light': '纸张暖白 · 森林绿',
    'dark': '墨绿夜色 · 薄荷绿',
    'dog': '奶油手绘 · 白狗与暖黄',
}

# 语义色板。同一套键在三套主题里一一对应，切换时按角色换色。
THEMES = {
    'light': {
        'ACCENT': '#1f6b4e',
        'ACCENT_DARK': '#14513a',
        'ACCENT_SOFT': '#eaf2ed',
        'ON_ACCENT': '#f7fff9',
        'TEXT_DARK': '#1b241f',
        'TEXT_MUTED': '#5f6b63',
        'TEXT_FAINT': '#6b776e',
        'BG': '#f3f4f0',
        'CARD': '#ffffff',
        'SURFACE': '#f7f8f4',
        'SURFACE_ALT': '#eef1eb',
        'BORDER': '#e1e5de',
        'BORDER_STRONG': '#cfd6ce',
        'GROUP_BG': '#edf3ed',
        'GROUP_MISC_BG': '#f2f3f0',
        'CHECKED_BG': '#e1efe6',
        'WARN': '#a9761f',
        'DANGER': '#a8443a',
        'DISABLED_BG': '#f4f5f2',
        'DISABLED_FG': '#a8b0a9',
        'ACCENT_DISABLED': '#b7cdc1',
        'ON_ACCENT_DISABLED': '#f5f8f5',
        'HOVER_STRONG': '#e5eae2',
        'TRACK': '#e8ece5',
        'TRACK_SOFT': '#edf0e9',
        'SCROLL': '#d7ddd5',
        'TEXT_BODY': '#3d484f',
        'LINK': '#17573f',
        'PROGRESS': '#1f6b4e',
        'ACCENT_TEXT': '#0f4632',
        'CONTROL_BORDER': '#c4ccc4',
        'HERO_FROM': '#f7f8f3',
        'HERO_TO': '#e6efe7',
        'HERO_TEXT': '#1b241f',
        'HERO_MUTED': '#5f6b63',
        'HERO_ART': '#2b8361',
        'HERO_ART_SOFT': '#c7ded0',
        'HERO_BTN_BG': '#fbfdf9',
        'HERO_BTN_FG': '#1b241f',
        'HERO_BTN_BORDER': '#cfd6ce',
        'ICON_BG': '#1f6b4e',
        'ICON_FG': '#ffffff',
        'ICON_ACCENT': '#b9e3cd',
        'ICON_SHADOW': '#14513a',
    },
    'dark': {
        'ACCENT': '#4ec08c',
        'ACCENT_DARK': '#38a875',
        'ACCENT_SOFT': '#203a2e',
        'ON_ACCENT': '#07140e',
        'TEXT_DARK': '#e8f1eb',
        'TEXT_MUTED': '#a7b8ad',
        'TEXT_FAINT': '#7f9388',
        'BG': '#101614',
        'CARD': '#17201c',
        'SURFACE': '#1b2621',
        'SURFACE_ALT': '#223029',
        'BORDER': '#27362e',
        'BORDER_STRONG': '#354a3f',
        'GROUP_BG': '#1e2f27',
        'GROUP_MISC_BG': '#1a241f',
        'CHECKED_BG': '#234133',
        'WARN': '#e0b25b',
        'DANGER': '#e07b70',
        'DISABLED_BG': '#202a25',
        'DISABLED_FG': '#66776d',
        'ACCENT_DISABLED': '#2f5d47',
        'ON_ACCENT_DISABLED': '#9db3a7',
        'HOVER_STRONG': '#2b3d33',
        'TRACK': '#26342c',
        'TRACK_SOFT': '#1f2b24',
        'SCROLL': '#33453b',
        'TEXT_BODY': '#c6d4cb',
        'LINK': '#6fd6a4',
        'PROGRESS': '#4ec08c',
        'ACCENT_TEXT': '#79dfad',
        'CONTROL_BORDER': '#40564a',
        'HERO_FROM': '#101a16',
        'HERO_TO': '#1c2e25',
        'HERO_TEXT': '#f0f7f2',
        'HERO_MUTED': '#a9b9ae',
        'HERO_ART': '#5fd29b',
        'HERO_ART_SOFT': '#2b4a3b',
        'HERO_BTN_BG': '#223029',
        'HERO_BTN_FG': '#e8f1eb',
        'HERO_BTN_BORDER': '#3b5145',
        'ICON_BG': '#0f1a15',
        'ICON_FG': '#d8f5e5',
        'ICON_ACCENT': '#4ec08c',
        'ICON_SHADOW': '#000000',
    },
    'dog': {
        'ACCENT': '#f0b44a',
        'ACCENT_DARK': '#d9942b',
        'ACCENT_SOFT': '#fff1cf',
        'ON_ACCENT': '#2b251c',
        'TEXT_DARK': '#2b2b2b',
        'TEXT_MUTED': '#6f675c',
        'TEXT_FAINT': '#7b6f5e',
        'BG': '#fdf6e9',
        'CARD': '#fffdf8',
        'SURFACE': '#fff9ee',
        'SURFACE_ALT': '#f7ecd9',
        'BORDER': '#e7d8c2',
        'BORDER_STRONG': '#c9b08b',
        'GROUP_BG': '#fff0d4',
        'GROUP_MISC_BG': '#f7efdf',
        'CHECKED_BG': '#ffedbe',
        'WARN': '#b97716',
        'DANGER': '#b5523f',
        'DISABLED_BG': '#f2eadb',
        'DISABLED_FG': '#a89d8c',
        'ACCENT_DISABLED': '#f0dcae',
        'ON_ACCENT_DISABLED': '#8d8478',
        'HOVER_STRONG': '#f1e2c9',
        'TRACK': '#eadfca',
        'TRACK_SOFT': '#f4ecdd',
        'SCROLL': '#d9c8ad',
        'TEXT_BODY': '#4a4338',
        'LINK': '#8f560a',
        'PROGRESS': '#d9942b',
        'ACCENT_TEXT': '#955a0c',
        'CONTROL_BORDER': '#242424',
        'HERO_FROM': '#fffaf0',
        'HERO_TO': '#ffefd4',
        'HERO_TEXT': '#2b2b2b',
        'HERO_MUTED': '#7a6a54',
        'HERO_ART': '#c68f2e',
        'HERO_ART_SOFT': '#e8c98e',
        'HERO_BTN_BG': '#fff8ea',
        'HERO_BTN_FG': '#2b2b2b',
        'HERO_BTN_BORDER': '#2b2b2b',
        'ICON_BG': '#fffaf0',
        'ICON_FG': '#2b2b2b',
        'ICON_ACCENT': '#f0b44a',
        'ICON_SHADOW': '#e8d7bd',
    },
}

# 旧版本里写死在控件上的颜色，也归到语义角色，切换主题时一并替换。
LEGACY_COLORS = {
    '#e8ece5': 'TRACK',
    '#e6eae3': 'TRACK',
    '#edf0e9': 'TRACK_SOFT',
    '#d7ddd5': 'SCROLL',
    '#f4f5f2': 'DISABLED_BG',
    '#a8b0a9': 'DISABLED_FG',
    '#b4bcb5': 'DISABLED_FG',
    '#b7cdc1': 'ACCENT_DISABLED',
    '#f5f8f5': 'ON_ACCENT_DISABLED',
    '#e5eae2': 'HOVER_STRONG',
    '#3d484f': 'TEXT_BODY',
    '#f2f3f0': 'GROUP_MISC_BG',
}

# 粗体强调色（线条小狗主题使用墨线，而不是把黄色压在白字上）。
PRIMARY_TEXT_ON_ACCENT = {key: value['ON_ACCENT'] for key, value in THEMES.items()}


def palette_for(key: str) -> dict:
    return dict(THEMES.get(key) or THEMES['light'])


def theme_key(key: str | None) -> str:
    return key if key in THEMES else 'light'


USER_ASSET_DIR = 'assets/user_provided/original'


def user_asset_path(name: str) -> Path:
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return base / 'assets' / 'user_provided' / 'original' / name


def load_user_asset(name: str):
    """只读加载用户提供的原图；不写回、不重绘、不生成替代图。"""

    if Image is None:
        return None
    try:
        with Image.open(user_asset_path(name)) as source:
            return source.convert('RGBA')
    except (OSError, ValueError):
        return None


def render_user_asset(name: str, size, background=None, crop=None):
    """按 contain 缩放用户原图；可选裁掉纯留白，原文件保持不变。"""

    if Image is None or ImageOps is None:
        return None
    image = load_user_asset(name)
    if image is None:
        return None
    if crop is not None and len(crop) == 4:
        width, height = image.size
        left, top, right, bottom = crop
        image = image.crop((int(left * width), int(top * height),
                            int(right * width), int(bottom * height)))
    target = (max(1, int(size[0])), max(1, int(size[1])))
    image = ImageOps.contain(image, target, Image.Resampling.LANCZOS)
    if background is None:
        return image
    if isinstance(background, str):
        fill = _hex(background) + (255,)
    else:
        fill = background
    canvas = Image.new('RGBA', target, fill)
    canvas.alpha_composite(image, ((target[0] - image.width) // 2,
                                   (target[1] - image.height) // 2))
    return canvas


def color_role(value) -> str | None:
    """把任意一个主题色值反查成语义角色。"""

    raw = str(value or '').strip().lower()
    if not raw:
        return None
    for palette in THEMES.values():
        for role, color in palette.items():
            if color.lower() == raw:
                return role
    return LEGACY_COLORS.get(raw)


# ---------------------------------------------------------------------------
# 基础绘图工具
# ---------------------------------------------------------------------------

def _hex(value: str) -> tuple[int, int, int]:
    value = value.lstrip('#')
    return (int(value[0:2], 16), int(value[2:4], 16),
            int(value[4:6], 16))


def _mix(left: str, right: str, ratio: float) -> tuple[int, int, int]:
    a = _hex(left)
    b = _hex(right)
    ratio = max(0.0, min(1.0, ratio))
    return tuple(int(round(a[i] + (b[i] - a[i]) * ratio)) for i in range(3))


def _rgba(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    return _hex(value) + (alpha,)


def _vertical_gradient(size, top: str, bottom: str):
    width, height = size
    image = Image.new('RGB', (1, max(1, height)))
    draw = ImageDraw.Draw(image)
    for y in range(max(1, height)):
        draw.point((0, y), fill=_mix(top, bottom,
                                      y / max(1, height - 1)))
    return image.resize((width, height))


def _rounded(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill,
                           outline=outline, width=width)


def _star_points(cx, cy, outer, inner, points=5, rotation=-math.pi / 2):
    coords = []
    for index in range(points * 2):
        angle = rotation + index * math.pi / points
        radius = outer if index % 2 == 0 else inner
        coords.append((cx + math.cos(angle) * radius,
                       cy + math.sin(angle) * radius))
    return coords


def _draw_star(draw, cx, cy, outer, fill, outline=None, width=1):
    draw.polygon(_star_points(cx, cy, outer, outer * 0.45),
                 fill=fill, outline=outline, width=width)


def _draw_paw(draw, cx, cy, size, fill, outline=None, width=1):
    """一个简笔爪印：大掌垫 + 三颗脚趾。"""

    pad_w = size * 0.52
    pad_h = size * 0.42
    draw.ellipse((cx - pad_w / 2, cy - pad_h * 0.15,
                  cx + pad_w / 2, cy + pad_h * 0.85),
                 fill=fill, outline=outline, width=width)
    toe = size * 0.17
    for dx, dy in ((-0.34, -0.46), (0.0, -0.62), (0.34, -0.46)):
        tx = cx + size * dx
        ty = cy + size * dy
        draw.ellipse((tx - toe / 2, ty - toe / 2,
                      tx + toe / 2, ty + toe / 2),
                     fill=fill, outline=outline, width=width)


def _draw_leaf(draw, cx, cy, length, angle, color, width):
    """两段弧线组成的叶子，用来做低调的纸面装饰。"""

    points = []
    for index in range(25):
        t = index / 24.0
        x = (t - 0.5) * length
        y = math.sin(t * math.pi) * length * 0.28
        points.append((x, y))
    lower = [(-x, -y) for x, y in reversed(points)]
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    def rotate(point):
        x, y = point
        return (cx + x * cos_a - y * sin_a,
                cy + x * sin_a + y * cos_a)

    upper = [rotate(point) for point in points]
    down = [rotate(point) for point in lower]
    draw.line(upper, fill=color, width=width, joint='curve')
    draw.line(down, fill=color, width=width, joint='curve')
    draw.line([rotate((0, 0)), rotate((length * 0.48, 0))],
              fill=color, width=max(1, width - 1))


# ---------------------------------------------------------------------------
# 线条小狗吉祥物
# ---------------------------------------------------------------------------

# 狗图改由用户提供的素材加载，不再代码绘制。

# ---------------------------------------------------------------------------
# 启动横幅
# ---------------------------------------------------------------------------

def render_hero(key: str, width: int, height: int, supersample: int = 2):
    """返回 PIL RGB 图；宽度不足时返回 None 由界面退回纯色。"""

    if Image is None or ImageDraw is None:
        return None
    key = theme_key(key)
    palette = THEMES[key]
    width = max(320, int(width))
    height = max(64, int(height))
    scale = max(1, int(supersample))
    w, h = width * scale, height * scale
    image = _vertical_gradient((w, h), palette['HERO_FROM'],
                               palette['HERO_TO'])
    art = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(art)
    art_color = palette['HERO_ART']
    soft = palette['HERO_ART_SOFT']

    if key == 'light':
        # 右侧一团柔和的日光 + 叶子线稿
        glow = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse((w * 0.68, -h * 0.55, w * 1.02, h * 0.95),
                   fill=_rgba(art_color, 26))
        glow = glow.filter(ImageFilter.GaussianBlur(18 * scale))
        art = Image.alpha_composite(art, glow)
        draw = ImageDraw.Draw(art)
        for i, (cx, cy, length, angle, alpha) in enumerate((
                (0.80, 0.62, 0.20, -0.55, 120),
                (0.88, 0.30, 0.15, 0.55, 90),
                (0.72, 0.22, 0.11, 1.10, 80))):
            _draw_leaf(draw, w * cx, h * cy, w * length, angle,
                       _rgba(art_color, alpha), max(2, 2 * scale))
        for gx in range(int(w * 0.50), int(w * 0.94), 26 * scale):
            for gy in range(int(h * 0.18), int(h * 0.86), 22 * scale):
                draw.ellipse((gx, gy, gx + 2 * scale, gy + 2 * scale),
                             fill=_rgba(art_color, 45))
    elif key == 'dark':
        # 月亮、星点与低对比的等高线
        glow = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse((w * 0.72, -h * 0.65, w * 1.02, h * 0.75),
                   fill=_rgba(art_color, 30))
        glow = glow.filter(ImageFilter.GaussianBlur(24 * scale))
        art = Image.alpha_composite(art, glow)
        draw = ImageDraw.Draw(art)
        moon_x, moon_y, moon_r = w * 0.86, h * 0.35, h * 0.20
        draw.ellipse((moon_x - moon_r, moon_y - moon_r,
                      moon_x + moon_r, moon_y + moon_r),
                     fill=_rgba('#e8f1eb', 210))
        draw.ellipse((moon_x - moon_r - h * 0.06,
                      moon_y - moon_r - h * 0.05,
                      moon_x + moon_r * 0.45,
                      moon_y + moon_r * 0.72),
                     fill=(0, 0, 0, 0))
        for i, (fx, fy, radius) in enumerate((
                (0.66, 0.26, 1.6), (0.72, 0.62, 1.0),
                (0.79, 0.48, 1.3), (0.93, 0.68, 1.8),
                (0.58, 0.72, 1.0), (0.90, 0.18, 1.1))):
            r = radius * scale
            draw.ellipse((w * fx - r, h * fy - r,
                          w * fx + r, h * fy + r),
                         fill=_rgba('#d8f5e5', 190))
        for offset, alpha in ((0.0, 70), (0.10, 45), (0.20, 28)):
            draw.arc((w * 0.62, h * (0.12 + offset),
                      w * 1.12, h * (0.92 + offset)),
                     start=190, end=330,
                     fill=_rgba(art_color, alpha), width=2 * scale)
    else:
        # 奶油纸、淡格线、爪印与星星
        for gx in range(0, w, 30 * scale):
            draw.line((gx, 0, gx, h), fill=_rgba(art_color, 16),
                      width=1 * scale)
        for gy in range(0, h, 30 * scale):
            draw.line((0, gy, w, gy), fill=_rgba(art_color, 16),
                      width=1 * scale)
        for cx, cy, size, alpha in (
                (0.34, 0.20, 0.06, 70), (0.48, 0.78, 0.045, 55),
                (0.60, 0.16, 0.04, 45), (0.07, 0.74, 0.05, 45)):
            _draw_paw(draw, w * cx, h * cy, w * size,
                      _rgba(art_color, alpha))
        for cx, cy, radius, alpha in (
                (0.30, 0.62, 0.035, 90), (0.56, 0.36, 0.022, 90),
                (0.64, 0.70, 0.030, 80)):
            _draw_star(draw, w * cx, h * cy, w * radius,
                       _rgba(art_color, alpha))
        # 右上角的狗图案按反馈移除；这里只保留纸纹、爪印和星星。
        # 一条手绘波浪线，压住横幅下沿
        points = []
        for index in range(0, 101):
            px = w * (0.02 + 0.78 * index / 100.0)
            py = h * 0.94 + math.sin(index * 0.42) * h * 0.018
            points.append((px, py))
        draw.line(points, fill=_rgba(art_color, 120),
                  width=2 * scale, joint='curve')

    # 顶部细高光 + 底部 2px 主题线
    draw.line((0, 0, w, 0), fill=_rgba('#ffffff', 30),
              width=1 * scale)
    draw.line((0, h - 2 * scale, w, h - 2 * scale),
              fill=_rgba(art_color, 120), width=2 * scale)
    image = Image.alpha_composite(image.convert('RGBA'), art).convert('RGB')
    if scale > 1:
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    return image


# ---------------------------------------------------------------------------
# 图标
# ---------------------------------------------------------------------------

def render_app_icon(key: str, size: int = 64):
    """应用图标：三套主题共用书页与勾，避免在 16px 下放狗头。"""

    if Image is None or ImageDraw is None:
        return None
    key = theme_key(key)
    palette = THEMES[key]
    size = max(16, int(size))
    scale = 4
    s = size * scale
    image = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = s * 0.24 if key != 'dog' else s * 0.28
    background = palette['ICON_BG']
    outline = palette['ICON_SHADOW'] if key != 'dog' else palette['ICON_FG']
    _rounded(draw, (s * 0.035, s * 0.035, s * 0.965, s * 0.965),
             radius, fill=background, outline=outline,
             width=max(1, int(s * 0.018)))

    if True:
        # 卡片/书页 + 勾
        card = (s * 0.22, s * 0.20, s * 0.78, s * 0.80)
        _rounded(draw, card, s * 0.08, fill=palette['ICON_FG'],
                 outline=None)
        line_color = background
        for index, ratio in enumerate((0.34, 0.45, 0.56)):
            draw.rounded_rectangle(
                (s * 0.31, s * ratio, s * (0.69 if index < 2 else 0.58),
                 s * (ratio + 0.035)),
                radius=s * 0.018, fill=_rgba(background, 90))
        draw.line((s * 0.31, s * 0.64, s * 0.42, s * 0.73,
                   s * 0.70, s * 0.42), fill=line_color,
                  width=max(2, int(s * 0.055)), joint='curve')
        # 一枚小叶子点缀
        _draw_leaf(draw, s * 0.74, s * 0.17, s * 0.24, -0.5,
                   palette['ICON_ACCENT'], max(2, int(s * 0.020)))
    image = image.resize((size, size), Image.Resampling.LANCZOS)
    return image


def render_settings_icon(key: str, size: int = 18, color: str | None = None):
    """设置入口的小图标：浅/深色是齿轮，线条小狗是爪印。"""

    if Image is None or ImageDraw is None:
        return None
    key = theme_key(key)
    palette = THEMES[key]
    size = max(12, int(size))
    scale = 4
    s = size * scale
    image = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    ink = color or palette['HERO_BTN_FG']
    if key == 'dog':
        _draw_paw(draw, s * 0.5, s * 0.56, s * 0.74,
                  None, ink, max(2, int(s * 0.075)))
    else:
        cx = cy = s * 0.5
        outer = s * 0.33
        inner = s * 0.20
        width = max(2, int(s * 0.065))
        draw.ellipse((cx - inner, cy - inner, cx + inner, cy + inner),
                     outline=ink, width=width)
        for index in range(8):
            angle = index * math.pi / 4
            x1 = cx + math.cos(angle) * (inner + s * 0.04)
            y1 = cy + math.sin(angle) * (inner + s * 0.04)
            x2 = cx + math.cos(angle) * outer
            y2 = cy + math.sin(angle) * outer
            draw.line((x1, y1, x2, y2), fill=ink, width=width)
        dot = s * 0.035
        draw.ellipse((cx - dot, cy - dot, cx + dot, cy + dot), fill=ink)
    return image.resize((size, size), Image.Resampling.LANCZOS)


# ---------------------------------------------------------------------------
# 设置里的主题预览卡片
# ---------------------------------------------------------------------------

def render_theme_preview(key: str, width: int = 144, height: int = 86):
    if Image is None or ImageDraw is None:
        return None
    key = theme_key(key)
    palette = THEMES[key]
    width, height = max(80, int(width)), max(50, int(height))
    scale = 3
    w, h = width * scale, height * scale
    image = Image.new('RGBA', (w, h), palette['BG'] + 'ff')
    draw = ImageDraw.Draw(image)

    # 顶部横幅
    hero_box = (0, 0, w, h * 0.34)
    for y in range(int(hero_box[1]), int(hero_box[3])):
        draw.line((0, y, w, y),
                  fill=_mix(palette['HERO_FROM'], palette['HERO_TO'],
                            y / max(1, hero_box[3])))
    if key == 'dog':
        _draw_paw(draw, w * 0.20, h * 0.25, w * 0.08,
                  _rgba(palette['HERO_ART'], 80))
    elif key == 'dark':
        for fx, fy, r in ((0.62, 0.14, 1.2), (0.78, 0.22, 0.8),
                          (0.88, 0.10, 1.4)):
            draw.ellipse((w * fx - r * scale, h * fy - r * scale,
                          w * fx + r * scale, h * fy + r * scale),
                         fill=_rgba('#d8f5e5', 210))
        draw.ellipse((w * 0.86 - h * 0.10, h * 0.12 - h * 0.10,
                      w * 0.86 + h * 0.10, h * 0.12 + h * 0.10),
                     fill='#f0f7f2')
    else:
        _draw_leaf(draw, w * 0.82, h * 0.22, w * 0.18, -0.4,
                   _rgba(palette['HERO_ART'], 150), scale * 2)

    # 小图标 + 标题条
    _rounded(draw, (w * 0.05, h * 0.09, w * 0.14, h * 0.27),
             h * 0.04, fill=palette['ICON_BG'],
             outline=palette['HERO_BTN_BORDER'], width=max(1, scale))
    draw.rounded_rectangle((w * 0.18, h * 0.12, w * 0.46, h * 0.16),
                           radius=scale, fill=palette['HERO_TEXT'])
    draw.rounded_rectangle((w * 0.18, h * 0.20, w * 0.36, h * 0.235),
                           radius=scale, fill=_rgba(palette['HERO_MUTED'],
                                                    190))

    # 主内容卡片
    card = (w * 0.05, h * 0.42, w * 0.95, h * 0.92)
    _rounded(draw, card, h * 0.05, fill=palette['CARD'],
             outline=palette['BORDER'], width=max(1, scale))
    for index, width_ratio in enumerate((0.55, 0.68, 0.34)):
        y = h * (0.53 + index * 0.09)
        draw.rounded_rectangle(
            (w * 0.10, y, w * (0.10 + width_ratio * 0.72), y + h * 0.035),
            radius=scale, fill=_rgba(palette['TEXT_MUTED'], 150))
    _rounded(draw, (w * 0.68, h * 0.72, w * 0.90, h * 0.84),
             h * 0.035, fill=palette['ACCENT'])
    # 勾选块
    draw.rounded_rectangle((w * 0.10, h * 0.72, w * 0.145, h * 0.765),
                           radius=scale, fill=palette['CHECKED_BG'],
                           outline=palette['ACCENT'], width=max(1, scale))

    image = image.convert('RGB').resize((width, height),
                                        Image.Resampling.LANCZOS)
    return image
