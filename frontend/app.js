/*
 * pgsonar demo UI.
 *
 * BACKEND CONTRACT
 * ----------------
 * GET {API}/scan  ->  text/event-stream. Each SSE message has an `event:` name and a JSON `data:` payload.
 * (API defaults to same origin; override with ?api=http://localhost:8000)
 *
 *   rls          {tables: [{name: "orders", rls: false}, ...]}          deterministic RLS pre-check
 *   plan         {text: "- bullet\n- bullet"}                            planner output
 *   tool_call    {id: "c1", name: "check_iqr_outlier", args: {...}}      agent invokes a tool
 *   tool_result  {id: "c1", output: "raw tool output string"}            matches a tool_call by id
 *   finding      {id, table, column?, severity: "high"|"medium"|"low",
 *                 title, detail, fix?}                                   one structured anomaly
 *   summary      {text: "final plain-English report (light markdown)"}
 *   done         {}                                                      stream finished; close it
 *   error        {message: "..."}                                        fatal; UI shows it and stops
 *
 * GET {API}/schema -> application/json
 *   {is_mock: bool, tables: [{name: "orders", columns: [{name: "amount", type: "numeric"}, ...]}, ...]}
 *
 * Open the page with ?mock=1 (or tick "Mock data") to replay a scripted scan with no backend.
 * Add &autorun=1 to start immediately.
 */
(function () {
  "use strict";

  var params = new URLSearchParams(location.search);
  var API = (params.get("api") || "").replace(/\/$/, "");

  var $ = function (id) { return document.getElementById(id); };
  var els = {
    run: $("run"), mock: $("mock"), status: $("status"),
    trace: $("trace"), traceEmpty: $("trace-empty"),
    tables: $("s-tables"), anoms: $("s-anoms"), rls: $("s-rls"), calls: $("s-calls"),
    statAnoms: $("stat-anoms"), statRls: $("stat-rls"),
    rlsBox: $("rls-box"), rlsList: $("rls-list"),
    findEmpty: $("find-empty"), findings: $("findings"),
    reportBox: $("report-box"), report: $("report"),
    schemaBtn: $("schema"), schemaDialog: $("schema-dialog"),
    schemaClose: $("schema-close"), schemaBody: $("schema-body"), schemaEmpty: $("schema-empty")
  };

  var runId = 0;          // bumped on every start/stop so stale async work can bail out
  var running = false;
  var source = null;      // active EventSource
  var pending = {};       // tool call id -> {step, sub, holder}
  var counts = { anoms: 0, rls: 0, calls: 0 };

  /* ---------- small DOM helpers (textContent only, so backend text can never inject HTML) ---------- */
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function scrollDown(pane) {
    var nearBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 140;
    if (nearBottom) pane.scrollTop = pane.scrollHeight;
  }

  function setStatus(kind, text) {
    els.status.className = "pill " + kind;
    els.status.textContent = text;
  }

  function fmtArgs(args) {
    if (!args) return "";
    return Object.keys(args).map(function (k) { return k + "=" + JSON.stringify(args[k]); }).join(", ");
  }

  /* Tiny markdown subset: #/## headings, - or * bullets, **bold**, `code`. */
  function inline(parent, text) {
    var re = /(\*\*[^*]+\*\*|`[^`]+`)/g, last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
      var tok = m[0];
      if (tok[0] === "`") parent.appendChild(el("code", null, tok.slice(1, -1)));
      else parent.appendChild(el("strong", null, tok.slice(2, -2)));
      last = m.index + tok.length;
    }
    if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
  }

  function renderMarkdown(target, text) {
    target.textContent = "";
    var list = null;
    String(text || "").split(/\r?\n/).forEach(function (raw) {
      var line = raw.trim();
      var bullet = /^[-*]\s+(.*)$/.exec(line);
      if (bullet) {
        if (!list) { list = el("ul"); target.appendChild(list); }
        var li = el("li"); inline(li, bullet[1]); list.appendChild(li);
        return;
      }
      list = null;
      if (!line) return;
      var h = /^#{1,6}\s+(.*)$/.exec(line);
      if (h) { var h4 = el("h4"); inline(h4, h[1]); target.appendChild(h4); return; }
      var p = el("p"); inline(p, line); target.appendChild(p);
    });
  }

  /* ---------- trace ---------- */
  function addStep(kind, glyph, label, sub) {
    els.traceEmpty.hidden = true;
    var step = el("div", "step " + kind);
    step.appendChild(el("div", "dot", glyph));
    var body = el("div", "body");
    body.appendChild(el("div", "label", label));
    var subEl = null;
    if (sub) { subEl = el("div", "sub"); subEl.textContent = sub; body.appendChild(subEl); }
    step.appendChild(body);
    els.trace.appendChild(step);
    scrollDown(els.trace);
    return { step: step, body: body, sub: subEl };
  }

  /* ---------- results ---------- */
  function bumpStats() {
    els.anoms.textContent = counts.anoms;
    els.rls.textContent = counts.rls;
    els.calls.textContent = counts.calls;
    els.statAnoms.classList.toggle("bad", counts.anoms > 0);
    els.statRls.classList.toggle("bad", counts.rls > 0);
  }

  var handlers = {
    rls: function (d) {
      var tables = d.tables || [];
      els.tables.textContent = tables.length;
      els.rlsList.textContent = "";
      counts.rls = 0;
      tables.forEach(function (t) {
        var li = el("li");
        li.appendChild(el("span", null, t.name));
        li.appendChild(el("span", "chip " + (t.rls ? "on" : "off"), t.rls ? "RLS on" : "RLS off"));
        els.rlsList.appendChild(li);
        if (!t.rls) counts.rls++;
      });
      els.rlsBox.hidden = tables.length === 0;
      bumpStats();
      var off = tables.filter(function (t) { return !t.rls; }).map(function (t) { return t.name; });
      var s = addStep("rls", "R", "Checked row-level security",
        off.length ? "Missing on: " + off.join(", ") : "Enabled on every table.");
      return s;
    },

    plan: function (d) {
      var s = addStep("plan", "P", "Planned the investigation");
      var list = el("ul");
      String(d.text || "").split(/\r?\n/).forEach(function (l) {
        var t = l.replace(/^\s*[-*\d.)]+\s*/, "").trim();
        if (!t) return;
        var li = el("li"); inline(li, t); list.appendChild(li);
      });
      s.body.appendChild(list);
    },

    tool_call: function (d) {
      counts.calls++; bumpStats();
      var s = addStep("tool pending", "›", d.name);
      s.body.querySelector(".label").appendChild(el("code", null, "(" + fmtArgs(d.args) + ")"));
      s.sub = el("div", "sub", "running…");
      s.body.appendChild(s.sub);
      pending[d.id] = s;
    },

    tool_result: function (d) {
      var s = pending[d.id];
      if (!s) return;
      delete pending[d.id];
      s.step.classList.remove("pending");
      var out = String(d.output == null ? "" : d.output);
      var first = out.split(/\r?\n/).filter(Boolean)[0] || "done";
      s.sub.textContent = first.length > 110 ? first.slice(0, 107) + "…" : first;
      var det = el("details");
      det.appendChild(el("summary", null, "Raw output"));
      det.appendChild(el("pre", "out", out));
      s.body.appendChild(det);
      scrollDown(els.trace);
    },

    finding: function (d) {
      els.findEmpty.hidden = true;
      counts.anoms++; bumpStats();
      var sev = ["high", "medium", "low"].indexOf(d.severity) >= 0 ? d.severity : "medium";
      var card = el("div", "finding " + sev);
      var head = el("div", "head");
      head.appendChild(el("span", "chip " + sev, sev));
      head.appendChild(el("span", "title", d.title || "Anomaly"));
      card.appendChild(head);
      if (d.table) card.appendChild(el("div", "where", d.table + (d.column ? "." + d.column : "")));
      if (d.detail) card.appendChild(el("p", null, d.detail));
      if (d.fix) {
        var fix = el("div", "fix");
        fix.appendChild(el("b", null, "Suggested fix: "));
        fix.appendChild(document.createTextNode(d.fix));
        card.appendChild(fix);
      }
      els.findings.appendChild(card);
      card.scrollIntoView({ block: "nearest", behavior: "smooth" });
    },

    summary: function (d) {
      els.reportBox.hidden = false;
      renderMarkdown(els.report, d.text);
      addStep("plan", "✓", "Wrote the final report");
      els.reportBox.scrollIntoView({ block: "nearest", behavior: "smooth" });
    },

    done: function () {
      // Until the backend sends structured findings, the anomalies live in the report text.
      if (!counts.anoms) {
        els.findEmpty.textContent = els.reportBox.hidden
          ? "No anomalies found."
          : "No structured findings yet. See the agent report below.";
      }
      finish("done", "Scan complete");
    },

    error: function (d) {
      addStep("error", "!", "Scan failed", d.message || "Unknown error");
      finish("error", "Error");
    }
  };

  /* ---------- run lifecycle ---------- */
  function reset() {
    els.trace.textContent = "";
    els.trace.appendChild(els.traceEmpty);
    els.traceEmpty.hidden = false;
    els.findings.textContent = "";
    els.findEmpty.textContent = "Findings appear here as the agent uncovers them.";
    els.findEmpty.hidden = false;
    els.rlsList.textContent = "";
    els.rlsBox.hidden = true;
    els.report.textContent = "";
    els.reportBox.hidden = true;
    els.tables.textContent = "0";
    pending = {};
    counts = { anoms: 0, rls: 0, calls: 0 };
    bumpStats();
  }

  function finish(kind, text) {
    running = false;
    runId++;
    if (source) { source.close(); source = null; }
    els.run.textContent = "Run demo scan";
    els.run.classList.remove("stop");
    setStatus(kind, text);
  }

  function dispatch(name, data) {
    var h = handlers[name];
    if (h) h(data || {});
  }

  function start() {
    reset();
    running = true;
    var id = ++runId;
    els.run.textContent = "Stop";
    els.run.classList.add("stop");
    setStatus("running", "Scanning");
    if (els.mock.checked) runMock(id); else runLive(id);
  }

  function stop() {
    finish("idle", "Stopped");
  }

  function runLive(id) {
    var gotAny = false;
    try {
      source = new EventSource(API + "/scan");
    } catch (e) {
      dispatch("error", { message: "Could not open the scan stream." });
      return;
    }
    var names = Object.keys(handlers);
    names.forEach(function (name) {
      source.addEventListener(name, function (ev) {
        if (id !== runId) return;
        // The browser fires its own data-less "error" event on connection failures; onerror handles those.
        if (name === "error" && ev.data === undefined) return;
        gotAny = true;
        var data = {};
        try { data = JSON.parse(ev.data); } catch (e) { /* leave empty */ }
        dispatch(name, data);
      });
    });
    source.onerror = function () {
      if (id !== runId) return;   // finished normally or stopped; the close triggers a benign error
      dispatch("error", {
        message: gotAny
          ? "Lost connection to the backend mid-scan."
          : "Could not reach " + (API || location.origin) + "/scan. Start the backend, or tick “Mock data”."
      });
    };
  }

  /* ---------- mock scan (mirrors the planted anomalies in db_populate.py) ---------- */
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  var MOCK = [
    [500, "rls", { tables: [{ name: "orders", rls: false }, { name: "users", rls: false }] }],
    [1100, "plan", { text:
      "- Start with `orders`: check freshness and the daily row-count trend over 90 days.\n" +
      "- Run IQR outlier detection on `orders.amount`; confirm with a z-score pass.\n" +
      "- Check `users` signup volume for bursts.\n" +
      "- Correlate any spike with recent schema changes.\n" +
      "- Skip `users.email`: text column, no numeric checks apply." }],
    [900, "tool_call", { id: "c1", name: "list_tables", args: {} }],
    [700, "tool_result", { id: "c1", output: "orders(id: integer, user_id: integer, amount: numeric, created_at: timestamp with time zone)\nusers(id: integer, email: text, signup_date: date)" }],
    [800, "tool_call", { id: "c2", name: "get_column_stats", args: { table_name: "orders", col_name: "amount" } }],
    [900, "tool_result", { id: "c2", output: "Stats for amount in orders:\ncount: 341\nmean: 145.72\nstddev: 811.40\nmin: 3.00\nmax: 8874.10\nq1: 34.50\nq3: 56.90" }],
    [800, "tool_call", { id: "c3", name: "check_iqr_outlier", args: { table_name: "orders", col_name: "amount" } }],
    [1000, "tool_result", { id: "c3", output: "Outliers for amount in orders:\nLower bound: 1.15\nUpper bound: 90.25\nOutliers: [5312.44, 6120.9, 7788.05, 8874.1, 5049.37, 6431.8]" }],
    [300, "finding", { id: "f1", table: "orders", column: "amount", severity: "high",
      title: "6 orders with implausibly large amounts",
      detail: "Typical orders sit between $3 and $90. Six orders fall between $5,049 and $8,874, roughly 100x the median. This pattern is consistent with pricing bugs or fraudulent orders.",
      fix: "Hold the six orders for review and add a CHECK constraint or application-level cap on amount." }],
    [900, "tool_call", { id: "c4", name: "check_row_count_trend", args: { table_name: "orders", date_col: "created_at", window_days: 30 } }],
    [1000, "tool_result", { id: "c4", output: "Row count trend for orders based on created_at:\n2026-09-11: 4\n2026-09-12: 3\n2026-09-13: 5\n2026-09-14: 38\n2026-09-15: 4\n2026-09-16: 3" }],
    [300, "finding", { id: "f2", table: "orders", column: "created_at", severity: "medium",
      title: "Order volume spike on 2026-09-14",
      detail: "38 orders landed on a single day against a baseline of 3 to 5. No matching schema change was found in the surrounding 36 hours.",
      fix: "Check for a promotion, a retry storm, or a duplicated batch import on that date." }],
    [900, "tool_call", { id: "c5", name: "check_row_count_trend", args: { table_name: "users", date_col: "signup_date", window_days: 90 } }],
    [1000, "tool_result", { id: "c5", output: "Row count trend for users based on signup_date:\n2026-08-20: 1\n2026-08-21: 0\n2026-08-22: 26\n2026-08-23: 1" }],
    [300, "finding", { id: "f3", table: "users", column: "signup_date", severity: "medium",
      title: "Bot-like signup burst on 2026-08-22",
      detail: "26 accounts registered in one day versus roughly one per day otherwise, several with sequential spike_user email patterns.",
      fix: "Review the accounts, add signup rate limiting or CAPTCHA, and purge confirmed bots." }],
    [900, "tool_call", { id: "c6", name: "check_recent_schema_changes", args: { table: "orders", around_timestamp: "2026-09-14T00:00:00Z", window_hours: 36 } }],
    [800, "tool_result", { id: "c6", output: "[]" }],
    [300, "finding", { id: "f4", table: "orders, users", severity: "high",
      title: "Row-level security is disabled on 2 tables",
      detail: "Both public tables are readable and writable by any role with table grants. On Supabase this can expose data through the auto-generated API.",
      fix: "ALTER TABLE ... ENABLE ROW LEVEL SECURITY, then add policies scoped to auth.uid()." }],
    [1200, "summary", { text:
      "## Summary\n" +
      "Scanned **2 tables** and found **4 issues**.\n" +
      "## What I found\n" +
      "- `orders.amount` has **6 extreme outliers** between $5,049 and $8,874.\n" +
      "- `orders` had a **volume spike** on 2026-09-14 with no related schema change.\n" +
      "- `users` had a **bot-like signup burst** on 2026-08-22.\n" +
      "## Recommended actions\n" +
      "- Enable RLS on `orders` and `users`.\n" +
      "- Add a column-level audit log to tables holding sensitive data, starting with `users.email`.\n" +
      "- Review the flagged orders and accounts before they affect reporting." }],
    [400, "done", {}]
  ];

  function runMock(id) {
    (async function () {
      for (var i = 0; i < MOCK.length; i++) {
        await sleep(MOCK[i][0]);
        if (id !== runId) return;
        dispatch(MOCK[i][1], MOCK[i][2]);
      }
    })();
  }

  /* ---------- schema viewer ---------- */
  var schemaLoaded = false;

  function renderSchema(data) {
    els.schemaBody.textContent = "";
    var tables = (data && data.tables) || [];
    if (!tables.length) {
      els.schemaEmpty.textContent = "No tables found in the public schema.";
      els.schemaBody.appendChild(els.schemaEmpty);
      return;
    }
    if (data.is_mock) {
      var note = el("p", "empty", "Mock data — set SUPABASE_DB_URL to see your real project.");
      els.schemaBody.appendChild(note);
    }
    tables.forEach(function (t) {
      var card = el("div", "schema-table");
      card.appendChild(el("h3", null, t.name));
      var list = el("ul", "schema-cols");
      (t.columns || []).forEach(function (c) {
        var li = el("li");
        li.appendChild(el("span", "col-name", c.name));
        li.appendChild(el("span", "col-type", c.type));
        list.appendChild(li);
      });
      card.appendChild(list);
      els.schemaBody.appendChild(card);
    });
  }

  function openSchema() {
    els.schemaDialog.showModal();
    if (schemaLoaded) return;
    els.schemaBody.textContent = "";
    els.schemaEmpty.textContent = "Loading schema…";
    els.schemaBody.appendChild(els.schemaEmpty);
    fetch((API || "") + "/schema")
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) { schemaLoaded = true; renderSchema(data); })
      .catch(function () {
        els.schemaEmpty.textContent = "Could not load the schema from " + (API || location.origin) + "/schema.";
      });
  }

  /* ---------- wire up ---------- */
  els.mock.checked = params.has("mock");
  els.run.addEventListener("click", function () { if (running) stop(); else start(); });
  els.schemaBtn.addEventListener("click", openSchema);
  els.schemaClose.addEventListener("click", function () { els.schemaDialog.close(); });
  if (params.has("autorun")) start();

  /* PRACTICE: a second, simpler entry point that talks to EventSource directly instead
   * of going through start()/runLive(). Not wired to any button right now -- both this
   * and els.run would otherwise fire two scans per click. Call scan() from the console
   * to try it, or swap it into the els.run listener above once you're ready. */
//   function scan() {
//     const eventSource = new EventSource(API + "/scan");

//     ["rls", "plan", "tool_call", "tool_result", "finding", "summary"].forEach((name) => {
//       eventSource.addEventListener(name, (event) => {
//         dispatch(name, JSON.parse(event.data));
//       });
//     });

//     eventSource.addEventListener("done", (event) => {
//       dispatch("done", JSON.parse(event.data));
//       eventSource.close();   // stop it here, or EventSource reconnects and reruns the scan
//     });

//     eventSource.addEventListener("error", (event) => {
//       // The browser fires its own data-less "error" event on connection failures (server
//       // down, non-200 status, etc). Only a real event: error from the backend carries JSON.
//       if (event.data === undefined) {
//         dispatch("error", { message: "Lost connection to the backend." });
//       } else {
//         dispatch("error", JSON.parse(event.data));
//       }
//       eventSource.close();
//     });

//     return eventSource;
//   }
//   window.scan = scan;   // exposed for console/manual testing only
})();
