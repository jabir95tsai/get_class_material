# NTU COOL Materials (`ntu-cool-gcm`)

> 一行指令，把你 NTU COOL 課程裡的 PDF、Pages、講課影片全部抓回本機。
> One command pulls every PDF, Page, and lecture video from your NTU COOL courses to your laptop.

```powershell
ntu-cool-gcm
```

```text
Found 5 course(s):

  1) 日文一下 Japanese (Ⅰ) (2)
  2) 作業管理 Operations Management
  3) 音樂、演化與大腦 Music, Evolution and the Brain
  4) 組織行為學 Organizational Behavior
  5) 管理科學模式 Management Science Model

Pick a course (1-5, or q to quit): 3
```

接下來程式會自己：登入 NTU COOL → 列出該課程所有週次 → 抓 PDF、Pages、YouTube 影片、NTU CDN 影片 → 整齊放到一個資料夾。所有檔案會用人看得懂的中文檔名，不是 Canvas 的內部代號。

After picking a course it logs in to NTU COOL, walks every week, and downloads PDFs, Pages, YouTube links, and NTU CDN videos into one folder. Filenames use the human-readable Chinese titles you see on COOL, not Canvas's internal IDs.

---

## 為什麼要用？/ Why use it?

- **整理一次到位**：教材分散在 NTU COOL 的 Files、Pages、外部連結、cool-video CDN，這個工具一次抓完並用人看得懂的檔名整齊放好。
- **AI 友善**：所有 PDF、影片、文字頁面集中在一個資料夾，餵給 ChatGPT / Claude / NotebookLM 做摘要、複習、問答都很方便。
- **重跑安全**：已經抓過的檔案不會重抓；下週開課再跑一次只會補新的。
- **不碰你的密碼**：用瀏覽器登入一次，後面都用 cookie，程式從來不知道你的學號密碼。

---

## 安裝 / Installation

需要先裝四樣東西：Python、Node.js、ffmpeg、本工具自己。前三個是其他工具要用到的，本工具用 `pip` 安裝。

You need four things: Python, Node.js, ffmpeg, then this tool itself. The first three are runtime helpers; the tool itself installs via `pip`.

### Step 1 — 裝 Python (3.11 或更新版本)

下載並安裝 Python：<https://www.python.org/downloads/>。安裝時記得勾選 **「Add Python to PATH」**。

```powershell
# 確認安裝成功
python --version
```

### Step 2 — 裝 Node.js

YouTube 影片下載要用 Node 來解開 YouTube 的 JS 防護，少了它畫質會被卡在 360p。

Without Node, YouTube downloads are stuck at 360p. Install from <https://nodejs.org/>.

```powershell
node --version
```

### Step 3 — 裝 ffmpeg

合併影片和音訊要用。Windows 最簡單的方式：

The video downloader merges streams with ffmpeg. On Windows:

```powershell
winget install Gyan.FFmpeg
# 或者用 scoop
scoop install ffmpeg
```

macOS:
```bash
brew install ffmpeg
```

確認：
```powershell
ffmpeg -version
```

### Step 4 — 裝本工具

目前還沒上 PyPI，從 GitHub clone 下來裝：

Not on PyPI yet — clone and install:

```powershell
git clone https://github.com/jabir95tsai/get_class_material.git
cd get_class_material
pip install -e .

# 第一次裝 Playwright 用的 Chromium
python -m playwright install chromium
```

---

## 用法 / Usage

### 第一次跑 / First run

打開 PowerShell（或 cmd / terminal），輸入：

```powershell
ntu-cool-gcm --refresh-session
```

第一次要加 `--refresh-session`，會跳出一個瀏覽器視窗讓你登入 NTU COOL。**用平常的學號密碼登入即可**。登入完不要關視窗，程式會自己抓 cookie 然後繼續。

The first run needs `--refresh-session`. A browser window pops up for NTU SSO — log in with your student account, then **leave it open**. The tool grabs the session cookie and continues automatically.

接著它會列出你的課程，輸入號碼選一門：

It then lists your active courses; type a number to pick:

```text
Found 5 course(s):

  1) 日文一下 Japanese (Ⅰ) (2)
  2) 作業管理 Operations Management
  3) 音樂、演化與大腦 Music, Evolution and the Brain
  4) 組織行為學 Organizational Behavior
  5) 管理科學模式 Management Science Model

Pick a course (1-5, or q to quit): 3
```

選完之後等程式跑完，跑完會告訴你檔案放哪：

```text
Done. Files saved to:
  C:\Users\you\ntu-cool-gcm_material\音樂、演化與大腦 Music, Evolution and the Brain (57544)
```

### 之後再跑 / Subsequent runs

```powershell
ntu-cool-gcm
```

只要你的 NTU COOL session 還沒過期（通常一兩天內），就不用再 `--refresh-session`。已經抓過的檔案會自動跳過，只補新的。

As long as your NTU COOL session is still valid (usually a day or two), you can drop `--refresh-session`. Already-downloaded files are skipped; only new content is fetched.

### YouTube 影片需要 cookie / YouTube cookies

NTU COOL 課程裡的 YouTube 影片很多是「不公開」（unlisted），需要你的 Google 帳號才能下載。第一次跑要先用內建工具匯出 YouTube cookies：

Many YouTube videos linked from NTU COOL are unlisted and need your Google session to download. Export YouTube cookies once with the bundled helper:

```powershell
youtube-cookies
```

會跳出一個瀏覽器，登入你的 Google / YouTube 帳號（建議用平常上 NTU COOL 看影片的那個帳號），登入完關掉就可以了。Cookies 會存到 `.secrets/youtube_cookies.txt`，之後 `ntu-cool-gcm` 會自動拿去用。

A browser window opens — log in with the same Google account you use to watch the unlisted videos. Cookies get saved to `.secrets/youtube_cookies.txt` and `ntu-cool-gcm` picks them up automatically.

---

## 你會拿到什麼 / What you get

每門課都會得到一個資料夾：

```
ntu-cool-gcm_material/
└── 音樂、演化與大腦 Music, Evolution and the Brain (57544)/
    ├── week1/
    │   ├── SYLBS_班次1.pdf
    │   ├── 1-1 生物音樂學簡介.mp4
    │   └── ...
    ├── week2/
    │   ├── 2-1-1 伊甸園外的生命長河.pdf
    │   ├── 2-3-2 緊拉慢唱的妙用.md
    │   └── 2-3-1 戲曲音樂「緊拉慢唱」的演化（上）.mp4
    └── week3/
        └── ...
```

- **PDF**：原始檔，檔名用 NTU COOL 上看到的那個。
- **`.md`**：Page 類型的內容轉成 Markdown，可以直接用 VS Code / Typora / Obsidian 開。
- **`.mp4`**：包含 YouTube 影片和 NTU 自己 CDN 上的講課影片，全部以講師命名的標題存檔。

- **PDFs** keep their original Canvas display names.
- **`.md`** files are Canvas Pages converted to plain Markdown — readable in any editor.
- **`.mp4`** files include both YouTube videos and NTU CDN lecture videos, named with the Chinese / English titles you see on COOL.

要換存檔位置：

To save somewhere else:

```powershell
ntu-cool-gcm --out D:\study\ntu
```

---

## 常見問題 / FAQ

### 它會抓我的學號密碼嗎？/ Does it see my password?

不會。程式只用瀏覽器登入後的 session cookie，跟你平常開 NTU COOL 用的是同一個機制。Cookie 存在 `.secrets/` 資料夾裡，這個資料夾預設不會被 git track。

No. It uses the browser session cookie after you log in — the same mechanism your normal browser uses. Cookies live in `.secrets/`, which is git-ignored.

### 為什麼影片只有 480p？/ Why are videos only 480p?

NTU 老師上傳到 YouTube 的影片很多本身就只有 480p。本工具會自動抓最高畫質，所以你看到的就是來源最高的解析度。NTU CDN 上的講課影片同樣保留原檔畫質。

Many lecture YouTube uploads are 480p-only at the source. The tool always picks max source quality, so you're getting the original.

### 跑到一半要重來怎麼辦？/ Can I re-run if it breaks halfway?

可以。再跑一次 `ntu-cool-gcm` 即可，已經抓好的檔案會跳過，只會補沒抓到的。

Yes. Re-run `ntu-cool-gcm` — completed files are skipped, only the missing ones are retried.

### 登入過期了 / Session expired

Cookie 大概一兩天會過期，過期之後再加 `--refresh-session` 跑一次就好：

NTU COOL session cookies expire after a day or two. When that happens:

```powershell
ntu-cool-gcm --refresh-session
```

### 我只想要 PDF，影片太大不要 / I only want PDFs, no videos

```powershell
ntu-cool-gcm --skip-youtube --skip-cool-videos
```

也可以單獨跳過：`--skip-pdfs`、`--skip-pages`、`--skip-youtube`、`--skip-cool-videos`。

You can mix and match: `--skip-pdfs`, `--skip-pages`, `--skip-youtube`, `--skip-cool-videos`.

### 出現 SSO timeout / SSO timeout error

代表瀏覽器視窗開了 10 分鐘還沒登入完。再跑一次 `--refresh-session`，這次盡快登入。

Means the browser sat on the SSO page for 10 minutes without finishing. Re-run with `--refresh-session` and complete login faster.

### 出現 `yt-dlp` 找不到 / yt-dlp not found

裝過 `pip install -e .` 之後 `yt-dlp` 應該會自動裝起來。如果沒有：

```powershell
pip install --upgrade yt-dlp
```

### Bash 視窗顯示中文檔名亂碼 / Bash shows mojibake for Chinese filenames

Windows 的 PowerShell 偶爾會把中文搞亂。檔案本身是好的，只是顯示問題。可以加 `-X utf8`：

The files are fine on disk; it's just a console display issue. Force UTF-8 with:

```powershell
python -X utf8 -m ntu_cool_materials pick
```

---

## 進階用法 / Advanced

### 直接指定課程 ID / Skip the picker

如果你已經知道課程的數字 ID（網址 `https://cool.ntu.edu.tw/courses/57544` 裡的 `57544`）：

If you already know the course id (from the URL):

```powershell
ntu-cool-materials download-course --course-id 57544
```

### 列出可見課程 / List visible courses

```powershell
ntu-cool-materials courses --headers-file .secrets\ntu_cool_headers.txt
```

### 抓某課的公告 / Fetch announcements for a course

```powershell
ntu-cool-materials announcements --course-id 57544 --headers-file .secrets\ntu_cool_headers.txt --print
```

存到 `<course>/announcements/announcements.json` + `announcements.md`。

### 自動更新 session（背景模式）/ Auto-refresh session in background

Headless 模式：失敗（session 真的過期）會直接 exit 2，不會跳視窗：

Headless mode exits with code 2 if the session is dead, no browser pops up:

```powershell
ntu-cool-materials announcements --course-id 57544 --headers-file .secrets\ntu_cool_headers.txt --refresh-session --headless-refresh
```

---

## 哪些不會抓 / What it skips

- 鎖住的 Discussion / Quiz / Assignment（Canvas 鎖了就抓不到）
- 老師沒設定 file/page 屬性、純連結到外部網站的 Module Item
- DRM 保護的 YouTube 影片（極少數）

Locked Discussions/Quizzes/Assignments, externally-hosted module items without file/page metadata, and the rare DRM-protected YouTube video.

---

## 倫理與授權 / Ethics & License

- 只用你自己的 NTU 帳號權限抓自己修的課，**不繞過任何權限**。
- 下載的教材依然受原始授權保護（老師、出版社的版權），請勿公開散播。
- 本工具本身採 MIT License — 見 [LICENSE](LICENSE)。

This tool only fetches materials you already have access to via your own NTU account. **It does not bypass any permission system.** Downloaded course materials remain subject to their original copyrights — do not redistribute. The tool itself is MIT-licensed; see [LICENSE](LICENSE).

---

## 給開發者 / For developers

See [CLAUDE.md](CLAUDE.md) for architecture notes (used by AI coding assistants and human contributors alike).

```powershell
# 跑測試
python -m unittest discover -s tests
```

## 參考 / References

- NTU 教師手冊：<https://facultyhandbook.ntu.edu.tw/09-4-NTUCOOL.html>
- Canvas API 文件：<https://developerdocs.instructure.com/services/canvas>
