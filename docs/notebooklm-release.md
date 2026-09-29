# 發布 NotebookLM 實驗版

此流程使用 `notebooklm-v<manifest版本>-alpha.<序號>` tag，與下載器的 `v*.*.*` PyPI 發布流程分開。不會自動發布至 PyPI 或瀏覽器商店。

## 本機驗證

```powershell
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -q
node --test tests/notebooklm_extension.test.mjs
python tools/build_notebooklm_extension.py
python -m build --wheel --outdir dist/notebooklm-companion
git diff --check
```

確認擴充套件與 companion 原始碼都已提交，且未加入教材、登入資料、`.secrets` 或本機設定檔。ZIP 使用固定檔案清單，只包含擴充套件執行檔、圖示、README 和 LICENSE。

## 發布

將已驗證的提交推送到 GitHub，再建立新的實驗版 tag。例如 manifest 為 `0.1.0` 時：

```powershell
git tag -a notebooklm-v0.1.0-alpha.1 -m "NotebookLM experimental preview"
git push origin notebooklm-v0.1.0-alpha.1
```

每次發布使用新序號，不移動舊 tag。`.github/workflows/notebooklm-release.yml` 會在 Windows 執行 Python／JavaScript 測試、建置 companion wheel 與擴充套件 ZIP，產生 SHA-256 清單，最後建立 **Pre-release**，且不設為 Latest。
發布依 [GitHub CLI release create](https://cli.github.com/manual/gh_release_create) 使用 `--verify-tag --prerelease --latest=false`。

Release 包含：

- `ntu-cool-to-notebooklm.zip`：解壓後載入 Chrome／Edge。
- `get_class_material-…-py3-none-any.whl`：同提交的 Native Messaging companion；即使 PyPI 尚未更新也能安裝。
- `INSTALL.md`：完整安裝步驟。
- `SHA256SUMS.txt`：前三個附件的 SHA-256。

Release 文字來自 `docs/notebooklm-release-notes.md`，必須保留「NotebookLM 實站上傳尚待驗證」；只有實際驗證後才能調整該聲明。發布後檢查 Actions 成功、Release 顯示 Pre-release、附件可下载並與 SHA256SUMS 相符，再依 INSTALL.md 在另一個環境測試安裝。

若 Release 已建立，工作流不覆寫既有附件；先檢查遠端狀態，修正後以新 tag 發布。自動化測試與 ZIP 建置成功只代表本機／CI 檢查，不代表 NotebookLM 真實匯入成功。
