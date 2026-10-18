/* Kervan Credit Passport — UI wiring (vanilla JS, no build step). */
(() => {
  const $ = (sel) => document.querySelector(sel);
  const el = (tag, cls, html) => { const n = document.createElement(tag); if (cls) n.className = cls; if (html !== undefined) n.innerHTML = html; return n; };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtMNT = (v) => v == null ? "—" : "₮" + Math.round(v).toLocaleString("en-US");
  const fmtM = (v) => v == null ? "—" : "₮" + (v / 1e6).toFixed(1) + "M";
  const pct = (v, d = 1) => v == null ? "—" : (v * 100).toFixed(d) + "%";

  // ---------------------------------------------------------------- motion helpers (presentation only)
  const REDUCE = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const EASE = "cubic-bezier(.2,.8,.2,1)";
  const RISE = [{ opacity: 0, transform: "translateY(4px)" }, { opacity: 1, transform: "none" }];
  // Web Animations wrapper: no-op under reduced motion or when unsupported.
  const fx = (node, keyframes, opts) => { if (REDUCE || !node || !node.animate) return null; try { return node.animate(keyframes, opts); } catch (e) { return null; } };
  // Tween the leading number of `text` (e.g. "72 / 100", "4.5%") from 0 to its final value, then re-append the suffix.
  function countUp(node, text) {
    if (node._raf) { cancelAnimationFrame(node._raf); node._raf = null; }
    node._target = text;
    const m = /^(-?\d+(?:\.\d+)?)(.*)$/s.exec(String(text));
    if (REDUCE || !m) { node.textContent = text; return; }
    const target = parseFloat(m[1]); const suffix = m[2]; const decimals = (m[1].split(".")[1] || "").length;
    const t0 = performance.now(); const dur = 700;
    const step = (now) => {
      const t = Math.min(1, (now - t0) / dur); const e = 1 - Math.pow(1 - t, 3);
      node.textContent = (target * e).toFixed(decimals) + suffix;
      if (t < 1) node._raf = requestAnimationFrame(step); else { node._raf = null; node.textContent = text; }
    };
    node.textContent = (0).toFixed(decimals) + suffix;
    node._raf = requestAnimationFrame(step);
  }
  // Static update that leaves a running count-up alone when it is already heading for the same text.
  function setNumber(node, text) {
    if (node._raf && node._target === text) return;
    if (node._raf) { cancelAnimationFrame(node._raf); node._raf = null; }
    node._target = text; node.textContent = text;
  }
  const skeleton = (n = 3, cls = "") => Array.from({ length: n }, (_, i) => `<span class="skeleton ${cls}" style="width:${[92, 76, 58, 84][i % 4]}%"></span>`).join("");

  const STEPS = [
    { key: "read", title: "Read", sub: "Gemini multimodal → structured JSON (response_schema + Pydantic)" },
    { key: "verify", title: "Verify", sub: "Python arithmetic + Gemini reasoning, merged (conservative status wins)" },
    { key: "score", title: "Score", sub: "Deterministic scorecard — no LLM" },
    { key: "explain", title: "Explain", sub: "Gemini writes; gains come from re-running the scorer; number guardrail" },
    { key: "write", title: "Write", sub: "Gemini drafts the credit memo with an evidence appendix" },
  ];

  const state = { config: null, currentId: null, assessment: null, lang: "en", bankSelected: null, es: null, revealedId: null, explainedId: null, scoreKey: null, planKey: null, pipelineId: null, bankFocus: false };

  // A 422 from FastAPI carries a list of {loc, msg}; flatten it so the message is readable, not "[object Object]".
  async function failure(r) { const d = (await r.json().catch(() => ({}))).detail; return new Error(Array.isArray(d) ? d.map((x) => (x.loc ? x.loc.slice(-1)[0] + ": " : "") + x.msg).join("; ") : (d || r.statusText)); }
  async function getJSON(url) { const r = await fetch(url); if (!r.ok) throw await failure(r); return r.json(); }
  async function postJSON(url, body) { const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) }); if (!r.ok) throw await failure(r); return r.json(); }
  async function postForm(url, fd) { const r = await fetch(url, { method: "POST", body: fd }); if (!r.ok) throw await failure(r); return r.json(); }

  // ---------------------------------------------------------------- tabs
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
  function showView(name) {
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + name));
    if (name === "bank") loadBank();
    if (name === "trust") loadTrust();
  }

  // ---------------------------------------------------------------- init
  async function init() {
    renderPipeline(null);
    try {
      state.config = await getJSON("/api/config");
      $("#modelChip").textContent = "model: " + state.config.model;
      $("#modeChip").textContent = "mode: " + state.config.mode;
      if (!state.config.gemini_configured) $("#modeChip").classList.add("chip-warn");
    } catch (e) { $("#modeChip").textContent = "config unavailable"; }
    try {
      const list = await getJSON("/api/assessments");
      const done = list.assessments.find((a) => a.status === "done");
      if (done) { await loadAssessment(done.id); }
    } catch (e) { /* ignore */ }
  }

  // ---------------------------------------------------------------- form + files
  const files = [];
  const fileInput = $("#fileInput");
  const dz = $("#dropzone");
  fileInput.addEventListener("change", () => { for (const f of fileInput.files) files.push(f); fileInput.value = ""; renderChips(); });
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
  dz.addEventListener("drop", (e) => { for (const f of e.dataTransfer.files) files.push(f); renderChips(); });
  const seenChips = new WeakSet();
  function renderChips() {
    const box = $("#fileChips");
    box.innerHTML = "";
    files.forEach((f) => {
      const c = el("span", "chip" + (seenChips.has(f) ? "" : " new"), `${esc(f.name)} <small>(${(f.size / 1024).toFixed(0)} KB)</small> &times;`);
      seenChips.add(f);
      c.title = "remove"; c.style.cursor = "pointer";
      c.addEventListener("click", (e) => {
        e.preventDefault(); // never let the click re-dispatch to the file input
        const remove = () => { const idx = files.indexOf(f); if (idx >= 0) files.splice(idx, 1); renderChips(); };
        const anim = fx(c, [{ opacity: 1, transform: "none" }, { opacity: 0, transform: "scale(.9)" }], { duration: 160, easing: EASE, fill: "forwards" });
        if (anim) { c.classList.add("removing"); anim.onfinish = remove; } else remove();
      });
      box.appendChild(c);
    });
  }

  $("#loanForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("#formError"); err.hidden = true;
    const fd = new FormData($("#loanForm"));
    fd.delete("files");
    files.forEach((f) => fd.append("files", f));
    if (!String(fd.get("business_name") || "").trim()) { err.textContent = "Enter the business name."; err.hidden = false; $("#loanForm [name=business_name]").focus(); return; }
    if (!files.length) { err.textContent = "Add at least one document: a ledger photo, a bank statement or a payment export."; err.hidden = false; return; }
    const btn = $("#runBtn"); btn.disabled = true; btn.textContent = "Running…";
    try {
      const created = await postForm("/api/assessments", fd);
      state.currentId = created.id; state.pipelineId = created.id;
      state.assessment = null;
      $("#passportCard").hidden = true;
      renderPipeline(null);
      runStream(created.id);
    } catch (ex) {
      err.textContent = ex.message; err.hidden = false; btn.disabled = false; btn.textContent = "Run assessment";
    }
  });

  // ---------------------------------------------------------------- pipeline (SSE)
  function renderPipeline(statusMap) {
    const ol = $("#pipeline"); ol.innerHTML = "";
    // Entrance + check-pop motion only on the live list (submit/init); a completed re-render is silent.
    ol.classList.toggle("live", !statusMap);
    STEPS.forEach((s, i) => {
      const st = statusMap?.[s.key] || { status: "idle" };
      const li = el("li");
      li.id = "step-" + s.key;
      li.style.setProperty("--i", i);
      li.appendChild(el("span", "dot " + (st.status === "idle" ? "" : st.status)));
      const mid = el("div");
      mid.appendChild(el("div", "step-title", esc(s.title)));
      mid.appendChild(el("div", "step-sub", esc(s.sub)));
      const log = el("div", "step-log" + (st.status === "error" ? " err" : ""), esc(st.message || ""));
      log.id = "log-" + s.key;
      mid.appendChild(log);
      li.appendChild(mid);
      const lat = el("span", "latency", st.latency_ms != null ? (st.latency_ms / 1000).toFixed(1) + "s" : "");
      lat.id = "lat-" + s.key;
      li.appendChild(lat);
      ol.appendChild(li);
    });
  }
  function updateStep(ev) {
    const li = $("#step-" + ev.step); if (!li) return;
    const dot = li.querySelector(".dot"); dot.className = "dot " + ev.status;
    const log = $("#log-" + ev.step); log.textContent = ev.message || ""; log.classList.toggle("err", ev.status === "error");
    if (ev.message) fx(log, RISE, { duration: 240, easing: EASE });
    if (ev.latency_ms != null) { const lat = $("#lat-" + ev.step); lat.textContent = (ev.latency_ms / 1000).toFixed(1) + "s"; fx(lat, RISE, { duration: 120, easing: EASE }); }
  }
  // Completion sync: update the live rows in place (no rebuild, so no replayed entrance or check pops at the
  // "done" moment). Live log lines and wall latencies the audience just watched are kept; trace text only fills gaps.
  function syncPipeline(map) {
    for (const s of STEPS) {
      const st = map[s.key]; const li = $("#step-" + s.key); if (!st || !li) continue;
      const dot = li.querySelector(".dot"); const cls = "dot " + st.status; if (dot.className !== cls) dot.className = cls;
      const log = $("#log-" + s.key);
      if (st.status === "error" || !log.textContent) log.textContent = st.message || "";
      log.classList.toggle("err", st.status === "error");
      const lat = $("#lat-" + s.key);
      if (!lat.textContent && st.latency_ms != null) lat.textContent = (st.latency_ms / 1000).toFixed(1) + "s";
    }
  }
  function runStream(id) {
    if (state.es) state.es.close();
    const t0 = Date.now();
    $("#pipelineMeta").textContent = "running…";
    const es = new EventSource(`/api/assessments/${id}/stream`);
    state.es = es;
    es.onmessage = async (m) => {
      const ev = JSON.parse(m.data);
      if (ev.step === "pipeline") {
        es.close(); state.es = null;
        $("#pipelineMeta").textContent = (ev.status === "done" ? "done" : "error") + ` · ${((Date.now() - t0) / 1000).toFixed(1)}s wall time`;
        const btn = $("#runBtn"); btn.disabled = false; btn.textContent = "Run assessment";
        if (ev.status === "error") { const err = $("#formError"); err.textContent = ev.message; err.hidden = false; }
        await loadAssessment(id);
        return;
      }
      updateStep(ev);
      if (ev.status === "done" && (ev.step === "score" || ev.step === "explain")) await loadAssessment(id, true);
    };
    es.onerror = () => {
      if (state.es) { es.close(); state.es = null; $("#pipelineMeta").textContent = "connection lost"; const btn = $("#runBtn"); btn.disabled = false; btn.textContent = "Run assessment"; loadAssessment(id); }
    };
  }

  // ---------------------------------------------------------------- passport
  async function loadAssessment(id, partial = false) {
    try {
      const a = await getJSON(`/api/assessments/${id}`);
      state.currentId = id; state.assessment = a;
      if (!partial && a.trace?.length) {
        const map = {};
        for (const s of STEPS) {
          const t = a.trace.filter((x) => x.step === s.key);
          if (t.length) map[s.key] = { status: t.every((x) => x.schema_valid) ? "done" : "error", latency_ms: t.reduce((n, x) => n + x.latency_ms, 0), message: [...new Set(t.map((x) => x.error || x.note).filter(Boolean))].join(" · ") };
        }
        if (a.status === "done" || a.status === "error") {
          if (state.pipelineId === id && $("#step-read")) syncPipeline(map);
          else { renderPipeline(map); state.pipelineId = id; }
        }
        $("#pipelineMeta").textContent = a.status + (a.mode ? ` · ${a.mode}` : "");
      }
      if (a.score) renderPassport(a);
    } catch (e) { console.error(e); }
  }

  function renderPassport(a) {
    const s = a.score; const v = a.verify;
    // Reveal guard: bars, count-up, flags, chart and plan animate once per assessment. It must survive re-renders:
    // runStream fires several reloads that can resolve within one frame (warm cache / offline), so a later render
    // with unchanged data must not rebuild — and thereby cancel — a reveal that has not painted yet.
    const fresh = state.revealedId !== a.id; if (fresh) state.revealedId = a.id;
    const freshExplain = !!a.explain && state.explainedId !== a.id; if (freshExplain) state.explainedId = a.id;
    const scoreKey = a.id + ":" + JSON.stringify([s, v?.flags ?? null, v?.monthly_series ?? null]);
    const sameScore = !fresh && state.scoreKey === scoreKey; state.scoreKey = scoreKey;
    const planKey = a.id + ":" + (a.explain ? "explain" : "sim" + (a.simulations?.length || 0));
    const samePlan = !fresh && !freshExplain && state.planKey === planKey; state.planKey = planKey;
    $("#passportCard").hidden = false;
    $("#passportMeta").textContent = `${a.declared.business_name} · ${a.alias} · ${s.scorecard_version}`;
    const badge = $("#gradeBadge"); badge.className = "grade-badge g-" + s.grade;
    $("#gradeText").textContent = s.grade; $("#gradeLabel").textContent = s.grade_label;
    const points = `${s.total_points} / ${s.max_points}`, pd = pct(s.pd_illustrative);
    if (fresh) { countUp($("#kpiPoints"), points); countUp($("#kpiPd"), pd); }
    else { setNumber($("#kpiPoints"), points); setNumber($("#kpiPd"), pd); }
    $("#kpiRate").textContent = s.rate_band;
    $("#kpiLimit").textContent = s.suggested_limit_mnt > 0 ? `${fmtM(s.suggested_limit_mnt)} · ${s.suggested_tenor_months} mo` : "no standard offer";
    const dh = { auto_approve_candidate: "Decision hint: auto-approve candidate (human still signs off).", refer_to_underwriter: "Decision hint: refer to underwriter — a human reviews this file.", decline_candidate: "Decision hint: decline candidate — see improvement plan." };
    $("#decisionHint").textContent = `${dh[s.decision_hint] || s.decision_hint} Reference installment ${fmtMNT(s.reference_monthly_installment_mnt)}/month at ${pct(0.2, 0)} reference rate.`;

    if (!sameScore) {
      const fxBox = $("#factors"); fxBox.innerHTML = ""; fxBox.classList.toggle("anim", fresh);
      s.factors.forEach((f, i) => {
        const row = el("div", "factor"); row.style.setProperty("--i", i);
        row.appendChild(el("div", "", `<div class="factor-label">${esc(f.label)}</div><div class="factor-raw">${esc(f.raw_display)}</div>`));
        const track = el("div", "track");
        const bar = el("div", "bar " + f.verification_status); bar.style.width = Math.max(2, (f.points / f.max_points) * 100) + "%";
        track.appendChild(bar); row.appendChild(track);
        row.appendChild(el("div", "factor-pts", `<b>${f.points}</b> / ${f.max_points}<br><small>${esc(f.reason_code)} · ×${f.weight_applied}</small>`));
        row.appendChild(el("div", "reason", esc(f.reason)));
        fxBox.appendChild(row);
      });
      $("#penalties").innerHTML = s.penalties.length ? "Penalties: " + s.penalties.map((p) => `${esc(p.code)} ${p.points}`).join(" · ") : "No penalties.";

      const fl = $("#flags"); fl.innerHTML = ""; fl.classList.toggle("anim", fresh);
      if (!v?.flags?.length) fl.appendChild(el("li", "", "No flags."));
      (v?.flags || []).forEach((f, i) => { const li = el("li", f.severity, `<b>${esc(f.severity)}</b> ${esc(f.message)}<span class="src">${f.source === "gemini" ? "model observation · no score impact" : "deterministic · " + esc(f.code)}</span>`); li.style.setProperty("--i", i); fl.appendChild(li); });

      $("#miniChart").classList.toggle("anim", fresh);
      renderMiniChart(v?.monthly_series || []);
    }

    if (!samePlan) {
      const plan = $("#plan"); const summary = $("#ownerSummary");
      plan.classList.toggle("anim", fresh || freshExplain);
      summary.classList.toggle("anim", freshExplain);
      if (a.explain) {
        renderSummary(a.explain);
        const g = a.explain.guardrail;
        $("#explainGuardrail").textContent = `Guardrail: ${g.numbers_checked} numbers checked · ${g.note}${g.violations?.length ? " · flagged: " + g.violations.join(", ") : ""}`;
        plan.innerHTML = "";
        if (!a.explain.improvement_plan.length) plan.innerHTML = '<li class="muted">Nothing to improve — every simulated action was re-scored and none raises the total.</li>';
        a.explain.improvement_plan.forEach((p, i) => { const li = el("li", "", `<b>${esc(p.action)}</b><span class="impact">${esc(p.expected_grade_impact)}</span><div class="why">${esc(p.why)}</div>`); li.style.setProperty("--i", i); plan.appendChild(li); });
      } else {
        summary.innerHTML = `<span class="sr-only muted">Explanation pending…</span>${skeleton(3)}`;
        summary.setAttribute("aria-label", "Explanation pending");
        plan.innerHTML = a.simulations?.length ? a.simulations.map((p, i) => `<li style="--i:${i}"><b>${esc(p.label)}</b><span class="impact">${esc(p.expected_grade_impact)}</span><div class="why">${esc(p.change)}</div></li>`).join("") : "";
      }
    }
    renderOffersBusiness(a);
  }
  function renderSummary(ex) { const n = $("#ownerSummary"); n.removeAttribute("aria-label"); n.textContent = state.lang === "mn" ? (ex.owner_summary_mn || ex.owner_summary) : ex.owner_summary; }
  $("#langToggle").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    state.lang = b.dataset.lang;
    document.querySelectorAll("#langToggle button").forEach((x) => x.classList.toggle("active", x === b));
    if (state.assessment?.explain) renderSummary(state.assessment.explain);
  });

  function renderMiniChart(series) {
    const box = $("#miniChart"); box.innerHTML = "";
    const rows = series.filter((r) => r.bank_inflow != null || r.ledger_sales != null);
    if (!rows.length) { box.innerHTML = '<span class="muted">No monthly series.</span>'; return; }
    const W = 420, H = 150, pad = 24;
    const max = Math.max(...rows.map((r) => Math.max(r.bank_inflow || 0, r.ledger_sales || 0, r.payment_net || 0))) || 1;
    const bw = (W - pad * 2) / rows.length;
    let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="--n:${rows.length}">`;
    rows.forEach((r, i) => {
      const x = pad + i * bw;
      const h = (r.bank_inflow || 0) / max * (H - 40);
      svg += `<rect class="bar-bank" style="--i:${i}" x="${x + 2}" y="${H - 20 - h}" width="${bw - 4}" height="${h}" rx="2"><title>${r.month}: bank ${fmtMNT(r.bank_inflow)}</title></rect>`;
      if (r.ledger_sales != null) { const hy = H - 20 - r.ledger_sales / max * (H - 40); svg += `<line class="tick" x1="${x + 2}" x2="${x + bw - 2}" y1="${hy}" y2="${hy}"><title>${r.month}: ledger ${fmtMNT(r.ledger_sales)}</title></line>`; }
      if (r.payment_net != null) { const hp = (r.payment_net / max) * (H - 40); svg += `<rect class="pay" x="${x + bw * 0.3}" y="${H - 20 - hp}" width="${bw * 0.4}" height="${hp}"><title>${r.month}: payments ${fmtMNT(r.payment_net)}</title></rect>`; }
      if (i % 2 === 0) svg += `<text x="${x + bw / 2}" y="${H - 6}" text-anchor="middle">${r.month.slice(2)}</text>`;
    });
    svg += `</svg><div class="muted chart-key">bars = bank inflows · ticks = ledger sales · dark = payment settlements</div>`;
    box.innerHTML = svg;
  }

  function renderOffersBusiness(a) {
    const box = $("#offersBusiness"); box.innerHTML = "";
    if (!a.offers?.length) return;
    box.appendChild(el("h3", "", "Offers received"));
    const list = el("div", "offers-list");
    a.offers.forEach((o) => {
      const d = el("div", "offer", `<span><b>${esc(o.bank_name)}</b> · ${fmtMNT(o.amount_mnt)} · ${o.tenor_months} months · ${pct(o.annual_rate, 1)} p.a. · <i>${o.status}</i></span>`);
      if (o.status === "offered") {
        const acc = el("button", "btn btn-small btn-primary", "Accept (reveals identity)"); acc.addEventListener("click", async () => { await postJSON(`/api/assessments/${a.id}/offers/${o.id}/accept`); await loadAssessment(a.id, true); });
        const dec = el("button", "btn btn-small", "Decline"); dec.addEventListener("click", async () => { await postJSON(`/api/assessments/${a.id}/offers/${o.id}/decline`); await loadAssessment(a.id, true); });
        const r = el("span", "row"); r.appendChild(acc); r.appendChild(dec); d.appendChild(r);
      }
      list.appendChild(d);
    });
    box.appendChild(list);
  }

  // ---------------------------------------------------------------- bank
  $("#bankRefresh").addEventListener("click", loadBank);
  async function loadBank() {
    const list = $("#bankList");
    const enter = !list.querySelector(".item"); // rows animate in on first fill only; selection re-renders are silent
    list.innerHTML = list.children.length ? list.innerHTML : skeleton(3, "row");
    try {
      const data = await getJSON("/api/bank/passports");
      list.classList.toggle("enter", enter);
      list.innerHTML = "";
      if (!data.passports.length) { list.innerHTML = '<p class="muted">No passports yet. Run an assessment in the Business view.</p>'; return; }
      data.passports.forEach((p, i) => {
        const selected = state.bankSelected === p.id;
        const item = el("div", "item" + (selected ? " selected" : "")); item.style.setProperty("--i", i);
        item.tabIndex = 0; item.setAttribute("role", "button"); item.setAttribute("aria-pressed", String(selected));
        item.innerHTML = `<div><b>${esc(p.identity_revealed ? p.business_name : p.alias)}</b><div class="muted">${esc(p.sector)} · ${esc(p.location)} · ${fmtM(p.loan_amount_mnt)} / ${p.tenor_months} mo</div><div class="muted">${p.offers.length} offer(s) · ${p.identity_revealed ? "identity revealed" : "identity hidden"}</div></div><div class="gpill g-${p.grade}">${p.grade}</div>`;
        item.addEventListener("click", () => { state.bankSelected = p.id; loadBank(); renderBankDetail(p.id); });
        item.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); state.bankFocus = true; item.click(); } });
        list.appendChild(item);
      });
      if (!state.bankSelected && data.passports.length) { state.bankSelected = data.passports[0].id; renderBankDetail(state.bankSelected); list.firstChild.classList.add("selected"); list.firstChild.setAttribute("aria-pressed", "true"); }
      // keyboard selection rebuilt the list: put focus back on the selected row
      if (state.bankFocus) { state.bankFocus = false; list.querySelector(".item.selected")?.focus(); }
    } catch (e) { list.innerHTML = `<p class="error">${esc(e.message)}</p>`; }
  }
  async function renderBankDetail(id) {
    const box = $("#bankDetail"); box.innerHTML = `<div aria-label="Loading"><span class="skeleton" style="width:40%;height:22px"></span>${skeleton(4)}</div>`;
    try {
      const p = await getJSON(`/api/bank/passports/${id}`);
      const mid = p.rate_min != null ? (p.rate_min + p.rate_max) / 2 : 0.25;
      box.innerHTML = `
        <div class="card-head"><h2>${p.identity_revealed ? esc(p.business_name) : "Passport " + esc(p.alias)}</h2><span class="gpill g-${p.grade}">${p.grade} · ${esc(p.grade_label)}</span></div>
        ${p.identity_revealed ? `<p class="muted">Owner: ${esc(p.owner_name)} — identity revealed after the business accepted an offer.</p>` : '<div class="hidden-id" title="You see the sector, location, request and verified figures only.">Identity hidden until the business accepts an offer.</div>'}
        <div class="kpis" style="margin-top:12px">
          <div class="kpi"><div class="kpi-label">Request</div><div class="kpi-value">${fmtM(p.loan_amount_mnt)} · ${p.tenor_months} mo</div></div>
          <div class="kpi"><div class="kpi-label">Points · PD</div><div class="kpi-value">${p.points} · ${pct(p.pd)}</div></div>
          <div class="kpi"><div class="kpi-label">Rate band</div><div class="kpi-value">${esc(p.rate_band)}</div></div>
          <div class="kpi"><div class="kpi-label">Suggested</div><div class="kpi-value">${p.suggested_limit_mnt > 0 ? fmtM(p.suggested_limit_mnt) + " · " + p.suggested_tenor_months + " mo" : "no standard offer"}</div></div>
        </div>
        <p class="muted" style="margin-top:8px">${esc(p.sector)} · ${esc(p.location)} · ${p.years_in_business} years · purpose: ${esc(p.purpose)} · decision hint: <b>${esc(p.decision_hint)}</b></p>
        <h3>Flags</h3>
        <ul class="flags">${(p.flags || []).filter((f) => f.severity !== "info").map((f) => `<li class="${f.severity}"><b>${esc(f.severity)}</b> ${esc(f.message)}</li>`).join("") || "<li>No warning or critical flags.</li>"}</ul>
        <h3>Make an offer</h3>
        <div class="offer-form">
          <label class="field"><span>Bank</span><input id="ofBank" placeholder="Your bank" required /></label>
          <label class="field"><span>Amount (₮)</span><input id="ofAmount" type="number" step="100000" value="${Math.round(p.suggested_limit_mnt || p.loan_amount_mnt)}" /></label>
          <label class="field"><span>Tenor (months)</span><input id="ofTenor" type="number" value="${p.suggested_tenor_months || p.tenor_months}" /></label>
          <label class="field"><span>Rate (% p.a.)</span><input id="ofRate" type="number" step="0.5" value="${(mid * 100).toFixed(1)}" /></label>
          <button class="btn btn-primary" id="ofBtn">Make offer</button>
        </div>
        <div class="offers-list" id="bankOffers">${(p.offers || []).map((o) => `<div class="offer"><span>${esc(o.bank_name)} · ${fmtMNT(o.amount_mnt)} · ${o.tenor_months} mo · ${pct(o.annual_rate)}</span><span class="chip ${o.status === "accepted" ? "chip-ok" : o.status === "declined" ? "chip-bad" : ""}">${o.status}</span></div>`).join("")}</div>
        <div class="card-head" style="margin-top:32px;margin-bottom:12px"><h3 style="margin:0" title="Gemini draft · deterministic scorecard section · evidence appendix">Credit memo</h3><button class="btn btn-small" id="memoDl">Download .md</button></div>
        ${p.memo_guardrail ? `<div class="guardrail-note muted">Guardrail: ${p.memo_guardrail.numbers_checked} numbers checked · ${esc(p.memo_guardrail.note)}${p.memo_guardrail.violations?.length ? " · flagged: " + esc(p.memo_guardrail.violations.join(", ")) : ""}</div>` : ""}
        <div class="memo">${p.memo_html || '<p class="muted">Memo not available (WRITE step did not complete).</p>'}</div>`;
      fx(box, [{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "none" }], { duration: 240, easing: EASE });
      $("#ofBtn").addEventListener("click", async () => {
        const bank = $("#ofBank").value.trim();
        if (!bank) { $("#ofBank").focus(); return; }
        const amount = +$("#ofAmount").value, tenor = +$("#ofTenor").value, ratePct = +$("#ofRate").value;
        if (!(amount > 0)) { alert("Enter an offer amount greater than zero."); $("#ofAmount").focus(); return; }
        if (!(tenor >= 1 && tenor <= 60)) { alert("Tenor must be between 1 and 60 months."); $("#ofTenor").focus(); return; }
        if (!(ratePct >= 0 && ratePct <= 100)) { alert("Rate must be between 0 and 100 % p.a."); $("#ofRate").focus(); return; }
        try {
          await postJSON(`/api/assessments/${id}/offers`, { bank_name: bank, amount_mnt: amount, tenor_months: Math.round(tenor), annual_rate: ratePct / 100 });
          await renderBankDetail(id); await loadBank();
          if (state.currentId === id) await loadAssessment(id, true);
        } catch (e) { alert(e.message); }
      });
      $("#memoDl").addEventListener("click", () => {
        const blob = new Blob([p.memo_markdown || ""], { type: "text/markdown" });
        const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `credit-memo-${p.alias}.md`; a.click();
      });
    } catch (e) { box.innerHTML = `<p class="error">${esc(e.message)}</p>`; }
  }

  // ---------------------------------------------------------------- trust
  async function loadTrust() {
    const sel = $("#trustSelect");
    try {
      const list = await getJSON("/api/assessments");
      sel.innerHTML = "";
      list.assessments.filter((a) => a.status !== "created").forEach((a) => { const o = el("option"); o.value = a.id; o.textContent = `${a.alias} · ${a.business_name} · ${a.grade || a.status}`; sel.appendChild(o); });
      const id = state.currentId && list.assessments.some((a) => a.id === state.currentId) ? state.currentId : (list.assessments[0]?.id);
      if (id) { sel.value = id; renderTrust(id); }
    } catch (e) { /* ignore */ }
    sel.onchange = () => renderTrust(sel.value);
    const g = $("#guardrails"); g.innerHTML = "";
    (state.config?.guardrails || []).forEach((x, i) => { const d = el("div", "guardrail", `<b>${esc(x.title)}</b>${esc(x.detail)}`); d.style.setProperty("--i", i); g.appendChild(d); });
  }
  async function renderTrust(id) {
    const a = await getJSON(`/api/assessments/${id}`);
    const tt = $("#traceTable");
    tt.innerHTML = `<tr><th>Step</th><th>Prompt</th><th>Model</th><th>Latency</th><th>Schema valid</th><th>Attempts</th><th>Cached</th><th>Inputs</th><th>Note / error</th></tr>` +
      (a.trace || []).map((t) => `<tr><td><b>${esc(t.step)}</b></td><td class="mono">${esc(t.prompt_name)} v${esc(t.prompt_version)}</td><td class="mono">${esc(t.model)}</td><td>${(t.latency_ms / 1000).toFixed(2)}s</td><td>${t.schema_valid ? "✔" : "✖"}</td><td>${t.attempts}</td><td>${t.cached ? "yes" : "no"}</td><td class="muted">${esc((t.input_refs || []).join(", "))}</td><td class="${t.error ? "error" : "muted"}">${esc(t.error || t.note || "")}</td></tr>`).join("");
    const it = $("#inputsTable");
    it.innerHTML = `<tr><th>Input</th><th>Value</th><th>Status</th><th>Basis</th></tr>` +
      (a.verify?.verified_inputs || []).map((v) => `<tr><td class="mono">${esc(v.name)}</td><td>${v.name.includes("mnt") ? fmtMNT(v.value) : v.value}</td><td><span class="status ${v.status}">${esc(v.status.replace("_", " "))}</span></td><td class="muted">${esc(v.basis)}</td></tr>`).join("");
    const ct = $("#checksTable");
    ct.innerHTML = `<tr><th>Check</th><th>Status</th><th>Source</th><th>Detail</th><th>Evidence</th></tr>` +
      (a.verify?.checks || []).map((c) => `<tr><td class="mono">${esc(c.check_name)}</td><td><span class="status ${c.status}">${esc(c.status.replace("_", " "))}</span></td><td><span class="status ${c.source}">${esc(c.source)}</span></td><td>${esc(c.detail)}</td><td class="muted">${esc((c.evidence_refs || []).filter(Boolean).join("; "))}</td></tr>`).join("") +
      (a.verify?.flags || []).map((f) => `<tr><td class="mono">flag ${esc(f.code)}</td><td><span class="status sev-${esc(f.severity)}">${esc(f.severity)}</span></td><td><span class="status ${f.source}">${esc(f.source)}</span></td><td>${esc(f.message)}</td><td class="muted">${esc((f.evidence_refs || []).filter(Boolean).join("; "))}</td></tr>`).join("");
    const dl = $("#docLinks"); dl.innerHTML = "";
    (a.documents || []).forEach((d) => { const link = el("a", "chip", `${esc(d.filename)} ↗`); link.href = `/api/assessments/${a.id}/documents/${d.doc_id}`; link.target = "_blank"; dl.appendChild(link); });
    const et = $("#evidenceTable");
    const rows = [];
    (a.reads || []).forEach((r) => {
      r.result.monthly_figures.forEach((f) => rows.push({ figure: `${r.result.document_type.replace("_", " ")} · ${f.month}`, value: fmtMNT(f.sales_or_inflows_mnt) + (f.expenses_or_outflows_mnt != null ? ` / out ${fmtMNT(f.expenses_or_outflows_mnt)}` : ""), doc: f.source_document, ref: f.page_or_row, conf: null }));
      r.result.extracted_fields.forEach((f) => rows.push({ figure: f.field.replace(/_/g, " "), value: f.value, doc: f.source_document, ref: f.page_or_row, conf: f.confidence }));
    });
    et.innerHTML = `<tr><th>Figure</th><th>Value</th><th>Source document</th><th>Page / row</th><th>Confidence</th></tr>` +
      rows.map((x) => `<tr><td>${esc(x.figure)}</td><td>${esc(x.value)}</td><td class="mono">${esc(x.doc)}</td><td class="mono">${esc(x.ref)}</td><td>${x.conf == null ? '<span class="muted">—</span>' : `<span class="conf"><i style="width:${Math.round(x.conf * 100)}%"></i></span>${(x.conf * 100).toFixed(0)}%`}</td></tr>`).join("");
    if (a.reads?.length) {
      const notes = a.reads.flatMap((r) => r.result.quality_notes.map((n) => `${r.filename}: ${n}`));
      if (notes.length) et.innerHTML += `<tr><td colspan="5" class="muted">Quality notes — ${esc(notes.join(" · "))}</td></tr>`;
    }
  }

  init();
})();
