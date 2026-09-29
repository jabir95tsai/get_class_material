# NTU COOL to NotebookLM

這個 Chrome／Edge 擴充套件搭配 `get-class-material`，把使用者在自己電腦選定的課程教材匯入已登入的 NotebookLM。使用者不需要逐檔選取、輸入連線埠或貼上配對金鑰。

**實驗版（Pre-release）：尚未上架 Chrome／Edge 商店，NotebookLM 實站上傳仍待驗證。**
自動測試不代表真實登入、上傳或中斷恢復已驗證。先用少量、有權上傳的教材試用。

## Windows 雙擊安裝（完整原始碼資料夾）

1. 雙擊專案根目錄的 `Install-NotebookLM.cmd`。需已安裝 Python 3.11+，首次會從套件來源下載依賴，不需要管理員權限。
2. 安裝完成會開啟 Chrome 擴充功能頁與資料夾。啟用「開發人員模式」，按「載入未封裝項目」，選取 `%USERPROFILE%\.ntu-cool-gcm\notebooklm\extension`。
3. 雙擊專案根目錄的 `Start-NotebookLM.cmd`，選課後保持視窗開啟，再到 NotebookLM 擴充套件確認匯入。

安裝程式將獨立 Python 環境與擴充套件放在 `%USERPROFILE%\.ntu-cool-gcm\notebooklm`，沿用既有 Native Host 在目前使用者的 Chrome／Edge 註冊方式，不讀取登入資料。重跑安裝即可更新本機版本，完成後在 Chrome 按「重新載入」。如果以前載入的是沒有固定 key 的版本，請先移除舊擴充套件，再載入新的資料夾；ID 改變後舊擴充套件的暫存設定不會自動遷移。更新時先結束進行中的匯入。

固定 key 是公開金鑰，只用於維持開發版 ID，不是登入憑證，也不代表商店認證。未來上架時須改用商店指派的公開金鑰並重新登記 Host。[Chrome 官方說明](https://developer.chrome.com/docs/extensions/reference/manifest/key)。此雙擊流程需完整專案，單獨的擴充套件 ZIP 仍使用以下手動流程。

## 下載與安裝（Windows Chrome／Edge）

1. 到 [GitHub Releases](https://github.com/jabir95tsai/get_class_material/releases) 選擇標示 **NotebookLM／Pre-release** 的版本。
   在 Assets 下載 `ntu-cool-to-notebooklm.zip` 與同一個 Release 的 `get_class_material-…-py3-none-any.whl`。
   若尚無該 Release，代表維護者尚未發布；請使用下方原始碼開發方式。
   GitHub 自動產生的「Source code (zip)」不是可直接載入的擴充套件。
2. 安裝 Python 3.11+，在 PowerShell 安裝下載的 wheel（將路徑換成實際檔案）：

   ```powershell
   python -m pip install --force-reinstall "C:\Users\你的帳號\Downloads\get_class_material-0.2.22-py3-none-any.whl"
   ```

   companion 必須與 Release 搭配；不要只裝可能尚未包含 Native Messaging host 的 PyPI 舊版。
3. 將 ZIP **解壓縮到固定位置**，例如 `Documents\ntu-cool-notebooklm`。不要直接載入 ZIP，也不要在安裝後刪除或移動資料夾。
4. 開啟 `chrome://extensions` 或 `edge://extensions`，啟用「開發人員模式」，按「載入未封裝項目／Load unpacked」，選取內含 `manifest.json` 的資料夾。
5. 複製擴充套件頁顯示的 32 字元 ID，在 PowerShell 執行：

   ```powershell
   ntu-cool-notebooklm-host --install <擴充套件ID>
   ```

   將 `<擴充套件ID>` 整段（含角括號）換成你自己的 ID。新版包含固定 key；舊版或自行更換 key 的版本，請以瀏覽器實際顯示的 ID 為準。
   Windows 登記在目前使用者的 Chrome／Edge 設定，不需管理員權限。
6. 回到擴充套件頁按「重新載入」，執行：

   ```powershell
   ntu-cool-materials notebooklm --extension
   ```

7. 選擇本機課程，保持 PowerShell 執行中。於正常瀏覽器自行登入 NotebookLM，點擴充套件的「連接目前課程」，選擇新增或已有筆記本，核對來源數量後按「確認並開始自動匯入」。

也可執行 `ntu-cool-gcm`，在下載後選擇匯入。Google 密碼、驗證碼與 cookie 不會由擴充套件讀取或保存。

### 更新與連線排除

- 更新時安裝新 Release 的 wheel，將新 ZIP 解壓至原位置，重新載入擴充套件，並再次執行 `--install`。Python 環境改變後也需重新登記。
- 移動資料夾、換瀏覽器或重新載入未封裝套件後，重新確認 ID，再執行 `--install`。目前登記會取代先前的 ID；請以本次要使用的瀏覽器為準。
- 找不到 `ntu-cool-notebooklm-host` 指令時，可在安裝 wheel 的同一個 Python 環境執行 `python -m ntu_cool_materials.notebooklm_native_host --install <擴充套件ID>`；若仍提示找不到 host，將該 Python 的 Scripts 目錄加入 PATH，再重新登記。
- 「no_active_import」表示目前沒有執行中的匯入工作；先執行上述 CLI 並選課，再按連接。
- 無法啟動 Native host 時，確認 wheel 已安裝、ID 正確、Python 環境仍存在，重新登記並重新載入擴充套件。
- 組織管理的瀏覽器可能禁用開發人員模式；需遵循組織政策。
- 自動匯入無法使用時，執行 `ntu-cool-materials notebooklm --manual` 準備手動上傳資料夾。

## 原始碼開發

在專案根目錄執行 `python -m pip install -e ".[dev]"`，再執行 `python tools/build_notebooklm_extension.py`。
可載入 `extensions/notebooklm` 資料夾，依上方步驟登記你自己的 ID。
商店版目前尚未提供；取得固定 Item ID 後才能另行規劃免複製 ID 的安裝。
[Chrome Native Messaging 官方文件](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging) 規定 `allowed_origins` 必須列出確切 ID，不能使用萬用字元。

## 權限與資料範圍

- `scripting`：只在 NotebookLM 頁面執行筆記本選擇與檔案上傳。
- `storage`：保存短期命令狀態，避免重新整理後重複建立或上傳。
- `nativeMessaging`：與同一台電腦上的 `get-class-material` 溝通。
- 網域權限只有 `notebooklm.google.com` 與相容舊網域 `notebook.google.com`。

Native host 只接受 `/session`、`/command`、目前上傳檔案的分段路由、`/start`、`/reply` 與 `/stop`。它不接受任意檔案路徑。短效 session 只在 CLI 執行期間存在；完成或停止時會刪除。教材仍由既有 SHA-256 紀錄去重，結果不明時保留 `pending` 並停止，不會盲目重送。

Excel `.xlsx/.xls` 目前仍會下載，但不會直接匯入 NotebookLM。未來若加入轉換，需把工作表轉成可檢查的 PDF、Markdown 或 CSV，並在送出前顯示轉換結果。

## 建置 ZIP

在專案根目錄執行：

```powershell
python tools/build_notebooklm_extension.py
```

輸出為 `dist/ntu-cool-to-notebooklm.zip`，`manifest.json` 位於 ZIP 根目錄。可作為 GitHub 實驗版附件；商店初次送出前需先完成實站測試，再於 Chrome Web Store Developer Dashboard 建立草稿、取得 Item ID 與 public key，固定開發 ID 後重新建置。
