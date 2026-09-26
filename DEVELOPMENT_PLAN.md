# A股策略验证系统 · 开发计划（V1）

版本：v1.0 ｜ 日期：2026-09-25 ｜ 依据：`PRD.md`（唯一依据）+ `UI_DESIGN.md`（界面附件）

---

## 0. 这份计划怎么用

### 0.1 你我各自的分工

| 谁 | 做什么 |
|---|---|
| **我（写代码）** | 按阶段顺序实现文件、函数、组件；每阶段结束自测并给出「本阶段验收清单」 |
| **你（验收）** | 大部分阶段只需要：**双击 `start.bat` → 打开浏览器 → 照清单点几下 → 告诉我哪条没过** |

你不需要看懂代码，也不需要敲命令。唯一需要你动手的准备工作：**启动 Docker Desktop**（等右下角小鲸鱼图标不闪动）。

### 0.2 阶段门禁（硬性规定）

一个阶段只有同时满足这三条才算完成，才允许进下一个阶段：

1. `docker compose up` 起来不报错，`http://localhost:8000` 能打开；
2. 容器内 `python -m app.selfcheck <阶段号>` 全部 PASS（自动化数字自检，见 §3）；
3. 本阶段对应的 PRD 第九章验收条目**人工点验通过**。

任何一条不过 → 停在当前阶段修，不带病往下走。

### 0.3 冲突处理

- 发现 PRD 写错或做不到：**先改 PRD，再改代码**，并在本文件 §9「计划偏差记录」里追加一行。
- 不允许"代码先跑起来、PRD 以后再补"。

---

## 1. 技术选型（已定，不再动摇）

| 层 | 选型 | 理由 | 明确不用 |
|---|---|---|---|
| 前端 | 原生 HTML + CSS + ES6 JS，多页面（非 SPA） | 无构建步骤，你自己在记事本里改一行字刷新就生效；浏览器前进后退可用；回测结果页可收藏 | React / Vue / Vite / Node |
| 图表 | ECharts（下载 `echarts.min.js` 放到本地 `js/vendor/`） | K线 + dataZoom + 标记点原生支持；离线可用，不依赖 CDN | 任何 CDN 引用 |
| 后端 | Python 3.11 + FastAPI + Uvicorn（单进程） | 与 pandas 同语言，取数算指标零转换；自带 OpenAPI 文档便于我自检 | Flask + 手写路由、Node 后端 |
| 计算 | pandas 2.2.3 + numpy 2.2.1（向量化求值） | 指标和条件全向量化，5年日线毫秒级 | 逐行 for 循环求值、TA-Lib（Windows/容器编译麻烦） |
| 存储 | SQLite（WAL 模式），单文件 `./data/quant.db` | 单人本地，零运维，备份=复制一个文件 | PostgreSQL / Redis / MongoDB |
| 行情数据 | akshare（主）→ baostock（备），各重试 3 次 | 免费、无需注册、覆盖 A股全市场；两源互为兜底 | 同花顺 iFinD（企业付费，个人拿不到）、tushare pro（需 token + 积分） |
| 搜索 | pypinyin 生成拼音首字母存库 | 支持 `gzmt → 600519 贵州茅台` | 前端模糊匹配（数据量大） |
| LLM | DeepSeek（OpenAI 兼容接口），**V2 才接** | 你已确认；Key 只存本地 `data/` | V1 不接任何 LLM |
| 部署 | Docker Compose，**单容器**（FastAPI 同时托管前端静态文件） | 一个 `start.bat` 解决；避免跨域与双服务编排 | 前后端分离双容器、Nginx、系统级 Python 安装 |
| 网络暴露 | 端口绑 `127.0.0.1:8000:8000` | 无登录系统，绑局域网会被同 WiFi 设备访问（PRD 验收 L11） | `0.0.0.0` |

### 1.1 已定死的技术红线（每个阶段都要守）

1. **AI 永远不生成也不执行代码。** DeepSeek 只能输出固定 JSON，字段与指标名走白名单校验，由固定引擎解释执行。
2. **fail-closed。** 数据缺口超过容忍上限 → 拒绝运行；NaN → 标记「数据不足」并跳过，**绝不静默当 False**。
3. **回测与实时模拟共用同一套撮合与费用代码**（`backtest.py` + `db.get_fee_config()`）。改撮合逻辑必须同时回归两边，V2 时尤其注意。
4. **每行 K 线记录 `source`**，混源要能查出来并在界面提示。
5. **不接实盘、不下真单**；所有结果页底部固定免责声明。
6. 新增任何指标：白名单（`indicators.py`）与前端列表（`GET /api/indicators`）**同时更新**，二者同源，不允许前端硬编码。

---

## 2. 阶段依赖图

```
阶段0 地基收口（Docker + 空页 + 自检脚手架）
  │
  ├──────────────► 阶段1 数据源实测（★最大风险，必须最早做）
  │                    │
  │                    ▼
  │                阶段2 数据层（取数/缓存/搜索/重采样/混源）
  │                    │
  ▼                    ▼
静态文件托管 ────►  阶段3 指标库 + DSL 校验 + 向量化求值 + 人话
                       │
                       ▼
                    阶段4 回测引擎 + 风控 + 绩效指标 + 结果落库 ★核心
                       │
        ┌──────────────┼──────────────────────────┐
        ▼              ▼                          ▼
   阶段5 前端通用   阶段6 首页（建策略）      阶段8 我的任务+设置
   组件与规范            │                       
        └────────────────┴──────► 阶段7 回测运行页 + 信号溯源 ★重交互
                                       │
                                       ▼
                                  阶段9 文档 + 端到端冒烟
```

关键依赖说明：

- 阶段 1 必须在**任何**数据相关代码之前完成 —— 我写的 akshare 函数名来自知识、**尚未实测**，akshare 接口变动频繁，不先验证就会把错误假设灌进整条链路。
- 阶段 4 依赖 2 + 3（要真数据 + 要能求值 DSL）。
- 阶段 5 可与 4 并行（纯前端，不碰引擎）。
- 阶段 6 / 7 / 8 依赖 5；7 另依赖 4。
- 阶段 9 依赖全部。

---

## 3. 全局约定（一次写清，后续不再重复）

### 3.1 目录结构（最终形态）

```
my-schedule/
├── PRD.md  UI_DESIGN.md  DEVELOPMENT_PLAN.md  DOCKER使用指南.md
├── docker-compose.yml  start.bat  stop.bat  logs.bat  test.bat
├── data/                              ← 挂载卷，容器删了数据还在
│   └── quant.db
├── backend/
│   ├── Dockerfile  requirements.txt
│   └── app/
│       ├── __init__.py  config.py  schema.sql  db.py      （已完成）
│       ├── main.py            FastAPI 入口 + 静态托管 + 全局异常处理
│       ├── errors.py          四类错误（数据源/输入/完整性/程序）统一构造
│       ├── selfcheck.py       分阶段自动化自检（数字可复核项）
│       ├── datasource.py      akshare/baostock 取数 + 重试降级 + source
│       ├── stocks.py          股票列表/拼音/搜索
│       ├── store.py           K线索引缓存 + 增量 + 重采样 + 混源检测
│       ├── indicators.py      白名单指标实现（向量化）+ 中文名 + 术语解释
│       ├── dsl.py             DSL 结构定义 + 白名单校验 + 向量化求值
│       ├── explain.py         DSL → 人话中文
│       ├── templates.py       6 个内置模板
│       ├── backtest.py        ★撮合内核（决策/成交/T+1/涨跌停/手续费/风控）
│       ├── metrics.py         11 项绩效 + 基准对齐
│       ├── preflight.py       运行前预检（不下载数据）
│       └── api/
│           ├── meta.py        health / indicators / operators / templates
│           ├── stocks.py      search / preflight
│           ├── strategies.py  CRUD / 复制 / 存为模板
│           ├── backtests.py   提交 / 结果 / trace / compare / export
│           ├── settings.py    读写 / 恢复默认 / 数据源测试 / 导出
│           └── cache.py       stats / stocks / refresh / delete
└── frontend/
    ├── index.html  backtest.html  tasks.html  settings.html
    ├── css/  base.css  components.css  pages.css
    └── js/   api.js format.js termtip.js toast.js dialog.js
              condition.js stockpicker.js preflight.js
              charts.js trace.js index.js backtest.js tasks.js settings.js
              vendor/echarts.min.js
```

（`sim.html` / `sentiment.html` / `api/sim.py` / `news.py` 属 V2/V3，本计划不建。）

### 3.2 错误响应统一格式

```json
{ "error": { "kind": "datasource | input | integrity | program",
             "code": "KLINE_GAP_TOO_LARGE",
             "message": "面向你的中文说明",
             "detail": "可折叠的技术细节 / 缺失日期区间",
             "field": "输入类错误时定位到哪个字段",
             "retriable": true } }
```

前端 `api.js` 按 `kind` 上色：数据源=橙（给「重试」「改用baostock」）、输入=红框+焦点跳转、完整性=红（fail-closed）、程序=红+可复制错误信息。对应 PRD 验收 L3–L5。

### 3.3 HTTP 约定

- 校验类错误 `400`，资源不存在 `404`，数据完整性拒绝 `422`，数据源不可用 `502`，未预期 `500`。
- 所有写接口返回**改动后的完整对象**，前端不靠本地推测更新。
- 时间一律 `YYYY-MM-DD`（日线），字符串比较即时间比较，避免时区坑。

### 3.4 `selfcheck.py`（自动化门禁）

一个可以按阶段跑的自检入口，`test.bat` 双击即运行：

```
docker compose exec app python -m app.selfcheck 4
```

用**人工构造的合成K线**（不是真行情）驱动回测引擎，把 PRD 第九章 F1–F13、G1–G4、D11–D13 变成断言。例如：

- 造一笔「100股 × 10元」→ 断言佣金 == 5.00（F2）
- 造「次日一字涨停」→ 断言不成交且日志含"涨停无法买入"（F5）
- 造「盘中最低 8.9 元、止损价 9.0」→ 断言成交价 == 9.0（F8）
- 造「次日开盘 8.5 跳空低于止损价 9.0」→ 断言成交价 == 8.5（F9）
- 全交易盈亏加总 + 期末市值 − 初始资金，与总收益率金额差 < 0.01 元（F10）

这样"引擎算错没有"不用靠肉眼，也不会被我后面重构悄悄改坏。

---

## 4. 阶段计划

### 阶段 0 · 地基收口

**目标**：把"容器能起来、页面能打开、自检能跑"这条最细的路先打通，后面每个阶段都在真环境里跑，不靠猜。

**依赖**：无。**需要你先启动 Docker Desktop。**

**已完成（复用，不重写）**：`docker-compose.yml`、`backend/Dockerfile`、`requirements.txt`、`start.bat` / `stop.bat` / `logs.bat`、`app/config.py`、`app/db.py`、`app/schema.sql`。

**新建 / 修改**

| 文件 | 内容 |
|---|---|
| `backend/app/errors.py` | `AppError(kind, code, message, detail, field, retriable)`；`datasource_error() input_error() integrity_error() program_error()` 四个构造器 |
| `backend/app/main.py` | `create_app()`；启动时 `init_db()`；`GET /api/health` → `{status:"ok", version, db:"ready"}`；`app.mount("/", StaticFiles(FRONTEND_DIR, html=True))`；`@app.exception_handler(AppError)` 与全局 `Exception` → §3.2 格式；日志带时间戳 |
| `backend/app/selfcheck.py` | `run(stage)` 分发；`--stage` 参数；PASS/FAIL 计数与退出码；阶段 0 只查 DB 初始化与默认设置写入 |
| `test.bat` | 双击跑 `docker compose exec app python -m app.selfcheck 全部`，中文输出 |
| `frontend/index.html` | 占位页：三段骨架 + TopNav（建策略/我的任务/设置）+ 数据源状态灯（先写死"未测试"）+ 底部免责声明 |
| `frontend/css/base.css` | CSS 变量：`--up:#d93026 --down:#0a8f5b`（红涨绿跌）、字号阶梯、间距阶梯、`--mono` 数字字体 |
| `backend/Dockerfile` | 加 `HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"` |

**组件**：无 JS 组件，只有 HTML 骨架。

**完成标准**

- A1：不启动 Docker → `start.bat` 只出中文引导、无英文堆栈、窗口不关。
- A2：启动 Docker → `[1/3][2/3][3/3]` → 浏览器自动打开，首页三段骨架渲染正常。
- A3：`docker compose ps` 端口显示 `127.0.0.1:8000->8000`。
- A4 / A5：`stop.bat`+`start.bat` 正常；`logs.bat` 能看日志、Ctrl+C 不退出容器。
- B2 / B3：`docker compose down --rmi all` 后重建，`data/quant.db` 仍在。
- L10：`docker-compose.yml` 含 `127.0.0.1:8000:8000` 与 `no-new-privileges:true`。
- L1：全站搜不到任何登录/注册字样。
- `test.bat` 阶段 0 全 PASS。
- F1–F5 勾选。

**风险**：首次 `pip install` 在 Docker 里可能拉镜像慢（已用清华源）。若 akshare 依赖链在 `python:3.11-slim` 上编译失败，退路是改用 `python:3.11-slim-bookworm` + `apt install -y libgomp1`。

---

### 阶段 1 · 数据源实测（★优先，纯验证不建功能）

**目标**：把 datasource 从"我以为的接口"变成"我验证过的接口"。这是全项目最大的未知风险。

**依赖**：阶段 0（要能在容器里跑 Python）。

**新建**

| 文件 | 内容 |
|---|---|
| `backend/app/datasource.py` | 只写取数，不写缓存。<br>`get_stock_list() -> DataFrame[code,name,market,industry]`<br>`get_daily(code, start, end, adjust="qfq") -> DataFrame[ts,open,high,low,close,volume,amount,turnover,pct_chg]`<br>`get_index_daily(code, start, end)`（000300）<br>`get_valuation_daily(code, start, end) -> DataFrame[ts,pe,pe_ttm,pb]`<br>`_ak_*` / `_bs_*` 两套实现 + `_with_retry(fn, tries=3)` + `fetch(...)`（按 `datasource_order` 顺序尝试，每行补 `source`）<br>`test_source(name) -> {ok, latency, error}` |
| `backend/app/spike.py` | 一次性探针脚本：打印每个 akshare/baostock 函数的**真实列名、行数、首末日期、耗时**，落盘 `data/spike-<日期>.txt` |

**必须实测回答的 8 个问题**（答案写进 `docs/数据源实测记录.md`）

1. `stock_zh_a_spot_em` / `stock_info_a_code_name` 哪个能稳定拿到全 A股代码+名称+行业？
2. `stock_zh_a_hist(symbol, period="daily", adjust="qfq")` 的中文列名到底是 `日期/开盘/.../换手率` 还是英文？返回条数上限？
3. 前复权数据的历史起点能到多远（决定 MA250 + 5年区间是否够用）？
4. baostock `query_history_k_data_plus` 的字段、`adjustflag` 取值、是否需要 `bs.login()`、并发限制？
5. 两源同一日同一只股票的**前复权价差多少**（决定混源警告的措辞与严重度）？
6. PE/PB：akshare 有无带日期的历史估值接口？没有的话 `PE`/`PB` 指标 V1 是否降级为「仅实时值、不参与历史回测」——**这条会直接影响 PRD §5.1 白名单，若要砍就要改 PRD**。
7. 连续请求 30 次的限流表现（决定要不要加 sleep、每批多少只）。
8. 沪深300 指数：`index_zh_a_hist` vs baostock `query_history_k_data_plus("sh.000300")`。

**完成标准**

- C10：`POST /api/datasource/test` 对 akshare、baostock 都返回 `✓ 连通正常 · 响应 X.Xs`（接口在阶段 2 正式暴露，此处用 spike 脚本验证）。
- C5：任一股票任一区间入库后，`SELECT DISTINCT source FROM kline` 非 NULL。
- C6：改 `datasource_order` 为 baostock 优先后能正常出数据。
- C8：断网 → 返回 `kind=datasource` 的橙色类错误，不崩溃、不返回半截数据。
- `docs/数据源实测记录.md` 里 8 个问题都有实际输出粘贴为证。
- F9 勾选（取数+重试+降级+source）；F11/F12 视实测结论决定是否本阶段完成。
- **门禁**：若 PE/PB 历史不可得，先更新 PRD §5.1 与 §7.2 再进阶段 2。

**风险与退路**：免费源接口经常改名。若 akshare 某函数彻底不可用 → 该数据项改由 baostock 出；两源都不行 → 记入 PRD §8.1 推迟，并在设置页说明栏标灰，**不做静默兜底**。

---

### 阶段 2 · 数据层（缓存 · 搜索 · 派生 · 混源）

**目标**：`代码 → 本地 SQLite 里一段干净可回测的K线`，并且第二次不重复下载。

**依赖**：阶段 1。

**新建 / 修改**

| 文件 | 函数 / 内容 |
|---|---|
| `backend/app/stocks.py` | `sync_stock_list(force=False) -> int`（写 `stock_basic`，用 pypinyin 生成 `pinyin` 首字母）；`search_stocks(kw, limit=8) -> [{code,name,market,pinyin}]`，三种匹配：代码前缀、名称子串、拼音前缀；`get_stock(code)`；`name_has_st(code,name)` |
| `backend/app/store.py` | `upsert_kline(df, code, period, adjust)`（`INSERT OR REPLACE`，单事务，批量 `executemany`）；`load_kline(code, period, adjust, start, end) -> DataFrame`（升序、去重、索引为 ts）；`ensure_kline(code, start, end, period, on_progress) -> {downloaded, cached, from_ts, to_ts}`（查 `sync_meta` 做**增量补**，含"缺头"判断）；`resample(df, "W"|"M")`（日线派生周/月线，OHLC 规则：open=首、high=max、low=min、close=末、volume/amount=sum、换手率 sum、涨跌幅按派生收盘重算）；`cache_stats()`；`cache_stock_list()`（含 `mixed_source` 标记：`COUNT(DISTINCT source)>1`）；`drop_cache(code)`；`clear_market_cache()` |
| `backend/app/datasource.py` | 接上阶段 1，加 `get_weekly/get_monthly` 走 `store.resample`（**不调外部接口**）；`get_benchmark(start,end)` 缓存进 `index_kline` |
| `backend/app/api/stocks.py` | `GET /api/stocks/search?kw=`；`POST /api/stocks/sync`（刷新股票列表） |
| `backend/app/api/cache.py` | `GET /api/cache/stats`；`GET /api/cache/stocks`；`POST /api/cache/refresh`；`DELETE /api/cache/{code}` |
| `backend/app/api/settings.py` | `GET/POST /api/settings`；`POST /api/datasource/test` |
| `backend/app/api/meta.py` | `GET /api/health` 返回真实数据源状态（读上次连通测试结果 + DB 状态） |
| `backend/app/selfcheck.py` | 阶段 2 断言：同一区间二次 `ensure_kline` 不触网（用打桩计数）；`resample` 的 OHLC 与手工核对一致；拼音 `gzmt`/`600519`/`贵州茅台` 三种查询都命中 600519；混源检测能返回 `mixed_source=True` |

**完成标准**

- C1、C2：搜 `gzmt` / `600519` / `贵州茅台` 都命中"600519 贵州茅台"。
- C3、C4：未缓存股票首次下载逐只出 `✓/⏳/○`（此阶段用命令行/接口验证），再跑同区间明显变快且提示"已缓存"。
- C5、C6、C7：source 入库、换源生效、混源能被查出并标记。
- C9：选周线跑数**未产生周线接口请求**（打桩断言 0 次）。
- K5、K6：缓存统计与已缓存列表数值与实际相符，混源行带 `⚠`。
- F6、F7、F8、F10、F11、F13 勾选（F12 视阶段 1 结论）。
- `test.bat 2` 全 PASS。

---

### 阶段 3 · 指标库 + DSL 校验 + 向量化求值 + 人话预览

**目标**：策略的"语言"定下来，并且**任何不合法的东西都进不来**。这是安全与正确性的闸门，做完就等于给 V2 的 AI 解析上了锁。

**依赖**：阶段 2（指标要在真K线上算）；但 DSL 校验与求值可先用合成数据并行开发。

**新建**

| 文件 | 内容 |
|---|---|
| `backend/app/indicators.py` | `INDICATORS: dict[name, IndicatorSpec]`，`IndicatorSpec = {code, cn, params:[{name,cn,default,min,max}], needs:str, term:{what,example,why}}`；实现 `compute(df, name, args) -> Series`，全部向量化；MACD(12,26,9)、RSI(n) 用 Wilder 平滑、KDJ(9,3,3)、BOLL(n,2)、EMA、VOL_MA、LIANGBI（量比=当日量/前5日均量）、`PE`/`PB` 从 `valuation` 表按 ts 对齐（**只用当日及之前**）；**暖机段留 NaN，不填 0、不前向填充**；`term_of(name)` 供 `/api/indicators` 直接下发给前端（前端不硬编码，见 §1.1 红线 6） |
| `backend/app/dsl.py` | `VALIDATE`：递归校验 `signals/sizing/risk`，未知指标、未知运算符、参数越界、多余字段、超过 2 层嵌套 → `input_error`，错误信息带**具体字段路径与非法值**；`OPERATORS` 白名单 + 每个运算符的 `allowed_rhs`（`cross_above/cross_below` 要求左右都是"会变化的线"，右值为固定数字时判非法）；`evaluate(dsl, df) -> EvalResult`：返回 `buy: Series[bool]`、`sell: Series[bool]`，以及**逐根逐条件的取值矩阵**（供信号溯源复用，不重复计算）；`insufficient: Series[bool]`——任一操作数为 NaN 即标记"数据不足"，该根**既不是 True 也不是 False**；`cross_above` 用 `prev<=x & cur>x`，prev 为 NaN 时整根标数据不足 |
| `backend/app/explain.py` | `describe(dsl) -> {buy_lines:[str], sell_lines:[str], risk_lines:[str], summary:str}`；`CROSS_ABOVE` → "5日均线从下往上穿过20日均线"；`right.t=="num"` → "高于 1.5"；`mult` → "成交量的 1.5 倍以上"；术语一律取 `indicators.INDICATORS[*].cn` |
| `backend/app/templates.py` | 6 个模板（双均线金叉、均线回踩、放量突破、RSI超卖反弹、MACD零轴上方、量能+均线三条件组合），每个 = `{id,name,note,period,dsl}` |
| `backend/app/api/meta.py` | `GET /api/indicators`（含中文与三段式解释）、`GET /api/operators`、`GET /api/templates` |
| `backend/app/api/strategies.py` | `GET /api/strategies`、`GET /api/strategies/{id}`、`POST`、`PUT`、`DELETE`；`POST /api/strategies/{id}/duplicate`；`POST /api/strategies/{id}/save-as-template`；`POST /api/strategies/explain`（DSL→人话，前端 300ms 内刷新预览用） |
| `backend/app/selfcheck.py` | 阶段 3 断言（见完成标准） |

**完成标准**

- D11：POST 指标名 `FOO` → 400，错误信息里明确出现 `FOO` 与"不在白名单"。
- D12：POST 运算符 `__import__` → 400，日志中**无任何代码执行痕迹**（引擎里没有 `eval/exec/__import__` 三个词，用 `grep` 自检）。
- D13：含 `exec`/`eval` 字段或多余字段 → 400。
- D7（后端侧）：3 层嵌套 → 400 并说明"最多 2 层"。
- D6（后端侧）：`cross_above` + 固定数字右值 → 400。
- G4（引擎侧）：区间 10 根但用 `MA(20)` → 全部标"数据不足"，`buy.sum()==0`。
- NaN 不静默：构造含停牌缺口的数据，断言 `insufficient.sum()>0` 且这些根不出现在信号里。
- F18：对 6 个模板逐个 `describe()`，输出中文人话、无裸英文指标名。
- F14、F15、F16、F17、F18、F19、F20 勾选。
- `test.bat 3` 全 PASS。

> ✅ **本阶段数量不一致已澄清并落地**：F14 原文写「18 个」，PRD §5.1 列 24 个，阶段 3 开工时又补进 `HHV` / `LLV`、移出 PE/PB，最终 **24 个**。PRD §5.1 / §7.3 F14 已改成 24，过程记在 §9 阶段 3 各行。

---

### 阶段 4 · 回测引擎 + 风控 + 绩效（★核心）

**目标**：一台算得准、说得清（每笔为什么成交/为什么不成交）的撮合内核。

**依赖**：阶段 2 + 3。

**新建**

| 文件 | 内容 |
|---|---|
| `backend/app/backtest.py` | `run_one(code, dsl, df, fee, initial_cash, ctx) -> BacktestResult`。**逐根循环但只在成交层循环（信号已向量化，性能足够）**。每根的顺序固定：①取上一根收盘产生的信号 → ②本根开盘成交（涨跌停/T+1/整手检查）→ ③盘中风控（止损止盈移动止损）→ ④收盘估值 + 记录资产点。`_fills(side, price, qty)` 统一算费用；`_limit_price(prev_close, code, name)` 按板块取 `limit_rate()`；`_blocked(bar, limit)` 判断封死；`_round_lot(shares)`；`Account{cash,shares,cost,buy_date,high_since_buy}`；输出 `trades[]`（每笔含 `fee:{commission,stamp,transfer,slippage}`、`reason`、`signal_ts`、`exec_ts`）、`equity[]`、`signal_log[]`（含 `status: filled|rejected|deferred|insufficient` 与中文原因）、`quality:{total,valid,skipped,warmup,gap_days,gap_ratio,sources,mixed}` |
| `backend/app/metrics.py` | `compute(result, bench_df, fee) -> {total_return, annual_return, max_dd, sharpe, win_rate, profit_loss_ratio, trade_count, avg_hold_days, final_equity, bench_return, excess_return}`；年化与夏普用 `annual_days`（252）与 `risk_free_rate`（2%）；最大回撤按资产曲线峰谷；胜率按**完整买卖回合**统计；基准缺失日对齐为 NaN 并在 `quality` 里报告 |
| `backend/app/preflight.py` | `check(dsl, codes, start, end, period, settings) -> items[]`，7 项：已选策略/已选股票/日期区间/未来日期/**指标暖机是否够长**（用区间交易日数 × 0.8 估算）/缓存状态（未缓存只警告不阻塞）/停牌与缺口预估。每项 `{level:"ok|warn|error", text, jump_to}` |
| `backend/app/api/backtests.py` | `POST /api/backtests`（多股，逐只跑，`on_progress` 用 SSE 或轮询 `GET /api/backtests/pending`，先做轮询，简单可靠）；写 `backtest_run` + 每只 `backtest`（含**费率快照** `fee_snapshot`，PRD §6.1 原则 3）；`GET /api/backtests/{run_id}`；`GET /api/backtests/{run_id}/trace?code=&ts=`（阶段 4 先出数据，前端阶段 7 消费）；`GET /api/backtests/{run_id}/compare`；`GET /api/backtests/{run_id}/export?code=`（CSV，UTF-8 **BOM**）；`GET /api/backtests?limit=50` |
| `backend/app/selfcheck.py` | 用 `synth.py` 造夹具（合成K线：一字涨停日、一字跌停日、跳空低开日、盘中触止损日、停牌缺口日、最长持有日）跑引擎断言 |
| `backend/app/synth.py` | `make_bars(rows) -> DataFrame`，让验收用例能被读出来 |

**撮合顺序必须严格照 PRD §5.4 表格实现**（决策在 i 收盘 → 成交在 i+1 开盘；止损盘中触及按触发价、跳空按开盘价；佣金万2.5最低5双向；印花税千0.5仅卖；过户费万0.1双向；滑点 0.1%；T+1；板块涨跌停；100股整手；停牌跳过）。

**完成标准**

- **F1–F13 全部通过**（这一组是数字可复核的，全放进 `selfcheck`，一条都不能省）。
- G1：删掉 10% 的K线 → `422` + 缺口比例 + 具体缺失日期区间。
- G2：容忍上限调到 20% → 能跑，`quality` 里写清跳过根数与缺口位置。
- G3：`force=true` → 走二次确认（后端只需接受参数并在结果里标 `accuracy_warning:true`）。
- G4：全"数据不足"→ 零信号、`trades==[]`。
- F28–F32：信号日志含被拒绝原因；数据质量报告字段齐；fail-closed 生效；11 项指标齐全；结果落库、**重复 GET 不重算**。
- K3（后端部分）：旧记录读出的收益率不变（费率快照生效）。
- F21–F32 勾选。
- `test.bat 4` 全 PASS。**F10 误差 < 0.01 元 是硬指标。**

---

### 阶段 5 · 前端通用组件与规范

**目标**：把所有页面共用的"零件"做一次，避免后面每个页面各写一套、颜色与术语不一致。

**依赖**：阶段 0（骨架）+ 阶段 3 的 `/api/indicators`（术语词典来源）。可与阶段 4 并行。

**新建**

| 文件 | 内容 |
|---|---|
| `frontend/js/api.js` | `api.get/post/del(path, body)`；统一解包 §3.2 错误；按 `kind` 分派到 `toast`/`dialog`/字段高亮；401/404 兜底；超时（30s）提示"可能被限流" |
| `frontend/js/format.js` | `money(n)` 千分位、`money_cn(n)` 万/亿缩写、`pct(n)` 带 `+/-` 与颜色 class、`price(n)` 固定 2/3 位、`shares(n)`、`date(ts)`、`num_cn_class(n)`（涨跌色 + 符号，不只靠颜色，验收 I13） |
| `frontend/js/termtip.js` | 31 条词典（UI_DESIGN §10.1）+ 后端 `/api/indicators` 的动态条目合并；`<span data-term="最大回撤">` 自动挂 tooltip；三段式气泡（一句话是什么 / 举个例子 / 为什么重要）；键盘可达（focus 时显示，Esc 关闭）；不依赖 hover-only |
| `frontend/js/toast.js` | `toast.ok/warn/err(msg,{action,onAction})`；「撤销」按钮 5 秒倒计时；不打断操作 |
| `frontend/js/dialog.js` | `confirm({title,body,lose,okText,danger})`；**取消按钮在左且默认聚焦**；Esc=取消；文案必须写清"会失去什么"（UI_DESIGN §10.3） |
| `frontend/js/charts.js` | ECharts 封装骨架：`candlestick(el, bars, {marks, overlay, sub})`、`equityChart(el, series)`、`subChart(el, kind)`；红涨绿跌 `color:{0:up,1:down}`；B/S 用 `markPoint`（红B▲ / 绿S▼）；`locate(ts)`（定位并高亮某根，供溯源用） |
| `frontend/css/components.css` | card / btn / input / table / chip / badge / tabs / skeleton / tooltip / mask 层级与 z-index 约定 |
| `frontend/css/pages.css` | 首页 `1fr / 340px` sticky 栅格；≥1024px 正常、1000px 降级（确认面板下移、指标卡成列、表格横向滚动） |
| `frontend/js/vendor/echarts.min.js` | 本地引入，禁 CDN |

**完成标准**

- D8、D9：悬停「最大回撤」与指标 RSI 的 `?` → 三段式气泡。
- L2：对照 UI_DESIGN §10.1 的 31 条，逐条能弹出（此阶段用一页临时 demo 页检查）。
- L6：`confirm` 的取消在左、Esc 等于取消。
- I13、L9：格式化与 1000px 降级。
- 网络面板确认**无任何外部 CDN 请求**。
- F46、F47、F48、F49（组件层）勾选。

---

### 阶段 6 · 首页（建策略）

**目标**：三段布局 + 条件构建器 + 股票搜索 + 预检 + 右侧确认面板，形成"填完就能跑"的闭环。

**依赖**：阶段 5、阶段 3（indicators/operators/templates）、阶段 2（search）、阶段 4（preflight 可选先接）。

**新建**

| 文件 | 内容 |
|---|---|
| `frontend/js/condition.js` | 条件构建器（**本页最重的组件**）。`render(groups)`、`addCond/addGroup/delCond(可撤销)/toggleLogic`；指标下拉**按组分类**显示「中文名 (CODE)」+ 搜索；参数框随 `needs` 出现/消失（D4）；参数即时校验红框 + 中文提示（D5）；右值三形态切换（另一指标 / 固定数字 / 指标×倍数）；`cross_above/below` 时禁用"固定数字"并解释（D6）；**嵌套上限 2 层**，第 3 层 `[+加条件组]` 禁用并说明（D7）；每次改动 `debounce(300ms)` 调 `/api/strategies/explain` 刷新右侧人话预览（D2、F44） |
| `frontend/js/stockpicker.js` | 搜索框（`/` 聚焦）、8 条建议、代码/名称/拼音命中、已选 chips（可移除、显示市场与缓存状态）、多选上限提示 |
| `frontend/js/preflight.js` | 7 项检查表逐条渲染；红项阻塞「开始回测」并**点击红项跳到对应配置区**（E1）；黄项仅警告；全绿按钮变蓝（E6） |
| `frontend/js/index.js` | 页面装配；三个策略入口标签（手动搭 ✅ / 从模板选 ✅ / 用大白话 🔒）；模板载入 → 自动切到手动搭 + Toaster（D1）；大白话标签点击 → 说明弹层 + `去设置`（D14，不报错不空白）；模式选择卡（历史回测 ✅，实时模拟/情绪分析 🔒 显示但置灰）；周期与区间；保存策略；`Ctrl+Enter` 提交；提交 → `POST /api/backtests` → 进度浮层逐只 `✓/⏳/○` → 跳 `backtest.html?run=N` |
| `frontend/index.html` | 替换阶段 0 占位为真实三段结构；右侧 sticky 确认面板；底部「最近运行」5 条；空状态卡片含「看看模板」 |

**完成标准**

- D1–D10、D14 全通过。
- E1–E6 全通过。
- C1、C2 在真实界面上复测。
- L4（输入错误字段标红 + 焦点跳转）、L7（下载 >3s 出可取消、>30s 提示限流）、L8（`/`、`Esc`、`Ctrl+Enter`、Tab + focus 样式）。
- F42、F43、F44、F45、F50 之外的 F53（免责声明）勾选。
- **场景 A 可走通**（PRD §1.3：一个朴素想法能跑出结果）。

---

### 阶段 7 · 回测运行页 + 信号溯源

**目标**：把结果讲清楚 —— 图、指标卡、明细、以及"为什么买/为什么没买"。

**依赖**：阶段 5、6（面包屑与跳转）、4（trace/compare/export 接口）。

**新建**

| 文件 | 内容 |
|---|---|
| `frontend/backtest.html` | 多股标签 + 面包屑 `首页 › 策略名 › 股票名` + 指标卡区 + 主图/副图 + 数据质量条 + 交易明细 + 免责声明 |
| `frontend/js/backtest.js` | 标签切换（标签上显示各自收益率，红涨绿跌，I9）；指标卡 11 项 + `?`（I1）；MA 叠加勾选（I6）；副图切换 成交量/MACD/RSI/KDJ/BOLL（I5）；缩放与「重置缩放」（I7）；tooltip 含 OHLC/涨跌幅/成交量/当日持仓（I8）；明细表分页 50 行 + 表底合计（I12）；行点击联动定位（H2）；导出 CSV（I11）；`[重跑]`；数据质量条 + `[详情]` 展开（G5、G6）+ 缺口时的红色 fail-closed 条与 `[强制忽略并继续]`（G3） |
| `frontend/js/trace.js` | ★信号溯源浮层。`open(code, ts)` → 调 `/trace`；条件对照表每行「条件 / 当时值 / 门槛 / ✓✗ / 术语?」；无交易日 → 标题「本日未触发买入」+ 未满足项 `✗`（H3）；当日行情块 + 持仓状态；成交笔显示费用明细四行（F1/F2/F3 复核）；止损卖出写「触发止损 -8%」+ 成本价/止损价/当日最低/实际成交价（H4）；`[在K线图上定位]`（H5）；Esc 关闭并把焦点还回去（H6） |
| `frontend/js/compare.js` | 横向对比弹层：对比表（点表头排序）+ 归一化收益曲线（多条从 1.0 起）+ 一句解读提示；被任务页 `[并排对比]` 复用 |
| `frontend/js/charts.js` | 补完 `locate()` 高亮、`markPoint` 点击派发、双 Y 轴对齐 |

**完成标准**

- **H1–H6 全通过**（这是本产品的差异化能力）。
- I1–I14 全通过（含 I2/I3/I4 与沪深300 基准对齐、I11 Excel 打开中文不乱码）。
- G5、G6 在界面上复测。
- F33–F41 勾选。
- **场景 B、C 可走通**（同一策略跨股票对比；"为什么那天没买"）。

---

### 阶段 8 · 我的任务 + 设置

**目标**：东西存得住、找得回、参数改得动。

**依赖**：阶段 5–7（复用组件、弹层与跳转目标页）。可与阶段 7 部分并行。

**新建**

| 文件 | 内容 |
|---|---|
| `frontend/tasks.html` + `js/tasks.js` | 两个标签（策略库 / 回测记录；模拟盘标签 V2 不出现）；策略卡片字段（名称、买卖规则摘要 = `explain.summary`、风控、创建时间、跑过几次、涉及股票数、最近收益）+ `[跑回测]`（跳首页预填）`[编辑]`（保存时问"覆盖 / 另存为新"）`[复制一份]` `[删除]`（确认文案含"会同时删除它的 N 条回测记录，不可恢复"，后端级联删除）`[保存为模板]`；回测记录表（时间/策略/股票/区间/收益/回撤）+ 表头排序 + 行点击进详情 + `[重跑]` + 勾选 `[并排对比]`（只勾 1 条时禁用）；空状态 `[看看模板]` |
| `frontend/settings.html` + `js/settings.js` | 费用与滑点（无保存按钮，500ms 防抖即时保存 + `✓ 已保存`；下方「一笔 10 万买卖总成本约 X 元」实时换算）；数据源（顺序 + `[测试]` + 响应耗时）；数据缺口容忍上限；缓存（统计 + 已缓存股票列表含 `⚠` 混源 + `[更新]` `[删除]` + `[清空行情缓存]` 二次确认写明行数与"策略和回测记录保留"）；AI 分组显示「未配置 ⚠」+ 获取 Key 三步图文说明（不报错）；关于分组（版本号、`127.0.0.1:8000（仅本机可访问）`、免责声明）；`[恢复本组默认]` / `[恢复全部默认]`（确认文案含"不会删除你的 API Key、策略和回测记录"）；`[导出全部数据]` |
| `backend/app/api/settings.py` | `POST /api/settings/reset`（白名单 key，**不含 `deepseek_api_key`**）；`GET /api/export`（导出策略+回测+设置为 JSON，**显式剔除 `deepseek_api_key`**） |
| `backend/app/api/backtests.py` | `DELETE /api/backtests/{id}`；策略删除时级联删 `backtest_run`/`backtest` |

**完成标准**

- J1–J10 全通过。
- K1–K11 全通过（**K3 尤其重要：改费率后旧记录数值必须不变**；K9 导出文件里搜不到 Key）。
- B1–B4：重启/重建后策略、记录、设置全在。
- F50、F51、F52 勾选。

---

### 阶段 9 · 文档 + 端到端冒烟

**目标**：让"你一个人"能长期用它，不需要我。

**依赖**：全部。

**新建**

| 文件 | 内容 |
|---|---|
| `DOCKER使用指南.md`（PRD F54） | 五节，全中文、带截图位与"看到这个界面是正常的"式说明：① 第一次启动（装 Docker Desktop、WSL2 提示、启动、等鲸鱼不闪）② 每天怎么用（双击 `start.bat`，浏览器自动开）③ 怎么停（`stop.bat`，强调 `data/` 不丢）④ 出问题看什么（`logs.bat`、`data/` 在哪、怎么备份）⑤ 常见问题（端口被占用、镜像拉不动、代理/VPN、数据源报错、换电脑怎么迁移） |
| `README.md` | 3 行：这是什么、双击哪个文件开始、要看细节读 PRD.md |
| `docs/数据源实测记录.md` | 阶段 1 产出，若还没写则补 |
| `CHANGELOG.md` | V1.0 条目 |

**自检与收尾**

- `test.bat` 全阶段 PASS。
- 走一遍 PRD §9 **M1–M15 端到端冒烟**（3 只股票：贵州茅台 / 宁德时代 / 中国平安，双均线金叉，近5年，含横向对比、CSV 导出、改费率重跑、重启保留）。
- 回头逐条勾 PRD §7 的 F1–F54，未做的挪进 §8.1 并说明原因。
- 用 PRD §1.4 的标准自测：**你不看文档、不问命令行，能独立完成场景 A→B→C**。做不到就是 V1 没完成。
- A6：第二次 `start.bat` 30 秒内打开浏览器。

---

## 5. 每阶段交付物一览（可当检查表）

| 阶段 | 交付物（能看到的） | 关键 PRD 条目 | 验收组 | 工作量 |
|---|---|---|---|---|
| 0 | 容器能起、占位首页能开、`test.bat` 能跑 | F1–F5 | A、B2/B3、L1、L10 | 小 |
| 1 | `数据源实测记录.md` + 可用取数函数 | F9（部分） | C5/C6/C8/C10 | 中，**风险最高** |
| 2 | 搜索能用、K线入库、缓存/混源能查 | F6–F13 | C、K5/K6 | 中 |
| 3 | 24 指标 + DSL 校验 + 人话 + 6 模板 | F14–F20 | D11–D14、G4 | 中 |
| 4 | 回测结果数值正确且可复核 | F21–F32 | **F、G1–G3**、K3 | **大，最关键** |
| 5 | 组件与规范齐 | F46–F49 部分 | D8/D9、I13、L6/L9 | 中 |
| 6 | 首页完整可用 | F42–F45 | D1–D10、E、L4/L7/L8 | 大 |
| 7 | 回测页 + 信号溯源 | F33–F41 | **H、I**、G5/G6 | 大 |
| 8 | 任务 + 设置 | F50–F52 | J、K、B1/B4 | 中 |
| 9 | 使用指南 + 冒烟通过 | F54 | **M1–M15**、A6、L12 | 小 |

---

## 6. 风险登记

| # | 风险 | 何时暴露 | 影响 | 应对 |
|---|---|---|---|---|
| R1 | akshare 接口名/列名与我所记不符，或已下线 | 阶段 1 | 高 | 提前实测、baostock 兜底、把结论写文档；不通过不进阶段 2 |
| R2 | 免费历史 PE/PB 拿不到 | 阶段 1 | 中 | 从白名单移除 `PE`/`PB` 的历史回测能力，改 PRD §5.1/§7.2，模板相应调整 |
| R3 | 首次 `pip install` 在 slim 镜像缺编译依赖 | 阶段 0/1 | 中 | 加 `apt install libgomp1`，必要时换 `3.11-slim-bookworm` 或 `3.12-slim` |
| R4 | 限流：多股+5年连续下载被封 | 阶段 2/4 | 中 | 批次间 sleep、指数退避、`sync_meta` 支持断点续下、进度条可取消 |
| R5 | 撮合边界（涨跌停封死判定、跳空止损）算错 | 阶段 4 | **高，静默错误最危险** | 合成夹具把 F1–F13 全变成断言；每根K线内顺序固定成文档；改引擎必跑 `test.bat 4` |
| R6 | 前端无构建导致 JS 全局变量互相污染 | 阶段 6/7 | 低中 | 每个 js 文件用 IIFE + 单一 `window.App.*` 命名空间；不用 `document.write` |
| R7 | ECharts 本地文件与 PRD 要求的 dataZoom/markPoint 行为不符 | 阶段 7 | 低 | 锁定 echarts 5.x 具体版本；溯源高亮失败退化为"明细表联动 + 时间窗切换" |
| R8 | 我在阶段中悄悄扩大范围（做 V2 功能） | 全程 | 中 | 本文件 §1.1 红线 + 门禁；新想法一律先写进 PRD §8.1，不当场实现 |
| R9 | 你没启动 Docker 导致我无法自测（本机无 Python） | 阶段 0 起 | 高 | 阶段 0 开工前确认 Docker Desktop 已启动；`test.bat` 由你双击，结果截图给我 |

---

## 7. 需要你做的准备（阶段 0 之前）

1. 打开 **Docker Desktop**，等右下角小鲸鱼图标**不再闪动**（表示引擎已就绪）。
2. 双击项目里的 `start.bat`，看到 `[1/3]` 说明一切正常（此阶段还没有页面，报"找不到 index.html"之类是正常的，我会补上）。
3. 如果弹窗卡住或出现英文报错，把窗口内容截图/复制给我 —— 不要自己关窗口。

---

## 8. 开工前待你确认（PRD §10 遗留）

| # | 事项 | 不确认会怎样 | 我的建议 |
|---|---|---|---|
| 1 | 是否要「今日看板」（PRD §6.4 候选方案） | 若要，则新增一个页面 + 1 个阶段，V1 工期变长；若不要，本计划按**不做**执行 | **V1 不做**，先验证核心闭环，放到 V2 再评估 |
| 2 | F14「18 个指标」与 §5.1「24 个指标」不一致 | ~~阶段 3 无法确定指标数量~~ | **已解决**：阶段 3 按 24 个实现并回改 PRD，过程见 §9 |
| 3 | 60 分钟线（PRD §8.1 挪到 V2 实测） | 无 | 维持 V2 实测后决定 |
| 4 | `REQUIREMENTS.md` 是否删除 | 无（已标为历史留存） | 保留到 V1 验收通过再删 |

> 这 4 条里，**只有第 2 条阻塞阶段 3**，第 1 条影响 V1 范围。其余不阻塞，我按上表默认方案推进，你随时可以叫停。

---

## 9. 计划偏差记录（实施中追加，不回头改写）

| 日期 | 阶段 | 偏差 | 处理 |
|---|---|---|---|
| 2026-09-25 | 0 | 计划里写 `akshare==1.16.19`，该版本在 PyPI 上不存在，镜像构建直接失败 | 用 `pip install --dry-run --report` 解析出可安装矩阵，钉到 `akshare==1.18.97`；红线：升级必须重新跑阶段 1 联网自检 |
| 2026-09-25 | 0 | 个股市场前缀 `market_prefix("920047")` 返回 `sh`（北交所被 `"9"` 规则吃掉） | 把 `4/8/92` 判断提前；自检 `t12` 补北交所实取 |
| 2026-09-25 | 1 | 计划的「akshare 主 + baostock 备」不成立：baostock 走 TCP 10030，本机网络不放行 | 上游顺序改为 `sina → tencent → eastmoney`；baostock 适配器保留但默认不启用，设置页可手动开启并 `[测试]`；PRD §2.2 / §7.2 F9 同步修订 |
| 2026-09-25 | 1 | 新浪与腾讯的**前复权**序列历史段最大相差 56.25%（均值 3.93%），「缺哪补哪」会伪造信号 | 硬约束：一只股票一个上游、整段重下覆盖；库内检出多 `source` 按完整性错误 fail-closed；PRD §5.3 / F13 同步修订 |
| 2026-09-25 | 1 | `ak.stock_a_indicator_lg`（计划里的 PE/PB 来源）在 1.18.97 已不存在；替代的百度估值接口是稀疏序列（平均 2~15 天一个点） | PE/PB 移出 V1 白名单（24 → 22 个），`valuation` 表只存最近一期供展示；PRD §5.1 / §5.9 / §8.1 同步修订。§321 行"F14 写 18 个"的疑问一并解决：以白名单实际条数为准 |
| 2026-09-25 | 1 | 东财系接口本机 5/5 次 `RemoteDisconnected`（含 `index_zh_a_hist`）；新浪分钟线仅约 124 根 | 60 分钟线移出计划（不是推迟到 V2，是删除），PRD §8.1 记录原因 |
| 2026-09-25 | 1 | `fetch_index_daily` 复用个股 `market_prefix()`，000300 被拼成 `sz000300` → 新浪返回空表（akshare 内 `KeyError 'date'`） | 新增 `config.INDEX_PREFIX` + `index_symbol()`（000/999/880→sh，399→sz，899→bj），指数备源用腾讯分页接口；自检 `t13` 钉死该规则 |
| 2026-09-25 | 1 | 单位换算最初打算"看数值大小猜单位"，会把 0.8% 的换手率错放大成 80% | `_finalize()` 改为由调用方**显式声明** `volume_unit` / `turnover_unit`；自检 `t03` 专门守这条 |
| 2026-09-25 | 1 | 自检 `all` 会去 import 尚未开发的阶段文件，直接 `ImportError` | 改为 `STAGE_MODULES` + `DELIVERED` 白名单：只跑已交付阶段，请求未交付阶段**报错而不是跳过**，并在输出里提示哪些阶段还没断言 |
| 2026-09-25 | 2 | 计划表里的 `backend/app/selfcheck.py` 单文件装不下三阶段的断言（阶段 1 已 18 条、阶段 2 已 27 条），挤在一起没法读 | 改为包 `selfcheck/`：`harness.py` 放 `check/eq/near/stub/fresh_db/client`，每个阶段一个 `sN_*.py`，`__main__.py` 按 `STAGE_MODULES` 装配。跑法不变：`python -m app.selfcheck <阶段\|all>` |
| 2026-09-25 | 2 | 缓存"缺不缺最新一天"最初按**自然日**判断，实网冒烟时第二次 `/api/cache/refresh` 又重下了一遍（收盘前/周末/长假永远"缺一天"），违反验收 C4 | 新增必要数据⑥**交易日历**（`ak.tool_trade_date_hist_sina`，1990-12-19~今年年底，一个月刷一次）+ `sync_meta.attempted_to`；`wanted_end = last_trading_day(min(end, today))`。PRD §5.3 先补数据项与原因，代码再加 `db.migrate()` 给老库补列 |
| 2026-09-25 | 2 | 把 F13 的"整段重下"实现成"重下本次请求区间"，对已缓存到 09-24 的股票点[重新下载]只请求 01-05~01-16，先 `DELETE` 全表再只写回 10 根 —— 485 根历史凭空消失 | 重下范围改为 **缓存已有整段 ∪ 本次请求区间**（`to_ts = max(end, meta.last_ts)`），未来 `end` 夹到 `today()` 以免 `attempted_to` 记成未来把补数据永久卡死；PRD §5.3 补"整段到底指哪一段"；自检 `t05b`/`t05c` 各钉一条（已验证：把修复改回旧写法这两条会红） |
| 2026-09-25 | 2 | `/api/cache/refresh` 两条分支的 `rows` 意思不一样（下载分支=整段写入根数 663，走缓存分支=区间内根数 178），前端同一条文案会自相矛盾 | 统一为"**请求区间内的根数**"，整段写入根数另放 `written`；以库里实际存下的内容回报（缺价根在 `upsert` 已被丢弃），一根都没存下时抛完整性错误而不是返回成功 |
| 2026-09-25 | 2 | 拼音检索按**逐字**转换，多音字全错（重庆银行→`hx` 开头）；另有 4 条我自己写错的预期串 | `pinyin_of` 改为词组级 `lazy_pinyin`（`Style.FIRST_LETTER`），预期串按实测 pypinyin 输出校正；自检 `t10` 覆盖多音字 |
| 2026-09-25 | 3 | PRD §5.1 的白名单里没有"前 N 日最高/最低价"，而 §3.2 的内置模板「放量创新高」必须用"突破前 20 日新高"才能写出来 | 按红线先改 PRD 再写代码：§5.1 补 `HHV(n)` / `LLV(n)` 并说明来由，F14 与 §10 第 5 条同步改为 **24 个**；顺带支持"跌破前低"这一类止损条件 |
| 2026-09-25 | 3 | 本计划 §3.3 列的 6 个模板名（均线回踩 / 放量突破 / MACD零轴上方 / 量能+均线三条件组合）与 PRD §3.2 的 6 个不一致 | **以 PRD §3.2 为准**（双均线金叉 / MACD金叉 / RSI超卖反弹 / 布林带突破 / 放量创新高 / 均线+止损），`templates.py` 照 PRD 实现，自检 `t30` 把名字钉死 |
| 2026-09-25 | 3 | 周期对外的写法原先接口收 `w` / `m`、数据库存 `daily`、前端又要再翻译一遍，同一件事三种叫法 | 统一成 `daily` / `weekly` / `monthly` 一套词（`store.PERIODS`，`/api/operators` 下发）；并给 `ensure_kline` 加 `DERIVED_PERIOD` 守卫——谁想把周线当日线写进库就当场拒绝，C9 的"周线不发接口请求"从此在数据层就有兜底 |
| 2026-09-25 | 3 | `LLV` 初版复用 `HHV` 的实现忘了把 `max` 换成 `min`，算出来是"前 N 日**最低价里的最高**" | 突破/跌破类条件会整条反过来（例：前 2 日最低 7 会被判成 9）；改为 `_hhv(df, n, col, newest)` 一个开关，自检 `t10` 同时钉 HHV 与 LLV |
| 2026-09-25 | 3 | `_wilder` 的种子按**位置**取前 n 个，但传进去的是 `diff()` 的结果（第 0 根天然是 NaN），于是种子只用了 n-1 个变动、还比数据本身早一根出值 | 改为按"前 n 个**真实存在**的变动"起算（RSI(n) 第 n+1 根才有值）；自检 `t05` 手算 66.6667、`t03` 通查 24 条指标的暖机声明 |
| 2026-09-25 | 3 | F15 写"与 JSON Schema"，实际交付的是 `dsl.py` 里的手写递归校验器 | 保留手写校验：错误信息带**字段路径**（如 `signals.buy.items[0].left.name`），前端能直接跳到那一格，这是 JSON Schema 报错做不到的；约束改由 `/api/indicators` + `/api/operators` 下发给前端 |
| 2026-09-25 | 3 | `POST/PUT/duplicate` 返回的是裸数据库行——没有 `explain`、`dsl` 还是 JSON 字符串，和 `GET` 的形状不一样，前端存完得再查一次 | `_insert()` 统一走 `_shape()`，四个写接口和两个读接口同一份返回结构；自检 `t32` 钉住"新建响应里就带人话预览" |
