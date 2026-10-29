/* Fission Console - vanilla JS, no build step */
"use strict";
const $ = (sel, el=document) => el.querySelector(sel);
const $$ = (sel, el=document) => [...el.querySelectorAll(sel)];
const KINDS = ["functions","packages","environments","httptriggers","timetriggers"];
const LABELS = {functions:"Functions",packages:"Packages",environments:"Environments",
  httptriggers:"HTTP Triggers",timetriggers:"Timer Triggers"};

const BASE = (location.pathname.replace(/\/+$/, "") || "/console");
function buildOp(method, url, body){
  const [path, qs] = url.split("?");
  const q = new URLSearchParams(qs || "");
  const parts = path.split("/").filter(Boolean); // api, ...
  if (parts[1] === "overview") return {op:"overview"};
  if (parts[1] === "metrics") return {op:"metrics"};
  if (parts[1] === "config") return method === "GET" ? {op:"config_get"} : {op:"config_put", cfg:body};
  if (parts[1] === "invoke") return Object.assign({op:"invoke"}, body);
  if (parts[1] === "testdata") return method === "GET" ? {op:"testdata_get"} : {op:"testdata_put", data: body.data};
  if (parts[1] === "source-token") return {op:"source_token_put", name: body.name, token: body.token};
  if (parts[1] === "gitops") {
    const opm={tree:"gitops_tree", file:"gitops_file", status:"gitops_status", plan:"gitops_plan", apply:"gitops_apply", export:"gitops_export"};
    return Object.assign({op:opm[parts[2]]||"gitops_plan", name: body.name}, body);
  }
  if (parts[1] === "functions" && parts[4] === "logs")
    return {op:"logs", ns:parts[2], name:parts[3], pod:q.get("pod") || ""};
  const kind = parts[1];
  if (parts.length === 2) return method === "POST" ? {op:"create", kind, obj:body} : {op:"list", kind};
  if (parts.length === 4) {
    if (method === "PUT") return {op:"update", kind, ns:parts[2], name:parts[3], obj:body};
    if (method === "DELETE") return {op:"delete", kind, ns:parts[2], name:parts[3]};
    return {op:"get", kind, ns:parts[2], name:parts[3]};
  }
  throw new Error("unsupported endpoint " + url);
}
async function api(method, url, body) {
  const envelope = buildOp(method, url, body);
  const res = await fetch(BASE, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(envelope)});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.message || res.statusText || ("HTTP " + res.status));
  return data;
}
function toast(msg, err=false) {
  const el = document.createElement("div");
  el.className = "toast" + (err ? " err" : "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 3600);
}
function esc(s){return String(s??"").replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function modal(html){ $("#modal-body").innerHTML = html; $("#modal").showModal(); }
function closeModal(){ const m=$("#modal"); if(m.open) m.close(); }
$("#modal").addEventListener("click", e => { if (e.target === $("#modal")) closeModal(); });

function badge(kind, text){
  const map = {ok:"ok", err:"err", warn:"warn", build:"build", idle:"idle"};
  return `<span class="badge ${map[kind]||"idle"}">${esc(text)}</span>`;
}
function pkgBuildStatus(pkg){
  const c = pkg?.status?.buildstatus?.status || "none";
  if (c === "succeeded") return badge("ok","构建成功");
  if (c === "failed") return badge("err","构建失败");
  if (c === "running" || c === "pending") return badge("build","构建中");
  return badge("idle","无构建");
}
function hero(title, sub, meta){
  return `<div class="hero"><div><h1>${esc(title)}</h1><p class="sub">${esc(sub||"")}</p></div>
    <div class="meta">${meta||""}</div></div>`;
}
function skeletons(rows=5){ return `<div class="skeleton">${Array.from({length:rows},(_,i)=>`<div class="row" style="width:${92-i*11}%"></div>`).join("")}</div>`; }
function emptyBox(title, hint){ return `<div class="empty"><strong>${esc(title)}</strong>${esc(hint||"")}</div>`; }

/* ---------- routing ---------- */
const routes = {
  "": viewOverview, "overview": viewOverview,
  "functions": viewKind, "packages": viewKind, "environments": viewKind,
  "httptriggers": viewKind, "timetriggers": viewKind,
  "gitops": viewGitopsV2, "tester": viewTester,
};
const CRUMBS={overview:"总览",tester:"API 测试台",functions:"Functions",packages:"Packages",environments:"Environments",httptriggers:"HTTP Triggers",timetriggers:"Timer Triggers",gitops:"GitOps 同步"};
async function refreshTopbar(){
  try{
    const o=await api("GET","/api/overview");
    const bad=o.core.filter(x=>x.ready!==x.pods).length;
    const el=$("#top-cluster");
    el.className="pill"+(bad?" warn":"");
    el.innerHTML=`<span class="dot"></span>${bad?"FISSION DEGRADED":"6/6 NODES READY"}`;
  }catch(e){ $("#top-cluster").innerHTML='<span class="dot"></span>API 不可达'; $("#top-cluster").className="pill err"; }
  try{
    const cfg=await api("GET","/api/config"); const s=(cfg.sources||[])[0];
    const el=$("#top-source");
    if(s){ el.innerHTML=`<span class="dot"></span>GITHUB · ${esc((s.branch||"main").toUpperCase())}`; el.className="pill info"; }
    else { el.innerHTML='<span class="dot"></span>仓库未配置'; el.className="pill"; }
  }catch(e){}
}
function navigate(){ closeModal();
  const crumb=$("#crumb"); if(crumb) crumb.textContent=CRUMBS[(location.hash.replace(/^#\/?/,"").split("/")[0])||"overview"]||"总览";
  const h = location.hash.replace(/^#\/?/, "");
  const parts = h.split("/");
  $$("#nav a").forEach(a => a.classList.toggle("active", a.dataset.view === (parts[0]||"overview")));
  refreshTopbar();
  const main = $("#main");
  try {
    if (parts[0] === "function" && parts.length >= 3) return viewFunctionDetail(parts[1], parts.slice(2).join("/"));
    const view = routes[parts[0]] || viewOverview;
    view(parts[0] === "" || parts[0] === "overview" ? "overview" : parts[0]);
  } catch (e) { main.innerHTML = emptyBox("页面加载失败", e.message); }
}
window.addEventListener("hashchange", navigate);

/* ---------- overview ---------- */
async function viewOverview(){
  $("#main").innerHTML = `<h1>总览</h1><p class="subtitle">集群资源与 Fission 核心组件健康</p>` + skeletons();
  const d = await api("GET", "/api/overview");
  const cnt = d.counts;
  const coreRows = d.core.map(c => `<tr><td><span class="name">${esc(c.app)}</span></td>
    <td>${c.pods}</td><td>${c.ready === c.pods ? badge("ok","健康") : badge("err",`${c.ready}/${c.pods} 就绪`)}</td></tr>`).join("")
    || `<tr><td colspan="3">${emptyBox("无核心组件","fission 命名空间没有 Pod")}</td></tr>`;
  $("#main").innerHTML = hero("运行总览", "函数、触发器与核心组件的当前态势",
    `<b>${new Date().toLocaleDateString("zh-CN")}</b><br>15s 采样 · FISSION.IO/V1`) + `
    <div class="page-body">
      <div class="strip">${[["Functions",cnt.functions],["Packages",cnt.packages],["Environments",cnt.environments],["HTTP Triggers",cnt.httptriggers],["Timer Triggers",cnt.timetriggers]]
        .map(([k,v])=>`<div class="cell"><div class="k">${k}</div><div class="v">${v??0}</div></div>`).join("")}</div>
      <div class="board">
        <section><h2>资源观测 <span class="count">15s</span></h2><div id="obs" class="chart-board"></div></section>
        <aside><h2>核心组件 <span class="count">${d.core.length}</span></h2>
          <div class="table-wrap"><table><thead><tr><th>组件</th><th>Pods</th><th>状态</th></tr></thead><tbody>${coreRows}</tbody></table></div>
          <h2>运行提示</h2>
          <div class="signal"><span class="mark"></span><span class="t">采样器保持在线，图表每 15 秒追加数据点</span><span class="u">AUTO</span></div>
          <div class="signal"><span class="mark"></span><span class="t">GitOps 写回需人工触发，自动同步仅拉取</span><span class="u">SAFE</span></div>
          <h2>函数清单 <span class="count" id="fn-count">—</span></h2><div id="fn-list"><div class="skeleton"><div class="row"></div><div class="row" style="width:70%"></div></div></div>
        </aside>
      </div>
    </div>`;
  renderObservability();
  (async()=>{
    try{
      const [fns,trs]=await Promise.all([api("GET","/api/functions"),api("GET","/api/httptriggers")]);
      const rows=fns.items.map(f=>{
        const t=trs.items.filter(x=>fnRef(x.spec).split(":")[0]===f.metadata.name).map(x=>x.metadata.name);
        const ex=f.spec?.InvokeStrategy?.ExecutionStrategy?.ExecutorType||f.spec?.invokeStrategy?.executionStrategy?.executorType||"poolmgr";
        return `<tr><td><span class="name">${esc(f.metadata.name)}</span><div class="mono">${esc(f.metadata.namespace)} · ${esc(ex)}</div></td>
          <td>${t.length?t.map(x=>`<span class="chip">${esc(x)}</span>`).join(""):'<span class="mono">无触发器</span>'}</td></tr>`;
      }).join("");
      const fc=$("#fn-count"); if(fc) fc.textContent=fns.items.length;
      $("#fn-list").innerHTML=`<div class="table-wrap"><table><thead><tr><th>函数</th><th>触发器</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }catch(e){ $("#fn-list").innerHTML=emptyBox("函数列表不可用",e.message); }
  })();
}

/* ---------- kind list views ---------- */
const COLUMNS = {
  functions: fn => {
    const env = fn.spec?.environment?.name || "-";
    const pkg = fn.spec?.package?.packageref?.name || "-";
    const triggers = (fn._triggers||[]).map(t=>t.metadata.name);
    return `<td><span class="name" data-ns="${esc(fn.metadata.namespace)}" data-name="${esc(fn.metadata.name)}">${esc(fn.metadata.name)}</span>
      <div class="mono">${esc(fn.metadata.namespace)}</div></td>
      <td>${esc(env)}</td><td class="mono">${esc(pkg).slice(0,28)}${pkg.length>28?"…":""}</td>
      <td>${triggers.length ? triggers.map(t=>`<span class="chip">${esc(t)}</span>`).join("") : '<span class="badge idle">无触发器</span>'}</td>`;
  },
  packages: p => `<td><span class="mono">${esc(p.metadata.name)}</span><div class="mono">${esc(p.metadata.namespace)}</div></td>
    <td>${esc(p.spec?.environment?.name||"-")}</td><td>${pkgBuildStatus(p)}</td>`,
  environments: e => `<td><span class="name">${esc(e.metadata.name)}</span><div class="mono">${esc(e.metadata.namespace)}</div></td>
    <td class="mono">${esc(e.spec?.runtime?.image||"")}</td>
    <td>${badge("ok","就绪")}</td>`,
  httptriggers: t => { const s=t.spec||{}; const methods=(s.methods&&s.methods.length?s.methods:[s.method]).filter(Boolean).join(",");
    return `<td><span class="name">${esc(t.metadata.name)}</span><div class="mono">${esc(t.metadata.namespace)}</div></td>
    <td><span class="chip">${esc(methods||"GET")}</span></td><td class="mono">${esc((s.relativeUrl||s.relativeurl||s.prefix)||"")}</td>
    <td class="mono">${esc(fnRef(s))}</td>
    <td>${s.createingress ? badge("warn","已建 Ingress") : badge("idle","内部")}</td>`; },
  timetriggers: t => `<td><span class="name">${esc(t.metadata.name)}</span><div class="mono">${esc(t.metadata.namespace)}</div></td>
    <td class="mono">${esc(t.spec?.cron||"")}</td><td class="mono">${esc(fnRef(t.spec))}</td>`,
};
function fnRef(spec){
  const r = spec?.functionref || {};
  if (r.type === "function-weights")
    return Object.entries(r.functionweights||{}).map(([n,w])=>`${n}:${w}%`).join(" ");
  return r.name || "";
}
const TEMPLATES = {
  functions: ns => ({apiVersion:"fission.io/v1",kind:"Function",metadata:{name:"new-func",namespace:ns},
    spec:{environment:{name:"python",namespace:ns},package:{packageref:{name:"REPLACE-PACKAGE",namespace:ns}},
    invokeStrategy:{strategyType:"execution",executionStrategy:{executorType:"poolmgr",specializationTimeout:120}},
    functionTimeout:60,idletimeout:120,concurrency:500,requestsPerPod:1}}),
  packages: ns => ({apiVersion:"fission.io/v1",kind:"Package",metadata:{name:"new-pkg",namespace:ns},
    spec:{environment:{name:"python",namespace:ns},deployment:{literal:""}}}),
  environments: ns => ({apiVersion:"fission.io/v1",kind:"Environment",metadata:{name:"new-env",namespace:ns},
    spec:{version:2,runtime:{image:"fission/python-env:latest"},builder:{image:"fission/python-builder:latest"}}}),
  httptriggers: ns => ({apiVersion:"fission.io/v1",kind:"HTTPTrigger",metadata:{name:"new-trigger",namespace:ns},
    spec:{relativeUrl:"/new-func",methods:["GET"],functionref:{type:"function",name:"REPLACE"}}}),
  timetriggers: ns => ({apiVersion:"fission.io/v1",kind:"TimeTrigger",metadata:{name:"new-timer",namespace:ns},
    spec:{cron:"@every 1h",functionref:{type:"name",name:"REPLACE"}}}),
};
const FORMS = {
  httptriggers: [["name","名称","text","new-trigger"],["namespace","命名空间","text","fission"],
    ["relativeUrl","URL 路径","text","/new-func"],["methods","HTTP 方法(逗号分隔)","text","GET"],
    ["functionref.name","目标函数","text",""]],
  timetriggers: [["name","名称","text","new-timer"],["namespace","命名空间","text","fission"],
    ["cron","Cron 表达式","text","@every 1h"],["functionref.name","目标函数","text",""]],
  environments: [["name","名称","text","new-env"],["namespace","命名空间","text","fission"],
    ["spec.runtime.image","运行镜像","text","fission/python-env:latest"]],
};
function formToObj(meta, values){
  const obj = structuredClone(meta);
  for (const [path, val] of Object.entries(values)){
    if (!val) continue;
    const keys = path.split(".");
    let cur = obj;
    while (keys.length > 1){ cur = cur[keys[0]] = cur[keys[0]] || {}; keys.shift(); }
    cur[keys[0]] = keys[0]==="methods" ? val.split(",").map(s=>s.trim()).filter(Boolean) : val;
  }
  return obj;
}
let FUNCS = [], SELECTED_FN = null;
async function viewKind(kind){
  const main = $("#main");
  main.innerHTML = hero(LABELS[kind], "资源的声明、状态与操作", "<b>fission.io/v1</b><br>CRD 直连") + `<div class="page-body">${skeletons()}</div>`;
  let items = (await api("GET", `/api/${kind}`)).items;
  if (kind === "functions"){
    const trs = (await api("GET", "/api/httptriggers")).items;
    items.forEach(f => f._triggers = trs.filter(t => fnRef(t.spec).split(":")[0] === f.metadata.name));
    FUNCS = items;
    main.innerHTML = hero("Functions", "",
      `<b>${items.length} FUNCTIONS · 03 PACKAGES</b><br>fission.io/v1 · live`) + `
      <div class="page-body">
        <div class="master">
          <div class="master-list"><h2>资源清单 <span class="count">${items.length}</span></h2><div id="fn-rows"></div></div>
          <div class="inspector" id="fn-inspector">${emptyBox("选择一个函数","从左侧清单选择函数，查看日志、指标与操作。")}</div>
        </div>
      </div>`;
    const paint = (kw="") => {
      const list = items.filter(f => (f.metadata.name + f.metadata.namespace).toLowerCase().includes(kw.toLowerCase()));
      $("#fn-rows").innerHTML = list.map((f, i) => `
        <div class="mrow ${SELECTED_FN===f.metadata.name?"sel":""}" data-fn="${esc(f.metadata.name)}">
          <span class="num">${String(i+1).padStart(2,"0")}</span>
          <div><div class="nm">${esc(f.metadata.name)}</div><div class="mt">${esc(f.metadata.namespace)} · ${esc(f.spec?.environment?.name||"python")} · ${esc(f.spec?.InvokeStrategy?.ExecutionStrategy?.ExecutorType||f.spec?.invokeStrategy?.executionStrategy?.executorType||"poolmgr")}</div></div>
          <span class="st">READY</span>
        </div>`).join("") || emptyBox("无匹配函数","调整搜索关键词。");
      $$("#fn-rows [data-fn]").forEach(row => row.onclick = () => { SELECTED_FN = row.dataset.fn; paint(kw); selectFunction(items.find(f=>f.metadata.name===SELECTED_FN)); });
    };
    paint();
    const fs=$("#fn-search"); if(fs) fs.oninput = e => paint(e.target.value);
    const bn=$("#btn-new"); if(bn) bn.onclick = () => createResource("functions");
    if (items.length) { SELECTED_FN = items[0].metadata.name; paint(); selectFunction(items[0]); }
    return;
  }
  items.sort((a,b)=>(a.metadata.namespace+a.metadata.name).localeCompare(b.metadata.namespace+b.metadata.name));
  const render = (kw="") => {
    const f = items.filter(i => (i.metadata.name + i.metadata.namespace).toLowerCase().includes(kw.toLowerCase()));
    $("#list").innerHTML = !f.length && !kw ? emptyBox(`还没有 ${LABELS[kind]}`,"点击「新建」创建第一个资源。")
      : !f.length ? emptyBox("无匹配结果","调整搜索关键词。")
      : `<div class="table-wrap"><table><thead><tr><th>名称</th>
          ${kind==="packages"?"<th>环境</th><th>构建</th>":""}${kind==="environments"?"<th>运行镜像</th><th>状态</th>":""}
          ${kind==="httptriggers"?"<th>方法</th><th>URL</th><th>目标函数</th><th>对外</th>":""}${kind==="timetriggers"?"<th>Cron</th><th>目标函数</th>":""}
          <th style="width:140px">操作</th></tr></thead><tbody>
          ${f.map(i=>`<tr>${COLUMNS[kind](i)}<td><button class="btn small" data-edit="${esc(i.metadata.namespace)}|${esc(i.metadata.name)}">编辑</button>
            <button class="btn small danger" data-del="${esc(i.metadata.namespace)}|${esc(i.metadata.name)}">删除</button></td></tr>`).join("")}
          </tbody></table></div>`;
    $$("#list [data-edit]").forEach(b=>b.onclick=()=>editResource(kind,...b.dataset.edit.split("|")));
    $$("#list [data-del]").forEach(b=>b.onclick=()=>deleteResource(kind,...b.dataset.del.split("|")));
  };
  main.innerHTML = hero(LABELS[kind], "资源的声明、状态与操作", `<b>${items.length} ${LABELS[kind].toUpperCase()}</b><br>fission.io/v1 · CRD`) + `
    <div class="page-body"><div class="toolbar"><input type="search" id="search" placeholder="搜索名称或命名空间">
      <div class="spacer"></div><button class="btn primary" id="btn-new">新建 ${LABELS[kind]}</button></div><div id="list"></div></div>`;
  render();
  $("#search").oninput = e => render(e.target.value);
  $("#btn-new").onclick = () => createResource(kind);
}

async function selectFunction(fn){
  const el=$("#fn-inspector"); if(!el) return;
  const spec=fn.spec||{}; const ns=fn.metadata.namespace, name=fn.metadata.name;
  let met={};
  try{ const m=await api("GET","/api/metrics"); const last=(m.series||[]).slice(-1)[0]; met=(last&&last.fns&&last.fns[name])||{}; }catch(e){}
  const idx=Math.max(0,FUNCS.findIndex(f=>f.metadata.name===name));
  el.innerHTML=`
    <div class="insp-head"><div class="gate">${String(idx+1).padStart(2,"0")}</div>
      <div><h2>${esc(name)}</h2><div class="sub">${esc(ns)} / ${esc(spec.environment?.name||"python")} · ${esc(spec.package?.packageref?.name||"—")}</div></div>
      <div class="status">READY</div></div>
    <div class="tabs"><button class="active" data-ftab="logs">日志</button><button data-ftab="invoke">调用测试</button><button data-ftab="yaml">YAML</button><button data-ftab="meta">触发器</button></div>
    <div class="kpis">
      <div class="kpi"><div class="k">CPU</div><div class="v">${met.cpu_m??"—"}${met.cpu_m!=null?"m":""}</div></div>
      <div class="kpi"><div class="k">内存</div><div class="v">${met.mem_mi??"—"}${met.mem_mi!=null?"Mi":""}</div></div>
      <div class="kpi"><div class="k">副本</div><div class="v">${met.pods??"—"}</div></div>
      <div class="kpi"><div class="k">P99</div><div class="v">—</div></div>
    </div>
    <div id="tab-body"></div>
    <div class="actions"><button class="btn primary" id="insp-invoke">发起调用</button><button class="btn" id="insp-yaml">查看 YAML</button><button class="btn" id="insp-git">同步到仓库</button></div>`;
  const show=tab=>{
    $$("[data-ftab]").forEach(b=>b.classList.toggle("active",b.dataset.ftab===tab));
    if(tab==="logs") tabLogs(ns,name);
    else if(tab==="invoke") tabInvoke(ns,name,fn._triggers||[]);
    else if(tab==="yaml") tabYaml(fn);
    else $("#tab-body").innerHTML=`<div class="table-wrap"><table><thead><tr><th>触发器</th><th>URL</th><th>方法</th></tr></thead><tbody>
      ${(fn._triggers||[]).map(t=>`<tr><td class="mono">${esc(t.metadata.name)}</td><td class="mono">${esc(t.spec.relativeurl||t.spec.relativeUrl||t.spec.prefix||"")}</td><td>${esc((t.spec.methods||[t.spec.method]).filter(Boolean).join(", "))}</td></tr>`).join("")||"<tr><td colspan=3>无触发器</td></tr>"}</tbody></table></div>`;
  };
  $$("[data-ftab]").forEach(b=>b.onclick=()=>show(b.dataset.ftab));
  $("#insp-invoke").onclick=()=>{ location.hash="#/tester"; };
  $("#insp-yaml").onclick=()=>show("yaml");
  $("#insp-git").onclick=()=>{ location.hash="#/gitops"; };
  show("logs");
}
function createResource(kind){
  const ns = KINDS.includes(kind) ? "fission" : "fission";
  const form = FORMS[kind];
  const body = form
    ? `<div class="mode-switch"><button class="active" data-m="form">表单</button><button data-m="json">JSON</button></div>
       <div class="form-grid" id="form-area">${form.map(([path,label,type,ph])=>
         `<label>${label}<input data-path="${path}" type="${type}" placeholder="${esc(ph)}"></label>`).join("")}</div>
       <div id="json-area" style="display:none"><textarea id="json-input" rows="16" style="width:100%">${esc(JSON.stringify(TEMPLATES[kind](ns),null,2))}</textarea></div>`
    : `<textarea id="json-input" rows="16" style="width:100%">${esc(JSON.stringify(TEMPLATES[kind](ns),null,2))}</textarea>`;
  modal(`<div class="modal-pad"><h3>新建 ${LABELS[kind]}</h3>${body}
    <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:16px">
    <button class="btn" id="m-cancel">取消</button><button class="btn primary" id="m-save">创建</button></div></div>`);
  let useJson = !form;
  if (form) $$(".mode-switch button").forEach(b => b.onclick = () => {
    useJson = b.dataset.m === "json";
    $$(".mode-switch button").forEach(x=>x.classList.toggle("active", x===b));
    $("#form-area").style.display = useJson ? "none" : "";
    $("#json-area").style.display = useJson ? "" : "none";
    if (useJson){
      const values = {}; $$("#form-area input").forEach(i => values[i.dataset.path] = i.value);
      const merged = formToObj(TEMPLATES[kind](ns), values);
      $("#json-input").value = JSON.stringify(merged, null, 2);
    }
  });
  $("#m-cancel").onclick = closeModal;
  $("#m-save").onclick = async () => {
    let obj;
    if (useJson || !form){ obj = JSON.parse($("#json-input").value); }
    else {
      const values = {}; $$("#form-area input").forEach(i => values[i.dataset.path] = i.value);
      obj = formToObj(TEMPLATES[kind](ns), values);
    }
    try { await api("POST", `/api/${kind}`, obj); toast(`已创建 ${obj.metadata.name}`); closeModal(); navigate(); }
    catch(e){ toast("创建失败: " + e.message, true); }
  };
}
function editResource(kind, ns, name){
  api("GET", `/api/${kind}/${ns}/${name}`).then(obj => {
    modal(`<div class="modal-pad"><h3>编辑 ${esc(kind)}/${esc(name)}</h3>
      <textarea id="json-input" rows="18" style="width:100%">${esc(JSON.stringify(obj,null,2))}</textarea>
      <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:14px">
      <button class="btn" id="m-cancel">取消</button><button class="btn primary" id="m-save">保存(乐观锁)</button></div></div>`);
    $("#m-cancel").onclick = closeModal;
    $("#m-save").onclick = async () => {
      try { await api("PUT", `/api/${kind}/${ns}/${name}`, JSON.parse($("#json-input").value));
        toast("已保存"); closeModal(); navigate(); }
      catch(e){ toast("保存失败: " + e.message, true); }
    };
  });
}
function deleteResource(kind, ns, name){
  modal(`<div class="modal-pad"><h3>删除 ${esc(name)}</h3>
    <p style="color:var(--text-2)">此操作不可恢复。输入资源名称以确认:</p>
    <input type="text" id="confirm-name" placeholder="${esc(name)}" style="width:100%">
    <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:16px">
    <button class="btn" id="m-cancel">取消</button><button class="btn danger" id="m-del" disabled>删除</button></div></div>`);
  $("#confirm-name").oninput = e => $("#m-del").disabled = e.target.value !== name;
  $("#m-cancel").onclick = closeModal;
  $("#m-del").onclick = async () => {
    try { await api("DELETE", `/api/${kind}/${ns}/${name}`); toast(`已删除 ${name}`); closeModal(); navigate(); }
    catch(e){ toast("删除失败: " + e.message, true); }
  };
}

/* ---------- function detail ---------- */
async function viewFunctionDetail(ns, name){
  const main = $("#main");
  main.innerHTML = `<a class="back" href="#/functions">← 返回 Functions</a>` + skeletons();
  const fn = await api("GET", `/api/functions/${ns}/${name}`);
  const trs = (await api("GET", "/api/httptriggers")).items.filter(t => fnRef(t.spec).split(":")[0] === name);
  const spec = fn.spec || {};
  const triggerRow = trs.length
    ? `<tr><td>触发器</td><td>${trs.map(t=>`<span class="chip">${esc(t.metadata.name)} (${esc((t.spec.relativeUrl||t.spec.prefix||""))})</span>`).join(" ")}</td></tr>` : "";
  main.innerHTML = `
    <a class="back" href="#/functions" style="font-size:12px;color:var(--ink-3);text-decoration:none">← Functions</a>
    <div class="detail-head"><h1 style="margin:0">${esc(name)}</h1>
      <p class="subtitle" style="margin:6px 0 0">${esc(ns)} 命名空间 · 环境 ${esc(spec.environment?.name||"-")}</p></div>
    <dl class="kv">
      <dt>Package</dt><dd class="mono">${esc(spec.package?.packageref?.name||"-")}</dd>
      <dt>执行策略</dt><dd>${esc(spec.invokeStrategy?.executionStrategy?.executorType||"poolmgr")}
        · 超时 ${esc(spec.functionTimeout??"-")}s · 空闲回收 ${esc(spec.idletimeout??"-")}s</dd>
      <dt>并发</dt><dd>${esc(spec.concurrency??"-")} · 每Pod请求数 ${esc(spec.requestsPerPod??"-")}</dd>
      ${triggerRow}
    </dl>
    <div class="tabs">
      <button class="active" data-tab="logs">日志</button>
      <button data-tab="invoke">调用测试</button>
      <button data-tab="yaml">YAML</button>
    </div>
    <div id="tab-body"></div>`;
  let active = "logs";
  const body = () => {
    if (active === "logs") return tabLogs(ns, name);
    if (active === "invoke") return tabInvoke(ns, name, trs);
    return tabYaml(fn);
  };
  $$(".tabs button").forEach(b => b.onclick = () => {
    active = b.dataset.tab;
    $$(".tabs button").forEach(x=>x.classList.toggle("active", x===b));
    body();
  });
  body();
}
async function tabLogs(ns, name){
  const el = $("#tab-body");
  el.innerHTML = `<select id="log-pod" hidden><option value="">加载中…</option></select>
    <div class="log-box"><span class="muted">正在加载日志…</span></div>`;
  const load = async () => {
    const d = await api("GET", `/api/functions/${ns}/${name}/logs?pod=${$("#log-pod").value||""}`);
    const sel = $("#log-pod");
    if (!d.pods.length){
      el.innerHTML = `<div class="log-box"><span class="muted">该函数当前没有运行中的 Pod(poolmgr 已缩容)。调用一次函数后即可查看日志。</span></div>`;
      return;
    }
    if (sel.options.length !== d.pods.length){
      sel.innerHTML = d.pods.map(p=>`<option value="${esc(p.name)}">${esc(p.name)} · ${esc(p.phase)}${p.ready?"":" (未就绪)"}</option>`).join("");
    } else {
      sel.value = sel.value || d.pods[0].name;
    }
    const pod = sel.value || d.pods[0].name;
    const text = d.logs[pod] || "";
    el.querySelector(".log-box").textContent = text || "（暂无日志输出）";
  };
  $("#log-pod").onchange = load;
  await load();
}
function tabInvoke(ns, name, trs){
  if (!trs.length){
    $("#tab-body").innerHTML = emptyBox("没有可用的 HTTP Trigger",
      "先在 HTTP Triggers 页为该函数创建一个触发器,才能从控制台调用。");
    return;
  }
  $("#tab-body").innerHTML = `
    <div class="form-grid">
      <label>触发器<select id="inv-trigger">${trs.map(t=>`<option value="${esc(t.metadata.name)}">${esc(t.metadata.name)} (${esc(t.spec.relativeUrl||t.spec.prefix||"")})</option>`).join("")}</select></label>
      <label>HTTP 方法<select id="inv-method"><option>GET</option><option>POST</option><option>PUT</option><option>DELETE</option></select></label>
      <label class="full">Headers(JSON,可空)<input id="inv-headers" placeholder='{"X-Test":"1"}'></label>
      <label class="full">Body(文本,POST/PUT 时发送)<textarea id="inv-body" rows="5" style="width:100%"></textarea></label>
    </div>
    <div style="margin-top:14px"><button class="btn primary" id="inv-go">发起调用</button>
    <span id="inv-meta" style="margin-left:12px;color:var(--text-2)"></span></div>
    <div id="inv-result" style="margin-top:14px"></div>`;
  $("#inv-go").onclick = async () => {
    const btn = $("#inv-go"); btn.disabled = true; btn.textContent = "调用中…";
    $("#inv-result").innerHTML = "";
    let headers = {};
    if ($("#inv-headers").value.trim()){ try { headers = JSON.parse($("#inv-headers").value); }
      catch(e){ toast("Headers 不是合法 JSON", true); btn.disabled=false; btn.textContent="发起调用"; return; } }
    try {
      const r = await api("POST", "/api/invoke", {
        ns, trigger: $("#inv-trigger").value, method: $("#inv-method").value,
        headers, body: $("#inv-body").value || null });
      $("#inv-meta").textContent = `HTTP ${r.status} · ${r.latency_ms} ms`;
      $("#inv-result").innerHTML = `<div class="json-view">${esc(r.body || "(空响应)")}</div>`;
      if (r.status >= 400) toast(`调用返回 HTTP ${r.status}`, true);
    } catch(e){ toast("调用失败: " + e.message, true); }
    btn.disabled = false; btn.textContent = "发起调用";
  };
}
function tabYaml(fn){
  $("#tab-body").innerHTML = `<div class="json-view">${esc(JSON.stringify(fn,null,2))}</div>`;
}

/* ---------- API 测试台（Postman 轻量版） ---------- */
let TDATA = {collections: [], environments: []};
let TESTER = {method:"GET", trigger:"", headers:[], query:[], body:"", env:"", lastResp:null};
async function loadTestdata(){ TDATA = await api("GET", "/api/testdata"); }
async function saveTestdata(){ await api("PUT", "/api/testdata", {data: TDATA}); }

function envVars(){
  const env = TDATA.environments.find(e=>e.name===TESTER.env);
  const vars = {}; (env?env.vars:[]).forEach(v=>{ if(v.key) vars[v.key]=v.value; });
  return vars;
}
function applyVars(s){
  if (!s) return s;
  return String(s).replace(/\{\{\s*([\w.-]+)\s*\}\}/g, (m,k)=>envVars()[k] ?? m);
}
async function viewTester(preset){
  const main = $("#main");
  main.innerHTML = hero("API 测试台","构造请求、保存收藏、管理环境变量、查看完整响应","<b>REQUEST / RESPONSE</b><br>变量替换 · 历史重放") + `<div class="page-body"><div id="tester-root">${skeletons(4)}</div></div>`;
  await loadTestdata();
  const trs = (await api("GET", "/api/httptriggers")).items;
  if (!trs.length){
    $("#tester-root").innerHTML = emptyBox("没有可调用的触发器","请先在 HTTP Triggers 页面为函数创建触发器,再回来测试。");
    return;
  }
  if (preset?.trigger) TESTER.trigger = preset.trigger;
  if (!TESTER.trigger && trs.length) TESTER.trigger = trs[0].metadata.name;
  TESTER.headers = TESTER.headers.length ? TESTER.headers : [{k:"",v:""}];
  TESTER.query = TESTER.query.length ? TESTER.query : [{k:"",v:""}];
  renderTester(trs);
}
function kvRows(kind){
  return TESTER[kind].map((row,i)=>`<tr>
    <td><input data-kv="${kind}" data-i="${i}" data-f="k" value="${esc(row.k)}" placeholder="key" style="width:100%"></td>
    <td><input data-kv="${kind}" data-i="${i}" data-f="v" value="${esc(row.v)}" placeholder="value" style="width:100%"></td>
    <td><button class="btn small" data-kv-del="${kind}" data-i="${i}">×</button></td></tr>`).join("");
}
function renderTester(trs){
  $("#tester-root").innerHTML = `
    <div class="test-grid">
      <aside class="test-col">
        <div class="rail-title">收藏集</div>
        <div id="coll-list">${TDATA.collections.map((c,ci)=>`
          <div class="coll-name">${esc(c.name)}</div>
          ${c.requests.map((r,ri)=>`<div class="req-item">
            <span class="m">${esc(r.method)}</span>
            <button data-load-req="${ci}|${ri}">${esc(r.name)}</button>
            <button data-del-req="${ci}|${ri}" style="justify-self:end;color:var(--ink-3)">×</button></div>`).join("")}`).join("")
          || '<div style="color:var(--ink-3);font-size:12px">还没有收藏的请求</div>'}
        </div>
        <button class="btn small" id="add-coll" style="margin-top:10px">新建收藏集</button>
        <div class="rail-title">环境</div>
        <select id="env-sel" style="width:100%"><option value="">无环境</option>
          ${TDATA.environments.map(e=>`<option ${e.name===TESTER.env?"selected":""}>${esc(e.name)}</option>`).join("")}</select>
        <button class="btn small" id="edit-env" style="margin-top:6px">编辑变量</button>
      </aside>
      <section class="test-main">
        <div class="urlbar">
          <select id="req-method">${["GET","POST","PUT","PATCH","DELETE"].map(m=>`<option ${m===TESTER.method?"selected":""}>${m}</option>`).join("")}</select>
          <input id="req-trigger-input" list="trigger-list" value="${esc(TESTER.trigger)}" placeholder="选择触发器">
          <datalist id="trigger-list">${trs.map(t=>`<option value="${esc(t.metadata.name)}">${esc(t.spec.relativeurl||t.spec.relativeUrl||t.spec.prefix||"")}</option>`).join("")}</datalist>
          <button class="btn" id="req-send">发送</button>
        </div>
        <div style="display:flex;gap:18px;margin-top:8px;flex-wrap:wrap">
          <span class="mono">${trs.find(t=>t.metadata.name===TESTER.trigger)?.spec?.relativeurl||""}</span>
          <span class="mono" style="color:var(--ink-3)">变量用 {{name}} 引用环境值</span>
        </div>
        <h2>Query 参数</h2>
        <table class="kv-table"><tbody id="query-rows">${kvRows("query")}</tbody></table>
        <button class="btn small" data-kv-add="query" style="margin-top:6px">+ 参数</button>
        <h2>Headers</h2>
        <table class="kv-table"><tbody id="header-rows">${kvRows("headers")}</tbody></table>
        <button class="btn small" data-kv-add="headers" style="margin-top:6px">+ Header</button>
        <h2>Body</h2>
        <textarea id="req-body" rows="8" style="width:100%" placeholder='{"plan":"pro","seats":25}'>${esc(TESTER.body)}</textarea>
        <div style="display:flex;gap:8px;margin-top:8px">
          <button class="btn" id="body-pretty">格式化 JSON</button>
          <button class="btn" id="save-req">保存到收藏</button>
        </div>
      </section>
      <aside class="test-side">
        <div class="rail-title" style="margin-top:0">响应</div>
        <div id="resp-root">${TESTER.lastResp ? "" : '<div class="empty" style="padding:28px 8px"><strong>等待请求</strong>发送后在此显示状态、耗时与响应体</div>'}</div>
        <div class="rail-title">历史</div>
        <div id="hist-root"></div>
      </aside>
    </div>`;
  bindTester(trs);
}
function bindTester(trs){
  const root = $("#tester-root");
  root.oninput = e => {
    if (e.target.dataset.kv){ TESTER[e.target.dataset.kv][+e.target.dataset.i][e.target.dataset.f] = e.target.value; }
    if (e.target.id === "req-body") TESTER.body = e.target.value;
    if (e.target.id === "req-method") TESTER.method = e.target.value;
    if (e.target.id === "req-trigger-input") TESTER.trigger = e.target.value;
    if (e.target.id === "env-sel") TESTER.env = e.target.value;
  };
  root.onclick = async e => {
    const t = e.target;
    if (t.dataset.kvAdd){ TESTER[t.dataset.kvAdd].push({k:"",v:""}); renderTester(trs); }
    if (t.dataset.kvDel){ TESTER[t.dataset.kvDel].splice(+t.dataset.i,1); renderTester(trs); }
    if (t.id === "body-pretty"){ try{ TESTER.body = JSON.stringify(JSON.parse(TESTER.body),null,2); $("#req-body").value = TESTER.body; }catch(err){ toast("Body 不是合法 JSON", true); } }
    if (t.id === "req-send") await sendTest();
    if (t.id === "save-req") saveRequest();
    if (t.dataset.loadReq){ const [ci,ri]=t.dataset.loadReq.split("|").map(Number); Object.assign(TESTER, TDATA.collections[ci].requests[ri]); renderTester(trs); }
    if (t.dataset.delReq){ const [ci,ri]=t.dataset.delReq.split("|").map(Number); TDATA.collections[ci].requests.splice(ri,1); await saveTestdata(); renderTester(trs); }
    if (t.id === "add-coll"){ const name=prompt("收藏集名称"); if(name){ TDATA.collections.push({name,requests:[]}); await saveTestdata(); renderTester(trs); } }
    if (t.id === "edit-env") editEnvironment();
  };
  renderHistory();
}
function headersObj(){
  const out={}; TESTER.headers.forEach(h=>{ if(h.k) out[applyVars(h.k)] = applyVars(h.v); }); return out;
}
function queryObj(){
  const out={}; TESTER.query.forEach(q=>{ if(q.k) out[applyVars(q.k)] = applyVars(q.v); }); return out;
}
async function sendTest(){
  const btn=$("#req-send"); btn.disabled=true; btn.textContent="发送中…";
  let body = applyVars(TESTER.body) || null;
  if (body && ["POST","PUT","PATCH"].includes(TESTER.method)){
    try{ JSON.parse(body); }catch(e){ /* treat as raw text */ }
  }
  const started=performance.now();
  try{
    const r = await api("POST", "/api/invoke", {ns:"fission", trigger:TESTER.trigger, method:TESTER.method,
      headers:headersObj(), query:queryObj(), body});
    TESTER.lastResp = r;
    try { renderResponse(r, Math.round(performance.now()-started)); }
    catch(err){ console.error(err); toast("响应渲染失败: " + err.message, true); }
    pushHistory({ts:Date.now(), method:TESTER.method, trigger:TESTER.trigger, status:r.status, latency:r.latency_ms,
      req:{headers:headersObj(), query:queryObj(), body}, resp:r});
  }catch(e){ toast("发送失败: "+e.message, true); TESTER.lastResp=null; }
  btn.disabled=false; btn.textContent="发送";
}
function renderResponse(r, wall){
  const ok = r.status < 400;
  const size = new Blob([r.body||""]).size;
  $("#resp-root").innerHTML = `
    <div class="resp-head">
      <span class="resp-code ${ok?"":"bad"}">HTTP ${r.status}</span>
      <span class="resp-meta">${r.latency_ms} ms · ${size} B · 往返 ${wall} ms</span>
    </div>
    <div class="resp-tabs">
      <button class="active" data-resp-tab="body">Body</button><button data-resp-tab="headers">Headers</button>
    </div>
    <div id="resp-pane"></div>`;
  const bodyPane = () => {
    let pretty = r.body || "";
    try { pretty = JSON.stringify(JSON.parse(pretty), null, 2); } catch(e){}
    $("#resp-pane").innerHTML = `<div style="display:flex;gap:6px;margin-bottom:6px">
      <button class="btn small" data-resp-mode="pretty">Pretty</button>
      <button class="btn small" data-resp-mode="raw">Raw</button>
      <button class="btn small" id="copy-resp">复制</button></div>
      <pre class="resp-body" id="resp-body">${esc(pretty)}</pre>`;
    $$("#resp-pane [data-resp-mode]").forEach(b=>b.onclick=()=>{
      $$("#resp-pane [data-resp-mode]").forEach(x=>x.classList.toggle("active", x===b));
      $("#resp-body").textContent = b.dataset.respMode==="pretty" ? pretty : (r.body||"");
    });
    $("#copy-resp").onclick = () => { navigator.clipboard?.writeText(r.body||""); toast("已复制响应"); };
  };
  bodyPane();
  $$("[data-resp-tab]").forEach(b=>b.onclick=()=>{
    $$("[data-resp-tab]").forEach(x=>x.classList.toggle("active",x===b));
    if(b.dataset.respTab==="body") bodyPane();
    else $("#resp-pane").innerHTML = `<table><thead><tr><th>Header</th><th>Value</th></tr></thead><tbody>${Object.entries(r.headers||{}).map(([k,v])=>`<tr><td class="mono">${esc(k)}</td><td class="mono">${esc(v)}</td></tr>`).join("")}</tbody></table>`;
  });
}
function saveRequest(){
  const name = prompt("请求名称");
  if(!name) return;
  let coll = TDATA.collections[0];
  if(!coll){ coll={name:"默认收藏集",requests:[]}; TDATA.collections.push(coll); }
  coll.requests.push({name, method:TESTER.method, trigger:TESTER.trigger,
    headers:TESTER.headers.filter(h=>h.k), query:TESTER.query.filter(q=>q.k), body:TESTER.body, env:TESTER.env});
  saveTestdata().then(()=>{toast("已保存"); viewTester();});
}
function editEnvironment(){
  const name = prompt("环境名称(留空创建新环境)", TESTER.env);
  if(name===null) return;
  const env = TDATA.environments.find(e=>e.name===name) || (TDATA.environments.push({name,vars:[]}), TDATA.environments[TDATA.environments.length-1]);
  const text = env.vars.map(v=>`${v.key}=${v.value}`).join("\n");
  const next = prompt("每行 KEY=VALUE", text);
  if(next===null) return;
  env.vars = next.split("\n").map(l=>{const i=l.indexOf("="); return i<0?null:{key:l.slice(0,i).trim(), value:l.slice(i+1)};}).filter(Boolean);
  TESTER.env = name;
  saveTestdata().then(()=>{toast("环境已保存"); viewTester();});
}
function pushHistory(item){
  const h = JSON.parse(localStorage.getItem("fc-history")||"[]");
  h.unshift(item); localStorage.setItem("fc-history", JSON.stringify(h.slice(0,50))); renderHistory();
}
function renderHistory(){
  const el=$("#hist-root"); if(!el) return;
  const h=JSON.parse(localStorage.getItem("fc-history")||"[]");
  el.innerHTML = h.length ? `<div class="table-wrap"><table><thead><tr><th>时间</th><th>方法</th><th>触发器</th><th>状态</th><th>耗时</th><th></th></tr></thead><tbody>
    ${h.map((x,i)=>`<tr><td>${new Date(x.ts).toLocaleTimeString()}</td><td>${esc(x.method)}</td><td class="mono">${esc(x.trigger)}</td>
      <td>${badge(x.status<400?"ok":"err",x.status)}</td><td>${x.latency} ms</td>
      <td><button class="btn small" data-history="${i}">重放</button></td></tr>`).join("")}</tbody></table></div>`
    : emptyBox("暂无历史记录","发送一次请求后会出现在这里。");
  $$("[data-history]").forEach(b=>b.onclick=()=>{
    const x=h[+b.dataset.history];
    TESTER.method=x.method; TESTER.trigger=x.trigger;
    TESTER.headers=Object.entries(x.req.headers||{}).map(([k,v])=>({k,v}));
    TESTER.query=Object.entries(x.req.query||{}).map(([k,v])=>({k,v}));
    TESTER.body=x.req.body||"";
    viewTester();
  });
}

/* ---------- GitOps v2 ---------- */
async function viewGitopsV2(){
  $("#main").innerHTML = hero("GitOps 双向同步","仓库是事实源，集群是运行态；两边都可以发起变更","<b>PULL / PUSH</b><br>人工确认后生效") + `<div class="page-body">
    <div class="pipeline">
      <div class="stage"><div class="n">Source</div><div class="d">GitHub 仓库</div><div class="s">YAML 清单</div></div>
      <div class="stage"><div class="n">Plan</div><div class="d">差异投影</div><div class="s">create / update / delete</div></div>
      <div class="stage"><div class="n">Apply</div><div class="d">同步集群</div><div class="s">归属标签保护</div></div>
      <div class="stage"><div class="n">Writeback</div><div class="d">PR 或直推</div><div class="s">人工触发</div></div>
    </div>
    <div class="toolbar"><button class="btn primary" id="btn-add-src">添加仓库源</button></div><div id="src-list">${skeletons()}</div><div id="gitops-summary"></div></div>`;
  const cfg = await api("GET","/api/config");
  if (!cfg.sources.length){
    $("#src-list").innerHTML = emptyBox("还没有仓库源","点击「添加仓库源」连接一个包含 Fission 清单的 GitHub 仓库。");
  } else {
    $("#src-list").innerHTML = `<div style="border-top:2px solid var(--ink)">${cfg.sources.map(s=>`
      <div class="source-row">
        <div><div class="nm">${esc(s.name)}</div><div class="mono">${s.writeMode==="direct"?"DIRECT PUSH":"PULL REQUEST"}</div></div>
        <div class="mono">${esc(s.repo)}<br>${esc(s.branch||"main")}${s.path?" / "+esc(s.path):""}</div>
        <div>${s.writeMode==="direct"?badge("warn","直推"):badge("ok","PR")}</div>
        <div>${s.autoMinutes?badge("build","每 "+s.autoMinutes+" 分"):badge("idle","手动")}</div>
        <div style="display:flex;gap:4px;justify-content:flex-end;flex-wrap:wrap">
          <button class="btn small" data-browse="${esc(s.name)}">浏览</button>
          <button class="btn small" data-plan2="${esc(s.name)}">差异</button>
          <button class="btn small primary" data-export2="${esc(s.name)}">写回</button>
          <button class="btn small" data-edit-src2="${esc(s.name)}">编辑</button>
          <button class="btn small danger" data-del-src2="${esc(s.name)}">删除</button>
        </div>
      </div>`).join("")}</div>`;
  }
  $("#btn-add-src").onclick = () => editSource2(null, null);
  $$("[data-edit-src2]").forEach(b=>b.onclick=()=>editSource2(b.dataset.editSrc2, cfg.sources.find(s=>s.name===b.dataset.editSrc2)));
  $$("[data-del-src2]").forEach(b=>b.onclick=async()=>{
    if(!confirm(`删除仓库源 ${b.dataset.delSrc2}?`)) return;
    cfg.sources = cfg.sources.filter(s=>s.name!==b.dataset.delSrc2);
    await api("PUT","/api/config",cfg); toast("已删除"); viewGitopsV2();
  });
  $$("[data-browse]").forEach(b=>b.onclick=()=>browseRepo(b.dataset.browse));
  $$("[data-plan2]").forEach(b=>b.onclick=()=>planV2(b.dataset.plan2));
  $$("[data-export2]").forEach(b=>b.onclick=()=>exportV2(b.dataset.export2));
  const first=(cfg.sources||[])[0];
  if(first) loadGitopsSummary(first.name);
}
async function loadGitopsSummary(name){
  const el=$("#gitops-summary"); if(!el) return;
  el.innerHTML=`<h2>仓库态势</h2>${skeletons(3)}`;
  try{
    const [tree,plan]=await Promise.all([
      api("POST","/api/gitops/tree",{name}),
      api("POST","/api/gitops/plan",{name})
    ]);
    const changes=(plan.create||[]).length+(plan.update||[]).length;
    const states={};
    (tree.resources||[]).forEach(r=>states[r.state]=(states[r.state]||0)+1);
    el.innerHTML=`<h2>仓库态势 · ${esc(tree.repo)}@${esc(tree.branch)}</h2>
      <div class="strip">
        <div class="cell"><div class="k">仓库文件</div><div class="v">${tree.files.length}</div></div>
        <div class="cell"><div class="k">解析资源</div><div class="v">${tree.resources.length}</div></div>
        <div class="cell"><div class="k">待应用</div><div class="v">${changes}</div></div>
        <div class="cell"><div class="k">删除候选</div><div class="v">${(plan.delete||[]).length}</div></div>
        <div class="cell"><div class="k">冲突</div><div class="v">${(plan.conflict||[]).length}</div></div>
      </div>
      <h2>资源状态</h2>
      ${tree.resources.length?`<div class="table-wrap"><table><thead><tr><th>资源</th><th>Kind</th><th>文件</th><th>状态</th></tr></thead><tbody>
        ${tree.resources.slice(0,12).map(r=>`<tr><td><span class="name">${esc(r.name)}</span><div class="mono">${esc(r.ns)}</div></td>
          <td>${esc(r.kind)}</td><td class="mono">${esc(r.path)}</td><td>${stateBadge(r.state)}</td></tr>`).join("")}
      </tbody></table></div>`:emptyBox("仓库中还没有 Fission 资源","把集群资源写回仓库,或在仓库中添加 YAML 清单。")}`;
  }catch(e){ el.innerHTML=emptyBox("仓库态势不可用",e.message); }
}
function editSource2(name, existing){
  const s = existing || {name:"",repo:"",branch:"main",path:"",autoMinutes:0,writeMode:"pr"};
  modal(`<div class="modal-pad"><h3>${name?"编辑":"添加"}仓库源</h3>
    <div class="form-grid">
      <label>名称<input id="s2-name" value="${esc(s.name)}" ${name?"disabled":""}></label>
      <label>仓库(owner/name)<input id="s2-repo" value="${esc(s.repo)}"></label>
      <label>分支<input id="s2-branch" value="${esc(s.branch||"main")}"></label>
      <label>子目录<input id="s2-path" value="${esc(s.path||"")}"></label>
      <label>写回模式<select id="s2-mode"><option value="pr" ${s.writeMode!=="direct"?"selected":""}>PR(推荐)</option><option value="direct" ${s.writeMode==="direct"?"selected":""}>直接提交</option></select></label>
      <label>自动拉取(分钟,0=关闭)<input id="s2-auto" type="number" value="${s.autoMinutes||0}"></label>
      <label class="full">GitHub Token(存 Secret,留空不修改)<input id="s2-token" type="password" placeholder="ghp_..."></label>
    </div>
    <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:16px">
      <button class="btn" id="m-cancel">取消</button><button class="btn primary" id="m-save">保存</button></div></div>`);
  $("#m-cancel").onclick = closeModal;
  $("#m-save").onclick = async () => {
    const src = {name:$("#s2-name").value.trim(), repo:$("#s2-repo").value.trim(),
      branch:$("#s2-branch").value.trim()||"main", path:$("#s2-path").value.trim(),
      autoMinutes:parseInt($("#s2-auto").value||"0"), writeMode:$("#s2-mode").value};
    if(!src.name||!src.repo){ toast("名称和仓库必填",true); return; }
    const token = $("#s2-token").value.trim();
    const cfg = await api("GET","/api/config");
    cfg.sources = (cfg.sources||[]).filter(x=>x.name!==(name||src.name));
    cfg.sources.push(src);
    try{
      await api("PUT","/api/config",cfg);
      if(token) await api("POST","/api/source-token",{name:src.name,token});
      toast("已保存"); closeModal(); viewGitopsV2();
    }catch(e){ toast("保存失败: "+e.message,true); }
  };
}
async function browseRepo(name){
  modal(`<div class="modal-pad" style="width:min(1100px,94vw)"><h3>仓库浏览 · ${esc(name)}</h3><div id="browse-body">${skeletons(6)}</div>
    <div style="display:flex;justify-content:flex-end;margin-top:12px"><button class="btn" id="m-cancel">关闭</button></div></div>`);
  $("#m-cancel").onclick = closeModal;
  try{
    const t = await api("POST","/api/gitops/tree",{name});
    $("#browse-body").innerHTML = `<div class="browser-grid">
      <div><div class="rail-title" style="margin-top:0">仓库文件 · ${t.files.length}</div><div style="max-height:440px;overflow:auto">
        ${t.files.map(f=>`<div class="browser-file" data-file="${esc(f.path)}">${esc(f.path)}<span style="float:right;color:var(--ink-3)">${f.size}B</span></div>`).join("")||"<div class='empty'>无文件</div>"}
      </div></div>
      <div><div class="rail-title" style="margin-top:0">文件内容</div><pre class="resp-body" id="file-view" style="max-height:300px">选择左侧文件查看</pre></div>
      <div class="res-cell"><div class="rail-title" style="margin-top:0">解析资源 · ${t.resources.length}</div>
        <div style="max-height:340px;overflow:auto"><table><thead><tr><th>资源</th><th>Kind</th><th>状态</th></tr></thead><tbody>
        ${t.resources.map(r=>`<tr><td class="mono">${esc(r.ns)}/${esc(r.name)}</td><td>${esc(r.kind)}</td>
          <td>${stateBadge(r.state)}</td></tr>`).join("")}</tbody></table></div>
      </div></div>`;
    $$("[data-file]").forEach(b=>b.onclick=async()=>{
      const f = await api("POST","/api/gitops/file",{name,path:b.dataset.file});
      $("#file-view").textContent = f.content;
    });
  }catch(e){ $("#browse-body").innerHTML = emptyBox("仓库读取失败", e.message); }
}
function stateBadge(s){
  const map = {synced:["ok","已同步"],"repo-changed":["warn","仓库有变更"],"cluster-changed":["build","集群有变更"],
    conflict:["err","双方冲突"],"repo-only":["idle","仅仓库"]};
  const m = map[s]||["idle",s]; return badge(m[0],m[1]);
}
async function planV2(name){
  modal(`<div class="modal-pad" style="width:min(900px,94vw)"><h3>差异与同步 · ${esc(name)}</h3><div id="plan2-body">${skeletons(4)}</div>
    <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:14px">
      <button class="btn" id="m-cancel">关闭</button><button class="btn primary" id="plan2-apply">应用</button></div></div>`);
  $("#m-cancel").onclick = closeModal;
  let plan;
  try{ plan = await api("POST","/api/gitops/plan",{name}); }
  catch(e){ $("#plan2-body").innerHTML = emptyBox("预览失败",e.message); return; }
  const sec=(title,arr,render)=>arr.length?`<h2>${title}(${arr.length})</h2><div class="table-wrap"><table><thead><tr><th>资源</th><th>Kind</th><th>${render?"处理":"位置"}</th></tr></thead><tbody>
    ${arr.map((i,idx)=>`<tr><td class="mono">${esc(i.ns)}/${esc(i.name)}</td><td>${esc(i.kind)}</td>
      <td>${render?render(i,idx):esc(i.path||"")}</td></tr>`).join("")}</tbody></table></div>`:"";
  $("#plan2-body").innerHTML =
    `<p class="subtitle">仓库 ${esc(plan.repo)}@${esc(plan.branch)} · ${plan.files} 个文件</p>` +
    sec("将创建",plan.create) + sec("将更新",plan.update) +
    sec("将删除(勾选后执行)",plan.delete,(i,idx)=>`<label><input type="checkbox" class="del-check" data-key="${esc(i.key)}"> 删除</label>`) +
    sec("冲突(逐条选择)",plan.conflict,(i,idx)=>`<select class="conflict-sel" data-key="${esc(i.key)}"><option value="">跳过</option><option value="repo">以仓库为准</option><option value="cluster">以集群为准</option></select>`) +
    sec("无变化",plan.unchanged) +
    ((!plan.create.length&&!plan.update.length&&!plan.delete.length&&!plan.conflict.length)?emptyBox("一切正常","集群与仓库已一致。"):"");
  $("#plan2-apply").onclick = async () => {
    const deletes=$$(".del-check:checked").map(c=>c.dataset.key);
    const conflicts={}; $$(".conflict-sel").forEach(s=>{ if(s.value) conflicts[s.dataset.key]=s.value; });
    const unresolved = plan.conflict.filter(c=>!conflicts[c.key]);
    if(unresolved.length && !confirm(`${unresolved.length} 个冲突未处理,继续将跳过它们。继续?`)) return;
    try{
      const r = await api("POST","/api/gitops/apply",{name,deletes,conflicts});
      toast(`完成:创建 ${r.created} · 更新 ${r.updated} · 删除 ${r.deleted}` + (r.errors.length?` · 错误 ${r.errors.length}`:""), r.errors.length>0);
      if(r.errors.length) modal(`<div class="modal-pad"><h3>同步错误</h3><div class="json-view">${esc(r.errors.join("\n"))}</div></div>`);
      else closeModal();
    }catch(e){ toast("同步失败: "+e.message,true); }
  };
}
async function exportV2(name){
  modal(`<div class="modal-pad" style="width:min(760px,94vw)"><h3>写回仓库 · ${esc(name)}</h3>
    <p class="subtitle">仅导出带有归属标签的资源,文件布局为「类型/名称.yaml」</p>
    <div class="form-grid"><label class="full">写回模式<select id="exp-mode"><option value="pr">PR(推荐)</option><option value="direct">直接提交</option></select></label></div>
    <div id="exp-result" style="margin-top:12px"></div>
    <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:14px">
      <button class="btn" id="m-cancel">关闭</button><button class="btn primary" id="exp-go">开始写回</button></div></div>`);
  $("#m-cancel").onclick = closeModal;
  $("#exp-go").onclick = async () => {
    const btn=$("#exp-go"); btn.disabled=true; btn.textContent="写回中…";
    try{
      const r = await api("POST","/api/gitops/export",{name,mode:$("#exp-mode").value});
      if(r.pr_url) $("#exp-result").innerHTML = `<p>已创建 PR:<a href="${esc(r.pr_url)}" target="_blank" rel="noopener">${esc(r.pr_url)}</a></p>`;
      else $("#exp-result").innerHTML = `<p>已提交 ${esc(r.commit||"")} 到分支 ${esc(r.branch||"")}</p>` + (r.pr_error?`<p class="badge err">${esc(r.pr_error)}</p>`:"");
      toast(`写回完成:${r.changed||0} 个文件`);
    }catch(e){ $("#exp-result").innerHTML = `<div class="json-view">${esc(e.message)}</div>`; }
    btn.disabled=false; btn.textContent="开始写回";
  };
}



/* ---------- command palette ---------- */
const CMD_ITEMS = [
  {label:"总览", hint:"页面", run:()=>location.hash="#/overview"},
  {label:"API 测试台", hint:"页面", run:()=>location.hash="#/tester"},
  {label:"Functions", hint:"页面", run:()=>location.hash="#/functions"},
  {label:"Packages", hint:"页面", run:()=>location.hash="#/packages"},
  {label:"Environments", hint:"页面", run:()=>location.hash="#/environments"},
  {label:"HTTP Triggers", hint:"页面", run:()=>location.hash="#/httptriggers"},
  {label:"Timer Triggers", hint:"页面", run:()=>location.hash="#/timetriggers"},
  {label:"GitOps 同步", hint:"页面", run:()=>location.hash="#/gitops"},
  {label:"新建 Function", hint:"操作", run:()=>{ location.hash="#/functions"; setTimeout(()=>{const b=$("#btn-new"); if(b) b.click();},250); }},
  {label:"预览仓库差异", hint:"操作", run:()=>{ location.hash="#/gitops"; setTimeout(()=>{const b=$("[data-plan2]"); if(b) b.click();},300); }},
  {label:"刷新当前页", hint:"操作", run:()=>navigate()},
];
let cmdkIndex = 0;
function openCmdk(){
  const d=$("#cmdk"); if(!d) return; d.showModal();
  $("#cmdk-input").value=""; cmdkIndex=0; renderCmdk("");
  setTimeout(()=>$("#cmdk-input").focus(),10);
}
function renderCmdk(q){
  const items=CMD_ITEMS.filter(i=>i.label.toLowerCase().includes(q.toLowerCase()));
  cmdkIndex=Math.max(0,Math.min(cmdkIndex,items.length-1));
  $("#cmdk-list").innerHTML=items.map((i,n)=>`<div class="item ${n===cmdkIndex?"active":""}" data-cmd="${n}">${esc(i.label)}<span class="k">${esc(i.hint)}</span></div>`).join("")
    || `<div class="item">无匹配结果</div>`;
  $$("#cmdk-list .item[data-cmd]").forEach(el=>el.onclick=()=>{ const it=items[+el.dataset.cmd]; $("#cmdk").close(); it.run(); });
}
function initCmdk(){
  const open=$("#cmdk-open"); if(open) open.onclick=openCmdk;
  const input=$("#cmdk-input");
  if(input){
    input.oninput=()=>renderCmdk(input.value);
    input.onkeydown=e=>{
      const items=CMD_ITEMS.filter(i=>i.label.toLowerCase().includes(input.value.toLowerCase()));
      if(e.key==="ArrowDown"){ e.preventDefault(); cmdkIndex=Math.min(cmdkIndex+1,items.length-1); renderCmdk(input.value); }
      if(e.key==="ArrowUp"){ e.preventDefault(); cmdkIndex=Math.max(cmdkIndex-1,0); renderCmdk(input.value); }
      if(e.key==="Enter"&&items[cmdkIndex]){ $("#cmdk").close(); items[cmdkIndex].run(); }
    };
  }
  document.addEventListener("keydown",e=>{
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="k"){ e.preventDefault(); openCmdk(); }
  });
}
initCmdk();


navigate();

/* ---------- observability charts (vanilla SVG) ---------- */
let obsTimer = null;
async function renderObservability(){
  const el = $("#obs"); if (!el) return;
  const draw = async () => {
    let m; try { m = await api("GET", "/api/metrics"); } catch(e){ el.innerHTML = emptyBox("观测数据加载失败", e.message); return; }
    if (m.last_error) { el.innerHTML = emptyBox("采样器暂不可用", m.last_error); return; }
    const series = m.series || [];
    const fns = {};
    series.forEach(s => Object.entries(s.fns||{}).forEach(([f,v]) => { (fns[f] = fns[f] || {cpu:[],mem:[],pods:[]}).cpu.push([s.ts,v.cpu_m]); fns[f].mem.push([s.ts,v.mem_mi]); fns[f].pods.push([s.ts,v.pods]); }));
    series.forEach(s => Object.keys(fns).forEach(f => { if (!(s.fns||{})[f]) { fns[f].cpu.push([s.ts,0]); fns[f].mem.push([s.ts,0]); fns[f].pods.push([s.ts,0]); } }));
    const names = Object.keys(fns);
    if (!names.length){ el.innerHTML = emptyBox("暂无函数运行数据","函数被调用并产生 Pod 后,这里会出现 CPU / 内存 / 副本数时间序列。"); return; }
    const cs = m.cold_starts||{};
    el.innerHTML = `<div class="cards" style="margin-bottom:12px">${names.map(f=>`
      <div class="stat"><div class="num">${cs[f]||0}</div><div class="label">${esc(f)} 冷启动次数</div></div>`).join("")}</div>
      ${chartBlock("CPU (毫核)", names, fns, "cpu")}
      ${chartBlock("内存 (MiB)", names, fns, "mem")}
      ${chartBlock("副本数", names, fns, "pods")}`;
  };
  await draw();
  if (obsTimer) clearInterval(obsTimer);
  obsTimer = setInterval(() => { if (location.hash.includes("overview")) draw(); else clearInterval(obsTimer); }, 15000);
}
const CHART_COLORS = ["#0b0b0b","#d22b2b","#0e7a3c","#145a86","#8a6d00","#d9af00"];
function chartBlock(title, names, fns, key){
  const W=980,H=200,P=44;
  let pts={}, min=0, max=1;
  names.forEach((n)=>{ pts[n]=fns[n][key].map(([t,v])=>[t,v]); max=Math.max(max,...fns[n][key].map(x=>x[1])); });
  if (key==="pods") max=Math.max(1,max);
  max = Math.ceil(max*1.2*10)/10;
  const t0=Math.min(...names.map(n=>pts[n][0]?.[0]??Infinity)), t1=Math.max(...names.map(n=>pts[n][pts[n].length-1]?.[0]??0));
  if (!isFinite(t0)||t1<=t0){ return `<h2>${esc(title)}</h2>`+emptyBox("数据积累中","每个采样周期(15s)增加一个点,稍后再看。"); }
  const X=t=>P+(t-t0)/(t1-t0)*(W-P-10), Y=v=>H-24-(v-min)/(max-min||1)*(H-40);
  const paths = names.map((n,i)=>{
    const d = pts[n].map(([t,v],j)=>(j?"L":"M")+X(t).toFixed(1)+" "+Y(v).toFixed(1)).join(" ");
    const col = CHART_COLORS[i%CHART_COLORS.length];
    return `<path d="${d}" fill="none" stroke="${col}" stroke-width="2" stroke-linejoin="round"/>
      ${pts[n].map(([t,v])=>`<circle cx="${X(t).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="2.5" fill="${col}"><title>${esc(n)} ${new Date(t*1000).toLocaleTimeString()} · ${v}</title></circle>`).join("")}`;
  }).join("");
  const grid=[0,.5,1].map(f=>{const gy=(H-24)-f*(H-40);const gv=(max-min)*f+min;
    return `<line x1="${P}" y1="${gy}" x2="${W-10}" y2="${gy}" stroke="#d4d0c4"/><text x="${P-6}" y="${gy+4}" font-size="10" fill="#666666" text-anchor="end">${gv%1?gv.toFixed(1):gv}</text>`;}).join("");
  const legend = names.map((n,i)=>`<span style="display:inline-flex;align-items:center;gap:5px;font:11px var(--mono);color:var(--ink-2);margin-right:12px"><i style="width:14px;height:2px;background:${CHART_COLORS[i%CHART_COLORS.length]}"></i>${esc(n)}</span>`).join("");
  return `<div class="chart-head"><b>${esc(title)}</b><span>${legend}</span></div><div style="padding:8px 0 0;border-top:1px solid var(--rule)">
    <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">${grid}${paths}</svg>
    <div style="font-size:11px;color:var(--text-3);padding:2px 6px 6px">时间窗口:${new Date(t0*1000).toLocaleTimeString()} → ${new Date(t1*1000).toLocaleTimeString()}(15s 采样)</div></div>`;
}

/* ---------- gitops view ---------- */
async function viewGitops(){
  const main = $("#main");
  main.innerHTML = `<h1>GitOps 同步</h1><p class="subtitle">从 GitHub 仓库拉取 Fission 清单,自动同步函数 / 环境 / 触发器</p>
    <div class="toolbar"><button class="btn primary" id="btn-add-src">添加仓库源</button></div><div id="src-list">${skeletons()}</div><div id="gitops-summary"></div></div>`;
  const load = async () => {
    const cfg = await api("GET", "/api/config");
    if (!cfg.sources.length){ $("#src-list").innerHTML = emptyBox("还没有仓库源","点击「添加仓库源」连接一个包含 Fission YAML 的 GitHub 仓库。"); }
    else {
      $("#src-list").innerHTML = `<div class="table-wrap"><table><thead><tr><th>名称</th><th>仓库</th><th>类型</th><th>自动同步</th><th>操作</th></tr></thead><tbody>
        ${cfg.sources.map(s=>`<tr>
          <td><span class="name">${esc(s.name)}</span></td>
          <td class="mono">${esc(s.repo)}<div>${esc(s.branch||"main")}${s.path?" · "+esc(s.path):""}</div></td>
          <td>${s.token?badge("warn","私有") : badge("idle","公开")}</td>
          <td>${s.autoMinutes?badge("ok","每 "+s.autoMinutes+" 分钟") : badge("idle","手动")}</td>
          <td><button class="btn small" data-plan="${esc(s.name)}">预览差异</button>
              <button class="btn small primary" data-sync="${esc(s.name)}">立即同步</button>
              <button class="btn small" data-edit-src="${esc(s.name)}">编辑</button>
              <button class="btn small danger" data-del-src="${esc(s.name)}">删除</button></td></tr>`).join("")}
        </tbody></table></div>`;
    }
    $("#btn-add-src").onclick = () => editSource(null);
    $$("#src-list [data-edit-src]").forEach(b=>b.onclick=()=>editSource(b.dataset.editSrc, cfg.sources.find(s=>s.name===b.dataset.editSrc)));
    $$("#src-list [data-del-src]").forEach(b=>b.onclick=async()=>{
      if(!confirm(`删除仓库源 ${b.dataset.delSrc}?`)) return;
      cfg.sources = cfg.sources.filter(s=>s.name!==b.dataset.delSrc);
      await api("PUT","/api/config",cfg); toast("已删除"); load();
    });
    $$("#src-list [data-plan]").forEach(b=>b.onclick=()=>runPlan(b.dataset.plan));
    $$("#src-list [data-sync]").forEach(b=>b.onclick=()=>runSync(b.dataset.sync));
  };
  await load();
}
function editSource(name, existing){
  const s = existing || {name:"", repo:"", branch:"main", path:"", token:"", autoMinutes:0};
  modal(`<div class="modal-pad"><h3>${name?"编辑":"添加"}仓库源</h3>
    <div class="form-grid">
      <label>名称<input id="src-name" value="${esc(s.name)}"></label>
      <label>仓库(owner/name)<input id="src-repo" value="${esc(s.repo)}" placeholder="octocat/hello-world"></label>
      <label>分支<input id="src-branch" value="${esc(s.branch||"main")}"></label>
      <label>子目录(可空)<input id="src-path" value="${esc(s.path||"")}"></label>
      <label class="full">Token(私有仓库必填,公开留空)<input id="src-token" value="${esc(s.token||"")}"></label>
      <label>自动同步(分钟,0=关闭)<input id="src-auto" type="number" value="${s.autoMinutes||0}"></label>
    </div>
    <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:16px">
      <button class="btn" id="m-cancel">取消</button><button class="btn primary" id="m-save">保存</button></div></div>`);
  $("#m-cancel").onclick = closeModal;
  $("#m-save").onclick = async () => {
    const src = {name: $("#src-name").value.trim(), repo: $("#src-repo").value.trim(),
      branch: $("#src-branch").value.trim() || "main", path: $("#src-path").value.trim(),
      token: $("#src-token").value.trim(), autoMinutes: parseInt($("#src-auto").value||"0")};
    if (!src.name || !src.repo){ toast("名称和仓库必填", true); return; }
    const cfg = await api("GET","/api/config");
    cfg.sources = (cfg.sources||[]).filter(x=>x.name!==name);
    cfg.sources.push(src);
    try { await api("PUT","/api/config",cfg); toast("已保存"); closeModal(); navigate(); }
    catch(e){ toast("保存失败: "+e.message, true); }
  };
}
async function runPlan(name){
  modal(`<div class="modal-pad"><h3>差异预览 · ${esc(name)}</h3><div id="plan-body">${skeletons(4)}</div>
    <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:14px">
    <button class="btn" id="m-cancel">关闭</button></div></div>`);
  $("#m-cancel").onclick = closeModal;
  try {
    const plan = await api("POST", `/api/gitops/plan/${name}`);
    const sec = (t, arr) => arr.length ? `<h2>${t}(${arr.length})</h2><div class="table-wrap"><table><thead><tr><th>文件</th><th>Kind</th><th>资源</th></tr></thead><tbody>
      ${arr.map(i=>`<tr><td>${esc(i.path||"")}</td><td>${esc(i.kind||"")}</td><td class="mono">${esc(i.ns||"")}/${esc(i.name||"")}</td></tr>`).join("")}</tbody></table></div>` : "";
    $("#plan-body").innerHTML =
      `<p class="subtitle">仓库 ${esc(plan.repo)}@${esc(plan.branch)} · ${plan.files} 个文件</p>` +
      sec("将创建", plan.create) + sec("将更新", plan.update) + sec("无变化", plan.unchanged) +
      ((plan.errors||[]).length ? `<h2>解析错误</h2><div class="json-view">${esc(plan.errors.map(e=>e.path+": "+e.error).join("\n"))}</div>` : "") +
      ((!plan.create.length && !plan.update.length) ? emptyBox("一切正常","集群与仓库已一致。") : "");
  } catch(e){ $("#plan-body").innerHTML = emptyBox("预览失败", e.message); }
}
async function runSync(name){
  if (!confirm(`立即从 ${name} 同步到集群?`)) return;
  toast("同步中…");
  try {
    const r = await api("POST", `/api/gitops/sync/${name}`);
    toast(`同步完成:新建 ${r.created},更新 ${r.updated}` + (r.errors.length?`,${r.errors.length} 个错误`:""), r.errors.length>0);
    if (r.errors.length) modal(`<div class="modal-pad"><h3>同步错误</h3><div class="json-view">${esc(r.errors.join("\n"))}</div></div>`);
  } catch(e){ toast("同步失败: "+e.message, true); }
}
