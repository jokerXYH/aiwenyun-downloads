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
    'dark': '石墨夜色 · 薄荷绿',
    'dog': '奶油暖纸 · 暖黄',
}

# 语义色板。同一套键在三套主题里一一对应，切换时按角色换色。
THEMES = {
    'light': {
        'ACCENT': '#1f6b4e',
        'ACCENT_DARK': '#14513a',
        'ACCENT_SOFT': '#eaf2ed',
        'ON_ACCENT': '#f7fff9',
        'TEXT_DARK': '#1c1f1d',
        'TEXT_MUTED': '#5a615c',
        'TEXT_FAINT': '#737a75',
        'BG': '#f5f4f0',
        'CARD': '#ffffff',
        'SURFACE': '#f8f7f3',
        'SURFACE_ALT': '#efede7',
        'BORDER': '#e3e1da',
        'BORDER_STRONG': '#cfccc3',
        'GROUP_BG': '#eef3ee',
        'GROUP_MISC_BG': '#f2f1ec',
        'CHECKED_BG': '#e2f0e7',
        'WARN': '#a9761f',
        'DANGER': '#b34137',
        'DISABLED_BG': '#f3f2ee',
        'DISABLED_FG': '#a5a8a4',
        'ACCENT_DISABLED': '#b7cdc1',
        'ON_ACCENT_DISABLED': '#f5f8f5',
        'HOVER_STRONG': '#e6e4dd',
        'TRACK': '#e7e6e0',
        'TRACK_SOFT': '#eeede8',
        'SCROLL': '#d6d4cc',
        'TEXT_BODY': '#3a3f3c',
        'LINK': '#17573f',
        'PROGRESS': '#1f6b4e',
        'ACCENT_TEXT': '#0f4632',
        'CONTROL_BORDER': '#c4c1b8',
        'HERO_FROM': '#f5f4f0',
        'HERO_TO': '#f5f4f0',
        'HERO_TEXT': '#1c1f1d',
        'HERO_MUTED': '#5a615c',
        'HERO_ART': '#1f6b4e',
        'HERO_ART_SOFT': '#d6e4dc',
        'HERO_BTN_BG': '#ffffff',
        'HERO_BTN_FG': '#1c1f1d',
        'HERO_BTN_BORDER': '#cfccc3',
        'ICON_BG': '#1f6b4e',
        'ICON_FG': '#ffffff',
        'ICON_ACCENT': '#b9e3cd',
        'ICON_SHADOW': '#14513a',
    },
    'dark': {
        'ACCENT': '#4ec08c',
        'ACCENT_DARK': '#38a875',
        'ACCENT_SOFT': '#1c3128',
        'ON_ACCENT': '#0c1712',
        'TEXT_DARK': '#ededed',
        'TEXT_MUTED': '#a1a1a6',
        'TEXT_FAINT': '#85858b',
        'BG': '#141415',
        'CARD': '#1a1a1c',
        'SURFACE': '#202022',
        'SURFACE_ALT': '#28282b',
        'BORDER': '#2b2b2f',
        'BORDER_STRONG': '#3a3a40',
        'GROUP_BG': '#1f2623',
        'GROUP_MISC_BG': '#1c1c1e',
        'CHECKED_BG': '#1e362c',
        'WARN': '#e0b25b',
        'DANGER': '#e06c65',
        'DISABLED_BG': '#1d1d20',
        'DISABLED_FG': '#68686f',
        'ACCENT_DISABLED': '#2c5442',
        'ON_ACCENT_DISABLED': '#9db3a7',
        'HOVER_STRONG': '#303034',
        'TRACK': '#27272b',
        'TRACK_SOFT': '#202023',
        'SCROLL': '#3a3a40',
        'TEXT_BODY': '#c8c8cc',
        'LINK': '#6fd6a4',
        'PROGRESS': '#4ec08c',
        'ACCENT_TEXT': '#79dfad',
        'CONTROL_BORDER': '#45454c',
        'HERO_FROM': '#141415',
        'HERO_TO': '#141415',
        'HERO_TEXT': '#ededed',
        'HERO_MUTED': '#a1a1a6',
        'HERO_ART': '#4ec08c',
        'HERO_ART_SOFT': '#273a31',
        'HERO_BTN_BG': '#202022',
        'HERO_BTN_FG': '#ededed',
        'HERO_BTN_BORDER': '#3a3a40',
        'ICON_BG': '#1c3128',
        'ICON_FG': '#ededed',
        'ICON_ACCENT': '#4ec08c',
        'ICON_SHADOW': '#101012',
    },
    'dog': {
        'ACCENT': '#e3a23a',
        'ACCENT_DARK': '#c78520',
        'ACCENT_SOFT': '#faecd0',
        'ON_ACCENT': '#262119',
        'TEXT_DARK': '#262119',
        'TEXT_MUTED': '#665d51',
        'TEXT_FAINT': '#7d7263',
        'BG': '#faf6ee',
        'CARD': '#fffefa',
        'SURFACE': '#f5eedf',
        'SURFACE_ALT': '#ede3cf',
        'BORDER': '#e4d8c3',
        'BORDER_STRONG': '#cfc0a7',
        'GROUP_BG': '#f8ecd5',
        'GROUP_MISC_BG': '#f2eadb',
        'CHECKED_BG': '#f7e1ad',
        'WARN': '#b57416',
        'DANGER': '#b5523f',
        'DISABLED_BG': '#f0e8da',
        'DISABLED_FG': '#a59a89',
        'ACCENT_DISABLED': '#e8d4ad',
        'ON_ACCENT_DISABLED': '#857c70',
        'HOVER_STRONG': '#e7dac4',
        'TRACK': '#e7dcc8',
        'TRACK_SOFT': '#f0e7d6',
        'SCROLL': '#d4c5ab',
        'TEXT_BODY': '#443d33',
        'LINK': '#8f560a',
        'PROGRESS': '#c78520',
        'ACCENT_TEXT': '#8c530b',
        'CONTROL_BORDER': '#b8a68e',
        'HERO_FROM': '#fff0ce',
        'HERO_TO': '#fff0ce',
        'HERO_TEXT': '#262119',
        'HERO_MUTED': '#665d51',
        'HERO_ART': '#c78520',
        'HERO_ART_SOFT': '#e8d0a3',
        'HERO_BTN_BG': '#fffdf8',
        'HERO_BTN_FG': '#262119',
        'HERO_BTN_BORDER': '#cfc0a7',
        'ICON_BG': '#f5eedf',
        'ICON_FG': '#262119',
        'ICON_ACCENT': '#e3a23a',
        'ICON_SHADOW': '#cfc0a7',
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
    except (OSError, ValueError, Image.DecompressionBombError):
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
    """返回 PIL RGB 图；安静的纯色顶栏 + 1px 底部分隔线，无光晕或装饰贴纸。"""

    if Image is None or ImageDraw is None:
        return None
    key = theme_key(key)
    palette = THEMES[key]
    width = max(320, int(width))
    height = max(64, int(height))
    scale = max(1, int(supersample))
    w, h = width * scale, height * scale
    image = Image.new('RGB', (w, h), _hex(palette['HERO_FROM']))
    draw = ImageDraw.Draw(image)
    # 仅保留一条 1px 安静分隔线，不再叠加渐变、光晕、高光或装饰贴纸
    draw.line((0, h - scale, w, h - scale),
              fill=_hex(palette['BORDER']), width=scale)
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


def render_settings_icon(key: str, size: int = 16, color: str | None = None):
    """设置入口的小图标：三套主题统一使用简洁齿轮线图标。"""

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
    cx = cy = s * 0.5
    outer = s * 0.34
    inner = s * 0.20
    width = max(2, int(s * 0.075))
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

def render_theme_preview(key: str, width: int = 144, height: int = 88):
    if Image is None or ImageDraw is None:
        return None
    key = theme_key(key)
    palette = THEMES[key]
    width, height = max(80, int(width)), max(50, int(height))
    scale = 3
    w, h = width * scale, height * scale
    image = Image.new('RGBA', (w, h), palette['BG'] + 'ff')
    draw = ImageDraw.Draw(image)

    # 顶部纯色顶栏 + 1px 底部分隔线
    hero_h = int(h * 0.34)
    draw.rectangle((0, 0, w, hero_h), fill=palette['HERO_FROM'])
    draw.line((0, hero_h, w, hero_h), fill=palette['BORDER'],
              width=max(1, scale))

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
