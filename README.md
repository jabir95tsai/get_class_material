# NTU COOL Materials

一行指令把 NTU COOL 課程的講義、Page、上課影片全部抓回本機，幫你整理好餵給 AI 複習。

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

選號碼，程式自己登入 NTU COOL，把該課程所有週次的 **PDF**、**Page**、**YouTube**、**上課影片**全部抓下來。檔名都用人看得懂的中文。

---

## 安裝（只要一行）

先確認你有 **Python 3.11 以上**（沒有就到 <https://www.python.org/downloads/> 裝，安裝時記得勾「Add Python to PATH」），然後：

```powershell
pip install ntu-cool-material
```

就這樣。

---

## 第一次使用

```powershell
ntu-cool-gcm
```

第一次跑會自動幫你：

1. 偵測缺哪些東西（Chromium 瀏覽器、yt-dlp 等）
2. **自動下載安裝**少的部分
3. 跳出瀏覽器視窗讓你登入 NTU COOL（學號密碼）
4. 列出你的課程讓你選
5. 開始下載

> 如果還缺 Node.js 或 ffmpeg（下載 YouTube 影片用），程式會在 Windows 上嘗試用 `winget` 自動裝，Mac 上用 `brew`。如果都沒有就會顯示要你自己跑的指令。

跑完會告訴你檔案放哪：

```text
Done. Files saved to:
  C:\Users\you\ntu-cool-gcm_material\音樂、演化與大腦 Music, Evolution and the Brain (57544)
```

---

## 之後再跑

```powershell
ntu-cool-gcm
```

只要登入沒過期（一兩天內）就不用任何額外動作。已經抓過的檔案會自動跳過，只補新的。

如果登入過期了：

```powershell
ntu-cool-gcm --refresh-session
```

---

## YouTube cookie（不公開影片需要）

NTU 老師很多影片是 YouTube「不公開」，需要你的 Google 帳號才抓得到。第一次跑：

```powershell
youtube-cookies
```

跳出瀏覽器，登入你平常看 NTU COOL 影片的 Google 帳號，登入完關掉就好。

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
- **`.mp4`** → 影片（YouTube + NTU 上課影片）

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

**Q：自動安裝失敗？**

```powershell
ntu-cool-materials doctor --fix
```

doctor 會列出缺什麼，並再次嘗試自動修復。如果還是失敗，會印出該手動執行的指令給你複製貼上。

**Q：只想下載某些東西？**

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
pip install -e .
python -m playwright install chromium
python -m unittest discover -s tests
```

架構說明見 [CLAUDE.md](CLAUDE.md)。

- PyPI: <https://pypi.org/project/ntu-cool-material/>
- GitHub: <https://github.com/jabir95tsai/get_class_material>
</details>
