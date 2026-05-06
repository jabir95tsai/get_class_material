# NTU COOL Get Class Material

> **一行指令,把 NTU COOL 整門課下載下來。**
> PDF、上課簡報、YouTube 影片、上課影片,全部抓下來,可以直接餵給 ChatGPT / Gemini / NotebookLM 幫你讀書。

## 如何使用

開啟powershell,輸入以下指令:

```powershell
pip install ntu-cool-material
ntu-cool-gcm
```

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
pip install ntu-cool-material
```

按 Enter,等它跑完(會看到一堆 `Collecting...`、`Downloading...`、`Installing...`)。最後看到 `Successfully installed ...` 就 OK。

> **`pip` 是什麼?** 它是 Python 內建的「套件下載器」。安裝 Python 時會跟著一起裝。如果這行說「找不到 pip」,通常代表 Python 沒裝好或沒勾「Add Python to PATH」,回到第 2 步重裝。

### 第 5 步 — 第一次跑工具

```powershell
ntu-cool-gcm
```

第一次跑會花幾分鐘,因為它會幫你裝幾個額外的東西:

- **Chromium 瀏覽器**(讓工具能幫你登入,大約 200MB)
- **ffmpeg**(合併 YouTube 影片用,大約 240MB,Windows 用 `winget` 自動裝)
- **Node.js**(處理 YouTube 影片用,Windows 用 `winget` 自動裝)

> 如果跳出「使用者帳戶控制」要求權限,點「是」(因為 winget 在裝系統工具)。

如果你看到 `winget` 不存在,工具會印出手動安裝的指令給你。Windows 11 大多有內建 `winget`。

裝完後,工具會跳出一個 **Chromium 瀏覽器視窗**,自動連到 NTU COOL 登入頁。**用平常的學號密碼登入**。

> 🔒 **密碼安全提醒:**
> - 密碼**只**輸入在這個瀏覽器登入頁,跟你平常用 Chrome 登入一樣
> - 工具本身**從來不會看到密碼**(它只在登入完拿瀏覽器的 cookie)
> - 不要把密碼貼在 PowerShell、聊天室、或任何其他地方

登入完成後,**不要關閉瀏覽器視窗**,工具會自己處理。

### 第 6 步 — 選課

登入完成後,PowerShell 會列出你的課程,像這樣:

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
  C:\Users\你\ntu-cool-gcm_material\音樂、演化與大腦 Music, Evolution and the Brain (57544)
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
C:\Users\你\ntu-cool-gcm_material\
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

```powershell
ntu-cool-gcm --refresh-session
```

會再開一次瀏覽器讓你重新登入。

### 只想下載 PDF,不要影片

```powershell
ntu-cool-gcm --skip-youtube --skip-cool-videos
```

可以混搭:`--skip-pdfs`、`--skip-pages`、`--skip-youtube`、`--skip-cool-videos`。

### 換存檔位置

```powershell
ntu-cool-gcm --out D:\我的課程
```

預設是當前目錄底下的 `ntu-cool-gcm_material/`。

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

### YouTube 不公開影片下載失敗

NTU 老師很多影片是 YouTube「不公開」設定,需要你的 Google 帳號 cookie 才抓得到。第一次設定:

```powershell
youtube-cookies
```

會跳出瀏覽器,**用你平常會看 NTU COOL 影片的那個 Google 帳號登入**,登入完關掉就好。之後再跑 `ntu-cool-gcm` 就抓得到了。

---

## 常見問題

### Q: 輸入 `python --version` 顯示「無法辨識」/「找不到」

代表你**還沒裝 Python**,或者**裝的時候沒勾「Add Python to PATH」**。

**解法:** 重裝一次,這次記得勾那個選項。然後**關掉 PowerShell 重開**。

### Q: 輸入 `pip install ntu-cool-material` 顯示「找不到 pip」

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

```powershell
ntu-cool-gcm --refresh-session
```

會再開一次瀏覽器讓你重新登入。

### Q: YouTube 影片抓不到 / 失敗很多

最常見原因:

1. **沒設 YouTube cookies** — 跑 `youtube-cookies` 一次
2. **沒裝 ffmpeg** — 跑 `ntu-cool-materials doctor` 確認
3. **影片有 DRM 保護** — 罕見但會發生,這種真的抓不下來

跑 `ntu-cool-materials doctor` 看有沒有缺東西。

### Q: 下載到一半中斷怎麼辦

**直接再跑一次 `ntu-cool-gcm`** 即可。

- 已下載完的檔案會跳過
- 大檔影片會從中斷的地方續傳(不用重抓)

### Q: 我的檔案到底放在哪

預設在你**執行 `ntu-cool-gcm` 時所在的目錄**底下,叫 `ntu-cool-gcm_material/`。

如果你在 PowerShell 看到提示是 `PS C:\Users\你>`,那就是 `C:\Users\你\ntu-cool-gcm_material\`。

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

工具會在你執行的目錄下建一個 `.secrets/` 資料夾,裡面存:
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

- PyPI: <https://pypi.org/project/ntu-cool-material/>
- GitHub: <https://github.com/jabir95tsai/get_class_material>
- License: MIT — 見 [LICENSE](LICENSE)

支援的 Python 版本:3.11+。Playwright + yt-dlp + ffmpeg + Node.js 為下載 YouTube 影片所需,工具會在第一次執行時嘗試自動安裝。

</details>



