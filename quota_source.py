"""額度資料來源。

**只認官方數字，兩個來源、優先順序如下：**

1. `api`——直接問 Anthropic 的 `/api/oauth/usage`，用 Claude Code 存在
   `~/.claude/.credentials.json` 的 OAuth token。這是**即時**的，而且 `resets_at`
   是伺服器直接給的精確值，不必回推。
2. `desktop`——Claude 桌面版快取的 `plan-usage-history.json`。它每 5 分鐘才寫一次，
   所以最舊會落後 5 分鐘；token 過期或沒網路時的後備。

兩個都拿不到就回 None，由 UI 明講讀不到——不做任何用量推估，
寧可沒有數字也不要給假的。
"""

from __future__ import annotations

import glob
import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

WINDOW_HOURS = 5

CLAUDE_CODE_PROJECTS = os.path.expanduser("~/.claude/projects")
CREDENTIALS = os.path.expanduser("~/.claude/.credentials.json")

USAGE_API = "https://api.anthropic.com/api/oauth/usage"
API_TIMEOUT = 12
# 打 API 的節奏。畫面每 10 秒重畫是為了讓倒數跟得上，那不需要重打網路。
#
# 這個端點有速率限制，但沒有文件、429 給的 Retry-After 也是 0（無用）。
# 實測以 20 秒間隔連打 12 次，只有相隔 121 秒的兩次成功，中間全是 429
# ——看起來大約是「每 2 分鐘 1 次」，但沒有保證，而且隨時可能被 Anthropic 調整。
#
# 所以不寫死猜測值，改成自己收斂：成功就慢慢加快，被擋就退開。
# 這樣永遠貼著端點當下實際容許的最快節奏跑，不必依賴我對限制的猜測。
API_INTERVAL_MIN = 120      # 再快也沒意義，實測撐不住
API_INTERVAL_MAX = 600
API_INTERVAL_START = 150
API_MIN_INTERVAL = API_INTERVAL_START  # 外部參考用

_interval = float(API_INTERVAL_START)


def current_interval() -> int:
    """目前自我收斂到的查詢間隔（秒）。"""
    return int(_interval)

# 桌面版的資料目錄。Windows 從 Microsoft Store 安裝時是 MSIX 封裝，
# 資料會落在 Packages\<套件名>\LocalCache\Roaming\Claude，不是一般的 %APPDATA%\Claude。
_LOCAL = os.environ.get("LOCALAPPDATA", "")
_ROAM = os.environ.get("APPDATA", "")
DESKTOP_DIRS = [
    # MSIX（Microsoft Store 版）：用萬用字元涵蓋不同版本的套件資料夾
    *glob.glob(os.path.join(_LOCAL, "Packages", "Claude*", "LocalCache", "Roaming", "Claude")),
    *glob.glob(os.path.join(_LOCAL, "Packages", "*Claude*", "LocalCache", "Local", "Claude")),
    # 傳統安裝版
    os.path.join(_ROAM, "Claude"),
    os.path.join(_LOCAL, "Claude"),
    os.path.join(_ROAM, "AnthropicClaude"),
    os.path.expanduser("~/Library/Application Support/Claude"),  # macOS
]


@dataclass
class Quota:
    percent_used: float
    resets_at: Optional[datetime]
    week_used: Optional[float] = None  # 每週額度已用 %
    week_resets_at: Optional[datetime] = None
    sampled_at: Optional[datetime] = None
    resets_exact: bool = True  # False = 只知道不晚於這個時間（取樣有斷層）
    source: str = "desktop"     # 'api' = 即時官方值, 'desktop' = 桌面版 5 分鐘快取

    @property
    def live(self) -> bool:
        return self.source == "api"

    @property
    def age_min(self) -> float:
        if not self.sampled_at:
            return 0.0
        return (datetime.now(timezone.utc) - self.sampled_at).total_seconds() / 60

    @property
    def stale(self) -> bool:
        """資料有沒有舊到不該再相信。

        API 是即時的，只要拿得到就不算舊；桌面版快取則是它關掉之後就不再更新。
        """
        return False if self.live else self.age_min > 30

    @property
    def percent_left(self) -> float:
        return max(0.0, 100.0 - self.percent_used)

    @property
    def week_left(self) -> Optional[float]:
        return None if self.week_used is None else max(0.0, 100.0 - self.week_used)


# ──────────────────────────── 來源 1：即時用量 API ────────────────────────────
#
# Claude Code 登入後會把自己帳號的 OAuth token 存在 ~/.claude/.credentials.json，
# 拿它去 GET /api/oauth/usage 就能問到「這個帳號現在用了多少」。全程唯讀、
# 只碰使用者自己的帳號，也不會產生任何額度用量。

_api_lock = threading.Lock()
_api_cache: tuple[float, Optional[Quota]] = (0.0, None)
_api_muted_until = 0.0     # 被擋之後暫時別再打
_api_last_error = ""       # 最近一次失敗原因，給疑難排解看


def _oauth_token() -> Optional[str]:
    """讀 Claude Code 的 access token；過期或格式不符就回 None。"""
    try:
        with open(CREDENTIALS, encoding="utf-8-sig") as f:
            oauth = json.load(f).get("claudeAiOauth") or {}
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token:
        return None
    expires = oauth.get("expiresAt")
    # expiresAt 是毫秒。留 60 秒緩衝，快過期就當作沒有——不自己做 refresh，
    # 那是 Claude Code 的事，硬搶著換可能把它的登入狀態弄壞。
    if isinstance(expires, (int, float)) and time.time() * 1000 > expires - 60_000:
        return None
    return token


def _parse_window(node) -> tuple[Optional[float], Optional[datetime]]:
    if not isinstance(node, dict):
        return None, None
    used = node.get("utilization")
    used = float(used) if isinstance(used, (int, float)) else None
    when = None
    raw = node.get("resets_at")
    if isinstance(raw, str):
        try:
            when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            when = None
    return used, when


def _mute(seconds: float, why: str) -> None:
    global _api_muted_until, _api_last_error
    _api_muted_until = time.time() + seconds
    _api_last_error = f"{why}（暫停 {seconds / 60:.0f} 分鐘）"


def fetch_api_quota() -> Optional[Quota]:
    """打一次用量 API。會阻塞，請在背景執行緒呼叫。"""
    global _api_last_error, _interval

    if time.time() < _api_muted_until:
        return None
    token = _oauth_token()
    if not token:
        _api_last_error = "沒有可用的 OAuth token"
        return None

    req = urllib.request.Request(USAGE_API, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "ClaudeQuota/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            # 撞到限制：把常態間隔往外退，並等到那個間隔過去再試。
            # 伺服器給了有效的 Retry-After 就聽它的（實測都是 0，等於沒給）。
            _interval = min(_interval * 1.5, API_INTERVAL_MAX)
            retry = 0.0
            try:
                retry = float(e.headers.get("Retry-After") or 0)
            except (TypeError, ValueError):
                retry = 0.0
            _mute(max(retry, _interval), f"端點速率限制，間隔調為 {int(_interval)} 秒")
        elif e.code in (401, 403):
            _mute(900, f"憑證被拒（HTTP {e.code}）")
        else:
            _mute(300, f"HTTP {e.code}")
        return None
    except Exception as e:
        _api_last_error = f"{type(e).__name__}: {e}"
        return None

    if not isinstance(data, dict):
        _api_last_error = "回應格式不符"
        return None
    used, resets = _parse_window(data.get("five_hour"))
    if used is None:
        _api_last_error = "回應缺少 five_hour"
        return None
    week_used, week_resets = _parse_window(data.get("seven_day"))

    # 成功了就往回收一點，慢慢逼近端點當下容許的最快節奏
    _interval = max(_interval * 0.9, API_INTERVAL_MIN)
    _api_last_error = ""
    now = datetime.now(timezone.utc)
    return Quota(
        percent_used=max(0.0, min(100.0, used)),
        resets_at=resets,
        week_used=None if week_used is None else max(0.0, min(100.0, week_used)),
        week_resets_at=week_resets,
        sampled_at=now,
        resets_exact=True,   # 伺服器直接給的，不是回推的
        source="api",
    )


def refresh_api(force: bool = False) -> Optional[Quota]:
    """更新 API 快取。會阻塞網路，請在背景執行緒呼叫。"""
    global _api_cache
    with _api_lock:
        ts, cached = _api_cache
        if not force and cached is not None and time.time() - ts < _interval:
            return cached
    q = fetch_api_quota()
    if q is not None:
        with _api_lock:
            _api_cache = (time.time(), q)
    return q


def seconds_until_allowed() -> float:
    """還要等多久才可以再問一次 API。0 代表現在就可以。

    手動「立即重新整理」必須看這個——在限流窗口內硬打只會吃 429，
    反而把自我收斂的間隔往外推，越按越慢。
    """
    with _api_lock:
        ts = _api_cache[0]
    return max(0.0, _api_muted_until - time.time(), ts + _interval - time.time())


def api_status() -> str:
    """目前 API 這條路的狀態，給疑難排解與 UI 提示用。"""
    left = _api_muted_until - time.time()
    if left > 0:
        return f"{_api_last_error or '暫停中'} · 還有 {left:.0f} 秒"
    return _api_last_error or f"正常 · 每 {current_interval()} 秒更新"


def cached_api_quota(max_age: float = 300.0) -> Optional[Quota]:
    """取用已抓到的 API 結果，不碰網路。太舊就當作沒有。"""
    with _api_lock:
        ts, q = _api_cache
    return q if q is not None and time.time() - ts <= max_age else None


# ──────────────────────────── 來源 2：桌面版官方快取 ────────────────────────────

# 遞迴掃桌面版資料夾要 ~44ms（裡面有好幾 GB 的 VM bundle），而檔案位置幾乎不會變，
# 所以清單快取 5 分鐘。小工具想每 10 秒更新倒數也不必付這個代價。
FILE_SCAN_TTL = 300
_scan_cache: tuple[float, list[str]] = (0.0, [])


def _find_usage_files(force: bool = False) -> list[str]:
    """在桌面版資料目錄裡找額度快取檔（檔名可能隨版本改，所以用關鍵字搜）。"""
    global _scan_cache
    import time

    now = time.time()
    if not force and _scan_cache[1] and now - _scan_cache[0] < FILE_SCAN_TTL:
        if all(os.path.isfile(p) for p in _scan_cache[1]):
            return _scan_cache[1]

    found = []
    for base in DESKTOP_DIRS:
        if not base or not os.path.isdir(base):
            continue
        for path in glob.glob(os.path.join(base, "**", "*.json"), recursive=True):
            name = os.path.basename(path).lower()
            if any(k in name for k in ("plan-usage", "usage-history", "usage", "quota", "rate-limit")):
                found.append(path)
    # 新的排前面
    found = sorted(found, key=lambda p: os.path.getmtime(p), reverse=True)
    _scan_cache = (now, found)
    return found


def _dig(obj, *keys):
    """在巢狀結構裡找第一個符合 key 名稱的值（快取格式未知，容錯處理）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower().replace("_", "") in keys:
                return v
            r = _dig(v, *keys)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _dig(v, *keys)
            if r is not None:
                return r
    return None


# 一次下降超過這麼多百分點才算「視窗重置」；1～2 點的小降是四捨五入造成的
RESET_DROP = 5
# 前後兩筆取樣間隔在這個範圍內，才能把重置時刻定位得夠準（正常每 5 分鐘一筆）
SAMPLE_GAP_MIN = 15

# Anthropic 把視窗起點對齊到 10 分鐘邊界。兩處證據：
#   1. 429 回應裡的 resets_at（logs/claude.ai-web.log）例：2026-07-10T08:50:00Z
#   2. 桌面版記到的三次精確重置 19:50 / 03:20 / 00:40，區間內唯一的 10 分邊界
# 所以「首次用量的時刻」要向下取整到 10 分，才會等於真正的視窗起點。
BOUNDARY_MIN = 10


def _floor_boundary(t: datetime) -> datetime:
    return t.replace(minute=t.minute // BOUNDARY_MIN * BOUNDARY_MIN, second=0, microsecond=0)


def _code_window_reset() -> Optional[datetime]:
    """從 Claude Code 的對話紀錄推目前 5 小時視窗何時重置。

    視窗規則是「上個視窗過期後的第一則訊息開啟新視窗，之後 5 小時」，所以把所有
    計費事件依時間走一遍就能還原視窗邊界。

    注意這也是**上限**：視窗可能是網頁版或手機先開的，本機紀錄看不到那些，
    推出來的起點只會比真實起點晚 → 重置時間只會比真實的晚。
    （2026-07-25 實測：本機推 07:05，桌面版記到真正重置在 03:20。）
    """
    stamps: set[datetime] = set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=2)
    for path in glob.glob(os.path.join(CLAUDE_CODE_PROJECTS, "*", "*.jsonl")):
        try:
            if datetime.fromtimestamp(os.path.getmtime(path), timezone.utc) < cutoff:
                continue
            with open(path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not (d.get("message") or {}).get("usage") or not d.get("timestamp"):
                        continue
                    try:
                        stamps.add(datetime.fromisoformat(str(d["timestamp"]).replace("Z", "+00:00")))
                    except ValueError:
                        continue
        except OSError:
            continue

    if not stamps:
        return None
    window = timedelta(hours=WINDOW_HOURS)
    start = None
    for t in sorted(stamps):
        if start is None or t >= start + window:
            start = _floor_boundary(t)
    return None if start is None else start + window


def _window_reset_time(samples: list, latest_t: int) -> tuple[Optional[datetime], bool]:
    """回推目前這個 5 小時視窗何時重置。

    視窗是從「該視窗的第一次用量」起算 5 小時，不是從現在起算。桌面版每 5 分鐘
    存一筆，重置時 fh 會驟降（例：100 → 0），那一筆的時間就是新視窗的起點。

    回傳 (重置時間, 是否精確)。桌面版沒開的期間會有斷層，這時只能確定
    「起點不晚於這一筆」，也就是重置時間是個上限 → 第二個值為 False。
    """
    now = datetime.now(timezone.utc)
    start: Optional[datetime] = None
    exact = False

    # 由新往舊找最近一次驟降，那一筆即為目前視窗的起點
    for i in range(len(samples) - 1, 0, -1):
        cur, prev = samples[i], samples[i - 1]
        if cur["u"]["fh"] < prev["u"]["fh"] - RESET_DROP:
            # 重置發生在 prev 與 cur 之間；取整到 10 分邊界就是確切的視窗起點
            start = _floor_boundary(datetime.fromtimestamp(cur["t"] / 1000, timezone.utc))
            gap_min = (cur["t"] - prev["t"]) / 1000 / 60
            exact = gap_min <= SAMPLE_GAP_MIN
            break

    if start is None:
        # 整段歷史都沒重置過（剛裝、或紀錄很短）——只能用最舊的一筆當上限
        start = datetime.fromtimestamp(samples[0]["t"] / 1000, timezone.utc)
        exact = False

    # 起點若已超過 5 小時前，代表中間一定重置過但沒被記到；用滾動下限收斂
    floor = datetime.fromtimestamp(latest_t / 1000, timezone.utc) - timedelta(hours=WINDOW_HOURS)
    if start < floor:
        start, exact = floor, False
    if start < now - timedelta(hours=WINDOW_HOURS):
        return None, False

    resets = start + timedelta(hours=WINDOW_HOURS)
    if exact:
        return resets, True

    # 只有上限時，再拿本機紀錄推一個上限來收斂——兩個都是上限，取較早的那個
    code = _code_window_reset()
    if code and now < code < resets:
        resets = code
    return resets, False


def _parse_plan_usage_history(data: dict) -> Optional[dict]:
    """解析桌面版的 plan-usage-history.json。

    實際格式（version 2）：
        {"version": 2, "samples": [{"t": <毫秒>, "org": "...", "u": {"fh": 42, "sd": 42}}, ...]}
    其中 fh = 5 小時額度已用 %，sd = 每週額度已用 %，每 5 分鐘取樣一次。
    """
    raw = data.get("samples")
    if not isinstance(raw, list) or not raw:
        return None
    samples = sorted(
        (
            s for s in raw
            if isinstance(s, dict) and isinstance(s.get("u"), dict)
            and isinstance(s["u"].get("fh"), (int, float)) and isinstance(s.get("t"), (int, float))
        ),
        key=lambda s: s["t"],
    )
    if not samples:
        return None

    last = samples[-1]
    try:
        five_hour = float(last["u"]["fh"])
        week = float(last["u"]["sd"]) if "sd" in last["u"] else None
        sampled = datetime.fromtimestamp(last["t"] / 1000, timezone.utc)
    except (TypeError, ValueError, OSError):
        return None

    resets, exact = _window_reset_time(samples, last["t"])
    return {
        "used": five_hour,
        "week": week,
        "sampled": sampled,
        "resets": resets,
        "resets_exact": exact,
    }


# 解析結果快取。桌面版每 5 分鐘才寫一次檔，但小工具想更頻繁地更新倒數，
# 所以用 (mtime, size) 當鍵，檔案沒變就不重解析——重解析 274 筆要 ~47ms。
_parse_cache: dict[str, tuple[tuple[float, int], Optional[dict]]] = {}


def read_desktop_quota() -> Optional[Quota]:
    """讀桌面版快取的官方額度。找不到或格式不符就回 None——不做任何推估。"""
    for path in _find_usage_files():
        try:
            st = os.stat(path)
        except OSError:
            continue
        stamp = (st.st_mtime, st.st_size)

        cached = _parse_cache.get(path)
        if cached and cached[0] == stamp:
            parsed = cached[1]
            if parsed is None:
                continue
            return _to_quota(parsed)

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            _parse_cache[path] = (stamp, None)
            continue
        if not isinstance(data, dict):
            _parse_cache[path] = (stamp, None)
            continue

        parsed = _parse_plan_usage_history(data)
        if not parsed:
            # 格式改版時的後備：在巢狀結構裡撈百分比欄位。這仍然是官方數字，
            # 只是換了位置；撈不到就繼續找下一個檔案，絕不自行推算。
            pct = _dig(data, "percentused", "utilization", "percent", "usedpercent")
            left = _dig(data, "percentremaining", "remaining", "percentleft")
            if pct is None and left is not None:
                try:
                    pct = 100.0 - float(left)
                except (TypeError, ValueError):
                    pct = None
            if pct is None:
                _parse_cache[path] = (stamp, None)
                continue
            try:
                used = float(pct)
            except (TypeError, ValueError):
                _parse_cache[path] = (stamp, None)
                continue
            parsed = {
                "used": used * 100 if used <= 1.0 else used,
                "week": None,
                "sampled": datetime.fromtimestamp(st.st_mtime, timezone.utc),
                "resets": None,
                "resets_exact": False,
            }

        _parse_cache[path] = (stamp, parsed)
        return _to_quota(parsed)
    return None


def _to_quota(parsed: dict) -> Quota:
    week = parsed["week"]
    return Quota(
        percent_used=max(0.0, min(100.0, parsed["used"])),
        resets_at=parsed["resets"],
        week_used=None if week is None else max(0.0, min(100.0, week)),
        sampled_at=parsed["sampled"],
        resets_exact=parsed["resets_exact"],
    )


def get_quota() -> Optional[Quota]:
    """對外唯一入口，不碰網路。

    優先用已抓到的即時 API 結果；沒有就退回桌面版快取。兩個都沒有就 None，
    交給 UI 明說讀不到——絕不推估。網路更新請由外面開執行緒呼叫 refresh_api()。
    """
    return cached_api_quota() or read_desktop_quota()


if __name__ == "__main__":
    import io
    import sys

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    refresh_api(force=True)  # 自測時直接同步打一次
    q = get_quota()
    if q is None:
        print("讀不到額度資料。")
        print(f"  OAuth token：{'有' if _oauth_token() else '沒有或已過期'}（{CREDENTIALS}）")
        print(f"  桌面版快取：{_find_usage_files() or '無'}")
        sys.exit(1)

    print(f"來源：{'即時 API' if q.live else '桌面版快取（最多落後 5 分鐘）'}")
    print(f"5 小時額度：已用 {q.percent_used:.0f}%   剩餘 {q.percent_left:.0f}%")
    if q.week_used is not None:
        print(f"每週額度：已用 {q.week_used:.0f}%   剩餘 {q.week_left:.0f}%")
    if q.resets_at:
        print(f"重置：{'' if q.resets_exact else '不晚於 '}{q.resets_at.astimezone():%m-%d %H:%M}")
    else:
        print("重置：未知")
    if q.week_resets_at:
        print(f"每週重置：{q.week_resets_at.astimezone():%m-%d %H:%M}")
    if not q.live:
        print(f"取樣：{q.age_min:.0f} 分鐘前{'（已過期）' if q.stale else ''}")
        print(f"API 狀態：{api_status()}")
