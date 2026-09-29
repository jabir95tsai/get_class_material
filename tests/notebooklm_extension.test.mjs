import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {pageOperation} from "../extensions/notebooklm/page.mjs";

test("manifest has no account, cookie, debugger or broad web access", async () => {
  const m=JSON.parse(await readFile(new URL("../extensions/notebooklm/manifest.json",import.meta.url)));
  assert.equal(m.manifest_version,3);
  assert.deepEqual(m.permissions,["scripting","storage","nativeMessaging"]);
  assert.deepEqual(m.host_permissions,["https://notebooklm.google.com/*","https://notebook.google.com/*"]);
  assert.equal(m.externally_connectable,undefined);
});

test("panel uses native messaging and contains no pairing controls", async () => {
  const panel=await readFile(new URL("../extensions/notebooklm/panel.mjs",import.meta.url),"utf8");
  const html=await readFile(new URL("../extensions/notebooklm/panel.html",import.meta.url),"utf8");
  assert.match(panel,/sendNativeMessage\(NATIVE_HOST/);
  assert.doesNotMatch(panel,/fetch\(|127\.0\.0\.1|pairing|Bearer/);
  assert.doesNotMatch(html,/配對金鑰|id="key"|id="port"/);
});

test("refuses account pages without reading DOM",async () => {
  globalThis.location={hostname:"accounts.google.com",protocol:"https:"};
  await assert.rejects(pageOperation("scan"),/登入/);
});

test("isolated chunk transfer preserves bytes, checks order and discards buffers",async () => {
  globalThis.location={hostname:"notebooklm.google.com",protocol:"https:",origin:"https://notebooklm.google.com",pathname:"/notebook/test"};
  const data=Buffer.from([0,1,2,127,255]);
  await pageOperation("begin",{id:"test",title:"notes.pdf",size:data.length});
  await assert.rejects(pageOperation("chunk",{id:"test",index:1,data:data.toString("base64")}),/順序/);
  await pageOperation("chunk",{id:"test",index:0,data:data.toString("base64")});
  assert.deepEqual([...globalThis.__ntuUpload.chunks[0]],[...data]);
  await pageOperation("discard");
  assert.equal(globalThis.__ntuUpload,undefined);
});

test("navigation to another notebook stops file operations",async () => {
  globalThis.location={hostname:"notebooklm.google.com",protocol:"https:",origin:"https://notebooklm.google.com",pathname:"/notebook/other"};
  await assert.rejects(pageOperation("begin",{id:"test",title:"notes.md",size:10,expectedURL:"https://notebooklm.google.com/notebook/expected"}),/其他目標/);
});

test("oversized or incomplete files cannot be submitted",async () => {
  globalThis.location={hostname:"notebooklm.google.com",protocol:"https:",origin:"https://notebooklm.google.com",pathname:"/notebook/test"};
  await assert.rejects(pageOperation("begin",{id:"x",title:"a",size:200000001}),/大小/);
  await pageOperation("begin",{id:"x",title:"a",size:10});
  await assert.rejects(pageOperation("submit",{id:"x"}),/完整/);
  await pageOperation("discard");
});

function scanDOM(links, labels = {}) {
  globalThis.location={hostname:"notebook.google.com",protocol:"https:",origin:"https://notebook.google.com",pathname:"/",href:"https://notebook.google.com/"};
  globalThis.document={
    querySelectorAll: selector => selector === 'a[href*="/notebook/"]' ? links
      : selector === 'h1,h2,h3,[role="heading"]' ? [{textContent:"我的筆記本",getClientRects:()=>[{}]}] : [],
    getElementById: id => labels[id] ? {textContent:labels[id]} : null,
  };
}
function notebookLink(id, {text="", attrs={}, cardTitle="", hidden=false, featured=false}={}) {
  return {href:`https://notebook.google.com/notebook/${id}`,innerText:text,
    getClientRects:()=>hidden ? [] : [{}],getAttribute:name=>attrs[name] || null,
    closest:selector=>selector === ".featured-project-card" ? (featured ? {} : null)
      : ({querySelector:()=>cardTitle ? {textContent:cardTitle} : null})};
}
test("scan resolves empty overlay links through sibling aria-labelledby in order",async()=>{
  scanDOM([notebookLink("course",{attrs:{"aria-labelledby":"missing title emoji","aria-label":"wrong"}})],{title:" 半導體\n 入門 ",emoji:"📟"});
  assert.deepEqual(await pageOperation("scan"),[{title:"半導體 入門 📟",url:"https://notebook.google.com/notebook/course"}]);
});
test("scan retains labelled, text and legacy card links with distinct unknown names",async()=>{
  scanDOM([
    notebookLink("a",{attrs:{"aria-labelledby":"missing","aria-label":"統計學"}}),
    notebookLink("b",{text:"投資學"}), notebookLink("c",{cardTitle:"舊版標題"}),
    notebookLink("d"),notebookLink("e"),notebookLink("a",{text:"duplicate"}),
    notebookLink("hidden",{text:"隱藏",hidden:true}),
  ]);
  const result=await pageOperation("scan");
  assert.deepEqual(result.map(n=>n.title),["統計學","投資學","舊版標題","無法讀取名稱（d）","無法讀取名稱（e）"]);
  assert.equal(new Set(result.map(n=>n.url)).size,5);
});

test("scan excludes featured notebooks even inside an owned-list layout",async()=>{
  scanDOM([notebookLink("featured",{text:"推薦",featured:true}),notebookLink("own",{text:"自己的課程"})]);
  assert.deepEqual((await pageOperation("scan")).map(n=>n.title),["自己的課程"]);
});
test("scan waits for My notebooks rather than enumerating the initial mixed homepage",async()=>{
  scanDOM([]);
  let selected=false;
  const filter={textContent:"我的筆記本",getAttribute:()=>null,getClientRects:()=>[{}],click:()=>{selected=true;}};
  document.querySelectorAll=selector=>{
    if(selector==='button,[role="button"]') return [filter];
    if(selector==='h1,h2,h3,[role="heading"]') return selected ? [{textContent:"我的筆記本",getClientRects:()=>[{}]}] : [];
    if(selector==='a[href*="/notebook/"]') {
      assert.equal(selected,true);
      return [notebookLink("own",{text:"自己的課程"})];
    }
    return [];
  };
  assert.equal((await pageOperation("scan"))[0].title,"自己的課程");
});
test("scan refuses to enumerate when ownership filter is unavailable",async()=>{
  scanDOM([]);
  document.querySelectorAll=selector=>{
    assert.notEqual(selector,'a[href*="/notebook/"]');
    return [];
  };
  await assert.rejects(pageOperation("scan"),/無法確認.*我的筆記本/);
});

test("injected operation serializes page errors instead of losing them at the Chrome boundary",async()=>{
  globalThis.location={hostname:"notebook.google.com",protocol:"https:",origin:"https://notebook.google.com",pathname:"/notebook/test"};
  delete globalThis.__ntuUpload;
  const reply=await pageOperation("submit",{id:"missing",returnEnvelope:true});
  assert.equal(reply.ok,false);
  assert.match(reply.error,/教材尚未完整傳送/);
  assert.deepEqual(await pageOperation("location",{returnEnvelope:true}),{ok:true,value:"https://notebook.google.com/notebook/test"});
});
test("panel rejects missing injection results and propagates explicit page failure",async()=>{
  const {runInNewContext}=await import("node:vm");
  const source=await readFile(new URL("../extensions/notebooklm/panel.mjs",import.meta.url),"utf8");
  let injected;
  const context={URL,Date,setTimeout,pageOperation,
    document:{getElementById:()=>({})},
    chrome:{tabs:{query:async()=>[{id:1,url:"https://notebook.google.com/notebook/test"}],get:async()=>({id:1,url:"https://notebook.google.com/notebook/test"})},
      scripting:{executeScript:async()=>[{result:injected}]}}};
  const invoke=runInNewContext(source.replace(/^import .*;\r?\n/,"")+"\npage",context);
  await assert.rejects(invoke("submit"),/沒有回傳有效結果/);
  injected={ok:false,error:"找不到唯一的操作按鈕，已停止。"};
  await assert.rejects(invoke("submit"),/找不到唯一/);
  injected={ok:true};
  await assert.rejects(invoke("submit"),/空結果/);
  injected={ok:true,value:[]};
  assert.deepEqual(await invoke("ready"),[]);
});

test("new uploader submits one file to the dialog drop zone without needing a file input",async()=>{
  globalThis.location={hostname:"notebook.google.com",protocol:"https:",origin:"https://notebook.google.com",pathname:"/notebook/test"};
  const title="announcement [123456789abc].md";
  let dropped=false;
  const events=[];
  const zone={getClientRects:()=>[{}],dispatchEvent:e=>{
    events.push(e.type);
    if(e.type==='drop') {assert.equal(e.dataTransfer.files[0].name,title); assert.equal(e.dataTransfer.files[0].size,3); dropped=true;}
  }};
  const sidebar={getClientRects:()=>[{}],dispatchEvent:()=>assert.fail('must not submit twice')};
  const dialog={getClientRects:()=>[{}],querySelectorAll:()=>[zone]};
  const row={getClientRects:()=>[{}],querySelectorAll:()=>[],querySelector:s=>s.includes('progressbar')?null:s.includes('checkbox')?{disabled:false,getAttribute:()=>null}:{textContent:title}};
  globalThis.document={querySelectorAll:s=>s.includes('mat-dialog-container')?[dialog]:s==='[xapscottyuploaderdropzone]'?[zone,sidebar]:s==='.single-source-container'&&dropped?[row]:[]};
  const originalTransfer=globalThis.DataTransfer, originalDrag=globalThis.DragEvent;
  globalThis.DataTransfer=class{files=[];items={add:f=>this.files.push(f)};};
  globalThis.DragEvent=class{constructor(type,options){this.type=type;Object.assign(this,options);}};
  try {
    await pageOperation('begin',{id:'upload',title,size:3});
    await pageOperation('chunk',{id:'upload',index:0,data:Buffer.from('abc').toString('base64')});
    assert.equal(await pageOperation('submit',{id:'upload'}),true);
    assert.deepEqual(events,['dragenter','dragover','drop']);
  } finally {globalThis.DataTransfer=originalTransfer;globalThis.DragEvent=originalDrag;}
});

test("operation log survives refresh, retains errors and stays bounded",async()=>{
  const {runInNewContext}=await import('node:vm');
  const source=(await readFile(new URL('../extensions/notebooklm/panel.mjs',import.meta.url),'utf8')).replace(/^import .*;\r?\n/,'');
  const saved=new Map();
  const storage={getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v)};
  function boot(){
    const elements={};
    const context={URL,Date,setTimeout,pageOperation,sessionStorage:storage,document:{getElementById:id=>elements[id]??=( {textContent:''} )}};
    return {elements,api:runInNewContext(source+'\n({log,status})',context)};
  }
  const first=boot();first.api.status('上傳欄位逾時');first.api.status('上傳欄位逾時');
  assert.equal(first.elements.log.textContent.match(/上傳欄位逾時/g).length,1);
  const second=boot();assert.match(second.elements.log.textContent,/上傳欄位逾時/);
  second.api.log('x'.repeat(17000));assert.equal(second.elements.log.textContent.length,16000);
});

test("ignored drops, processing sources and rejected sources never report success",async()=>{
  const originals={Date:globalThis.Date,setTimeout:globalThis.setTimeout,DataTransfer:globalThis.DataTransfer,DragEvent:globalThis.DragEvent,document:globalThis.document,location:globalThis.location};
  const title='announcement [123456789abc].md';
  let now=0, mode='ignored';
  const zone={getClientRects:()=>[{}],dispatchEvent:()=>true};
  const row={getClientRects:()=>[{}],innerText:title,
    querySelector:s=>s.includes('progressbar')?{}:{textContent:title},
    querySelectorAll:()=>mode==='rejected'?[{textContent:'error'}]:[]};
  globalThis.Date={now:()=>now};
  globalThis.setTimeout=fn=>{now+=10000;fn();};
  globalThis.DataTransfer=class{files=[];items={add:f=>this.files.push(f)};};
  globalThis.DragEvent=class{constructor(type,options){this.type=type;Object.assign(this,options);}};
  globalThis.location={hostname:'notebook.google.com',protocol:'https:',origin:'https://notebook.google.com',pathname:'/notebook/test'};
  globalThis.document={querySelectorAll:s=>s==='[xapscottyuploaderdropzone]'?[zone]:s==='.single-source-container'&&mode!=='ignored'?[row]:[]};
  try {
    for (const [scenario,message] of [['ignored',/未在來源清單找到檔案/],['processing',/來源曾出現在清單/],['rejected',/顯示處理失敗/]]) {
      mode=scenario;now=0;
      await pageOperation('begin',{id:'upload',title,size:3});
      await pageOperation('chunk',{id:'upload',index:0,data:Buffer.from('abc').toString('base64')});
      const reply=await pageOperation('submit',{id:'upload',returnEnvelope:true});
      assert.equal(reply.ok,false);assert.match(reply.error,message);
      assert.equal(globalThis.__ntuUpload,undefined);
    }
  } finally {Object.assign(globalThis,originals);}
});
