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
    "left.dh": {"zh": "剩 {d} 天 {h} 小時", "en": "{d}d {h}h left"},
    "left.d": {"zh": "剩 {d} 天", "en": "{d}d left"},
    "card.week_resets": {"zh": "{left} · {time} 重置", "en": "{left} · resets {time}"},
    # ── 桌面寵物 ──
    "menu.pet": {"zh": "桌面寵物", "en": "Desktop pet"},
    "pet.show": {"zh": "顯示寵物", "en": "Show pet"},
    "pet.character.slime": {"zh": "史萊姆", "en": "Slime"},
    "pet.character.cat": {"zh": "橘貓", "en": "Ginger cat"},
    "pet.character.ghost": {"zh": "小幽靈", "en": "Little ghost"},
    "pet.character.chick": {"zh": "小雞", "en": "Chick"},
    "pet.character.penguin": {"zh": "企鵝", "en": "Penguin"},
    "pet.character.robot": {"zh": "機器人", "en": "Robot"},
    "pet.character.bunny": {"zh": "兔子", "en": "Bunny"},
    "pet.character.frog": {"zh": "青蛙", "en": "Frog"},
    "pet.character.bear": {"zh": "小熊", "en": "Bear"},
    "pet.music": {"zh": "跳舞時播音樂", "en": "Play music while dancing"},
    "menu.videos": {"zh": "等 Claude 時看短影片", "en": "Watch videos while Claude works"},
    "videos.add": {"zh": "新增常用網站或 app…", "en": "Add a site or app…"},
    "videos.remove": {"zh": "移除我加的", "en": "Remove one I added"},
    "videos.add_title": {"zh": "新增常用網站或 app", "en": "Add a site or app"},
    "videos.add_name": {"zh": "名稱（顯示在選單上）", "en": "Name (shown in the menu)"},
    "videos.add_url": {"zh": "網址，或 app 連結（例如 spotify:）", "en": "Web address, or an app link (e.g. spotify:)"},
    "videos.add_ok": {"zh": "加入", "en": "Add"},
    "videos.added": {"zh": "加好了：{name}", "en": "Added: {name}"},
    "pet.say.videos": {"zh": "去看吧！Claude 需要你的時候我會發通知叫你",
                       "en": "Go ahead! I'll send a notification when Claude needs you"},
    "pet.menu.state": {"zh": "改變狀態", "en": "Change state"},
    "pet.state.auto": {"zh": "自動（跟著 Claude）", "en": "Automatic (follow Claude)"},
    "pet.state.working": {"zh": "工作中", "en": "Working"},
    "pet.state.waiting": {"zh": "在等你", "en": "Waiting for you"},
    "pet.state.done": {"zh": "做完了", "en": "Done"},
    "pet.state.error": {"zh": "出錯了", "en": "Error"},
    "pet.state.idle": {"zh": "睡覺", "en": "Asleep"},
    "pet.state.awake": {"zh": "醒著（自訂角色會跳舞）", "en": "Awake (custom characters dance)"},
    "pet.hide": {"zh": "隱藏寵物", "en": "Hide the pet"},
    "pet.menu.size": {"zh": "大小", "en": "Size"},
    "pet.size.1.0": {"zh": "小", "en": "Small"},
    "pet.size.1.5": {"zh": "中（1.5 倍）", "en": "Medium (1.5x)"},
    "pet.size.2.0": {"zh": "大（2 倍）", "en": "Large (2x)"},
    "pet.size.3.0": {"zh": "特大（3 倍）", "en": "Extra large (3x)"},
    "menu.theme": {"zh": "主題", "en": "Theme"},
    "theme.paper": {"zh": "遊戲紙（預設）", "en": "Game paper (default)"},
    "theme.night": {"zh": "夜晚", "en": "Night"},
    "theme.sakura": {"zh": "櫻花", "en": "Sakura"},
    "theme.mint": {"zh": "薄荷", "en": "Mint"},
    "theme.changed": {"zh": "換好主題了！", "en": "New look!"},
    "menu.look": {"zh": "外觀與語言", "en": "Look & language"},
    "menu.advanced": {"zh": "進階設定", "en": "Advanced"},
    "pet.menu.character": {"zh": "角色", "en": "Character"},
    "pet.menu.color": {"zh": "顏色", "en": "Color"},
    "pet.menu.placement": {"zh": "位置", "en": "Where it lives"},
    "pet.color.default": {"zh": "角色原本的顏色", "en": "The character's own color"},
    "pet.color.pink": {"zh": "粉紅", "en": "Pink"},
    "pet.color.blue": {"zh": "天藍", "en": "Blue"},
    "pet.color.purple": {"zh": "薰衣草紫", "en": "Lavender"},
    "pet.color.yellow": {"zh": "奶油黃", "en": "Butter yellow"},
    "pet.color.mint": {"zh": "薄荷綠", "en": "Mint"},
    "pet.color.gray": {"zh": "灰色", "en": "Gray"},
    "pet.wake": {"zh": "叫醒（陪你一下）", "en": "Wake up (hang out for a bit)"},
    "pet.custom_open": {"zh": "自訂角色…（打開資料夾）", "en": "Custom character… (open folder)"},
    "pet.say.wake": {"zh": "醒來囉！陪你一下～", "en": "I'm up! I'll hang out with you for a bit"},
    "pet.say.awake": {"zh": "醒著！目前沒有進行中的工作", "en": "Awake! Nothing's running right now"},
    "pet.say.hello": {"zh": "嗨，我是{name}！", "en": "Hi, I'm {name}!"},
    "pet.say.custom": {"zh": "在資料夾裡開一個子資料夾、放進你的圖，就能從「角色」選單選它。命名方式看 README.txt",
                       "en": "Make a subfolder there with your images, then pick it under Character. README.txt explains the file names"},
    "pet.placement.mini": {"zh": "坐在迷你橫條上", "en": "Sit on the mini bar"},
    "pet.placement.walk": {"zh": "在工作列上方散步", "en": "Walk along the top of the taskbar"},
    "pet.placement.inbar": {"zh": "在工作列裡散步（縮小）", "en": "Walk inside the taskbar (smaller)"},
    "pet.placement.free": {"zh": "自由擺放（可拖曳）", "en": "Anywhere (drag it)"},
    "pet.hook": {"zh": "寵物：準確偵測「在等你允許」（安裝 hook）", "en": "Pet: detect permission prompts exactly (install hook)"},
    "pet.hook_on": {"zh": "已安裝 hook。Claude 需要你允許時我會馬上知道！",
                    "en": "Hook installed. I'll know right away when Claude needs your permission!"},
    "pet.hook_off": {"zh": "已移除 hook，改回用猜的。", "en": "Hook removed. Back to guessing."},
    "pet.hook_failed": {"zh": "改不了 Claude Code 設定：{e}", "en": "Could not change Claude Code settings: {e}"},
    "pet.say.working": {"zh": "{who} 工作中", "en": "{who} is working"},
    "pet.say.for": {"zh": "，已經 {min} 分鐘", "en": " ({min} min so far)"},
    "pet.say.waiting": {"zh": "{who} 在等你按允許！", "en": "{who} needs your permission!"},
    "pet.say.waiting_guess": {"zh": "{who} 好像在等你按允許？", "en": "{who} may be waiting for your permission?"},
    "pet.say.done": {"zh": "{who} 做完了！", "en": "{who} is done!"},
    "pet.say.error": {"zh": "{who} 出錯了…", "en": "{who} hit an error…"},
    "pet.say.idle": {"zh": "沒有進行中的工作，睡個覺", "en": "Nothing running. Nap time"},
    "pet.say.others": {"zh": "（另外還有 {n} 個對話在跑）", "en": " (+{n} more running)"},
    "confirm.pet_hook.title": {"zh": "安裝 Claude Code hook？", "en": "Install a Claude Code hook?"},
    "confirm.pet_hook.body": {
        "zh": "對話紀錄看不出 Claude 是不是卡在「允許／拒絕」提示，所以寵物只能用猜的"
              "（工具送出後 20 秒沒結果就當作在等你，跑很久的指令也會被猜錯）。\n\n"
              "安裝後會在 ~/.claude/settings.json 加一個 Notification hook："
              "Claude Code 需要你允許時，會跑一支小 PowerShell 腳本把通知存成檔案給寵物讀。\n\n"
              "• 好處：「在等你」變準確，馬上就知道\n"
              "• 代價：每次通知多跑一下 PowerShell（約半秒，不影響 Claude）；"
              "會改到你的 Claude Code 設定（第一次改之前會留 settings.json.claudequota.bak 備份）\n"
              "• 隨時可以在同一個選項移除，只會拿掉這一條，其他設定不動",
        "en": "The transcripts don't show whether Claude is stuck on an allow/deny prompt, so the pet has to guess "
              "(a tool call with no result after 20 seconds counts as waiting; long-running commands fool it).\n\n"
              "This adds a Notification hook to ~/.claude/settings.json: when Claude Code needs your permission it "
              "runs a tiny PowerShell script that saves the notification to a file for the pet.\n\n"
              "• Upside: \"waiting for you\" becomes exact and instant\n"
              "• Cost: PowerShell runs for about half a second per notification (Claude isn't slowed); your Claude "
              "Code settings are changed (a settings.json.claudequota.bak backup is kept before the first change)\n"
              "• Remove it any time from the same menu item; only this one entry is taken out",
    },
    "pace.plenty": {"zh": "照這個進度，重置時還會剩不少", "en": "At this pace you'll have plenty left at reset"},
    "pace.on_track": {"zh": "照這個進度，剛好夠用到重置", "en": "At this pace you'll just about last until reset"},
    "pace.short": {"zh": "照這個進度，約 {time} 會用完", "en": "At this pace you'll run out around {time}"},
    "tray.pace_short": {"zh": "（約 {time} 用完）", "en": " (out ~{time})"},
    "weekdays": {"zh": "週一,週二,週三,週四,週五,週六,週日", "en": "Mon,Tue,Wed,Thu,Fri,Sat,Sun"},
    "weekday_time": {"zh": "{day} {time}", "en": "{day} {time}"},

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
    "poke.no_effect": {"zh": "自動起算沒讓視窗開始，已暫停 · 請確認 Claude Code 登入的是訂閱帳號",
                       "en": "Auto-start didn't start a window, paused · check Claude Code uses your subscription"},
    "note.rate_limited": {"zh": "官方端點限流中 · {sec} 秒後才能再問一次",
                          "en": "Rate-limited · next check possible in {sec}s"},
    "note.auto_poke": {"zh": "視窗重置後自動起算：{state}", "en": "Auto-start after reset: {state}"},
    "note.auto_poke_on": {"zh": "已開啟自動起算 · 每次花不到 5 小時額度的 1%",
                          "en": "Auto-start on · each start uses under 1% of the 5-hour limit"},
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
    "confirm.enable": {"zh": "開啟", "en": "Turn on"},
    "confirm.cancel": {"zh": "先不要", "en": "Not now"},
    "confirm.auto_poke.title": {"zh": "重置後自動起算", "en": "Auto-start after reset"},
    "confirm.auto_poke.body": {
        "zh": (
            "沒有進行中的 5 小時視窗時，自動執行一次 `claude -p hi`，讓新視窗立刻開始計時。\n\n"
            "好處\n"
            "• 上一個視窗一結束就開始計時，一天內的重置時間會提早\n"
            "• 不用記得手動按閃電鍵\n\n"
            "代價\n"
            "• 每次花不到 5 小時額度的 1%（用最小的 Haiku 模型），即使你那段時間本來不打算用\n"
            "• 人不在電腦前時開的視窗，可能還沒用就過期了\n\n"
            "隨時可以在右鍵選單關掉。"
        ),
        "en": (
            "When no 5-hour window is running, run `claude -p hi` once so a new window starts right away.\n\n"
            "Pros\n"
            "• The clock starts as soon as the previous window ends, so it resets earlier in your day\n"
            "• No need to remember the bolt button\n\n"
            "Cons\n"
            "• Each start uses under 1% of the 5-hour limit (it runs on the small Haiku model), "
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
            "• 每週額度：每週重置一次。下面會照這週的平均速度提示重置前夠不夠用，"
            "不夠時用紅字標出大約何時用完（週期開始半天內不提示）\n"
            "數字都是官方的，取不到就顯示「讀不到額度資料」，不會自己估。\n"
            "查的是 Claude Code（含 VS Code 擴充功能）登入的帳號；桌面版登入別的帳號不影響。\n\n"
            "■ 系統列圖示\n"
            "• 圖示顯示 5 小時額度的用量，有 6 種樣式，在右鍵選單「外觀與語言 → 圖示樣式」切換\n"
            "• 左鍵：顯示或收起卡片\n"
            "• 右鍵：打開選單\n\n"
            "■ 卡片右上角的按鈕\n"
            "• ⚡ 閃電：立刻開始一個 5 小時視窗（會送出一個很小的 Claude Code 請求）\n"
            "• ↻ 重新整理：重新讀官方數字。端點有限流，大約 2 分鐘才能問一次\n"
            "• ─ 縮小：變成工作列旁的細橫條，點橫條可再展開\n"
            "• ✕ 收起：只是藏起來，程式還在系統列執行\n"
            "卡片和橫條都可以用滑鼠拖到任何位置。\n\n"
            "■ 右鍵選單\n"
            "• 最上面：顯示／隱藏、縮成橫條、立即重新整理、開啟 5 小時視窗（同卡片上的按鈕）\n"
            "• 桌面寵物：見下一節\n"
            "• 外觀與語言：圖示樣式、語言，以及「移回畫面內」（卡片跑到螢幕外找不到時用）\n"
            "• 等 Claude 時看短影片：打開 YouTube Shorts、Instagram Reels 或 YouTube；"
            "之後兩小時內 Claude 需要你，會發 Windows 通知叫你回來\n"
            "• 進階設定：三個預設關閉、開啟前都會先說明的功能\n"
            "  – 重置後自動起算：視窗結束後自動開下一個（每次花不到 1%）\n"
            "  – 自動換發過期 token：token 過期時自己換新的（有讓 Claude Code 登出的風險）\n"
            "  – 寵物準確偵測「在等你允許」：在 Claude Code 設定裝一個 hook\n\n"
            "■ 桌面寵物\n"
            "• 一隻跟著 Claude Code 狀態變表情的小東西：工作中、在等你允許（冒驚嘆號）、"
            "做完了（冒星星）、出錯了（頭上有烏雲）、沒事時睡覺。CLI、VS Code、桌面版的 Code 分頁都看得到\n"
            "• 點一下：跳出對話框說明哪個專案在做什麼，再點對話框就關掉。在等你、做完、出錯時會自己冒出來講\n"
            "• 睡著時點一下會叫醒它；哪種模式都可以拖到別的地方\n"
            "• 在寵物身上按右鍵：叫醒（任何時候都行）、手動改變狀態（或設回自動）、音樂開關、大小、隱藏\n"
            "• 右鍵選單「桌面寵物」：開關、叫醒、換角色（9 種）、換顏色（每個角色各記各的）、"
            "換位置（坐在橫條上／工作列上方散步／工作列裡散步／自由擺放）\n"
            "• 自訂角色：「角色 → 自訂角色…」打開資料夾，放進你自己的圖（命名方式見裡面的 README.txt）\n"
            "• 跳舞時播音樂：自訂角色有設定音樂時才會出現，可以關掉\n"
            "• 「在等你允許」預設是用猜的（工具送出 20 秒沒結果），跑很久的指令會猜錯；"
            "「進階設定」裡可以裝 Claude Code hook 讓它變準（開啟前會說明）\n\n"
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
            "• Weekly limit: resets once a week. Below it, a hint says whether this week's average pace lasts "
            "until the reset, and in red roughly when you'd run out if not (no hint in the first half day)\n"
            "Numbers are official. If none are available it says \"No usage data\" instead of guessing.\n"
            "It reads the account Claude Code (including the VS Code extension) is signed in to; "
            "the desktop app being on another account doesn't matter.\n\n"
            "■ Tray icon\n"
            "• The icon shows your 5-hour usage; pick one of 6 styles under Look & language > Icon style\n"
            "• Left-click: show or hide the card\n"
            "• Right-click: open the menu\n\n"
            "■ Card buttons (top right)\n"
            "• ⚡ Bolt: start a 5-hour window now (sends one tiny Claude Code request)\n"
            "• ↻ Refresh: re-read the official numbers. The endpoint is rate-limited to about once every 2 minutes\n"
            "• ─ Shrink: turn into a thin bar next to the taskbar; click the bar to expand\n"
            "• ✕ Hide: only hides the card; the app keeps running in the tray\n"
            "Drag the card or the bar anywhere.\n\n"
            "■ Tray menu\n"
            "• At the top: Show / hide, Shrink to bar, Refresh now, Start 5-hour window (same as the card buttons)\n"
            "• Desktop pet: see the next section\n"
            "• Look & language: icon style, language, and Move back on screen (if the card ended up off-screen)\n"
            "• Watch videos while Claude works: opens YouTube Shorts, Instagram Reels or YouTube; for the next "
            "two hours you get a Windows notification when Claude needs you\n"
            "• Advanced: three things that are off by default and explained before turning on\n"
            "  – Auto-start after reset: start the next window automatically (under 1% each time)\n"
            "  – Refresh expired token: renew an expired token itself (may sign Claude Code out)\n"
            "  – Pet: detect permission prompts exactly, by installing a Claude Code hook\n\n"
            "■ Desktop pet\n"
            "• A little creature that reacts to Claude Code: working, waiting for your permission (a \"!\"), "
            "done (sparkles), error (a rain cloud), and asleep when nothing's running. Sees the CLI, VS Code and "
            "the desktop app's Code tab\n"
            "• Click it for a dialog saying which project is doing what; click the dialog to close it. It speaks "
            "up by itself when Claude is waiting, done or hits an error\n"
            "• Click it while it sleeps to wake it up; drag it anywhere in any mode\n"
            "• Right-click the pet: wake it up (any time), set its state by hand (or back to automatic), music "
            "on/off, size, hide\n"
            "• Desktop pet in the tray menu: on/off, wake up, character (9), color (remembered per character), "
            "and where it lives (on the mini bar, along the top of the taskbar, inside the taskbar, or anywhere)\n"
            "• Your own character: Character > Custom character… opens a folder for your images (README.txt "
            "inside explains the file names)\n"
            "• Play music while dancing: shows up when a custom character comes with music; turn it off any time\n"
            "• \"Waiting for permission\" is a guess by default (a tool call with no result for 20 seconds), so "
            "long-running commands can fool it; install the Claude Code hook under Advanced to make it exact "
            "(explained before it's installed)\n\n"
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
