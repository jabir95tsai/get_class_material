import {pageOperation} from "./page.mjs";

const $ = id => document.getElementById(id);
const NATIVE_HOST = "tw.edu.ntu.cool_notebooklm";
let session = null, tabId = null, expectedURL = null, nativeSequence = 0;
let notebooks = [], running = false, stopRequested = false;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const LOG_KEY = "ntu-notebooklm-operation-log";
// Tab-local diagnostics survive a refresh; never include tokens or file contents.
try { $("log").textContent = sessionStorage.getItem(LOG_KEY) || ""; } catch {}
const log = text => {
  $("log").textContent = `${$("log").textContent || ""}${new Date().toLocaleString()}　${text}\n`.slice(-16000);
  try { sessionStorage.setItem(LOG_KEY, $("log").textContent); } catch {}
};
const status = text => {
  if ($("status").textContent !== text) log(text);
  $("status").textContent = text;
};

function validURL(raw) {
  const u = new URL(raw);
  if (u.protocol !== "https:" || !["notebooklm.google.com","notebook.google.com"].includes(u.hostname) || u.port || u.username || u.password || !/^\/notebook\/[A-Za-z0-9_-]+\/?$/.test(u.pathname)) throw new Error("不是有效的 NotebookLM 筆記本網址。");
  return u.origin + u.pathname.replace(/\/$/, "");
}

async function api(path, data) {
  const id = `${Date.now()}-${++nativeSequence}`;
  const message = {id, path, data:data ? {...data,session_id:session?.session_id} : undefined};
  const response = await new Promise((resolve,reject) => {
    chrome.runtime.sendNativeMessage(NATIVE_HOST,message,result => {
      const error=chrome.runtime.lastError;
      if (error) reject(new Error("找不到本機橋接。請先安裝最新版 get-class-material。"));
      else resolve(result);
    });
  });
  if (!response || response.id !== id || !response.ok) {
    if (response?.error === "no_active_import") throw new Error("目前沒有等待匯入的課程；請先執行 ntu-cool-gcm。 ");
    throw new Error("無法連接目前課程；請確認終端機仍在執行。 ");
  }
  return response.result;
}

async function notebookTab(create = false) {
  if (tabId !== null) {
    try { const tab = await chrome.tabs.get(tabId); if (tab) return tab; } catch { tabId = null; }
  }
  const tabs = await chrome.tabs.query({url:["https://notebooklm.google.com/*","https://notebook.google.com/*"]});
  tabs.sort((a,b) => Number(b.active)-Number(a.active) || (b.lastAccessed || 0)-(a.lastAccessed || 0));
  const tab = tabs[0] || (create ? await chrome.tabs.create({url:"https://notebooklm.google.com/"}) : null);
  if (!tab) throw new Error("請先開啟 NotebookLM 並自行登入 Google。");
  tabId = tab.id; return tab;
}

async function page(op, args = {}) {
  if (stopRequested && !["discard"].includes(op)) throw new Error("已要求停止。");
    const tab = await notebookTab();
  const url = new URL(tab.url || "about:blank");
  if (!["notebooklm.google.com","notebook.google.com"].includes(url.hostname)) throw new Error("請自行完成 Google 登入，再回來掃描。");
  const result = await chrome.scripting.executeScript({target:{tabId}, world:"ISOLATED", func:pageOperation,
    args:[op,{...args,expectedURL:expectedURL || null,returnEnvelope:true}]});
  if (result.length !== 1) throw new Error("無法確認 NotebookLM 分頁。");
  const reply = result[0].result;
  if (!reply || typeof reply.ok !== "boolean") throw new Error("NotebookLM 網頁操作沒有回傳有效結果，已停止；請重新載入擴充套件後再試。");
  if (!reply.ok) throw new Error(reply.error || "NotebookLM 網頁操作失敗。");
  if (reply.value === undefined) throw new Error("NotebookLM 網頁操作回傳空結果，已停止。");
  return reply.value;
}

async function waitForNotebook() {
  const until = Date.now()+20000;
  while (Date.now()<until) {
    try { const url = await page("location"); if (url) return validURL(url); } catch (error) { if (stopRequested) throw error; }
    await pause(300);
  }
  throw new Error("筆記本未能開啟，請檢查網頁。");
}

async function navigate(url) {
  expectedURL = null;
  await notebookTab(true);
  await chrome.tabs.update(tabId,{url});
  const until = Date.now()+20000;
  while (Date.now()<until) {
    if (stopRequested) throw new Error("已要求停止。");
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete" && tab.url && new URL(tab.url).hostname.match(/^(notebooklm|notebook)\.google\.com$/)) return;
    await pause(300);
  }
  throw new Error("頁面未就緒；請在 NotebookLM 完成登入。");
}

async function execute(command) {
  const p = command.payload;
  if (command.operation === "open") {
    await navigate(p.url ? validURL(p.url) : "https://notebooklm.google.com/");
    if (!p.url) await page("create");
    expectedURL = await waitForNotebook();
    if (p.url && validURL(p.url).split("/").pop() !== expectedURL.split("/").pop()) throw new Error("開啟的筆記本與指定目標不同。");
    return page("prepare",{created:!p.url,title:p.title});
  }
  if (["ready","count"].includes(command.operation)) return page(command.operation);
  if (command.operation === "upload") {
    log(`上傳 ${p.title}`);
    await page("begin",{id:command.id,title:p.title,size:p.size});
    try {
      for (let index=0; index<Math.ceil(p.size/p.chunk_size); index++) {
        const chunk = await api(`/chunk/${command.id}/${index}`);
        await page("chunk",{id:command.id,index,data:chunk.data});
      }
      await page("submit",{id:command.id});
      log(`頁面已出現來源，等待本機再次核對：${p.title}`);
      return true;
    } finally { try { await page("discard"); } catch {} }
  }
  throw new Error("本機傳來不支援的操作。");
}

async function pump() {
  // The lock covers every extension work tab and prevents two upload consumers.
  await navigator.locks.request("ntu-notebooklm-import",{ifAvailable:true},async lock => {
    if (!lock) throw new Error("另一個擴充套件工作頁正在匯入。");
    while (!stopRequested) {
      const current = await api("/session");
      if (current.session_id !== session.session_id) throw new Error("本機課程已變更，請重新連接。");
      session.status = current.status;
      status(current.message);
      if (["complete","failed"].includes(current.status)) return;
      const {command} = await api("/command");
      if (!command) { await pause(500); continue; }
      const cacheKey = `command:${session.session_id}:${command.id}`;
      const previous = (await chrome.storage.session.get(cacheKey))[cacheKey];
      let response;
      if (previous?.response) {
        response = previous.response;  // Lost HTTP acknowledgement: resend only result.
      } else if (previous) {
        log("上次操作結果不明，已停止，請核對筆記本後重新執行本機指令。");
        response = {id:command.id,ok:false};
      } else {
        await chrome.storage.session.set({[cacheKey]:{started:true}});
        try { response = {id:command.id,ok:true,result:await execute(command)}; }
        catch (error) { log(error.message || "網頁操作失敗"); response = {id:command.id,ok:false}; }
        await chrome.storage.session.set({[cacheKey]:{response}});
      }
      await api("/reply",response);
      // Keep the response cached to prevent mutation replay after a tab reload.
      if (!response.ok) { status("操作未完成；請查看上方訊息與終端機。"); return; }
      await pause(100);
    }
  });
}

function controls(busy) {
  for (const id of ["connect","open","scan","target"]) $(id).disabled = busy;
  $("start").disabled = busy || !session || session.status !== "waiting";
  $("stop").disabled = !busy;
}

async function connect() {
  session = await api("/session");
  if (session.suggested_notebook_url) {
    notebooks=[{title:"指令指定的筆記本",url:validURL(session.suggested_notebook_url)}];
    $("target").replaceChildren(new Option("新增一本筆記本","new"),new Option("指令指定的筆記本","0"));
    $("target").value="0";
  }
  $("course").textContent = `${session.course} · ${session.sources} 個候選来源 · 略過 ${session.skipped}`;
  status(session.message); controls(false);
  if (session.status === "running") {
    running=true; stopRequested=false; controls(true);
    try { await pump(); } finally { running=false; controls(false); }
  }
}

$("connect").onclick = () => connect().catch(e => status(e.message));
$("open").onclick = () => notebookTab(true).then(tab => chrome.tabs.update(tab.id,{active:true})).catch(e => status(e.message));
$("scan").onclick = async () => {
  try {
    expectedURL=null; stopRequested=false; await notebookTab();
    notebooks=await page("scan");
    $("target").replaceChildren(new Option("新增一本筆記本","new"));
    notebooks.forEach((n,i) => $("target").add(new Option(n.title,String(i))));
    $("target").value="new";
    status(`已讀取 ${notebooks.length} 本筆記本（首頁僅掃描「我的筆記本」，或使用目前開啟的筆記本）。請選擇匯入目標。`);
  } catch(e) { status(e.message); }
};
$("start").onclick = async () => {
  if (running || !session) return;
  running=true; stopRequested=false; controls(true);
  try {
    let target=null;
    if ($("target").value !== "new") {
      const choice=notebooks[Number($("target").value)];
      if (!choice) throw new Error("請重新掃描筆記本。");
      if (choice.url) target=validURL(choice.url);
      else { expectedURL=null;await page("pick",choice);target=await waitForNotebook(); }
    } else {
      // Read the current page before requesting creation. A login page stops here.
      await page("scan");
    }
    await api("/start",{notebook_url:target,force_new:!target});
    session.status="running";
    await pump();
  } catch(e) { status(e.message || "匯入連線中斷，請查看終端機。"); }
  finally { running=false; controls(false); }
};
$("stop").onclick = async () => {
  stopRequested=true;
  try { await api("/stop",{}); } catch {}
  status("已要求停止。已送到 Google 的來源不會自動刪除；請核對結果。");
};
