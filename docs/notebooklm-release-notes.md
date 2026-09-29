這是 **NotebookLM 擴充套件實驗版（Pre-release）**，尚未上架 Chrome／Edge 商店。
**NotebookLM 實站上傳尚待驗證**；自動測試通過不代表 Google 登入、真實上傳或去重已通過實站驗證。請先以少量、有權上傳的教材試用。

1. 在本頁 Assets 下載 `ntu-cool-to-notebooklm.zip`、`get_class_material-…-py3-none-any.whl` 與 `INSTALL.md`。不要把 GitHub 自動產生的 Source code ZIP 當作擴充套件 ZIP。
2. 安裝 Python 3.11+，以 `python -m pip install --force-reinstall "下載的 wheel 完整路徑"` 安裝本次 companion。PyPI 版本可能尚未包含此實驗功能。
3. 將擴充套件 ZIP 解壓至固定資料夾，開啟 `chrome://extensions` 或 `edge://extensions` 的開發人員模式，按「載入未封裝項目」，選取包含 `manifest.json` 的資料夾。
4. 複製此瀏覽器顯示的擴充套件 ID，執行 `ntu-cool-notebooklm-host --install <擴充套件ID>`（以實際 ID 取代整個佔位文字及角括號）。
5. 重新載入擴充套件，執行 `ntu-cool-materials notebooklm --extension`，保持終端機開啟。在正常瀏覽器自行登入 NotebookLM，再點擴充套件的「連接目前課程」，選筆記本並確認來源後開始。

完整更新及故障排除步驟見附件 `INSTALL.md`（ZIP 內也有 `README.md`）。附件提供 `SHA256SUMS.txt` 供下載完整性比對。ZIP 不含教材或登入資料。
