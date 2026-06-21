# A股 ETF MACD低位 + MA均线策略

这是一个基于 `akshare` ETF 行情接口的简单研究脚本，包含完整交易逻辑、回测输出和快收盘信号扫描。

## 安装

```bash
cd /home/listar/code/a_share_quant
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 策略逻辑

买入：

- 快收盘价格从 5 日线下方向上穿过 5 日线。
- MACD 处于低位：`DIFF < 0 且 DEA < 0`，或者柱体仍小于 0 但正在向 0 靠近。
- 满足后买入半仓。
- 次一交易日如果突破/站上 17 日线，加到满仓。

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

当前版本的加仓条件已经调整为：半仓后突破/站上 MA17 才加到满仓。对应结果文件：

- `output/core_deduped_add_ma17_2025_2026_compare.csv`
- `output/core_deduped_add_ma17_stats.csv`

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
  --codes core \
  --data-source sina \
  --start 20260101 \
  --realtime
```

日报会保存到 `signals/YYYYMMDD_daily_signals.csv`。详细说明见 [LIVE_TRADING.md](/home/listar/code/a_share_quant/LIVE_TRADING.md)。

实盘日报的 `--start` 只用于计算 MA 和 MACD，不需要从很早开始。一般保留最近 100-150 个交易日即可；当前是 2026 年 6 月，推荐用 `--start 20260101`，如果想让 EMA 更稳定，可以用 `--start 20251001`。

## 参数

- `--asset etf`：默认使用 ETF 数据源 `fund_etf_hist_em` / `fund_etf_spot_em`。如果要临时测股票，可传 `--asset stock`。
- `--data-source akshare`：历史行情数据源，可选 `akshare`、`sina`、`zzshare`。默认 `akshare`，ETF 日线失败时自动用 `sina` 兜底。
- `--variant original`：单独回测某个策略版本，可选 `original`、`trend`、`trend_exit2`、`adaptive`。
- `--codes core`：使用内置 ETF 核心池 [etf_pool.csv](/home/listar/code/a_share_quant/etf_pool.csv)，只读取 `enabled=1` 的标的。
- `--codes all`：使用内置 ETF 全量池，包括已标记排除的标的。
- `--codes-file etf_pool.csv`：使用自定义 ETF 池，CSV 必须包含 `code` 列。

当前核心池已经剔除 walk-forward 表现较差的非银、银行、医药、医疗、消费、酒、上证50、沪深300、证券、红利、部分新能源/红利/5G 标的。排除原因保留在 `etf_pool.csv` 的 `exclude_reason` 列。

核心池也做了主题去重：创业板/创业板50 保留创业板 ETF，科创50/科创板50 保留科创板50 ETF，半导体/芯片/科创芯片保留科创芯片 ETF，人工智能保留易方达人工智能 ETF。
- `--ma5-break-ratio 0.10`：低于 5 日线 10% 卖出 1/3。
- `--macd-fast 12 --macd-slow 26 --macd-signal 9`：MACD 参数，默认使用网格搜索综合表现最好的 `12/26/9`。如果要测试你指定的参数，可传 `--macd-fast 10 --macd-slow 20 --macd-signal 7`。
- `--hist-turn-ratio 0.003`：判断 MACD 柱体“即将转正”的阈值，柱体绝对值除以价格小于该值且柱体变大。
- `--adjust qfq`：默认前复权，其他可选 `hfq` 或空字符串。
- `--cash 100000`：初始资金。
- `--fee 0.0003`：佣金。
- `--stamp-tax 0`：卖出印花税，ETF 默认按 0 处理。
- `--retries 3`：行情接口失败时的重试次数；长区间请求失败时脚本会尝试分年拉取。

## 说明

这个脚本只用于策略研究和信号验证，不构成投资建议。实盘还有滑点、最小手续费、T+1、盘口成交、停牌或异常折溢价等问题，正式使用前需要补更严格的交易约束。

`zzshare` 说明：当前脚本会尝试 import `zzshare`，并自动识别常见的 ETF 日线函数名，例如 `fund_etf_hist`、`etf_daily`、`get_etf_daily` 等。如果你的 `zzshare` API 函数名或字段不同，把函数签名贴出来即可精确适配。
