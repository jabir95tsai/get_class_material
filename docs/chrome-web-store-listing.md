# Chrome Web Store 上架資料草稿

## 名稱

NTU COOL to NotebookLM

## 簡短說明

將你在本機下載的 NTU COOL 課程教材，自動匯入自己已登入的 NotebookLM 筆記本。

## 單一用途

讓使用者選擇一門已下載的 NTU COOL 課程，並把支援的教材來源匯入使用者指定的 NotebookLM 筆記本。

## 詳細說明

搭配開源工具 `get-class-material` 使用。下載課程後，擴充套件會顯示候選來源數量，讓使用者選擇新增或已有的 NotebookLM 筆記本，並在最後確認後逐檔上傳。已確認的來源會記錄在本機，避免重複匯入。

本工具不是 Google 或國立臺灣大學的官方產品。不會要求或保存 Google 密碼、驗證碼或 cookie，也不會將教材或使用統計傳送給專案維護者。

## 權限理由

- `scripting`：只在 NotebookLM 頁面執行使用者要求的筆記本選擇與教材上傳。
- `storage`：保存短期命令狀態，避免頁面重新整理後重複執行上傳。
- `nativeMessaging`：讀取同一台電腦上由 `get-class-material` 明確選定的本次教材。
- `https://notebooklm.google.com/*`、`https://notebook.google.com/*`：限制網頁操作範圍在 NotebookLM。

## 審核測試步驟

1. 安裝最新版 `get-class-material` 與 Native Messaging host。
2. 執行 `ntu-cool-materials notebooklm --extension --course-dir <測試教材資料夾>`。
3. 點擊擴充套件並選「連接目前課程」。
4. 登入 NotebookLM，掃描並選擇測試筆記本。
5. 按「確認並開始自動匯入」，確認來源完成且重跑不會重複上傳。

## 尚待補齊

- Chrome Web Store 草稿 Item ID 與 public key。
- 隱私權政策的公開 HTTPS 網址。
- 1280×800 或 640×400 商店截圖。
- 實站測試帳號的審核操作說明（不得提供私人帳密）。
