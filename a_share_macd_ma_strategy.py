#!/usr/bin/env python3
"""
A-share ETF MACD + MA strategy using akshare data.

Rules:
- Buy 50% when price crosses above MA5 and MACD is in a low area.
- If the next trading day's close/current price is still above MA5, add to 100%.
- Sell 1/3 of current shares when price is below MA5 by the configured drawdown.
- Clear all when price falls below MA17.

This script is for research/backtesting only, not investment advice.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib
import inspect
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


try:
    import akshare as ak
except ImportError:  # pragma: no cover
    ak = None


ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
SIGNAL_DIR = ROOT / "signals"
ETF_POOL_FILE = ROOT / "etf_pool.csv"
POSITIONS_FILE = ROOT / "positions.csv"

ADAPTIVE_EXCLUDE = {"512800", "516160", "512690"}
ADAPTIVE_TREND = {"159915"}
ADAPTIVE_TREND_EXIT2 = {"512880", "512070"}


@dataclass
class StrategyConfig:
    initial_cash: float = 100_000.0
    fee_rate: float = 0.0003
    stamp_tax_rate: float = 0.0005
    lot_size: int = 100
    ma5_break_ratio: float = 0.10
    hist_turn_ratio: float = 0.003
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    trend_filter: bool = False
    ma17_exit_days: int = 1


def normalize_code(code: str) -> str:
    code = code.strip()
    if "." in code:
        return code.split(".")[0]
    return code


def ensure_akshare() -> None:
    if ak is None:
        raise RuntimeError("未安装 akshare，请先运行：pip install akshare")


def fetch_hist(
    code: str,
    start: str,
    end: str,
    asset: str = "etf",
    data_source: str = "akshare",
    adjust: str = "qfq",
    use_cache: bool = True,
    retries: int = 3,
) -> pd.DataFrame:
    ensure_akshare()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    code = normalize_code(code)
    cache_file = CACHE_DIR / f"{data_source}_{asset}_{code}_{start}_{end}_{adjust}.csv"

    if use_cache and cache_file.exists():
        df = pd.read_csv(cache_file)
    else:
        df = fetch_hist_with_retry(code, start, end, asset, data_source, adjust, retries)
        if df.empty:
            raise RuntimeError(f"{code} 没有取到行情数据")
        df.to_csv(cache_file, index=False)

    rename_map = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "日期时间": "date",
        "时间": "date",
        "代码": "code",
        "开": "open",
        "高": "high",
        "低": "low",
        "收": "close",
        "vol": "volume",
    }
    df = df.rename(columns=rename_map)
    required = ["date", "open", "high", "low", "close", "volume"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise RuntimeError(f"{code} 行情字段缺失：{missing}")

    df = df[required].copy()
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna().sort_values("date").reset_index(drop=True)
    return df


def fetch_hist_with_retry(
    code: str,
    start: str,
    end: str,
    asset: str,
    data_source: str,
    adjust: str,
    retries: int,
) -> pd.DataFrame:
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            if data_source == "sina":
                start_date = dt.datetime.strptime(start, "%Y%m%d").date()
                end_date = dt.datetime.strptime(end, "%Y%m%d").date()
                return fetch_etf_hist_sina(code, start_date, end_date)
            if data_source == "zzshare":
                return fetch_hist_zzshare(code, start, end, asset, adjust)
            if data_source != "akshare":
                raise ValueError(f"不支持的数据源：{data_source}")
            if asset == "etf":
                return ak.fund_etf_hist_em(
                    symbol=code,
                    period="daily",
                    start_date=start,
                    end_date=end,
                    adjust=adjust,
                )
            if asset == "stock":
                return ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=start,
                    end_date=end,
                    adjust=adjust,
                )
            raise ValueError(f"不支持的资产类型：{asset}")
        except Exception as exc:
            last_exc = exc
            time.sleep(min(2 * attempt, 6))

    start_date = dt.datetime.strptime(start, "%Y%m%d").date()
    end_date = dt.datetime.strptime(end, "%Y%m%d").date()
    if data_source == "akshare" and asset == "etf":
        try:
            return fetch_etf_hist_sina(code, start_date, end_date)
        except Exception as exc:
            last_exc = exc

    if start_date.year == end_date.year:
        raise last_exc or RuntimeError("行情接口请求失败")

    frames = []
    for year in range(start_date.year, end_date.year + 1):
        chunk_start = max(start_date, dt.date(year, 1, 1)).strftime("%Y%m%d")
        chunk_end = min(end_date, dt.date(year, 12, 31)).strftime("%Y%m%d")
        frames.append(fetch_hist_with_retry(code, chunk_start, chunk_end, asset, data_source, adjust, retries))
        time.sleep(0.8)
    return pd.concat(frames, ignore_index=True).drop_duplicates(subset=["日期"])


def fetch_hist_zzshare(code: str, start: str, end: str, asset: str, adjust: str) -> pd.DataFrame:
    try:
        zz = importlib.import_module("zzshare")
    except ImportError as exc:
        raise RuntimeError("未安装 zzshare，或当前环境无法 import zzshare") from exc

    candidates = [
        "fund_etf_hist_em",
        "fund_etf_hist",
        "etf_hist",
        "etf_daily",
        "get_etf_daily",
        "stock_zh_a_hist",
        "stock_hist",
        "get_kline",
    ]
    funcs = [(name, getattr(zz, name)) for name in candidates if hasattr(zz, name)]
    if not funcs:
        raise RuntimeError(
            "已找到 zzshare 包，但没有识别到 ETF 日线函数；"
            "请提供 zzshare 的日线函数名和返回字段，我可以把适配器补准确。"
        )

    errors: list[str] = []
    for name, func in funcs:
        try:
            kwargs = build_data_kwargs(func, code, start, end, adjust)
            df = func(**kwargs)
            if isinstance(df, pd.DataFrame) and not df.empty:
                return df
            errors.append(f"{name}: 返回空数据")
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise RuntimeError("zzshare 数据源尝试失败：" + " | ".join(errors[:5]))


def build_data_kwargs(func: object, code: str, start: str, end: str, adjust: str) -> dict[str, object]:
    sig = inspect.signature(func)
    kwargs: dict[str, object] = {}
    param_map = {
        "symbol": code,
        "code": code,
        "ts_code": code,
        "start_date": start,
        "start": start,
        "begin": start,
        "end_date": end,
        "end": end,
        "period": "daily",
        "freq": "daily",
        "adjust": adjust,
    }
    for key, value in param_map.items():
        if key in sig.parameters:
            kwargs[key] = value
    return kwargs


def fetch_etf_hist_sina(code: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    prefix = "sh" if code.startswith("5") else "sz"
    df = ak.fund_etf_hist_sina(symbol=f"{prefix}{code}")
    if df.empty:
        return df
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"].dt.date >= start) & (df["date"].dt.date <= end)]
    return df.rename(
        columns={
            "date": "日期",
            "open": "开盘",
            "close": "收盘",
            "high": "最高",
            "low": "最低",
            "volume": "成交量",
            "amount": "成交额",
        }
    )


def fetch_realtime_price(
    code: str,
    asset: str,
    spot: pd.DataFrame | None = None,
) -> dict[str, float | str] | None:
    ensure_akshare()
    code = normalize_code(code)
    if spot is None:
        if asset == "etf":
            spot = ak.fund_etf_spot_em()
        elif asset == "stock":
            spot = ak.stock_zh_a_spot_em()
        else:
            raise ValueError(f"不支持的资产类型：{asset}")
    row = spot[spot["代码"].astype(str).str.zfill(6) == code.zfill(6)]
    if row.empty:
        return None
    row = row.iloc[0]
    return {
        "date": dt.date.today().isoformat(),
        "open": float(row.get("今开", row.get("最新价"))),
        "high": float(row.get("最高", row.get("最新价"))),
        "low": float(row.get("最低", row.get("最新价"))),
        "close": float(row["最新价"]),
        "volume": float(row.get("成交量", 0) or 0),
    }


def append_realtime_bar(
    hist: pd.DataFrame,
    code: str,
    asset: str,
    spot: pd.DataFrame | None = None,
) -> pd.DataFrame:
    tick = fetch_realtime_price(code, asset, spot)
    if tick is None:
        return hist
    today = pd.to_datetime(tick["date"])
    row = pd.DataFrame([{**tick, "date": today}])
    if not hist.empty and hist.iloc[-1]["date"].date() == today.date():
        hist = pd.concat([hist.iloc[:-1], row], ignore_index=True)
    else:
        hist = pd.concat([hist, row], ignore_index=True)
    return hist.sort_values("date").reset_index(drop=True)


def add_indicators(df: pd.DataFrame, cfg: StrategyConfig | None = None) -> pd.DataFrame:
    cfg = cfg or StrategyConfig()
    df = df.copy()
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma17"] = df["close"].rolling(17).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    ema_fast = df["close"].ewm(span=cfg.macd_fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=cfg.macd_slow, adjust=False).mean()
    df["diff"] = ema_fast - ema_slow
    df["dea"] = df["diff"].ewm(span=cfg.macd_signal, adjust=False).mean()
    df["macd_hist"] = (df["diff"] - df["dea"]) * 2
    return df


def is_low_macd(row: pd.Series, prev: pd.Series, cfg: StrategyConfig) -> bool:
    below_zero = row["diff"] < 0 and row["dea"] < 0
    hist_turning = (
        row["macd_hist"] < 0
        and row["macd_hist"] > prev["macd_hist"]
        and abs(row["macd_hist"]) / row["close"] <= cfg.hist_turn_ratio
    )
    return bool(below_zero or hist_turning)


def cross_above_ma5(row: pd.Series, prev: pd.Series) -> bool:
    return bool(prev["close"] <= prev["ma5"] and row["close"] > row["ma5"])


def passes_trend_filter(row: pd.Series, cfg: StrategyConfig) -> bool:
    if not cfg.trend_filter:
        return True
    if pd.isna(row["ma20"]) or pd.isna(row["ma60"]):
        return False
    return bool(row["close"] > row["ma20"] and row["ma20"] > row["ma60"])


def buy_shares_for_target(
    cash: float,
    shares: int,
    price: float,
    target_ratio: float,
    cfg: StrategyConfig,
) -> tuple[int, float]:
    equity = cash + shares * price
    target_value = equity * target_ratio
    current_value = shares * price
    value_to_buy = max(0.0, target_value - current_value)
    raw_shares = int(value_to_buy / (price * (1 + cfg.fee_rate)))
    buy_shares = raw_shares // cfg.lot_size * cfg.lot_size
    if buy_shares <= 0:
        return 0, cash
    cost = buy_shares * price * (1 + cfg.fee_rate)
    if cost > cash:
        buy_shares = int(cash / (price * (1 + cfg.fee_rate))) // cfg.lot_size * cfg.lot_size
        cost = buy_shares * price * (1 + cfg.fee_rate)
    return buy_shares, cash - cost


def sell_shares(
    cash: float,
    sell_count: int,
    price: float,
    cfg: StrategyConfig,
) -> float:
    proceeds = sell_count * price * (1 - cfg.fee_rate - cfg.stamp_tax_rate)
    return cash + proceeds


def backtest(df: pd.DataFrame, code: str, cfg: StrategyConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = add_indicators(df, cfg)
    cash = cfg.initial_cash
    shares = 0
    pending_add_date: pd.Timestamp | None = None
    below_ma17_days = 0
    trades: list[dict[str, object]] = []
    equity_rows: list[dict[str, object]] = []

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        if pd.isna(row["ma17"]) or pd.isna(prev["ma5"]) or (cfg.trend_filter and pd.isna(row["ma60"])):
            equity_rows.append(
                {
                    "date": row["date"],
                    "cash": cash,
                    "shares": shares,
                    "close": row["close"],
                    "equity": cash + shares * row["close"],
                    "position_ratio": 0.0,
                }
            )
            continue

        date = row["date"]
        price = float(row["close"])
        action = ""
        reason = ""
        below_ma17_days = below_ma17_days + 1 if shares > 0 and price < row["ma17"] else 0

        if shares > 0 and below_ma17_days >= cfg.ma17_exit_days:
            sell_count = shares
            cash = sell_shares(cash, sell_count, price, cfg)
            shares -= sell_count
            pending_add_date = None
            below_ma17_days = 0
            action = "SELL_ALL"
            reason = f"连续{cfg.ma17_exit_days}天跌破MA17清仓"
            trades.append(make_trade(code, date, action, sell_count, price, cash, shares, reason))

        elif shares > 0 and price < row["ma5"] * (1 - cfg.ma5_break_ratio):
            sell_count = max(cfg.lot_size, int(shares / 3) // cfg.lot_size * cfg.lot_size)
            sell_count = min(shares, sell_count)
            cash = sell_shares(cash, sell_count, price, cfg)
            shares -= sell_count
            action = "SELL_1_3"
            reason = f"低于MA5 {cfg.ma5_break_ratio:.0%}"
            trades.append(make_trade(code, date, action, sell_count, price, cash, shares, reason))

        if (
            shares == 0
            and passes_trend_filter(row, cfg)
            and cross_above_ma5(row, prev)
            and is_low_macd(row, prev, cfg)
        ):
            buy_count, cash = buy_shares_for_target(cash, shares, price, 0.5, cfg)
            if buy_count > 0:
                shares += buy_count
                pending_add_date = next_trade_date(df, i)
                below_ma17_days = 0
                action = "BUY_HALF"
                reason = "低位MACD且上穿MA5"
                trades.append(make_trade(code, date, action, buy_count, price, cash, shares, reason))

        elif shares > 0 and pending_add_date is not None and date == pending_add_date:
            if price > row["ma17"]:
                buy_count, cash = buy_shares_for_target(cash, shares, price, 1.0, cfg)
                if buy_count > 0:
                    shares += buy_count
                    action = "BUY_FULL"
                    reason = "次日突破MA17"
                    trades.append(make_trade(code, date, action, buy_count, price, cash, shares, reason))
            pending_add_date = None

        equity = cash + shares * price
        equity_rows.append(
            {
                "date": date,
                "cash": cash,
                "shares": shares,
                "close": price,
                "ma5": row["ma5"],
                "ma20": row["ma20"],
                "ma17": row["ma17"],
                "ma60": row["ma60"],
                "diff": row["diff"],
                "dea": row["dea"],
                "macd_hist": row["macd_hist"],
                "equity": equity,
                "position_ratio": 0 if equity <= 0 else shares * price / equity,
                "action": action,
                "reason": reason,
            }
        )

    return pd.DataFrame(trades), pd.DataFrame(equity_rows)


def next_trade_date(df: pd.DataFrame, idx: int) -> pd.Timestamp | None:
    if idx + 1 >= len(df):
        return None
    return df.iloc[idx + 1]["date"]


def make_trade(
    code: str,
    date: pd.Timestamp,
    action: str,
    shares: int,
    price: float,
    cash: float,
    holding: int,
    reason: str,
) -> dict[str, object]:
    return {
        "code": code,
        "date": date.date().isoformat(),
        "action": action,
        "shares": shares,
        "price": round(price, 3),
        "cash_after": round(cash, 2),
        "holding_after": holding,
        "reason": reason,
    }


def signal_for_latest(df: pd.DataFrame, code: str, cfg: StrategyConfig) -> dict[str, object]:
    df = add_indicators(df, cfg)
    if len(df) < 30:
        raise RuntimeError("数据不足，至少需要 30 根日线")
    row = df.iloc[-1]
    prev = df.iloc[-2]
    signals: list[str] = []
    if passes_trend_filter(row, cfg) and cross_above_ma5(row, prev) and is_low_macd(row, prev, cfg):
        signals.append("BUY_HALF: 低位MACD且价格上穿MA5")
    if row["close"] > row["ma17"]:
        signals.append("ADD_OK: 若昨日已半仓，今日突破MA17，可加到满仓")
    if row["close"] < row["ma5"] * (1 - cfg.ma5_break_ratio):
        signals.append(f"SELL_1_3: 低于MA5 {cfg.ma5_break_ratio:.0%}")
    if row["close"] < row["ma17"]:
        signals.append("SELL_ALL: 跌破MA17")
    return {
        "code": code,
        "date": row["date"].date().isoformat(),
        "close_or_realtime": round(float(row["close"]), 3),
        "ma5": round(float(row["ma5"]), 3),
        "ma17": round(float(row["ma17"]), 3),
        "diff": round(float(row["diff"]), 4),
        "dea": round(float(row["dea"]), 4),
        "macd_hist": round(float(row["macd_hist"]), 4),
        "signals": " | ".join(signals) if signals else "NO_ACTION",
    }


def latest_indicator_rows(df: pd.DataFrame, cfg: StrategyConfig) -> tuple[pd.Series, pd.Series]:
    df = add_indicators(df, cfg)
    if len(df) < 30:
        raise RuntimeError("数据不足，至少需要 30 根日线")
    return df.iloc[-1], df.iloc[-2]


def load_positions(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    df = pd.read_csv(path, dtype={"code": str})
    if df.empty:
        return {}
    df["code"] = df["code"].astype(str).str.zfill(6)
    positions = {}
    for _, row in df.iterrows():
        code = normalize_code(str(row["code"]).zfill(6))
        positions[code] = {
            "shares": int(float(row.get("shares", 0) or 0)),
            "entry_date": str(row.get("entry_date", "") or ""),
            "position_stage": str(row.get("position_stage", "") or ""),
            "notes": str(row.get("notes", "") or ""),
        }
    return positions


def daily_action_for_code(
    code: str,
    row: pd.Series,
    prev: pd.Series,
    cfg: StrategyConfig,
    position: dict[str, object] | None,
) -> dict[str, object]:
    shares = int(position.get("shares", 0)) if position else 0
    stage = str(position.get("position_stage", "")) if position else ""
    action = "HOLD"
    reason = "无动作"
    priority = 0

    if shares > 0 and row["close"] < row["ma17"]:
        action = "SELL_ALL"
        reason = "跌破MA17，清仓"
        priority = 100
    elif shares > 0 and row["close"] < row["ma5"] * (1 - cfg.ma5_break_ratio):
        action = "SELL_1_3"
        reason = f"低于MA5 {cfg.ma5_break_ratio:.0%}，卖出1/3"
        priority = 90
    elif shares > 0 and stage.lower() in {"half", "buy_half", "50", "0.5"} and row["close"] > row["ma17"]:
        action = "BUY_FULL"
        reason = "已半仓且突破MA17，加到目标满仓"
        priority = 80
    elif shares == 0 and passes_trend_filter(row, cfg) and cross_above_ma5(row, prev) and is_low_macd(row, prev, cfg):
        action = "BUY_HALF"
        reason = "低位MACD且上穿MA5，买入目标半仓"
        priority = 70

    return {
        "code": code,
        "date": row["date"].date().isoformat(),
        "action": action,
        "priority": priority,
        "shares": shares,
        "position_stage": stage,
        "price": round(float(row["close"]), 3),
        "ma5": round(float(row["ma5"]), 3),
        "ma17": round(float(row["ma17"]), 3),
        "diff": round(float(row["diff"]), 4),
        "dea": round(float(row["dea"]), 4),
        "macd_hist": round(float(row["macd_hist"]), 4),
        "reason": reason,
    }


def calc_summary(equity: pd.DataFrame, trades: pd.DataFrame) -> dict[str, object]:
    if equity.empty:
        return {}
    start_equity = float(equity.iloc[0]["equity"])
    end_equity = float(equity.iloc[-1]["equity"])
    total_return = end_equity / start_equity - 1
    curve = equity["equity"].astype(float)
    max_drawdown = (curve / curve.cummax() - 1).min()
    return {
        "start": equity.iloc[0]["date"].date().isoformat(),
        "end": equity.iloc[-1]["date"].date().isoformat(),
        "start_equity": round(start_equity, 2),
        "end_equity": round(end_equity, 2),
        "total_return": f"{total_return:.2%}",
        "max_drawdown": f"{max_drawdown:.2%}",
        "trade_count": int(len(trades)),
    }


def load_codes_file(path: Path, enabled_only: bool = False) -> list[str]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if "code" not in (reader.fieldnames or []):
            raise RuntimeError(f"{path} 缺少 code 列")
        codes = []
        for row in reader:
            if not row.get("code"):
                continue
            if enabled_only and row.get("enabled", "1").strip() not in {"1", "true", "TRUE", "yes", "Y"}:
                continue
            codes.append(normalize_code(row["code"]))
        return codes


def parse_codes(raw: str, codes_file: str | None = None) -> list[str]:
    codes: list[str] = []
    if codes_file:
        codes.extend(load_codes_file(Path(codes_file).expanduser()))

    raw = raw.strip()
    if raw.lower() == "core":
        codes.extend(load_codes_file(ETF_POOL_FILE, enabled_only=True))
    elif raw.lower() == "all":
        codes.extend(load_codes_file(ETF_POOL_FILE, enabled_only=False))
    elif raw:
        codes.extend(normalize_code(item) for item in raw.replace("，", ",").split(",") if item.strip())

    unique_codes = list(dict.fromkeys(codes))
    if not unique_codes:
        raise RuntimeError("请通过 --codes 或 --codes-file 指定至少一个ETF代码")
    return unique_codes


def run_backtest(args: argparse.Namespace) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    summaries = []
    for code in parse_codes(args.codes, args.codes_file):
        try:
            profile = profile_for_variant(args.variant, code)
            if profile == "skip":
                summary = {"variant": args.variant, "profile": profile, "code": code, "status": "skipped"}
                summaries.append(summary)
                print(pd.Series(summary).to_string())
                print()
                continue
            cfg = config_for_profile(args, profile)
            df = fetch_hist(
                code,
                args.start,
                args.end,
                args.asset,
                args.data_source,
                args.adjust,
                not args.no_cache,
                args.retries,
            )
            trades, equity = backtest(df, code, cfg)
            prefix = f"{code}_{args.variant}" if args.variant != "original" else code
            trades_file = OUTPUT_DIR / f"{prefix}_trades.csv"
            equity_file = OUTPUT_DIR / f"{prefix}_equity.csv"
            trades.to_csv(trades_file, index=False)
            equity.to_csv(equity_file, index=False)
            summary = {"variant": args.variant, "profile": profile, "code": code, **calc_summary(equity, trades)}
            summaries.append(summary)
            print(pd.Series(summary).to_string())
            print(f"trades: {trades_file}")
            print(f"equity: {equity_file}")
            print()
        except Exception as exc:
            summary = {"variant": args.variant, "code": code, "error": str(exc)}
            summaries.append(summary)
            print(pd.Series(summary).to_string())
            print()

    summary_file = OUTPUT_DIR / (f"summary_{args.variant}.csv" if args.variant != "original" else "summary.csv")
    pd.DataFrame(summaries).to_csv(summary_file, index=False)


def run_signal(args: argparse.Namespace) -> None:
    rows = []
    for code in parse_codes(args.codes, args.codes_file):
        try:
            profile = profile_for_variant(args.variant, code)
            if profile == "skip":
                rows.append({"code": code, "signals": "SKIP: adaptive剔除"})
                continue
            cfg = config_for_profile(args, profile)
            df = fetch_hist(
                code,
                args.start,
                args.end,
                args.asset,
                args.data_source,
                args.adjust,
                not args.no_cache,
                args.retries,
            )
            if args.realtime:
                df = append_realtime_bar(df, code, args.asset)
            rows.append(signal_for_latest(df, code, cfg))
        except Exception as exc:
            rows.append({"code": code, "signals": f"ERROR: {exc}"})
    print(pd.DataFrame(rows).to_string(index=False))


def run_daily(args: argparse.Namespace) -> None:
    SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    positions = load_positions(Path(args.positions).expanduser())
    rows = []
    realtime_spot = fetch_realtime_spot(args.asset) if args.realtime else None
    for code in parse_codes(args.codes, args.codes_file):
        try:
            profile = profile_for_variant(args.variant, code)
            if profile == "skip":
                rows.append({"code": code, "action": "SKIP", "reason": "adaptive剔除"})
                continue
            cfg = config_for_profile(args, profile)
            df = fetch_hist(
                code,
                args.start,
                args.end,
                args.asset,
                args.data_source,
                args.adjust,
                not args.no_cache,
                args.retries,
            )
            if args.realtime:
                df = append_realtime_bar(df, code, args.asset, realtime_spot)
            row, prev = latest_indicator_rows(df, cfg)
            rows.append(daily_action_for_code(code, row, prev, cfg, positions.get(code)))
        except Exception as exc:
            rows.append({"code": code, "action": "ERROR", "reason": str(exc)})

    result = pd.DataFrame(rows)
    if "priority" in result.columns:
        result = result.sort_values(["priority", "code"], ascending=[False, True])
    date_label = dt.date.today().strftime("%Y%m%d")
    if "date" in result.columns and result["date"].notna().any():
        date_label = str(result["date"].dropna().iloc[0]).replace("-", "")
    output_file = SIGNAL_DIR / f"{date_label}_daily_signals.csv"
    result.to_csv(output_file, index=False)
    print(result.to_string(index=False))
    print(f"\nsaved: {output_file}")


def fetch_realtime_spot(asset: str) -> pd.DataFrame:
    ensure_akshare()
    if asset == "etf":
        return ak.fund_etf_spot_em()
    if asset == "stock":
        return ak.stock_zh_a_spot_em()
    raise ValueError(f"不支持的资产类型：{asset}")


def run_compare(args: argparse.Namespace) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    variants = ["original", "trend", "trend_exit2", "adaptive"]
    all_rows = []
    codes = parse_codes(args.codes, args.codes_file)
    data_cache: dict[str, pd.DataFrame] = {}

    for code in codes:
        try:
            data_cache[code] = fetch_hist(
                code,
                args.start,
                args.end,
                args.asset,
                args.data_source,
                args.adjust,
                not args.no_cache,
                args.retries,
            )
        except Exception as exc:
            for variant in variants:
                all_rows.append({"variant": variant, "code": code, "error": str(exc)})

    for variant in variants:
        for code, df in data_cache.items():
            profile = profile_for_variant(variant, code)
            if profile == "skip":
                all_rows.append({"variant": variant, "profile": profile, "code": code, "status": "skipped"})
                continue
            cfg = config_for_profile(args, profile)
            trades, equity = backtest(df, code, cfg)
            summary = {"variant": variant, "profile": profile, "code": code, **calc_summary(equity, trades)}
            all_rows.append(summary)

    result = pd.DataFrame(all_rows)
    result.to_csv(OUTPUT_DIR / "compare_summary.csv", index=False)
    best = select_best_variants(result)
    best.to_csv(OUTPUT_DIR / "best_strategy_by_etf.csv", index=False)
    print(result.to_string(index=False))
    print()
    print("BEST_STRATEGY_BY_ETF")
    print(best.to_string(index=False))


def run_walk_forward(args: argparse.Namespace) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    train_start = args.train_start or "20240101"
    train_end = args.train_end or "20241231"
    test_start = args.test_start or "20260101"
    test_end = args.test_end or args.end
    variants = ["original", "trend", "trend_exit2"]
    rows = []
    codes = parse_codes(args.codes, args.codes_file)

    for code in codes:
        try:
            train_df = fetch_hist(
                code,
                train_start,
                train_end,
                args.asset,
                args.data_source,
                args.adjust,
                not args.no_cache,
                args.retries,
            )
            test_df = fetch_hist(
                code,
                test_start,
                test_end,
                args.asset,
                args.data_source,
                args.adjust,
                not args.no_cache,
                args.retries,
            )
            train_results = []
            for variant in variants:
                cfg = config_for_profile(args, variant)
                trades, equity = backtest(train_df, code, cfg)
                train_results.append({"variant": variant, "code": code, **calc_summary(equity, trades)})
            best = select_best_variants(pd.DataFrame(train_results))
            if best.empty:
                rows.append({"code": code, "error": "训练期没有可用策略"})
                continue
            if float(best.iloc[0]["risk_score"]) < 0:
                rows.append(
                    {
                        "code": code,
                        "selected_variant": "cash",
                        "train_return": best.iloc[0]["total_return"],
                        "train_drawdown": best.iloc[0]["max_drawdown"],
                        "train_trades": best.iloc[0]["trade_count"],
                        "test_start": test_start,
                        "test_end": test_end,
                        "test_return": "0.00%",
                        "test_drawdown": "0.00%",
                        "test_trades": 0,
                        "test_end_equity": args.cash,
                    }
                )
                continue
            best_variant = str(best.iloc[0]["variant"])
            cfg = config_for_profile(args, best_variant)
            test_trades, test_equity = backtest(test_df, code, cfg)
            test_summary = calc_summary(test_equity, test_trades)
            train_summary = train_results[
                [row["variant"] for row in train_results].index(best_variant)
            ]
            rows.append(
                {
                    "code": code,
                    "selected_variant": best_variant,
                    "train_return": train_summary["total_return"],
                    "train_drawdown": train_summary["max_drawdown"],
                    "train_trades": train_summary["trade_count"],
                    "test_start": test_summary.get("start"),
                    "test_end": test_summary.get("end"),
                    "test_return": test_summary.get("total_return"),
                    "test_drawdown": test_summary.get("max_drawdown"),
                    "test_trades": test_summary.get("trade_count"),
                    "test_end_equity": test_summary.get("end_equity"),
                }
            )
        except Exception as exc:
            rows.append({"code": code, "error": str(exc)})

    result = pd.DataFrame(rows)
    result.to_csv(OUTPUT_DIR / "walk_forward_2024_train_2026_test.csv", index=False)
    print(result.to_string(index=False))


def select_best_variants(result: pd.DataFrame) -> pd.DataFrame:
    if result.empty or "total_return" not in result.columns:
        return pd.DataFrame()
    df = result.copy()
    if "status" in df.columns:
        df = df[df["status"].fillna("") != "skipped"].copy()
    df = df.dropna(subset=["total_return", "max_drawdown"])
    if df.empty:
        return pd.DataFrame()
    df["return_num"] = df["total_return"].astype(str).str.rstrip("%").astype(float)
    df["drawdown_num"] = df["max_drawdown"].astype(str).str.rstrip("%").astype(float).abs()
    df["risk_score"] = df["return_num"] / df["drawdown_num"].replace(0, 0.01)
    df.loc[df["return_num"] <= 0, "risk_score"] = -999
    df = df.sort_values(["code", "risk_score", "return_num"], ascending=[True, False, False])
    best = df.groupby("code", as_index=False).head(1).copy()
    cols = [
        "code",
        "variant",
        "profile",
        "total_return",
        "max_drawdown",
        "risk_score",
        "trade_count",
        "end_equity",
    ]
    best["risk_score"] = best["risk_score"].round(3)
    return best[[col for col in cols if col in best.columns]].sort_values("risk_score", ascending=False)


def build_config(args: argparse.Namespace) -> StrategyConfig:
    return config_for_profile(args, profile_for_variant(args.variant, ""))


def profile_for_variant(variant: str, code: str) -> str:
    code = normalize_code(code) if code else code
    if variant != "adaptive":
        return variant
    if code in ADAPTIVE_EXCLUDE:
        return "skip"
    if code in ADAPTIVE_TREND:
        return "trend"
    if code in ADAPTIVE_TREND_EXIT2:
        return "trend_exit2"
    return "original"


def config_for_profile(args: argparse.Namespace, profile: str) -> StrategyConfig:
    if profile == "original":
        trend_filter = False
        ma17_exit_days = 1
    elif profile == "trend":
        trend_filter = True
        ma17_exit_days = 1
    elif profile == "trend_exit2":
        trend_filter = True
        ma17_exit_days = 2
    else:
        raise ValueError(f"不支持的策略配置：{profile}")

    return StrategyConfig(
        initial_cash=args.cash,
        fee_rate=args.fee,
        stamp_tax_rate=args.stamp_tax,
        ma5_break_ratio=args.ma5_break_ratio,
        hist_turn_ratio=args.hist_turn_ratio,
        macd_fast=args.macd_fast,
        macd_slow=args.macd_slow,
        macd_signal=args.macd_signal,
        trend_filter=trend_filter,
        ma17_exit_days=ma17_exit_days,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A股ETF MACD低位 + MA5上穿策略")
    parser.add_argument("--mode", choices=["backtest", "signal", "daily", "compare", "walk_forward"], default="backtest")
    parser.add_argument(
        "--variant",
        choices=["original", "trend", "trend_exit2", "adaptive"],
        default="original",
        help="策略版本：original原始；trend加趋势过滤；trend_exit2再加连续2天跌破MA17清仓；adaptive按ETF类型选择",
    )
    parser.add_argument("--asset", choices=["etf", "stock"], default="etf", help="资产类型，默认ETF")
    parser.add_argument(
        "--data-source",
        choices=["akshare", "sina", "zzshare"],
        default="akshare",
        help="历史行情数据源；akshare 默认带 sina 兜底",
    )
    parser.add_argument("--codes", default="", help="ETF代码，多个用逗号分隔；core使用启用池；all使用全量池")
    parser.add_argument("--codes-file", help="ETF池CSV文件，必须包含 code 列")
    parser.add_argument("--positions", default=str(POSITIONS_FILE), help="daily模式持仓CSV文件")
    parser.add_argument("--start", default="20200101")
    parser.add_argument("--end", default=dt.date.today().strftime("%Y%m%d"))
    parser.add_argument("--adjust", choices=["qfq", "hfq", ""], default="qfq", help="复权方式")
    parser.add_argument("--cash", type=float, default=100_000.0)
    parser.add_argument("--fee", type=float, default=0.0003)
    parser.add_argument("--stamp-tax", type=float, default=0.0)
    parser.add_argument("--ma5-break-ratio", type=float, default=0.10, help="低于MA5多少比例卖1/3")
    parser.add_argument("--hist-turn-ratio", type=float, default=0.003, help="MACD柱体即将转正阈值")
    parser.add_argument("--macd-fast", type=int, default=12, help="MACD快线EMA周期")
    parser.add_argument("--macd-slow", type=int, default=26, help="MACD慢线EMA周期")
    parser.add_argument("--macd-signal", type=int, default=9, help="MACD信号线DEA周期")
    parser.add_argument("--realtime", action="store_true", help="signal模式使用实时价更新最后一根K线")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--retries", type=int, default=3, help="行情接口失败重试次数")
    parser.add_argument("--train-start", help="walk_forward训练开始日期，例如 20240101")
    parser.add_argument("--train-end", help="walk_forward训练结束日期，例如 20241231")
    parser.add_argument("--test-start", help="walk_forward验证开始日期，例如 20260101")
    parser.add_argument("--test-end", help="walk_forward验证结束日期，例如 20260621")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.mode == "backtest":
            run_backtest(args)
        elif args.mode == "signal":
            run_signal(args)
        elif args.mode == "daily":
            run_daily(args)
        elif args.mode == "walk_forward":
            run_walk_forward(args)
        else:
            run_compare(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
