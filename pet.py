"""桌面寵物：一隻跟著 Claude Code 狀態變表情的小東西。

內建六個角色，每個角色七種狀態：工作中 / 在等你 / 做完了 / 出錯了 / 睡覺 /
醒著（被你叫醒）/ 被拎起來（拖曳中）。全部用 Pillow 畫，不用任何圖檔；
身體顏色可以換。也可以放自己的圖當自訂角色（見 custom_frame）。

視窗跟卡片、橫條一樣是無邊框置頂的 tool window，背景用 CHROMA 色挖空。
挖空只認「完全等於 CHROMA」的像素，半透明的邊會變成一圈黑邊，所以每一格畫完
都把 alpha 二值化，再靠深色描邊把鋸齒藏起來。
"""
from __future__ import annotations

import glob
import json
import math
import os
import shutil

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageSequence

CHARACTERS = ("slime", "cat", "ghost", "chick", "penguin", "robot", "bunny", "frog", "bear")
PLACEMENTS = ("mini", "walk", "inbar", "free")
STATES = ("working", "waiting", "done", "error", "idle", "awake", "held")
FRAMES = 8
FRAME_MS = 120
S = 64          # 邏輯尺寸（正方形），上面留空間給驚嘆號、星星、z
FEET = 60       # 腳底的 y
SS = 4          # 先放大畫再縮小，邊緣才平滑

INK = (52, 48, 64)
ORANGE = (232, 128, 72)
PINK = (238, 120, 150)
BLUSH = (245, 160, 170)
YELLOW = (250, 200, 70)
WHITE = (255, 255, 255)

# 身體顏色。None = 角色自己的預設色
COLORS = {
    "default": None,
    "pink": (246, 170, 196),
    "blue": (140, 186, 242),
    "purple": (186, 160, 236),
    "yellow": (252, 214, 104),
    "mint": (132, 214, 182),
    "gray": (196, 198, 210),
}
DEFAULT_BODY = {
    "slime": (120, 208, 172),
    "cat": (248, 188, 118),
    "ghost": (250, 250, 255),
    "chick": (255, 216, 92),
    "penguin": (66, 78, 116),
    "robot": (172, 192, 218),
    "bunny": (250, 246, 248),
    "frog": (138, 204, 104),
    "bear": (196, 146, 104),
}


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    names = ("msjhbd.ttc", "seguisb.ttf", "arialbd.ttf") if bold else ("msjh.ttc", "segoeui.ttf", "arial.ttf")
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _edge(body):
    """描邊色：身體色壓暗。很淺的身體（小幽靈）壓暗後偏灰，改帶一點紫。"""
    if sum(body) > 700:
        return (130, 128, 175)
    return _mix(body, (30, 26, 40), 0.55)


class _Pen:
    """以邏輯座標作畫，內部乘上縮放倍率。"""

    def __init__(self, k: float):
        self.k = k
        self.img = Image.new("RGBA", (round(S * k), round(S * k)), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    def _b(self, box):
        return [v * self.k for v in box]

    def _w(self, width):
        return max(1, round(width * self.k)) if width else 0

    def ellipse(self, box, fill=None, outline=None, width=0):
        self.d.ellipse(self._b(box), fill=fill, outline=outline, width=self._w(width))

    def rect(self, box, fill=None, outline=None, width=0, r=0):
        if r:
            self.d.rounded_rectangle(self._b(box), r * self.k, fill=fill, outline=outline, width=self._w(width))
        else:
            self.d.rectangle(self._b(box), fill=fill, outline=outline, width=self._w(width))

    def line(self, pts, fill, width):
        self.d.line([v * self.k for p in pts for v in p], fill=fill, width=self._w(width), joint="curve")

    def arc(self, box, start, end, fill, width):
        self.d.arc(self._b(box), start, end, fill=fill, width=self._w(width))

    def polygon(self, pts, fill=None, outline=None, width=0):
        self.d.polygon([v * self.k for p in pts for v in p], fill=fill, outline=outline, width=self._w(width))

    def text(self, xy, s, size, fill):
        self.d.text((xy[0] * self.k, xy[1] * self.k), s, font=_font(round(size * self.k)), fill=fill, anchor="mm")


# ─────────────────────────────── 動作與表情 ───────────────────────────────

def _motion(state: str, f: int) -> dict:
    """每個狀態共用的身體動作：左右位移 dx、離地高度 lift、壓扁程度 sq。"""
    ph = f / FRAMES * 2 * math.pi
    m = {"dx": 0.0, "lift": 0.0, "sq": 0.0}
    if state == "working":
        m["lift"] = abs(math.sin(ph)) * 4
        m["sq"] = math.sin(2 * ph) * 0.07
    elif state == "waiting":
        m["lift"] = max(0.0, math.sin(2 * ph)) * 8
        m["sq"] = -0.05
    elif state == "done":
        m["lift"] = abs(math.sin(ph)) * 3
    elif state == "error":
        m["dx"] = math.sin(2 * ph) * 1.2
    elif state == "idle":
        m["sq"] = math.sin(ph) * 0.04
    elif state == "awake":
        m["dx"] = math.sin(ph) * 1.5
        m["lift"] = abs(math.sin(ph)) * 1.2
    elif state == "held":
        m["dx"] = math.sin(ph) * 3
        m["lift"] = 5
    return m


EYES = {"working": "focus", "waiting": "wide", "done": "happy", "error": "x",
        "idle": "closed", "awake": "dot", "held": "wide"}


def _eyes(p: _Pen, kind: str, lx, rx, y, s=1.0, color=INK):
    for x in (lx, rx):
        if kind == "dot":
            p.ellipse((x - 2.3 * s, y - 2.7 * s, x + 2.3 * s, y + 2.7 * s), fill=color)
            if color == INK:
                p.ellipse((x - .4 * s, y - 2.1 * s, x + 1.2 * s, y - .5 * s), fill=WHITE)
        elif kind == "focus":  # 認真盯著手上的事：眼睛往下看
            p.ellipse((x - 2.2 * s, y - .6 * s, x + 2.2 * s, y + 3.4 * s), fill=color)
            if color == INK:
                p.ellipse((x - .2 * s, y + .2 * s, x + 1.2 * s, y + 1.4 * s), fill=WHITE)
        elif kind == "wide":
            if color == INK:
                p.ellipse((x - 3.4 * s, y - 3.8 * s, x + 3.4 * s, y + 3.8 * s), fill=WHITE, outline=INK, width=1.2)
                p.ellipse((x - 1.8 * s, y - 1.6 * s, x + 1.8 * s, y + 2.2 * s), fill=INK)
            else:
                p.ellipse((x - 3 * s, y - 3.4 * s, x + 3 * s, y + 3.4 * s), outline=color, width=1.6)
                p.ellipse((x - 1.2 * s, y - 1 * s, x + 1.2 * s, y + 1.4 * s), fill=color)
        elif kind == "happy":
            p.arc((x - 3 * s, y - 2 * s, x + 3 * s, y + 3 * s), 200, 340, color, 1.6)
        elif kind == "x":
            p.line([(x - 2.4 * s, y - 2.4 * s), (x + 2.4 * s, y + 2.4 * s)], color, 1.6)
            p.line([(x - 2.4 * s, y + 2.4 * s), (x + 2.4 * s, y - 2.4 * s)], color, 1.6)
        elif kind == "closed":
            p.arc((x - 3 * s, y - 3 * s, x + 3 * s, y + 1.5 * s), 20, 160, color, 1.6)


def _face(p: _Pen, state: str, f: int, face: dict):
    """眼睛、嘴巴、腮紅。face 由各角色給：中心 cx、眼睛高度 ey、眼距 gap、大小 s、顏色。"""
    cx, ey, gap, s = face["cx"], face["ey"], face["gap"], face.get("s", 1.0)
    color = face.get("color", INK)
    kind = EYES[state]
    if state == "awake" and f == 5:  # 偶爾眨個眼
        kind = "closed"
    _eyes(p, kind, cx - gap, cx + gap, ey, s, color)
    if face.get("blush", True) and state in ("done", "awake", "held"):
        for x in (cx - gap - 6 * s, cx + gap + 2 * s):
            p.ellipse((x, ey + 3 * s, x + 4 * s, ey + 6 * s), fill=BLUSH)
    if face.get("no_mouth"):
        return
    my = ey + face.get("mouth", 6) * s
    if state in ("waiting", "held"):
        r = 2.4 if state == "waiting" else 1.8
        p.ellipse((cx - r * s, my - r * s, cx + r * s, my + r * 1.4 * s), fill=color)
    elif state == "done":
        p.arc((cx - 4 * s, my - 5 * s, cx + 4 * s, my + 1 * s), 20, 160, color, 1.6)
    elif state == "awake":
        p.arc((cx - 3 * s, my - 4 * s, cx + 3 * s, my + .5 * s), 30, 150, color, 1.4)
    elif state == "error":
        p.arc((cx - 3.5 * s, my - 1 * s, cx + 3.5 * s, my + 4 * s), 200, 340, color, 1.6)
    elif state == "working":
        p.line([(cx - 1.8 * s, my - 1 * s), (cx + 1.8 * s, my - 1 * s)], color, 1.4)


# ─────────────────────────────── 頭上的小東西 ───────────────────────────────

def _bang(p: _Pen, f: int, x=52, y=12):
    """在等你：一閃一閃的橘色驚嘆號泡泡。"""
    if f % 4 == 3:
        return
    p.ellipse((x - 8, y - 8, x + 8, y + 8), fill=ORANGE, outline=INK, width=1.2)
    p.rect((x - 1.4, y - 5, x + 1.4, y + 2), fill=WHITE)
    p.ellipse((x - 1.6, y + 3, x + 1.6, y + 6.2), fill=WHITE)


def _zz(p: _Pen, f: int, x=48, y=20):
    for i in range(2):
        t = ((f + i * 4) % FRAMES) / FRAMES
        p.text((x + 6 * t + i * 2, y - 14 * t), "z", 7 + 5 * t, (110, 110, 150))


def _sparkle(p: _Pen, cx, cy, r, color=YELLOW):
    p.polygon([(cx, cy - r), (cx + r * .3, cy - r * .3), (cx + r, cy), (cx + r * .3, cy + r * .3),
               (cx, cy + r), (cx - r * .3, cy + r * .3), (cx - r, cy), (cx - r * .3, cy - r * .3)],
              fill=color, outline=INK, width=0.8)


def _sparkles(p: _Pen, f: int, cx=32, cy=32):
    for i in range(3):
        a = (f / FRAMES + i / 3) * 2 * math.pi
        _sparkle(p, cx + 25 * math.cos(a), cy - 8 + 14 * math.sin(a), 3.6 + (i == f % 3) * 1.2)


def _cloud(p: _Pen, f: int, x=50, y=10):
    """出錯了：頭上一朵小烏雲在滴雨。"""
    gray, edge = (176, 180, 196), (100, 104, 124)
    for bx, by, r in ((-5, 1, 4.2), (0, -2, 5), (5, 1, 4.2)):
        p.ellipse((x + bx - r, y + by - r, x + bx + r, y + by + r), fill=gray, outline=edge, width=1)
    p.rect((x - 8, y + 1, x + 8, y + 5), fill=gray)
    p.line([(x - 8.6, y + 5), (x + 8.6, y + 5)], edge, 1)
    dy = (f % 4) * 1.6
    p.ellipse((x - 2.2, y + 8 + dy, x + 0.6, y + 11.5 + dy), fill=(120, 170, 235))


def _sweat(p: _Pen, x=48, y=18):
    p.polygon([(x, y - 4), (x - 2.6, y + 1), (x + 2.6, y + 1)], fill=(150, 200, 245))
    p.ellipse((x - 2.8, y - 1.2, x + 2.8, y + 3.8), fill=(150, 200, 245), outline=(90, 140, 200), width=0.8)


def _note(p: _Pen, f: int, x=50, y=14):
    """醒著：偶爾哼個小調。"""
    if f > 3:
        return
    y -= f * 1.5
    p.ellipse((x - 3, y + 3, x + 1, y + 6), fill=INK)
    p.line([(x + 0.6, y + 4.5), (x + 0.6, y - 4), (x + 4, y - 2.6)], INK, 1.2)


def _overlay(p: _Pen, state: str, f: int):
    if state == "waiting":
        _bang(p, f)
    elif state == "done":
        _sparkles(p, f)
    elif state == "idle":
        _zz(p, f)
    elif state == "error":
        _cloud(p, f)
    elif state == "held":
        _sweat(p)
    elif state == "awake":
        _note(p, f)


# ─────────────────────────────── 角色 ───────────────────────────────

def _slime(p, state, f, m, body):
    edge = _edge(body)
    cx, ground = 32 + m["dx"], FEET - m["lift"]
    w, h = 40 * (1 + m["sq"]), 28 * (1 - m["sq"])
    if state == "error":
        w, h = 50, 15
    if state == "held":
        w, h = 34, 32
    top = ground - h
    p.ellipse((cx - w / 2, top, cx + w / 2, ground + h * 0.55), fill=body, outline=edge, width=1.6)
    p.rect((cx - w / 2 - 2, ground, cx + w / 2 + 2, ground + h), fill=(0, 0, 0, 0))
    p.line([(cx - w / 2 + 3, ground), (cx + w / 2 - 3, ground)], edge, 1.6)
    p.ellipse((cx - w / 4, top + 4, cx - w / 4 + 7, top + 8), fill=_mix(body, WHITE, 0.7))
    if state == "error":
        for dx in (-14, 6, 16):
            p.ellipse((cx + dx - 2, ground - 1, cx + dx + 2, ground + 3 + (f % 3)), fill=body, outline=edge, width=1)
    return {"cx": cx, "ey": top + h * 0.46, "gap": 8}


def _cat(p, state, f, m, body):
    edge = _edge(body)
    cx, ground = 32 + m["dx"], FEET - m["lift"]
    w, h = 36 * (1 + m["sq"]), 32 * (1 - m["sq"])
    top = ground - h
    inner = (250, 176, 190)
    # 尾巴（在身體後面）：等你和醒著的時候搖
    wag = math.sin(f / FRAMES * 2 * math.pi * 2) * 4 if state in ("waiting", "awake", "done") else 0
    tail = [(cx + w / 2 - 4, ground - 4), (cx + w / 2 + 6, ground - 8), (cx + w / 2 + 9 + wag * .3, ground - 18),
            (cx + w / 2 + 5 + wag, ground - 26)]
    if state == "idle":
        tail = [(cx + w / 2 - 4, ground - 2), (cx + w / 2 + 6, ground - 2), (cx + w / 2 + 4, ground - 8)]
    p.line(tail, edge, 6.4)
    p.line(tail, body, 4)
    # 耳朵
    ear = 0 if state != "error" else 3  # 出錯時耳朵往下壓
    for side in (-1, 1):
        bx = cx + side * w * 0.3
        tip = (bx + side * 4, top - 9 + ear)
        p.polygon([(bx - 7, top + 6), tip, (bx + 7, top + 6)], fill=body, outline=edge, width=1.6)
        p.polygon([(bx - 3.5, top + 5), (bx + side * 3 + 0.5 * side, top - 3 + ear), (bx + 3.5, top + 5)], fill=inner)
    # 身體（頭和身體一顆）
    p.rect((cx - w / 2, top, cx + w / 2, ground), fill=body, outline=edge, width=1.6, r=w * 0.42)
    # 額頭的三條花紋
    for dx in (-4, 0, 4):
        p.line([(cx + dx, top + 2.5), (cx + dx, top + 6.5)], _mix(body, edge, 0.6), 1.6)
    # 前腳
    for dx in (-7, 7):
        p.ellipse((cx + dx - 4.5, ground - 5, cx + dx + 4.5, ground + 1), fill=_mix(body, WHITE, 0.35),
                  outline=edge, width=1.2)
    ey = top + 13
    # 鬍鬚
    for side in (-1, 1):
        for dy in (-1, 2):
            p.line([(cx + side * 12, ey + 5 + dy * .6), (cx + side * 19, ey + 4 + dy * 1.4)], edge, 0.9)
    p.polygon([(cx - 1.8, ey + 3.2), (cx + 1.8, ey + 3.2), (cx, ey + 5)], fill=PINK)
    return {"cx": cx, "ey": ey, "gap": 7.5, "mouth": 7.5}


def _ghost(p, state, f, m, body):
    edge = _edge(body)
    ph = f / FRAMES * 2 * math.pi
    if state == "error":
        body = _mix(body, (170, 196, 226), 0.6)
    cx, w = 30.0 + m["dx"] * 2, 34.0
    top = 18.0 + math.sin(ph) * 2.2 - m["lift"] * .5
    if state == "idle":
        top += 3
    bottom = top + 36
    left, right = cx - w / 2, cx + w / 2
    pts = []
    for i in range(0, 181, 10):
        a = math.radians(180 + i)
        pts.append((cx + w / 2 * math.cos(a), top + w / 2 + w / 2 * math.sin(a)))
    pts.append((right, bottom))
    for i in range(25):
        x = right - (right - left) * i / 24
        pts.append((x, bottom - 3 + 3 * math.sin(i / 8 * 2 * math.pi + ph)))
    pts.append((left, top + w / 2))
    p.polygon(pts, fill=body, outline=edge, width=1.6)
    ey = top + 15
    if state == "waiting":  # 舉手敲螢幕
        knock = 2 if f % 2 else 0
        p.ellipse((right - 2, ey - 2 - knock, right + 6, ey + 6 - knock), fill=body, outline=edge, width=1.4)
    return {"cx": cx, "ey": ey, "gap": 7}


def _chick(p, state, f, m, body):
    edge = _edge(body)
    cx, ground = 32 + m["dx"], FEET - m["lift"]
    r = 17 * (1 + m["sq"] * .5)
    cy = ground - 17 * (1 - m["sq"])
    beak = (246, 150, 60)
    # 腳
    for dx in (-5, 5):
        p.line([(cx + dx, ground - 2), (cx + dx, ground + 2)], beak, 1.8)
        p.line([(cx + dx - 2.5, ground + 2), (cx + dx + 2.5, ground + 2)], beak, 1.6)
    # 頭頂的毛
    for dx, tilt in ((-3, -4), (0, 0), (3, 4)):
        p.line([(cx + dx, cy - r + 2), (cx + dx + tilt * .6, cy - r - 5)], edge, 1.6)
    p.ellipse((cx - r, cy - r, cx + r, cy + r), fill=body, outline=edge, width=1.6)
    # 翅膀：等你和做完時拍動
    flap = (-3 if f % 2 else 1) if state in ("waiting", "done", "held") else 0
    for side in (-1, 1):
        wx = cx + side * (r - 1)
        p.ellipse((wx - 4, cy + flap - 2, wx + 4, cy + flap + 7), fill=_mix(body, edge, 0.25), outline=edge, width=1.2)
    ey = cy - 3
    open_ = state in ("waiting", "held", "done")
    p.polygon([(cx - 3.2, ey + 4), (cx + 3.2, ey + 4), (cx, ey + (8.5 if open_ else 7))], fill=beak, outline=edge,
              width=0.8)
    if open_:
        p.polygon([(cx - 2.6, ey + 3.4), (cx + 2.6, ey + 3.4), (cx, ey + 1.2)], fill=beak, outline=edge, width=0.8)
    return {"cx": cx, "ey": ey, "gap": 7, "no_mouth": True}


def _penguin(p, state, f, m, body):
    edge = _mix(body, (10, 10, 20), 0.5)
    cx, ground = 32 + m["dx"], FEET - m["lift"]
    w, h = 32 * (1 + m["sq"]), 40 * (1 - m["sq"])
    top = ground - h
    orange = (246, 156, 60)
    for dx in (-6, 6):
        p.ellipse((cx + dx - 4.5, ground - 2.5, cx + dx + 4.5, ground + 1.5), fill=orange, outline=edge, width=1)
    # 鰭：等你和做完時揮手
    flap = (6 if f % 2 else 0) if state in ("waiting", "done") else 0
    for side in (-1, 1):
        fx = cx + side * (w / 2 - 1)
        lift = flap if side == 1 else 0
        p.polygon([(fx, top + 16), (fx + side * 6, top + 28 - lift), (fx + side * 1, top + 30)], fill=body,
                  outline=edge, width=1.2)
    p.ellipse((cx - w / 2, top, cx + w / 2, ground + 2), fill=body, outline=edge, width=1.6)
    belly = (250, 250, 252)
    p.ellipse((cx - w / 2 + 5, top + 7, cx + w / 2 - 5, ground), fill=belly)
    ey = top + 15
    p.polygon([(cx - 2.8, ey + 3.2), (cx + 2.8, ey + 3.2), (cx, ey + 6.4)], fill=orange, outline=edge, width=0.8)
    return {"cx": cx, "ey": ey, "gap": 6.5, "mouth": 9}


def _robot(p, state, f, m, body):
    edge = _edge(body)
    cx, ground = 32 + m["dx"], FEET - m["lift"] * .6
    # 天線燈：顏色跟著狀態走
    light = {"working": (110, 170, 250) if f % 2 else (70, 110, 200), "waiting": ORANGE, "done": (110, 210, 120),
             "error": (230, 80, 80) if f % 2 else (160, 50, 50), "idle": (150, 150, 165),
             "awake": (120, 220, 230), "held": YELLOW}[state]
    head_top = ground - 40 + (2 if state == "idle" else 0)
    p.line([(cx, head_top), (cx, head_top - 7)], edge, 1.6)
    p.ellipse((cx - 3.4, head_top - 11, cx + 3.4, head_top - 4.2), fill=light, outline=edge, width=1.2)
    # 身體與輪子
    p.rect((cx - 11, ground - 14, cx + 11, ground - 2), fill=_mix(body, edge, 0.2), outline=edge, width=1.4, r=3)
    p.ellipse((cx - 3, ground - 11, cx + 3, ground - 5), fill=light)
    for dx in (-7, 7):
        p.ellipse((cx + dx - 3.5, ground - 4, cx + dx + 3.5, ground + 2.5), fill=(90, 96, 112), outline=edge,
                  width=1)
    # 頭和螢幕臉
    p.rect((cx - 17, head_top, cx + 17, ground - 13), fill=body, outline=edge, width=1.6, r=7)
    screen = (40, 46, 64)
    p.rect((cx - 12.5, head_top + 4.5, cx + 12.5, ground - 18), fill=screen, r=4)
    glow = {"error": (240, 110, 110), "waiting": (255, 190, 110)}.get(state, (120, 230, 240))
    return {"cx": cx, "ey": head_top + 11, "gap": 6, "s": .85, "color": glow, "mouth": 5.5, "blush": False}


def _bunny(p, state, f, m, body):
    edge = _edge(body)
    cx, ground = 32 + m["dx"], FEET - m["lift"]
    w, h = 32 * (1 + m["sq"]), 28 * (1 - m["sq"])
    top = ground - h
    inner = (250, 182, 196)
    # 長耳朵：睡覺和出錯時垂下來，等你時豎直抖動
    droop = state in ("idle", "error")
    twitch = (2 if f % 2 else 0) if state == "waiting" else 0
    for side in (-1, 1):
        bx = cx + side * 6
        if droop:
            ear = (bx + side * 2, top + 2, bx + side * 18, top + 9)
            box = (min(ear[0], ear[2]), ear[1], max(ear[0], ear[2]), ear[3])
            p.ellipse(box, fill=body, outline=edge, width=1.4)
        else:
            tip = top - 20 - (twitch if side == 1 else 0)
            p.ellipse((bx - 4.5, tip, bx + 4.5, top + 6), fill=body, outline=edge, width=1.4)
            p.ellipse((bx - 2, tip + 4, bx + 2, top + 2), fill=inner)
    p.ellipse((cx - w / 2, top, cx + w / 2, ground + 2), fill=body, outline=edge, width=1.6)
    for dx in (-6, 6):
        p.ellipse((cx + dx - 4, ground - 4, cx + dx + 4, ground + 1), fill=_mix(body, WHITE, 0.4), outline=edge,
                  width=1.1)
    ey = top + 11
    p.ellipse((cx - 1.6, ey + 3, cx + 1.6, ey + 5), fill=PINK)
    return {"cx": cx, "ey": ey, "gap": 7, "mouth": 7.5}


def _frog(p, state, f, m, body):
    edge = _edge(body)
    cx, ground = 32 + m["dx"], FEET - m["lift"]
    w, h = 42 * (1 + m["sq"]), 24 * (1 - m["sq"])
    top = ground - h
    # 頭頂兩顆凸起的大眼泡
    for side in (-1, 1):
        ex = cx + side * 10
        p.ellipse((ex - 7, top - 7, ex + 7, top + 7), fill=body, outline=edge, width=1.6)
    p.ellipse((cx - w / 2, top, cx + w / 2, ground + 4), fill=body, outline=edge, width=1.6)
    p.rect((cx - w / 2 - 2, ground, cx + w / 2 + 2, ground + 6), fill=(0, 0, 0, 0))
    p.line([(cx - w / 2 + 4, ground), (cx + w / 2 - 4, ground)], edge, 1.6)
    for side in (-1, 1):  # 補回眼泡和身體接縫處的底色
        ex = cx + side * 10
        p.ellipse((ex - 5.6, top - 1, ex + 5.6, top + 6), fill=body)
    p.ellipse((cx - w / 2 + 6, top + 9, cx + w / 2 - 6, ground - 1), fill=_mix(body, (255, 250, 220), 0.55))
    # 眼睛在眼泡裡，嘴巴是一條寬寬的線
    face = {"cx": cx, "ey": top, "gap": 10, "mouth": 12, "no_mouth": True}
    my = top + 11
    if state in ("waiting", "held"):
        p.ellipse((cx - 3, my - 1.5, cx + 3, my + 3.5), fill=INK)
    elif state == "error":
        p.arc((cx - 6, my, cx + 6, my + 6), 200, 340, INK, 1.6)
    elif state in ("done", "awake"):
        p.arc((cx - 8, my - 6, cx + 8, my + 2), 20, 160, INK, 1.6)
    else:
        p.arc((cx - 7, my - 4, cx + 7, my + 1), 30, 150, INK, 1.4)
    return face


def _bear(p, state, f, m, body):
    edge = _edge(body)
    cx, ground = 32 + m["dx"], FEET - m["lift"]
    w, h = 36 * (1 + m["sq"]), 32 * (1 - m["sq"])
    top = ground - h
    light = _mix(body, (255, 244, 226), 0.55)
    for side in (-1, 1):
        ex = cx + side * 13
        p.ellipse((ex - 6, top - 4, ex + 6, top + 8), fill=body, outline=edge, width=1.6)
        p.ellipse((ex - 3, top - 1, ex + 3, top + 5), fill=light)
    p.rect((cx - w / 2, top, cx + w / 2, ground), fill=body, outline=edge, width=1.6, r=w * 0.45)
    for dx in (-8, 8):
        p.ellipse((cx + dx - 5, ground - 6, cx + dx + 5, ground + 1), fill=light, outline=edge, width=1.2)
    ey = top + 12
    p.ellipse((cx - 6.5, ey + 2, cx + 6.5, ey + 11), fill=light)
    p.ellipse((cx - 2.2, ey + 3.2, cx + 2.2, ey + 6), fill=INK)
    return {"cx": cx, "ey": ey, "gap": 8, "mouth": 8}


_DRAW = {"slime": _slime, "cat": _cat, "ghost": _ghost, "chick": _chick, "penguin": _penguin, "robot": _robot,
         "bunny": _bunny, "frog": _frog, "bear": _bear}


def _finish(img: Image.Image, scale: float, chroma: tuple, flip: bool) -> Image.Image:
    img = img.resize((round(S * scale), round(S * scale)), Image.LANCZOS)
    if flip:
        img = ImageOps.mirror(img)
    if chroma is None:  # 畫在 Canvas 上（小遊戲）就保留真正的透明
        return img
    alpha = img.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
    out = Image.new("RGB", img.size, chroma)
    out.paste(img.convert("RGB"), mask=alpha)
    return out


def frame(character: str, state: str, f: int, scale: float, chroma: tuple, flip: bool = False,
          color: str = "default") -> Image.Image:
    """畫內建角色的一格，回傳 RGB 圖：透明的地方填 chroma 色。"""
    f %= FRAMES
    pen = _Pen(scale * SS)
    body = COLORS.get(color) or DEFAULT_BODY.get(character, DEFAULT_BODY["slime"])
    if state == "error" and character != "ghost":
        body = _mix(body, (170, 176, 205), 0.45)  # 出錯時臉色發青
    face = _DRAW.get(character, _slime)(pen, state, f, _motion(state, f), body)
    _face(pen, state, f, face)
    _overlay(pen, state, f)
    return _finish(pen.img, scale, chroma, flip)


# ─────────────────────────────── 自訂角色 ───────────────────────────────
#
# 在資料夾 pets/<角色名>/ 放圖就是一個新角色。檔名用狀態命名：
#   working.png / working.gif / working_1.png, working_2.png …（多張依序播放）
# 缺的狀態會依序改用 awake → idle → 資料夾裡的第一張圖。

CUSTOM_DIR = "pets"
_IMG_EXT = (".png", ".gif", ".webp")
_FALLBACK = {"held": ("waiting", "awake", "idle"), "awake": ("idle",), "working": ("awake", "idle"),
             "waiting": ("awake", "idle"), "done": ("awake", "idle"), "error": ("idle",), "idle": ("awake",)}
CUSTOM_README = {
    "zh": "在這個資料夾裡開一個子資料夾（名字就是角色名，例如 my-dog），裡面放圖：\n\n"
          "  working.png   工作中\n  waiting.png   在等你允許\n  done.png      做完了\n"
          "  error.png     出錯了\n  idle.png      睡覺\n  awake.png     被叫醒\n  held.png      被拎起來\n\n"
          "可以用 .png、.gif（會動）或 .webp。想做動畫也可以放 working_1.png、working_2.png… 依序播放。\n"
          "缺的狀態會改用 awake 或 idle 的圖，最少放一張就能用。圖會等比例縮到 64x64 以內、對齊底部。\n"
          "預設每秒 8 格；想像影片一樣順，在角色資料夾放一個 pet.json，內容寫 {\"fps\": 24}。\n"
          "放好之後，從系統列選單「桌面寵物 → 角色」選它（選單每次打開都會重新掃描）。\n",
    "en": "Make a subfolder here (its name is the character's name, e.g. my-dog) and put images in it:\n\n"
          "  working.png   working\n  waiting.png   waiting for your permission\n  done.png      done\n"
          "  error.png     error\n  idle.png      asleep\n  awake.png     woken up\n  held.png      being dragged\n\n"
          "Use .png, .gif (animated) or .webp. For an animation you can also use working_1.png, working_2.png, …\n"
          "Missing states fall back to awake or idle; a single image is enough. Images are scaled to fit 64x64, "
          "bottom-aligned.\n"
          "Animations play at 8 frames a second; for smooth video-like motion add a pet.json containing "
          "{\"fps\": 24} to the character's folder.\n"
          "Then pick it under Desktop pet > Character in the tray menu (the list is rescanned every time).\n",
}


def custom_root(data_dir: str) -> str:
    return os.path.join(data_dir, CUSTOM_DIR)


def custom_characters(data_dir: str) -> list[str]:
    root = custom_root(data_dir)
    try:
        names = sorted(n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n)))
    except OSError:
        return []
    return [n for n in names if _images(os.path.join(root, n))]


def ensure_custom_root(data_dir: str, lang: str) -> str:
    root = custom_root(data_dir)
    os.makedirs(root, exist_ok=True)
    readme = os.path.join(root, "README.txt")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8-sig") as f:
            f.write(CUSTOM_README.get(lang, CUSTOM_README["en"]) + "\n" + CUSTOM_README["en" if lang == "zh" else "zh"])
    return root


def _images(folder: str) -> list[str]:
    try:
        return sorted(os.path.join(folder, n) for n in os.listdir(folder) if n.lower().endswith(_IMG_EXT))
    except OSError:
        return []


_files_cache: dict = {}


def _state_files(folder: str, state: str) -> list[str]:
    """這個狀態要播的圖檔。播放時每格都會問，所以照資料夾的修改時間快取。"""
    try:
        stamp = os.path.getmtime(folder)
    except OSError:
        return []
    cached = _files_cache.get((folder, state))
    if cached and cached[0] == stamp:
        return cached[1]
    files = _find_state_files(folder, state)
    _files_cache[(folder, state)] = (stamp, files)
    return files


def forget_sources(folder: str) -> None:
    """縮好的圖已經準備好了，原始大圖就不用留在記憶體裡。"""
    for key in [k for k in _custom_cache if k[0] == folder]:
        del _custom_cache[key]


def _find_state_files(folder: str, state: str) -> list[str]:
    for name in (state,) + _FALLBACK.get(state, ()):
        for ext in _IMG_EXT:
            single = os.path.join(folder, name + ext)
            if os.path.isfile(single):
                return [single]
        seq = sorted(glob.glob(os.path.join(folder, name + "_*")),
                     key=lambda p: int("".join(c for c in os.path.splitext(p)[0].rsplit("_", 1)[-1] if c.isdigit()) or 0))
        seq = [p for p in seq if p.lower().endswith(_IMG_EXT)]
        if seq:
            return seq
    every = _images(folder)
    return every[:1]


_custom_cache: dict = {}


def _custom_sources(folder: str, state: str) -> list[Image.Image]:
    files = _state_files(folder, state)
    key = (folder, state, tuple((p, os.path.getmtime(p)) for p in files if os.path.exists(p)))
    if key in _custom_cache:
        return _custom_cache[key]
    frames = []
    for path in files:
        try:
            with Image.open(path) as im:
                frames += [fr.convert("RGBA") for fr in ImageSequence.Iterator(im)][:24]
        except (OSError, ValueError):
            continue
    _custom_cache[key] = frames
    return frames


_fps_cache: dict = {}


def custom_fps(folder: str) -> float:
    """自訂角色的播放速度：資料夾裡 pet.json 的 {"fps": 24}；沒寫就跟內建角色一樣。"""
    path = os.path.join(folder, "pet.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return 1000 / FRAME_MS
    cached = _fps_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, encoding="utf-8") as f:
            fps = float(json.load(f).get("fps", 1000 / FRAME_MS))
    except (OSError, ValueError, AttributeError, TypeError):
        fps = 1000 / FRAME_MS
    fps = max(2.0, min(fps, 30.0))
    _fps_cache[path] = (mtime, fps)
    return fps


def custom_count(folder: str, state: str) -> int:
    """自訂角色這個狀態有幾格動畫（至少 1）。

    只數檔案、不讀圖（幾百張的影片動畫讀一次要好幾秒，不能卡在畫面執行緒上）；
    只有 GIF 這種一個檔案裡多格的才真的打開來數。
    """
    files = _state_files(folder, state)
    if any(p.lower().endswith((".gif", ".webp")) for p in files):
        return max(1, len(_custom_sources(folder, state)))
    return max(1, len(files))


def custom_music(folder: str) -> dict[str, str]:
    """自訂角色每個狀態要放的音樂：{狀態: WAV 路徑}。沒設定就是空的。

    pet.json 可以寫成每個狀態各一首：{"music": {"done": "ending.wav", "awake": "dance.wav"}}，
    或一首歌用在好幾個狀態：{"music": {"file": "dance.wav", "states": ["done", "awake"]}}。
    只支援 WAV（Windows 內建就能播，不用多裝東西）。
    """
    try:
        with open(os.path.join(folder, "pet.json"), encoding="utf-8") as f:
            music = json.load(f).get("music") or {}
        if "file" in music:
            music = {s: music["file"] for s in music.get("states", ("done", "awake"))}
        pairs = {s: os.path.join(folder, str(name)) for s, name in music.items() if s in STATES}
    except (OSError, ValueError, AttributeError, TypeError):
        return {}
    return {s: p for s, p in pairs.items() if p.lower().endswith(".wav") and os.path.isfile(p)}


def custom_frame(folder: str, state: str, f: int, scale: float, chroma: tuple, flip: bool = False) -> Image.Image:
    """自訂角色的一格：等比例縮進 64x64、底部對齊腳底。讀不到圖就畫一隻史萊姆。"""
    frames = _custom_sources(folder, state)
    if not frames:
        return frame("slime", state, f, scale, chroma, flip)
    src = frames[f % len(frames)]
    box = round(FEET * scale), round(S * scale)
    canvas = Image.new("RGBA", (box[1], box[1]), (0, 0, 0, 0))
    fit = src.copy()
    fit.thumbnail((box[1], box[0]), Image.LANCZOS)
    canvas.alpha_composite(fit, ((box[1] - fit.width) // 2, box[0] - fit.height))
    if flip:
        canvas = ImageOps.mirror(canvas)
    if chroma is None:
        return canvas
    alpha = canvas.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
    out = Image.new("RGB", canvas.size, chroma)
    out.paste(canvas.convert("RGB"), mask=alpha)
    return out


# ─────────────────────────────── 遊戲風對話框 ───────────────────────────────

BUBBLE_W = 250           # 文字區最寬（邏輯像素）
_PAPER = (255, 251, 238)
_TAG = (232, 128, 72)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: float) -> list[str]:
    """中英混排換行：英文照單字斷，中文照字斷。"""
    lines, cur = [], ""
    for token in _tokens(text):
        trial = cur + token
        if cur and font.getlength(trial) > max_w:
            lines.append(cur.rstrip())
            cur = token.lstrip()
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines or [""]


def _tokens(text: str) -> list[str]:
    out, word = [], ""
    for ch in text:
        if ch.isascii() and not ch.isspace():
            word += ch
            continue
        if word:
            out.append(word)
            word = ""
        out.append(ch)
    if word:
        out.append(word)
    return out


def bubble_layout(text: str, scale: float) -> tuple[list[str], int, int]:
    """對話框的行與外框大小（實際像素）。"""
    font = _font(round(13 * scale))
    lines = _wrap(text, font, BUBBLE_W * scale)
    line_h = round(20 * scale)
    tw = max(font.getlength(s) for s in lines)
    pad = 14 * scale
    w = round(max(tw + pad * 2, 120 * scale))
    h = round(len(lines) * line_h + pad * 2 + 12 * scale + 10 * scale)  # +名牌 +尾巴
    return lines, w, h


def draw_duck(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, body=(255, 210, 64)):
    """一隻小黃鴨的頭（主題裝飾用）：圓頭、橘嘴、黑眼睛、一撮毛。"""
    edge = _mix(body, (90, 60, 20), 0.55)
    w = max(1, round(r * 0.12))
    d.line([(cx - r * .1, cy - r * .95), (cx + r * .05, cy - r * 1.3), (cx + r * .2, cy - r * .95)], fill=edge,
           width=w)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=body, outline=edge, width=w)
    bx = cx + r * .62
    d.ellipse((bx - r * .1, cy - r * .05, bx + r * .75, cy + r * .42), fill=(246, 140, 60), outline=edge, width=w)
    d.ellipse((cx + r * .12, cy - r * .42, cx + r * .4, cy - r * .12), fill=(40, 30, 30))
    d.ellipse((cx - r * .55, cy + r * .05, cx - r * .2, cy + r * .32), fill=(250, 160, 170))


def bubble_image(text: str, name: str, shown: int, caret: bool, scale: float, chroma: tuple,
                 tail_x: int | None = None, palette: dict | None = None) -> Image.Image:
    """像遊戲對話框的泡泡：粗框、底色、左上角名牌、指向寵物的尾巴。

    shown 是打字機效果目前顯示到第幾個字；caret 控制右下角閃爍的 ▼。
    palette 是右鍵選單的主題色（fancymenu.THEMES），沒給就用米色遊戲紙。
    """
    p = palette or {"bg": _PAPER, "edge": INK, "inner": (232, 222, 196), "accent": _TAG, "text": INK,
                    "tag_text": WHITE}
    lines, w, h = bubble_layout(text, scale)
    k = SS
    img = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    tag_h, tail_h = 12 * scale * k, 10 * scale * k
    top, bottom = tag_h, h * k - tail_h
    bw = max(2, round(2.4 * scale * k))
    r = 10 * scale * k
    d.rounded_rectangle((bw / 2, top, w * k - bw / 2, bottom), r, fill=p["bg"], outline=p["edge"], width=bw)
    inset = 4 * scale * k
    d.rounded_rectangle((inset, top + inset, w * k - inset, bottom - inset), r * .6, outline=p["inner"],
                        width=max(1, round(1.2 * scale * k)))
    # 尾巴：指向寵物（對話框被擠到寵物旁邊時 tail_x 是負的，就不畫）
    if tail_x is None or tail_x > -1000:
        tx = (tail_x if tail_x is not None else w // 2) * k
        tx = max(r + 8 * scale * k, min(tx, w * k - r - 8 * scale * k))
        half = 7 * scale * k
        d.polygon([(tx - half, bottom - bw), (tx + half * .2, h * k - 1), (tx + half, bottom - bw)], fill=p["bg"])
        d.line([(tx - half, bottom - bw / 2), (tx + half * .2, h * k - 1), (tx + half, bottom - bw / 2)],
               fill=p["edge"], width=bw, joint="curve")
    # 名牌
    nf = _font(round(10.5 * scale * k))
    nw = nf.getlength(name) + 16 * scale * k
    duck = p.get("deco") == "duck"
    extra = 16 * scale * k if duck else 0
    d.rounded_rectangle((12 * scale * k, 0, 12 * scale * k + nw + extra, tag_h * 1.9), 7 * scale * k,
                        fill=p["accent"], outline=p["edge"], width=max(1, round(1.8 * scale * k)))
    d.text((12 * scale * k + extra + nw / 2, tag_h * .95), name, font=nf, fill=p["tag_text"], anchor="mm")
    if duck:
        draw_duck(d, 12 * scale * k + 13 * scale * k, tag_h * .95, 6 * scale * k)
    # 文字（打字機效果）
    font = _font(round(13 * scale * k))
    y = top + 14 * scale * k + 2 * scale * k
    left = 14 * scale * k
    remaining = shown
    for line in lines:
        part = line[:max(0, remaining)]
        remaining -= len(line)
        d.text((left, y), part, font=font, fill=p["text"], anchor="lt")
        y += 20 * scale * k
    if caret:
        cx, cy = w * k - 16 * scale * k, bottom - 12 * scale * k
        s = 4 * scale * k
        d.polygon([(cx - s, cy - s * .6), (cx + s, cy - s * .6), (cx, cy + s * .7)], fill=p["accent"])
    small = img.resize((w, h), Image.LANCZOS)
    alpha = small.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
    out = Image.new("RGB", small.size, chroma)
    out.paste(small.convert("RGB"), mask=alpha)
    return out


# ─────────────────────── 選用的 Claude Code hook ───────────────────────
#
# 「需要你允許」這件事對話紀錄裡看不出來，只有 Claude Code 的 Notification hook
# 知道。hook 指令是一支 PowerShell 小腳本：把 Claude Code 從 stdin 給的通知 JSON
# 原封不動存成檔案，寵物再去讀。指令裡不放任何 $，bash 和 cmd 跑起來都一樣。

HOOK_MARK = "claudequota_pet_hook.ps1"
_HOOK_PS1 = (
    "# ClaudeQuota desktop pet: save Claude Code's notification so the pet knows Claude is waiting for you.\n"
    "$raw = [Console]::In.ReadToEnd()\n"
    "$out = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'claudequota_pet_hook.json'\n"
    "[IO.File]::WriteAllText($out, $raw)\n"
)


def hook_paths(data_dir: str) -> tuple[str, str]:
    """(hook 腳本, hook 寫出來的通知檔)"""
    return os.path.join(data_dir, HOOK_MARK), os.path.join(data_dir, "claudequota_pet_hook.json")


def _hook_command(script: str) -> str:
    return f'powershell -NoProfile -ExecutionPolicy Bypass -File "{script.replace(os.sep, "/")}"'


def _load_settings(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)  # 格式壞掉就讓它丟出去，不能蓋掉使用者的設定
    if not isinstance(data, dict):
        raise ValueError("settings.json is not an object")
    return data


def hook_installed(settings_path: str) -> bool:
    try:
        data = _load_settings(settings_path)
    except (OSError, ValueError):
        return False
    return HOOK_MARK in json.dumps(data.get("hooks", {}).get("Notification", []))


def install_hook(settings_path: str, data_dir: str) -> None:
    """在 Claude Code 的使用者設定加一個 Notification hook。第一次改之前先留備份。"""
    script, _ = hook_paths(data_dir)
    os.makedirs(data_dir, exist_ok=True)
    with open(script, "w", encoding="utf-8") as f:
        f.write(_HOOK_PS1)
    data = _load_settings(settings_path)
    if HOOK_MARK in json.dumps(data.get("hooks", {}).get("Notification", [])):
        return
    if os.path.exists(settings_path) and not os.path.exists(settings_path + ".claudequota.bak"):
        shutil.copy2(settings_path, settings_path + ".claudequota.bak")
    hooks = data.setdefault("hooks", {})
    hooks.setdefault("Notification", []).append(
        {"matcher": "", "hooks": [{"type": "command", "command": _hook_command(script)}]})
    _write_settings(settings_path, data)


def uninstall_hook(settings_path: str, data_dir: str) -> None:
    """只拿掉自己加的那一條，其他 hook 原封不動。"""
    data = _load_settings(settings_path)
    hooks = data.get("hooks", {})
    kept = []
    for g in hooks.get("Notification", []):
        inner = [h for h in g.get("hooks", []) if HOOK_MARK not in str(h.get("command", ""))]
        if inner:
            kept.append(dict(g, hooks=inner))
    if kept:
        hooks["Notification"] = kept
    else:
        hooks.pop("Notification", None)
    if not hooks:
        data.pop("hooks", None)
    _write_settings(settings_path, data)
    for path in hook_paths(data_dir):
        try:
            os.remove(path)
        except OSError:
            pass


def _write_settings(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
