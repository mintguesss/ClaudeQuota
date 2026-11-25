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

import chat_state
import claude_state
import fancymenu
import i18n
import pet
import quota_source
from i18n import t

APP_DIR = os.path.dirname(os.path.abspath(__file__))
# 打包成 exe 時 __file__ 在每次啟動都不同的暫存資料夾裡，設定存那裡會一關就消失，
# 所以改放 %APPDATA%\ClaudeQuota。直接跑原始碼時維持放在程式旁邊。
if getattr(sys, "frozen", False):
    DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "ClaudeQuota")
    os.makedirs(DATA_DIR, exist_ok=True)
else:
    DATA_DIR = APP_DIR
STATE_FILE = os.path.join(DATA_DIR, "widget_state.json")
# 讀檔有快取（檔案沒變就不重解析），一次 tick 幾乎沒有成本；畫面也只在內容真的變了
# 才重繪。真正的資料源頭是桌面版每 5 分鐘寫一次檔，所以再短也不會更即時，
# 縮短只是讓倒數與過期提示跟得上。
REFRESH_SEC = 10

# 沒有進行中的視窗時，自動打一次最小呼叫把它點著。冷卻時間要大於桌面版的
# 5 分鐘取樣間隔，否則會在快取更新前重複觸發。
AUTO_POKE_COOLDOWN_MIN = 12
# 連續起算幾次視窗都沒開，就停止自動起算（避免每 12 分鐘白花一次）
AUTO_POKE_MAX_TRIES = 2
AUTO_POKE_MAX_AGE_MIN = 30  # 用量資料比這個舊就不自動起算

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
    "en_l": r"C:\Windows\Fonts\segoeuil.ttf",
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
LOGO_PATH = os.path.join(DATA_DIR, "claude_logo.png")
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


def _gauge_icon(size: int) -> Image.Image:
    """程式自己的圖示（橘底 + 儀表環），跟 assets/icon.ico 同一個圖案。"""
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, s - 1, s - 1], s * 0.2, fill=CLAUDE_ORANGE)
    w = round(s * 0.12)
    box = [s * 0.2, s * 0.2, s * 0.8, s * 0.8]
    d.arc(box, 0, 360, fill=(236, 170, 148), width=w)
    d.arc(box, -90, -90 + 0.68 * 360, fill=(255, 255, 255), width=w)
    return img.resize((size, size), Image.LANCZOS)


def _app_icon() -> str:
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.join(APP_DIR, "assets", "icon.ico")


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


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]


def taskbar_slot(sw: int, sh: int, w: int, h: int) -> tuple[int, int]:
    """橫條的預設位置：工作列左側、垂直置中。

    量不到工作列（例如自動隱藏）就退回螢幕左下角。
    """
    try:
        u = _u32()
        tray = u.FindWindowW("Shell_TrayWnd", None)
        bar = RECT()
        if tray and u.GetWindowRect(tray, ctypes.byref(bar)) and bar.bottom - bar.top >= h:
            return 35, bar.top + (bar.bottom - bar.top - h) // 2
    except Exception:
        pass
    return 35, max(0, sh - h - 14)


def taskbar_height(sh: int) -> int | None:
    """工作列的高度；工作列不在底下或量不到就 None。"""
    top = taskbar_top(sh)
    return sh - top if top < sh else None


def taskbar_top(sh: int) -> int:
    """工作列上緣的 y。工作列不在底下或自動隱藏時，就當作螢幕底邊。"""
    try:
        u = _u32()
        tray = u.FindWindowW("Shell_TrayWnd", None)
        bar = RECT()
        if tray and u.GetWindowRect(tray, ctypes.byref(bar)) and bar.top > sh // 2 and bar.top < sh:
            return bar.top
    except Exception:
        pass
    return sh


# ────────────────── 讓浮動視窗待得住（但該讓的時候讓開） ──────────────────
#
# 無邊框置頂視窗在 Windows 上有三種各自獨立的消失方式，要三種解法：
#   1. Win+D／「顯示桌面」會收掉所有出現在工作列上的視窗
#      → 標成 tool window 就不在那份名單裡（桌面元件類的程式都這樣做）
#   2. 切到別的虛擬桌面時，視窗只留在它被建立的那個桌面
#      → 發現桌面換了就重新 show 一次，視窗會被指派到目前的桌面
#   3. 沉到工作列後面——按 Win+D 時真正發生的就是這件事
#      實測：Win+D 之後視窗的 visible／rect／DWM cloak 全都沒變，變的只有 z 序。
#      橫條的預設位置就在工作列的矩形裡，而工作列自己也是 topmost 視窗，
#      Win+D 會把桌面和工作列拉到最上面，橫條就沉到工作列後面看不到了。
#      同一個 topmost 層裡誰在上面沒有辦法固定，只能每趟都重新宣告一次置頂。
#      → 唯一例外是全螢幕的遊戲或影片：那種時候不搶，直接收起來讓開（見 fullscreen_monitor）
# 這些 tkinter 都做不到，只能直接動 Win32 API。

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4  # 顯示但不搶焦點——不能打斷使用者正在打字的視窗
MONITOR_DEFAULTTONEAREST = 2

# 看門狗巡邏間隔。這同時也是 Win+D 之後橫條「消失」的最長時間，所以要短；
# 一趟只有幾個很便宜的 API 呼叫，250ms 的成本可以忽略。
GUARD_MS = 250

# 這些滿版的殼層視窗不算「全螢幕程式」。少了這幾個，按 Win+D 回到桌面
# 或叫出工作檢視就會被誤判成在玩遊戲。
SHELL_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow",
    "ForegroundStaging", "MultitaskingViewFrame",
}

_u32_ready = False


def _u32():
    """user32，並且把會碰到視窗 handle 的函式宣告成 c_void_p。

    ctypes 預設把回傳值當 c_int，64 位元下 handle 會被截斷成垃圾值。
    """
    global _u32_ready
    u = ctypes.windll.user32
    if not _u32_ready:
        for name in ("GetParent", "GetForegroundWindow", "FindWindowW", "MonitorFromWindow"):
            getattr(u, name).restype = ctypes.c_void_p
        u.GetParent.argtypes = [ctypes.c_void_p]
        u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        u.IsWindowVisible.argtypes = [ctypes.c_void_p]
        u.IsIconic.argtypes = [ctypes.c_void_p]
        u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
        u.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        u.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        u.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
        u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        _u32_ready = True
    return u


EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_SYSTEM_MINIMIZESTART = 0x0016
EVENT_SYSTEM_MINIMIZEEND = 0x0017
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002

WinEventProc = ctypes.WINFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
    ctypes.c_long, ctypes.c_long, ctypes.c_uint, ctypes.c_uint)


def hook_shell_events(callback):
    """前景視窗改變／視窗最小化時立刻回呼。

    光靠看門狗定期巡是「事後補救」，兩趟之間的空窗使用者看得見——按 Win+D
    會閃一下就是這個。這些事件是 z 序被動的當下就送到，搶回置頂快得多。

    回呼是在 Tk 的訊息迴圈裡被叫的，所以裡面只能碰 Win32，絕對不要碰 Tk。
    回傳值一定要留著：proc 被回收掉，hook 就會打到一個不存在的位址。
    """
    try:
        u = _u32()
        u.SetWinEventHook.restype = ctypes.c_void_p
        u.SetWinEventHook.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
                                      WinEventProc, ctypes.c_uint, ctypes.c_uint,
                                      ctypes.c_uint]
        proc = WinEventProc(lambda *_: callback())
        hooks = [
            u.SetWinEventHook(ev, ev, None, proc, 0, 0,
                              WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
            for ev in (EVENT_SYSTEM_FOREGROUND,
                       EVENT_SYSTEM_MINIMIZESTART, EVENT_SYSTEM_MINIMIZEEND)
        ]
        return proc, hooks
    except Exception:
        return None  # 掛不上就只剩看門狗，會閃但不會壞


def fullscreen_monitor() -> int | None:
    """前景如果是佔滿整個螢幕的程式（遊戲、全螢幕影片、簡報），回傳它所在的螢幕。

    判準就是「視窗矩形蓋滿所在螢幕」——獨佔全螢幕的遊戲和瀏覽器的 F11／YouTube
    全螢幕都符合，一般的最大化視窗因為讓出了工作列的高度所以不符合。
    只回傳螢幕而不是 True／False，是為了多螢幕：在別的螢幕玩遊戲時，
    停在這一螢幕的橫條沒有理由跟著消失。
    """
    try:
        u = _u32()
        fg = u.GetForegroundWindow()
        if not fg:
            return None
        cls = ctypes.create_unicode_buffer(96)
        u.GetClassNameW(fg, cls, 96)
        if cls.value in SHELL_CLASSES:
            return None

        win = RECT()
        if not u.GetWindowRect(fg, ctypes.byref(win)):
            return None
        mon = u.MonitorFromWindow(fg, MONITOR_DEFAULTTONEAREST)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not mon or not u.GetMonitorInfoW(mon, ctypes.byref(info)):
            return None
        m = info.rcMonitor
        covers = (win.left <= m.left and win.top <= m.top
                  and win.right >= m.right and win.bottom >= m.bottom)
        return mon if covers else None
    except Exception:
        return None


def window_handle(win: tk.Misc) -> int | None:
    """取得 tkinter 視窗真正的 HWND。

    winfo_id() 給的是 Tk 自己那層子視窗，真正的 top-level 是它的父視窗。
    """
    try:
        win.update_idletasks()
        wid = win.winfo_id()
    except tk.TclError:
        return None
    try:
        return _u32().GetParent(wid) or wid
    except Exception:
        return wid


def make_tool_window(hwnd: int | None) -> None:
    """標成 tool window，讓「顯示桌面」收不到它；順便從 Alt+Tab 清單消失。"""
    if not hwnd:
        return
    try:
        u = _u32()
        get = getattr(u, "GetWindowLongPtrW", u.GetWindowLongW)
        put = getattr(u, "SetWindowLongPtrW", u.SetWindowLongW)
        get.restype = put.restype = ctypes.c_ssize_t
        get.argtypes = [ctypes.c_void_p, ctypes.c_int]
        put.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        ex = get(hwnd, GWL_EXSTYLE)
        if not ex & WS_EX_TOOLWINDOW:
            put(hwnd, GWL_EXSTYLE, ex | WS_EX_TOOLWINDOW)
    except Exception:
        pass


class _GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]


class VirtualDesktops:
    """IVirtualDesktopManager 的最小 ctypes 綁定。

    用的是公開介面（shobjidl_core.h 裡那個），不是網路上常見的 IVirtualDesktopManagerInternal
    ——後者的 IID 每個 Windows 版本都會換，綁上去等著下次更新壞掉。

    只拿來問「前景視窗在哪個桌面」。問我們自己的視窗是問不到的：tool window
    不歸虛擬桌面管理員管，會直接回 TYPE_E_ELEMENTNOTFOUND。
    """

    CLSID = "{AA509086-5CA9-4C25-8F95-589D3C07B48A}"
    IID = "{A5CD92FF-29BE-454C-8D04-D82879FB3F1B}"

    def __init__(self):
        ole = ctypes.windll.ole32
        ole.CoInitializeEx(None, 0x2)  # STA。已經初始化過會回 RPC_E_CHANGED_MODE，不影響後面
        clsid, iid = _GUID(), _GUID()
        if ole.CLSIDFromString(self.CLSID, ctypes.byref(clsid)) != 0 or \
                ole.IIDFromString(self.IID, ctypes.byref(iid)) != 0:
            raise OSError("GUID 解析失敗")
        self._p = ctypes.c_void_p()
        hr = ole.CoCreateInstance(ctypes.byref(clsid), None, 1,  # CLSCTX_INPROC_SERVER
                                  ctypes.byref(iid), ctypes.byref(self._p))
        if hr != 0 or not self._p:
            raise OSError(f"CoCreateInstance 失敗 0x{hr & 0xffffffff:08x}")
        self._vt = ctypes.cast(
            self._p, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

    def _m(self, slot: int, *argtypes):
        """取 vtable 第 slot 個方法。前三格固定是 IUnknown 的三個。"""
        return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(self._vt[slot])

    def on_current(self, hwnd: int) -> bool:
        out = ctypes.c_int(0)
        self._m(3, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))(
            self._p, hwnd, ctypes.byref(out))
        return bool(out.value)

    def desktop_of(self, hwnd: int) -> _GUID:
        gid = _GUID()
        self._m(4, ctypes.c_void_p, ctypes.POINTER(_GUID))(self._p, hwnd, ctypes.byref(gid))
        return gid

    def move(self, hwnd: int, gid: _GUID) -> None:
        self._m(5, ctypes.c_void_p, ctypes.POINTER(_GUID))(self._p, hwnd, ctypes.byref(gid))


_vdm: VirtualDesktops | None = None
_vdm_tried = False


def current_desktop_id() -> bytes | None:
    """目前這個虛擬桌面的 GUID。切桌面時這個值會變，用來當作切換的訊號。

    沒有 API 能直接問「現在在哪個桌面」，但前景視窗一定在目前的桌面上，
    問它就等於問到了。問不到（例如前景是桌面本身）就回 None，當作「不知道」——
    比猜錯好，猜錯會讓視窗每半秒閃一次。
    """
    global _vdm, _vdm_tried
    if not _vdm_tried:
        _vdm_tried = True
        try:
            _vdm = VirtualDesktops()
        except Exception:
            _vdm = None  # 沒有這個介面就放棄這一項，其他保護照常
    if _vdm is None:
        return None
    try:
        fg = _u32().GetForegroundWindow()
        return bytes(_vdm.desktop_of(fg)) if fg else None
    except OSError:
        return None


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
        return t("left.reset")
    h, m = divmod(int(delta_min), 60)
    if h and m:
        return t("left.hm", h=h, m=m)
    return t("left.h", h=h) if h else t("left.m", m=m)


def human_left_long(delta_min: float) -> str:
    """每週額度用：一天以上就講「幾天幾小時」，分鐘在這個尺度沒意義。"""
    if delta_min < 24 * 60:
        return human_left(delta_min)
    d, rest = divmod(int(delta_min), 24 * 60)
    h = rest // 60
    return t("left.dh", d=d, h=h) if h else t("left.d", d=d)


def weekday_time(when: datetime) -> str:
    """「週四 14:00」/「Thu 14:00」。星期名稱自己對照，不依賴系統 locale。"""
    local = (when + timedelta(seconds=30)).astimezone()
    return t("weekday_time", day=t("weekdays").split(",")[local.weekday()], time=f"{local:%H:%M}")


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

    環境裡有 ANTHROPIC_API_KEY（或 AUTH_TOKEN）時，`claude -p` 會優先用它，
    請求就變成按 API 計費、完全不碰訂閱額度——視窗開不起來，還白花錢。所以子程序的
    環境要把它們拿掉，逼 CLI 用訂閱登入。模型用 Haiku：只是要「有一次用量」，
    用大模型只會多吃額度。
    """
    cli = find_claude_cli()
    if not cli:
        return False, t("poke.no_cli")
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    # 在專用的資料夾裡跑、而且不存對話紀錄：不然每戳一次，你的專案裡就多一個「hi」對話，
    # 寵物也會把它當成你的對話，一直說「做完了」
    cwd = claude_state.POKE_DIR
    try:
        os.makedirs(cwd, exist_ok=True)
    except OSError:
        cwd = None
    base = [os.environ.get("COMSPEC", "cmd.exe"), "/c", cli, "-p", "hi", "--model", "haiku"]

    def run(extra):
        return subprocess.run(base + extra, capture_output=True, timeout=180, env=env, cwd=cwd,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    try:
        r = run(["--no-session-persistence"])
        err = (r.stderr or b"").decode("utf-8", "ignore").lower()
        if r.returncode != 0 and "no-session-persistence" in err:  # 舊版 CLI 沒有這個選項
            r = run([])
    except subprocess.TimeoutExpired:
        return False, t("poke.timeout")
    except OSError as e:
        return False, t("poke.start_failed", e=e)
    if r.returncode != 0:
        return False, (r.stderr or b"").decode("utf-8", "ignore").strip()[:60] or t("poke.failed")
    return True, t("poke.ok")


# ──────────────────────────────── 系統列圖示 ────────────────────────────────

# Windows 會把圖示縮到 16～28px（SM_CXSMICON，本機是 28），細節全部會糊掉，
# 所以每種樣式都先畫 256px 再讓系統縮。四種各有取捨，用系統列選單切換。
TRAY_STYLES = [
    ("burst", "style.burst"),
    ("number", "style.number"),
    ("left", "style.left"),
    ("ring", "style.ring"),
    ("pie", "style.pie"),
    ("dual", "style.dual"),
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


def _tray_burst(pct, S, week=None):
    """狀態色圓角底 + 白色星芒。最像真的 app icon，但沒有數字。"""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, S - 1, S - 1], S * 0.24,
                                          fill=_state_fill(pct) + (255,))
    mask = _burst_mask(S)
    if mask is not None:
        img.paste((255, 255, 255, 255), (0, 0), mask)
    return img


def _tray_number(pct, S, week=None, show_left=False):
    """狀態色圓角底 + 白色大數字 + 底部細進度條。

    圖示格子是 Shell 定死的 28x28（SM_CXSMICON），沒有 API 能放大，
    所以走扁平路線：字塞好塞滿、只留一條進度細線。發光或漸層在這個尺寸
    只會把字的邊緣糊掉，看起來更小。show_left=True 時數字與進度條都改成「剩餘」。
    """
    bg_pct = pct
    if show_left and pct is not None:
        pct = 100 - pct
    text = "?" if pct is None else f"{pct:.0f}"
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 底色永遠跟「已用」走，顯示剩餘時也是越少越紅
    d.rounded_rectangle([0, 0, S - 1, S - 1], S * 0.24, fill=_state_fill(bg_pct) + (255,))

    # 數字往上讓一點給進度條
    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    f = _fit_text(md, text, S * (0.74 if len(text) >= 3 else 0.92), S * 0.80)
    _centered(md, (S / 2, S / 2 - S * 0.05), text, f, 255)
    img.paste((255, 255, 255, 255), (0, 0), mask)

    y0, y1 = int(S * 0.845), int(S * 0.895)
    x0, x1 = int(S * 0.14), int(S * 0.86)
    r = (y1 - y0) / 2
    d.rounded_rectangle([x0, y0, x1, y1], r, fill=_track(bg_pct))
    w = int((x1 - x0) * (pct or 0) / 100)
    if w:
        d.rounded_rectangle([x0, y0, x0 + max(w, y1 - y0), y1], r, fill=(255, 255, 255, 255))
    return img


def _track(pct) -> tuple:
    """狀態色底上的淡色軌道。

    要先算成實色再畫：ImageDraw 在 RGBA 上畫半透明色是「取代」不是「疊上去」，
    畫出來會變成半透明的洞，在工作列上透出底下的顏色。
    """
    c = _state_fill(pct)
    return tuple(round(v + (255 - v) * 0.4) for v in c) + (255,)


def _tile(pct, S, round_=True):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fill = _state_fill(pct) + (255,)
    if round_:
        d.rounded_rectangle([0, 0, S - 1, S - 1], S * 0.24, fill=fill)
    else:
        d.ellipse([0, 0, S - 1, S - 1], fill=fill)
    return img, d


def _tray_ring(pct, S, week=None):
    """狀態色圓底 + 外圈白色進度環 + 中間數字。"""
    img, d = _tile(pct, S, round_=False)
    w = round(S * 0.11)
    box = [w / 2 + S * 0.05, w / 2 + S * 0.05, S - w / 2 - S * 0.05, S - w / 2 - S * 0.05]
    d.arc(box, 0, 360, fill=_track(pct), width=w)
    if pct:
        d.arc(box, -90, -90 + 3.6 * pct, fill=(255, 255, 255, 255), width=w)
    text = "?" if pct is None else f"{pct:.0f}"
    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    _centered(md, (S / 2, S / 2), text, _fit_text(md, text, S * 0.52, S * 0.44), 255)
    img.paste((255, 255, 255, 255), (0, 0), mask)
    return img


def _tray_pie(pct, S, week=None):
    """狀態色圓角底 + 白色圓餅：一眼看比例，不讀數字。"""
    img, d = _tile(pct, S)
    m = S * 0.16
    box = [m, m, S - m, S - m]
    d.ellipse(box, fill=_track(pct))
    if pct:
        d.pieslice(box, -90, -90 + 3.6 * pct, fill=(255, 255, 255, 255))
    return img


def _tray_dual(pct, S, week=None):
    """兩條粗進度條：上面 5 小時、下面每週。唯一一個同時看得到兩個額度的樣式。"""
    img, d = _tile(pct, S)
    x0, x1 = S * 0.16, S * 0.84
    for y, h, value in ((S * 0.24, S * 0.22, pct), (S * 0.58, S * 0.18, week)):
        r = h / 2
        d.rounded_rectangle([x0, y, x1, y + h], r, fill=_track(pct))
        if value:
            w = max((x1 - x0) * value / 100, h)
            d.rounded_rectangle([x0, y, x0 + w, y + h], r, fill=(255, 255, 255, 255))
    return img


def _tray_left(pct, S, week=None):
    return _tray_number(pct, S, week, show_left=True)


_TRAY_FN = {
    "burst": _tray_burst, "number": _tray_number, "left": _tray_left, "ring": _tray_ring,
    "pie": _tray_pie, "dual": _tray_dual,
}


def make_tray_image(pct_used: float | None, style: str = "burst",
                    week_used: float | None = None) -> Image.Image:
    return _TRAY_FN.get(style, _tray_burst)(pct_used, TRAY_S, week_used)


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
        if i18n.lang() == "en":  # 英文介面改用 Segoe UI，比正黑體的英文字形好看
            kind = {"zh": "en", "zh_l": "en_l"}.get(kind, kind)
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

    W, H = 344, 224
    PAD = 16
    RADIUS = 8      # Windows 11 通知的圓角就是小小的
    FOOT_TOP = 194  # 頁尾淡底色帶從這裡開始（每週那列下面還有重置時間和進度兩行）

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
            self._text((self.PAD + 78, 18), t("card.stale"), self._f("zh_l", 11), WARN_RED)
        self._icon_button(self.W - 108, 24, "poke", ICON_BOLT, hot, size=11)
        self._icon_button(self.W - 80, 24, "refresh", ICON_REFRESH, hot)
        self._icon_button(self.W - 52, 24, "mini", ICON_MIN, hot, size=9)
        self._icon_button(self.W - 24, 24, "close", ICON_CLOSE, hot, size=9)

        foot = ""
        if q is None:
            self._text((self.W / 2, 78), t("card.no_data"), self._f("zh", 14), TXT, anchor="mm")
            self._text((self.W / 2, 100), t("card.no_data_hint"),
                       self._f("zh_l", 11), FAINT, anchor="mm")
            foot = t("card.no_data_foot")
        else:
            if q.resets_at:
                left = (q.resets_at - datetime.now(timezone.utc)).total_seconds() / 60
                # 進位到最近的分鐘：伺服器給的重置時刻可能差幾百毫秒才到整分
                # （例：08:29:59.9），直接截掉分秒會顯示成早一分鐘。
                hm = f"{(q.resets_at + timedelta(seconds=30)).astimezone():%H:%M}"
                sub1 = t("card.resets", left=human_left(left),
                         approx="" if q.resets_exact else t("card.approx"), time=hm)
            else:
                sub1 = t("card.not_started")
            self._row(52, t("card.five_hour"), q.percent_used,
                      grad_for(q.percent_used), accent_for(q.percent_used), sub1)
            if q.week_used is not None:
                sub2 = None
                if q.week_resets_at:
                    wleft = (q.week_resets_at - datetime.now(timezone.utc)).total_seconds() / 60
                    sub2 = t("card.week_resets", left=human_left_long(wleft), time=weekday_time(q.week_resets_at))
                self._row(118, t("card.weekly"), q.week_used, GRAD_WEEK, WEEK_ACCENT, sub2)
                pace = q.week_pace()
                if pace:
                    kind, finish = pace
                    text = t("pace.short", time=weekday_time(finish)) if finish else t(f"pace.{kind}")
                    self._text((self.PAD, 174), text, self._f("zh_l", 11),
                               WARN_RED if kind == "short" else FAINT)
            if q.live:
                # 更新節奏會自我收斂、也可能被限流，每次間隔都不同，
                # 所以直接把「上次更新多久了」秒數寫出來
                age = quota_source.seconds_since_fetch()
                foot = t("card.live")
                if age is not None:
                    foot += t("card.updated_ago", sec=f"{age:.0f}")
            else:
                age = t("card.desktop_now") if q.age_min < 1.5 else t("card.desktop_ago", min=f"{q.age_min:.0f}")
                foot = t("card.desktop_foot", age=age, status=quota_source.api_status())

        # 頁尾：平常講資料狀態，滑到按鈕上或剛做完動作時換成提示
        hint = note or (t(f"hint.{hot}") if hot in ("close", "mini", "refresh", "poke") else None)
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
            self._text((32, cy), t("card.no_data"), self._f("zh_l", 11), FAINT, anchor="lm")
            return self._finish()

        self._slot(31, "5h", q.percent_used,
                   grad_for(q.percent_used), accent_for(q.percent_used))
        if q.week_used is not None:
            sep = 31 + self.SLOT - 4
            self.d.line([sep * self.k, (cy - 7) * self.k, sep * self.k, (cy + 7) * self.k],
                        fill=HAIR, width=max(1, int(self.k)))
            self._slot(31 + self.SLOT + 8, t("mini.week"), q.week_used, GRAD_WEEK, WEEK_ACCENT)

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
    SHOWS_AGE = False  # 畫面上有沒有「N 秒前更新」這種每秒會變的東西

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

        self._hwnd: int | None = None
        self._suspended = False  # 被全螢幕程式擠掉，不是使用者的意思
        make_tool_window(self.hwnd)

    # ── 待得住 ──
    @property
    def hwnd(self) -> int | None:
        if self._hwnd is None:
            self._hwnd = window_handle(self.win)
        return self._hwnd

    def _raise(self, hwnd: int):
        _u32().SetWindowPos(hwnd, ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def lift_now(self):
        """事件回呼專用：只動 z 序。

        這是在 Windows 的訊息回呼裡跑的，碰 Tk 會有重入風險，所以只讀
        已經算好的欄位、只叫 Win32。該不該讓開（全螢幕）交給看門狗判斷。
        """
        if self.visible and not self._suspended and self._hwnd:
            self._raise(self._hwnd)

    def suspend(self):
        """暫時讓開（全螢幕程式當前）。不動 visible——那記的是使用者的意思。"""
        if self.visible and not self._suspended:
            self._suspended = True
            self.win.withdraw()

    def resume(self):
        if self._suspended:
            self._suspended = False
            if self.visible:
                self.win.deiconify()
                if self.hwnd:  # 重新顯示的視窗不保證回到最上面
                    self._raise(self.hwnd)

    def keep_alive(self, desk_changed: bool = False, busy_mon: int | None = None):
        """看門狗巡一趟：該讓的讓開，其餘一律救回來。

        只管「應該看得見」的視窗——使用者自己按 ✕ 收起來的不會被硬拉回來。
        """
        hwnd = self.hwnd
        if not hwnd:
            return
        u = _u32()
        # 同一個螢幕上有全螢幕程式就收起來；等它結束（或切到別的視窗）再自己回來
        if busy_mon and u.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) == busy_mon:
            self.suspend()
            return
        self.resume()
        if not self.visible:
            return

        if u.IsIconic(hwnd) or not u.IsWindowVisible(hwnd):
            u.ShowWindow(hwnd, SW_SHOWNOACTIVATE)  # 真的被收掉了（少見，但便宜）
        elif desk_changed:
            # 剛切過虛擬桌面。視窗是在 show 的當下被指派到某個桌面的，
            # 所以關掉再開一次就會跟著過來——沒有 API 能直接搬 tool window。
            u.ShowWindow(hwnd, SW_HIDE)
            u.ShowWindow(hwnd, SW_SHOWNOACTIVATE)

        # 每趟都重新宣告置頂。這是 Win+D 之後救得回來的唯一手段：視窗沒有被隱藏，
        # 只是沉到同樣是 topmost 的工作列後面。已經在最上面時這個呼叫等於沒做事。
        self._raise(hwnd)

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
        self._suspended = False  # 使用者的指令蓋過看門狗的判斷
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

    @staticmethod
    def _pace_sig(q):
        """每週進度提示換算到分鐘：預計用完的時刻只顯示到分。"""
        pace = q.week_pace()
        if pace is None or pace[1] is None:
            return pace
        return pace[0], weekday_time(pace[1])

    def _signature(self):
        """畫面上真正看得到的東西。沒變就不用重畫（重畫要 ~34ms）。"""
        q = self._last
        left = wleft = None
        if q and q.resets_at:
            left = int((q.resets_at - datetime.now(timezone.utc)).total_seconds() // 60)
        if q and q.week_resets_at:
            wleft = int((q.week_resets_at - datetime.now(timezone.utc)).total_seconds() // 60)
        # 「N 秒前更新」每秒都會變，但只有卡片會顯示它，橫條不需要跟著重畫
        age = None
        if q is not None and q.live and self.SHOWS_AGE:
            secs = quota_source.seconds_since_fetch()
            age = None if secs is None else int(secs)
        # 語言也要算進去：橫條沒有每秒變的東西，少了它切換語言後會一直停在舊語言
        return (
            i18n.lang(), self._hot, self._note, age,
            None if q is None else (q.percent_used, q.week_used, q.resets_exact,
                                    q.stale, q.live, left, wleft, self._pace_sig(q)),
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

    STATE = ""       # 沿用既有的 x / y 鍵，舊設定不會失效
    SHOWS_AGE = True  # 頁尾有「N 秒前更新」，要每秒重畫

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

    位置**跟卡片分開記**（STATE 前綴 mini_）：橫條適合停在工作列旁邊常駐，
    卡片則適合放在桌面上看，兩者各有各的好位置。按縮小就直接回到橫條自己的位置。
    """

    STATE = "mini_"

    def __init__(self, root, dpi, on_click):
        self.on_click = on_click
        super().__init__(root, dpi, MiniPainter(dpi), taskbar_slot)

    def _click(self, e):
        self._start_drag(e)

    def _end_drag(self, e):
        moved = self._moved
        super()._end_drag(e)
        if not moved:  # 沒拖動就是單純點一下
            self.on_click()


# ──────────────────────────────── 桌面寵物 ────────────────────────────────

CLAUDE_SETTINGS = os.path.expanduser("~/.claude/settings.json")
PET_POLL_MS = 2000      # 多久看一次 Claude Code 的狀態
PET_BUBBLE_MS = 7000
PET_NEWS_MS = 10000     # 「做完了」「出錯了」自己冒出來時停多久
PET_WAITING_MS = 14000  # 「在等你允許」比較要緊，停久一點
PET_AWAKE_SEC = 90      # 叫醒之後醒著多久才又睡回去
PET_DONE_MIN_SEC = 30   # 做了至少這麼久才報「做完了」
PET_SIZES = (1.0, 1.5, 2.0, 3.0)
WATCH_SEC = 2 * 3600    # 打開短影片後多久內，Claude 需要你時改發 Windows 通知
VIDEO_SITES = (  # (名稱, 網址)：名稱是品牌名，不翻譯
    ("YouTube Shorts", "https://www.youtube.com/shorts"),
    ("Instagram Reels", "https://www.instagram.com/reels/"),
    ("TikTok", "https://www.tiktok.com/"),
    ("YouTube", "https://www.youtube.com/"),
    ("Netflix", "https://www.netflix.com/"),
    ("Disney+", "https://www.disneyplus.com/"),
    ("Twitch", "https://www.twitch.tv/"),
    ("Bilibili", "https://www.bilibili.com/"),
    ("Facebook Reels", "https://www.facebook.com/reel/"),
)
THEMES_FILE = os.path.join(DATA_DIR, "pets", "themes.json")  # 自訂主題（跟自訂角色放一起，不進版本控制）
TYPE_MS = 28            # 對話框打字機效果，每格出幾個字的間隔
CHROMA_RGB = tuple(int(CHROMA[i:i + 2], 16) for i in (1, 3, 5))


class _PetPainter:
    """寵物的畫在 PetWindow 裡逐格處理；這裡只提供 FloatWindow 要的尺寸與按鈕。"""

    def __init__(self, dpi: float):
        self.pw = self.ph = round(pet.S * dpi)
        self.buttons = []


class Bubble:
    """遊戲風對話框：打字機效果一個字一個字出來，點一下就關掉。"""

    def __init__(self, root: tk.Tk, dpi: float):
        self.root, self.dpi = root, dpi
        self.win: tk.Toplevel | None = None
        self.text = self.name = ""
        self._shown = 0
        self._tail: int | None = None
        self._jobs: list[str] = []

    @property
    def open(self) -> bool:
        return self.win is not None

    def show(self, text: str, name: str, ms: int, anchor, links: list | None = None):
        """anchor() 回傳寵物視窗的範圍，對話框放在它上面，尾巴指向它。

        links 跟 text 的每一段（換行分開）對應：那一段講的對話在桌面版裡的 id，點那一段就跳過去；
        沒有的放空字串。只有一段時，點整個對話框都算。
        """
        self.close()
        self.text, self.name, self._shown = text, name, 0
        self.links = links or []
        self._anchor = anchor
        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        try:
            w.attributes("-transparentcolor", CHROMA)
        except tk.TclError:
            pass
        self.label = tk.Label(w, bd=0, bg=CHROMA, cursor="hand2")
        self.label.pack()
        self.label.bind("<Button-1>", self._click)
        self.win = w
        make_tool_window(window_handle(w))
        self.place()
        self._type()
        self._jobs.append(self.root.after(ms, self.close))

    def place(self):
        if self.win is None:
            return
        _lines, bw, bh = pet.bubble_layout(self.text, self.dpi)
        x0, y0, x1, y1 = self._anchor()          # 寵物視窗的範圍：對話框不能蓋到它
        cx = (x0 + x1) // 2
        sw = self.root.winfo_screenwidth()
        x = max(4, min(cx - bw // 2, sw - bw - 4))
        y = y0 - bh - round(2 * self.dpi)
        tail = cx - x
        if y < 4:  # 上面放不下（寵物靠螢幕上緣）：改放旁邊，尾巴就不指了
            x, y = fancymenu.clear_of(self.root, (x0, y0, x1, y1), x, max(4, y), bw, bh, round(6 * self.dpi))
            tail = -9999
        if tail != self._tail:
            self._tail = tail
            self._paint()
        self.win.geometry(f"{bw}x{bh}+{x}+{y}")

    def _link_at(self, y: int) -> str:
        """游標在第幾段：照 pet.bubble_image 的排版算（名牌 12、第一行從 28 開始、每行 20，都乘上縮放）。"""
        if not any(self.links):
            return ""
        paras = self.text.split("\n")
        if len(paras) == 1:
            return self.links[0] if self.links else ""
        font = pet._font(round(13 * self.dpi))
        line = int((y / self.dpi - 28) // 20)
        for i, para in enumerate(paras):
            n = len(pet._wrap(para, font, pet.BUBBLE_W * self.dpi))
            if line < n:
                return self.links[i] if i < len(self.links) else ""
            line -= n
        return ""

    def _click(self, e):
        """點到某個對話的那一行：叫桌面版打開它。點其他地方：關掉對話框。"""
        link = self._link_at(e.y)
        self.close()
        if link:
            claude_state.open_session(link)

    def _paint(self):
        done = self._shown >= len(self.text)
        img = pet.bubble_image(self.text, self.name, self._shown, done, self.dpi, CHROMA_RGB, self._tail,
                               fancymenu.theme())
        self._photo = ImageTk.PhotoImage(img)
        self.label.configure(image=self._photo)

    def _type(self):
        if self.win is None:
            return
        if self._shown < len(self.text):
            self._shown = min(len(self.text), self._shown + 2)
            self._paint()
            self._jobs.append(self.root.after(TYPE_MS, self._type))

    def close(self):
        for job in self._jobs:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self._jobs.clear()
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win = None
        self._tail = None


class PetWindow(FloatWindow):
    """跟著 Claude Code 狀態變表情的寵物。

    三種位置：坐在迷你橫條上（橫條收起時改坐在工作列右邊）、在工作列上散步、
    自由擺放。哪種模式都能拖：拖到哪就待在哪（坐在橫條上的會改成自由擺放、
    散步的會從放下的地方繼續走）。點一下會冒出對話框；睡著時點一下會叫醒它。
    """

    STATE = "pet_"

    def __init__(self, root, dpi, mini: FloatWindow):
        self.mini = mini
        st = load_state()
        self.character = st.get("pet_char", "slime")
        if not isinstance(self.character, str):
            self.character = "slime"
        colors = st.get("pet_colors")
        self._colors = colors if isinstance(colors, dict) else {}
        self.placement = st.get("pet_place", "mini") if st.get("pet_place") in pet.PLACEMENTS else "mini"
        size = st.get("pet_size", 1.0)
        self.size = size if size in PET_SIZES else 1.0  # 建立視窗時算預設位置就會用到
        super().__init__(root, dpi, _PetPainter(dpi), self._free_default)
        self.state = claude_state.State("idle")
        self._f = 0
        self._dir = 1
        self._walk_x: float | None = None
        self._frames: dict = {}
        self._anim: str | None = None
        self._poll: str | None = None
        self._hook_file: str | None = None
        self._hook_checked = 0.0
        self._awake_until = 0.0
        self._held = False
        self._since = time.time()      # 目前顯示的狀態從什麼時候開始（自訂角色照它算第幾格）
        self._shown_kind = ""
        self._pil: dict = {}           # 背景準備好的自訂角色圖
        self._preparing: set = set()
        self.music_on = bool(st.get("pet_music", True))
        self._music_for = None
        self.on_alert = None           # Claude 需要你時通知外面（例如發 Windows 通知）
        self._work_start: dict = {}    # 每個對話這次從什麼時候開始工作
        self._told: set = set()        # 已經講過的事件，不重講
        self.last_links: list = []     # describe() 每一行對應的對話（點了可以跳過去）
        self.auto_jump = bool(st.get("auto_jump", False))  # 做完或需要回覆時自動切回那個對話
        chat_state.enabled = bool(st.get("chat_detect", True))  # 也看桌面版的聊天模式
        self.bubble = Bubble(root, dpi)
        self.forced: str | None = None  # 右鍵選單手動指定的狀態
        self.label.bind("<Button-3>", self._context)
        if not st.get("pet_on", True):
            self.hide()

    # ── 角色與外觀 ──
    @property
    def custom(self) -> str | None:
        """自訂角色的資料夾；內建角色回 None。"""
        if self.character.startswith("custom:"):
            folder = os.path.join(pet.custom_root(DATA_DIR), self.character[7:])
            return folder if os.path.isdir(folder) else None
        return None

    def display_name(self) -> str:
        if self.character.startswith("custom:"):
            return self.character[7:]
        return t(f"pet.character.{self.character}")

    @property
    def color(self) -> str:
        """每個角色各記各的顏色：把史萊姆換成藍色，換成小雞時還是黃色的小雞。"""
        c = self._colors.get(self.character, "default")
        return c if c in pet.COLORS else "default"

    def set_character(self, character: str):
        self.character = character
        self._music_for = None
        folder = self.custom
        if folder:
            self._prepare(folder, self.scale, False, first=self.shown_state())
        save_state(pet_char=character)
        self.say(t("pet.say.hello", name=self.display_name()), 4000)

    def set_color(self, color: str):
        self._colors[self.character] = color
        save_state(pet_colors=self._colors)

    # ── 大小與位置 ──
    @property
    def scale(self) -> float:
        """平常照螢幕縮放；在工作列裡散步時縮到剛好塞進工作列的高度。"""
        if self.placement == "inbar":
            bar = taskbar_height(self.root.winfo_screenheight())
            if bar:
                return bar / pet.S
        return self.dpi * self.size

    def set_size(self, size: float):
        self.size = size
        save_state(pet_size=size)
        self._resize()

    def _resize(self):
        size = round(pet.S * self.scale)
        if size != self.W:
            self.W = self.H = size
            self.win.geometry(f"{size}x{size}")

    def _free_default(self, sw, sh, w, h):
        return sw - w - round(240 * self.dpi), taskbar_top(sh) - round(pet.FEET * self.scale)

    def _anchor(self) -> tuple[int, int] | None:
        """mini / walk 模式該站的位置；free 模式回 None（由使用者拖）。"""
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        feet = round(pet.FEET * self.scale)
        if self.placement == "mini":
            m = self.mini
            if m.visible and not m._suspended:
                x = m.win.winfo_x() + m.W - self.W - round(4 * self.dpi)
                return x, m.win.winfo_y() - feet + round(3 * self.dpi)
            return self._free_default(sw, sh, self.W, self.H)
        if self.placement in ("walk", "inbar"):
            if self._walk_x is None:
                self._walk_x = float(self._free_default(sw, sh, self.W, self.H)[0])
            speed = {"working": 1.4, "awake": 0.8}.get(self.shown_state(), 0) * self.dpi
            self._walk_x += speed * self._dir
            if self._walk_x <= 0 or self._walk_x >= sw - self.W:
                self._dir = -self._dir
                self._walk_x = max(0.0, min(self._walk_x, sw - self.W))
            if self.placement == "inbar":
                return round(self._walk_x), taskbar_top(sh)
            return round(self._walk_x), taskbar_top(sh) - feet
        return None

    def set_placement(self, placement: str):
        self.placement = placement
        self._walk_x = None
        save_state(pet_place=placement)
        self._resize()
        if placement == "free":
            x, y = self._clamp(*self._default())
            self.win.geometry(f"+{x}+{y}")

    def reset_position(self):
        self._walk_x = None
        super().reset_position()

    def _head(self) -> tuple[int, int, int, int]:
        """寵物視窗在螢幕上的範圍。對話框和右鍵選單都要避開它，不要蓋到角色。"""
        x, y = self.win.winfo_x(), self.win.winfo_y()
        return x, y, x + self.W, y + self.H

    # ── 滑鼠 ──
    def _click(self, e):
        self._start_drag(e)

    def _on_drag(self, e):
        if not self._drag:
            return
        x, y = self._clamp(e.x_root - self._drag[0], e.y_root - self._drag[1])
        if not self._moved and abs(x - self.win.winfo_x()) + abs(y - self.win.winfo_y()) < 4:
            return  # 手抖不算拖
        self._moved = True
        self._held = True
        self.win.geometry(f"+{x}+{y}")
        self.bubble.place()

    def _end_drag(self, e):
        if not self._drag:
            return
        moved, self._drag, self._held = self._moved, None, False
        if not moved:
            self._tap()
            return
        x, y = self._clamp(self.win.winfo_x(), self.win.winfo_y())
        if self.placement in ("walk", "inbar"):
            self._walk_x = float(x)  # 從放下的地方繼續走（會落回工作列）
        else:
            if self.placement == "mini":
                self.placement = "free"
                save_state(pet_place="free")
            self._save_pos(x, y)

    def _tap(self):
        """點一下：開著對話框就關掉；睡著就叫醒；不然說明現在的狀態。"""
        if self.bubble.open:
            self.bubble.close()
        elif self.shown_state() == "idle":
            self.wake()
        else:
            text = self.describe(full=True)
            self.say(text, PET_BUBBLE_MS + 1500 * text.count("\n"), self.last_links)  # 列得越多，留越久

    def _hover(self, e):
        pass

    # ── 狀態 ──
    def wake(self):
        """叫醒：不管 Claude 在做什麼，都醒來陪你一陣子（自訂角色會跳舞、放歌）。"""
        self._awake_until = time.time() + PET_AWAKE_SEC
        self._f = 0
        self.say(t("pet.say.wake"), PET_BUBBLE_MS)

    def set_forced(self, kind: str | None):
        """手動固定成某個狀態；None 回到自動跟著 Claude。"""
        self.forced = kind
        self._awake_until = 0.0
        self._f = 0

    def shown_state(self) -> str:
        """畫面上要演哪個狀態。順序：被拎著 > Claude 在等你 > 手動指定 > 被叫醒 > 跟著 Claude。"""
        if self._held:
            return "held"
        if self.state.kind == "waiting":  # 需要你的時候一定要讓你看到
            return "waiting"
        if self.forced:
            return self.forced
        if time.time() < self._awake_until:
            return "awake"
        return self.state.kind

    # ── 右鍵選單（寵物身上，系統列的「桌面寵物」也用同一份） ──
    def _context(self, e):
        x0, y0, x1, y1 = self._head()
        fancymenu.popup(self.root, self.dpi, self.menu_items(), x1 + round(6 * self.dpi), y0,
                        title=self.display_name(), avoid=(x0, y0, x1, y1))

    def menu_items(self, tray: bool = False) -> list:
        def radio(label, current, command):
            return {"label": label, "checked": current, "radio": True, "command": command}

        def characters():
            items = [radio(t(f"pet.character.{k}"), self.character == k, lambda k=k: self.set_character(k))
                     for k in pet.CHARACTERS]
            custom = pet.custom_characters(DATA_DIR)  # 每次打開都重新掃，放了新圖不用重開程式
            if custom:
                items.append("-")
                items += [radio(n, self.character == f"custom:{n}", lambda n=n: self.set_character(f"custom:{n}"))
                          for n in custom]
            items += ["-", {"label": t("pet.custom_open"), "command": self.open_folder}]
            return items

        items = []
        if tray:
            items += [{"label": t("pet.show"), "checked": self.visible, "command": self.toggle}, "-"]
        items += [
            {"label": t("pet.wake"), "command": self.wake, "enabled": self.visible},
            {"label": t("pet.menu.state"), "submenu": [
                radio(t("pet.state.auto"), self.forced is None, lambda: self.set_forced(None)), "-",
                *[radio(t(f"pet.state.{k}"), self.forced == k, lambda k=k: self.set_forced(k))
                  for k in ("working", "waiting", "done", "error", "idle", "awake")]]},
        ]
        if self.has_music():
            items.append({"label": t("pet.music"), "checked": self.music_on,
                          "command": lambda: self.set_music(not self.music_on)})
        items += [
            "-",
            {"label": t("pet.menu.character"), "submenu": characters},
            {"label": t("pet.menu.color"), "enabled": self.custom is None, "submenu": [
                radio(t(f"pet.color.{k}"), self.color == k, lambda k=k: self.set_color(k)) for k in pet.COLORS]},
            {"label": t("pet.menu.size"), "enabled": self.placement != "inbar", "submenu": [
                radio(t(f"pet.size.{k}"), self.size == k, lambda k=k: self.set_size(k)) for k in PET_SIZES]},
            {"label": t("pet.menu.placement"), "submenu": [
                radio(t(f"pet.placement.{k}"), self.placement == k, lambda k=k: self.set_placement(k))
                for k in pet.PLACEMENTS]},
        ]
        if not tray:
            items += ["-", {"label": t("pet.hide"), "command": self.hide}]
        return items

    def describe(self, full: bool = False) -> str:
        """現在的狀態說成一句話。full（點寵物時）且同時有好幾個對話在動：每個都列一行。"""
        s = self.state
        sessions = claude_state.last_sessions
        if full and len(sessions) >= 2:
            lines = [t("pet.say.sessions", n=len(sessions))]
            for x in sessions[:6]:
                label = t("pet.say.maybe_waiting") if x.kind == "waiting" and x.guessed else t(f"pet.state.{x.kind}")
                mins = int((time.time() - x.since) // 60) if x.kind == "working" else 0
                sep = "：" if i18n.lang() == "zh" else ": "
                lines.append(f"• {x.project or 'Claude'}{sep}{label}" + (t("pet.say.mins", min=mins) if mins else ""))
            if len(sessions) > 6:
                lines.append(t("pet.say.more", n=len(sessions) - 6))
            self.last_links = [""] + [x.link for x in sessions[:6]] + [""]  # 跟每一行對應，點了可以跳過去
            return "\n".join(lines)
        self.last_links = [s.link]
        who = s.project or "Claude"
        kind = s.kind
        if kind == "idle" and self.shown_state() in ("awake", "held"):
            return t("pet.say.awake")
        if kind == "working":
            mins = max(0, int((time.time() - s.since) // 60))
            text = t("pet.say.working", who=who) + (t("pet.say.for", min=mins) if mins else "")
        elif kind == "waiting":
            text = t("pet.say.waiting_guess" if s.guessed else "pet.say.waiting", who=who)
        else:
            text = t(f"pet.say.{kind}", who=who)
        if s.others:
            text += t("pet.say.others", n=s.others)
        return text

    def _check_hook(self):
        now = time.time()
        if now - self._hook_checked > 10:
            self._hook_checked = now
            installed = pet.hook_installed(CLAUDE_SETTINGS)
            self._hook_file = pet.hook_paths(DATA_DIR)[1] if installed else None

    def poll(self):
        """定期看一次 Claude Code 在做什麼；狀態變了就換動畫，重要的變化冒對話框。"""
        self._poll = None
        if not self.visible:
            return
        self._check_hook()
        try:
            new = claude_state.current(self._hook_file)
        except Exception as e:
            if sys.stderr:
                print("claude_state failed:", e, file=sys.stderr)
            new = claude_state.State("idle")
        old = self.state
        self.state = new
        if new.kind != old.kind:
            self._f = 0
        news = self._news(claude_state.last_sessions)
        if news:
            text = self._say_one(news)
            self.say(text, PET_WAITING_MS if news.kind == "waiting" else PET_NEWS_MS, [news.link])
            if self.on_alert:
                self.on_alert(text)
            if self.auto_jump and news.kind in ("waiting", "done") and news.link:
                # 做完或需要回覆：直接切回那個對話。沒人點，Windows 不讓對方自己跳到前面，所以另外拉一次
                if claude_state.open_session(news.link):
                    vscode = news.link.startswith("vscode:")
                    claude_state.bring_forward("vscode" if vscode else "desktop", news.cwd,
                                               delay=claude_state.VSCODE_OPEN_DELAY + 1.5 if vscode else 1.5)
        self._poll = self.root.after(PET_POLL_MS, self.poll)

    def _news(self, sessions):
        """這一輪有沒有值得冒出來講的事；一件事只講一次。

        同時開好幾個對話時，以前是「最重要的狀態一變就講」，於是每個對話每結束一輪都會跳一次
        「做完了」。現在每個對話分開記：
          - 在等你、出錯了：每一次都講（同一次不重講）
          - 做完了：只講這次至少做了 PET_DONE_MIN_SEC 的；一下子就結束的小事不吵你。
            聊天模式另外：回完時你不在那個聊天（還沒讀），再短也講
        程式剛開時已經是「做完了」的不算（沒看到它在做）。
        """
        now = time.time()
        seen = set()
        best = None
        for s in sessions:
            seen.add(s.key)
            if s.kind == "working":
                self._work_start.setdefault(s.key, s.since)
                continue
            event = (s.key, s.kind, round(s.since))
            if event in self._told:
                continue
            if s.kind == "done":
                started = self._work_start.pop(s.key, None)
                unread_chat = s.entry == "claude-chat" and s.unread
                if not unread_chat and (started is None or s.since - started < PET_DONE_MIN_SEC):
                    self._told.add(event)
                    continue
            elif s.kind not in ("waiting", "error"):
                continue
            self._told.add(event)
            if best is None or claude_state.PRIORITY[s.kind] > claude_state.PRIORITY[best.kind]:
                best = s
        for key in [k for k in self._work_start if k not in seen]:  # 對話不見了（關掉、太久沒動）
            del self._work_start[key]
        if len(self._told) > 500:
            self._told.clear()
        return best

    def _say_one(self, s) -> str:
        who = s.project or "Claude"
        if s.kind == "waiting":
            return t("pet.say.waiting_guess" if s.guessed else "pet.say.waiting", who=who)
        return t(f"pet.say.{s.kind}", who=who)

    def start(self):
        folder = self.custom
        if folder:  # 一開始就在背景把圖準備好，換狀態時不用等
            self._prepare(folder, self.scale, False, first=self.shown_state())
        if self._poll is None:
            self.poll()
        if self._anim is None:
            self._step()

    def _frame(self, kind: str, flip: bool):
        """這一刻該顯示的圖；自訂角色還沒準備好就回 None（畫面先停在上一格）。"""
        scale = self.scale
        folder = self.custom
        if folder:
            return self._custom_photo(folder, kind, scale, flip)
        key = (self.character, self.color, scale, kind, self._f, flip)
        photo = self._frames.get(key)
        if photo is None:
            name = self.character if self.character in pet.CHARACTERS else "slime"
            img = pet.frame(name, kind, self._f, scale, CHROMA_RGB, flip, self.color)
            if len(self._frames) > 1500:
                self._frames.clear()
            photo = self._frames[key] = ImageTk.PhotoImage(img)
        return photo

    # ── 自訂角色：照時間決定播哪一格，圖在背景先準備好 ──
    #
    # 真人影片做的角色一個狀態就有幾百張圖。以前是播到哪張才讀檔、縮放，一張要幾十毫秒，
    # 於是每秒 24 格實際只播得出十幾格，看起來像慢動作。現在：
    #   1. 播第幾格由「這個狀態開始到現在幾秒」乘上 fps 決定，慢了就跳格，速度永遠正確
    #   2. 讀檔和縮放都在背景執行緒做完，畫面執行緒只把現成的圖轉成 PhotoImage
    def _custom_photo(self, folder: str, kind: str, scale: float, flip: bool):
        n = pet.custom_count(folder, kind)
        idx = int((time.time() - self._since) * pet.custom_fps(folder)) % n
        base = (self.character, scale, kind, flip)
        imgs = self._pil.get(base)
        if not imgs:
            self._prepare(folder, scale, flip, first=kind)
            return None
        if idx >= len(imgs):  # 還在準備中：先在已經做好的幾格裡循環
            idx %= len(imgs)
        key = base + (idx,)
        photo = self._frames.get(key)
        if photo is None:
            if len(self._frames) > 1500:
                self._frames.clear()
            photo = self._frames[key] = ImageTk.PhotoImage(imgs[idx])
        return photo

    def _prepare(self, folder: str, scale: float, flip: bool, first: str):
        """在背景把這個角色每個狀態的每一格都縮好。正在做就不重複開。"""
        job = (self.character, scale, flip)
        if job in self._preparing:
            return
        self._preparing.add(job)
        order = [first] + [s for s in pet.STATES if s != first]

        def work():
            try:
                for kind in order:
                    base = (job[0], scale, kind, flip)
                    if base in self._pil:
                        continue
                    n = pet.custom_count(folder, kind)
                    frames = self._pil[base] = []  # 一格一格加進去，畫面可以邊做邊播
                    for i in range(n):
                        frames.append(pet.custom_frame(folder, kind, i, scale, CHROMA_RGB, flip))
                    pet.forget_sources(folder)
            finally:
                self._preparing.discard(job)

        # 換了角色或大小，舊的圖就用不到了
        for k in [k for k in self._pil if k[0] != job[0] or k[1] != scale]:
            del self._pil[k]
        threading.Thread(target=work, daemon=True).start()

    # ── 音樂（自訂角色在 pet.json 設定，例如跳舞時播歌） ──
    def set_chat_detect(self, on: bool):
        chat_state.enabled = on
        save_state(chat_detect=on)
        self.say(t("pet.chat_on") if on else t("pet.chat_off"), PET_BUBBLE_MS)

    def set_auto_jump(self, on: bool):
        self.auto_jump = on
        save_state(auto_jump=on)
        self.say(t("pet.auto_jump_on") if on else t("pet.auto_jump_off"), PET_BUBBLE_MS)

    def set_music(self, on: bool):
        self.music_on = on
        save_state(pet_music=on)
        self._music_for = None  # 下一格重新判斷要不要播
        if not on:
            self._stop_music()

    def has_music(self) -> bool:
        folder = self.custom
        return bool(folder and pet.custom_music(folder))

    def _update_music(self, kind: str):
        want = None
        folder = self.custom
        if folder and self.music_on and self.visible and not self._suspended:
            path = pet.custom_music(folder).get(kind)
            if path:
                want = (path, kind)
        if want == self._music_for:
            return
        self._music_for = want
        if want is None:
            self._stop_music()
            return
        try:
            import winsound
            # 被叫醒時一直跳一直播；做完了就慶祝一輪，不要整整三分鐘都在放歌
            flags = winsound.SND_FILENAME | winsound.SND_ASYNC | (winsound.SND_LOOP if kind == "awake" else 0)
            winsound.PlaySound(want[0], flags)
        except (ImportError, RuntimeError):
            self._music_for = None

    def _stop_music(self):
        try:
            import winsound
            winsound.PlaySound(None, 0)
        except (ImportError, RuntimeError):
            pass

    def _step(self):
        """播下一格動畫，並讓 mini / walk 模式跟著目標位置走。"""
        self._anim = None
        if not self.visible:
            return
        kind = self.shown_state()
        if kind != self._shown_kind:
            # 換狀態就從第一格開始，音樂也從頭播，畫面和聲音才對得上
            self._shown_kind = kind
            self._since = time.time()
        self._update_music(kind)
        if not self._suspended:
            self._resize()
            pos = None if self._held else self._anchor()
            if pos is not None:
                x, y = self._clamp(*pos)
                if (x, y) != (self.win.winfo_x(), self.win.winfo_y()):
                    self.win.geometry(f"+{x}+{y}")
                    self.bubble.place()
            flip = self.placement in ("walk", "inbar") and self._dir < 0 and not self._held
            photo = self._frame(kind, flip)
            if photo is not None:
                self.label.configure(image=photo)
            self._f = (self._f + 1) % pet.FRAMES
        # 睡覺時慢慢動就好，省 CPU
        # 自訂角色照它自己設定的播放速度（例如真人影片是每秒 24 格）
        folder = self.custom
        if folder:
            delay = max(15, round(1000 / pet.custom_fps(folder)))
        else:
            delay = pet.FRAME_MS * (3 if kind == "idle" else 1)
        self._anim = self.root.after(delay, self._step)

    def toggle(self):
        super().toggle()
        save_state(pet_on=self.visible)
        if self.visible:
            self.start()
        else:
            self.bubble.close()
            self._music_for = None
            self._stop_music()

    def suspend(self):
        super().suspend()
        self.bubble.close()
        self._music_for = None
        self._stop_music()

    def say(self, text: str, ms: int, links: list | None = None):
        if self.visible and not self._suspended:
            self.bubble.show(text, self.display_name(), ms, self._head, links)

    def render(self, q):
        pass  # 寵物不看額度


# ──────────────────────────────── 主程式 ────────────────────────────────

def _tray_icon_class(pystray):
    """pystray 的系統列圖示，但右鍵改開我們自己畫的選單。

    pystray 在 Windows 上右鍵會用 TrackPopupMenuEx 顯示原生選單，沒辦法換外觀。
    這裡攔下右鍵放開（WM_RBUTTONUP）的通知，改叫 on_right_click；其他照舊（左鍵、通知）。
    """
    WM_RBUTTONUP = 0x0205

    class TrayIcon(pystray.Icon):
        on_right_click = None

        def _on_notify(self, wparam, lparam):
            if lparam == WM_RBUTTONUP and self.on_right_click:
                self.on_right_click()
                return
            super()._on_notify(wparam, lparam)

    return TrayIcon


class App:
    def __init__(self):
        self.dpi = enable_dpi_awareness()
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = QuotaWindow(self.root, self.dpi, self.request_refresh,
                                  self.request_poke, self.minimize)
        self.mini = MiniWindow(self.root, self.dpi, self.expand)
        self.pet = PetWindow(self.root, self.dpi, self.mini)
        self.pet.on_alert = self._alert
        self.pet.open_folder = self.open_pet_folder
        self._watch_until = 0.0
        self.icon = None
        self._poking = False
        self._last_poke: datetime | None = None
        self._auto_tries = 0          # 這次沒有視窗以來，自動起算試了幾次
        self._auto_gave_up = False
        self._tray_pct = object()  # 讓第一次一定會設圖示
        self._tick: str | None = None    # 目前排定的更新排程
        self._ticker: str | None = None  # 每秒跳動的「N 秒前」計時器
        self._api_busy = False
        self._api_next = 0.0  # 下次可以打 API 的時間
        self._desk: bytes | None = None  # 上一趟看到的虛擬桌面 GUID
        self._hook = None  # 殼層事件 hook，要留著參照不然會被回收

        st = load_state()
        # 自動起算會花額度，預設關閉；換發 token 不開的話一定會過期，預設開啟
        self.auto_poke = bool(st.get("auto_poke", False))
        fancymenu.load_custom_themes(THEMES_FILE)
        fancymenu.set_theme(st.get("theme", "paper"))
        quota_source.ALLOW_REFRESH = bool(st.get("token_refresh", True))
        i18n.set_lang(st.get("lang", "auto"))
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
    # 兩者各記各的位置：橫條停在工作列旁邊常駐，卡片放在桌面上看。
    # 切換時都回到自己的位置，不會互相把對方拖走。
    def minimize(self):
        self.window.hide()
        self.mini.show()
        self.minimized = True
        save_state(minimized=True, hidden=False)

    def expand(self):
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
            self.window.note(t("note.rate_limited", sec=f"{wait:.0f}"), clear_after=4000)
        else:
            self._pull_api(force=True)  # 下次什麼時候再問由 _after_pull 決定
        self.root.after(0, lambda: self.refresh(force=True))

    # ── 即時用量 API ──
    def _pull_api(self, force: bool = False):
        """在背景執行緒更新 API 快取。網路呼叫絕不能跑在 Tk 主執行緒上，否則畫面會卡住。"""
        if self._api_busy:
            return
        self._api_busy = True

        # 先把下一次排遠一點：萬一這條執行緒卡在逾時上，也不會每 10 秒又疊一條
        self._api_next = time.time() + 60

        def work():
            try:
                quota_source.refresh_api(force=force)
            finally:
                self._api_busy = False
                self.root.after(0, self._after_pull)

        threading.Thread(target=work, daemon=True).start()

    def _after_pull(self):
        """一次 API 嘗試結束後，重新算下次該問的時間。

        時間點交給 quota_source.seconds_until_allowed()——限流冷卻與自我收斂出來的
        間隔都在它手上。以前這裡是照固定節奏排的，於是被靜音之後（開機時伺服器回
        5xx 就會靜音 5 分鐘）還要再等一整個間隔才重試，開機後得等上好幾分鐘才看得到
        正確數字。現在靜音一結束就立刻再試。

        失敗但沒被靜音時（網路瞬斷、開機時網路還沒起來）它會回 0，下限 20 秒讓我們
        很快再試一次，而不是空等一個間隔。
        """
        self._api_next = time.time() + max(quota_source.seconds_until_allowed(), 20.0)
        self.refresh()

    # ── 開啟 5 小時視窗 ──
    def set_tray_style(self, style: str):
        self.tray_style = style
        save_state(tray_style=style)
        self._tray_pct = object()  # 讓下一次 refresh 一定重畫圖示
        self.refresh()

    def _confirm(self, title_key: str, body_key: str) -> bool:
        """開啟會花額度或動到登入的功能前，先把它做什麼、好處與代價講清楚。"""
        return fancymenu.ask(self.root, self.dpi, t(title_key), t(body_key), t("confirm.enable"), t("confirm.cancel"))

    def toggle_auto_poke(self):
        if not self.auto_poke and not self._confirm("confirm.auto_poke.title", "confirm.auto_poke.body"):
            return
        self.auto_poke = not self.auto_poke
        self._auto_tries, self._auto_gave_up = 0, False  # 重新開啟就重新給兩次機會
        save_state(auto_poke=self.auto_poke)
        # 開啟時直接講代價，關閉時只回報狀態
        self.window.note(t("note.auto_poke_on") if self.auto_poke else t("note.auto_poke", state=t("off")),
                         clear_after=8000)

    def toggle_token_refresh(self):
        if not quota_source.ALLOW_REFRESH and not self._confirm("confirm.token.title", "confirm.token.body"):
            return
        quota_source.ALLOW_REFRESH = not quota_source.ALLOW_REFRESH
        save_state(token_refresh=quota_source.ALLOW_REFRESH)
        self.window.note(t("note.token_refresh_on") if quota_source.ALLOW_REFRESH
                         else t("note.token_refresh", state=t("off")), clear_after=8000)
        self._api_next = 0.0  # 開了之後馬上試一次，不用等下一個週期
        self.refresh(force=True)

    # ── 桌面寵物 ──
    def toggle_pet_hook(self):
        """安裝或移除 Claude Code 的 Notification hook，讓寵物準確知道「在等你允許」。"""
        on = pet.hook_installed(CLAUDE_SETTINGS)
        if not on and not self._confirm("confirm.pet_hook.title", "confirm.pet_hook.body"):
            return
        try:
            if on:
                pet.uninstall_hook(CLAUDE_SETTINGS, DATA_DIR)
            else:
                pet.install_hook(CLAUDE_SETTINGS, DATA_DIR)
        except (OSError, ValueError) as e:
            self.window.note(t("pet.hook_failed", e=str(e)[:60]), clear_after=10000)
            return
        self.pet._hook_checked = 0.0  # 下次輪詢馬上換資料來源
        self.pet.say(t("pet.hook_off") if on else t("pet.hook_on"), PET_BUBBLE_MS)

    # ── 等 Claude 的時候看短影片 ──
    def video_items(self) -> list:
        """「看短影片」子選單：常見的影音網站，加上你自己加的（存在設定檔裡）。"""
        mine = [tuple(x) for x in load_state().get("video_links", []) if isinstance(x, list) and len(x) == 2]
        items = [{"label": name, "command": lambda u=url: self.open_videos(u)} for name, url in VIDEO_SITES]
        if mine:
            items.append("-")
            items += [{"label": name, "command": lambda u=url: self.open_videos(u)} for name, url in mine]
        items += ["-", {"label": t("videos.add"), "command": self.add_video_link}]
        if mine:
            items.append({"label": t("videos.remove"), "submenu": [
                {"label": name, "command": lambda n=name, u=url: self.remove_video_link(n, u)} for name, url in mine]})
        return items

    def add_video_link(self):
        got = fancymenu.ask_text(self.root, self.dpi, t("videos.add_title"),
                                 [t("videos.add_name"), t("videos.add_url")], t("videos.add_ok"), t("confirm.cancel"))
        if not got or not got[0] or not got[1]:
            return
        name, url = got
        if "://" not in url and not url.endswith(":"):
            url = "https://" + url          # 只打 youtube.com 也行；spotify: 這種 app 連結照原樣
        links = [x for x in load_state().get("video_links", []) if isinstance(x, list)]
        links.append([name, url])
        save_state(video_links=links)
        self.pet.say(t("videos.added", name=name), 4000)

    def remove_video_link(self, name: str, url: str):
        links = [x for x in load_state().get("video_links", []) if x != [name, url]]
        save_state(video_links=links)

    def open_videos(self, url: str):
        """用預設瀏覽器打開短影片。接下來兩小時內 Claude 一需要你，就發 Windows 通知叫你回來。

        看影片多半是全螢幕，寵物和卡片都會讓開，對話框看不到，所以改用系統通知。
        """
        import webbrowser
        webbrowser.open(url)
        self._watch_until = time.time() + WATCH_SEC
        self.pet.say(t("pet.say.videos"), PET_BUBBLE_MS)

    def _alert(self, text: str):
        if time.time() < self._watch_until or self.pet._suspended:
            if self.icon is not None:
                try:
                    self.icon.notify(text, "ClaudeQuota")
                except Exception:
                    pass

    def open_pet_folder(self):
        """打開自訂角色資料夾（第一次會建立，並放一份說明怎麼命名圖檔的 README）。"""
        root = pet.ensure_custom_root(DATA_DIR, i18n.lang())
        try:
            os.startfile(root)
        except OSError as e:
            self.window.note(str(e)[:60], clear_after=8000)
        self.pet.say(t("pet.say.custom"), PET_BUBBLE_MS * 2)

    def show_help(self):
        """使用說明視窗。已經開著就拉到最前面，不重複開。"""
        win = getattr(self, "_help", None)
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.lift()
            win.focus_force()
            return

        win = self._help = tk.Toplevel(self.root)
        win.title(t("help.title"))
        win.attributes("-topmost", True)
        win.configure(bg="#ffffff")
        try:
            win.iconbitmap(default=_app_icon())
        except tk.TclError:
            pass

        en = i18n.lang() == "en"
        family = "Segoe UI" if en else "Microsoft JhengHei UI"
        bold = "Segoe UI Semibold" if en else "Microsoft JhengHei UI"
        orange = "#%02x%02x%02x" % CLAUDE_ORANGE
        page, ink, muted, line = "#f6f6f8", "#1a1a1a", "#5c5c66", "#e6e6ea"

        # ── 標題列：圖示 + 名稱 + 副標 ──
        head = tk.Frame(win, bg="#ffffff")
        head.pack(fill="x")
        win._icon = ImageTk.PhotoImage(_gauge_icon(round(44 * self.dpi)))  # 留參照，不然會被回收
        tk.Label(head, image=win._icon, bg="#ffffff").pack(side="left", padx=(22, 12), pady=16)
        titles = tk.Frame(head, bg="#ffffff")
        titles.pack(side="left", pady=16)
        tk.Label(titles, text="ClaudeQuota", font=("Segoe UI Semibold", 15), fg=ink,
                 bg="#ffffff").pack(anchor="w")
        tk.Label(titles, text=t("help.subtitle"), font=(family, 9), fg=muted,
                 bg="#ffffff").pack(anchor="w")
        tk.Frame(win, bg=line, height=1).pack(fill="x")

        # ── 底部：主要按鈕 ──
        foot = tk.Frame(win, bg="#ffffff")
        foot.pack(side="bottom", fill="x")
        tk.Frame(win, bg=line, height=1).pack(side="bottom", fill="x")
        close = tk.Label(foot, text=t("help.close"), font=(bold, 10), fg="#ffffff", bg=orange,
                         padx=26, pady=6, cursor="hand2")
        close.pack(side="right", padx=20, pady=12)
        close.bind("<Button-1>", lambda _e: win.destroy())
        close.bind("<Enter>", lambda _e: close.configure(bg="#c4603f"))
        close.bind("<Leave>", lambda _e: close.configure(bg=orange))
        win.bind("<Escape>", lambda _e: win.destroy())

        # ── 內容 ──
        body = tk.Frame(win, bg=page)
        body.pack(fill="both", expand=True)
        # 中文沒有空格，"word" 會找不到斷點而把整行推到下一行，所以中文逐字換行
        text = tk.Text(body, wrap="word" if en else "char", relief="flat", bg=page, fg=ink,
                       font=(family, 10), padx=36, pady=16, spacing1=3, spacing2=8, spacing3=3,
                       width=58, height=28, cursor="arrow", highlightthickness=0, borderwidth=0)
        bar = tk.Scrollbar(body, command=text.yview)
        text.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)

        text.tag_configure("intro", font=(family, 10), foreground=muted, spacing1=2, spacing3=8)
        text.tag_configure("head", font=(bold, 11, "bold"), foreground=orange, spacing1=32, spacing3=12)
        # 圓點固定用小號 Segoe UI（正黑體的 ● 很大），懸掛縮排用量出來的寬度，換行才會對齊文字
        import tkinter.font as tkfont
        dot_font = tkfont.Font(family="Segoe UI", size=7)
        hang = 6 + dot_font.measure("●") + tkfont.Font(family=family, size=10).measure("  ")
        text.tag_configure("bullet", lmargin1=6, lmargin2=hang, spacing1=7, spacing3=9)
        text.tag_configure("dot", foreground=orange, font=dot_font)
        text.tag_configure("term", font=(bold, 10, "bold"))
        text.tag_configure("note", foreground=muted, lmargin1=6, lmargin2=6, spacing1=10)

        seen_head = False
        for raw in t("help.body").split("\n"):
            if not raw.strip():
                continue
            if raw.startswith("■ "):
                seen_head = True
                text.insert("end", raw[2:] + "\n", "head")
            elif raw.startswith("• "):
                item = raw[2:]
                # 「名稱：說明」把名稱加粗，掃讀時一眼看到在講哪個功能
                sep = next((s for s in ("：", ": ") if s in item), None)
                text.insert("end", "●", ("bullet", "dot"))
                text.insert("end", "  ", "bullet")
                if sep:
                    term, desc = item.split(sep, 1)
                    text.insert("end", term, ("bullet", "term"))
                    text.insert("end", sep + desc + "\n", "bullet")
                else:
                    text.insert("end", item + "\n", "bullet")
            else:
                text.insert("end", raw + "\n", "note" if seen_head else "intro")
        text.configure(state="disabled")
        # 滾輪在整個視窗上都能捲動內容
        win.bind("<MouseWheel>", lambda e: text.yview_scroll(int(-e.delta / 120) * 3, "units"))
        win.update_idletasks()
        # 放在螢幕中央
        w, h = win.winfo_width(), win.winfo_height()
        win.geometry(f"+{(win.winfo_screenwidth() - w) // 2}+{(win.winfo_screenheight() - h) // 2}")
        win.focus_force()

    def set_lang(self, choice: str):
        i18n.set_lang(choice)
        save_state(lang=choice)
        self._tray_pct = object()  # 系統列提示文字也要換語言
        if self.icon:
            self.icon.update_menu()
        self.refresh(force=True)

    def request_poke(self, auto: bool = False):
        if self._poking:
            return
        self._poking = True
        self._last_poke = datetime.now(timezone.utc)
        self.window.note(t("poke.running_auto") if auto else t("poke.running"))
        threading.Thread(target=self._poke_worker, daemon=True).start()

    def _poke_worker(self):
        ok, msg = poke_window()

        def done():
            self._poking = False
            # 只用中文字：微軟正黑體 Light 沒有 ✓ / ✕ 的 glyph，會畫成豆腐格
            self.window.note(msg if ok else t("poke.fail_prefix", msg=msg), clear_after=6000)
            self.refresh(force=True)

        self.root.after(0, done)

    def _maybe_auto_poke(self, q):
        """沒有進行中的視窗時自動點著它。

        resets_at 為 None 就代表目前沒有 5 小時視窗在跑（前一個已經過期）。
        冷卻時間必須大於桌面版 5 分鐘的取樣間隔，否則快取還沒反映就會重複觸發。
        """
        if q is not None and q.resets_at is not None:
            self._auto_tries = 0  # 視窗開起來了，下一次重置後可以再試
            self._auto_gave_up = False
            return
        if not self.auto_poke or self._poking or q is None:
            return
        # 資料太舊就不知道視窗到底有沒有在跑（例如桌面版很久沒更新它的用量紀錄），不要亂戳
        if not q.live and q.age_min > AUTO_POKE_MAX_AGE_MIN:
            return
        if self._last_poke and \
                (datetime.now(timezone.utc) - self._last_poke).total_seconds() < AUTO_POKE_COOLDOWN_MIN * 60:
            return
        # 起算過了視窗還是沒開，就是這條路對這台電腦沒用（例如 CLI 登入的不是訂閱帳號）。
        # 不停下來的話每 12 分鐘就會再花一次，所以試兩次沒效果就停，並告訴使用者。
        if self._auto_tries >= AUTO_POKE_MAX_TRIES:
            if not self._auto_gave_up:
                self._auto_gave_up = True
                self.window.note(t("poke.no_effect"), clear_after=30000)
            return
        self._auto_tries += 1
        self.request_poke(auto=True)

    def _lift_all(self):
        """殼層事件一來就立刻搶回置頂，不等下一趟看門狗。"""
        for w in (self.window, self.mini, self.pet):
            w.lift_now()

    def _guard(self):
        """定期巡視兩個浮動視窗，確保它們還在該在的地方。

        虛擬桌面只在這裡問一次就好——兩個視窗面對的是同一個桌面。
        """
        desk = current_desktop_id()
        changed = desk is not None and self._desk is not None and desk != self._desk
        if desk is not None:
            self._desk = desk
        busy = fullscreen_monitor()
        for w in (self.window, self.mini, self.pet):
            w.keep_alive(changed, busy)
        self.root.after(GUARD_MS, self._guard)

    def _start_ticker(self):
        """每秒更新卡片上的「N 秒前更新」。

        只在卡片看得見時才跑，而且靠 _signature() 擋掉沒必要的重畫——
        秒數沒跳就不會真的去重繪那 34ms。
        """
        if self._ticker is not None:
            return

        def tick():
            self._ticker = None
            if self.window.visible:
                self.window.redraw()
            self._ticker = self.root.after(1000, tick)

        self._ticker = self.root.after(1000, tick)

    def refresh(self, *_, force: bool = False):
        # 畫面每 REFRESH_SEC 重畫是為了倒數；網路照 quota_source 自己收斂出來的節奏
        if time.time() >= self._api_next:
            self._pull_api()  # 下次什麼時候再問由 _after_pull 決定

        try:
            q = quota_source.get_quota()
        except Exception as e:
            if sys.stderr:
                print("get_quota failed:", e, file=sys.stderr)
            q = None
        self.window.render(q)
        self.mini.render(q)

        pct = None if q is None else round(q.percent_used)
        week = None if q is None or q.week_used is None else round(q.week_used)
        if self.icon and ((pct, week) != self._tray_pct or force):  # 雙條樣式也畫每週，所以兩個都看
            self._tray_pct = (pct, week)
            self.icon.icon = make_tray_image(pct, self.tray_style, week)
            if q is None:
                self.icon.title = t("tray.no_data")
            else:
                week = t("tray.week", pct=f"{q.week_used:.0f}") if q.week_used is not None else ""
                pace = q.week_pace()
                if pace and pace[0] == "short":
                    week += t("tray.pace_short", time=weekday_time(pace[1]))
                stale = t("tray.stale") if q.stale else ""
                self.icon.title = t("tray.used", pct=f"{q.percent_used:.0f}", week=week, stale=stale)

        self._maybe_auto_poke(q)
        self._start_ticker()
        # 手動重整也會走到這裡，不取消舊的排程就會養出好幾條並行的更新迴圈
        if self._tick is not None:
            self.root.after_cancel(self._tick)
        self._tick = self.root.after(REFRESH_SEC * 1000, self.refresh)

    # ── 右鍵選單（系統列圖示） ──
    def show_tray_menu(self):
        fancymenu.load_custom_themes(THEMES_FILE)  # 改了 themes.json 不用重開
        avoid = self.pet._head() if self.pet.visible else None
        fancymenu.popup(self.root, self.dpi, self.tray_items(), from_taskbar=True, title="ClaudeQuota", avoid=avoid)

    def set_theme(self, name: str):
        """選單、確認視窗、寵物對話框一起換主題。"""
        fancymenu.set_theme(name)
        save_state(theme=name)
        self.pet.say(t("theme.changed"), 3500)

    def tray_items(self) -> list:
        """系統列右鍵選單。每次打開都重新產生，勾勾和語言才會是最新的。

        分三層：最常用的動作放最上面，寵物一組，外觀一組；會花額度或改到別的程式設定的
        功能收進「進階」，開啟前都會先說明。
        """
        def radio(label, current, command):
            return {"label": label, "checked": current, "radio": True, "command": command}

        def home():
            (self.mini if self.minimized else self.window).reset_position()

        return [
            {"label": t("menu.show"), "command": self.toggle_visible},
            {"label": t("menu.mini"), "checked": self.minimized,
             "command": self.expand if self.minimized else self.minimize},
            {"label": t("menu.refresh"), "command": self.request_refresh},
            {"label": t("menu.poke"), "command": self.request_poke},
            "-",
            {"label": t("menu.pet"), "submenu": lambda: self.pet.menu_items(tray=True)},
            {"label": t("menu.videos"), "submenu": self.video_items},
            {"label": t("menu.look"), "submenu": lambda: [
                {"label": t("menu.style"), "submenu": [
                    radio(t(lab), self.tray_style == k, lambda k=k: self.set_tray_style(k)) for k, lab in TRAY_STYLES]},
                {"label": t("menu.lang"), "submenu": [
                    radio(t("lang.auto"), i18n.choice() == "auto", lambda: self.set_lang("auto")),
                    radio("中文", i18n.choice() == "zh", lambda: self.set_lang("zh")),
                    radio("English", i18n.choice() == "en", lambda: self.set_lang("en"))]},
                {"label": t("menu.theme"), "submenu": [
                    radio(fancymenu.LABELS.get(k) or t(f"theme.{k}"), fancymenu.theme_name() == k,
                          lambda k=k: self.set_theme(k))
                    for k in fancymenu.THEMES]},
                {"label": t("menu.home"), "command": home},
            ]},
            {"label": t("menu.advanced"), "submenu": lambda: [
                {"label": t("menu.auto_poke"), "checked": self.auto_poke, "command": self.toggle_auto_poke},
                {"label": t("menu.token_refresh"), "checked": quota_source.ALLOW_REFRESH,
                 "command": self.toggle_token_refresh},
                {"label": t("pet.hook"), "checked": pet.hook_installed(CLAUDE_SETTINGS),
                 "command": self.toggle_pet_hook},
                {"label": t("pet.auto_jump"), "checked": self.pet.auto_jump,
                 "command": lambda: self.pet.set_auto_jump(not self.pet.auto_jump)},
                {"label": t("pet.chat"), "checked": chat_state.enabled,
                 "command": lambda: self.pet.set_chat_detect(not chat_state.enabled)},
            ]},
            "-",
            {"label": t("menu.help"), "command": self.show_help},
            {"label": t("menu.quit"), "command": self.quit},
        ]

    def run(self):
        import pystray

        # 右鍵選單是自己畫的（fancymenu），不用 Windows 預設的灰色選單。pystray 只負責系統列圖示本身，
        # 它的選單只留一個預設項目給左鍵用；右鍵改由 _TrayIcon 攔下來開我們的選單。
        menu = pystray.Menu(
            pystray.MenuItem(lambda _i: t("menu.show"), lambda: self.root.after(0, self.toggle_visible),
                             default=True),
            pystray.MenuItem(lambda _i: t("menu.quit"), self.quit),
        )
        self.icon = _tray_icon_class(pystray)("ClaudeQuota", make_tray_image(None, self.tray_style),
                                              t("tray.title"), menu)
        self.icon.on_right_click = lambda: self.root.after(0, self.show_tray_menu)
        threading.Thread(target=self.icon.run, daemon=True).start()
        # 圖示註冊之後才有對應的登錄檔項目可以設定，所以延後一點再提升
        self.root.after(4000, promote_tray_icon)
        self.refresh()
        self._guard()
        if self.pet.visible:
            self.pet.start()
        # hook 的事件是靠執行緒的訊息佇列送的，而 mainloop 就在抽這個佇列
        self._hook = hook_shell_events(self._lift_all)
        self.root.mainloop()

    def quit(self, *_):
        if self.icon:
            self.icon.stop()
        self.root.after(0, self.root.destroy)


if __name__ == "__main__":
    App().run()
