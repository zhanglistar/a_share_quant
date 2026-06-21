# 实盘信号日报

这个项目只生成信号，不自动下单。

## 维护持仓

每天成交后更新 `positions.csv`：

```csv
code,shares,entry_date,position_stage,notes
159819,10000,2026-06-18,half,首次半仓
588080,8000,2026-06-17,full,已加到目标仓位
```

`position_stage`：

- `half`：已按信号买入目标半仓，次日若仍站上 MA5 会提示 `BUY_FULL`。
- `full`：已加到目标满仓。

没有持仓就只保留表头。

## 每日运行

建议 14:20-14:45 运行：

```bash
cd /home/listar/code/a_share_quant
python a_share_macd_ma_strategy.py \
  --mode daily \
  --codes core \
  --data-source sina \
  --start 20260101 \
  --realtime
```

输出会保存到：

```text
signals/YYYYMMDD_daily_signals.csv
```

## 动作含义

- `BUY_HALF`：无持仓，低位 MACD 且上穿 MA5，买入目标半仓。
- `BUY_FULL`：已有半仓，今日仍站上 MA5，加到目标满仓。
- `SELL_1_3`：低于 MA5 10%，卖出当前持仓 1/3。
- `SELL_ALL`：跌破 MA17，清仓。
- `HOLD`：无动作。

实盘建议先小资金手动执行，记录成交价、滑点和是否严格按规则执行。

`--start` 只用于计算 MA 和 MACD，不需要从很早开始。一般保留最近 100-150 个交易日即可；当前是 2026 年 6 月，推荐用 `--start 20260101`。如果想让 EMA 初始值更稳定，可以用 `--start 20251001`。
