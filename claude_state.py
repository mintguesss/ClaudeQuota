"""Claude Code 現在在做什麼：工作中、在等你、做完了、出錯了，或沒事。

給桌面寵物用。資料來源有兩個：

1. 對話紀錄（預設，免設定）：~/.claude/projects/<專案>/<對話>.jsonl。
   CLI、VS Code 擴充功能和桌面版的 Code 分頁都會寫這裡，所以三種都抓得到。
   只讀最近幾分鐘有動過的檔案的結尾，不讀內容文字，只看每一筆的種類和時間，
   外加你幫對話取的名稱（custom-title 那幾行），用來稱呼它。
   限制：「在等你按允許」紀錄裡看不出來，只能用「工具呼叫送出後很久沒結果」來猜，
   跑很久的指令也會被猜成在等你。
2. Claude Code 的 Notification hook（選用）：需要你允許時 Claude Code 會呼叫 hook，
   hook 把通知內容寫到一個檔案。有裝的話「在等你」就用它，不再用猜的。
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

PROJECTS = os.path.expanduser("~/.claude/projects")

ACTIVE_SEC = 15 * 60    # 這麼久沒動的對話就不看了
DONE_SEC = 3 * 60       # 做完之後開心多久
ERROR_SEC = 5 * 60
THINK_SEC = 10 * 60     # 送出之後這麼久都沒回應，大概是被關掉了，不算工作中
WAIT_GUESS_SEC = 20     # 工具呼叫送出後這麼久沒結果，猜是在等你按允許
TAIL_BYTES = 96 * 1024
TITLE_MAX = 24          # 對話名稱太長就截斷，免得對話框被撐開

# 會跳出允許提示的工具。Read / Grep 這類唯讀工具預設不用問，跑久了也不是在等你。
ASKS_PERMISSION = ("Bash", "PowerShell", "Edit", "MultiEdit", "Write", "NotebookEdit", "WebFetch", "mcp__")

# 狀態的優先順序：同時有好幾個對話時，顯示最需要你注意的那個
PRIORITY = {"waiting": 4, "error": 3, "working": 2, "done": 1, "idle": 0}


@dataclass
class State:
    kind: str                     # waiting / error / working / done / idle
    project: str = ""             # 你幫對話取的名稱；沒取過就是對話所在資料夾的名字
    since: float = 0.0            # 這個狀態從什麼時候開始（epoch 秒）
    guessed: bool = False         # waiting 是猜的（沒裝 hook）
    others: int = 0               # 另外還有幾個對話在跑
    key: str = ""                 # 哪個對話（紀錄檔路徑），用來分辨同一個對話的同一次完成
    link: str = ""                # 點名稱時要打開哪裡（見 open_session）；終端機裡的對話沒有
    entry: str = ""               # 對話從哪裡開的：claude-desktop / claude-vscode / cli …
    cwd: str = ""


def _ts(entry: dict) -> Optional[float]:
    s = entry.get("timestamp")
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _tail_entries(path: str) -> list[dict]:
    """檔案結尾的幾十筆紀錄（新的在後面）。第一行可能被切一半，丟掉。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            raw = f.read()
    except OSError:
        return []
    lines = raw.decode("utf-8", "ignore").splitlines()
    if size > TAIL_BYTES:
        lines = lines[1:]
    out = []
    for line in lines:
        try:
            j = json.loads(line)
        except ValueError:
            continue
        if isinstance(j, dict) and j.get("type") in ("user", "assistant") and not j.get("isSidechain"):
            out.append(j)
    return out


_titles: dict[str, tuple[int, str]] = {}  # 紀錄檔路徑 → (讀到第幾個 byte, 名稱)


def session_title(path: str) -> str:
    """你幫這個對話取的名稱（桌面版改名或 /rename），沒取過就是空字串。

    名稱可能寫在檔案的任何位置，所以第一次會從頭找；讀過的部分記住，之後只讀新增的幾行。
    只解析含 custom-title 的那幾行。
    """
    done, title = _titles.get(path, (0, ""))
    try:
        size = os.path.getsize(path)
        if size < done:  # 檔案被重寫過，從頭來
            done, title = 0, ""
        if size == done:
            return title
        with open(path, "rb") as f:
            f.seek(done)
            for line in f:
                if not line.endswith(b"\n"):
                    break  # 最後一行可能還沒寫完，下次再讀
                done += len(line)
                if b'"custom-title"' not in line:
                    continue
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                if isinstance(j, dict) and j.get("type") == "custom-title":
                    title = " ".join(str(j.get("customTitle") or "").split())
    except OSError:
        return title
    _titles[path] = (done, title)
    return title


# 桌面版 Code 分頁替每個對話存的資料：側邊欄上看到的名稱（自動取的或你改的）就在這裡，
# 不在紀錄檔裡。cliSessionId 就是紀錄檔的檔名，用它對回去。
DESKTOP_SESSIONS = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Claude-Data", "*", "claude-code-sessions"),
    os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code-sessions"),
    os.path.expanduser("~/Library/Application Support/Claude/claude-code-sessions"),  # macOS
]
DESKTOP_SCAN_SEC = 10
_desktop: dict = {"at": 0.0, "files": {}, "titles": {}, "links": {}, "busy": False}
# files: 路徑 → (修改時間, [(紀錄檔 id, 名稱, 桌面版對話 id)])


def desktop_titles(now: Optional[float] = None, wait: bool = False) -> dict[str, str]:
    """紀錄檔 id → 桌面版側邊欄上的對話名稱。

    每個檔案有 400 KB 上下，第一次讀可能要一秒，所以在背景執行緒重掃，這裡直接回上一次的結果
    （寵物晚兩秒才換上名稱，總比畫面卡住好）。每 10 秒最多重掃一次，沒變的檔案不重讀。
    wait=True 時同步掃完再回傳（測試用）。
    """
    import threading

    now = now or time.time()
    if now - _desktop["at"] >= DESKTOP_SCAN_SEC and not _desktop["busy"]:
        _desktop["at"] = now
        _desktop["busy"] = True
        if wait:
            _scan_desktop()
        else:
            threading.Thread(target=_scan_desktop, daemon=True).start()
    return _desktop["titles"]


def _scan_desktop():
    try:
        _scan_desktop_files()
    finally:
        _desktop["busy"] = False  # 出了什麼意外也要能再掃下一次


def _scan_desktop_files():
    import glob

    files = {}
    for base in DESKTOP_SESSIONS:
        for path in glob.glob(os.path.join(base, "*", "*", "*.json")):
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            cached = _desktop["files"].get(path)
            if cached and cached[0] == mtime:
                files[path] = cached
                continue
            pairs = []
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                title = " ".join(str(d.get("title") or "").split())
                local = str(d.get("sessionId") or "") if not d.get("isArchived") else ""
                for key in ("cliSessionId", "lastAssistantCliSessionId"):
                    if d.get(key):
                        pairs.append((str(d[key]), title, local))
            except (OSError, ValueError, AttributeError):
                pass
            files[path] = (mtime, pairs)
    # 修改時間舊的先放、新的後放：同一個紀錄檔被兩個對話用到時，以最近動過的那個為準
    titles, links = {}, {}
    for _mtime, pairs in sorted(files.values(), key=lambda x: x[0]):
        for cli, title, local in pairs:
            if title:
                titles[cli] = title
            if local.startswith("local_"):
                links[cli] = local
    # 一次換掉，讀的那邊不會看到一半
    _desktop["files"], _desktop["titles"], _desktop["links"] = files, titles, links


_LOCAL_ID = re.compile(r"^local_[A-Za-z0-9-]{1,64}$")  # 跟桌面版自己檢查的格式一樣


_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
VSCODE_OPEN_DELAY = 1.5  # 先叫出那個資料夾的 VS Code 視窗，等它到前面再開對話


def open_session(link: str) -> bool:
    """打開這個對話。link 有兩種：

    - local_…：桌面版 Code 分頁的對話，用桌面版註冊的 claude:// 連結
    - vscode:<對話 id>|<資料夾>：VS Code 擴充功能裡的對話。VS Code 會把連結交給最後用過的那個視窗，
      所以先用 vscode://file 叫出那個資料夾的視窗（已經開著就是切過去），再用擴充功能自己的
      vscode://anthropic.claude-code/open?session= 打開對話
    """
    import threading

    link = link or ""
    try:
        # Windows 只讓「剛被點過的程式」把別的視窗拉到前面。點寵物對話框的是我們，
        # 這裡把這個權利讓給任何程式，桌面版和 VS Code 才能自己跳到最前面，不會只在工作列閃
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except (AttributeError, OSError):
        pass
    try:
        if _LOCAL_ID.match(link):
            os.startfile(f"claude://code/continue?session={link}&source=desktop_action")
            return True
        if link.startswith("vscode:"):
            sid, _, folder = link[len("vscode:"):].partition("|")
            if not _UUID.match(sid):
                return False
            if folder and os.path.isdir(folder):
                os.startfile("vscode://file/" + folder.replace("\\", "/").rstrip("/") + "/")
                threading.Timer(VSCODE_OPEN_DELAY, _open_vscode_session, (sid,)).start()
            else:
                _open_vscode_session(sid)
            return True
    except (OSError, AttributeError):  # AttributeError：不是 Windows
        return False
    return False


def bring_forward(kind: str, folder: str = "", delay: float = 2.0):
    """自動切換時把桌面版或 VS Code 的視窗拉到最前面（背景執行緒，等對方先處理完連結）。

    使用者點了寵物對話框時，Windows 允許我們把前景讓給對方；但自動切換沒人點，
    Windows 只會讓對方在工作列閃一下。這裡找到那個視窗，借用目前前景視窗的輸入佇列
    （AttachThreadInput）把它叫到前面——Windows 上要從背景把視窗拉到前面的標準做法。
    kind："desktop" 或 "vscode"（vscode 用資料夾名比對視窗標題）。
    """
    import threading

    def work():
        time.sleep(delay)
        try:
            hwnd = _find_window(kind, folder)
            if hwnd:
                _force_foreground(hwnd)
        except Exception:
            pass

    threading.Thread(target=work, daemon=True).start()


def _find_window(kind: str, folder: str) -> int:
    import ctypes.wintypes as wt

    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    want_exe = "claude.exe" if kind == "desktop" else "code.exe"
    name = os.path.basename(folder.rstrip("\\/")).lower()
    best = [0, 0]

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def each(h, _):
        if not u.IsWindowVisible(h) or u.GetWindow(h, 4):  # 4 = GW_OWNER：只看主視窗
            return True
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        proc = k.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not proc:
            return True
        buf, size = ctypes.create_unicode_buffer(520), wt.DWORD(520)
        ok = k.QueryFullProcessImageNameW(proc, 0, buf, ctypes.byref(size))
        k.CloseHandle(proc)
        if not ok or os.path.basename(buf.value).lower() != want_exe:
            return True
        title = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(h, title, 512)
        r = wt.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        area = (r.right - r.left) * (r.bottom - r.top)
        if kind == "vscode" and name and name not in title.value.lower():
            return True
        if area > best[1]:
            best[:] = [h, area]
        return True

    u.EnumWindows(each, 0)
    return best[0]


def _force_foreground(hwnd: int):
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg = u.GetForegroundWindow()
    fg_thread = u.GetWindowThreadProcessId(fg, None)
    me = k.GetCurrentThreadId()
    attached = fg_thread and fg_thread != me and u.AttachThreadInput(me, fg_thread, True)
    try:
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
    finally:
        if attached:
            u.AttachThreadInput(me, fg_thread, False)


def _open_vscode_session(sid: str):
    try:
        os.startfile(f"vscode://anthropic.claude-code/open?session={sid}")
    except (OSError, AttributeError):
        pass


def _short(name: str) -> str:
    return name if len(name) <= TITLE_MAX else name[:TITLE_MAX - 1].rstrip() + "…"


def _content(entry: dict) -> list:
    c = (entry.get("message") or {}).get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    return c if isinstance(c, list) else []


def session_state(entries: list[dict], now: float, hook: Optional[dict] = None,
                  guess_waiting: bool = True) -> Optional[State]:
    """從一個對話的最後幾筆紀錄判斷它現在的狀態。看不出來就 None。"""
    if not entries:
        return None
    last = entries[-1]
    t = _ts(last)
    if t is None:
        return None
    project = os.path.basename(str(last.get("cwd") or "").rstrip("\\/"))
    age = now - t

    # hook 說在等你，而且之後對話沒有再動過，就是真的在等你
    if hook and hook.get("at", 0) >= t - 0.5:
        return State("waiting", project, hook["at"])

    if last.get("type") == "assistant":
        if last.get("isApiErrorMessage"):
            return State("error", project, t) if age < ERROR_SEC else State("idle", project, t)
        reason = (last.get("message") or {}).get("stop_reason")
        if reason == "tool_use":
            tools = [b.get("name", "") for b in _content(last) if b.get("type") == "tool_use"]
            asks = any(n.startswith(ASKS_PERMISSION) for n in tools)
            if guess_waiting and asks and WAIT_GUESS_SEC < age < THINK_SEC:
                return State("waiting", project, t + WAIT_GUESS_SEC, guessed=True)
            return State("working", project, t) if age < THINK_SEC else State("idle", project, t)
        if reason in (None, ""):
            # 還在串流中（思考或文字寫到一半）
            return State("working", project, t) if age < THINK_SEC else State("idle", project, t)
        return State("done", project, t) if age < DONE_SEC else State("idle", project, t)

    # 最後一筆是使用者：剛送出的提問，或剛跑完的工具結果，Claude 正在想下一步
    texts = [b.get("text", "") for b in _content(last) if b.get("type") == "text"]
    if any(s.startswith("[Request interrupted") for s in texts):
        return State("idle", project, t)
    return State("working", project, t) if age < THINK_SEC else State("idle", project, t)


def read_hook(path: str) -> Optional[dict]:
    """hook 寫下的最後一則「需要你允許」通知：{'transcript': 路徑, 'at': 時間}。"""
    try:
        at = os.path.getmtime(path)
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    kind = str(data.get("notification_type") or "")
    message = str(data.get("message") or "")
    # 「Claude 在等你輸入」是做完之後閒著的提醒，不是卡在允許提示
    if kind == "idle_prompt" or (not kind and "permission" not in message.lower()):
        return None
    return {"transcript": os.path.normcase(os.path.abspath(str(data.get("transcript_path") or ""))), "at": at}


_cache: dict[str, tuple[tuple[float, int], list[dict]]] = {}


def current(hook_file: Optional[str] = None, now: Optional[float] = None) -> State:
    """所有最近有動的對話裡，最需要你注意的那個狀態。"""
    now = now or time.time()
    hook = read_hook(hook_file) if hook_file else None
    states = []
    try:
        projects = list(os.scandir(PROJECTS))
    except OSError:
        projects = []
    for proj in projects:
        if not proj.is_dir():
            continue
        try:
            files = list(os.scandir(proj.path))
        except OSError:
            continue
        for f in files:
            if not f.name.endswith(".jsonl"):
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            if now - st.st_mtime > ACTIVE_SEC:
                continue
            key = (st.st_mtime, st.st_size)
            cached = _cache.get(f.path)
            if cached and cached[0] == key:
                entries = cached[1]
            else:
                entries = _tail_entries(f.path)[-8:]
                _cache[f.path] = (key, entries)
            mine = hook if hook and hook["transcript"] == os.path.normcase(os.path.abspath(f.path)) else None
            s = session_state(entries, now, mine, guess_waiting=hook_file is None)
            if s:
                s.key = f.path
                s.entry = str(entries[-1].get("entrypoint") or "")
                s.cwd = str(entries[-1].get("cwd") or "")
                # 名稱的優先順序：/rename 取的 → 桌面版側邊欄上的名稱 → 資料夾名
                cli = f.name[:-len(".jsonl")]
                title = session_title(f.path) or desktop_titles(now).get(cli, "")
                s.link = _desktop["links"].get(cli, "")
                if not s.link and s.entry == "claude-vscode":
                    s.link = f"vscode:{cli}|{s.cwd}"
                if title:
                    s.project = _short(title)
                states.append(s)
    # 不再活躍的檔案不用留在快取裡
    for cache in (_cache, _titles):
        for path in [p for p in cache if not os.path.exists(p)]:
            del cache[path]
    busy = sorted((s for s in states if s.kind != "idle"), key=lambda s: (PRIORITY[s.kind], s.since), reverse=True)
    global last_sessions
    last_sessions = busy  # 每個有在動的對話，最需要你注意的排前面（點寵物時列出來）
    if not busy:
        return State("idle")
    best = busy[0]
    best.others = sum(1 for s in busy if s.kind in ("working", "waiting")) - (best.kind in ("working", "waiting"))
    return best


last_sessions: list[State] = []
