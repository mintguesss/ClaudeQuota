# Custom pets

[中文](#自訂寵物)

Any folder of images can be a desktop pet. Put it under `pets\<name>\` next to `widget.py` (or `%APPDATA%\ClaudeQuota\pets\<name>\` for the exe; *Desktop pet → Character → Custom character…* opens it) and pick it from the *Character* menu.

## The folder

| File | What it is |
| --- | --- |
| `working.png`, `waiting.png`, `done.png`, `error.png`, `idle.png`, `awake.png`, `held.png` | One image per state: working, waiting for your permission, done, error, asleep, woken up, being dragged |
| `working_1.png`, `working_2.png`, … | An animation: numbered frames play in order (`.gif` works too) |
| `pet.json` | Optional settings, below |
| `*.wav` | Optional music for a state |

A single image is enough: missing states fall back to `awake`, then `idle`. Images are scaled to fit the pet's square, bottom-aligned.

```json
{
  "fps": 24,
  "music": {"done": "ending.wav", "awake": "dance.wav"}
}
```

- `fps`: playback speed. Without it, frames play at the built-in pets' pace (about 8 a second). Use the video's frame rate for video-like motion; playback follows the clock, so it never runs slow.
- `music`: a WAV per state. *Woken up* loops it; other states play it once. Right-click the pet → *Play music while dancing* turns it off.

## Making one from video and photos

`tools/make_pet.py` does the tedious parts: it cuts the person out of every frame (rembg), drops anyone else in the frame, keeps one scale for the whole clip so nothing jitters, adds the state icons, and writes `pet.json`.

```bash
pip install "rembg[cpu]" opencv-python-headless pillow
```

FFmpeg must be on your PATH. The first run downloads the background-removal model (about 220 MB).

**Clips from a video, photos for the rest:**

```bash
python tools/make_pet.py --name Star --video stage.mp4 --crop 480 --clip awake=14:10 --clip done=171.5:10 --clip working=36:8 --photo idle=lying.jpg --photo error=pout.jpg --photo waiting=wave.jpg --photo held=wave.jpg --music awake --music done --studio track.m4a
```

- `--clip STATE=START:LENGTH` takes seconds from `--video` at `--fps` (default 24).
- `--crop` keeps a band around the middle of a wide video, where a fancam keeps its subject; people outside it are dropped.
- `--music STATE` cuts that clip's music. With `--studio`, the music comes from a clean studio recording instead of the noisy stage sound. The tool lines the two up by matching beats across the whole song, so the music stays in time with the dance. If your video file has no sound, give its sound separately with `--video-audio`.

**One photo that moves like the built-in animals:**

```bash
python tools/make_pet.py --name Star-mini --animal standing.png
```

This uses one cut-out for every state and applies the animals' own movements (bounce, hop, squash, wobble), icons, error tint and pace.

**Tips**

- Choose stretches where the person is alone in the middle. Overlapping dancers can't be separated.
- Stop a clip before the video fades out.
- Fancams often cut off the feet, and that can't be recovered.

Only use footage, photos and music you're allowed to use, and keep pets made from other people's work to yourself.

## Custom themes

The menus, dialogs and speech bubble can take your own colors. Put a `themes.json` in the same `pets` folder; it shows up under *Look & language → Theme*:

```json
{
  "duckling": {
    "label": "Duckling",
    "bg": [255, 241, 247], "edge": [234, 112, 154], "accent": [240, 100, 146],
    "hover": [255, 225, 237], "text": [92, 38, 62],
    "deco": "duck", "mark": "x‿x"
  }
}
```

Colors you leave out come from the default theme. The keys are `bg`, `edge`, `edge_w` (border width), `inner` (inner line), `hover`, `text`, `faint` (disabled text), `accent` (name tag, check marks, primary button), `tag_text` and `sep`. `deco: "duck"` puts a little duck on the name tags and uses it as the selected mark; `mark` is a short text in the menus' bottom-right corner.

---

## 自訂寵物

任何一個放圖的資料夾都能當桌面寵物。放在 `widget.py` 旁邊的 `pets\<名字>\`（exe 版是 `%APPDATA%\ClaudeQuota\pets\<名字>\`，「桌面寵物 → 角色 → 自訂角色…」會打開它），再從「角色」選單選它。

### 資料夾內容

| 檔案 | 用途 |
| --- | --- |
| `working.png`、`waiting.png`、`done.png`、`error.png`、`idle.png`、`awake.png`、`held.png` | 每個狀態一張：工作中、在等你允許、做完了、出錯了、睡覺、被叫醒、被拎起來 |
| `working_1.png`、`working_2.png`… | 動畫：照編號依序播放（`.gif` 也可以） |
| `pet.json` | 選用設定，見下方 |
| `*.wav` | 選用，某個狀態的音樂 |

一張圖就能用，缺的狀態會改用 `awake`，再沒有就用 `idle`。圖會等比例縮進寵物的方框、對齊底部。

- `fps`：播放速度。沒寫就跟內建角色一樣（每秒約 8 格）。影片做的角色就寫影片的格數。播放是照時間算的，不會變慢動作。
- `music`：每個狀態一個 WAV。「被叫醒」會循環播放，其他狀態播一輪。在寵物身上按右鍵 →「跳舞時播音樂」可以關掉。

### 從影片和照片做一隻

`tools/make_pet.py` 會幫你處理麻煩的部分：
- 每一格把人去背（rembg），並刪掉畫面裡的其他人。
- 整段用同一個縮放比例，畫面不會抖。
- 加上各狀態的小圖示，最後寫好 `pet.json`。

需要先裝好 `rembg`、`opencv-python-headless`、`pillow`，並且 FFmpeg 要在 PATH 裡。第一次執行會下載去背模型，約 220 MB。

- **影片片段**：`--clip 狀態=開始秒數:長度`。
- **只留中間**：`--crop` 只保留畫面中間一段，直拍通常把主角放中間，旁邊的人會被拿掉。
- **配樂**：`--music 狀態` 剪那段的音樂。加 `--studio 原曲檔` 會改用乾淨的錄音室版本，工具會比對整首歌的節拍把兩者對齊，音樂和舞步不會錯拍。影片檔沒有聲音的話，用 `--video-audio` 另外給它的聲音檔。
- **動物模式**：`--animal 照片` 用一張去背照片，套用內建動物的動作、圖示、出錯時的顏色和播放速度。

**小提醒**
- 挑主角一個人在中間的片段，跟別人疊在一起的去不乾淨。
- 片段要在影片淡出之前結束。
- 直拍常常切掉腳，這個救不回來。

只使用你有權使用的影片、照片和音樂；用別人作品做的寵物請留著自己用。

### 自訂主題

選單、對話框和寵物的對話泡泡都可以換成你自己的顏色。在同一個 `pets` 資料夾放一個 `themes.json`（格式見上面英文段落的範例），它就會出現在「外觀與語言 → 主題」。

- 沒寫的顏色沿用預設主題。
- `deco: "duck"` 會在名牌上放一隻小鴨，選中的項目也會變成小鴨。
- `mark` 是選單右下角的一小段字。
