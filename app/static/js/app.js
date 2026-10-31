/* Intelligent PDF Reader - frontend controller */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const MAX_MB = +document.body.dataset.maxMb;
  const STEPS = ["upload", "interests", "settings", "process", "review", "generate"];
  const FLAG_LABELS = {
    boundary_uncertain: "Boundary uncertain", borderline_relevance: "Borderline relevance", low_ocr_confidence: "Low OCR confidence",
    duplicate: "Duplicate", multipage_table: "Multi-page table", table_structure_uncertain: "Table structure uncertain",
  };
  const state = { sid: null, session: null, topics: [], keywords: [], results: [], step: "upload", polling: null, uploads: {} };

  // ---------------------------------------------------------------- helpers
  async function api(method, url, body, isForm) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      if (isForm) opts.body = body; else { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
    }
    const res = await fetch(`/api${url}`, opts);
    let data = {};
    try { data = await res.json(); } catch (_) { /* non-JSON */ }
    if (!res.ok) { const e = new Error(data.error || `Request failed (${res.status})`); e.status = res.status; e.data = data; throw e; }
    return data;
  }
  function alertMsg(msg, type = "danger", timeout = 7000) {
    const div = document.createElement("div");
    div.className = `alert alert-${type} alert-dismissible fade show`;
    div.innerHTML = `${esc(msg)}<button type="button" class="btn-close" data-bs-dismiss="alert"></button>`;
    $("#alerts").appendChild(div);
    if (timeout) setTimeout(() => div.remove(), timeout);
  }
  const fmtSize = (b) => b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`;
  const fmtPages = (p) => !p.length ? "" : p.length === 1 ? `p. ${p[0]}` : (p[p.length - 1] - p[0] === p.length - 1 ? `pp. ${p[0]}–${p[p.length - 1]}` : `pp. ${p.join(", ")}`);

  // ---------------------------------------------------------------- session
  async function initSession() {
    const saved = localStorage.getItem("ipr_sid");
    if (saved) {
      try { state.session = await api("GET", `/sessions/${saved}`); state.sid = saved; } catch (_) { localStorage.removeItem("ipr_sid"); }
    }
    if (!state.sid) {
      state.session = await api("POST", "/sessions");
      state.sid = state.session.id;
      localStorage.setItem("ipr_sid", state.sid);
    }
    const s = state.session;
    state.topics = [...s.interests.topics];
    state.keywords = [...s.interests.keywords];
    $("#description").value = s.interests.description || "";
    applySettings(s.settings_effective);
    renderDocs(); renderTags(); updateSummaries();
    if (s.job && ["running", "queued"].includes(s.job.status)) { goto("process"); startPolling(); }
    else if (s.result_count) { await loadResults(); renderJob(s.job); }
    if (s.output) showOutput(s.output);
  }
  async function refreshSession() { state.session = await api("GET", `/sessions/${state.sid}`); updateSummaries(); return state.session; }

  // ---------------------------------------------------------------- navigation
  async function goto(step) {
    if (state.step === "interests") await saveInterests().catch((e) => alertMsg(e.message));
    if (state.step === "settings") await saveSettings().catch((e) => alertMsg(e.message));
    state.step = step;
    STEPS.forEach((s) => $(`#pane-${s}`).classList.toggle("d-none", s !== step));
    $$("#stepper li").forEach((li) => li.classList.toggle("active", li.dataset.step === step));
    if (step === "process") renderProcessSummary();
    if (step === "review") await loadResults();
    if (step === "generate") renderGenerateSummary();
    window.scrollTo(0, 0);
  }
  function updateSummaries() {
    const s = state.session; if (!s) return;
    const ready = s.documents.filter((d) => d.status === "ready");
    $("#sum-upload").textContent = s.documents.length ? `${ready.length} document(s), ${ready.reduce((a, d) => a + d.pages, 0)} pages` : "No documents";
    $("#sum-interests").textContent = state.topics.length ? `${state.topics.length} topic(s), ${state.keywords.length} keyword(s)` : (state.keywords.length ? `${state.keywords.length} keyword(s)` : "No topics");
    const st = s.settings_effective || {};
    $("#sum-settings").textContent = `${st.mode === "exploratory" ? "Exploratory" : "Strict"} · ${(st.include_levels || []).join("/")}`;
    const job = s.job || {};
    $("#sum-process").textContent = { done: "Complete", running: `${Math.round(job.progress || 0)}%`, queued: "Queued", error: "Failed", cancelled: "Cancelled" }[job.status] || "Not started";
    const inc = state.results.filter((r) => r.included).length;
    $("#sum-review").textContent = state.results.length ? `${inc} of ${state.results.length} selected` : "—";
    $("#sum-generate").textContent = s.output ? `${s.output.pages} pages` : "—";
    $$("#stepper li").forEach((li) => {
      const d = { upload: ready.length > 0, interests: state.topics.length || state.keywords.length || $("#description").value.trim(), process: job.status === "done", review: state.results.length > 0, generate: !!s.output }[li.dataset.step];
      li.classList.toggle("done", !!d);
    });
  }

  // ---------------------------------------------------------------- upload
  function renderDocs() {
    const tbody = $("#doc-table tbody");
    const docs = state.session.documents;
    const rows = docs.map((d) => {
      let status;
      if (d.status === "ready") status = `<span class="badge text-bg-success"><i class="bi bi-check-circle me-1"></i>Ready</span>`;
      else if (d.status === "locked") status = `<div class="input-group input-group-sm"><span class="input-group-text"><i class="bi bi-lock"></i></span>
          <input type="password" class="form-control pw" placeholder="Password" data-id="${d.id}"><button class="btn btn-outline-primary unlock" data-id="${d.id}">Unlock</button></div>
          <div class="small text-danger mt-1">${esc(d.error)}</div>`;
      else status = `<span class="badge text-bg-danger">${esc(d.error || d.status)}</span>`;
      return `<tr><td><i class="bi bi-file-earmark-pdf text-danger me-2"></i>${esc(d.filename)}</td><td>${fmtSize(d.size)}</td><td>${d.pages || "—"}</td>
        <td>${status}</td><td class="text-end"><button class="btn btn-sm btn-outline-danger remove-doc" data-id="${d.id}" title="Remove"><i class="bi bi-trash"></i></button></td></tr>`;
    });
    const up = Object.entries(state.uploads).map(([k, u]) => `<tr><td><i class="bi bi-file-earmark-pdf me-2"></i>${esc(u.name)}</td><td>${fmtSize(u.size)}</td><td>—</td>
      <td><div class="progress" style="height:10px"><div class="progress-bar progress-bar-animated progress-bar-striped" style="width:${u.pct}%"></div></div>
      <div class="small text-secondary">${u.pct < 100 ? `Uploading ${u.pct}%` : "Validating…"}</div></td><td></td></tr>`);
    tbody.innerHTML = rows.concat(up).join("") || `<tr class="empty"><td colspan="5" class="text-center text-secondary py-4">No documents uploaded yet.</td></tr>`;
    const ready = docs.filter((d) => d.status === "ready");
    $("#doc-totals").textContent = docs.length ? `${ready.length} ready · ${ready.reduce((a, d) => a + d.pages, 0)} pages · ${fmtSize(docs.reduce((a, d) => a + d.size, 0))}` : "";
    updateSummaries();
  }
  function uploadFile(file) {
    if (!/\.pdf$/i.test(file.name)) { alertMsg(`'${file.name}' is not a PDF. Only PDF files are supported.`); return Promise.resolve(); }
    if (file.size > MAX_MB * 1048576) { alertMsg(`'${file.name}' exceeds the ${MAX_MB} MB limit.`); return Promise.resolve(); }
    const key = Math.random().toString(36).slice(2);
    state.uploads[key] = { name: file.name, size: file.size, pct: 0 };
    renderDocs();
    return new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      const fd = new FormData(); fd.append("file", file);
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) { state.uploads[key].pct = Math.round((e.loaded / e.total) * 100); renderDocs(); } };
      xhr.onload = () => {
        delete state.uploads[key];
        let data = {}; try { data = JSON.parse(xhr.responseText); } catch (_) { /* ignore */ }
        if (xhr.status >= 200 && xhr.status < 300) state.session.documents.push(data);
        else alertMsg(data.error || `Upload of '${file.name}' failed (${xhr.status}).`);
        renderDocs(); resolve();
      };
      xhr.onerror = () => { delete state.uploads[key]; alertMsg(`Upload of '${file.name}' failed (network error).`); renderDocs(); resolve(); };
      xhr.open("POST", `/api/sessions/${state.sid}/documents`); xhr.send(fd);
    });
  }
  async function handleFiles(files) { for (const f of files) await uploadFile(f); }

  // ---------------------------------------------------------------- interests
  function renderTags() {
    const tag = (v, i, kind) => `<span class="tag ${kind === "keywords" ? "kw" : ""}"><span contenteditable="true" spellcheck="false" data-kind="${kind}" data-i="${i}" title="Click to edit">${esc(v)}</span><button data-kind="${kind}" data-i="${i}" title="Remove"><i class="bi bi-x-lg"></i></button></span>`;
    $("#topic-tags").innerHTML = state.topics.map((t, i) => tag(t, i, "topics")).join("") || `<span class="small text-secondary">No topics yet.</span>`;
    $("#keyword-tags").innerHTML = state.keywords.map((t, i) => tag(t, i, "keywords")).join("") || `<span class="small text-secondary">No keywords (optional).</span>`;
    updateSummaries();
  }
  function addItems(kind, text) {
    const list = state[kind];
    text.split(/[,;\n]+/).map((s) => s.trim()).filter(Boolean).forEach((v) => { if (!list.some((x) => x.toLowerCase() === v.toLowerCase())) list.push(v.slice(0, 120)); });
    renderTags();
  }
  async function saveInterests() {
    const data = await api("PUT", `/sessions/${state.sid}/interests`, { topics: state.topics, keywords: state.keywords, description: $("#description").value });
    state.topics = data.topics; state.keywords = data.keywords; renderTags();
  }

  // ---------------------------------------------------------------- settings
  function applySettings(s) {
    if (!s) return;
    $$("input[name=mode]").forEach((r) => (r.checked = r.value === s.mode));
    $$(".opt").forEach((el) => (el.checked = !!s[el.dataset.key]));
    $$(".lvl").forEach((el) => (el.checked = (s.include_levels || []).includes(el.value)));
    const auto = s.medium_threshold === null || s.medium_threshold === undefined;
    $("#auto-threshold").checked = auto;
    $("#threshold-box").classList.toggle("d-none", auto);
    if (!auto) { $("#medium-threshold").value = s.medium_threshold; $("#medium-threshold-val").textContent = (+s.medium_threshold).toFixed(2); }
  }
  async function saveSettings() {
    const body = { mode: $("input[name=mode]:checked").value, include_levels: $$(".lvl").filter((e) => e.checked).map((e) => e.value) };
    $$(".opt").forEach((el) => (body[el.dataset.key] = el.checked));
    body.medium_threshold = $("#auto-threshold").checked ? null : +$("#medium-threshold").value;
    if (!body.semantic && !body.keyword) { alertMsg("At least one of semantic or keyword search must be enabled; semantic search was kept on.", "warning"); body.semantic = true; $("#opt-semantic").checked = true; }
    const eff = await api("PUT", `/sessions/${state.sid}/settings`, body);
    state.session.settings = body; state.session.settings_effective = eff; updateSummaries();
  }

  // ---------------------------------------------------------------- processing
  function renderProcessSummary() {
    const s = state.session, st = s.settings_effective || {};
    const ready = s.documents.filter((d) => d.status === "ready");
    $("#process-summary").innerHTML = `
      <div class="fw-semibold mb-2">Run summary</div>
      <dl class="row small mb-0">
        <dt class="col-4">Documents</dt><dd class="col-8">${ready.length ? ready.map((d) => `${esc(d.filename)} <span class="text-secondary">(${d.pages} p.)</span>`).join("<br>") : '<span class="text-danger">None ready</span>'}</dd>
        <dt class="col-4">Topics</dt><dd class="col-8">${esc(state.topics.join(", ")) || "—"}</dd>
        <dt class="col-4">Description</dt><dd class="col-8">${esc($("#description").value.slice(0, 160)) || "—"}</dd>
        <dt class="col-4">Keywords</dt><dd class="col-8">${state.keywords.length ? esc(state.keywords.slice(0, 12).join(", ")) + (state.keywords.length > 12 ? ` … (+${state.keywords.length - 12})` : "") : "—"}</dd>
        <dt class="col-4">Mode</dt><dd class="col-8 text-capitalize">${esc(st.mode)} · ${st.semantic ? "semantic" : ""}${st.semantic && st.keyword ? " + " : ""}${st.keyword ? "keyword" : ""}</dd>
        <dt class="col-4">Extract</dt><dd class="col-8">${[st.paragraphs && "discussions", st.tables && "tables", st.ocr && "OCR"].filter(Boolean).join(", ")}</dd>
      </dl>`;
    renderJob(s.job || {});
  }
  function renderJob(job) {
    const pct = Math.round(job.progress || 0);
    const bar = $("#job-bar");
    bar.style.width = `${pct}%`; bar.textContent = `${pct}%`;
    bar.classList.toggle("progress-bar-animated", ["running", "queued"].includes(job.status));
    bar.classList.toggle("bg-success", job.status === "done");
    bar.classList.toggle("bg-danger", job.status === "error");
    $("#job-stage").textContent = job.stage || (job.status === "idle" || !job.status ? "Ready" : job.status);
    $("#job-message").textContent = job.message || "";
    if (job.status === "error") $("#job-message").classList.add("text-danger"); else $("#job-message").classList.remove("text-danger");
    const running = ["running", "queued"].includes(job.status);
    $("#btn-process").disabled = running;
    $("#btn-process").innerHTML = job.status === "done" || job.status === "error" || job.status === "cancelled" ? '<i class="bi bi-arrow-repeat me-1"></i>Re-run' : '<i class="bi bi-play-fill me-1"></i>Start processing';
    $("#btn-cancel").classList.toggle("d-none", !running);
    $("#btn-to-review").disabled = !(job.status === "done" || state.results.length);
    const log = $("#job-log");
    log.innerHTML = (job.log || []).map((l) => `<div>${esc(l)}</div>`).join("");
    log.scrollTop = log.scrollHeight;
    renderDocReports();
  }
  function renderDocReports() {
    const docs = (state.session.documents || []).filter((d) => d.report);
    const box = $("#doc-reports");
    if (!docs.length) { box.classList.add("d-none"); return; }
    box.classList.remove("d-none");
    box.querySelector(".card-body").innerHTML = `<div class="fw-semibold mb-2">Document analysis</div>` + docs.map((d) => {
      const r = d.report;
      const warns = (r.warnings || []).map((w) => `<li class="${w.level === "error" ? "text-danger" : "text-warning-emphasis"}">Page ${w.page}: ${esc(w.message)}</li>`).join("");
      return `<div class="small mb-2"><i class="bi bi-file-earmark-pdf me-1"></i><b>${esc(d.filename)}</b> — ${r.elements} content blocks, ${r.tables} table(s)${r.ocr_pages.length ? `, OCR on page(s) ${r.ocr_pages.join(", ")}` : ""}${warns ? `<ul class="mb-0">${warns}</ul>` : ""}</div>`;
    }).join("");
  }
  async function startProcessing() {
    try {
      await saveInterests(); await saveSettings();
      const job = await api("POST", `/sessions/${state.sid}/process`);
      state.session.job = job; state.results = []; renderJob(job); startPolling();
    } catch (e) { alertMsg(e.message); }
  }
  function startPolling() {
    clearInterval(state.polling);
    state.polling = setInterval(async () => {
      try {
        const job = await api("GET", `/sessions/${state.sid}/job`);
        state.session.job = job; renderJob(job); updateSummaries();
        if (!["running", "queued"].includes(job.status)) {
          clearInterval(state.polling);
          await refreshSession();
          if (job.status === "done") { await loadResults(); renderJob(state.session.job); alertMsg(`Processing complete: ${state.results.length} extract(s) found.`, "success"); }
          else if (job.status === "error") alertMsg(job.message);
        }
      } catch (e) { clearInterval(state.polling); alertMsg(e.message); }
    }, 1000);
  }

  // ---------------------------------------------------------------- review
  async function loadResults() {
    const data = await api("GET", `/sessions/${state.sid}/results`);
    state.results = data.results; state.topicsRun = data.topics;
    const sel = $("#f-topic"), cur = sel.value;
    sel.innerHTML = `<option value="">All topics</option>` + data.topics.map((t) => `<option ${t === cur ? "selected" : ""}>${esc(t)}</option>`).join("");
    renderResults(); updateSummaries();
  }
  function visibleResults() {
    const topic = $("#f-topic").value, kind = $("#f-kind").value, status = $("#f-status").value;
    const levels = $$(".f-level").filter((e) => e.checked).map((e) => e.value);
    const q = $("#f-search").value.trim().toLowerCase();
    return state.results.filter((r) => (!topic || r.topic === topic) && levels.includes(r.level) && (!kind || r.kind === kind)
      && (!status || (status === "in") === !!r.included) && (!q || r.text.toLowerCase().includes(q) || (r.filename || "").toLowerCase().includes(q)));
  }
  function renderResults() {
    const res = state.results;
    const inc = res.filter((r) => r.included);
    $("#review-stats").innerHTML = [["Extracts", res.length], ["Selected", inc.length], ["Tables", inc.filter((r) => r.kind === "table").length], ["Source pages", new Set(inc.flatMap((r) => r.pages.map((p) => `${r.doc_id}:${p}`))).size]]
      .map(([l, v]) => `<div class="stat"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");
    if (!res.length) {
      $("#results").innerHTML = `<div class="card"><div class="card-body text-center text-secondary py-5"><i class="bi bi-search display-6"></i><div class="mt-2">${state.session.job && state.session.job.status === "done" ? "No relevant content was found. Try Exploratory mode, add a description, or broaden your topics." : "Run processing to see results."}</div></div></div>`;
      updateSummaries(); return;
    }
    const vis = visibleResults();
    const topics = (state.topicsRun || []).filter((t) => vis.some((r) => r.topic === t));
    $("#results").innerHTML = topics.map((t) => {
      const items = vis.filter((r) => r.topic === t);
      return `<div class="topic-head d-flex align-items-center gap-2"><h5 class="mb-0"><i class="bi bi-bookmark-star text-primary me-1"></i>${esc(t)}</h5>
          <span class="badge text-bg-light border">${items.length} extract(s)</span></div>${items.map(resultCard).join("")}`;
    }).join("") || `<div class="text-secondary p-4 text-center">No results match the current filters.</div>`;
    updateSummaries();
  }
  function resultCard(r) {
    const flags = (r.flags || []).map((f) => `<span class="badge rounded-pill text-bg-warning flag me-1">${esc(FLAG_LABELS[f] || f)}</span>`).join("");
    const dup = r.duplicate_of ? `<div class="small text-secondary"><i class="bi bi-files me-1"></i>${r.duplicate_reason === "same_content_other_topic" ? "Same content already extracted under another topic" : r.duplicate_reason === "repeated_text" ? "Repeats text found elsewhere in the documents" : "Overlaps with another extract"} — excluded by default to avoid duplicates.</div>` : "";
    const also = r.also_relevant_to && r.also_relevant_to.length ? `<div class="small text-secondary">Also relevant to: ${esc(r.also_relevant_to.join(", "))}</div>` : "";
    const table = r.table ? `<div class="small text-secondary"><i class="bi bi-table me-1"></i>${esc(r.table.title || "Table")} — ${r.table.rows} rows × ${r.table.cols} columns${r.table.pages.length > 1 ? `, continues across pages ${r.table.pages.join(", ")}` : ""}</div>` : "";
    const imgs = r.regions.map((g, i) => `<img loading="lazy" src="/api/sessions/${state.sid}/snapshots/${encodeURIComponent(g.file)}" alt="Snapshot ${i + 1} from page ${g.page + 1}" data-rid="${r.id}" data-page="${g.page + 1}" style="width:${Math.min(100, ((g.bbox[2] - g.bbox[0]) / 6.12)).toFixed(1)}%">`).join("");
    return `<div class="card result-card mb-3 lvl-${r.level} ${r.included ? "" : "excluded"}" id="card-${r.id}"><div class="card-body">
      <div class="d-flex justify-content-between align-items-start gap-3 flex-wrap">
        <div>
          <div class="mb-1"><span class="badge lv-${r.level} me-1">${r.level}</span><span class="badge text-bg-light border me-1">${r.kind === "table" ? '<i class="bi bi-table"></i> Table' : '<i class="bi bi-text-paragraph"></i> Discussion'}</span>${flags}</div>
          <div class="fw-semibold">${esc(r.section || r.text.split("\n")[0].slice(0, 90))}</div>
          <div class="small text-secondary"><i class="bi bi-file-earmark-pdf me-1"></i>${esc(r.filename)} · ${fmtPages(r.pages)} · score ${r.score.toFixed(2)}</div>
          ${table}${also}${dup}
        </div>
        <div class="form-check form-switch fs-5"><input class="form-check-input toggle-inc" type="checkbox" role="switch" data-id="${r.id}" id="inc-${r.id}" ${r.included ? "checked" : ""}>
          <label class="form-check-label small" for="inc-${r.id}">${r.included ? "Included" : "Excluded"}</label></div>
      </div>
      <div class="row g-3 mt-1">
        <div class="col-xl-8"><div class="snap-wrap">${imgs}</div></div>
        <div class="col-xl-4 small">
          <div class="fw-semibold mb-1">Why this was selected</div>
          <ul class="reasons ps-3 mb-2">${(r.reasons || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
          ${r.matched_keywords && r.matched_keywords.length ? `<div class="mb-2"><span class="text-secondary">Matched keywords:</span> ${r.matched_keywords.map((k) => `<span class="badge text-bg-light border">${esc(k)}</span>`).join(" ")}</div>` : ""}
          <details><summary class="text-primary">Extracted text</summary><pre class="small mt-2 mb-0" style="white-space:pre-wrap">${esc(r.text.slice(0, 3000))}${r.text.length > 3000 ? "…" : ""}</pre></details>
        </div>
      </div></div></div>`;
  }
  async function setIncluded(id, included) {
    const r = state.results.find((x) => x.id === id);
    try { await api("PATCH", `/sessions/${state.sid}/results/${id}`, { included }); r.included = included; state.session.output = null; }
    catch (e) { alertMsg(e.message); }
    renderResults();
  }
  async function bulk(included) {
    const ids = visibleResults().map((r) => r.id);
    if (!ids.length) return;
    await api("POST", `/sessions/${state.sid}/results/bulk`, { ids, included });
    state.results.forEach((r) => { if (ids.includes(r.id)) r.included = included; });
    renderResults();
  }

  // ---------------------------------------------------------------- generate
  function renderGenerateSummary() {
    const inc = state.results.filter((r) => r.included);
    const byTopic = {};
    inc.forEach((r) => (byTopic[r.topic] = (byTopic[r.topic] || 0) + 1));
    $("#generate-summary").innerHTML = inc.length ? `<div class="small"><b>${inc.length}</b> extract(s) selected:<ul class="mb-0">${Object.entries(byTopic).map(([t, n]) => `<li>${esc(t)}: ${n}</li>`).join("")}</ul></div>`
      : `<div class="small text-danger">No extracts selected. Include results in the review step first.</div>`;
    $("#btn-generate").disabled = !inc.length;
    if (state.session.output) showOutput(state.session.output); else { $("#btn-download").classList.add("d-none"); }
  }
  function showOutput(out) {
    const url = `/api/sessions/${state.sid}/download`;
    $("#btn-download").href = url; $("#btn-download").classList.remove("d-none");
    $("#generate-info").textContent = `${out.pages} pages · ${out.results} extract(s) · generated ${new Date(out.generated * 1000).toLocaleString()}`;
    $("#pdf-preview").src = `${url}?inline=1&t=${out.generated}`;
    $("#pdf-preview").classList.remove("d-none"); $("#pdf-placeholder").classList.add("d-none");
  }
  async function generate() {
    const btn = $("#btn-generate");
    btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Generating…';
    try { const out = await api("POST", `/sessions/${state.sid}/generate`, { title: $("#out-title").value }); state.session.output = out; showOutput(out); updateSummaries(); alertMsg("PDF generated.", "success", 4000); }
    catch (e) { alertMsg(e.message); }
    finally { btn.disabled = false; btn.innerHTML = '<i class="bi bi-file-earmark-pdf me-1"></i>Generate PDF'; }
  }

  // ---------------------------------------------------------------- events
  function bind() {
    $$("[data-goto]").forEach((b) => b.addEventListener("click", () => goto(b.dataset.goto)));
    $$("#stepper li").forEach((li) => li.addEventListener("click", () => goto(li.dataset.step)));
    $("#btn-new-session").addEventListener("click", async () => {
      if (!confirm("Start a new session? Current documents and results will be discarded.")) return;
      try { await api("DELETE", `/sessions/${state.sid}`); } catch (_) { /* already gone */ }
      localStorage.removeItem("ipr_sid"); location.reload();
    });
    const dz = $("#dropzone"), fi = $("#file-input");
    dz.addEventListener("click", () => fi.click());
    dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") fi.click(); });
    fi.addEventListener("change", () => { handleFiles(Array.from(fi.files)); fi.value = ""; });
    ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
    dz.addEventListener("drop", (e) => handleFiles(Array.from(e.dataTransfer.files)));
    $("#doc-table").addEventListener("click", async (e) => {
      const rm = e.target.closest(".remove-doc"), un = e.target.closest(".unlock");
      if (rm) {
        try { await api("DELETE", `/sessions/${state.sid}/documents/${rm.dataset.id}`); state.session.documents = state.session.documents.filter((d) => d.id !== rm.dataset.id); state.results = state.results.filter((r) => r.doc_id !== rm.dataset.id); renderDocs(); }
        catch (err) { alertMsg(err.message); }
      }
      if (un) {
        const pw = $(`.pw[data-id="${un.dataset.id}"]`).value;
        try { const d = await api("POST", `/sessions/${state.sid}/documents/${un.dataset.id}/unlock`, { password: pw }); const i = state.session.documents.findIndex((x) => x.id === d.id); state.session.documents[i] = d; renderDocs(); alertMsg(`'${d.filename}' unlocked.`, "success", 3000); }
        catch (err) { alertMsg(err.message); }
      }
    });
    $("#doc-table").addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.classList.contains("pw")) $(`.unlock[data-id="${e.target.dataset.id}"]`).click(); });
    const bindAdd = (inputSel, btnSel, kind) => {
      const inp = $(inputSel);
      const add = () => { if (inp.value.trim()) { addItems(kind, inp.value); inp.value = ""; } };
      $(btnSel).addEventListener("click", add);
      inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
      inp.addEventListener("input", () => { if (inp.value.includes(",")) { const parts = inp.value.split(","); inp.value = parts.pop(); addItems(kind, parts.join(",")); } });
    };
    bindAdd("#topic-input", "#btn-add-topic", "topics");
    bindAdd("#keyword-input", "#btn-add-keyword", "keywords");
    ["#topic-tags", "#keyword-tags"].forEach((sel) => {
      const el = $(sel);
      el.addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { state[b.dataset.kind].splice(+b.dataset.i, 1); renderTags(); } });
      el.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.isContentEditable) { e.preventDefault(); e.target.blur(); } });
      el.addEventListener("focusout", (e) => {
        if (!e.target.isContentEditable) return;
        const { kind, i } = e.target.dataset; const v = e.target.textContent.trim();
        if (v) state[kind][+i] = v.slice(0, 120); else state[kind].splice(+i, 1);
        renderTags();
      });
    });
    $("#btn-clear-keywords").addEventListener("click", () => { state.keywords = []; renderTags(); });
    $("#keyword-file").addEventListener("change", async (e) => {
      const f = e.target.files[0]; e.target.value = ""; if (!f) return;
      const fd = new FormData(); fd.append("file", f);
      try { const d = await api("POST", `/sessions/${state.sid}/keywords/file`, fd, true); const before = state.keywords.length; addItems("keywords", d.keywords.join(",")); alertMsg(`Imported ${state.keywords.length - before} new keyword(s) from ${f.name}.`, "success", 4000); }
      catch (err) { alertMsg(err.message); }
    });
    $("#description").addEventListener("input", updateSummaries);
    $("#auto-threshold").addEventListener("change", (e) => $("#threshold-box").classList.toggle("d-none", e.target.checked));
    $("#medium-threshold").addEventListener("input", (e) => ($("#medium-threshold-val").textContent = (+e.target.value).toFixed(2)));
    $$("input[name=mode]").forEach((r) => r.addEventListener("change", () => {
      const lv = r.value === "strict" ? ["HIGH", "MEDIUM"] : ["HIGH", "MEDIUM", "LOW"];
      $$(".lvl").forEach((el) => (el.checked = lv.includes(el.value)));
    }));
    $("#btn-process").addEventListener("click", startProcessing);
    $("#btn-cancel").addEventListener("click", () => api("POST", `/sessions/${state.sid}/job/cancel`).catch((e) => alertMsg(e.message)));
    ["#f-topic", "#f-kind", "#f-status"].forEach((s) => $(s).addEventListener("change", renderResults));
    $$(".f-level").forEach((el) => el.addEventListener("change", renderResults));
    $("#f-search").addEventListener("input", renderResults);
    $("#btn-include-visible").addEventListener("click", () => bulk(true));
    $("#btn-exclude-visible").addEventListener("click", () => bulk(false));
    $("#results").addEventListener("change", (e) => { if (e.target.classList.contains("toggle-inc")) setIncluded(e.target.dataset.id, e.target.checked); });
    $("#results").addEventListener("click", (e) => {
      const img = e.target.closest(".snap-wrap img"); if (!img) return;
      const r = state.results.find((x) => x.id === img.dataset.rid);
      $("#snap-modal-title").textContent = `${r.filename} — page ${img.dataset.page}`;
      $("#snap-modal-body").innerHTML = `<img src="${img.src}" class="img-fluid shadow-sm bg-white" alt="">`;
      bootstrap.Modal.getOrCreateInstance($("#snap-modal")).show();
    });
    $("#btn-generate").addEventListener("click", generate);
  }

  bind();
  initSession().catch((e) => alertMsg(`Could not start a session: ${e.message}`, "danger", 0));
})();
