function avatarColor(name) {
  var hash = 0;
  var s = String(name || "?");
  for (var i = 0; i < s.length; i++) { hash = (hash * 31 + s.charCodeAt(i)) | 0; }
  var palette = ["#2563eb","#0ea5e9","#10b981","#f59e0b","#ef4444","#8b5cf6","#ec4899","#14b8a6"];
  return palette[Math.abs(hash) % palette.length];
}
function placeholderAvatar(name) {
  var el = document.createElement("span");
  el.className = "avatar fallback";
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
    if (!h.querySelector("img")) { h.appendChild(placeholderAvatar(h.getAttribute("data-name"))); }
  });
});
