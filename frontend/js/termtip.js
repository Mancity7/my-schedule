/* 术语气泡：三段式（是什么 / 举个例子 / 为什么重要）。
   静态 31 条 + /api/indicators 动态条目合并。
   用法：<span data-term="最大回撤"> 自动挂 tooltip。
   键盘可达：focus 显示，Esc 关闭。不依赖 hover-only。 */
var termtip = (function () {
  var tip = null;
  var dynamicTerms = {};
  var loaded = false;

  /* 静态词典（UI_DESIGN §10.1 的 31 条） */
  var STATIC = {
    "最大回撤": {
      what: "从最高点到最低点，账户最多亏了多少比例。",
      example: "账户从 10 万涨到 15 万，又跌到 12 万，最大回撤 = (15-12)/15 = 20%。",
      why: "衡量最坏情况下的亏损幅度，比总收益率更能反映风险。",
    },
    "年化收益率": {
      what: "把总收益折算成每年赚多少，方便和不同期限的策略比较。",
      example: "3 年赚了 60%，年化 ≈ 17%（不是简单除以 3，是复利计算）。",
      why: "不同策略运行时间不同，年化是统一标尺。",
    },
    "夏普比率": {
      what: "每承受 1 单位风险，换来多少超额收益。越高越好。",
      example: "夏普 1.5 意味着多承担 1% 的波动，能多赚 1.5%。",
      why: "光看收益不够，同样的收益波动越小越舒服。",
    },
    "胜率": {
      what: "所有交易中，赚钱的交易占百分之几。",
      example: "做了 10 笔交易，6 笔赚 4 笔亏，胜率 = 60%。",
      why: "胜率高不代表一定赚钱——还要看每笔赚多少亏多少。",
    },
    "盈亏比": {
      what: "平均每笔赚的钱 / 平均每笔亏的钱。",
      example: "平均赚 2000 元、平均亏 1000 元，盈亏比 = 2。",
      why: "胜率 40% + 盈亏比 3，可能比胜率 70% + 盈亏比 1 更赚。",
    },
    "波动率": {
      what: "收益上下波动的剧烈程度，用标准差衡量。",
      example: "波动率 20% 意味着一年收益大概率在 ±20% 范围内。",
      why: "波动越大，心理压力和实际风险都越大。",
    },
    "总收益率": {
      what: "从开始到结束，账户总共赚了（或亏了）百分之几。",
      example: "10 万变成 12 万，总收益率 = 20%。",
      why: "最直观的收益指标，但不考虑时间和风险。",
    },
    "总手续费": {
      what: "整个回测期间，佣金 + 印花税 + 过户费加起来花了多少。",
      example: "做了 50 笔交易，总手续费 800 元。",
      why: "手续费会吃掉利润，频繁交易尤其明显。",
    },
    "RSI": {
      what: "相对强弱指标，衡量近期涨跌的速度和幅度。0-100。",
      example: "RSI > 70 通常认为「超买」，RSI < 30 通常认为「超卖」。",
      why: "帮助判断短期是否涨多了或跌多了，但不是万能的。",
    },
    "MACD": {
      what: "趋势跟踪指标，由 DIF 线、DEA 线和柱状图组成。",
      example: "DIF 上穿 DEA（金叉）常被视为买入信号。",
      why: "适合捕捉中期趋势，震荡市里容易反复打脸。",
    },
    "KDJ": {
      what: "随机指标，K/D/J 三条线，灵敏度高。",
      example: "J 值 > 100 超买，J 值 < 0 超卖。",
      why: "比 RSI 更灵敏，但假信号也更多，适合配合其他指标。",
    },
    "均线": {
      what: "过去 N 天收盘价的平均值连成的线，比如 MA20 = 20 日均线。",
      example: "股价站上 MA20，说明近 20 天买入的人平均不亏。",
      why: "支撑/阻力判断的基础工具，趋势一目了然。",
    },
    "布林带": {
      what: "中轨（均线）+ 上下轨（均线 ± 2 倍标准差），价格通常在带内运行。",
      example: "股价碰到上轨，可能短期偏贵；碰到下轨，可能偏便宜。",
      why: "衡量价格偏离正常范围的程度。",
    },
    "成交量": {
      what: "当天成交了多少股。",
      example: "平时成交 1000 万股，突然放大到 5000 万，说明有大动作。",
      why: "量价配合是技术分析的基础，放量突破比缩量突破更可信。",
    },
    "换手率": {
      what: "当天成交量占流通股数的百分比。",
      example: "换手率 10% 意味着十分之一的流通股换了主人。",
      why: "反映股票的活跃度和资金关注度。",
    },
    "涨停": {
      what: "A 股主板每天最多涨 10%（创业板/科创板 20%），到顶就叫涨停。",
      example: "昨天收 10 元，今天最多涨到 11 元（10 × 1.10 = 11.00）。",
      why: "涨停封死意味着买盘极强，但也买不进去。",
    },
    "跌停": {
      what: "每天最多跌 10%（创业板/科创板 20%），到底就叫跌停。",
      example: "昨天收 10 元，今天最多跌到 9 元（10 × 0.90 = 9.00）。",
      why: "跌停封死意味着卖盘极强，但想卖也卖不出去。",
    },
    "T+1": {
      what: "A 股规则：今天买的股票，明天才能卖。",
      example: "周一买入，最早周二才能卖出。",
      why: "意味着当日买入后遇到暴跌只能干看着，回测必须遵守这个规则。",
    },
    "滑点": {
      what: "实际成交价和预期价格之间的差距。",
      example: "信号说 10 元买，实际成交 10.01 元，滑点 = 0.1%。",
      why: "模拟真实交易中的摩擦，让回测更贴近现实。",
    },
    "佣金": {
      what: "券商收的交易手续费，双向收取（买卖都收）。",
      example: "万 2.5 = 成交 10 万元收 25 元，最低 5 元。",
      why: "频繁交易佣金会累积，设置里可以调整。",
    },
    "印花税": {
      what: "国家收的税，只在卖出时收取。",
      example: "千 0.5 = 卖出 10 万元收 50 元。",
      why: "A 股特有，买入不收。",
    },
    "过户费": {
      what: "交易所收的过户手续费，双向收取。",
      example: "万 0.1 = 成交 10 万元收 1 元。",
      why: "金额很小但不能忽略，尤其是高频策略。",
    },
    "回测": {
      what: "用历史数据模拟策略运行，看如果当时按这个规则交易会怎样。",
      example: "把「MA5 上穿 MA20 就买」的规则放到 2020-2024 年跑一遍。",
      why: "快速验证想法，但历史不代表未来。",
    },
    "基准收益": {
      what: "同期沪深 300 指数的涨跌，用来做参照。",
      example: "策略赚 30%，基准赚 35%，说明还不如直接买指数。",
      why: "策略好不好，要和「躺平买指数」比。",
    },
    "上证指数": {
      what: "上海证券交易所综合股价指数，反映沪市整体走势。",
      example: "你的策略赚 20%，上证指数同期涨 25%，说明大盘涨得比你策略多。",
      why: "大盘是最基础的参照，跑不赢大盘的策略意义有限。",
    },
    "买入持有": {
      what: "回测第一天买入、最后一天卖出，中间不做任何操作的收益率。",
      example: "买入持有赚 15%，你的策略赚 25%，说明策略的择时带来了额外 10% 的超额收益。",
      why: "最简单的基准——如果策略跑不赢「买了不动」，那策略可能不如不折腾。",
    },
    "预检": {
      what: "正式运行前的检查清单，确认数据和配置没有明显问题。",
      example: "检查数据是否覆盖完整、策略条件是否合理等。",
      why: "避免跑完才发现数据有缺口或参数填错了。",
    },
    "数据缺口": {
      what: "历史数据中缺失的交易日。",
      example: "2023 年 3 月有 5 天数据缺失，可能是数据源的问题。",
      why: "缺口太大会影响指标计算和回测准确性。",
    },
    "信号溯源": {
      what: "回头看某一天，策略为什么买 / 为什么没买 / 为什么卖。",
      example: "点某一笔交易，能看到当天每个条件是否满足。",
      why: "理解策略行为，判断是合理信号还是巧合。",
    },
    "网格寻优": {
      what: "自动尝试不同参数组合，找「最优」参数。",
      example: "MA 天数从 5 试到 60，找收益最高的那个。",
      why: "本系统不支持——容易过拟合历史数据，实盘反而更差。",
    },
    "情绪分析": {
      what: "用新闻 / 社交媒体文本判断市场情绪。",
      example: "分析财经新闻标题，给出「乐观 / 悲观」评分。",
      why: "计划中 V3 版本的功能，当前版本暂不支持。",
    },
    "实时模拟": {
      what: "用实时行情数据运行策略，不下真单。",
      example: "每天收盘后自动检查策略信号，模拟买卖。",
      why: "计划中 V2 版本的功能，当前版本暂不支持。",
    },
    "数据源": {
      what: "提供股票历史行情的数据接口。",
      example: "本系统支持 akshare 和 baostock 两个免费数据源。",
      why: "不同数据源覆盖范围和速度不同，可在设置中切换。",
    },
  };

  function _getTerm(name) {
    return dynamicTerms[name] || STATIC[name] || null;
  }

  function _show(el, entry) {
    _hide();
    tip = document.createElement("div");
    tip.className = "termtip";
    tip.setAttribute("role", "tooltip");

    var sections = [
      { label: "是什么", text: entry.what },
      { label: "举个例子", text: entry.example },
      { label: "为什么重要", text: entry.why },
    ];
    sections.forEach(function (s) {
      if (!s.text) return;
      var sec = document.createElement("div");
      sec.className = "termtip-section";
      var lbl = document.createElement("div");
      lbl.className = "termtip-label";
      lbl.textContent = s.label;
      var txt = document.createElement("div");
      txt.className = "termtip-text";
      txt.textContent = s.text;
      sec.appendChild(lbl);
      sec.appendChild(txt);
      tip.appendChild(sec);
    });

    document.body.appendChild(tip);
    var rect = el.getBoundingClientRect();
    var tipRect = tip.getBoundingClientRect();
    var top = rect.bottom + window.scrollY + 6;
    var left = rect.left + window.scrollX + rect.width / 2 - tipRect.width / 2;
    if (left < 8) left = 8;
    if (left + tipRect.width > document.documentElement.clientWidth - 8) {
      left = document.documentElement.clientWidth - tipRect.width - 8;
    }
    tip.style.top = top + "px";
    tip.style.left = left + "px";
    tip.classList.add("termtip-show");
  }

  function _hide() {
    if (tip) { tip.remove(); tip = null; }
  }

  function _attach(root) {
    root = root || document;
    var els = root.querySelectorAll("[data-term]");
    els.forEach(function (el) {
      if (el._termtip_bound) return;
      el._termtip_bound = true;
      el.setAttribute("tabindex", "0");
      el.classList.add("term");

      var name = el.getAttribute("data-term");
      var helpIcon = document.createElement("i");
      helpIcon.className = "term-help";
      helpIcon.textContent = "?";
      helpIcon.setAttribute("tabindex", "0");
      el.appendChild(helpIcon);

      function showForThis() {
        var entry = _getTerm(name);
        if (entry) _show(el, entry);
      }

      el.addEventListener("mouseenter", showForThis);
      el.addEventListener("mouseleave", _hide);
      el.addEventListener("focus", showForThis);
      el.addEventListener("blur", _hide);
      el.addEventListener("keydown", function (e) {
        if (e.key === "Escape") { _hide(); el.blur(); }
      });
      helpIcon.addEventListener("mouseenter", showForThis);
      helpIcon.addEventListener("mouseleave", _hide);
      helpIcon.addEventListener("focus", showForThis);
      helpIcon.addEventListener("blur", _hide);
    });
  }

  function loadDynamic() {
    if (loaded) return Promise.resolve();
    loaded = true;
    return api.get("/api/indicators").then(function (data) {
      (data.items || []).forEach(function (item) {
        if (item.term) {
          dynamicTerms[item.cn] = item.term;
        }
      });
    }).catch(function () { /* 离线时只用静态词典 */ });
  }

  return {
    attach: _attach,
    loadDynamic: loadDynamic,
    get: _getTerm,
  };
})();
