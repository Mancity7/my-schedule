-- A股策略验证系统 数据库结构

-- 股票基础信息（含拼音首字母，方便搜索）
CREATE TABLE IF NOT EXISTS stock_basic (
    code        TEXT PRIMARY KEY,
    name        TEXT,
    market      TEXT,
    industry    TEXT,
    pinyin      TEXT,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_stock_name ON stock_basic(name);

-- K线行情。period: daily（周线月线由日线重采样派生，不落库）
-- source 记录每行来自哪个上游：sina / tencent / eastmoney / baostock
-- 各家前复权算法不完全一致，混源会造成口径波动，需要能查出来
CREATE TABLE IF NOT EXISTS kline (
    code        TEXT NOT NULL,
    period      TEXT NOT NULL,
    adjust      TEXT NOT NULL DEFAULT 'qfq',
    ts          TEXT NOT NULL,          -- 日期 YYYY-MM-DD 或分钟 YYYY-MM-DD HH:MM
    open        REAL, high REAL, low REAL, close REAL,
    volume      REAL,                   -- 手
    amount      REAL,                   -- 元
    turnover    REAL,                   -- 换手率 %
    pct_chg     REAL,                   -- 涨跌幅 %
    source      TEXT,                   -- sina / tencent / eastmoney / baostock
    PRIMARY KEY (code, period, adjust, ts)
);

-- 指数日线（沪深300 等基准）
CREATE TABLE IF NOT EXISTS index_kline (
    code        TEXT NOT NULL,
    ts          TEXT NOT NULL,
    open        REAL, high REAL, low REAL, close REAL,
    volume      REAL, amount REAL,
    PRIMARY KEY (code, ts)
);

-- 估值快照。实测免费源是稀疏序列（2~15 天一个点），不足以逐日回测，
-- 所以只存"最近一期"给个股信息卡展示，PE/PB 不进 V1 指标白名单。
CREATE TABLE IF NOT EXISTS valuation (
    code        TEXT NOT NULL,
    ts          TEXT NOT NULL,
    pe          REAL,
    pe_ttm      REAL,
    pb          REAL,
    dv_ratio    REAL,
    total_mv    REAL,                   -- 万元
    PRIMARY KEY (code, ts)
);

-- 策略：DSL 以 JSON 文本存储
CREATE TABLE IF NOT EXISTS strategy (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    note        TEXT,
    period      TEXT NOT NULL DEFAULT 'daily',
    dsl         TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'manual',   -- manual / template / nl
    nl_text     TEXT,                              -- 原始自然语言描述（V2）
    created_at  TEXT,
    updated_at  TEXT
);

-- 用户点[保存为模板]存下来的策略。内置 6 个模板写在 templates.py，不占库。
CREATE TABLE IF NOT EXISTS template (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    note            TEXT,
    period          TEXT NOT NULL DEFAULT 'daily',
    dsl             TEXT NOT NULL,
    from_strategy   INTEGER,              -- 由哪条策略存来的
    created_at      TEXT
);

-- 回测任务。一次提交可能对应多只股票 => 一个 backtest_run 下挂多条 backtest
CREATE TABLE IF NOT EXISTS backtest_run (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id INTEGER NOT NULL,
    name        TEXT,
    period      TEXT,
    start_date  TEXT,
    end_date    TEXT,
    initial_cash REAL,
    codes       TEXT,                    -- JSON 数组
    params      TEXT,                    -- JSON: 回测参数快照
    status      TEXT DEFAULT 'done',     -- running / done / error / cancelled
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS backtest (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL,
    code        TEXT NOT NULL,
    name        TEXT,                    -- 股票名称
    status      TEXT DEFAULT 'done',     -- pending / done / error
    error       TEXT,
    error_code  TEXT,                    -- 错误码（GAP_EXCEEDED / EMPTY_KLINE 等）
    metrics     TEXT,                    -- JSON: 绩效指标
    equity      TEXT,                    -- JSON: [{ts, equity, cash, position}]
    trades      TEXT,                    -- JSON: 逐笔明细
    signals     TEXT,                    -- JSON: 信号日志（含未成交原因）
    fee_snapshot TEXT,                   -- JSON: 费率快照（验收 K3）
    trace       TEXT,                    -- JSON: 溯源数据（大，按需加载）
    quality     TEXT,                    -- JSON: 数据质量（warmup/gaps/insufficient）
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_bt_run ON backtest(run_id);

-- 键值设置
CREATE TABLE IF NOT EXISTS settings (
    k           TEXT PRIMARY KEY,
    v           TEXT
);

-- 运行时状态（上次数据源测试结果、股票列表同步时间等）。
-- 和 settings 分开：这些是程序自己写的，用户不该改，导出时可以整体带上。
CREATE TABLE IF NOT EXISTS app_state (
    k           TEXT PRIMARY KEY,
    v           TEXT,
    updated_at  TEXT
);

-- 数据同步元信息：记录某只股票某周期已缓存到哪一天，用于增量更新
-- attempted_to = 上次请求到的日期。数据源在收盘前/节假日拿不到当天K线，
-- 记下"已经试到哪天"才不会每次回测都重下一遍（验收 C4）。
CREATE TABLE IF NOT EXISTS sync_meta (
    code        TEXT NOT NULL,
    period      TEXT NOT NULL,
    adjust      TEXT NOT NULL DEFAULT 'qfq',
    first_ts    TEXT,
    last_ts     TEXT,
    attempted_to TEXT,
    updated_at  TEXT,
    PRIMARY KEY (code, period, adjust)
);

-- 交易日历。缓存是否"缺最新一天"、数据缺口率都必须按交易日算，
-- 按自然日算会在周末和长假时每次都重新下载。
CREATE TABLE IF NOT EXISTS trade_calendar (
    ts          TEXT PRIMARY KEY,     -- YYYY-MM-DD
    updated_at  TEXT
);

-- ---------------------------------------------------------------- 实时模拟盘
-- 一只股票一个账户、独立 10 万资金、全仓进出 —— 和回测同口径，两边数字才可比。
-- dsl 与 fee_snapshot 都是**创建时的快照**：之后改策略、改费率都不追溯已有账户，
-- 否则账户中途换规则，前面攒下来的盈亏就成了两套逻辑拼出来的，没法解释。
CREATE TABLE IF NOT EXISTS sim_account (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id   INTEGER NOT NULL,
    strategy_name TEXT,
    code          TEXT NOT NULL,
    name          TEXT,
    period        TEXT NOT NULL DEFAULT 'daily',
    initial_cash  REAL NOT NULL,
    dsl           TEXT NOT NULL,           -- JSON 快照
    fee_snapshot  TEXT NOT NULL,           -- JSON 快照
    status        TEXT NOT NULL DEFAULT 'running',  -- running / paused / closed
    start_date    TEXT NOT NULL,           -- 从哪天开始跟踪（含）
    settled_to    TEXT,                    -- 结算游标：已经处理到哪根K线
    state         TEXT,                    -- JSON 撮合状态（现金/持仓/委托/持有天数）
    last_error    TEXT,
    created_at    TEXT,
    updated_at    TEXT
);
-- 同一策略+同一股票只允许一个在跑的账户。平仓/暂停后可以重建，所以是部分索引。
CREATE UNIQUE INDEX IF NOT EXISTS uq_sim_running
    ON sim_account(strategy_id, code) WHERE status = 'running';

-- 模拟盘成交。字段与回测的 trades 一一对应，前端可以用同一套渲染。
CREATE TABLE IF NOT EXISTS sim_trade (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL,
    ts          TEXT NOT NULL,        -- 成交日
    from_ts     TEXT,                 -- 产生这笔委托的信号日
    side        TEXT NOT NULL,        -- buy / sell
    kind        TEXT NOT NULL,        -- signal / stop_loss / take_profit / trailing_stop / max_hold
    price       REAL, shares INTEGER, amount REAL, pnl REAL,
    fees        TEXT,                 -- JSON
    reason      TEXT,
    settled_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_sim_trade_acct ON sim_trade(account_id, ts);

-- 每日结算后的资产快照。主键含 ts，重算同一天是覆盖而不是追加（幂等的关键之一）。
CREATE TABLE IF NOT EXISTS sim_equity (
    account_id  INTEGER NOT NULL,
    ts          TEXT NOT NULL,
    close       REAL,
    cash        REAL,
    position    REAL,
    equity      REAL,
    day_pnl     REAL,                 -- 相对上一交易日资产的变化
    day_pnl_pct REAL,
    PRIMARY KEY (account_id, ts)
);

-- 信号日志。**被拒绝的信号也要记**：PRD 场景里"当日跌停封死，信号顺延到下一交易日"
-- 这句话就出自这张表的 skip_reason，不写下来用户只会觉得"怎么没卖出去"。
CREATE TABLE IF NOT EXISTS sim_signal (
    account_id  INTEGER NOT NULL,
    ts          TEXT NOT NULL,
    buy         INTEGER NOT NULL DEFAULT 0,
    sell        INTEGER NOT NULL DEFAULT 0,
    insufficient INTEGER NOT NULL DEFAULT 0,
    action      TEXT,
    skip_reason TEXT,
    settled_at  TEXT,
    PRIMARY KEY (account_id, ts)
);

-- 结算批次。谁触发的、补了哪几天、成没成，全记下来。
-- 幂等恢复靠它对账：游标只在整批成功后才推进，中途崩了下次重跑同一段。
CREATE TABLE IF NOT EXISTS sim_settlement (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL,
    trigger     TEXT NOT NULL,        -- create / manual / schedule
    from_ts     TEXT,                 -- 本次处理的第一根（含）
    to_ts       TEXT,                 -- 本次处理的最后一根（含）
    bars        INTEGER NOT NULL DEFAULT 0,
    trades      INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'ok',   -- ok / idle / error
    message     TEXT,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_sim_settle_acct ON sim_settlement(account_id, id);

-- 新闻与情绪（V3）
CREATE TABLE IF NOT EXISTS news (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT,
    title       TEXT,
    url         TEXT,
    source      TEXT,
    pub_time    TEXT,
    sentiment   REAL,
    summary     TEXT,
    fetched_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_news_code ON news(code, pub_time);
