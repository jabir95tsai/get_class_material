// This function is injected only on NotebookLM, in the extension's isolated world.
// No Google sign-in automation, cookie access, private API calls or page-state RPC.
export async function pageOperation(op, args = {}) {
  // executeScript does not reliably reject its promise for errors thrown inside
  // the injected function. Keep the error transport inside this serialized function.
  async function operate() {
  const allowed = ["notebooklm.google.com", "notebook.google.com"];
  if (!allowed.includes(location.hostname) || location.protocol !== "https:") throw new Error("請先在正常瀏覽器完成 Google 登入，並開啟 NotebookLM。");
  const visible = e => Boolean(e && e.getClientRects().length);
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const wait = async (fn, timeout = 15000) => {
    const until = Date.now() + timeout;
    while (Date.now() < until) { const value = fn(); if (value) return value; await sleep(250); }
    throw new Error("NotebookLM 介面未就緒或已變動，請查看網頁後再試。");
  };
  const buttons = pattern => [...document.querySelectorAll('button,[role="button"]')].filter(e => visible(e) && !e.disabled && pattern.test(e.getAttribute("aria-label") || e.textContent));
  const click = pattern => { const b = buttons(pattern); if (b.length !== 1) throw new Error("找不到唯一的操作按鈕，已停止。"); b[0].click(); };
  const notebookURL = () => /^\/notebook\/[A-Za-z0-9_-]+\/?$/.test(location.pathname) ? location.origin + location.pathname.replace(/\/$/, "") : null;
  if (args.expectedURL && notebookURL() !== args.expectedURL) throw new Error("NotebookLM 分頁已切換到其他目標，已停止。");
  const titles = () => [...document.querySelectorAll(".single-source-container")].filter(visible);
  const readyTitle = row => {
    if (row.querySelector('[role="progressbar"],mat-progress-spinner,mat-spinner,[aria-busy="true"]')) return null;
    if ([...row.querySelectorAll("mat-icon")].some(i => /error|failed|progress_activity|pending|processing|uploading|錯誤|失敗|處理中|上傳中/i.test(i.textContent))) return null;
    const cb = row.querySelector('input[type="checkbox"],[role="checkbox"]');
    if (!cb || cb.disabled || cb.getAttribute("aria-disabled") === "true") return null;
    const title = row.querySelector(".source-title,.source-item-title");
    return title?.textContent.trim() || row.innerText.split("\n").map(t => t.trim()).find(t => / \[[0-9a-f]{12}\]\.[a-z0-9]+$/.test(t)) || null;
  };
  if (op === "location") return notebookURL();
  if (op === "scan") {
    const current = notebookURL();
    if (current) return [{title: document.title || "目前筆記本", url: current}];
    // The home page also contains featured and shared notebooks. Only enumerate
    // after the site's own ownership filter has finished rendering its heading.
    const mine = /^(我的筆記本|My notebooks)$/i;
    const ownListReady = () => [...document.querySelectorAll('h1,h2,h3,[role="heading"]')]
      .some(e => visible(e) && mine.test(e.textContent.trim()));
    if (!ownListReady()) {
      const filters = buttons(mine);
      if (filters.length !== 1) throw new Error("無法確認「我的筆記本」清單。請在 NotebookLM 開啟自己的目標筆記本後再掃描。");
      filters[0].click();
      await wait(ownListReady);
    }
    const found = [], seen = new Set();
    const clean = value => (value || "").replace(/\s+/g, " ").trim();
    const linkTitle = a => {
      // NotebookLM uses empty overlay links labelled by sibling title/emoji nodes.
      const labelled = (a.getAttribute("aria-labelledby") || "").split(/\s+/)
        .filter(Boolean).map(id => document.getElementById(id)?.textContent || "").join(" ");
      return clean(labelled) || clean(a.getAttribute("aria-label")) || clean(a.innerText)
        || clean(a.closest("project-button,.project-button-card")?.querySelector(".project-button-title")?.textContent);
    };
    for (const a of document.querySelectorAll('a[href*="/notebook/"]')) {
      if (a.closest(".featured-project-card")) continue;
      const u = new URL(a.href, location.href);
      if (visible(a) && allowed.includes(u.hostname) && /^\/notebook\/[A-Za-z0-9_-]+\/?$/.test(u.pathname)) {
        const url = u.origin + u.pathname.replace(/\/$/, "");
        if (!seen.has(url)) {
          const title = linkTitle(a) || `無法讀取名稱（${u.pathname.split("/").filter(Boolean).pop()}）`;
          found.push({title, url}); seen.add(url);
        }
      }
    }
    if (!found.length) [...document.querySelectorAll("project-button")].forEach((card,index) => {
      if (card.closest(".featured-project-card") || card.querySelector(".featured-project-card")) return;
      const title = card.querySelector(".project-button-title")?.textContent.trim();
      if (visible(card) && title) found.push({title, index});
    });
    return found;
  }
  if (op === "pick") {
    const card = document.querySelectorAll("project-button")[args.index];
    if (!visible(card) || card.querySelector(".project-button-title")?.textContent.trim() !== args.title) throw new Error("筆記本清單已變動，請重新掃描。");
    card.click(); return true;
  }
  if (op === "create") {
    if (notebookURL()) throw new Error("建立前請回到 NotebookLM 首頁。");
    click(/Create (new )?notebook|New notebook|建立.*筆記本|新增.*筆記本/i);
    return true;
  }
  if (op === "prepare") {
    await wait(() => notebookURL());
    // Dismiss only a source-upload dialog, never a consent or account prompt.
    const dialogs = [...document.querySelectorAll('[role="dialog"]')].filter(visible);
    for (const dialog of dialogs) {
      if (/Add sources|新增來源|加入來源/i.test(dialog.textContent)) {
        const close = [...dialog.querySelectorAll("button")].find(b => /close|關閉/i.test(b.getAttribute("aria-label") || b.textContent));
        if (close) close.click();
      }
    }
    if (args.created) {
      const names = [...document.querySelectorAll("input")].filter(e => /Notebook title|筆記本名稱|筆記本標題/i.test(e.getAttribute("aria-label") || ""));
      if (names.length === 1) {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(names[0],args.title);
        names[0].dispatchEvent(new Event("input",{bubbles:true})); names[0].dispatchEvent(new Event("change",{bubbles:true})); names[0].blur();
      }
    }
    globalThis.__ntuNotebookCreatedEmpty = Boolean(args.created);
    return notebookURL();
  }
  if (op === "ready" || op === "count") {
    const rows = titles();
    const empty = [...document.querySelectorAll("p,span,div")].some(e => visible(e) && /^(0 sources?|0 個來源|Saved sources will appear here)$/i.test(e.textContent.trim()));
    if (!rows.length && !empty && !globalThis.__ntuNotebookCreatedEmpty) throw new Error("無法可靠讀取來源清單，已停止以免重複上傳。");
    return op === "count" ? rows.length : rows.map(readyTitle).filter(Boolean);
  }
  if (op === "begin") {
    if (!notebookURL() || args.size > 200000000 || args.size <= 0) throw new Error("上傳目標或檔案大小不正確。");
    globalThis.__ntuUpload = {id:args.id, title:args.title, size:args.size, chunks:[], received:0, index:0};
    return true;
  }
  if (op === "chunk") {
    const upload = globalThis.__ntuUpload;
    if (!upload || upload.id !== args.id || upload.index !== args.index) throw new Error("檔案傳輸順序錯誤。");
    const raw = atob(args.data), bytes = Uint8Array.from(raw,c => c.charCodeAt(0));
    if (bytes.length > 393216 || upload.received + bytes.length > upload.size) throw new Error("檔案大小超出預期。");
    upload.chunks.push(bytes); upload.received += bytes.length; upload.index++;
    return true;
  }
  if (op === "discard") { delete globalThis.__ntuUpload; return true; }
  if (op === "submit") {
    const upload = globalThis.__ntuUpload;
    if (!upload || upload.id !== args.id || upload.received !== upload.size) throw new Error("教材尚未完整傳送。");
    // The current uploader exposes a drop zone, not a persistent file input.
    // Prefer the open dialog's zone; never dispatch to both dialog and sidebar.
    const uploadTarget = () => {
      const dialogs = [...document.querySelectorAll('[role="dialog"],mat-dialog-container')].filter(visible);
      const zones = [...new Set(dialogs.flatMap(d => [...d.querySelectorAll('[xapscottyuploaderdropzone]')]))].filter(visible);
      const candidates = zones.length ? zones : [...document.querySelectorAll('[xapscottyuploaderdropzone]')].filter(visible);
      if (candidates.length > 1) throw new Error("無法確認唯一的檔案拖放區。");
      if (candidates.length === 1) return {zone:candidates[0]};
      const inputs = [...document.querySelectorAll('input[type="file"]')];
      if (inputs.length > 1) throw new Error("無法確認檔案上傳欄位。");
      return inputs.length === 1 ? {input:inputs[0]} : null;
    };
    let target = uploadTarget();
    if (!target) { click(/Add sources?|新增來源|加入來源/i); target = await wait(uploadTarget); }
    const mime = {pdf:"application/pdf",txt:"text/plain",md:"text/markdown",csv:"text/csv",
      docx:"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      pptx:"application/vnd.openxmlformats-officedocument.presentationml.presentation",
      epub:"application/epub+zip",mp3:"audio/mpeg",wav:"audio/wav",m4a:"audio/mp4",mp4:"video/mp4",
      aac:"audio/aac",ogg:"audio/ogg",opus:"audio/opus"};
    const file = new File(upload.chunks,upload.title,{type:mime[upload.title.split(".").pop().toLowerCase()] || "application/octet-stream"});
    const transfer = new DataTransfer(); transfer.items.add(file);
    const title = upload.title;
    delete globalThis.__ntuUpload; globalThis.__ntuNotebookCreatedEmpty = false;
    if (target.zone) {
      for (const type of ["dragenter", "dragover", "drop"]) {
        target.zone.dispatchEvent(new DragEvent(type,{bubbles:true,cancelable:true,dataTransfer:transfer}));
      }
    } else {
      target.input.files = transfer.files;
      target.input.dispatchEvent(new Event("input",{bubbles:true}));
      target.input.dispatchEvent(new Event("change",{bubbles:true}));
    }
    // Dispatching a synthetic event is not an acknowledgement from the site.
    // Keep transport failure distinct from a source that exists but is processing.
    const submittedURL = notebookURL();
    const deadline = Date.now() + 180000;
    let observed = false;
    while (Date.now() < deadline) {
      if (notebookURL() !== submittedURL) throw new Error("等待上傳時筆記本已切換，結果未確認；請回原筆記本核對來源。");
      const rows = titles();
      if (rows.some(row => readyTitle(row) === title)) return true;
      const row = rows.find(row => row.querySelector(".source-title,.source-item-title")?.textContent.trim() === title
        || (row.innerText || "").split("\n").some(line => line.trim() === title));
      if (row) {
        observed = true;
        if ([...row.querySelectorAll("mat-icon")].some(icon => /^(error|error_outline|failed)$/i.test(icon.textContent.trim()))) {
          throw new Error("NotebookLM 已列出這個來源，但顯示處理失敗；請在來源清單查看原因，不會自動重送。");
        }
      }
      await sleep(250);
    }
    throw new Error(observed
      ? "來源曾出現在清單，但 3 分鐘內未確認可用；已保留 pending，請核對處理狀態，不會自動重送。"
      : "送出後 3 分鐘內未在來源清單找到檔案，無法確認網頁是否接收上傳。請使用 NotebookLM 的「新增來源 → 上傳檔案」；保留 pending，成功後重新匯入會自動核對，請勿直接刪除紀錄重試。");
  }
  throw new Error("Unsupported operation");
  }
  if (!args.returnEnvelope) return operate();
  try {
    return {ok:true, value:await operate()};
  } catch (error) {
    return {ok:false, error:error instanceof Error ? error.message : "NotebookLM 網頁操作失敗。"};
  }
}
