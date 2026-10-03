# NTU COOL Get Class Material

> **一行指令,把 NTU COOL 整門課PDF、上課簡報、上課影片下載下來。**

## 如何使用

開啟powershell,輸入以下指令:

```powershell
pip install get-class-material
ntu-cool-gcm
```

## 自動匯入個人版 NotebookLM（實驗功能）

透過非官方 `notebooklm-py` API 匯入（固定測試版本 `0.8.3`），這是唯一的匯入方式。

**Windows 原始碼安裝：**雙擊 `Install-NotebookLM.cmd`，首次再執行 `Login-NotebookLM.cmd`，自行在獨立 Chrome 視窗登入 Google。完成後雙擊 `Start-NotebookLM.cmd` 選課匯入。已登入過的本機設定檔會直接沿用。

其他環境可在同一 Python 環境安裝及登入：

```sh
python -m pip install ".[notebooklm]"
notebooklm login
ntu-cool-materials notebooklm
```

登入狀態由 `notebooklm-py` 保存在本機。Windows 啟動器使用專案的 `.secrets/notebooklm-api`；CLI 可用 `NOTEBOOKLM_HOME` 或 `--notebooklm-storage` 指定。不要提交或分享登入檔案。程式不會從日常 Chrome 擷取 Cookie；登入失效時請重新執行登入程式。

舊版的擴充套件（`--extension`）、自動化瀏覽器（`--browser`）與手動上傳資料夾（`--manual`）已移除。沒有安裝 `notebooklm-py` 時，`ntu-cool-gcm` 下載完成後不會詢問匯入，只會提示一次安裝指令；明確加 `--notebooklm` 則會顯示安裝方式並回傳失敗。

成功使用本機 API 登入後，會將登入檔的絕對路徑記錄在 `~/.ntu-cool-gcm/notebooklm-storage.json`（只記錄位置，不複製憑證），之後從其他目錄啟動 `gcm` 也能沿用。明確指定的登入參數及目前目錄的登入設定仍優先；刪除這個位置紀錄即可取消跨目錄沿用。

### CMD 無點擊流程

在專案目錄執行（登入狀態有效時，不需點擊瀏覽器）：

```bat
rem 下載指定課程，完成後直接 API 匯入
Download-and-Import.cmd --course-id 60804

rem 僅匯入已下載教材
Start-NotebookLM.cmd --course-id 60804

rem 僅核對遠端，不上傳
Start-NotebookLM.cmd --course-id 60804 --verify-only
```

指定 ID 後不顯示選課或筆記本選單：已有課程對應就重用，否則以課程名稱新建筆記本。可加 `--notebooklm-url` 指定自己的筆記本。命令完成後直接回到 CMD，以退出碼回報成功或失敗，不會停在「按任意鍵」。下載命令使用背景瀏覽器處理需要瀏覽器的影片，停用互動重試；登入失效時需另外完成登入。未帶課程參數的 `Start-NotebookLM.cmd` 仍提供互動選課。

### 互動選課流程

1. 執行 `ntu-cool-gcm`，用自己的台大帳號登入、選課下載。
2. 下載完成後回答「要將這門課匯入 NotebookLM 嗎？」；預設否，下載不會自動把教材送到 Google。
3. 回答 `y` 就直接開始 API 匯入：沿用此課程已記錄的筆記本，首次則以課程名稱自動建立。不再選筆記本或二次確認。
4. 終端機顯示匯入結果與筆記本連結。缺少或過期的登入會提示重新登入；上傳失敗時教材仍會保留。

一般 `gcm` 指令也會沿用目前目錄 `.secrets`（不存在時使用家目錄 `.ntu-cool-gcm/.secrets`）內的 `notebooklm-api` API 登入。明確指定 `--notebooklm-storage` 或 SDK 的 `NOTEBOOKLM_HOME`／`NOTEBOOKLM_PROFILE`／`NOTEBOOKLM_AUTH_JSON` 時，以指定設定為準。若都沒有本機登入則使用 SDK 的預設設定檔。API 功能需在執行 `gcm` 的同一 Python 環境安裝 `get-class-material[notebooklm]`。

已有教材的使用者只要執行 `ntu-cool-materials notebooklm`，就能從預設下載目錄選課，
也可輸入其他資料夾路徑。每位使用者使用自己的帳號與本機設定檔，不需要維護者的 Google 帳號、API key 或 Codex。
`--no-notebooklm` 可略過下載後的詢問；管線／非互動模式不會自動詢問。

```powershell
# 開啟既有教材匯入引導 / Guided course picker
ntu-cool-materials notebooklm

# 指定資料夾，仍顯示引導 / Guided import for a given folder
ntu-cool-materials notebooklm --course-dir "C:\教材\課程名稱 (60804)" --guide

# 選課、下載後，自動匯入 NotebookLM / Download, then import
ntu-cool-gcm --notebooklm

# 指定單一課程 / One course
ntu-cool-materials download-course --course-id 60804 --notebooklm

# 已下載教材：先列出候選來源，不登入、不上傳 / Local preview only
ntu-cool-materials notebooklm --course-dir "C:\教材\課程名稱 (60804)" --dry-run

# 核對 API 遠端來源與本機紀錄，禁止上傳或建立筆記本 / Remote read-only check
ntu-cool-materials notebooklm --course-dir "C:\教材\課程名稱 (60804)" --verify-only

# 匯入同一個資料夾 / Import an existing course folder
ntu-cool-materials notebooklm --course-dir "C:\教材\課程名稱 (60804)"

# 改用你已建立的筆記本 / Use an existing notebook
ntu-cool-materials notebooklm --course-dir "C:\教材\課程名稱 (60804)" --notebooklm-url "https://notebooklm.google.com/notebook/YOUR_NOTEBOOK_ID"
```

每個課程資料夾首次直接匯入會以課程名稱建立筆記本；之後依課程根目錄的
`.notebooklm-import.json` 重用。也可用 `--notebooklm-url` 指定你擁有的筆記本。
互動引導會顯示目標並要求確認；尚無對應時可從自己的筆記本清單選擇或新增。
在多選課程時指定同一個 URL，會將這些課程都匯入該筆記本。

- 預設匯入 PDF、TXT、Markdown、DOCX、PPTX、CSV、EPUB，以及 MP3、WAV、M4A、MP4、AAC、OGG、OPUS 影音檔；略過 JSON、metadata、
  隱藏資料夾、目錄摘要、空檔案、連結檔案與超過 200 MB 的檔案。
- NotebookLM 不支援 Excel：下載完成與匯入前，會用 [markitdown](https://github.com/microsoft/markitdown)
  把每個 `.xlsx`／`.xls` 轉成旁邊的 `<檔名>.xlsx.md`（每個工作表一個表格），匯入的是這份 Markdown。
  原始 Excel 不會被修改；公式只保留計算結果，圖表與圖片會遺失。`[notebooklm]` 已內含轉換工具，
  只想轉檔可單獨安裝：

  ```powershell
  python -m pip install "get-class-material[excel]"
  ```
- 不想匯入影音檔可加 `--notebooklm-no-media`。每個影音檔各佔一個來源名額。
  影音依 NotebookLM 的音訊來源處理，**不保證理解影片畫面**；此版本不轉碼、不切割超大檔案，
  也不直接匯入 YouTube URL。
- 來源數預設上限為 50（含筆記本現有來源）。付費個人方案可依實際額度設定
  `--notebooklm-max-sources 100` 等值；這只調整本機檢查，不會提高 Google 帳號額度。
  超過上限會停止；請指定較小的週次資料夾或另一個筆記本。
- 使用 SHA-256 比對內容。來源名稱為「週次 - 檔名」；不同週的同名講義仍可區分，名稱重複時加上 (2)、(3)。
  檔案內容變更後會新增來源並保留遠端舊版本；不會刪除或覆寫遠端來源。
- 上傳前先記錄 `pending`，只有來源清單顯示已可使用才記錄 `complete`。
  逾時或中斷後，重跑會先確認遠端來源；無法確認時停止，不自動重送。
  若確定來源不存在，才移除 `.notebooklm-import.json` 中對應的 `pending` 項目後重試。
  程式異常退出若遺留 `.notebooklm-import.lock`，確認沒有匯入程式執行後再移除該鎖。
- `--dry-run` 只檢查本機候選檔案，不會讀取遠端來源、驗證登入或預估遠端去重結果。
  NotebookLM 仍會檢查每來源字數、檔案內容、方案額度及其他限制。
- `--verify-only` 需先登入，只讀取既有目標的來源狀態並核對本機紀錄，不建立筆記本、不上傳、不改写匯入紀錄；來源缺少時回傳失敗。

API 模式使用**非官方 Google 個人版介面**，不是 Enterprise 官方 API；Google 更新可能導致失效。上傳後會依來源 ID、檔名及 READY 狀態再次核對，保留既有去重與 pending 保護。處理中的同名來源會先等待，狀態不明或失敗則停止，避免重複上傳。
教材下載完成後即使匯入失敗，本機教材仍會保留；畫面會印出可直接重試的 `ntu-cool-materials notebooklm --course-dir …` 指令。
僅匯入你有權上傳至 Google 的教材。

維護者發佈前應用全新的設定檔驗證 Google 登入、首次上傳、重跑去重及中斷恢復。
單元測試和打包檢查不代表通過 Google 實站驗證；此功能通過實站測試前保留「實驗」標示。

來源：[Google 支援格式與限制](https://support.google.com/gemininotebook/answer/16215270)。

## 怎麼更新到最新版

**不確定要不要更新?** 直接跑這行就對了 —— 已經是最新版的話它什麼都不會做,不會弄壞任何東西:

```powershell
pip install --upgrade get-class-material
```

> 就是安裝指令多加 `--upgrade`。建議偶爾跑一次拿最新修正。
> (0.2.18 以後的版本啟動時會自動提醒你有沒有新版;但如果你現在是更舊的版本,看不到提醒是正常的 —— 跑上面那行更新一次就會開始有提醒了。)

---

## 這個工具是什麼

期中期末考前,你想把整學期的講義跟錄影影片整理一份在本機,丟給 AI 幫你做摘要、複習、出考古題?

這個工具會自動:

1. 用你自己的台大帳號登入 NTU COOL(只在瀏覽器登入頁輸入密碼,程式不會看到)
2. 把你選的課程整門搬下來:**PDF 講義、Page 文字內容、YouTube 連結影片、NTU 上課錄影**

之後你可以:
- 直接拖 PDF 到 ChatGPT / Gemini 問問題
- 把 `.md` 文字貼進 NotebookLM 做筆記

## 誰適合用

- 台大學生,有自己 NTU COOL 帳號,想要把整門課的教材下載卻不想一個一個檔案手動下載

---

## 新手使用完整教學

### 第 1 步 — 確認你有沒有 Python

**Python** 是這個工具運作所需的程式語言環境。

打開 PowerShell(往下兩段教怎麼打開),貼上這行按 Enter:

```powershell
python --version
```

如果看到類似 `Python 3.13.x`,而且 **3 後面那個數字 ≥ 11**,就跳到第 3 步。

如果看到「找不到」、「無法辨識的指令」,或顯示的版本是 3.10 以下,先做第 2 步。

### 第 2 步 — 安裝 Python

到 <https://www.python.org/downloads/> 下載最新版,執行安裝程式。

> ⚠ **非常重要:** 安裝畫面第一頁的最下面有個小勾選 **「Add Python to PATH」** 或 **「Add python.exe to PATH」**,**請勾起來**。沒勾的話 PowerShell 之後找不到 python,就要重裝。

裝完之後**關掉所有 PowerShell 視窗,再開一次新的**(這樣才會吃到新的 PATH 設定)。再跑一次第 1 步確認。

### 第 3 步 — 打開 PowerShell

**PowerShell** 是 Windows 內建的「黑黑的命令列視窗」。

最快的開法:

1. 按鍵盤 **`Win` 鍵**(就是有 Windows 標誌那顆)
2. 直接打 `PowerShell`
3. 按 Enter

或是用滑鼠:**開始選單 → 搜尋 PowerShell → 點開**。

開啟後你會看到一個藍色或黑色的視窗,最後一行是類似 `PS C:\Users\你的名字>` 的字。游標在那邊閃。

**從現在起,所有「貼上指令」都是貼到這個視窗裡然後按 Enter。**

### 第 4 步 — 安裝這個工具

在 PowerShell 貼上:

```powershell
pip install get-class-material
```

按 Enter,等它跑完(會看到一堆 `Collecting...`、`Downloading...`、`Installing...`)。最後看到 `Successfully installed ...` 就 OK。

> **`pip` 是什麼?** 它是 Python 內建的「套件下載器」。安裝 Python 時會跟著一起裝。如果這行說「找不到 pip」,通常代表 Python 沒裝好或沒勾「Add Python to PATH」,回到第 2 步重裝。

### 第 5 步 — 第一次跑工具

```powershell
ntu-cool-gcm
```

第一次跑會花幾分鐘,因為它會幫你裝幾個額外的東西:

- **Chromium 瀏覽器**(讓工具能幫你登入,大約 200MB)
- **ffmpeg**(合併 YouTube 影片用,大約 240MB,Windows 用 `winget` 安裝)
- **Node.js**(處理 YouTube 影片用,Windows 用 `winget` 安裝)

ffmpeg 和 Node.js 只有下載 YouTube 影片才需要,所以工具會先問你 `要現在自動安裝嗎? [Y/n]`,直接按 Enter 就是安裝。選 `n` 的話 30 天內不會再問,PDF / Page / 上課影片照常下載;之後想裝隨時可以跑 `ntu-cool-materials doctor --fix`。

> 如果跳出「使用者帳戶控制」要求權限,點「是」(因為 winget 在裝系統工具)。

如果你看到 `winget` 不存在,工具會印出手動安裝的指令給你。Windows 11 大多有內建 `winget`。

裝完後,工具會跳出一個 **Chromium 瀏覽器視窗**,自動連到 NTU COOL 登入頁。**用平常的學號密碼登入**。

> 🔒 **密碼安全提醒:**
> - 密碼**只**輸入在這個瀏覽器登入頁,跟你平常用 Chrome 登入一樣
> - 工具本身**從來不會看到密碼**(它只在登入完拿瀏覽器的 cookie)
> - 不要把密碼貼在 PowerShell、聊天室、或任何其他地方

登入完成並成功讀取課程清單後，工具會自動關閉 NTU COOL 瀏覽器視窗；你不需要保留登入頁面。
若選到含 NTU cool-video 的課程，程式可能會用同一個登入設定檔短暫重新開啟瀏覽器來取得影片資訊。

### 第 6 步 — 選課

登入完成後,工具要連線 NTU COOL 抓你的課程清單。這時你會看到一個會轉的小橫槓:

```text
- 連線 NTU COOL 中…
| 讀取課程清單中…
```

**這是正常的**,代表它正在跟伺服器要資料(網路慢的話會轉久一點),不是當掉。轉完就會列出你的課程,像這樣:

```text
找到 5 門課程:

  1) 日文一下 Japanese (Ⅰ) (2)
  2) 作業管理 Operations Management
  3) 音樂、演化與大腦 Music, Evolution and the Brain
  4) 組織行為學 Organizational Behavior
  5) 管理科學模式 Management Science Model

選擇課程 (1-5 可空格多選如 "1 3 5", h = 過去課程, a = 下載全部, en = English, q = 離開)
>
```

可以這樣輸入:

| 你輸入 | 結果 |
|---|---|
| `3` | 下載第 3 門 |
| `1 3 5` | 一次下載第 1、3、5 門(空格分隔) |
| `1,2,4` | 也可以用逗號 |
| `a` | 下載全部 5 門 |
| `h` | 進入過去學期的選單,選舊課程 |
| `en` | 切換成英文介面 |
| `q` | 離開 |

按 Enter 後,工具就會開始幫你抓檔案。檔案大的影片會看到進度條:

```text
[##########----] 47%  12.3 / 26.1 MB  3-1 演化的證據.mp4
```

### 第 7 步 — 下載完成

跑完會看到:

```text
完成。
  PDF:        新增 6、跳過 0、失敗 0
  Page:       新增 3、跳過 0、失敗 0
  YouTube:    新增 7、跳過 0、失敗 0
  上課影片:   新增 4、跳過 0、失敗 0

檔案存放位置:
  C:\Users\你\Documents\ntu-cool-gcm_material\音樂、演化與大腦 Music, Evolution and the Brain (57544)
```

**最後那行就是你的檔案放在哪。** 用檔案總管打開就能看到。

之後會問你:

```text
下一個動作: c = 繼續下載別的 / a = 下載全部 / h = 過去課程 / en = English / q = 離開
> 
```

要再下載另一門就 `c`,要結束就 `q`(會自動關閉 PowerShell 視窗)。

---

## 你會拿到什麼

下載到的目錄結構長這樣:

```
C:\Users\你\Documents\ntu-cool-gcm_material\
└── 音樂、演化與大腦 Music, Evolution and the Brain (57544)\
    ├── course_overview.md        ← 這份課程的目錄索引(可以餵給 AI)
    ├── week1\
    │   ├── SYLBS_班次1.pdf
    │   └── 1-1 生物音樂學簡介.mp4
    ├── week2\
    │   ├── 2-1-1 伊甸園外的生命長河.pdf
    │   └── 2-3-2 緊拉慢唱的妙用.md
    └── week3\
        └── ...
```

- **`.pdf`** — 老師上傳的講義,原始檔
- **`.md`** — Page 文字內容(VS Code、Typora、Obsidian 都能讀)
- **`.mp4`** — YouTube 影片 + NTU 上課錄影,都用人看得懂的中文標題
- **`course_overview.md`** — 整門課的目錄索引,列出每週有什麼、檔案放在哪。**直接拖到 AI 就可以叫它幫你做學習計畫**

---

## 下載後怎麼給 AI 用

### PDF
直接拖到 ChatGPT、Gemini、Claude 的對話框,然後問:
- 「幫我整理這份講義的重點」
- 「出 5 題選擇題」
- 「這份跟我之前丟的那份有什麼差別?」

### Markdown(`.md` 檔)
- **NotebookLM**: 把整個 `.md` 檔當「來源」上傳,可以一次餵很多份
- **ChatGPT / Gemini**: 用文字編輯器開,複製貼上即可

### 影片(`.mp4`)
影片本身大多 AI 工具不能直接吃,要先轉成文字:
- 用 **MacWhisper**(Mac)、**Whisper Desktop**(Windows)、或 OpenAI 線上 Whisper
- 轉好的逐字稿再丟給 AI 做摘要

### 整門課一起
把整個課程資料夾的 `course_overview.md` 跟幾份重點 PDF 一起拖到 NotebookLM,叫它「根據這些教材幫我做考試重點摘要」。

---

## 常見任務

### 之後再跑一次

```powershell
ntu-cool-gcm
```

只要登入沒過期(通常一兩天內),不用任何額外動作。**已經抓過的檔案會自動跳過,只補新增的**。

### 登入過期了

**多數情況你不用做任何事** — 直接跑 `ntu-cool-gcm`,工具會在連線時自動發現登入過期,並馬上幫你開瀏覽器重新登入(你會看到「登入已過期,開啟瀏覽器重新登入…」)。

如果想手動強制重新登入,也可以加:

```powershell
ntu-cool-gcm --refresh-session
```

### 只想下載 PDF,不要影片

```powershell
ntu-cool-gcm --skip-youtube --skip-cool-videos
```

下載課程時預設一併抓取所有可見公告（標題、作者、日期與全文），存放於課程資料夾的 `announcements/announcements.md` 與 `announcements/.announcements.json`（隱藏檔）。每次執行會重新抓取，以更新老師修改過的公告；課程總覽亦提供公告連結。可用 `--skip-announcements` 略過。

可以混搭:`--skip-announcements`、`--skip-pdfs`、`--skip-pages`、`--skip-youtube`、`--skip-cool-videos`。

### 換存檔位置

```powershell
ntu-cool-gcm --out D:\我的課程
```

預設是在你的「文件 / Documents」資料夾底下的 `ntu-cool-gcm_material/`。

如果你的目前資料夾已經有舊版建立的 `ntu-cool-gcm_material/`,工具會沿用那個資料夾,避免把舊檔案拆散。

### Word / PowerPoint / Excel / Zip 等非 PDF 檔

預設會**全部下載**，並先查詢 Canvas 檔案資料，保留原本的副檔名，包括 Word、PowerPoint、Excel、Zip、圖片、影片及程式碼檔案。若來源完全沒有副檔名，仍沿用 `.pdf` 備援命名。

### 增量更新、檢查完整性與下載速度

- 使用來源 ID 與課程內的 `.ntu_cool_materials.sqlite3` 記錄檔案版本、大小、SHA-256 和實際路徑；同名教材不會互相略過，模組改變順序也會保留原有路徑。
- Files 會查詢遠端更新資訊，Pages 每次重新取得內容。沒有變動的教材會跳過；本機大小不符時自動補抓。
- 舊版下載的檔案沒有來源紀錄時，第一次會就地沿用：一般檔案需大小與 Canvas 相符，影片需是可播放的 MP4，否則才重新下載。請保留 manifest，讓後續更新能正確辨識檔案。
- 預設同時下載 3 個 Canvas 檔案，可用 `--workers 1` 改為依序下載，最多設定 4。LTI 瀏覽器操作仍依序執行。
- YouTube 依 video ID 補抓缺少的影片，並利用課程內的 `.media-cache/` 避免同一影片跨週重抓。快取與週次資料夾的影片是硬連結，不會多占磁碟空間；不支援硬連結的磁碟（例如 exFAT 隨身碟）則改為複製，完成後刪除快取。
- 需要下載 YouTube 且 yt-dlp 已超過 60 天未更新，或影片下載失敗時，會先詢問是否用 pip 更新 yt-dlp（預設是）；非互動執行不會自動更新。
- Pages／公告保留連結與圖片網址，並抓取其中可識別、同一 Canvas 網站的檔案附件。外部網站只保留連結，不會遞迴下載整個網站。
- 每次正常完成流程會寫入 `.download_report.json`（與 `.ntu_cool_materials.sqlite3`、`.notebooklm-import.json` 等紀錄檔在 Windows 上會自動設為隱藏檔）。部分失敗會回傳退出碼 1；互動選單只將成功的課程標記完成。非互動執行遇到登入過期會失敗退出，可加 `--refresh-session` 重新登入。

```powershell
# 額外檢查本機內容雜湊 / Verify local SHA-256 before skipping
ntu-cool-gcm --verify-files

# 限制並行數 / Limit simultaneous file transfers
ntu-cool-materials download-course --course-id 57544 --workers 2
```

影片通常以來源 ID 識別；如果平台直接替換同一 ID 的影片且未提供更新資訊，無法僅靠本機 manifest 察覺遠端內容改變。刪除該本機影片（YouTube 也須刪除 `.media-cache/youtube/` 中對應 ID 的快取影片）後重跑可重新取得；`--verify-files` 用於檢查本機是否損壞，並不代表重新下載所有遠端影片。

### 已經知道課程 ID(網址裡的數字)

```powershell
ntu-cool-materials download-course --course-id 57544
```

(例如 `https://cool.ntu.edu.tw/courses/57544` 裡的 `57544`)

### 檢查環境是不是都裝好了

```powershell
ntu-cool-materials doctor
```

會列出 Python、瀏覽器、ffmpeg 等等的安裝狀態。

如果有缺,加上 `--fix` 嘗試自動修:

```powershell
ntu-cool-materials doctor --fix
```

### 看自己是哪個版本(回報問題時很有用)

```powershell
ntu-cool-gcm --version
```

`doctor` 的最上面也會印出版本號。回報問題時附上版本,會比較好幫你查。

工具每次啟動時也會**自動檢查有沒有新版**(一天最多查一次,離線就跳過,不會拖慢速度)。如果有新版會提醒你:

```text
💡 有新版本 0.2.18(你目前 0.2.17)。更新指令: pip install --upgrade get-class-material
```

照著跑那行 `pip install --upgrade get-class-material` 就更新好了。

### YouTube 不公開影片下載失敗

大部分 NTU 老師的 YouTube 影片是「**不公開 (unlisted)**」,這種**不需要登入**就能抓,工具會直接下載。

只有**真正設「私人 / 限齡 / 會員限定」**的影片才需要你的 YouTube 登入。這時工具**不用你另外登入**——它會在有影片失敗時問你一次:

```text
有 2 個 YouTube 影片下載失敗。
如果是私人 / 限齡 / 會員影片,需要你的 YouTube 登入狀態才能抓。
要用你瀏覽器裡已登入的 YouTube 帳號重試嗎? [y/N]
```

按 `y`,工具就會**直接讀取你平常用的瀏覽器(Chrome / Edge / Firefox …)裡已經登入的 YouTube 帳號**來重試——你不用再登入一次。

> 💡 **為什麼是讀瀏覽器、不是跳出來登入?**
> Google 會擋「被程式控制的瀏覽器」登入(會顯示「這個瀏覽器可能不安全」),所以直接讀你平常瀏覽器裡的登入狀態最穩。
>
> ⚠ 如果你用 **Chrome / Edge**,重試前請**先完全關掉那個瀏覽器**——瀏覽器開著的時候 cookie 檔會被鎖住,讀不到。Firefox 通常不用關。
>
> 前提是你平常那個瀏覽器**有登入會看到這些影片的 Google 帳號**(通常就是你的台大 Google 或個人帳號)。

---

## 常見問題

### Q: 怎麼更新到最新版?

在 PowerShell 跑這行:

```powershell
pip install --upgrade get-class-material
```

已經是最新版的話它不會做任何事,所以隨時都可以放心跑。想確認自己現在哪一版,打 `ntu-cool-gcm --version`。

### Q: 輸入 `python --version` 顯示「無法辨識」/「找不到」

代表你**還沒裝 Python**,或者**裝的時候沒勾「Add Python to PATH」**。

**解法:** 重裝一次,這次記得勾那個選項。然後**關掉 PowerShell 重開**。

### Q: 輸入 `pip install get-class-material` 顯示「找不到 pip」

跟上一題一樣,通常是 Python 沒裝好。重裝 Python 並勾「Add Python to PATH」。

### Q: PowerShell 顯示「無法載入指令碼,因為這個系統上已停用指令碼執行」

這是 Windows 的安全設定。打開 PowerShell **以系統管理員身份**(右鍵點 PowerShell 圖示 → 以系統管理員身份執行),貼上:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

按 `Y` 確認。然後關掉視窗,正常開一個新的就 OK。

### Q: `ntu-cool-gcm` 跑下去說 `winget` 找不到

`winget` 是 Windows 11 內建的應用程式安裝工具。Windows 10 比較舊版可能沒有。

**解法:** 到 Microsoft Store 搜尋「應用程式安裝程式」(App Installer),點安裝,重開 PowerShell。

或者直接手動安裝 ffmpeg 跟 Node.js:
1. ffmpeg: <https://www.gyan.dev/ffmpeg/builds/>(下載 `ffmpeg-release-essentials.zip`)
2. Node.js: <https://nodejs.org/>(下載 LTS 版)

裝完關掉 PowerShell 重開,再跑 `ntu-cool-gcm`。

### Q: NTU COOL 登入過期了

直接再跑一次 `ntu-cool-gcm` 就好 — 工具會自動發現過期並開瀏覽器讓你重新登入。要手動強制的話可以加 `--refresh-session`:

```powershell
ntu-cool-gcm --refresh-session
```

### Q: YouTube 影片抓不到 / 失敗很多

最常見原因:

1. **是私人 / 限齡 / 會員影片** — 需要你的 YouTube 登入。工具下載失敗時會問你「要用瀏覽器裡已登入的帳號重試嗎?」,按 `y` 即可(用 Chrome/Edge 的話記得先**關掉瀏覽器**再重試)
2. **沒裝 ffmpeg / Node.js** — 跑 `ntu-cool-materials doctor` 確認
3. **影片有 DRM 保護** — 罕見但會發生,這種真的抓不下來

跑 `ntu-cool-materials doctor` 看有沒有缺東西。

### Q: 下載到一半中斷怎麼辦

**直接再跑一次 `ntu-cool-gcm`** 即可。

- 已記錄完成且版本／本機大小吻合的檔案會跳過。
- 伺服器支援 Range，且部分檔案具有吻合的 ETag／Last-Modified 時才續傳；無法確認版本時會安全地重新下載。
- 暫時性網路錯誤與 429／部分 5xx 最多嘗試 3 次，遵守 Retry-After（最多等 60 秒）。
- 下載長度與續傳範圍檢查通過後才替換正式檔案；失敗時保留原本完整檔案。

### Q: 我的檔案到底放在哪

預設在你的「文件 / Documents」資料夾底下,叫 `ntu-cool-gcm_material/`。

常見位置像這樣:

```text
C:\Users\你\Documents\ntu-cool-gcm_material\
```

如果你以前已經在目前資料夾建立過 `ntu-cool-gcm_material/`,工具會優先沿用那個舊資料夾。

每次跑完工具最後一行也會印「檔案存放位置:」+ 完整路徑。

### Q: 可以只抓 PDF 嗎

可以:

```powershell
ntu-cool-gcm --skip-pages --skip-youtube --skip-cool-videos
```

### Q: 可以換輸出資料夾嗎

可以:

```powershell
ntu-cool-gcm --out D:\我的課程資料夾
```

### Q: 影片畫質好像不太好

YouTube 影片畫質取決於老師上傳的原檔。多數 NTU 老師上傳的是 **480p**,工具會自動抓最高畫質,所以你看到的就是來源最高解析度。

NTU 上課影片(cool-video)畫質維持來源原檔。

### Q: 這樣會不會違反版權

**這個工具只下載你自己有權限看到的課程教材** — 跟你平常用瀏覽器一個個下載的權限一模一樣,不繞過任何權限系統。

但是:

- 下載下來的教材**仍然受老師、出版社、原作者的版權保護**
- **僅限自己學習使用**,不要公開散播、不要上傳到網路、不要分享給校外人士
- 老師同意才能在合理範圍內分享給同班同學

簡單說:**自己讀沒問題,公開散播違法。**

---

## 安全與隱私提醒

### 密碼

- 密碼**只**輸入在工具跳出來的瀏覽器登入頁(就是 NTU COOL 的官方登入頁面)
- 工具本身**從來不會看到密碼**
- 不要把密碼複製貼上到 PowerShell、聊天室、Email 或任何其他地方

### `.secrets/` 資料夾

工具會把登入資料存在 `~/.ntu-cool-gcm/.secrets/`(Windows 是 `C:\Users\<你的帳號>\.ntu-cool-gcm\.secrets\`;如果你執行的目錄下本來就有 `.secrets/`,會沿用那一個),裡面存:
- 你登入後的 cookie(等同於登入狀態)
- YouTube 的 cookie

**這個資料夾等同於你的登入憑證**。請:

- ❌ **不要**分享給別人,連同學也不要
- ❌ **不要**上傳到 GitHub、雲端硬碟、Discord
- ❌ **不要**截圖貼到聊天室
- ✅ 換電腦就重新跑 `ntu-cool-gcm --refresh-session` 重新登入即可

預設它是隱藏資料夾(Windows 用 `dir` 看不到,要在檔案總管開「顯示隱藏檔」才看得到)。

### 下載的教材

不要公開散播。詳見上面的版權問答。

---

## 進階:其他指令

如果你只想看可見課程清單,不下載:

```powershell
ntu-cool-materials courses --refresh-session
```

抓某課的公告:

```powershell
ntu-cool-materials announcements --course-id 57544 --refresh-session --print
```

完整指令清單請看 `--help`:

```powershell
ntu-cool-materials --help
ntu-cool-gcm --help
```

---

<details>
<summary>給開發者 / 從原始碼安裝</summary>

```powershell
git clone https://github.com/jabir95tsai/get_class_material.git
cd get_class_material
pip install -e .
python -m playwright install chromium
python -m unittest discover -s tests
```

架構說明見 [CLAUDE.md](CLAUDE.md)。

- PyPI: <https://pypi.org/project/get-class-material/>
- GitHub: <https://github.com/jabir95tsai/get_class_material>
- License: MIT — 見 [LICENSE](LICENSE)

支援的 Python 版本:3.11+。Playwright + yt-dlp + ffmpeg + Node.js 為下載 YouTube 影片所需;第一次執行時必要項目(Playwright Chromium、yt-dlp)會自動安裝,ffmpeg / Node.js 會先詢問。

</details>



