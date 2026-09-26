/* 统一 HTTP 客户端。所有页面 fetch 都走这里。 */
var api = (function () {
  var TIMEOUT = 30000;

  function request(method, path, body, timeoutMs) {
    var ctrl = new AbortController();
    var ms = timeoutMs || TIMEOUT;
    var timer = setTimeout(function () { ctrl.abort(); }, ms);

    var opts = {
      method: method,
      headers: { "Content-Type": "application/json" },
      signal: ctrl.signal,
    };
    if (body !== undefined && body !== null) {
      opts.body = JSON.stringify(body);
    }

    return fetch(path, opts).then(function (resp) {
      clearTimeout(timer);
      var isJson = (resp.headers.get("content-type") || "").indexOf("json") >= 0;

      if (resp.status === 401) {
        toast.err("请求被拒绝（401），请检查配置");
        return Promise.reject({ kind: "auth", message: "401" });
      }
      if (resp.status === 404 && path.indexOf("/api/") !== 0) {
        toast.err("页面不存在");
        return Promise.reject({ kind: "notfound", message: "404" });
      }

      return (isJson ? resp.json() : resp.text().then(function (t) { return { error: { message: t } }; }))
        .then(function (data) {
          if (!resp.ok) {
            var err = data.error || { kind: "program", message: "HTTP " + resp.status };
            api._handleError(err, resp.status);
            return Promise.reject(err);
          }
          return data;
        });
    }).catch(function (e) {
      clearTimeout(timer);
      if (e && e.name === "AbortError") {
        var timeoutErr = { kind: "datasource", code: "TIMEOUT", message: "请求超时（" + Math.round(ms / 1000) + "秒），可能被限流", retriable: true };
        toast.err(timeoutErr.message);
        return Promise.reject(timeoutErr);
      }
      if (e && e.kind) throw e;
      var netErr = { kind: "datasource", code: "NETWORK", message: "网络不通，请检查连接", retriable: true };
      toast.err(netErr.message);
      return Promise.reject(netErr);
    });
  }

  function _handleError(err, status) {
    var kind = err.kind || "program";
    var msg = err.message || "未知错误";

    if (kind === "input" && err.field) {
      var el = document.querySelector("[data-field=\"" + err.field + "\"]");
      if (el) {
        el.classList.add("field-err");
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.focus && el.focus();
      }
      toast.err(msg);
    } else if (kind === "datasource") {
      toast.warn(msg, err.retriable ? { action: "重试", onAction: function () { location.reload(); } } : undefined);
    } else if (kind === "integrity") {
      toast.err(msg + "（数据不完整，系统拒绝继续）");
    } else {
      toast.err(msg);
    }
  }

  return {
    get: function (path) { return request("GET", path); },
    post: function (path, body, timeoutMs) { return request("POST", path, body, timeoutMs); },
    put: function (path, body) { return request("PUT", path, body); },
    del: function (path) { return request("DELETE", path); },
    _handleError: _handleError,
  };
})();
