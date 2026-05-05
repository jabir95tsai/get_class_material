# NTU COOL Materials

一行指令把 NTU COOL 課程的 PDF 跟 Page 全部抓回本機，幫你整理好餵給 AI 複習。

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

選號碼，程式自己登入 NTU COOL，把該課程所有週次的 **PDF** 跟 **Page** 抓下來放到資料夾。檔名都用人看得懂的中文。

---

## 安裝

### 1. 先裝 Python（3.11 或更新）

到 <https://www.python.org/downloads/> 下載安裝。安裝時記得勾 **「Add Python to PATH」**。

### 2. 裝這個工具

```powershell
pip install ntu-cool-material
python -m playwright install chromium
```

第二行是裝登入用的瀏覽器（一次就好）。

### 3. 確認都裝好了

```powershell
ntu-cool-materials doctor
```

看到 `✓ All set` 就可以開始用。

---

## 使用方式

### 第一次：登入 NTU COOL

```powershell
ntu-cool-gcm --refresh-session
```

會跳出一個瀏覽器視窗，**用學號密碼登入**。登入完不要關視窗，程式會自己抓 cookie 然後繼續。

選課程後等程式跑完，會告訴你檔案放哪：

```text
Done. Files saved to:
  C:\Users\you\ntu-cool-gcm_material\音樂、演化與大腦 Music, Evolution and the Brain (57544)
```

### 之後再跑

```powershell
ntu-cool-gcm
```

只要登入沒過期（一兩天內）就不用再 `--refresh-session`。

---

## 想下載影片？

預設**不抓影片**，因為 YouTube 影片要額外的工具，檔案也很大。如果要抓：

### 1. 多裝兩個東西

| 工具 | Windows | Mac |
|------|---------|-----|
| Node.js | `winget install OpenJS.NodeJS` | `brew install node` |
| ffmpeg | `winget install Gyan.FFmpeg` | `brew install ffmpeg` |

### 2. 多裝一個 Python 套件

```powershell
pip install ntu-cool-material[videos]
```

### 3. 設定 YouTube cookie（不公開影片需要）

```powershell
youtube-cookies
```

跳出瀏覽器，登入你平常看 NTU COOL 影片的 Google 帳號，登入完關掉就好。

### 4. 跑

```powershell
ntu-cool-gcm
```

這次會把 PDF、Page、YouTube 影片、NTU 上課影片全部抓下來。

---

## 你會拿到什麼

```
ntu-cool-gcm_material/
└── 音樂、演化與大腦 Music, Evolution and the Brain (57544)/
    ├── week1/
    │   ├── SYLBS_班次1.pdf
    │   └── 1-1 生物音樂學簡介.mp4
    ├── week2/
    │   ├── 2-1-1 伊甸園外的生命長河.pdf
    │   └── 2-3-2 緊拉慢唱的妙用.md
    └── ...
```

- **PDF** → 老師上傳的講義
- **`.md`** → Page 類型的內容（VS Code、Typora、Obsidian 都能讀）
- **`.mp4`** → 影片（裝了 `[videos]` 才會有）

要換存檔位置：

```powershell
ntu-cool-gcm --out D:\study
```

---

## 常見問題

**Q：它會看到我的密碼嗎？**
不會。只用瀏覽器登入後的 cookie，密碼程式從來不知道。

**Q：跑到一半當機？**
再跑一次就好，已經抓過的檔案不會重抓。

**Q：登入過期了？**
```powershell
ntu-cool-gcm --refresh-session
```

**Q：只想要某些東西？**

```powershell
ntu-cool-gcm --skip-pdfs          # 不要 PDF
ntu-cool-gcm --skip-pages         # 不要 Page
ntu-cool-gcm --skip-youtube       # 不要 YouTube
ntu-cool-gcm --skip-cool-videos   # 不要上課影片
```

可以混搭。

**Q：哪些東西不會抓？**

- 鎖住的討論 / 作業 / 小考
- 純外部連結（不是 YouTube 的那種）
- 有 DRM 保護的 YouTube 影片（很少見）

**Q：直接指定課程 ID（不要選單）？**

```powershell
ntu-cool-materials download-course --course-id 57544
```

課程 ID 就是 NTU COOL 網址裡的數字：`https://cool.ntu.edu.tw/courses/57544`。

---

## 注意事項

- 只用你自己的 NTU 帳號權限抓自己修的課，**不繞過任何權限**。
- 下載的教材依然受老師、出版社的版權保護，請勿公開散播。
- 本工具採 MIT License — 見 [LICENSE](LICENSE)。

---

<details>
<summary>給開發者 / 從原始碼安裝</summary>

```powershell
git clone https://github.com/jabir95tsai/get_class_material.git
cd get_class_material
pip install -e .[videos]
python -m playwright install chromium
python -m unittest discover -s tests
```

架構說明見 [CLAUDE.md](CLAUDE.md)。

- PyPI: <https://pypi.org/project/ntu-cool-material/>
- GitHub: <https://github.com/jabir95tsai/get_class_material>
</details>
