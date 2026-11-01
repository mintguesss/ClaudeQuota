# ClaudeQuota

[![CI](https://github.com/mintguesss/ClaudeQuota/actions/workflows/ci.yml/badge.svg)](https://github.com/mintguesss/ClaudeQuota/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/mintguesss/ClaudeQuota)](https://github.com/mintguesss/ClaudeQuota/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Windows 10/11](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D6)

**See your Claude subscription's 5-hour and weekly limits at a glance, without opening anything.**

A small Windows tray app for Claude Pro / Max users. It shows the official usage numbers and when the 5-hour window resets, so you stop getting surprised by "you've hit your limit" in the middle of a task.

[中文說明](#中文說明)

<img src="docs/hero-en.png" alt="ClaudeQuota on a Windows 11 desktop: a floating card showing the 5-hour limit at 42% with 2h 16m left and the weekly limit at 18%, a mini bar docked on the taskbar, the tray icon showing 42, and the tray menu">

## Features

- **Tray icon** with your 5-hour usage in 6 styles; hover for a summary
- **Floating card** with both limits, progress bars, and when each one resets (time left and the exact time, including the weekly reset day)
- **Mini bar** you can dock on the taskbar, or hide everything and keep only the tray icon
- **Put them anywhere**: drag the card and the mini bar wherever you like. Each remembers its own spot and reopens there next time
- Stays put through Win+D and virtual desktop switches, and **gets out of the way of full-screen games and videos**
- **Official numbers only**: read from Anthropic's usage endpoint about every 2 minutes, falling back to the Claude desktop app's local cache. If neither is available it says so instead of guessing
- English and Chinese interface, following your Windows language (switchable in the tray menu)

## Download

Grab **`ClaudeQuota.exe`** from the [latest release](https://github.com/mintguesss/ClaudeQuota/releases/latest) and run it. No Python needed.

- The exe is built by GitHub Actions from this repository's source; a SHA-256 checksum is attached to each release.
- It is not code-signed, so Windows SmartScreen may warn on first run: **More info → Run anyway**.
- To start with Windows: press `Win + R`, type `shell:startup`, and put a shortcut to `ClaudeQuota.exe` in that folder.

**Requirements:** Windows 10 or 11, and [Claude Code](https://claude.com/claude-code) signed in at least once (that is where the usage token comes from). The Claude desktop app is optional and acts as a fallback.

### Run from source

```bash
git clone https://github.com/mintguesss/ClaudeQuota.git
cd ClaudeQuota
pip install pystray pillow
pythonw widget.py
```

`啟動.vbs` (double-click) does the same without a console window.

## Using it

Left-click the tray icon to show or hide the card; right-click for the menu. The ✕ on the card only hides it; quit from the tray menu. **Help** in the tray menu explains every button and menu item in the app itself.

| Card button | What it does |
| --- | --- |
| ⚡ | Start the 5-hour window now (sends one tiny Claude Code request) |
| ↻ | Re-read the numbers (the endpoint is rate-limited, about once every 2 minutes) |
| ─ | Shrink to the mini bar |
| ✕ | Hide (keeps running in the tray) |

### Icon styles

Pick one under **Icon style** in the tray menu. The colour follows your 5-hour usage and turns red as you near the limit.

<img src="docs/tray-styles-en.png" alt="Six tray icon styles shown on light and dark taskbars: burst, number used, number left, ring, pie, two bars">

### Two optional settings (off by default)

| Setting | What it does | Cost |
| --- | --- | --- |
| **Auto-start after reset** | Starts a new 5-hour window as soon as the last one ends, so your reset time comes earlier | About **1–2% of the 5-hour limit** each time (measured: one `claude -p hi`, ~30k tokens) |
| **Refresh expired token** | Keeps live numbers working when Claude Code hasn't run for a while | May make a running Claude Code ask you to sign in again. Off, you just see the desktop app's cache until you open Claude Code |

The app explains each one and asks before turning it on.

## Data and privacy

- **Claude Code:** reads the OAuth token from `~/.claude/.credentials.json` to ask for *your own* account's usage. It also reads timestamps (not content) from Claude Code's session logs to work out when the window started. With *Refresh expired token* on, it also writes the renewed token back to the same file.
- **Claude desktop app:** reads its locally cached usage history.
- **Network:** only Anthropic's endpoints, for the usage request and, if enabled, the token refresh. No accounts, conversations, tokens or usage data are sent anywhere else. No telemetry.
- **Settings** are stored in `%APPDATA%\ClaudeQuota` (exe) or next to `widget.py` (source).

## Disclaimer

Unofficial personal project, not affiliated with, endorsed by or sponsored by Anthropic. Claude and Anthropic are trademarks of their respective owners; the MIT license grants no trademark rights. The usage endpoint is undocumented and may change at any time.

Interface idea inspired by [GlassQuota](https://github.com/chen10191200-hue/GlassQuota).

## License

[MIT](LICENSE)

---

## 中文說明

**不用打開任何東西，就能隨時看到 Claude 訂閱的 5 小時與每週額度。**

給 Claude Pro / Max 使用者的 Windows 系統列小工具，顯示官方用量數字與 5 小時視窗的重置時間，不會再做到一半才突然撞到額度上限。

<img src="docs/hero-zh.png" alt="ClaudeQuota 在 Windows 11 桌面上：置頂卡片顯示 5 小時額度 42%、剩 2 小時 16 分，每週額度 18%；放在工作列上的迷你橫條、顯示 42 的系統列圖示與右鍵選單">

### 功能

- 系統列圖示顯示 5 小時用量，有 6 種樣式可選；滑鼠停留可看摘要
- 置頂卡片顯示兩個額度的用量、進度條，以及各自的重置時間（剩多久與確切時間，每週額度會標星期幾）
- 卡片可縮成一條細橫條放在工作列上，或收起只留系統列圖示
- **位置隨你放**：卡片和橫條都能拖到任何地方，各自記住位置，下次開啟會出現在同一個地方
- 不受「顯示桌面」（Win+D）或切換虛擬桌面影響；全螢幕的遊戲或影片會自動讓開，結束後自己回來
- **只用官方數字**：約每 2 分鐘向 Anthropic 用量端點取得，取不到時改讀 Claude 桌面版的本機快取；兩者都沒有就明說讀不到，不做推估
- 中英文介面，跟隨 Windows 語言（可在系統列選單切換）

### 下載

到 [最新版本](https://github.com/mintguesss/ClaudeQuota/releases/latest) 下載 **`ClaudeQuota.exe`** 直接執行，不需要 Python。

- exe 由 GitHub Actions 從本倉庫原始碼建置，每個版本都附 SHA-256 校驗碼。
- 沒有程式碼簽章，第一次執行時 SmartScreen 可能會擋：按「**其他資訊 → 仍要執行**」。
- 開機自動啟動：按 `Win + R`，輸入 `shell:startup`，把 `ClaudeQuota.exe` 的捷徑放進去。

**需求：** Windows 10 或 11，以及至少登入過一次的 [Claude Code](https://claude.com/claude-code)（用量 token 從它來）。Claude 桌面版為選用，作為備援資料來源。

從原始碼執行：`pip install pystray pillow` 後雙擊 `啟動.vbs`（不會出現主控台視窗）。

### 圖示樣式

在系統列選單的「**圖示樣式**」切換。顏色跟著 5 小時用量走，快用完時會變紅。

<img src="docs/tray-styles-zh.png" alt="六種系統列圖示樣式，分別放在淺色與深色工作列上：星芒、已用數字、剩餘數字、進度環、圓餅、雙條">

### 兩個選用設定（預設關閉）

| 設定 | 作用 | 代價 |
| --- | --- | --- |
| **重置後自動起算** | 上一個 5 小時視窗一結束就開新的，重置時間會提早 | 每次約花 **5 小時額度的 1–2%**（實測一次 `claude -p hi` 約 3 萬 token） |
| **自動換發過期 token** | Claude Code 很久沒開時也能看到即時數字 | 正在執行的 Claude Code 可能會要你重新登入。不開的話只會改顯示桌面版快取，開一次 Claude Code 就恢復 |

開啟前程式會先說明並詢問；系統列選單的「**使用說明**」也會逐項解釋每個按鈕和功能。

### 資料與隱私

- **Claude Code**：讀取 `~/.claude/.credentials.json` 的 OAuth token，只查詢自己帳號的用量；另讀取對話紀錄的時間戳（不讀內容）推算視窗起點。開啟「自動換發過期 token」時，會把新 token 寫回同一個檔案。
- **Claude 桌面版**：讀取其本機快取的用量紀錄。
- **網路**：只連 Anthropic 官方端點（查詢用量，以及開啟時的 token 換發）。不上傳帳號、對話、token 或用量資料，沒有遙測。
- **設定檔**存在 `%APPDATA%\ClaudeQuota`（exe）或 `widget.py` 旁邊（原始碼）。

### 說明

本專案為非官方個人專案，與 Anthropic 無隸屬或背書關係。Claude、Anthropic 及相關標誌為其各自權利人的商標；MIT 授權不授予任何商標權。所用官方端點無公開文件，可能隨時變更。介面構想參考自 [GlassQuota](https://github.com/chen10191200-hue/GlassQuota)。
