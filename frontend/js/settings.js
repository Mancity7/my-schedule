/* 设置页：按组显示、即时保存、恢复默认、数据源测试、数据管理。 */
(function () {
  var LABELS = {
    commission_rate: "佣金费率",
    commission_min: "最低佣金",
    stamp_duty_rate: "印花税率",
    transfer_fee_rate: "过户费率",
    slippage: "滑点",
    initial_cash: "初始资金",
    risk_free_rate: "无风险利率",
    annual_days: "年化天数",
    benchmark: "基准指数",
    datasource_order: "数据源优先级",
    gap_tolerance_pct: "缺口容忍(%)",
    cache_ttl_days: "缓存有效期(天)",
    deepseek_api_key: "DeepSeek API Key",
    deepseek_base_url: "DeepSeek 接口地址",
    deepseek_model: "DeepSeek 模型",
  };
  var GROUP_LABELS = {
    trade: "交易费用",
    metric: "绩效参数",
    data: "数据设置",
    ai: "AI 设置（V2）",
  };
  var UNITS = {
    commission_rate: "（如 0.00025 = 万2.5）",
    stamp_duty_rate: "（如 0.0005 = 千0.5）",
    transfer_fee_rate: "（如 0.00001 = 万0.1）",
    slippage: "（如 0.001 = 0.1%）",
    initial_cash: "元",
    risk_free_rate: "（如 0.02 = 2%）",
    annual_days: "天",
    benchmark: "（6位代码，如 000300）",
    gap_tolerance_pct: "%",
    cache_ttl_days: "天",
  };

  var settingsData = null;

  function init() {
    loadSettings();
    loadDatasourceStatus();
    bindClearCache();
    bindExportAll();
    bindTestAll();
  }

  function loadSettings() {
    api.get("/api/settings").then(function (data) {
      settingsData = data;
      renderSettings();
    }).catch(function () {
      document.getElementById("settings-container").innerHTML = '<div class="muted">加载失败</div>';
    });
  }

  function renderSettings() {
    var el = document.getElementById("settings-container");
    var values = settingsData.values;
    var groups = settingsData.groups;
    var ranges = settingsData.ranges;
    var defaults = settingsData.defaults;

    el.innerHTML = "";
    Object.keys(groups).forEach(function (gk) {
      var keys = groups[gk];
      var section = document.createElement("div");
      section.className = "card settings-section";
      if (el.firstChild) section.style.marginTop = "var(--gap)";

      var header = document.createElement("div");
      header.style.display = "flex";
      header.style.alignItems = "center";
      header.style.gap = "12px";
      header.style.marginBottom = "12px";
      header.innerHTML = '<h2 style="margin:0">' + (GROUP_LABELS[gk] || gk) + '</h2>' +
        '<span class="spacer"></span>' +
        '<button class="btn btn-sm btn-ghost reset-group" data-group="' + gk + '" type="button">恢复本组默认</button>';
      section.appendChild(header);

      keys.forEach(function (key) {
        var val = values[key];
        var def = defaults[key];
        var range = ranges[key];
        var isSecret = settingsData.secret_set && settingsData.secret_set[key];
        var unit = UNITS[key] || "";

        var row = document.createElement("div");
        row.style.display = "flex";
        row.style.alignItems = "center";
        row.style.gap = "12px";
        row.style.marginBottom = "8px";
        row.setAttribute("data-field", key);

        var label = document.createElement("label");
        label.style.minWidth = "140px";
        label.style.fontSize = "14px";
        label.textContent = LABELS[key] || key;
        row.appendChild(label);

        var input = document.createElement("input");
        input.className = "input";
        input.style.flex = "1";
        input.style.maxWidth = "300px";
        input.setAttribute("data-key", key);
        input.value = val != null ? val : "";
        if (isSecret) input.type = "password";
        if (range) {
          input.placeholder = def + " " + unit + "（范围 " + range[0] + " ~ " + range[1] + "）";
        } else if (unit) {
          input.placeholder = unit;
        }

        input.addEventListener("change", function () {
          saveField(key, this.value, this);
        });
        row.appendChild(input);

        if (val !== def && !(isSecret && val === "***")) {
          var hint = document.createElement("span");
          hint.className = "muted";
          hint.style.fontSize = "12px";
          hint.textContent = "默认：" + def;
          row.appendChild(hint);
        }

        section.appendChild(row);
      });

      el.appendChild(section);
    });

    el.querySelectorAll(".reset-group").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var gk = this.getAttribute("data-group");
        dialog.confirm({
          title: "恢复默认",
          body: "确认将「" + (GROUP_LABELS[gk] || gk) + "」恢复为默认值？",
          okText: "恢复",
        }).then(function (ok) {
          if (!ok) return;
          api.post("/api/settings/reset", { group: gk }).then(function () {
            toast.ok("已恢复默认");
            loadSettings();
          }).catch(function () {});
        });
      });
    });
  }

  function saveField(key, value, inputEl) {
    var body = {};
    body[key] = value;
    api.post("/api/settings", body).then(function (data) {
      toast.ok("已保存");
      if (data.values) {
        settingsData.values = data.values;
        if (data.secret_set) settingsData.secret_set = data.secret_set;
      }
      if (inputEl) inputEl.classList.remove("field-err");
    }).catch(function (err) {
      if (inputEl) inputEl.classList.add("field-err");
    });
  }

  function loadDatasourceStatus() {
    api.get("/api/datasource/status").then(function (data) {
      renderDatasourceResults(data);
    }).catch(function () {});
  }

  function renderDatasourceResults(data) {
    var el = document.getElementById("datasource-results");
    if (!data || !data.results || !data.results.length) {
      el.innerHTML = '<div class="muted">尚未测试过数据源。</div>';
      return;
    }
    var html = '<table class="tbl"><thead><tr><th>数据源</th><th>状态</th><th>延迟</th><th>测试时间</th></tr></thead><tbody>';
    data.results.forEach(function (r) {
      var status = r.ok ? '<span class="badge badge-ok">可用</span>' : '<span class="badge badge-err">不可用</span>';
      var latency = r.latency_ms != null ? r.latency_ms + " ms" : "--";
      var time = r.checked_at ? r.checked_at.slice(0, 16).replace("T", " ") : "--";
      html += '<tr><td>' + (r.label || r.name) + '</td><td>' + status + '</td><td>' + latency + '</td><td>' + time + '</td></tr>';
    });
    html += '</tbody></table>';
    if (data.checked_at) {
      html += '<div class="muted" style="font-size:12px;margin-top:4px">上次测试：' + data.checked_at.slice(0, 16).replace("T", " ") + '</div>';
    }
    el.innerHTML = html;
  }

  function bindTestAll() {
    var btn = document.getElementById("test-all");
    if (!btn) return;
    btn.addEventListener("click", function () {
      btn.disabled = true;
      btn.textContent = "测试中...";
      api.post("/api/datasource/test", { name: "" }).then(function (data) {
        renderDatasourceResults(data);
        toast.ok("测试完成");
        btn.disabled = false;
        btn.textContent = "测试全部数据源";
      }).catch(function () {
        btn.disabled = false;
        btn.textContent = "测试全部数据源";
      });
    });
  }

  function bindClearCache() {
    var btn = document.getElementById("clear-cache");
    if (!btn) return;
    btn.addEventListener("click", function () {
      dialog.confirm({
        title: "清空行情缓存",
        body: "确认清空所有行情缓存？策略和回测记录不受影响，但下次回测需要重新下载行情数据。",
        okText: "清空",
        danger: true,
      }).then(function (ok) {
        if (!ok) return;
        api.post("/api/cache/clear").then(function () {
          toast.ok("已清空缓存");
        }).catch(function () {});
      });
    });
  }

  function bindExportAll() {
    var btn = document.getElementById("export-all");
    if (!btn) return;
    btn.addEventListener("click", function () {
      api.get("/api/settings").then(function (data) {
        var exportData = { settings: {}, exported_at: new Date().toISOString() };
        var values = data.values;
        Object.keys(values).forEach(function (k) {
          if (data.secret_set && data.secret_set[k]) return;
          exportData.settings[k] = values[k];
        });
        var blob = new Blob([JSON.stringify(exportData, null, 2)], { type: "application/json" });
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = "settings_export_" + new Date().toISOString().slice(0, 10) + ".json";
        a.click();
        URL.revokeObjectURL(url);
        toast.ok("已导出（API Key 已排除）");
      }).catch(function () {});
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
