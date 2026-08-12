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
  if (!input) return;
  function apply() {
    var q = (input.value || "").trim().toLowerCase();
    var cc = country ? country.value : "";
    var cp = company ? company.value : "";
    document.querySelectorAll(".customer-card").forEach(function (card) {
      var hay = (card.getAttribute("data-search") || "").toLowerCase();
      var ok = (!q || hay.indexOf(q) >= 0)
        && (!cc || hay.indexOf("country=" + cc.toLowerCase()) >= 0)
        && (!cp || hay.indexOf("company=" + cp.toLowerCase()) >= 0);
      card.style.display = ok ? "" : "none";
    });
  }
  input.addEventListener("input", apply);
  if (country) country.addEventListener("change", apply);
  if (company) company.addEventListener("change", apply);
}
document.addEventListener("DOMContentLoaded", function () {
  initCustomerFilter();
  document.querySelectorAll(".avatar-holder[data-name]").forEach(function (h) {
    if (!h.querySelector("img")) { h.appendChild(placeholderAvatar(h.getAttribute("data-name"), h.getAttribute("data-avatar-class"))); }
  });
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
(function () {
  var banner = document.getElementById("collector-banner");
  if (!banner) return;
  var NORMAL_MS = 15000;
  var FAST_MS = 5000;
  function check() {
    fetch("/api/collector/status")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var down = !d.alive;
        banner.hidden = !down;
        timer = setTimeout(check, down ? FAST_MS : NORMAL_MS);
      })
      .catch(function () {
        banner.hidden = false;
        timer = setTimeout(check, FAST_MS);
      });
  }
  var timer = setTimeout(check, 0);
})();
