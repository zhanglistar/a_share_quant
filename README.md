# A股 ETF MACD低位 + MA均线策略

这是一个基于 `akshare` ETF 行情接口的简单研究脚本，包含完整交易逻辑、回测输出和快收盘信号扫描。

## 安装

```bash
cd /home/listar/code/a_share_quant
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 当前采用方案

当前实盘测试采用 `original` 低位反弹策略，不使用 `breakout` 作为主策略。

推荐配置：

- ETF 池：`attack` 进攻池。
- 策略版本：`original`。
- 组合仓位：最多同时持有 2-3 只 ETF。
- 单只仓位：最多持有 50% 仓位时用 `--max-positions 2 --portfolio-slot 0.5`；更稳一点可用 3 只、每只约 33%。
- 数据源：优先 `sina`，实盘日报使用 `--realtime`。

采用这个版本的原因：

- `original` 在 2026 验证期表现最好，适合当前先做实盘观察。
- `breakout` 在 2025 趋势行情里收益更高，但 2026 验证期弱于 `original`，暂时只作为观察策略。
- 2025 年 `original` 表现一般，说明策略仍依赖市场环境，不能按 2026 的收益线性外推。

推荐实盘日报命令：

```bash
python a_share_macd_ma_strategy.py \
  --mode daily \
  --codes attack \
  --data-source sina \
  --start 20260101 \
  --realtime
```

推荐组合回测命令：

```bash
python a_share_macd_ma_strategy.py \
  --mode portfolio \
  --codes attack \
  --data-source sina \
  --cash 10000 \
  --portfolio-start 20260101 \
  --portfolio-end 20260621 \
  --max-positions 2 \
  --portfolio-slot 0.5
```

当前参考回测结果，1 万本金、`attack` 池、最多 2 只、每只 50%：

- 2025 年：约 `+2.13%`，最大回撤约 `-10.47%`。
- 2026 年截至 2026-06-18：约 `+18.90%`，最大回撤约 `-8.59%`。

## 策略逻辑

买入：

- 快收盘价格从 5 日线下方向上穿过 5 日线。
- 当日成交量不能明显缩量，默认要求 `volume > volume_ma5 * 0.8`。如果要更严格的放量，可调高 `--volume-ratio`。
- MACD 处于低位：`DIFF < 0 且 DEA < 0`，或者柱体仍小于 0 但正在向 0 靠近。
- 满足后买入半仓。
- 次一交易日如果突破/站上 17 日线，且成交量满足量能条件，加到满仓。

卖出：

- 持仓后，如果价格低于 `MA5 * (1 - 10%)`，卖出当前持仓的 1/3。
- 如果价格跌破 17 日线，清仓离场。

注意：回测使用日线收盘价模拟“快收盘”成交。真实 14:00-14:30 的价格还会变化，实盘信号建议只当候选提醒。

## 回测示例

```bash
python a_share_macd_ma_strategy.py \
  --mode backtest \
  --codes 510300,159915 \
  --start 20200101 \
  --end 20260621
```

批量跑内置 ETF 池：

```bash
python a_share_macd_ma_strategy.py \
  --mode backtest \
  --codes core \
  --start 20240101 \
  --end 20241231
```

进攻池只保留高弹性方向：

```bash
python a_share_macd_ma_strategy.py \
  --mode daily \
  --codes attack \
  --data-source sina \
  --start 20260101 \
  --realtime
```

指定数据源：

```bash
# 默认：akshare 东方财富接口，失败时自动用新浪 ETF 日线兜底
python a_share_macd_ma_strategy.py --mode backtest --codes core --data-source akshare

# 直接使用新浪 ETF 日线
python a_share_macd_ma_strategy.py --mode backtest --codes core --data-source sina

# 尝试使用本地安装的 zzshare
python a_share_macd_ma_strategy.py --mode backtest --codes core --data-source zzshare
```

也可以自定义一个 CSV 文件，包含 `code` 列，然后传：

```bash
python a_share_macd_ma_strategy.py --mode backtest --codes-file etf_pool.csv --start 20240101 --end 20241231
```

对比策略版本：

```bash
python a_share_macd_ma_strategy.py \
  --mode compare \
  --codes core \
  --data-source sina \
  --start 20240101 \
  --end 20260621
```

策略版本：

- `original`：原始逻辑，低位 MACD + 上穿 MA5，跌破 MA17 清仓。
- `trend`：买入时增加趋势过滤，要求 `close > MA20 且 MA20 > MA60`。
- `trend_exit2`：在 `trend` 基础上，要求连续 2 天跌破 MA17 才清仓。
- `breakout`：趋势突破逻辑，要求 20 日收盘新高、`MA5 > MA17 > MA60`、`DIFF > DEA`、MACD 柱体继续增强，并且成交量大于 5 日均量的 1.2 倍；买入直接按目标仓位，跌破 MA10 或从持仓后最高收盘价回撤 10% 清仓。
- `adaptive`：按 ETF 选择策略；当前规则会剔除 `512800`、`516160`、`512690`，`159915` 用 `trend`，`512880`/`512070` 用 `trend_exit2`，其它用 `original`。

对比输出：

- `output/compare_summary.csv`：三版本逐 ETF 对比。
- `output/compare_variant_stats.csv`：三版本总体统计。
- `output/best_strategy_by_etf.csv`：按 `收益 / |最大回撤|` 选择的逐 ETF 最优版本。
- `output/best_strategy_by_etf_named.csv`：带 ETF 名称和分类的逐 ETF 最优版本。

训练/验证：

```bash
python a_share_macd_ma_strategy.py \
  --mode walk_forward \
  --codes core \
  --data-source sina \
  --train-start 20240101 \
  --train-end 20241231 \
  --test-start 20260101 \
  --test-end 20260621
```

walk-forward 会只用训练期选择每只 ETF 的策略版本，再固定到验证期运行；如果训练期所有版本都是负收益，则验证期选择空仓。

输出：

- `output/walk_forward_2024_train_2026_test.csv`
- `output/walk_forward_2024_train_2026_test_named.csv`
- `output/walk_forward_stats.csv`

当前版本的加仓条件已经调整为：半仓后突破/站上 MA17 且满足量能条件，才加到满仓。对应结果文件：

- `output/core_deduped_add_ma17_2025_2026_compare.csv`
- `output/core_deduped_add_ma17_stats.csv`

加入买入和加仓量能条件后的结果文件：

- `output/core_volume_buy_add_2025_2026_compare.csv`
- `output/core_volume_buy_add_stats.csv`

输出文件：

- `output/510300_trades.csv`：交易明细
- `output/510300_equity.csv`：每日权益曲线
- `output/summary.csv`：多股票汇总

## 快收盘信号

14:00-14:30 可以跑：

```bash
python a_share_macd_ma_strategy.py \
  --mode signal \
  --codes 510300,159915 \
  --start 20250101 \
  --realtime
```

如果不加 `--realtime`，会使用 akshare 日线里最新一根 K 线。

## 实盘日报

先维护 [positions.csv](/home/listar/code/a_share_quant/positions.csv)，再运行：

```bash
python a_share_macd_ma_strategy.py \
  --mode daily \
  --codes attack \
  --data-source sina \
  --start 20260101 \
  --realtime
```

日报会保存到 `signals/YYYYMMDD_daily_signals.csv`。详细说明见 [LIVE_TRADING.md](/home/listar/code/a_share_quant/LIVE_TRADING.md)。

实盘日报的 `--start` 只用于计算 MA 和 MACD，不需要从很早开始。一般保留最近 100-150 个交易日即可；当前是 2026 年 6 月，推荐用 `--start 20260101`，如果想让 EMA 更稳定，可以用 `--start 20251001`。

## 个股突破策略

个股使用独立脚本 [a_share_stock_breakout_strategy.py](/home/listar/code/a_share_quant/a_share_stock_breakout_strategy.py)。它不复用 ETF 的低位反弹逻辑，而是做更进攻的趋势突破扫描。

默认过滤：

- 剔除 ST、名称含“退”的股票。
- 剔除北交所和不常用代码段，只保留主板、创业板、科创板常见 A 股代码。
- 剔除当日成交额低于 1 亿的股票。
- 剔除价格低于 5 元或高于 120 元的股票。
- 剔除接近涨停的股票，避免信号出现但买不到。

买入候选条件：

- 收盘价或实时价突破 `60` 日新高。
- `MA5 > MA10 > MA20 > MA60`。
- 价格在 MA20 之上。
- 成交量大于 5 日均量的 `1.5` 倍。
- MACD `DIFF > DEA`，且柱体继续增强。

持仓卖出提醒：

- 跌破成本 `8%`：`SELL_ALL`。
- 从持仓高点回撤 `12%`：`SELL_ALL`。
- 跌破 MA20：`SELL_ALL`。
- 跌破 MA10：`REDUCE`，减仓或收紧止损。

运行全市场扫描：

```bash
python a_share_stock_breakout_strategy.py \
  --start 20250101 \
  --realtime \
  --top 30
```

只扫描指定股票：

```bash
python a_share_stock_breakout_strategy.py \
  --codes 000001,600519,300750 \
  --start 20250101 \
  --realtime \
  --show-watch
```

对指定股票做回测：

```bash
python a_share_stock_breakout_strategy.py \
  --mode backtest \
  --codes 000001,600519,300750 \
  --start 20250101 \
  --end 20260621 \
  --cash 100000
```

回测输出：

```text
output/stock_breakout_summary_YYYYMMDD_YYYYMMDD.csv
output/stock_breakout_CODE_YYYYMMDD_YYYYMMDD_trades.csv
output/stock_breakout_CODE_YYYYMMDD_YYYYMMDD_equity.csv
```

个股持仓维护在 [stock_positions.csv](/home/listar/code/a_share_quant/stock_positions.csv)：

```csv
code,shares,entry_price,peak_price,entry_date,notes
300750,200,180.50,195.20,2026-06-18,突破买入
```

扫描结果会保存到：

```text
signals/YYYYMMDD_stock_breakout_signals.csv
```

20w 本金建议：

- 先用 5w-10w 测试个股突破策略，ETF 主策略继续保留。
- 个股最多持有 3-5 只。
- 单票初始仓位 2w-4w，单票最大不超过 5w。
- 单票最大亏损控制在本金的 1%-1.5%，也就是约 2000-3000 元。

## 参数

- `--asset etf`：默认使用 ETF 数据源 `fund_etf_hist_em` / `fund_etf_spot_em`。如果要临时测股票，可传 `--asset stock`。
- `--data-source akshare`：历史行情数据源，可选 `akshare`、`sina`、`zzshare`。默认 `akshare`，ETF 日线失败时自动用 `sina` 兜底。
- `--variant original`：单独回测某个策略版本，可选 `original`、`trend`、`trend_exit2`、`adaptive`、`breakout`。
- `--codes core`：使用内置 ETF 核心池 [etf_pool.csv](/home/listar/code/a_share_quant/etf_pool.csv)，只读取 `enabled=1` 的标的。
- `--codes attack`：使用进攻池，只读取 `enabled=1 且 attack=1` 的高弹性标的。
- `--codes all`：使用内置 ETF 全量池，包括已标记排除的标的。
- `--codes-file etf_pool.csv`：使用自定义 ETF 池，CSV 必须包含 `code` 列。

当前核心池已经剔除 walk-forward 表现较差的非银、银行、医药、医疗、消费、酒、上证50、沪深300、证券、红利、部分新能源/红利/5G 标的。排除原因保留在 `etf_pool.csv` 的 `exclude_reason` 列。

核心池也做了主题去重：创业板/创业板50 保留创业板 ETF，科创50/科创板50 保留科创板50 ETF，半导体/芯片/科创芯片保留科创芯片 ETF，人工智能保留易方达人工智能 ETF。

进攻池当前包含：创业板、科创板50、科创芯片、人工智能、机器人、通信、新能车、军工。实盘建议同一时间最多持有 1-2 只，按 daily 日报的 `rank_score` 优先选择。

## 组合级进攻回测

组合回测使用统一账户资金，不再按每只 ETF 独立计算。示例：

```bash
python a_share_macd_ma_strategy.py \
  --mode portfolio \
  --codes attack \
  --data-source sina \
  --cash 10000 \
  --portfolio-start 20260101 \
  --portfolio-end 20260621 \
  --max-positions 2 \
  --portfolio-slot 0.5
```

趋势突破组合示例：

```bash
python a_share_macd_ma_strategy.py \
  --mode portfolio \
  --codes attack \
  --portfolio-strategy breakout \
  --data-source sina \
  --cash 10000 \
  --portfolio-start 20260101 \
  --portfolio-end 20260621 \
  --max-positions 2 \
  --portfolio-slot 0.5
```

参数含义：

- `--max-positions 2`：最多同时持有 2 只 ETF。
- `--portfolio-slot 0.5`：每只 ETF 加满后的目标仓位 50%；反弹策略首次买入为目标仓位的一半，突破策略直接买到目标仓位。
- `--portfolio-strategy original`：组合默认使用低位反弹逻辑；可改为 `breakout` 测试趋势突破逻辑。

当前测试结果显示，低位反弹组合在 2026 上半年更强；`breakout` 在 2025 趋势行情里更进攻，但 2026 验证期不应直接替代主策略。使用 1 万本金、进攻池、最多 2 只、每只 50% 仓位的组合回测：

- 低位反弹：2025 年约 `+2.13%`，最大回撤 `-10.47%`；2026 年截至 2026-06-18 约 `+18.90%`，最大回撤 `-8.59%`。
- `breakout`：2025 年约 `+29.43%`，最大回撤 `-10.21%`；2026 年截至 2026-06-18 约 `+6.27%`，最大回撤 `-6.22%`。

因此当前建议：实盘主策略仍看低位反弹信号，`breakout` 只作为市场明显走强时的进攻备选或观察信号。
- `--ma5-break-ratio 0.10`：低于 5 日线 10% 卖出 1/3。
- `--macd-fast 12 --macd-slow 26 --macd-signal 9`：MACD 参数，默认使用网格搜索综合表现最好的 `12/26/9`。如果要测试你指定的参数，可传 `--macd-fast 10 --macd-slow 20 --macd-signal 7`。
- `--volume-window 5 --volume-ratio 0.8`：买入量能条件，默认成交量大于 5 日均量的 0.8 倍；如果要严格放量，可用 `--volume-ratio 1.0` 或 `1.2`。
- `--breakout-window 20 --breakout-volume-ratio 1.2`：突破策略参数，默认突破 20 日收盘新高，且成交量大于 5 日均量的 1.2 倍。
- `--trailing-stop 0.10`：突破策略持仓后，从最高收盘价回撤 10% 清仓。
- `--hist-turn-ratio 0.003`：判断 MACD 柱体“即将转正”的阈值，柱体绝对值除以价格小于该值且柱体变大。
- `--adjust qfq`：默认前复权，其他可选 `hfq` 或空字符串。
- `--cash 100000`：初始资金。
- `--fee 0.0003`：佣金。
- `--stamp-tax 0`：卖出印花税，ETF 默认按 0 处理。
- `--retries 3`：行情接口失败时的重试次数；长区间请求失败时脚本会尝试分年拉取。

## 说明

这个脚本只用于策略研究和信号验证，不构成投资建议。实盘还有滑点、最小手续费、T+1、盘口成交、停牌或异常折溢价等问题，正式使用前需要补更严格的交易约束。

`zzshare` 说明：当前脚本会尝试 import `zzshare`，并自动识别常见的 ETF 日线函数名，例如 `fund_etf_hist`、`etf_daily`、`get_etf_daily` 等。如果你的 `zzshare` API 函数名或字段不同，把函数签名贴出来即可精确适配。
