# NTU COOL to NotebookLM 隱私權政策

最後更新：2026-09-14

本擴充套件的單一用途，是把使用者透過 `get-class-material` 在自己電腦下載並選定的課程教材，匯入使用者自己已登入的 NotebookLM 筆記本。

## 處理的資料

擴充套件會處理本次選定課程的名稱、教材檔名、教材內容、NotebookLM 筆記本名稱與網址，以及匯入進度。資料只在使用者的電腦、Chrome／Edge 與 NotebookLM 網頁之間流動。

擴充套件不會要求、讀取或保存 Google 密碼、Google 驗證碼或瀏覽器 cookie；不會把教材、瀏覽紀錄或使用統計傳送給本專案維護者；不包含廣告或追蹤器，也不會販售資料。

## 資料使用與分享

上述資料只用於使用者主動要求的教材匯入。教材會由使用者的瀏覽器上傳至 Google NotebookLM，因此 Google 對該服務的資料處理另受 Google 的服務條款與隱私權政策規範。本專案不另行接收或保存副本。

## 本機儲存與刪除

本機下載程式會保存教材與匯入紀錄，用於續傳及避免重複匯入。擴充套件只保存操作中必要的短期狀態。使用者可刪除課程資料夾、其中的 `.notebooklm-import.json`，以及移除擴充套件來清除這些資料。

## 權限用途

- `scripting`：在使用者開啟的 NotebookLM 頁面執行選取筆記本及上傳操作。
- `storage`：保存操作中斷保護與短期狀態，避免重新整理後重複建立或上傳。
- `nativeMessaging`：與同一台電腦上的 `get-class-material` 交換本次選定教材。
- NotebookLM 網域權限：只操作 `notebooklm.google.com` 與相容舊網域 `notebook.google.com`。

## 聯絡方式

問題可透過專案的 GitHub Issues 提出：<https://github.com/jabir95tsai/get_class_material/issues>

