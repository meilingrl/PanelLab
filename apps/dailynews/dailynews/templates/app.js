/* 每日简报：主题切换与类别筛选。没有 JS 时页面照常显示全部条目。 */
(function () {
  "use strict";
  var KEY = "dailynews-theme";
  var root = document.documentElement;

  function stored() {
    try { var t = localStorage.getItem(KEY); return (t === "light" || t === "dark") ? t : null; }
    catch (e) { return null; }
  }

  var toggle = document.querySelector(".theme-toggle");
  if (toggle) {
    toggle.hidden = false;
    toggle.addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem(KEY, next); } catch (e) { /* 隐私模式等：只在本页生效 */ }
    });
  }

  // 没有手动选择时跟随系统深浅色的变化
  if (window.matchMedia) {
    var mq = window.matchMedia("(prefers-color-scheme: dark)");
    var onChange = function (e) { if (!stored()) { root.setAttribute("data-theme", e.matches ? "dark" : root.getAttribute("data-default") || "light"); } };
    if (mq.addEventListener) { mq.addEventListener("change", onChange); }
    else if (mq.addListener) { mq.addListener(onChange); }
  }

  var nav = document.querySelector(".filters");
  if (!nav) { return; }
  nav.hidden = false;
  var chips = nav.querySelectorAll(".chip");
  var items = document.querySelectorAll(".item");
  var sections = document.querySelectorAll(".section");
  var none = document.querySelector(".filter-empty");

  function apply(cat) {
    var i, shown = 0;
    for (i = 0; i < chips.length; i++) {
      chips[i].setAttribute("aria-pressed", chips[i].getAttribute("data-filter") === cat ? "true" : "false");
    }
    for (i = 0; i < items.length; i++) {
      items[i].hidden = !(cat === "all" || items[i].getAttribute("data-cat") === cat);
    }
    for (i = 0; i < sections.length; i++) {
      var visible = sections[i].querySelectorAll(".item:not([hidden])").length;
      sections[i].hidden = visible === 0;
      var c = sections[i].querySelector(".count");
      if (c) { c.textContent = String(visible); }
      shown += visible;
    }
    if (none) { none.hidden = shown !== 0; }
  }

  for (var i = 0; i < chips.length; i++) {
    chips[i].addEventListener("click", function () {
      apply(this.getAttribute("data-filter"));
      var main = document.querySelector("main");
      var top = main.getBoundingClientRect().top + window.pageYOffset - nav.offsetHeight - 8;
      if (window.pageYOffset > top) { window.scrollTo(0, top); }
    });
  }
})();
