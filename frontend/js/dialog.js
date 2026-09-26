/* 确认弹层。取消按钮在左且默认聚焦；Esc = 取消（UI_DESIGN §10.3）。 */
var dialog = (function () {

  function confirm(opts) {
    opts = opts || {};
    var title = opts.title || "确认";
    var body = opts.body || "";
    var lose = opts.lose || "";
    var okText = opts.okText || "确认";
    var danger = !!opts.danger;

    return new Promise(function (resolve) {
      var mask = document.createElement("div");
      mask.className = "dialog-mask";

      var dlg = document.createElement("div");
      dlg.className = "dialog";
      dlg.setAttribute("role", "alertdialog");
      dlg.setAttribute("aria-modal", "true");
      dlg.setAttribute("aria-label", title);

      var h = document.createElement("h3");
      h.className = "dialog-title";
      h.textContent = title;
      dlg.appendChild(h);

      if (body) {
        var p = document.createElement("p");
        p.className = "dialog-body";
        p.textContent = body;
        dlg.appendChild(p);
      }

      if (lose) {
        var w = document.createElement("p");
        w.className = "dialog-warn";
        w.textContent = "⚠ " + lose;
        dlg.appendChild(w);
      }

      var actions = document.createElement("div");
      actions.className = "dialog-actions";

      var cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.className = "btn btn-secondary";
      cancelBtn.textContent = "取消";
      cancelBtn.addEventListener("click", function () { cleanup(); resolve(false); });

      var okBtn = document.createElement("button");
      okBtn.type = "button";
      okBtn.className = danger ? "btn btn-danger" : "btn btn-primary";
      okBtn.textContent = okText;
      okBtn.addEventListener("click", function () { cleanup(); resolve(true); });

      actions.appendChild(cancelBtn);
      actions.appendChild(okBtn);
      dlg.appendChild(actions);

      mask.appendChild(dlg);
      document.body.appendChild(mask);

      cancelBtn.focus();

      function onKey(e) {
        if (e.key === "Escape") { e.preventDefault(); cleanup(); resolve(false); }
        if (e.key === "Tab") {
          var focusable = [cancelBtn, okBtn];
          var first = focusable[0], last = focusable[focusable.length - 1];
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault(); last.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault(); first.focus();
          }
        }
      }
      document.addEventListener("keydown", onKey);

      mask.addEventListener("click", function (e) {
        if (e.target === mask) { cleanup(); resolve(false); }
      });

      function cleanup() {
        document.removeEventListener("keydown", onKey);
        mask.remove();
      }
    });
  }

  function warn(title, body, onOk) {
    var mask = document.createElement("div");
    mask.className = "dialog-mask";

    var dlg = document.createElement("div");
    dlg.className = "dialog";
    dlg.setAttribute("role", "alertdialog");
    dlg.setAttribute("aria-modal", "true");

    var h = document.createElement("h3");
    h.className = "dialog-title";
    h.textContent = title || "提示";
    dlg.appendChild(h);

    if (body) {
      var p = document.createElement("p");
      p.className = "dialog-body";
      p.style.whiteSpace = "pre-line";
      p.textContent = body;
      dlg.appendChild(p);
    }

    var actions = document.createElement("div");
    actions.className = "dialog-actions";

    var okBtn = document.createElement("button");
    okBtn.type = "button";
    okBtn.className = "btn btn-primary";
    okBtn.textContent = "我知道了";
    okBtn.addEventListener("click", function () {
      mask.remove();
      if (typeof onOk === "function") onOk();
    });

    actions.appendChild(okBtn);
    dlg.appendChild(actions);

    mask.appendChild(dlg);
    document.body.appendChild(mask);

    okBtn.focus();

    function onKey(e) {
      if (e.key === "Escape" || e.key === "Enter") {
        e.preventDefault();
        mask.remove();
        document.removeEventListener("keydown", onKey);
        if (typeof onOk === "function") onOk();
      }
    }
    document.addEventListener("keydown", onKey);

    mask.addEventListener("click", function (e) {
      if (e.target === mask) {
        mask.remove();
        document.removeEventListener("keydown", onKey);
        if (typeof onOk === "function") onOk();
      }
    });
  }

  return { confirm: confirm, warn: warn };
})();
