function avatarColor(name) {
  var hash = 0;
  var s = String(name || "?");
  for (var i = 0; i < s.length; i++) { hash = (hash * 31 + s.charCodeAt(i)) | 0; }
  var palette = ["#2563eb","#0ea5e9","#10b981","#f59e0b","#ef4444","#8b5cf6","#ec4899","#14b8a6"];
  return palette[Math.abs(hash) % palette.length];
}
function placeholderAvatar(name, cls) {
  var el = document.createElement("span");
  el.className = "avatar fallback" + (cls ? " " + cls : "");
  el.style.background = avatarColor(name);
  el.textContent = (String(name || "?")[0] || "?").toUpperCase();
  return el;
}
function initCustomerFilter() {
  var input = document.getElementById("search-input");
  var country = document.getElementById("filter-country");
  var company = document.getElementById("filter-company");
  var tier = document.getElementById("filter-tier");
  if (!input) return;
  function apply() {
    var q = (input.value || "").trim().toLowerCase();
    var cc = country ? country.value : "";
    var cp = company ? company.value : "";
    var tt = tier ? tier.value : "";
    document.querySelectorAll(".customer-card").forEach(function (card) {
      var hay = (card.getAttribute("data-search") || "").toLowerCase();
      var tierMatch = true;
      if (tt) {
        var m = hay.match(/intent_level=([a-d])/);
        var cur = m ? m[1].toUpperCase() : "";
        tierMatch = (tt === "untiered") ? (cur === "") : (cur === tt);
      }
      var ok = (!q || hay.indexOf(q) >= 0)
        && (!cc || hay.indexOf("country=" + cc.toLowerCase()) >= 0)
        && (!cp || hay.indexOf("company=" + cp.toLowerCase()) >= 0)
        && tierMatch;
      card.style.display = ok ? "" : "none";
    });
  }
  input.addEventListener("input", apply);
  if (country) country.addEventListener("change", apply);
  if (company) company.addEventListener("change", apply);
  if (tier) tier.addEventListener("change", apply);
}
document.addEventListener("DOMContentLoaded", function () {
  initCustomerFilter();
  initWorkspaceFilter();
  initWorkspacePoll();
  // workspace-layout: 左栏客户选中态 (事件委托, 兼容 htmx 动态插入)
  document.addEventListener("click", function (e) {
    var row = e.target.closest ? e.target.closest(".ws-customer") : null;
    if (!row) return;
    document.querySelectorAll(".ws-customer.active").forEach(function (r) {
      r.classList.remove("active");
    });
    row.classList.add("active");
  });
  // workspace-layout: 右栏 Tab 切换 (事件委托, 兼容 htmx 动态插入)
  document.addEventListener("click", function (e) {
    var tab = e.target.closest ? e.target.closest(".ws-tab") : null;
    if (!tab) return;
    var name = tab.getAttribute("data-tab");
    if (!name) return;
    var scope = tab.closest(".ws-right");
    if (!scope) return;
    scope.querySelectorAll(".ws-tab").forEach(function (t) { t.classList.remove("active"); });
    scope.querySelectorAll(".ws-tab-pane").forEach(function (p) { p.classList.remove("active"); });
    tab.classList.add("active");
    var pane = scope.querySelector("#pane-" + name);
    if (pane) pane.classList.add("active");
  });
  document.querySelectorAll(".avatar-holder[data-name]").forEach(function (h) {
    if (!h.querySelector("img")) { h.appendChild(placeholderAvatar(h.getAttribute("data-name"), h.getAttribute("data-avatar-class"))); }
  });
});
// workspace-layout: 左栏客户搜索过滤
// workspace-reply-profile: 扩展支持意向等级筛选 (与搜索叠加)
function initWorkspaceFilter() {
  var input = document.getElementById("ws-search");
  var tier = document.getElementById("ws-tier");
  if (!input) return;
  function apply() {
    var q = (input.value || "").trim().toLowerCase();
    var tt = tier ? tier.value : "";
    document.querySelectorAll(".ws-customer").forEach(function (row) {
      var hay = (row.getAttribute("data-search") || "").toLowerCase();
      var tierMatch = true;
      if (tt) {
        var m = hay.match(/intent_level=([a-d])/);
        var cur = m ? m[1].toUpperCase() : "";
        tierMatch = (tt === "untiered") ? (cur === "") : (cur === tt);
      }
      var ok = (!q || hay.indexOf(q) >= 0) && tierMatch;
      row.style.display = ok ? "" : "none";
    });
  }
  input.addEventListener("input", apply);
  if (tier) tier.addEventListener("change", apply);
}
// workspace-live-refresh: 中栏聊天增量轮询 (JS setInterval + htmx.ajax, 5s)
// 注意: .ws-chat-poll 是点击客户后由 htmx 动态插入的, 需在每次 #ws-center 更新后重新初始化。
var _wsPollTimer = null;
function initWorkspacePoll() {
  var POLL_MS = 5000;
  // 清理旧轮询 (切换客户/会话时避免重复 setInterval)
  if (_wsPollTimer) { clearInterval(_wsPollTimer); _wsPollTimer = null; }
  var pollEl = document.querySelector(".ws-chat-poll");
  if (!pollEl) return;
  var chatId = pollEl.getAttribute("data-chat-id");
  var customerId = pollEl.getAttribute("data-customer-id");
  if (!chatId || !customerId) return;
  var msgBox = document.getElementById("messages");
  if (!msgBox) return;
  function latestTs() {
    var rows = msgBox.querySelectorAll(".chat-row[data-ts]");
    var max = 0;
    rows.forEach(function (r) {
      var t = parseInt(r.getAttribute("data-ts"), 10);
      if (!isNaN(t) && t > max) max = t;
    });
    return max;
  }
  function poll() {
    var after = latestTs();
    var url = "/workspace/customer/" + customerId + "/chat/poll?after_ts=" + after + "&chat_id=" + encodeURIComponent(chatId);
    var done = false;
    function onSwap() {
      if (done) return;
      done = true;
      document.removeEventListener("htmx:afterSwap", onSwap);
      // 新消息入场动画 + 滚动到底部
      var rows = msgBox.querySelectorAll(".chat-row[data-ts]");
      var last = rows[rows.length - 1];
      if (last) {
        last.classList.add("new-msg");
        msgBox.scrollTop = msgBox.scrollHeight;
      }
    }
    document.addEventListener("htmx:afterSwap", onSwap);
    htmx.ajax("GET", url, { target: "#messages", swap: "beforeend" });
  }
  _wsPollTimer = setInterval(poll, POLL_MS);
}
// workspace-live-refresh: #ws-center 每次被 htmx 替换后重新初始化轮询 (事件委托)
document.addEventListener("htmx:afterSwap", function (e) {
  if (e.target && e.target.id === "ws-center") {
    initWorkspacePoll();
  }
});
// workspace-reply-panel: 点击消息"回复"按钮 → 填充底部回复面板并显示 (事件委托)
document.addEventListener("click", function (e) {
  var btn = e.target.closest ? e.target.closest(".ws-reply-btn") : null;
  if (!btn) return;
  var msgId = btn.getAttribute("data-msg-id");
  var row = msgId ? document.querySelector('.chat-row[data-msg-id="' + msgId + '"]') : null;
  var textEl = row ? row.querySelector(".chat-text") : null;
  var text = textEl ? textEl.textContent : "";
  var panel = document.getElementById("ws-reply-panel");
  if (!panel) return;
  var msgInput = document.getElementById("ws-reply-message");
  var targetText = document.getElementById("ws-reply-target-text");
  if (msgInput) msgInput.value = text;
  if (targetText) targetText.textContent = (text || "(无正文)").slice(0, 60) + (text.length > 60 ? "…" : "");
  panel.hidden = false;
  // 滚动到底部让回复面板可见
  var msgBox = document.getElementById("messages");
  if (msgBox) msgBox.scrollTop = msgBox.scrollHeight;
});
// reply-workflow-optimization: 一键复制 (事件委托, 兼容 htmx 动态插入的 DOM)
document.addEventListener("click", function (e) {
  var btn = e.target.closest ? e.target.closest("[data-copy]") : null;
  if (!btn) return;
  var target = document.getElementById(btn.getAttribute("data-copy"));
  if (!target) return;
  var text = (target.value !== undefined) ? target.value : target.textContent;
  function copied() {
    var old = btn.textContent;
    btn.textContent = "已复制";
    setTimeout(function () { btn.textContent = old; }, 1200);
  }
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(copied);
  } else {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    copied();
  }
});
// batch2-search-cleanup-monitor: 采集器异常横幅 (D3, 自适应 15s/5s 轮询)
// collector-settings-center: 合并为统一轮询 (横幅 + 状态区 + 扫描进度, 消除双轮询)
(function () {
  var NORMAL_MS = 15000;
  var FAST_MS = 5000;
  function renderCollectorStatus(d) {
    var box = document.getElementById("collector-status");
    if (!box) return;
    var st = d.status || {};
    box.innerHTML = "<p>连接: <strong>" + (d.alive ? "在线" : "离线") +
      "</strong> · 状态: " + (st.state || "未知") +
      (st.last_sync ? " · 最近同步: " + new Date(st.last_sync * 1000).toLocaleString() : "") + "</p>";
    var progress = document.getElementById("scan-progress");
    var scan = d.scan || null;
    if (!scan) {
      if (progress && progress.dataset.doneShown === "1") {
        // 扫描完成已被新的 tick 覆盖 (scan 字段瞬态), 保留完成提示直至下次扫描
        progress.hidden = false;
      }
      return;
    }
    var text = document.getElementById("scan-progress-text");
    var bar = document.getElementById("scan-progress-bar");
    if (scan.running) {
      progress.hidden = false;
      delete progress.dataset.doneShown;
      var pct = (scan.total > 0) ? Math.round((scan.current / scan.total) * 100) : 0;
      if (text) text.textContent = "扫描中: 已扫 " + scan.current + "/" + scan.total + " 会话 · 新入库 " + scan.ingested + " 条";
      if (bar) bar.style.width = pct + "%";
    } else if (scan.done) {
      progress.hidden = false;
      progress.dataset.doneShown = "1";
      if (text) text.textContent = "扫描完成: 新入库 " + scan.ingested + " 条" +
        (scan.finished_at ? " · 完成于 " + new Date(scan.finished_at * 1000).toLocaleString() : "");
      if (bar) bar.style.width = "100%";
      var hint = document.getElementById("scan-hint");
      if (hint) hint.textContent = "";
    }
  }
  function check() {
    fetch("/api/collector/status")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var banner = document.getElementById("collector-banner"); // 惰性查询 (head 内脚本启动时 body 未解析)
        var down = !d.alive;
        if (banner) banner.hidden = !down;
        renderCollectorStatus(d);
        timer = setTimeout(check, down ? FAST_MS : NORMAL_MS);
      })
      .catch(function () {
        var banner = document.getElementById("collector-banner");
        if (banner) banner.hidden = false;
        timer = setTimeout(check, FAST_MS);
      });
  }
  function start() {
    if (document.getElementById("collector-banner") || document.getElementById("collector-status")) {
      var timer = setTimeout(check, 0);
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
// collector-settings-center: 设置读写 + 手动扫描触发 (模态确认)
(function () {
  function fmtValue(v) { return typeof v === "boolean" ? (v ? "true" : "false") : String(v); }

  function initSettings() {
    var form = document.getElementById("settings-form");
    if (!form) return;
    var inputs = form.querySelectorAll("[data-key]");
    var errBox = document.getElementById("settings-error");
    var saved = document.getElementById("settings-saved");
    function showErr(msg) {
      if (!errBox) return;
      errBox.textContent = msg;
      errBox.hidden = !msg;
    }
    function load() {
      fetch("/api/settings").then(function (r) { return r.json(); }).then(function (d) {
        inputs.forEach(function (el) {
          var key = el.getAttribute("data-key");
          var v = d.values[key];
          if (el.getAttribute("data-type") === "checkbox") {
            el.checked = v === true || v === "true";
          } else {
            el.value = v;
          }
          var def = el.parentNode.querySelector(".rt-default");
          if (!def) {
            def = document.createElement("span");
            def.className = "rt-default muted";
            def.style.cssText = "margin-left:6px;font-size:12px";
            el.parentNode.appendChild(def);
          }
          def.textContent = "默认 " + fmtValue(d.defaults[key]);
        });
      });
    }
    function resetKey(key) {
      fetch("/api/settings/reset", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: key }) })
        .then(function (r) { return r.json(); })
        .then(function () { load(); showErr(""); });
    }
    inputs.forEach(function (el) {
      var key = el.getAttribute("data-key");
      var p = el.closest("p");
      if (p) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-sm btn-ghost";
        btn.textContent = "恢复默认";
        btn.style.cssText = "margin-left:8px";
        btn.addEventListener("click", function () { resetKey(key); });
        p.appendChild(btn);
      }
    });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var values = {};
      inputs.forEach(function (el) {
        var key = el.getAttribute("data-key");
        values[key] = el.getAttribute("data-type") === "checkbox" ? el.checked : el.value;
      });
      fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values: values }) })
        .then(function (r) {
          return r.json().then(function (j) { return { ok: r.ok, j: j }; });
        })
        .then(function (res) {
          if (!res.ok) {
            showErr(res.j.error || "保存失败");
            if (saved) saved.hidden = true;
            return;
          }
          showErr("");
          if (saved) { saved.hidden = false; setTimeout(function () { saved.hidden = true; }, 2000); }
          load();
        })
        .catch(function () { showErr("网络错误"); });
    });
    load();
  }

  function initScanControl() {
    var btn = document.getElementById("scan-btn");
    if (!btn) return;
    var modal = document.getElementById("scan-modal");
    var hint = document.getElementById("scan-hint");
    function openModal() {
      if (!modal) return;
      modal.hidden = false;
      var cancel = document.getElementById("scan-modal-cancel");
      var ok = document.getElementById("scan-modal-confirm");
      function close() { modal.hidden = true; }
      cancel.onclick = close;
      ok.onclick = function () {
        close();
        btn.disabled = true;
        fetch("/api/collector/scan", { method: "POST" })
          .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
          .then(function (res) {
            if (!res.ok) { hint.textContent = res.j.error || "请求被拒绝"; }
            else {
              hint.textContent = "扫描已排队，进度即将显示";
              var prog = document.getElementById("scan-progress");
              if (prog) prog.hidden = false;
            }
          })
          .catch(function () { hint.textContent = "网络错误，请重试"; })
          .finally(function () { btn.disabled = false; });
      };
    }
    btn.addEventListener("click", openModal);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initSettings();
    initScanControl();
  });
})();
