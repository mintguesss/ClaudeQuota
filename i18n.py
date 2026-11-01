"""介面文字（繁中／英文）。

語言預設跟 Windows 的顯示語言走：中文介面顯示中文，其他一律英文。
系統列選單可以手動切換，選擇存在 widget_state.json 的 "lang"（auto / zh / en）。
"""

from __future__ import annotations

import ctypes

# 每個字串都要有 zh 與 en 兩個版本；少一個就是 bug，測試會抓。
STRINGS: dict[str, dict[str, str]] = {
    # ── 卡片 ──
    "card.stale": {"zh": "· 資料已過期", "en": "· data is stale"},
    "card.no_data": {"zh": "讀不到額度資料", "en": "No usage data"},
    "card.no_data_hint": {"zh": "請開啟 Claude 桌面版並登入一次",
                          "en": "Open Claude Code or the Claude desktop app once"},
    "card.no_data_foot": {"zh": "沒有官方數字就不顯示數字，不做推估",
                          "en": "No official number, so nothing is shown. No guessing."},
    "card.five_hour": {"zh": "5 小時額度", "en": "5-hour limit"},
    "card.weekly": {"zh": "每週額度", "en": "Weekly limit"},
    "card.resets": {"zh": "{left} · {approx}{time} 重置", "en": "{left} · resets {approx}{time}"},
    "card.approx": {"zh": "約 ", "en": "by "},
    "card.not_started": {"zh": "視窗還沒開始計時 · 按上方閃電鍵可立刻起算",
                         "en": "No window running · the bolt button starts one now"},
    "card.live": {"zh": "即時官方數字", "en": "Live official numbers"},
    "card.updated_ago": {"zh": " · {sec} 秒前更新", "en": " · updated {sec}s ago"},
    "card.desktop_now": {"zh": "剛剛更新", "en": "just now"},
    "card.desktop_ago": {"zh": "{min} 分鐘前取樣", "en": "sampled {min} min ago"},
    "card.desktop_foot": {"zh": "桌面版快取 · {age} · {status}", "en": "Desktop cache · {age} · {status}"},
    "hint.close": {"zh": "收起來 · 點系統列圖示可再開啟", "en": "Hide · click the tray icon to bring it back"},
    "hint.mini": {"zh": "縮成橫條 · 點橫條可再展開", "en": "Shrink to a bar · click the bar to expand"},
    "hint.refresh": {"zh": "重新讀取官方數字（受端點限流，約每 2 分鐘一次）",
                     "en": "Re-read the official numbers (rate-limited, about every 2 min)"},
    "hint.poke": {"zh": "打一次最小呼叫，讓 5 小時視窗從現在起算",
                  "en": "Send one tiny request so the 5-hour window starts now"},

    # ── 剩餘時間 ──
    "left.reset": {"zh": "已重置", "en": "reset"},
    "left.hm": {"zh": "剩 {h} 小時 {m} 分", "en": "{h}h {m}m left"},
    "left.h": {"zh": "剩 {h} 小時", "en": "{h}h left"},
    "left.m": {"zh": "剩 {m} 分", "en": "{m}m left"},

    # ── 迷你橫條 ──
    "mini.week": {"zh": "週", "en": "wk"},

    # ── 開啟 5 小時視窗 ──
    "poke.no_cli": {"zh": "找不到 claude CLI", "en": "claude CLI not found"},
    "poke.timeout": {"zh": "呼叫逾時", "en": "timed out"},
    "poke.start_failed": {"zh": "啟動失敗：{e}", "en": "could not start: {e}"},
    "poke.failed": {"zh": "呼叫失敗", "en": "request failed"},
    "poke.ok": {"zh": "已開啟視窗", "en": "Window started"},
    "poke.running_auto": {"zh": "自動開啟 5 小時視窗…", "en": "Starting the 5-hour window…"},
    "poke.running": {"zh": "正在開啟 5 小時視窗…", "en": "Starting the 5-hour window…"},
    "poke.fail_prefix": {"zh": "失敗 · {msg}", "en": "Failed · {msg}"},
    "note.rate_limited": {"zh": "官方端點限流中 · {sec} 秒後才能再問一次",
                          "en": "Rate-limited · next check possible in {sec}s"},
    "note.auto_poke": {"zh": "視窗重置後自動起算：{state}", "en": "Auto-start after reset: {state}"},
    "note.auto_poke_on": {"zh": "已開啟自動起算 · 每次約花 5 小時額度的 1–2%",
                          "en": "Auto-start on · each start uses about 1–2% of the 5-hour limit"},
    "note.token_refresh": {"zh": "自動換發過期 token：{state}", "en": "Refresh expired token: {state}"},
    "note.token_refresh_on": {"zh": "已開啟換發 · Claude Code 執行中時可能要你重新登入",
                              "en": "Token refresh on · a running Claude Code may ask you to sign in"},
    "on": {"zh": "開", "en": "on"},
    "off": {"zh": "關", "en": "off"},

    # ── 系統列 ──
    "tray.title": {"zh": "Claude 用量", "en": "Claude usage"},
    "tray.no_data": {"zh": "Claude 用量：讀不到資料", "en": "Claude usage: no data"},
    "tray.used": {"zh": "Claude 已用 {pct}%{week}{stale}", "en": "Claude {pct}% used{week}{stale}"},
    "tray.week": {"zh": "／每週已用 {pct}%", "en": " / week {pct}%"},
    "tray.stale": {"zh": "（資料已過期）", "en": " (stale)"},
    "style.burst": {"zh": "星芒（狀態色底）", "en": "Burst (status color)"},
    "style.number": {"zh": "數字（已用 %）", "en": "Number (% used)"},
    "style.left": {"zh": "數字（剩餘 %）", "en": "Number (% left)"},
    "style.ring": {"zh": "進度環", "en": "Ring"},
    "style.pie": {"zh": "圓餅", "en": "Pie"},
    "style.dual": {"zh": "雙條（5 小時＋每週）", "en": "Two bars (5-hour + weekly)"},
    "menu.show": {"zh": "顯示／隱藏", "en": "Show / hide"},
    "menu.mini": {"zh": "縮成橫條", "en": "Shrink to bar"},
    "menu.refresh": {"zh": "立即重新整理", "en": "Refresh now"},
    "menu.poke": {"zh": "開啟 5 小時視窗", "en": "Start 5-hour window"},
    "menu.auto_poke": {"zh": "重置後自動起算", "en": "Auto-start after reset"},
    "menu.token_refresh": {"zh": "自動換發過期 token（進階）", "en": "Refresh expired token (advanced)"},
    "menu.home": {"zh": "移回畫面內", "en": "Move back on screen"},
    "menu.style": {"zh": "圖示樣式", "en": "Icon style"},
    "menu.lang": {"zh": "語言 Language", "en": "Language 語言"},
    "lang.auto": {"zh": "跟隨系統", "en": "Follow system"},
    "menu.quit": {"zh": "結束 ClaudeQuota", "en": "Quit ClaudeQuota"},
    "menu.help": {"zh": "使用說明", "en": "Help"},

    # ── 開啟前確認 ──
    "confirm.yes_hint": {"zh": "要開啟嗎？", "en": "Turn it on?"},
    "confirm.auto_poke.title": {"zh": "重置後自動起算", "en": "Auto-start after reset"},
    "confirm.auto_poke.body": {
        "zh": (
            "沒有進行中的 5 小時視窗時，自動執行一次 `claude -p hi`，讓新視窗立刻開始計時。\n\n"
            "好處\n"
            "• 上一個視窗一結束就開始計時，一天內的重置時間會提早\n"
            "• 不用記得手動按閃電鍵\n\n"
            "代價\n"
            "• 每次約花 5 小時額度的 1–2%（實測一次約 3 萬 token），即使你那段時間本來不打算用\n"
            "• 人不在電腦前時開的視窗，可能還沒用就過期了\n\n"
            "隨時可以在右鍵選單關掉。"
        ),
        "en": (
            "When no 5-hour window is running, run `claude -p hi` once so a new window starts right away.\n\n"
            "Pros\n"
            "• The clock starts as soon as the previous window ends, so it resets earlier in your day\n"
            "• No need to remember the bolt button\n\n"
            "Cons\n"
            "• Each start uses about 1–2% of the 5-hour limit (about 30k tokens in our test), "
            "even if you were not going to use Claude\n"
            "• A window started while you are away may expire unused\n\n"
            "You can turn it off again from the tray menu."
        ),
    },
    "confirm.token.title": {"zh": "自動換發過期 token（進階）", "en": "Refresh expired token (advanced)"},
    "confirm.token.body": {
        "zh": (
            "Claude Code 的登入 token 過期時（例如很久沒開 CLI、只用桌面版），"
            "由 ClaudeQuota 自己換一個新的，並寫回 ~/.claude/.credentials.json。\n\n"
            "好處\n"
            "• Claude Code 很久沒開也能看到即時數字\n"
            "• 不開的話只能用桌面版快取，最多落後 5 分鐘\n\n"
            "代價\n"
            "• 換發會讓舊的 token 失效。若當下 Claude Code 正在執行，它可能會要你重新登入\n"
            "• 借用的是 Claude Code 自己的登入介面，沒有公開文件，隨時可能失效\n\n"
            "不開也沒關係：token 過期時打開一次 Claude Code 就會恢復。"
        ),
        "en": (
            "When Claude Code's sign-in token has expired (for example you have not opened the CLI for a while "
            "and only use the desktop app), ClaudeQuota gets a new one itself and saves it to "
            "~/.claude/.credentials.json.\n\n"
            "Pros\n"
            "• Live numbers keep working even if Claude Code has not run for a while\n"
            "• Without it you fall back to the desktop cache, up to 5 minutes old\n\n"
            "Cons\n"
            "• Refreshing invalidates the old token. If Claude Code is running at that moment, "
            "it may ask you to sign in again\n"
            "• It borrows Claude Code's own sign-in interface, which is undocumented and may stop working\n\n"
            "Leaving it off is fine: when the token expires, opening Claude Code once fixes it."
        ),
    },

    # ── 使用說明視窗 ──
    "help.title": {"zh": "ClaudeQuota 使用說明", "en": "ClaudeQuota help"},
    "help.close": {"zh": "關閉", "en": "Close"},
    "help.subtitle": {"zh": "使用說明", "en": "How it works"},
    "help.body": {
        "zh": (
            "ClaudeQuota 顯示 Claude 訂閱的兩個額度：\n"
            "• 5 小時額度：從你第一次使用起算 5 小時，時間到就重置\n"
            "• 每週額度：每週重置一次\n"
            "數字都是官方的，取不到就顯示「讀不到額度資料」，不會自己估。\n\n"
            "■ 系統列圖示\n"
            "• 圖示顯示 5 小時額度的用量，有 6 種樣式，可在右鍵選單的「圖示樣式」切換\n"
            "• 左鍵：顯示或收起卡片\n"
            "• 右鍵：打開選單\n\n"
            "■ 卡片右上角的按鈕\n"
            "• ⚡ 閃電：立刻開始一個 5 小時視窗（會送出一個很小的 Claude Code 請求）\n"
            "• ↻ 重新整理：重新讀官方數字。端點有限流，大約 2 分鐘才能問一次\n"
            "• ─ 縮小：變成工作列旁的細橫條，點橫條可再展開\n"
            "• ✕ 收起：只是藏起來，程式還在系統列執行\n"
            "卡片和橫條都可以用滑鼠拖到任何位置。\n\n"
            "■ 右鍵選單\n"
            "• 顯示／隱藏、縮成橫條：同上\n"
            "• 立即重新整理、開啟 5 小時視窗：同卡片上的 ↻ 和 ⚡\n"
            "• 重置後自動起算：視窗結束後自動開下一個（每次約花 1–2%，預設關閉，開啟前會說明）\n"
            "• 自動換發過期 token：token 過期時自己換新的（有登出風險，預設關閉，開啟前會說明）\n"
            "• 移回畫面內：卡片跑到螢幕外找不到時用\n"
            "• 圖示樣式、語言：外觀設定\n\n"
            "■ 卡片底部的小字\n"
            "• 即時官方數字 · N 秒前更新：直接向官方查到的\n"
            "• 桌面版快取：查不到時改讀 Claude 桌面版存的紀錄，最多落後 5 分鐘\n"
            "• 資料已過期：快取太舊（超過 30 分鐘），數字可能不準\n\n"
            "■ 常見狀況\n"
            "• 讀不到額度資料：打開 Claude Code 登入一次，或開啟 Claude 桌面版\n"
            "• 限流中，N 秒後才能再問：正常現象，等它倒數完會自己更新\n"
            "• 全螢幕遊戲或影片時卡片不見：會自動讓開，結束後自己回來\n\n"
            "■ 隱私\n"
            "只連 Anthropic 官方端點，查的是你自己帳號的用量。不上傳對話、token 或任何資料，沒有遙測。"
        ),
        "en": (
            "ClaudeQuota shows the two limits of your Claude subscription:\n"
            "• 5-hour limit: counts from your first use, resets 5 hours later\n"
            "• Weekly limit: resets once a week\n"
            "Numbers are official. If none are available it says \"No usage data\" instead of guessing.\n\n"
            "■ Tray icon\n"
            "• The icon shows your 5-hour usage; pick one of 6 styles under Icon style in the menu\n"
            "• Left-click: show or hide the card\n"
            "• Right-click: open the menu\n\n"
            "■ Card buttons (top right)\n"
            "• ⚡ Bolt: start a 5-hour window now (sends one tiny Claude Code request)\n"
            "• ↻ Refresh: re-read the official numbers. The endpoint is rate-limited to about once every 2 minutes\n"
            "• ─ Shrink: turn into a thin bar next to the taskbar; click the bar to expand\n"
            "• ✕ Hide: only hides the card; the app keeps running in the tray\n"
            "Drag the card or the bar anywhere.\n\n"
            "■ Tray menu\n"
            "• Show / hide, Shrink to bar: as above\n"
            "• Refresh now, Start 5-hour window: same as ↻ and ⚡ on the card\n"
            "• Auto-start after reset: start the next window automatically (about 1–2% each time; "
            "off by default, explained before turning on)\n"
            "• Refresh expired token: renew an expired token itself (may sign Claude Code out; off by default, "
            "explained before turning on)\n"
            "• Move back on screen: if the card ended up off-screen\n"
            "• Icon style, Language: appearance\n\n"
            "■ The small text at the bottom of the card\n"
            "• Live official numbers · updated Ns ago: fetched from the official endpoint\n"
            "• Desktop cache: the Claude desktop app's saved data, up to 5 minutes old\n"
            "• data is stale: the cache is over 30 minutes old and may be wrong\n\n"
            "■ Common situations\n"
            "• No usage data: sign in to Claude Code once, or open the Claude desktop app\n"
            "• Rate-limited, next check in Ns: normal; it updates by itself when the countdown ends\n"
            "• Card disappears during a full-screen game or video: it steps aside and comes back afterwards\n\n"
            "■ Privacy\n"
            "Only talks to Anthropic's official endpoints, about your own account's usage. "
            "No conversations, tokens or other data are uploaded. No telemetry."
        ),
    },

    # ── API 狀態（quota_source） ──
    "api.login_expired": {"zh": "登入已過期，請重新執行 claude 登入",
                          "en": "Login expired, sign in to claude again"},
    "api.refresh_failed": {"zh": "token 換發失敗：{e}", "en": "token refresh failed: {e}"},
    "api.write_failed": {"zh": "新 token 寫回失敗：{e}", "en": "could not save new token: {e}"},
    "api.paused": {"zh": "{why}（暫停 {min} 分鐘）", "en": "{why} (paused {min} min)"},
    "api.no_token": {"zh": "沒有可用的 OAuth token", "en": "no usable OAuth token"},
    "api.token_expired": {"zh": "token 已過期，開一次 Claude Code 即可",
                          "en": "token expired, open Claude Code once"},
    "api.rate_limited": {"zh": "端點速率限制，間隔調為 {sec} 秒",
                         "en": "rate-limited, interval now {sec}s"},
    "api.rejected": {"zh": "憑證被拒（HTTP {code}）", "en": "credentials rejected (HTTP {code})"},
    "api.bad_response": {"zh": "回應格式不符", "en": "unexpected response"},
    "api.no_five_hour": {"zh": "回應缺少 five_hour", "en": "response has no five_hour"},
    "api.status_paused": {"zh": "{err} · 還有 {sec} 秒", "en": "{err} · {sec}s left"},
    "api.paused_default": {"zh": "暫停中", "en": "paused"},
    "api.status_ok": {"zh": "正常 · 每 {sec} 秒更新", "en": "OK · every {sec}s"},
}

_override = "auto"


def system_lang() -> str:
    """Windows 顯示語言是中文就回 zh，其他（含非 Windows）回 en。"""
    try:
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except (AttributeError, OSError):
        return "en"
    return "zh" if lang_id & 0x3FF == 0x04 else "en"  # 0x04 = LANG_CHINESE（不分繁簡）


def set_lang(choice: str) -> None:
    global _override
    _override = choice if choice in ("auto", "zh", "en") else "auto"


def choice() -> str:
    return _override


def lang() -> str:
    return system_lang() if _override == "auto" else _override


def t(key: str, **kw) -> str:
    entry = STRINGS[key]
    text = entry.get(lang()) or entry["en"]
    return text.format(**kw) if kw else text
