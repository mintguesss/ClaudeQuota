"""桌面版「聊天」模式（不是 Code 分頁）的狀態：正在回、回完了。

聊天不會寫 ~/.claude/projects 的紀錄，所以另外看兩個地方：

1. 正在回：桌面版視窗裡有沒有「停止回應」按鈕。用 Windows 的 UI Automation 讀視窗上的按鈕名稱
   （跟螢幕閱讀器讀的是同一份資料），不讀對話內容。視窗縮小時也讀得到，但只看得到目前打開的那個
   聊天；你切到別的頁面時就看不到，這時靠第 2 點補上「回完了」。
   需要 comtypes；沒裝就只用第 2 點。
2. 回完了：桌面版把聊天清單快取在本機（IndexedDB，Snappy 壓縮的 V8 序列化資料），每個聊天有
   name（標題）、last_settled_at（最後一次回完的時間）、last_read_at（你最後看的時間）。
   Claude 回完後幾秒內就會寫進去。只取這三個欄位和對話 id，不讀訊息內容。

都是桌面版內部的格式，改版就可能失效；讀不到時安靜地什麼都不回報，不影響其他功能。
"""
from __future__ import annotations

import ctypes
import glob
import os
import re
import threading
import time
from datetime import datetime
from typing import Optional

POLL_SEC = 2.0
DONE_SEC = 3 * 60        # 回完之後算「做完了」多久（跟 Code 對話一樣）
RECENT_SEC = 15 * 60     # 只看最近這麼久有更新的快取檔

# 「停止回應」按鈕在各語言介面的名稱
STOP_NAMES = {"停止回應", "停止回应", "停止响应", "Stop response", "Stop generating", "応答を停止", "응답 중지"}
# 聊天頂端標題按鈕的名稱是「<標題>，重新命名工作階段」
_TITLE_BTN = re.compile(r"^(.+?)\s*(?:，|,)\s*(?:重新命名|重命名|rename)", re.I)

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

enabled = True
_lock = threading.Lock()
_live: dict = {"title": None, "since": 0.0, "ok": False}   # title：正在回的聊天標題（None = 沒在回）
_convs: dict[str, dict] = {}                                # 對話 id → {name, settled, read}
_seen_files: dict[str, float] = {}
_thread: Optional[threading.Thread] = None


# ---------- 快取 ----------

def cache_dirs() -> list[str]:
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    dirs = glob.glob(os.path.join(local, "Claude-Data", "*", "IndexedDB"))
    dirs.append(os.path.join(roaming, "Claude", "IndexedDB"))  # 舊版桌面版的位置
    out = []
    for d in dirs:
        for sub in ("https_claude.ai_0.indexeddb.blob", "https_claude.ai_0.indexeddb.leveldb"):
            p = os.path.join(d, sub)
            if os.path.isdir(p):
                out.append(p)
    return out


def _varint(b: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        s += 7
        if c < 0x80:
            return r, i


def unsnappy(b: bytes) -> bytes:
    """Snappy 解壓縮（raw 格式）。純 Python，不用多裝套件。"""
    n, i = _varint(b, 0)
    out = bytearray()
    end = len(b)
    while i < end:
        tag = b[i]
        i += 1
        kind = tag & 3
        if kind == 0:
            ln = tag >> 2
            if ln >= 60:
                k = ln - 59
                ln = int.from_bytes(b[i:i + k], "little")
                i += k
            ln += 1
            out += b[i:i + ln]
            i += ln
            continue
        if kind == 1:
            ln = ((tag >> 2) & 7) + 4
            off = ((tag >> 5) << 8) | b[i]
            i += 1
        elif kind == 2:
            ln = (tag >> 2) + 1
            off = int.from_bytes(b[i:i + 2], "little")
            i += 2
        else:
            ln = (tag >> 2) + 1
            off = int.from_bytes(b[i:i + 4], "little")
            i += 4
        if off <= 0 or off > len(out):
            raise ValueError("bad snappy offset")
        start = len(out) - off
        if off >= ln:
            out += out[start:start + ln]
        else:  # 重疊的複製：一段一段重複
            for j in range(ln):
                out.append(out[start + j])
    if len(out) != n:
        raise ValueError("bad snappy length")
    return bytes(out)


def load_value(raw: bytes) -> bytes:
    """IndexedDB 的值：Chromium 大的值會用 Snappy 壓縮（開頭 ff 11 02）。"""
    if raw[:3] == b"\xff\x11\x02":
        return unsnappy(raw[3:])
    return raw


def _v8_string(b: bytes, i: int) -> tuple[Optional[str], int]:
    """讀 V8 序列化的字串：'"' 單位元組（Latin-1），'c' 雙位元組（UTF-16LE），前面可能有 00 對齊。"""
    while i < len(b) and b[i] == 0:
        i += 1
    if i >= len(b):
        return None, i
    tag = b[i]
    if tag not in (0x22, 0x63):
        return None, i
    n, j = _varint(b, i + 1)
    data = b[j:j + n]
    try:
        s = data.decode("latin-1") if tag == 0x22 else data.decode("utf-16-le")
    except UnicodeDecodeError:
        return None, j + n
    return s, j + n


def _iso(s: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


_ENTRY = b'"\ncreated_at"'
_KEY = re.compile(rb'"\x0flast_settled_at"')


def parse_conversations(b: bytes) -> dict[str, dict]:
    """從快取資料裡找出聊天清單的每一項：id、標題、最後回完、最後讀過的時間。"""
    out: dict[str, dict] = {}
    for m in _KEY.finditer(b):
        start = b.rfind(_ENTRY, max(0, m.start() - 600), m.start())
        if start < 0:
            continue
        nxt = b.find(_ENTRY, m.end())
        entry = b[start: nxt if nxt > 0 else min(len(b), m.end() + 6000)]
        settled, _ = _v8_string(entry, entry.find(b'"\x0flast_settled_at"') + 17)
        read_at = None
        k = entry.find(b'"\x0clast_read_at"')
        if k >= 0:
            read_at, _ = _v8_string(entry, k + 14)
        k = entry.find(b'"\x04name')
        name = _v8_string(entry, k + 6)[0] if k >= 0 else None
        k = entry.rfind(b'"\x04uuid"$')
        uid = entry[k + 8:k + 44].decode("latin-1") if k >= 0 else ""
        if not _UUID.match(uid) or not settled:
            continue
        ts = _iso(settled)
        if ts is None:
            continue
        old = out.get(uid)
        if old and old["settled"] >= ts:
            continue
        out[uid] = {"name": name or "", "settled": ts, "read": _iso(read_at) if read_at else None}
    return out


def _scan_cache(now: float):
    found: dict[str, dict] = {}
    files = []
    for d in cache_dirs():
        if d.endswith(".blob"):  # 大的值各自一個檔，路徑像 blob\5\05\54a
            files += glob.glob(os.path.join(d, "*", "*", "*"))
        else:  # 小的值在 leveldb 的 .log 裡（沒壓縮）
            files += glob.glob(os.path.join(d, "*.log"))
    for f in files:
        try:
            mt = os.path.getmtime(f)
        except OSError:
            continue
        if now - mt > RECENT_SEC or _seen_files.get(f) == mt:
            continue
        _seen_files[f] = mt
        try:
            with open(f, "rb", buffering=0) as fh:
                raw = fh.read()
            if b"last_settled_at" not in raw and raw[:3] != b"\xff\x11\x02":
                continue
            found.update({k: v for k, v in parse_conversations(load_value(raw)).items()
                          if k not in found or found[k]["settled"] < v["settled"]})
        except (OSError, ValueError, IndexError):
            continue
    if found:
        with _lock:
            for k, v in found.items():
                if k not in _convs or _convs[k]["settled"] <= v["settled"]:
                    _convs[k] = v
    for f in [f for f, mt in _seen_files.items() if now - mt > RECENT_SEC]:
        del _seen_files[f]


# ---------- 視窗（UI Automation） ----------

class _Uia:
    def __init__(self):
        import comtypes
        import comtypes.client

        comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U

        self.U = U
        self.uia = comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation)
        self.cond = self.uia.CreatePropertyCondition(U.UIA_ControlTypePropertyId, U.UIA_ButtonControlTypeId)
        self.cache = self.uia.CreateCacheRequest()
        self.cache.AddProperty(U.UIA_NamePropertyId)

    def button_names(self, hwnd: int) -> list[str]:
        root = self.uia.ElementFromHandle(hwnd)
        arr = root.FindAllBuildCache(self.U.TreeScope_Descendants, self.cond, self.cache)
        if not arr:
            return []
        names = []
        for i in range(arr.Length):
            try:
                names.append(arr.GetElement(i).CachedName or "")
            except Exception:  # 讀到一半那個按鈕不見了（畫面在變）
                continue
        return names


def streaming_title(names: list[str]) -> Optional[str]:
    """按鈕名稱裡有「停止回應」就是正在回，回傳標題（看不出來就空字串）；沒在回就 None。"""
    if not any(n in STOP_NAMES for n in names):
        return None
    for n in names:
        m = _TITLE_BTN.match(n)
        if m:
            return m.group(1).strip()
    return ""


def _desktop_window() -> int:
    if os.name != "nt":
        return 0
    from claude_state import _find_window
    return _find_window("desktop", "")


def _run():
    uia = None
    uia_failed = False
    while True:
        now = time.time()
        if enabled:
            title = None
            if not uia_failed:
                try:
                    if uia is None:
                        uia = _Uia()
                    hwnd = _desktop_window()
                    if hwnd:
                        title = streaming_title(uia.button_names(hwnd))
                except ImportError:
                    uia_failed = True  # 沒裝 comtypes：只用快取
                except Exception:
                    uia = None  # 桌面版重開之類的，下一輪重建
            with _lock:
                if title is None:
                    _live.update(title=None, since=0.0)
                elif _live["title"] is None:
                    _live.update(title=title, since=now)
                else:  # 新聊天回到一半會從「New chat」自動改名，還是同一次回覆
                    _live["title"] = title
                _live["ok"] = True
            try:
                _scan_cache(now)
            except Exception:
                pass
        time.sleep(POLL_SEC)


def _ensure_thread():
    global _thread
    if _thread is None and os.name == "nt":
        _thread = threading.Thread(target=_run, name="chat-state", daemon=True)
        _thread.start()


def sessions(now: Optional[float] = None) -> list[dict]:
    """現在有動靜的聊天：[{kind, name, since, id, unread}]。kind 是 working 或 done。"""
    if not enabled:
        return []
    _ensure_thread()
    now = now or time.time()
    out = []
    with _lock:
        live_title, live_since = _live["title"], _live["since"]
        convs = dict(_convs)
    if live_title is not None:
        uid = next((k for k, v in sorted(convs.items(), key=lambda kv: -kv[1]["settled"])
                    if live_title and v["name"] == live_title), "")
        out.append({"kind": "working", "name": live_title, "since": live_since, "id": uid, "unread": False})
    for uid, v in convs.items():
        age = now - v["settled"]
        if age < 0 or age > DONE_SEC:
            continue
        if live_title is not None and v["name"] == live_title:
            continue  # 同一個聊天又在回了
        unread = v["read"] is None or v["read"] < v["settled"] - 1
        out.append({"kind": "done", "name": v["name"], "since": v["settled"], "id": uid, "unread": unread})
    return out
