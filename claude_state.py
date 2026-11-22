"""Claude Code 現在在做什麼：工作中、在等你、做完了、出錯了，或沒事。

給桌面寵物用。資料來源有兩個：

1. 對話紀錄（預設，免設定）：~/.claude/projects/<專案>/<對話>.jsonl。
   CLI、VS Code 擴充功能和桌面版的 Code 分頁都會寫這裡，所以三種都抓得到。
   只讀最近幾分鐘有動過的檔案的結尾，不讀內容文字，只看每一筆的種類和時間。
   限制：「在等你按允許」紀錄裡看不出來，只能用「工具呼叫送出後很久沒結果」來猜，
   跑很久的指令也會被猜成在等你。
2. Claude Code 的 Notification hook（選用）：需要你允許時 Claude Code 會呼叫 hook，
   hook 把通知內容寫到一個檔案。有裝的話「在等你」就用它，不再用猜的。
"""
from __future__ import annotations

import json
import os
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

# 會跳出允許提示的工具。Read / Grep 這類唯讀工具預設不用問，跑久了也不是在等你。
ASKS_PERMISSION = ("Bash", "PowerShell", "Edit", "MultiEdit", "Write", "NotebookEdit", "WebFetch", "mcp__")

# 狀態的優先順序：同時有好幾個對話時，顯示最需要你注意的那個
PRIORITY = {"waiting": 4, "error": 3, "working": 2, "done": 1, "idle": 0}


@dataclass
class State:
    kind: str                     # waiting / error / working / done / idle
    project: str = ""             # 對話所在資料夾的名字
    since: float = 0.0            # 這個狀態從什麼時候開始（epoch 秒）
    guessed: bool = False         # waiting 是猜的（沒裝 hook）
    others: int = 0               # 另外還有幾個對話在跑
    key: str = ""                 # 哪個對話（紀錄檔路徑），用來分辨同一個對話的同一次完成


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
                states.append(s)
    # 不再活躍的檔案不用留在快取裡
    for path in [p for p in _cache if not os.path.exists(p)]:
        del _cache[path]
    busy = sorted((s for s in states if s.kind != "idle"), key=lambda s: (PRIORITY[s.kind], s.since), reverse=True)
    global last_sessions
    last_sessions = busy  # 每個有在動的對話，最需要你注意的排前面（點寵物時列出來）
    if not busy:
        return State("idle")
    best = busy[0]
    best.others = sum(1 for s in busy if s.kind in ("working", "waiting")) - (best.kind in ("working", "waiting"))
    return best


last_sessions: list[State] = []
