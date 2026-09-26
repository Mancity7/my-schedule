/* 轻量提示条。ok / warn / err，支持「撤销」按钮带 5 秒倒计时。不打断操作。 */
var toast = (function () {
  var container = null;

  function _ensure() {
    if (container) return container;
    container = document.createElement("div");
    container.className = "toast-container";
    container.setAttribute("role", "status");
    container.setAttribute("aria-live", "polite");
    document.body.appendChild(container);
    return container;
  }

  function show(msg, type, opts) {
    opts = opts || {};
    var el = document.createElement("div");
    el.className = "toast toast-" + type;
    el.setAttribute("role", type === "err" ? "alert" : "status");

    var text = document.createElement("span");
    text.className = "toast-msg";
    text.textContent = msg;
    el.appendChild(text);

    if (opts.action) {
      var btn = document.createElement("button");
      btn.className = "toast-action";
      btn.type = "button";
      var remaining = 5;
      btn.textContent = opts.action + " (" + remaining + ")";
      btn.addEventListener("click", function () {
        clearTimeout(timer);
        el.remove();
        if (opts.onAction) opts.onAction();
      });
      el.appendChild(btn);

      var timer = setInterval(function () {
        remaining--;
        if (remaining <= 0) {
          clearInterval(timer);
          btn.remove();
        } else {
          btn.textContent = opts.action + " (" + remaining + ")";
        }
      }, 1000);

      setTimeout(function () { clearInterval(timer); }, 5500);
    }

    var close = document.createElement("button");
    close.className = "toast-close";
    close.type = "button";
    close.setAttribute("aria-label", "关闭");
    close.innerHTML = "&times;";
    close.addEventListener("click", function () { el.remove(); });
    el.appendChild(close);

    _ensure().appendChild(el);
    requestAnimationFrame(function () { el.classList.add("toast-show"); });

    setTimeout(function () {
      el.classList.remove("toast-show");
      el.addEventListener("transitionend", function () { el.remove(); }, { once: true });
      setTimeout(function () { el.remove(); }, 400);
    }, opts.action ? 5500 : 3500);

    return el;
  }

  return {
    ok: function (msg, opts) { return show(msg, "ok", opts); },
    warn: function (msg, opts) { return show(msg, "warn", opts); },
    err: function (msg, opts) { return show(msg, "err", opts); },
  };
})();
