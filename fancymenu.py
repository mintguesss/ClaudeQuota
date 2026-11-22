"""自己畫的右鍵選單和確認對話框，取代 Windows 預設的灰色方框；有幾套主題可以換。

選單項目用 dict 描述，每次打開都重新產生（勾選狀態才會是最新的）：
    {"label": "顯示寵物", "command": fn, "checked": True}        有勾勾的開關
    {"label": "史萊姆", "command": fn, "checked": True, "radio": True}   單選
    {"label": "角色", "submenu": [...] 或 回傳清單的函式}          子選單
    {"label": "...", "enabled": False}                             灰掉
    "-"                                                            分隔線

整張選單用 Pillow 畫成一張圖（圓角卡片、主題色邊框、最上面一塊名牌），跟寵物的對話框
泡泡同一套畫法。外面點一下、按 Esc 或選了項目就關掉。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import tkinter as tk

from PIL import Image, ImageDraw, ImageFont, ImageTk

CHROMA = "#010203"
_CHROMA = (1, 2, 3)
SS = 3                        # 先放大畫再縮小，圓角和字才平滑
ROW, SEP_H, PAD, RADIUS, HEAD = 30, 9, 7, 12, 30
LEFT, RIGHT = 34, 30          # 左邊放勾勾、右邊放子選單箭頭的空間
SWITCH_MS = 260               # 游標離開有子選單的那列多久才換：斜斜移向子選單時不會被打斷

# 主題：選單、確認對話框、寵物對話框泡泡共用同一組顏色
THEMES = {
    # 遊戲對話框：米色紙、深色粗框、橘色名牌
    "paper": {"bg": (255, 251, 238), "edge": (52, 48, 64), "edge_w": 2.2, "inner": (234, 222, 196),
              "hover": (250, 232, 196), "text": (40, 34, 48), "faint": (170, 160, 150), "accent": (232, 128, 72),
              "tag_text": (255, 255, 255), "sep": (232, 220, 194)},
    "night": {"bg": (34, 32, 50), "edge": (110, 104, 150), "edge_w": 1.6, "inner": (52, 49, 74),
              "hover": (62, 58, 92), "text": (236, 233, 250), "faint": (120, 116, 146), "accent": (255, 170, 120),
              "tag_text": (34, 32, 50), "sep": (62, 59, 86)},
    "sakura": {"bg": (255, 245, 248), "edge": (214, 130, 160), "edge_w": 1.8, "inner": (250, 222, 232),
               "hover": (255, 225, 236), "text": (80, 40, 56), "faint": (190, 150, 166), "accent": (228, 96, 140),
               "tag_text": (255, 255, 255), "sep": (246, 214, 226)},
    "mint": {"bg": (241, 252, 247), "edge": (96, 170, 140), "edge_w": 1.8, "inner": (210, 240, 226),
             "hover": (214, 243, 230), "text": (28, 60, 50), "faint": (140, 176, 162), "accent": (38, 156, 116),
             "tag_text": (255, 255, 255), "sep": (204, 232, 220)},
}
_theme = "paper"
_BUILTIN = tuple(THEMES)
LABELS: dict[str, str] = {}   # 自訂主題的顯示名稱（內建主題用 i18n 翻譯）


def load_custom_themes(path: str) -> None:
    """讀使用者自己的主題（例如放在 pets/themes.json，不會進版本控制）。

    格式：{"主題代號": {"label": "顯示名稱", "bg": [r, g, b], "accent": [...], ...,
                       "deco": "duck", "mark": "x‿x"}}
    沒寫的顏色沿用「遊戲紙」。deco 是名牌上的小圖案，mark 是選單右下角的小字。
    """
    for name in [n for n in THEMES if n not in _BUILTIN]:
        del THEMES[name]
    LABELS.clear()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    for name, spec in data.items():
        if name in _BUILTIN or not isinstance(spec, dict):
            continue
        theme = dict(THEMES["paper"])
        for key, value in spec.items():
            if key in ("label", "deco", "mark"):
                continue
            if key == "edge_w":
                theme[key] = float(value)
            elif isinstance(value, (list, tuple)) and len(value) == 3:
                theme[key] = tuple(int(v) for v in value)
        theme["deco"] = spec.get("deco")
        theme["mark"] = spec.get("mark")
        THEMES[name] = theme
        LABELS[name] = str(spec.get("label", name))


def set_theme(name: str):
    global _theme
    _theme = name if name in THEMES else "paper"


def theme() -> dict:
    return THEMES[_theme]


def theme_name() -> str:
    return _theme


def _hex(c) -> str:
    return "#%02x%02x%02x" % c


def _font(size: int, bold=False) -> ImageFont.FreeTypeFont:
    for name in (("msjhbd.ttc", "seguisb.ttf") if bold else ("msjh.ttc", "segoeui.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _cursor() -> tuple[int, int]:
    try:
        p = wt.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y
    except Exception:
        return 0, 0


def _mouse_down() -> bool:
    try:
        u = ctypes.windll.user32
        return bool(u.GetAsyncKeyState(0x01) & 0x8000 or u.GetAsyncKeyState(0x02) & 0x8000)
    except Exception:
        return False


def _work_area(root: tk.Misc) -> tuple[int, int, int, int]:
    try:
        r = wt.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def _make_tool_window(win: tk.Toplevel):
    """不出現在工作列和 Alt+Tab。"""
    try:
        win.update_idletasks()
        u = ctypes.windll.user32
        hwnd = u.GetParent(win.winfo_id()) or win.winfo_id()
        get = getattr(u, "GetWindowLongPtrW", u.GetWindowLongW)
        put = getattr(u, "SetWindowLongPtrW", u.SetWindowLongW)
        get.restype = put.restype = ctypes.c_ssize_t
        get.argtypes = [ctypes.c_void_p, ctypes.c_int]
        put.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        put(hwnd, -20, get(hwnd, -20) | 0x80)
    except Exception:
        pass


class FancyMenu:
    """一層選單。子選單是另一個 FancyMenu，掛在 child 上。"""

    def __init__(self, root: tk.Misc, dpi: float, items: list, x: int, y: int,
                 parent: "FancyMenu | None" = None, anchor_bottom: bool = False, title: str | None = None,
                 align_bottom: int | None = None, avoid: tuple | None = None):
        self.root, self.k, self.parent = root, dpi, parent
        self.items = [i for i in items if i]
        self.title = title
        self.child: FancyMenu | None = None
        self.child_row: int | None = None
        self.hot: int | None = None
        self._job = None
        self.t = theme()
        self.font = _font(round(13 * dpi * SS))
        self.head = HEAD * dpi if title else 0
        self._layout()
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-transparentcolor", CHROMA)
        except tk.TclError:
            pass
        self.label = tk.Label(self.win, bd=0, bg=CHROMA)
        self.label.pack()
        left, top, right, bottom = _work_area(root)
        if anchor_bottom:                     # 從工作列叫出來：往上長
            y -= self.h
        elif y + self.h > bottom:            # 子選單放不下：底部對齊游標那一列，往上長
            y = (align_bottom if align_bottom is not None else bottom) - self.h
        if x + self.w > right:
            x = (parent.x - self.w + round(6 * dpi)) if parent else right - self.w
        self.x, self.y = max(left, x), max(top, min(y, bottom - self.h))
        if avoid:
            self.x, self.y = _clear_of(avoid, self.x, self.y, self.w, self.h, (left, top, right, bottom),
                                       round(8 * dpi))
        self.win.geometry(f"{self.w}x{self.h}+{self.x}+{self.y}")
        _make_tool_window(self.win)
        self.label.bind("<Motion>", self._motion)
        self.label.bind("<Enter>", self._enter)
        self.label.bind("<Leave>", self._leave)
        self.label.bind("<ButtonRelease-1>", self._click)
        self.win.bind("<Escape>", lambda _e: self.close_all())
        self._paint()
        if parent is None:
            self.win.focus_force()
            self._watch()

    # ── 版面 ──
    def _layout(self):
        k = self.k
        rows, y = [], PAD * k + self.head
        widths = [130 * k]
        if self.title:
            widths.append(_font(round(11 * k * SS), True).getlength(self.title) / SS + 40 * k)
        for it in self.items:
            if it == "-":
                rows.append((y, y + SEP_H * k))
                y += SEP_H * k
            else:
                rows.append((y, y + ROW * k))
                y += ROW * k
                widths.append(self.font.getlength(it["label"]) / SS + (LEFT + RIGHT) * k)
        self.rows = rows
        self.w = round(max(widths) + PAD * 2 * k)
        self.h = round(y + PAD * k + (12 * k if self.t.get("mark") else 0))  # 右下角小字的位置

    def _row_at(self, py) -> int | None:
        for i, (a, b) in enumerate(self.rows):
            if a <= py < b and self.items[i] != "-":
                return i
        return None

    # ── 畫 ──
    def _paint(self):
        k, s, T = self.k, SS, self.t
        img = Image.new("RGB", (self.w * s, self.h * s), _CHROMA)
        d = ImageDraw.Draw(img)
        ew = max(1, round(T["edge_w"] * k * s))
        d.rounded_rectangle((ew / 2, ew / 2, self.w * s - ew / 2 - 1, self.h * s - ew / 2 - 1), RADIUS * k * s,
                            fill=T["bg"], outline=T["edge"], width=ew)
        inset = 4 * k * s
        d.rounded_rectangle((inset, inset, self.w * s - inset, self.h * s - inset), RADIUS * 0.6 * k * s,
                            outline=T["inner"], width=max(1, round(k * s)))
        if self.title:  # 名牌：跟寵物對話框的名牌同一個樣子
            tf = _font(round(11 * k * s), True)
            duck = T.get("deco") == "duck"
            extra = 18 * k * s if duck else 0
            tw = tf.getlength(self.title) + 22 * k * s + extra
            top = (PAD + 1) * k * s
            x0 = PAD * 2 * k * s
            d.rounded_rectangle((x0, top, x0 + tw, top + (HEAD - 8) * k * s), 8 * k * s, fill=T["accent"])
            mid = top + (HEAD - 8) / 2 * k * s
            d.text((x0 + extra + (tw - extra) / 2, mid), self.title, font=tf, fill=T["tag_text"], anchor="mm")
            if duck:
                import pet  # 小鴨的畫法跟寵物對話框共用
                pet.draw_duck(d, x0 + 13 * k * s, mid + 1 * k * s, 6.5 * k * s)
        if T.get("mark"):  # 右下角的小字
            _draw_mark(d, T["mark"], (self.w - PAD * 2 * k) * s, (self.h - PAD * 1.5 * k) * s,
                       _font(round(10 * k * s), True), T["faint"], k * s)
        for i, it in enumerate(self.items):
            a, b = self.rows[i]
            if it == "-":
                mid = (a + b) / 2 * s
                d.line((PAD * 2 * k * s, mid, (self.w - PAD * 2 * k) * s, mid), fill=T["sep"],
                       width=max(1, round(k * s)))
                continue
            enabled = it.get("enabled", True)
            if (i == self.hot or i == self.child_row) and enabled:
                d.rounded_rectangle((PAD * k * s, a * s, (self.w - PAD * k) * s, b * s), 8 * k * s, fill=T["hover"])
            cy = (a + b) / 2 * s
            color = T["text"] if enabled else T["faint"]
            d.text(((PAD + LEFT) * k * s, cy), it["label"], font=self.font, fill=color, anchor="lm")
            cx = (PAD + LEFT / 2) * k * s
            if it.get("checked"):
                if it.get("radio") and T.get("deco") == "duck":  # 選中的那個換成一隻小鴨
                    import pet
                    pet.draw_duck(d, cx - 1 * k * s, cy + 0.5 * k * s, 6 * k * s)
                elif it.get("radio"):
                    r = 4.2 * k * s
                    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=T["accent"])
                else:
                    d.line((cx - 5 * k * s, cy, cx - 1.5 * k * s, cy + 4 * k * s, cx + 6 * k * s, cy - 4.5 * k * s),
                           fill=T["accent"], width=max(2, round(2.2 * k * s)), joint="curve")
            elif it.get("radio"):
                r = 4.2 * k * s
                d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=T["faint"], width=max(1, round(1.4 * k * s)))
            if it.get("submenu"):
                ax = (self.w - (PAD + RIGHT / 2) * k) * s   # self.w 已經是實際像素
                h = 4 * k * s
                d.line((ax - h / 2, cy - h, ax + h / 2, cy, ax - h / 2, cy + h), fill=color,
                       width=max(2, round(1.6 * k * s)), joint="curve")
        img = img.resize((self.w, self.h), Image.LANCZOS)
        # 縮小後邊緣混到 chroma 的像素會變成一圈黑邊：接近 chroma 的一律當透明
        px = img.load()
        for yy in range(self.h):
            for xx in (*range(0, 6), *range(self.w - 6, self.w)):
                if sum(abs(c - e) for c, e in zip(px[xx, yy], _CHROMA)) < 90:
                    px[xx, yy] = _CHROMA
        self._photo = ImageTk.PhotoImage(img)
        self.label.configure(image=self._photo)

    # ── 滑鼠 ──
    def _cancel(self):
        if self._job:
            self.root.after_cancel(self._job)
            self._job = None

    def _motion(self, e):
        row = self._row_at(e.y)
        if row == self.hot:
            return
        self.hot = row
        self._paint()
        self._cancel()
        if row is None or row == self.child_row:
            return
        it = self.items[row]
        opens = it.get("submenu") and it.get("enabled", True)
        # 有子選單開著的時候不馬上換：游標可能正斜斜移向子選單，經過別列一下而已
        delay = SWITCH_MS if self.child else 140
        if opens:
            self._job = self.root.after(delay, lambda r=row: self._open_child(r))
        elif self.child:
            self._job = self.root.after(delay, self._close_child)

    def _enter(self, _e):
        if self.parent:  # 游標已經進到子選單：取消父選單裡等著要換掉它的排程
            self.parent._cancel()
            self.parent.hot = self.parent.child_row
            self.parent._paint()

    def _leave(self, _e):
        if self.child is None and self.hot is not None:
            self.hot = None
            self._paint()

    def _close_child(self):
        self._job = None
        if self.child:
            self.child.close()
            self.child = None
            self.child_row = None
            self._paint()

    def _open_child(self, row):
        self._job = None
        if self.child:
            self.child.close()
        it = self.items[row]
        sub = it["submenu"]() if callable(it["submenu"]) else it["submenu"]
        a, b = self.rows[row]
        self.child_row = row
        self.child = FancyMenu(self.root, self.k, sub, self.x + self.w - round(8 * self.k),
                               self.y + round(a - PAD * self.k), parent=self,
                               align_bottom=self.y + round(b + PAD * self.k))
        self._paint()

    def _click(self, e):
        row = self._row_at(e.y)
        if row is None:
            return
        it = self.items[row]
        if not it.get("enabled", True):
            return
        if it.get("submenu"):
            self._cancel()
            self._open_child(row)
            return
        self.close_all()
        if it.get("command"):
            self.root.after(0, it["command"])

    # ── 關閉 ──
    def _windows(self):
        m = self
        while m:
            yield m
            m = m.child

    def _watch(self):
        """在選單外面按滑鼠就關掉（overrideredirect 視窗收不到可靠的失焦事件，所以用輪詢）。"""
        try:
            if not self.win.winfo_exists():
                return
        except tk.TclError:
            return
        if _mouse_down():
            x, y = _cursor()
            if not any(m.x <= x < m.x + m.w and m.y <= y < m.y + m.h for m in self._windows()):
                self.close_all()
                return
        self.win.after(60, self._watch)

    def close(self):
        self._cancel()
        if self.child:
            self.child.close()
            self.child = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def close_all(self):
        top = self
        while top.parent:
            top = top.parent
        top.close()


def _draw_mark(d, text: str, right: float, bottom: float, font, fill, unit: float):
    """右下角的小字，靠右下對齊。「‿」常用字型裡沒有，遇到就自己畫一道笑弧。"""
    parts = text.split("‿")
    smile = 7 * unit
    widths = [font.getlength(p) for p in parts]
    total = sum(widths) + smile * (len(parts) - 1)
    x = right - total
    for i, (part, w) in enumerate(zip(parts, widths)):
        d.text((x, bottom), part, font=font, fill=fill, anchor="lb")
        x += w
        if i < len(parts) - 1:
            top = bottom - 6 * unit
            d.arc((x + 0.5 * unit, top - 4 * unit, x + smile - 0.5 * unit, top + 1.5 * unit), 20, 160, fill=fill,
                  width=max(1, round(1.4 * unit)))
            x += smile


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _clear_of(avoid, x, y, w, h, area, gap):
    """把一個方框挪到不蓋住 avoid（例如寵物）的地方：先試右邊，再左邊、上面、下面。"""
    left, top, right, bottom = area
    if not _overlaps((x, y, x + w, y + h), avoid):
        return x, y
    ax0, ay0, ax1, ay1 = avoid
    clamp_y = max(top, min(y, bottom - h))
    clamp_x = max(left, min(x, right - w))
    for cx, cy in ((ax1 + gap, clamp_y), (ax0 - gap - w, clamp_y), (clamp_x, ay0 - gap - h), (clamp_x, ay1 + gap)):
        if left <= cx and cx + w <= right and top <= cy and cy + h <= bottom:
            return cx, cy
    return x, y


def popup(root: tk.Misc, dpi: float, items: list, x: int | None = None, y: int | None = None,
          from_taskbar: bool = False, title: str | None = None, avoid: tuple | None = None) -> FancyMenu:
    if x is None or y is None:
        x, y = _cursor()
    return FancyMenu(root, dpi, items, x, y, anchor_bottom=from_taskbar, title=title, avoid=avoid)


def clear_of(root: tk.Misc, avoid, x, y, w, h, gap) -> tuple[int, int]:
    """給其他視窗（例如寵物的對話框）用：在畫面範圍內找一個不蓋住 avoid 的位置。"""
    return _clear_of(avoid, x, y, w, h, _work_area(root), gap)


# ─────────────────────────────── 確認對話框 ───────────────────────────────

def ask(root: tk.Misc, dpi: float, title: str, body: str, yes: str, no: str) -> bool:
    """跟選單同一個主題的「是／否」對話框。會等使用者選完才回傳。"""
    k, T = dpi, theme()
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    W = round(440 * k)
    card = _hex(T["bg"])
    outer = tk.Frame(win, bg=_hex(T["edge"]), padx=max(1, round(T["edge_w"] * k / 1.5)),
                     pady=max(1, round(T["edge_w"] * k / 1.5)))
    outer.pack()
    inner = tk.Frame(outer, bg=card, padx=round(22 * k), pady=round(18 * k))
    inner.pack()
    tk.Label(inner, text=title, bg=card, fg=_hex(T["text"]), font=("Microsoft JhengHei UI", 13, "bold"),
             anchor="w", justify="left", wraplength=W - round(44 * k)).pack(fill="x")
    tk.Frame(inner, bg=_hex(T["accent"]), height=max(2, round(3 * k)), width=round(36 * k)).pack(
        anchor="w", pady=(round(6 * k), round(12 * k)))
    tk.Label(inner, text=body, bg=card, fg=_hex(T["text"]), font=("Microsoft JhengHei UI", 10), anchor="w",
             justify="left", wraplength=W - round(44 * k)).pack(fill="x")
    result = tk.BooleanVar(win, value=False)
    done = tk.BooleanVar(win, value=False)
    row = tk.Frame(inner, bg=card)
    row.pack(fill="x", pady=(round(18 * k), 0))

    def button(text, primary, value):
        bg = _hex(T["accent"] if primary else T["hover"])
        fg = _hex(T["tag_text"] if primary else T["text"])
        hover = _hex(tuple(max(0, c - 22) for c in (T["accent"] if primary else T["hover"])))
        b = tk.Label(row, text=text, bg=bg, fg=fg, font=("Microsoft JhengHei UI", 10, "bold"),
                     padx=round(16 * k), pady=round(6 * k), cursor="hand2")
        b.bind("<Enter>", lambda _e: b.configure(bg=hover))
        b.bind("<Leave>", lambda _e: b.configure(bg=bg))
        b.bind("<ButtonRelease-1>", lambda _e: (result.set(value), done.set(True)))
        return b

    button(yes, True, True).pack(side="right")
    button(no, False, False).pack(side="right", padx=(0, round(8 * k)))
    win.bind("<Escape>", lambda _e: done.set(True))
    win.bind("<Return>", lambda _e: (result.set(True), done.set(True)))
    win.update_idletasks()
    left, top, right, bottom = _work_area(root)
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    win.geometry(f"+{(left + right - w) // 2}+{(top + bottom - h) // 2}")
    _make_tool_window(win)
    win.focus_force()
    win.grab_set()
    win.wait_variable(done)
    try:
        win.grab_release()
        win.destroy()
    except tk.TclError:
        pass
    return bool(result.get())


def ask_text(root: tk.Misc, dpi: float, title: str, fields: list[str], ok: str, cancel: str) -> list[str] | None:
    """跟選單同一個主題的輸入框（例如新增常用網站的名稱和網址）。取消就回 None。"""
    k, T = dpi, theme()
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    card = _hex(T["bg"])
    outer = tk.Frame(win, bg=_hex(T["edge"]), padx=max(1, round(T["edge_w"] * k / 1.5)),
                     pady=max(1, round(T["edge_w"] * k / 1.5)))
    outer.pack()
    inner = tk.Frame(outer, bg=card, padx=round(22 * k), pady=round(18 * k))
    inner.pack()
    tk.Label(inner, text=title, bg=card, fg=_hex(T["text"]), font=("Microsoft JhengHei UI", 13, "bold"),
             anchor="w").pack(fill="x")
    tk.Frame(inner, bg=_hex(T["accent"]), height=max(2, round(3 * k)), width=round(36 * k)).pack(
        anchor="w", pady=(round(6 * k), round(10 * k)))
    entries = []
    for label in fields:
        tk.Label(inner, text=label, bg=card, fg=_hex(T["text"]), font=("Microsoft JhengHei UI", 10),
                 anchor="w").pack(fill="x", pady=(round(4 * k), 0))
        e = tk.Entry(inner, font=("Microsoft JhengHei UI", 10), width=36, relief="flat", bg=_hex(T["hover"]),
                     fg=_hex(T["text"]), insertbackground=_hex(T["text"]), highlightthickness=1,
                     highlightbackground=_hex(T["inner"]), highlightcolor=_hex(T["accent"]))
        e.pack(fill="x", ipady=round(4 * k))
        entries.append(e)
    result: list = []
    done = tk.BooleanVar(win, value=False)
    row = tk.Frame(inner, bg=card)
    row.pack(fill="x", pady=(round(16 * k), 0))

    def finish(ok_pressed):
        if ok_pressed:
            result.extend(e.get().strip() for e in entries)
        done.set(True)

    for text, primary in ((ok, True), (cancel, False)):
        bg = _hex(T["accent"] if primary else T["hover"])
        b = tk.Label(row, text=text, bg=bg, fg=_hex(T["tag_text"] if primary else T["text"]),
                     font=("Microsoft JhengHei UI", 10, "bold"), padx=round(16 * k), pady=round(6 * k),
                     cursor="hand2")
        b.bind("<ButtonRelease-1>", lambda _e, v=primary: finish(v))
        b.pack(side="right", padx=(round(8 * k), 0))
    win.bind("<Escape>", lambda _e: finish(False))
    win.bind("<Return>", lambda _e: finish(True))
    win.update_idletasks()
    left, top, right, bottom = _work_area(root)
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    win.geometry(f"+{(left + right - w) // 2}+{(top + bottom - h) // 2}")
    _make_tool_window(win)
    win.focus_force()
    entries[0].focus_set()
    win.grab_set()
    win.wait_variable(done)
    try:
        win.grab_release()
        win.destroy()
    except tk.TclError:
        pass
    return result or None
