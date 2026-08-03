"""ClaudeQuota — 桌面上隨時看得到 Claude 用量。

系統列圖示顯示已用 %，點一下開／關可拖曳的置頂卡片。
卡片右上角的 ✕ 只是把它收起來，點系統列圖示就能再叫出來；要完全結束用系統列選單。

兩個畫質關鍵：
1. 宣告 DPI 感知，否則 Windows 會把整個視窗點陣拉伸（本機 175% 縮放 → 糊掉）。
   宣告之後 tkinter 的座標就是實體像素，卡片也照 DPI 倍率放大來畫。
2. 用 Pillow 以 SS 倍超取樣再降採樣。tkinter 的 Canvas 沒有反鋸齒，
   圓角、圖標、走勢線的邊緣都會是鋸齒狀，怎麼調都不會精緻。

資料只認 Claude 桌面版快取的官方數字，讀不到就明說讀不到（見 quota_source.py）。
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont, ImageTk

import quota_source

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(APP_DIR, "widget_state.json")
# 讀檔有快取（檔案沒變就不重解析），一次 tick 幾乎沒有成本；畫面也只在內容真的變了
# 才重繪。真正的資料源頭是桌面版每 5 分鐘寫一次檔，所以再短也不會更即時，
# 縮短只是讓倒數與過期提示跟得上。
REFRESH_SEC = 10

# 沒有進行中的視窗時，自動打一次最小呼叫把它點著。冷卻時間要大於桌面版的
# 5 分鐘取樣間隔，否則會在快取更新前重複觸發。
AUTO_POKE_COOLDOWN_MIN = 12

# 這個顏色會被設成透明，用來裁出圓角（tkinter 沒有原生圓角視窗）
CHROMA = "#010203"
# 整個視窗的不透明度，做出 Windows 通知那種淡淡的透明感
WINDOW_ALPHA = 0.95
# 繪製時的超取樣倍率，最後縮回去換到反鋸齒
SS = 3

# Windows 11 通知（淺色）的配色
BG = (250, 250, 250)
BG_LOW = (243, 243, 243)   # 卡片下半的極淡漸層，避免整片死白
EDGE = (224, 224, 224)
HAIR = (236, 236, 236)
TRACK = (231, 231, 231)
TXT = (26, 26, 26)
SUB = (56, 56, 62)      # 次要文字
FAINT = (84, 84, 92)     # 說明文字
HOVER = (232, 232, 232)
CLAUDE_ORANGE = (217, 119, 87)
WARN_RED = (198, 64, 58)

# 5 小時額度：整條都在暖色系（呼應 Claude 的橘），用得越多越往紅走。
# 不用「低用量就變藍」是因為那樣會跟每週額度的藍撞色，也讓這一列每次長得不一樣。
GRAD_CALM = ((240, 176, 96), (223, 126, 84))   # 琥珀 → 珊瑚橘
GRAD_WARN = ((233, 152, 62), (215, 98, 66))    # 橘 → 深橘
GRAD_HIGH = ((225, 110, 78), (196, 46, 52))    # 橘紅 → 深紅

# 每週額度固定用靛藍 → 天藍，不隨用量變色。
# 它不是警戒指標，換成自己的色系才不會跟 5 小時的紅黃綠搶讀。
GRAD_WEEK = ((72, 92, 190), (80, 160, 225))
WEEK_ACCENT = (78, 120, 205)

FONTS = {
    "en": r"C:\Windows\Fonts\segoeui.ttf",
    "en_sb": r"C:\Windows\Fonts\seguisb.ttf",
    "zh": r"C:\Windows\Fonts\msjh.ttc",       # 微軟正黑體：Segoe UI 沒有中文 glyph
    "zh_l": r"C:\Windows\Fonts\msjhl.ttc",
    "icon": r"C:\Windows\Fonts\SegoeIcons.ttf",  # Segoe Fluent Icons，Windows 11 的系統圖示字型
}
_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

# Segoe Fluent Icons 的碼位（Unicode 私用區，不是一般字元）
ICON_CLOSE = "\ue8bb"    # ChromeClose，Windows 通知右上角的 ✕
ICON_MIN = "\ue921"      # ChromeMinimize\uff0c\u7e2e\u6210\u8ff7\u4f60\u6a6b\u689d
ICON_REFRESH = "\ue72c"  # Refresh
ICON_BOLT = "\ue945"     # LightningBolt，用來手動開啟 5 小時視窗


# Claude 官方 app icon。安裝時從桌面版的 MSIX 套件複製過來（見 README），
# 手繪的放射狀標記怎麼畫都比不上原版。
LOGO_PATH = os.path.join(APP_DIR, "claude_logo.png")
_logo_cache: dict[int, Image.Image] = {}


def _extract_logo() -> bool:
    """從已安裝的 Claude 桌面版 MSIX 套件裡把 app icon 複製出來。"""
    import glob as _glob
    import shutil as _shutil

    pattern = os.path.join(
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        "WindowsApps", "Claude_*", "assets", "Square150x150Logo*.png",
    )
    for src in sorted(_glob.glob(pattern), reverse=True):  # 版本新的排前面
        try:
            _shutil.copyfile(src, LOGO_PATH)
            return True
        except OSError:
            continue
    return False


def claude_logo(size: int) -> Image.Image | None:
    size = int(size)
    if size not in _logo_cache:
        if not os.path.isfile(LOGO_PATH) and not _extract_logo():
            return None
        try:
            img = Image.open(LOGO_PATH).convert("RGBA")
        except (OSError, ValueError):
            return None
        _logo_cache[size] = img.resize((size, size), Image.LANCZOS)
    return _logo_cache[size]


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    key = (kind, int(size))
    if key not in _font_cache:
        try:
            _font_cache[key] = ImageFont.truetype(FONTS[kind], int(size))
        except OSError:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def grad_for(pct_used: float):
    return GRAD_CALM if pct_used < 50 else GRAD_WARN if pct_used < 80 else GRAD_HIGH


def accent_for(pct_used: float):
    """數字用的主色——比純黑有生氣，也讓狀態一眼可辨。"""
    return grad_for(pct_used)[1]


def solid_for(pct_used: float) -> str:
    return "#4a90e2" if pct_used < 50 else "#e9a83f" if pct_used < 80 else "#cd4e48"


def enable_dpi_awareness() -> float:
    """宣告 DPI 感知並回傳縮放倍率。必須在建立 Tk 視窗前呼叫。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            return 1.0
    try:
        dc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, dc)
        return max(1.0, dpi / 96.0)
    except Exception:
        return 1.0


def load_state() -> dict:
    # utf-8-sig：手動編輯過的檔案常帶 BOM（PowerShell 的 Out-File -Encoding utf8 就會加），
    # 用 utf-8 讀會直接拋 JSONDecodeError，設定就整包靜靜失憶了。
    try:
        with open(STATE_FILE, encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(**kw) -> None:
    s = load_state()
    s.update(kw)
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f)
    except (OSError, TypeError):  # TypeError = 有人塞了不能序列化的東西進來
        pass


def human_left(delta_min: float) -> str:
    if delta_min <= 0:
        return "已重置"
    h, m = divmod(int(delta_min), 60)
    if h and m:
        return f"剩 {h} 小時 {m} 分"
    return f"剩 {h} 小時" if h else f"剩 {m} 分"


# ──────────────────────── 手動開啟 5 小時視窗 ────────────────────────

def promote_tray_icon() -> bool:
    """讓系統列圖示直接顯示在工作列上，而不是被收進「^」溢位選單裡。

    Windows 11 把每個圖示的顯示與否記在 HKCU\\Control Panel\\NotifyIconSettings，
    `IsPromoted = 1` 就是「一律顯示」。找出本行程執行檔對應的那幾筆設定。
    """
    try:
        import winreg
    except ImportError:
        return False

    exe = os.path.basename(sys.executable).lower()
    changed = False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\NotifyIconSettings") as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    name = winreg.EnumKey(root, i)
                    with winreg.OpenKey(root, name, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
                        path, _ = winreg.QueryValueEx(k, "ExecutablePath")
                        if os.path.basename(str(path)).lower() != exe:
                            continue
                        try:
                            if winreg.QueryValueEx(k, "IsPromoted")[0] == 1:
                                continue
                        except FileNotFoundError:
                            pass
                        winreg.SetValueEx(k, "IsPromoted", 0, winreg.REG_DWORD, 1)
                        changed = True
                except OSError:
                    continue
    except OSError:
        return False
    return changed


def find_claude_cli() -> str | None:
    cand = os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd")
    return cand if os.path.isfile(cand) else shutil.which("claude")


def poke_window() -> tuple[bool, str]:
    """打一次最小的 Claude Code 呼叫，把 5 小時視窗點著。

    視窗是「第一次用量」才開始計時，所以想讓它從現在起算就得真的發一次請求。
    用 claude CLI 是因為它走的是訂閱額度；直接打 API 是另一套計費，不會影響額度。
    """
    cli = find_claude_cli()
    if not cli:
        return False, "找不到 claude CLI"
    try:
        r = subprocess.run(
            [os.environ.get("COMSPEC", "cmd.exe"), "/c", cli, "-p", "hi"],
            capture_output=True, timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return False, "呼叫逾時"
    except OSError as e:
        return False, f"啟動失敗：{e}"
    if r.returncode != 0:
        return False, (r.stderr or b"").decode("utf-8", "ignore").strip()[:60] or "呼叫失敗"
    return True, "已開啟視窗"


# ──────────────────────────────── 系統列圖示 ────────────────────────────────

# Windows 會把圖示縮到 16～28px（SM_CXSMICON，本機是 28），細節全部會糊掉，
# 所以每種樣式都先畫 256px 再讓系統縮。四種各有取捨，用系統列選單切換。
TRAY_STYLES = [
    ("burst", "星芒（狀態色底）"),
    ("number", "純大數字"),
]
TRAY_S = 256


def _state_fill(pct_used: float | None):
    return (154, 154, 160) if pct_used is None else tuple(grad_for(pct_used))[1]


def _fit_text(d: ImageDraw.ImageDraw, text: str, max_w: float, max_h: float) -> ImageFont.FreeTypeFont:
    """二分搜尋出塞得進指定框的最大字級。"""
    lo, hi = 8, 300
    while lo < hi:
        mid = (lo + hi + 1) // 2
        box = d.textbbox((0, 0), text, font=font("en_sb", mid))
        if (box[2] - box[0]) <= max_w and (box[3] - box[1]) <= max_h:
            lo = mid
        else:
            hi = mid - 1
    return font("en_sb", lo)


def _centered(d: ImageDraw.ImageDraw, xy, text, f, fill):
    box = d.textbbox((0, 0), text, font=f)
    d.text((xy[0] - (box[2] - box[0]) / 2 - box[0], xy[1] - (box[3] - box[1]) / 2 - box[1]),
           text, font=f, fill=fill)


def _burst_mask(size: int) -> Image.Image | None:
    """從官方 app icon 取出白色星芒的形狀（亮部 ∩ 不透明）。"""
    logo = claude_logo(size)
    if logo is None:
        return None
    r, _g, _b, a = logo.split()
    return Image.composite(
        r.point(lambda v: 255 if v > 225 else 0),
        Image.new("L", (size, size), 0),
        a.point(lambda v: 255 if v > 128 else 0),
    )


def _tray_burst(pct, S):
    """狀態色圓角底 + 白色星芒。最像真的 app icon，但沒有數字。"""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, S - 1, S - 1], S * 0.24,
                                          fill=_state_fill(pct) + (255,))
    mask = _burst_mask(S)
    if mask is not None:
        img.paste((255, 255, 255, 255), (0, 0), mask)
    return img


def _tray_number(pct, S):
    """狀態色圓角底 + 白色大數字 + 底部細進度條。

    圖示格子是 Shell 定死的 28x28（SM_CXSMICON），沒有 API 能放大，
    所以走扁平路線：字塞好塞滿、只留一條進度細線。發光或漸層在這個尺寸
    只會把字的邊緣糊掉，看起來更小。
    """
    text = "?" if pct is None else f"{pct:.0f}"
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, S - 1, S - 1], S * 0.24, fill=_state_fill(pct) + (255,))

    # 數字往上讓一點給進度條
    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    f = _fit_text(md, text, S * (0.74 if len(text) >= 3 else 0.92), S * 0.80)
    _centered(md, (S / 2, S / 2 - S * 0.05), text, f, 255)
    img.paste((255, 255, 255, 255), (0, 0), mask)

    y0, y1 = int(S * 0.845), int(S * 0.895)
    x0, x1 = int(S * 0.14), int(S * 0.86)
    r = (y1 - y0) / 2
    d.rounded_rectangle([x0, y0, x1, y1], r, fill=(255, 255, 255, 90))
    w = int((x1 - x0) * (pct or 0) / 100)
    if w:
        d.rounded_rectangle([x0, y0, x0 + max(w, y1 - y0), y1], r, fill=(255, 255, 255, 255))
    return img


_TRAY_FN = {"burst": _tray_burst, "number": _tray_number}


def make_tray_image(pct_used: float | None, style: str = "burst") -> Image.Image:
    return _TRAY_FN.get(style, _tray_burst)(pct_used, TRAY_S)


# ──────────────────────────────── 卡片繪製 ────────────────────────────────

class Painter:
    """繪圖基底。

    所有座標都用「邏輯像素」寫，內部乘上 dpi * SS 去畫，最後降採樣到實體像素。
    子類別覆寫 W / H / RADIUS 與 paint()。
    """

    W, H = 100, 100
    RADIUS = 8

    def __init__(self, dpi: float = 1.0):
        self.dpi = dpi
        self.pw, self.ph = round(self.W * dpi), round(self.H * dpi)
        self.k = dpi * SS  # 邏輯像素 → 繪製像素
        self.dw, self.dh = round(self.W * self.k), round(self.H * self.k)
        self.buttons: list[tuple[tuple[float, float, float, float], str]] = []

    # ── 低階繪圖（吃邏輯座標） ──
    def _rrect(self, box, r, **kw):
        self.d.rounded_rectangle([v * self.k for v in box], r * self.k, **kw)

    def _text(self, xy, s, f, fill, anchor="la"):
        self.d.text((xy[0] * self.k, xy[1] * self.k), s, font=f, fill=fill, anchor=anchor)

    def _f(self, kind, pt):
        return font(kind, pt * self.k)

    def _grad_bar(self, x, y, w, h, frac, colors):
        self._rrect((x, y, x + w, y + h), h / 2, fill=TRACK)
        fw = int(w * max(0.0, min(1.0, frac)) * self.k)
        if fw <= 0:
            return
        bh = int(h * self.k)
        fw = max(fw, bh)  # 再少也要畫得出一個圓頭
        c0, c1 = colors
        strip = Image.new("RGB", (fw, 1))
        px = strip.load()
        for i in range(fw):
            t = i / max(1, fw - 1)
            px[i, 0] = tuple(int(c0[j] + (c1[j] - c0[j]) * t) for j in range(3))
        mask = Image.new("L", (fw, bh), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, fw - 1, bh - 1], bh / 2, fill=255)
        self.img.paste(strip.resize((fw, bh), Image.NEAREST), (int(x * self.k), int(y * self.k)), mask)

    def _claude_mark(self, cx, cy, r):
        """貼上 Claude 官方 app icon；拿不到檔案就退回手繪的放射狀標記。"""
        side = int(r * 2 * self.k)
        logo = claude_logo(side)
        if logo is not None:
            self.img.paste(logo, (int(cx * self.k - side / 2), int(cy * self.k - side / 2)), logo)
            return

        import math

        for i in range(12):
            a = math.pi * 2 * i / 12 - math.pi / 2
            tip = r * (1.0 if i % 2 == 0 else 0.64)
            half = r * (0.20 if i % 2 == 0 else 0.17)
            ca, sa = math.cos(a), math.sin(a)
            px, py = -sa, ca
            pts = [
                (cx + ca * tip, cy + sa * tip),
                (cx + px * half, cy + py * half),
                (cx - ca * half, cy - sa * half),
                (cx - px * half, cy - py * half),
            ]
            self.d.polygon([(x * self.k, y * self.k) for x, y in pts], fill=CLAUDE_ORANGE)

    def _icon_button(self, cx, cy, name, glyph, hot, size=10, color=None):
        r = 11
        if hot == name:
            self.d.ellipse(
                [(cx - r) * self.k, (cy - r) * self.k, (cx + r) * self.k, (cy + r) * self.k],
                fill=HOVER,
            )
        self._text((cx, cy), glyph, self._f("icon", size),
                   color or (SUB if hot == name else FAINT), anchor="mm")
        self.buttons.append(((cx - r, cy - r, cx + r, cy + r), name))

    def _begin(self, bg=BG):
        self.buttons = []
        self.img = Image.new("RGB", (self.dw, self.dh), bg)
        self.d = ImageDraw.Draw(self.img)

    def _finish(self) -> Image.Image:
        """畫邊框、降採樣，再用色鍵裁出圓角。

        遮罩的 alpha 要二值化——半透明的邊緣像素若跟 CHROMA 混色會出現黑邊。
        """
        self._rrect((0.5, 0.5, self.W - 0.5, self.H - 0.5), self.RADIUS,
                    outline=EDGE, width=max(1, int(self.k)))
        card = self.img.resize((self.pw, self.ph), Image.LANCZOS)

        shape = Image.new("L", (self.dw, self.dh), 0)
        ImageDraw.Draw(shape).rounded_rectangle(
            [0, 0, self.dw - 1, self.dh - 1], self.RADIUS * self.k, fill=255
        )
        mask = shape.resize((self.pw, self.ph), Image.LANCZOS).point(lambda v: 255 if v >= 128 else 0)
        out = Image.new("RGB", (self.pw, self.ph), CHROMA)
        out.paste(card, (0, 0), mask)
        return out


# ──────────────────────────────── 卡片 ────────────────────────────────

class CardPainter(Painter):
    """把一張 Quota 畫成 Windows 通知樣式的卡片。"""

    W, H = 344, 182
    PAD = 16
    RADIUS = 8      # Windows 11 通知的圓角就是小小的
    FOOT_TOP = 152  # 頁尾淡底色帶從這裡開始

    # ── 一列額度 ──
    def _row(self, y, label, pct, colors, accent, sub=None):
        self._text((self.PAD, y), label, self._f("zh", 13), TXT)
        self._text((self.W - self.PAD, y - 3), f"{pct:.0f}%", self._f("en_sb", 18),
                   accent, anchor="ra")
        self._grad_bar(self.PAD, y + 25, self.W - self.PAD * 2, 8, pct / 100, colors)
        if sub:
            self._text((self.PAD, y + 40), sub, self._f("zh_l", 11), FAINT)

    # ── 整張卡 ──
    def paint(self, q, hot: str | None, note: str | None = None) -> Image.Image:
        self._begin()

        # 頁尾自成一個淡底色帶，整張卡才不會是一片死白
        self.d.rectangle([0, self.FOOT_TOP * self.k, self.dw, self.dh], fill=BG_LOW)
        self.d.line(
            [self.PAD * self.k, self.FOOT_TOP * self.k,
             (self.W - self.PAD) * self.k, self.FOOT_TOP * self.k],
            fill=HAIR, width=max(1, int(self.k)),
        )

        # 標題列：小圖標 + 應用名 + 過期警示 + 三顆按鈕
        self._claude_mark(self.PAD + 9, 24, 10)
        self._text((self.PAD + 26, 18), "Claude", self._f("en", 12), SUB)
        if q and q.stale:
            self._text((self.PAD + 78, 18), "· 資料已過期", self._f("zh_l", 11), WARN_RED)
        self._icon_button(self.W - 108, 24, "poke", ICON_BOLT, hot, size=11)
        self._icon_button(self.W - 80, 24, "refresh", ICON_REFRESH, hot)
        self._icon_button(self.W - 52, 24, "mini", ICON_MIN, hot, size=9)
        self._icon_button(self.W - 24, 24, "close", ICON_CLOSE, hot, size=9)

        foot = ""
        if q is None:
            self._text((self.W / 2, 78), "讀不到額度資料", self._f("zh", 14), TXT, anchor="mm")
            self._text((self.W / 2, 100), "請開啟 Claude 桌面版並登入一次",
                       self._f("zh_l", 11), FAINT, anchor="mm")
            foot = "沒有官方數字就不顯示數字，不做推估"
        else:
            if q.resets_at:
                left = (q.resets_at - datetime.now(timezone.utc)).total_seconds() / 60
                t = f"{q.resets_at.astimezone():%H:%M}"
                sub1 = f"{human_left(left)} · {'' if q.resets_exact else '約 '}{t} 重置"
            else:
                sub1 = "視窗還沒開始計時 · 按上方閃電鍵可立刻起算"
            self._row(52, "5 小時額度", q.percent_used,
                      grad_for(q.percent_used), accent_for(q.percent_used), sub1)
            if q.week_used is not None:
                self._row(118, "每週額度", q.week_used, GRAD_WEEK, WEEK_ACCENT)
            if q.live:
                foot = "即時官方數字"
            else:
                age = "剛剛更新" if q.age_min < 1.5 else f"{q.age_min:.0f} 分鐘前取樣"
                foot = f"桌面版快取 · {age} · {quota_source.api_status()}"

        # 頁尾：平常講資料狀態，滑到按鈕上或剛做完動作時換成提示
        hint = note or {
            "close": "收起來 · 點系統列圖示可再開啟",
            "mini": "縮成橫條 · 點橫條可再展開",
            "refresh": "重新讀取官方數字（受端點限流，約每 2 分鐘一次）",
            "poke": "打一次最小呼叫，讓 5 小時視窗從現在起算",
        }.get(hot or "")
        self._text((self.PAD, self.H - 17), hint or foot, self._f("zh_l", 11),
                   SUB if hint else FAINT)
        return self._finish()


# ──────────────────────────────── 迷你橫條 ────────────────────────────────

class MiniPainter(Painter):
    """停在工作列旁邊的細長橫條。

    系統列圖示的格子是固定 28x28 正方形（SM_CXSMICON），塞不下橫向排版；
    Windows 11 也移除了能在工作列放橫條的 Deskband API。所以另外開一個
    置頂小視窗來做「一直看得到」這件事。
    """

    W, H = 244, 32
    RADIUS = 10
    SLOT = 106  # 一組欄位的寬度：標籤 + 數字 + 迷你條

    def _slot(self, x, label, pct, colors, accent):
        """一組「標籤 + 數字 + 迷你條」。

        數字靠右對齊到固定位置，7%／26%／100% 位數不同時進度條也不會左右跳動。
        """
        cy = self.H / 2
        self._text((x, cy + 1), label, self._f("zh_l", 9), FAINT, anchor="lm")
        self._text((x + 53, cy), f"{pct:.0f}%", self._f("en_sb", 14), accent, anchor="rm")
        self._grad_bar(x + 60, cy - 2.5, 34, 5, pct / 100, colors)

    def paint(self, q, hot: str | None, note: str | None = None) -> Image.Image:
        self._begin()
        cy = self.H / 2
        self._claude_mark(16, cy, 9)

        if q is None:
            self._text((32, cy), "讀不到額度資料", self._f("zh_l", 11), FAINT, anchor="lm")
            return self._finish()

        self._slot(31, "5h", q.percent_used,
                   grad_for(q.percent_used), accent_for(q.percent_used))
        if q.week_used is not None:
            sep = 31 + self.SLOT - 4
            self.d.line([sep * self.k, (cy - 7) * self.k, sep * self.k, (cy + 7) * self.k],
                        fill=HAIR, width=max(1, int(self.k)))
            self._slot(31 + self.SLOT + 8, "週", q.week_used, GRAD_WEEK, WEEK_ACCENT)

        if q.stale:  # 資料過期時右上角點一個紅點
            self.d.ellipse([(self.W - 9) * self.k, 5 * self.k,
                            (self.W - 4) * self.k, 10 * self.k], fill=WARN_RED)
        return self._finish()


# ──────────────────────────────── 浮動視窗 ────────────────────────────────

class FloatWindow:
    """無邊框、置頂、可拖曳的小視窗；位置存在 widget_state.json。

    子類別提供 painter、狀態鍵前綴（STATE）與預設位置，並覆寫 _click 決定點擊行為。
    """

    STATE = ""

    def __init__(self, root: tk.Tk, dpi: float, painter: Painter, default_pos):
        self.root = root
        self.dpi = dpi
        self.painter = painter
        self.W, self.H = painter.pw, painter.ph
        self._default_pos = default_pos

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-transparentcolor", CHROMA)  # 圓角
            self.win.attributes("-alpha", WINDOW_ALPHA)       # 透明感
        except tk.TclError:
            pass

        st = load_state()
        dx, dy = self._default()
        x, y = self._clamp(st.get(self.STATE + "x", dx), st.get(self.STATE + "y", dy))
        self.win.geometry(f"{self.W}x{self.H}+{x}+{y}")

        self.label = tk.Label(self.win, bd=0, bg=CHROMA)
        self.label.pack()
        self._photo: ImageTk.PhotoImage | None = None

        self._drag = None
        self._moved = False
        self.on_moved = None  # 使用者手動拖走時通知外面（例如取消貼齊狀態）
        self.label.bind("<Button-1>", self._click)
        self.label.bind("<B1-Motion>", self._on_drag)
        self.label.bind("<ButtonRelease-1>", self._end_drag)
        self.label.bind("<Motion>", self._hover)
        self.label.bind("<Leave>", self._leave)

        self._hot: str | None = None
        self._note: str | None = None
        self._sig: object = object()  # 保證第一次一定會畫
        self.visible = True
        self._last = None

    # ── 位置 ──
    def _default(self) -> tuple[int, int]:
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        return self._default_pos(sw, sh, self.W, self.H)

    def _clamp(self, x, y) -> tuple[int, int]:
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        return max(0, min(int(x), sw - self.W)), max(0, min(int(y), sh - self.H))

    def _save_pos(self, x, y):
        save_state(**{self.STATE + "x": x, self.STATE + "y": y})

    def reset_position(self):
        x, y = self._clamp(*self._default())
        self.win.geometry(f"+{x}+{y}")
        self._save_pos(x, y)
        self.show()

    def toggle(self):
        self.visible = not self.visible
        self.win.deiconify() if self.visible else self.win.withdraw()

    def show(self):
        if not self.visible:
            self.toggle()

    def hide(self):
        if self.visible:
            self.toggle()

    # ── 滑鼠 ──
    def _hit(self, x, y) -> str | None:
        for (x0, y0, x1, y1), name in self.painter.buttons:
            if x0 * self.dpi <= x <= x1 * self.dpi and y0 * self.dpi <= y <= y1 * self.dpi:
                return name
        return None

    def _start_drag(self, e):
        self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())
        self._moved = False

    def _click(self, e):
        self._start_drag(e)

    def _on_drag(self, e):
        if not self._drag:
            return
        x, y = self._clamp(e.x_root - self._drag[0], e.y_root - self._drag[1])
        if (x, y) != (self.win.winfo_x(), self.win.winfo_y()):
            self._moved = True
        self.win.geometry(f"+{x}+{y}")

    def _end_drag(self, _e):
        if not self._drag:
            return
        self._drag = None
        x, y = self._clamp(self.win.winfo_x(), self.win.winfo_y())
        self._save_pos(x, y)
        if self._moved and self.on_moved:
            self.on_moved()

    def _hover(self, e):
        hit = self._hit(e.x, e.y)
        if hit != self._hot:
            self._hot = hit
            self.label.configure(cursor="hand2" if hit else "arrow")
            self.redraw()

    def _leave(self, _e):
        if self._hot is not None:
            self._hot = None
            self.redraw()

    # ── 繪製 ──
    def note(self, text: str | None, clear_after: int = 0):
        self._note = text
        self.redraw()
        if text and clear_after:
            self.root.after(clear_after, lambda: self.note(None))

    def _signature(self):
        """畫面上真正看得到的東西。沒變就不用重畫（重畫要 ~34ms）。"""
        q = self._last
        left = None
        if q and q.resets_at:
            left = int((q.resets_at - datetime.now(timezone.utc)).total_seconds() // 60)
        return (
            self._hot, self._note,
            None if q is None else (q.percent_used, q.week_used, q.resets_exact,
                                    q.stale, q.live, left),
        )

    def redraw(self, force: bool = False):
        sig = self._signature()
        if not force and sig == self._sig:
            return
        self._sig = sig
        self._photo = ImageTk.PhotoImage(self.painter.paint(self._last, self._hot, self._note))
        self.label.configure(image=self._photo)

    def render(self, q):
        self._last = q
        self.redraw()


class QuotaWindow(FloatWindow):
    """完整卡片。右上角四顆按鈕：⚡ 起算 ／ ↻ 重讀 ／ ─ 縮成橫條 ／ ✕ 收起。"""

    STATE = ""  # 沿用既有的 x / y 鍵，舊設定不會失效

    def __init__(self, root, dpi, on_refresh, on_poke, on_minimize):
        self.on_refresh = on_refresh
        self.on_poke = on_poke
        self.on_minimize = on_minimize
        super().__init__(root, dpi, CardPainter(dpi),
                         lambda sw, sh, w, h: (0, 0))

    def _click(self, e):
        hit = self._hit(e.x, e.y)
        if hit == "refresh":
            self.on_refresh()
        elif hit == "poke":
            self.on_poke()
        elif hit == "mini":
            self.on_minimize()
        elif hit == "close":
            self.hide()  # 只是收起來，不是結束程式
        else:
            self._start_drag(e)


class MiniWindow(FloatWindow):
    """卡片縮小後的形態。點一下展開回卡片，拖曳可移動。

    跟卡片共用同一組 x / y（STATE 都是空字串），所以縮放時左上角不動，
    看起來就是同一個東西在伸縮，而不是兩個各自為政的視窗。
    """

    STATE = ""

    def __init__(self, root, dpi, on_click):
        self.on_click = on_click
        super().__init__(root, dpi, MiniPainter(dpi),
                         lambda sw, sh, w, h: (0, 0))

    def _click(self, e):
        self._start_drag(e)

    def _end_drag(self, e):
        moved = self._moved
        super()._end_drag(e)
        if not moved:  # 沒拖動就是單純點一下
            self.on_click()


# ──────────────────────────────── 主程式 ────────────────────────────────

class App:
    def __init__(self):
        self.dpi = enable_dpi_awareness()
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = QuotaWindow(self.root, self.dpi, self.request_refresh,
                                  self.request_poke, self.minimize)
        self.mini = MiniWindow(self.root, self.dpi, self.expand)
        self.icon = None
        self._poking = False
        self._last_poke: datetime | None = None
        self._tray_pct = object()  # 讓第一次一定會設圖示
        self._tick: str | None = None  # 目前排定的更新排程
        self._api_busy = False
        self._api_next = 0.0  # 下次可以打 API 的時間

        st = load_state()
        self.auto_poke = bool(st.get("auto_poke", True))
        style = st.get("tray_style", "burst")
        self.tray_style = style if style in _TRAY_FN else "burst"
        # 三種狀態：卡片 / 橫條 / 全部收起。同時只會有一個視窗看得見。
        self.minimized = bool(st.get("minimized", False))
        self.mini.hide()
        if self.minimized:
            self.minimize()
        elif st.get("hidden", False):
            self.window.hide()

    # ── 卡片 ↔ 橫條 ↔ 收起 ──
    def _place(self, src, dst):
        """把 dst 擺到 src 現在的左上角，看起來就是同一個東西在伸縮。"""
        x, y = dst._clamp(src.win.winfo_x(), src.win.winfo_y())
        dst.win.geometry(f"+{x}+{y}")
        dst._save_pos(x, y)

    def minimize(self):
        if self.window.visible:
            self._place(self.window, self.mini)
        self.window.hide()
        self.mini.show()
        self.minimized = True
        save_state(minimized=True, hidden=False)

    def expand(self):
        self._place(self.mini, self.window)
        self.mini.hide()
        self.window.show()
        self.minimized = False
        save_state(minimized=False, hidden=False)

    def toggle_visible(self):
        """系統列圖示左鍵：在「看得見」與「全部收起」之間切換。"""
        shown = self.window.visible or self.mini.visible
        if shown:
            self.window.hide()
            self.mini.hide()
            save_state(hidden=True)
        else:
            (self.mini if self.minimized else self.window).show()
            save_state(hidden=False)

    def request_refresh(self):
        """手動重新整理。

        端點有限流，還沒到可以再問的時間就別打——硬打只會吃 429，把自我收斂的
        間隔往外推，變成越按越慢。這種時候就只重讀本機、告訴使用者還要等多久。
        """
        wait = quota_source.seconds_until_allowed()
        if wait > 0:
            self.window.note(f"官方端點限流中 · {wait:.0f} 秒後才能再問一次", clear_after=4000)
        else:
            self._api_next = time.time() + quota_source.current_interval()
            self._pull_api(force=True)
        self.root.after(0, lambda: self.refresh(force=True))

    # ── 即時用量 API ──
    def _pull_api(self, force: bool = False):
        """在背景執行緒更新 API 快取。網路呼叫絕不能跑在 Tk 主執行緒上，否則畫面會卡住。"""
        if self._api_busy:
            return
        self._api_busy = True

        def work():
            try:
                quota_source.refresh_api(force=force)
            finally:
                self._api_busy = False
                self.root.after(0, self.refresh)

        threading.Thread(target=work, daemon=True).start()

    # ── 開啟 5 小時視窗 ──
    def set_tray_style(self, style: str):
        self.tray_style = style
        save_state(tray_style=style)
        self._tray_pct = object()  # 讓下一次 refresh 一定重畫圖示
        self.refresh()

    def toggle_auto_poke(self):
        self.auto_poke = not self.auto_poke
        save_state(auto_poke=self.auto_poke)
        self.window.note(
            f"視窗重置後自動起算：{'開' if self.auto_poke else '關'}", clear_after=5000
        )

    def request_poke(self, auto: bool = False):
        if self._poking:
            return
        self._poking = True
        self._last_poke = datetime.now(timezone.utc)
        self.window.note("自動開啟 5 小時視窗…" if auto else "正在開啟 5 小時視窗…")
        threading.Thread(target=self._poke_worker, daemon=True).start()

    def _poke_worker(self):
        ok, msg = poke_window()

        def done():
            self._poking = False
            # 只用中文字：微軟正黑體 Light 沒有 ✓ / ✕ 的 glyph，會畫成豆腐格
            self.window.note(msg if ok else f"失敗 · {msg}", clear_after=6000)
            self.refresh(force=True)

        self.root.after(0, done)

    def _maybe_auto_poke(self, q):
        """沒有進行中的視窗時自動點著它。

        resets_at 為 None 就代表目前沒有 5 小時視窗在跑（前一個已經過期）。
        冷卻時間必須大於桌面版 5 分鐘的取樣間隔，否則快取還沒反映就會重複觸發。
        """
        if not self.auto_poke or self._poking or q is None or q.resets_at is not None:
            return
        if self._last_poke and \
                (datetime.now(timezone.utc) - self._last_poke).total_seconds() < AUTO_POKE_COOLDOWN_MIN * 60:
            return
        self.request_poke(auto=True)

    def refresh(self, *_, force: bool = False):
        # 畫面每 REFRESH_SEC 重畫是為了倒數；網路照 quota_source 自己收斂出來的節奏
        now = time.time()
        if now >= self._api_next:
            self._api_next = now + quota_source.current_interval()
            self._pull_api()

        try:
            q = quota_source.get_quota()
        except Exception as e:
            if sys.stderr:
                print("讀取用量失敗:", e, file=sys.stderr)
            q = None
        self.window.render(q)
        self.mini.render(q)

        pct = None if q is None else round(q.percent_used)
        if self.icon and (pct != self._tray_pct or force):
            self._tray_pct = pct
            self.icon.icon = make_tray_image(pct, self.tray_style)
            if q is None:
                self.icon.title = "Claude 用量：讀不到資料"
            else:
                week = f"／每週已用 {q.week_used:.0f}%" if q.week_used is not None else ""
                stale = "（資料已過期）" if q.stale else ""
                self.icon.title = f"Claude 已用 {q.percent_used:.0f}%{week}{stale}"

        self._maybe_auto_poke(q)
        # 手動重整也會走到這裡，不取消舊的排程就會養出好幾條並行的更新迴圈
        if self._tick is not None:
            self.root.after_cancel(self._tick)
        self._tick = self.root.after(REFRESH_SEC * 1000, self.refresh)

    def run(self):
        import pystray

        def home():
            (self.mini if self.minimized else self.window).reset_position()

        def style_item(key, label):
            # pystray 用 co_argcount 決定怎麼呼叫：零參數的會被包起來忽略引數，
            # 否則直接餵 (icon, item)。所以這裡一定要用真正的零參數閉包，
            # 不能寫成 lambda _i=None, k=key: ...——第二個引數會把 k 蓋成 MenuItem。
            def choose():
                self.root.after(0, lambda: self.set_tray_style(key))

            def is_current(_item):
                return self.tray_style == key

            return pystray.MenuItem(label, choose, checked=is_current, radio=True)

        menu = pystray.Menu(
            pystray.MenuItem("顯示／隱藏", lambda: self.root.after(0, self.toggle_visible),
                             default=True),
            pystray.MenuItem("縮成橫條",
                             lambda: self.root.after(
                                 0, self.expand if self.minimized else self.minimize),
                             checked=lambda _i: self.minimized),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("立即重新整理", self.request_refresh),
            pystray.MenuItem("開啟 5 小時視窗", lambda: self.root.after(0, self.request_poke)),
            pystray.MenuItem("重置後自動起算", lambda: self.root.after(0, self.toggle_auto_poke),
                             checked=lambda _i: self.auto_poke),
            pystray.MenuItem("移回畫面內", lambda: self.root.after(0, home)),
            pystray.MenuItem("圖示樣式",
                             pystray.Menu(*[style_item(k, lab) for k, lab in TRAY_STYLES])),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("結束 ClaudeQuota", self.quit),
        )
        self.icon = pystray.Icon("ClaudeQuota", make_tray_image(None, self.tray_style),
                                 "Claude 用量", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()
        # 圖示註冊之後才有對應的登錄檔項目可以設定，所以延後一點再提升
        self.root.after(4000, promote_tray_icon)
        self.refresh()
        self.root.mainloop()

    def quit(self, *_):
        if self.icon:
            self.icon.stop()
        self.root.after(0, self.root.destroy)


if __name__ == "__main__":
    App().run()
