#!/usr/bin/env python3
"""
A-share stock breakout signal scanner.

This script is for research and manual signal checking only. It does not place
orders. The default logic is intentionally stricter than the ETF strategy:

- filter out ST / delisting-risk names and low-liquidity stocks
- require a close/newest price breakout to a recent high
- require MA5 > MA10 > MA20 > MA60
- require volume expansion and MACD confirmation
- rank candidates by volume strength, breakout strength, momentum and liquidity
"""

from __future__ import annotations

import argparse
import datetime as dt
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
SIGNAL_DIR = ROOT / "signals"
POSITIONS_FILE = ROOT / "stock_positions.csv"


@dataclass
class StockBreakoutConfig:
    start: str = "20250101"
    end: str = dt.date.today().strftime("%Y%m%d")
    adjust: str = "qfq"
    breakout_window: int = 60
    volume_window: int = 5
    volume_ratio: float = 1.5
    min_amount: float = 100_000_000.0
    min_price: float = 5.0
    max_price: float = 120.0
    max_daily_pct: float = 9.8
    max_scan: int = 300
    top: int = 30
    fee_rate: float = 0.0003
    stop_loss: float = 0.08
    trailing_stop: float = 0.12
    use_cache: bool = True
    realtime: bool = True
    retries: int = 3
    initial_cash: float = 100_000.0


def ensure_akshare() -> None:
    if ak is None:
        raise RuntimeError("未安装 akshare，请先运行：pip install akshare")


def normalize_code(code: str) -> str:
    code = str(code).strip()
    if "." in code:
        return code.split(".")[0]
    return code.zfill(6)


def parse_number(value: object, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def first_value(row: pd.Series, names: list[str], default: object = None) -> object:
    for name in names:
        if name in row and not pd.isna(row[name]):
            return row[name]
    return default


def load_positions(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    df = pd.read_csv(path, dtype={"code": str})
    positions: dict[str, dict[str, object]] = {}
    for _, row in df.iterrows():
        code = normalize_code(str(row.get("code", "")))
        if not code:
            continue
        positions[code] = {
            "shares": int(parse_number(row.get("shares", 0), 0)),
            "entry_price": parse_number(row.get("entry_price", 0), 0),
            "peak_price": parse_number(row.get("peak_price", 0), 0),
            "entry_date": str(row.get("entry_date", "") or ""),
            "notes": str(row.get("notes", "") or ""),
        }
    return positions


def stock_prefix(code: str) -> str:
    if code.startswith("6"):
        return "sh"
    return "sz"


def is_supported_a_share(code: str) -> bool:
    return code.startswith(("00", "001", "002", "003", "30", "60", "688"))


def fetch_spot() -> pd.DataFrame:
    ensure_akshare()
    df = ak.stock_zh_a_spot_em()
    if df.empty:
        raise RuntimeError("没有取到 A 股实时行情")
    df = df.copy()
    df["代码"] = df["代码"].astype(str).str.zfill(6)
    return df


def filter_spot_universe(spot: pd.DataFrame, cfg: StockBreakoutConfig) -> pd.DataFrame:
    rows = []
    for _, row in spot.iterrows():
        code = normalize_code(row.get("代码", ""))
        name = str(row.get("名称", "") or "")
        price = parse_number(first_value(row, ["最新价", "收盘", "close"]), 0)
        amount = parse_number(first_value(row, ["成交额", "amount"]), 0)
        pct = parse_number(first_value(row, ["涨跌幅", "changepercent", "pct_chg"]), 0)
        if not is_supported_a_share(code):
            continue
        if "ST" in name.upper() or "退" in name:
            continue
        if price < cfg.min_price or price > cfg.max_price:
            continue
        if amount < cfg.min_amount:
            continue
        if pct >= cfg.max_daily_pct:
            continue
        rows.append({"code": code, "name": name, "price": price, "amount": amount, "pct": pct})
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    return result.sort_values(["amount", "pct"], ascending=[False, False]).head(cfg.max_scan).reset_index(drop=True)


def fetch_hist(code: str, cfg: StockBreakoutConfig) -> pd.DataFrame:
    ensure_akshare()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    code = normalize_code(code)
    cache_file = CACHE_DIR / f"stock_{code}_{cfg.start}_{cfg.end}_{cfg.adjust}.csv"
    if cfg.use_cache and cache_file.exists():
        df = pd.read_csv(cache_file)
    else:
        last_exc: Exception | None = None
        for attempt in range(1, cfg.retries + 1):
            try:
                df = ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=cfg.start,
                    end_date=cfg.end,
                    adjust=cfg.adjust,
                )
                if df.empty:
                    raise RuntimeError("empty history")
                df.to_csv(cache_file, index=False)
                break
            except Exception as exc:
                last_exc = exc
                try:
                    df = ak.stock_zh_a_daily(
                        symbol=f"{stock_prefix(code)}{code}",
                        start_date=cfg.start,
                        end_date=cfg.end,
                        adjust=cfg.adjust,
                    )
                    if df.empty:
                        raise RuntimeError("empty sina history")
                    df.to_csv(cache_file, index=False)
                    break
                except Exception as fallback_exc:
                    last_exc = fallback_exc
                time.sleep(min(attempt * 1.5, 5))
        else:
            raise last_exc or RuntimeError(f"{code} 行情接口请求失败")

    rename_map = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
    }
    df = df.rename(columns=rename_map)
    if "amount" not in df.columns and {"close", "volume"}.issubset(df.columns):
        df["amount"] = pd.to_numeric(df["close"], errors="coerce") * pd.to_numeric(df["volume"], errors="coerce")
    required = ["date", "open", "high", "low", "close", "volume", "amount"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise RuntimeError(f"{code} 行情字段缺失：{missing}")
    df = df[required].copy()
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna().sort_values("date").reset_index(drop=True)


def append_realtime_bar(hist: pd.DataFrame, spot_row: pd.Series | None) -> pd.DataFrame:
    if spot_row is None:
        return hist
    price = parse_number(first_value(spot_row, ["最新价", "close"]), 0)
    if price <= 0:
        return hist
    today = pd.to_datetime(dt.date.today())
    row = pd.DataFrame(
        [
            {
                "date": today,
                "open": parse_number(first_value(spot_row, ["今开", "open"]), price),
                "high": parse_number(first_value(spot_row, ["最高", "high"]), price),
                "low": parse_number(first_value(spot_row, ["最低", "low"]), price),
                "close": price,
                "volume": parse_number(first_value(spot_row, ["成交量", "volume"]), 0),
                "amount": parse_number(first_value(spot_row, ["成交额", "amount"]), 0),
            }
        ]
    )
    if not hist.empty and hist.iloc[-1]["date"].date() == today.date():
        hist = pd.concat([hist.iloc[:-1], row], ignore_index=True)
    else:
        hist = pd.concat([hist, row], ignore_index=True)
    return hist.sort_values("date").reset_index(drop=True)


def add_indicators(df: pd.DataFrame, cfg: StockBreakoutConfig) -> pd.DataFrame:
    df = df.copy()
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma10"] = df["close"].rolling(10).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    df["high_n"] = df["close"].rolling(cfg.breakout_window).max()
    df["volume_ma"] = df["volume"].rolling(cfg.volume_window).mean()
    df["amount_ma20"] = df["amount"].rolling(20).mean()
    ema_fast = df["close"].ewm(span=12, adjust=False).mean()
    ema_slow = df["close"].ewm(span=26, adjust=False).mean()
    df["diff"] = ema_fast - ema_slow
    df["dea"] = df["diff"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = (df["diff"] - df["dea"]) * 2
    df["ret20"] = df["close"] / df["close"].shift(20) - 1
    return df


def is_buy_signal(row: pd.Series, prev: pd.Series, cfg: StockBreakoutConfig) -> bool:
    if pd.isna(row["ma60"]) or pd.isna(row["high_n"]) or pd.isna(row["volume_ma"]):
        return False
    if row["volume_ma"] <= 0:
        return False
    return bool(
        row["close"] >= row["high_n"]
        and row["ma5"] > row["ma10"] > row["ma20"] > row["ma60"]
        and row["close"] > row["ma20"]
        and row["volume"] > row["volume_ma"] * cfg.volume_ratio
        and row["amount"] >= cfg.min_amount
        and row["diff"] > row["dea"]
        and row["macd_hist"] > prev["macd_hist"]
    )


def rank_score(row: pd.Series, cfg: StockBreakoutConfig) -> float:
    volume_strength = 0.0 if row["volume_ma"] <= 0 else float(row["volume"] / row["volume_ma"])
    breakout_strength = 0.0 if row["ma20"] <= 0 else float(row["close"] / row["ma20"] - 1)
    ret20 = 0.0 if pd.isna(row["ret20"]) else float(row["ret20"])
    amount_score = min(float(row["amount"]) / max(cfg.min_amount, 1), 10.0)
    return round(volume_strength * 20 + breakout_strength * 100 + ret20 * 50 + amount_score, 3)


def action_for_holding(row: pd.Series, position: dict[str, object], cfg: StockBreakoutConfig) -> tuple[str, str]:
    entry_price = float(position.get("entry_price", 0) or 0)
    peak_price = max(float(position.get("peak_price", 0) or 0), float(row["close"]))
    if entry_price > 0 and row["close"] <= entry_price * (1 - cfg.stop_loss):
        return "SELL_ALL", f"跌破成本止损 {cfg.stop_loss:.0%}"
    if peak_price > 0 and row["close"] <= peak_price * (1 - cfg.trailing_stop):
        return "SELL_ALL", f"从高点回撤 {cfg.trailing_stop:.0%}"
    if row["close"] < row["ma20"]:
        return "SELL_ALL", "跌破MA20"
    if row["close"] < row["ma10"]:
        return "REDUCE", "跌破MA10，减仓或收紧止损"
    return "HOLD", "持仓未触发卖出"


def sell_all(cash: float, shares: int, price: float, cfg: StockBreakoutConfig) -> float:
    return cash + shares * price * (1 - cfg.fee_rate)


def buy_full(cash: float, price: float, cfg: StockBreakoutConfig) -> tuple[int, float]:
    raw_shares = int(cash / (price * (1 + cfg.fee_rate)))
    shares = raw_shares // 100 * 100
    if shares <= 0:
        return 0, cash
    return shares, cash - shares * price * (1 + cfg.fee_rate)


def backtest_code(code: str, cfg: StockBreakoutConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    hist = fetch_hist(code, cfg)
    df = add_indicators(hist, cfg)
    cash = cfg.initial_cash
    shares = 0
    entry_price = 0.0
    peak_price = 0.0
    trades: list[dict[str, object]] = []
    equity_rows: list[dict[str, object]] = []

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        if pd.isna(row["ma60"]) or pd.isna(row["high_n"]) or pd.isna(row["volume_ma"]):
            continue

        date = row["date"]
        price = float(row["close"])
        action = ""
        reason = ""

        if shares > 0:
            peak_price = max(peak_price, price)
            position = {"entry_price": entry_price, "peak_price": peak_price, "shares": shares}
            sell_action, sell_reason = action_for_holding(row, position, cfg)
            if sell_action == "SELL_ALL":
                cash = sell_all(cash, shares, price, cfg)
                trades.append(make_trade(code, date, "SELL_ALL", shares, price, cash, 0, sell_reason))
                shares = 0
                entry_price = 0.0
                peak_price = 0.0
                action = "SELL_ALL"
                reason = sell_reason

        if shares == 0 and is_buy_signal(row, prev, cfg):
            buy_count, cash = buy_full(cash, price, cfg)
            if buy_count > 0:
                shares = buy_count
                entry_price = price
                peak_price = price
                action = "BUY"
                reason = f"{cfg.breakout_window}日新高 + 均线多头 + 放量突破"
                trades.append(make_trade(code, date, "BUY", buy_count, price, cash, shares, reason))

        equity = cash + shares * price
        equity_rows.append(
            {
                "date": date,
                "cash": cash,
                "shares": shares,
                "close": price,
                "equity": equity,
                "ma10": row["ma10"],
                "ma20": row["ma20"],
                "ma60": row["ma60"],
                "volume": row["volume"],
                "volume_ma": row["volume_ma"],
                "action": action,
                "reason": reason,
            }
        )
    return pd.DataFrame(trades), pd.DataFrame(equity_rows)


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


def calc_summary(code: str, equity: pd.DataFrame, trades: pd.DataFrame) -> dict[str, object]:
    if equity.empty:
        return {"code": code, "error": "没有可用权益曲线"}
    curve = equity["equity"].astype(float)
    start_equity = float(curve.iloc[0])
    end_equity = float(curve.iloc[-1])
    max_drawdown = float((curve / curve.cummax() - 1).min())
    return {
        "code": code,
        "start": equity.iloc[0]["date"].date().isoformat(),
        "end": equity.iloc[-1]["date"].date().isoformat(),
        "start_equity": round(start_equity, 2),
        "end_equity": round(end_equity, 2),
        "total_return": f"{end_equity / start_equity - 1:.2%}",
        "max_drawdown": f"{max_drawdown:.2%}",
        "trade_count": int(len(trades)),
    }


def analyze_code(
    code: str,
    name: str,
    spot_row: pd.Series | None,
    cfg: StockBreakoutConfig,
    position: dict[str, object] | None = None,
) -> dict[str, object]:
    hist = fetch_hist(code, cfg)
    if cfg.realtime:
        hist = append_realtime_bar(hist, spot_row)
    df = add_indicators(hist, cfg)
    if len(df) < max(cfg.breakout_window, 80):
        raise RuntimeError("数据不足")
    row = df.iloc[-1]
    prev = df.iloc[-2]

    volume_strength = 0.0 if row["volume_ma"] <= 0 else float(row["volume"] / row["volume_ma"])
    base = {
        "code": code,
        "name": name,
        "date": row["date"].date().isoformat(),
        "price": round(float(row["close"]), 2),
        "amount": round(float(row["amount"]), 2),
        "ma10": round(float(row["ma10"]), 2),
        "ma20": round(float(row["ma20"]), 2),
        "ma60": round(float(row["ma60"]), 2),
        "high_n": round(float(row["high_n"]), 2),
        "volume_strength": round(volume_strength, 3),
        "ret20": "" if pd.isna(row["ret20"]) else f"{float(row['ret20']):.2%}",
        "diff": round(float(row["diff"]), 4),
        "dea": round(float(row["dea"]), 4),
        "macd_hist": round(float(row["macd_hist"]), 4),
    }
    if position and int(position.get("shares", 0) or 0) > 0:
        action, reason = action_for_holding(row, position, cfg)
        return {**base, "action": action, "rank_score": 1000 if action == "SELL_ALL" else 500, "reason": reason}

    if is_buy_signal(row, prev, cfg):
        return {
            **base,
            "action": "BUY",
            "rank_score": rank_score(row, cfg),
            "reason": f"{cfg.breakout_window}日新高 + 均线多头 + 放量突破",
        }
    return {**base, "action": "WATCH", "rank_score": 0.0, "reason": "未满足突破买入"}


def run_scan(args: argparse.Namespace) -> None:
    cfg = StockBreakoutConfig(
        start=args.start,
        end=args.end,
        adjust=args.adjust,
        breakout_window=args.breakout_window,
        volume_window=args.volume_window,
        volume_ratio=args.volume_ratio,
        min_amount=args.min_amount,
        min_price=args.min_price,
        max_price=args.max_price,
        max_daily_pct=args.max_daily_pct,
        max_scan=args.max_scan,
        top=args.top,
        stop_loss=args.stop_loss,
        trailing_stop=args.trailing_stop,
        use_cache=not args.no_cache,
        realtime=args.realtime,
        retries=args.retries,
    )
    SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    spot = fetch_spot() if (cfg.realtime or not args.codes) else pd.DataFrame()
    positions = load_positions(Path(args.positions).expanduser())

    if args.codes:
        universe = []
        for code in [normalize_code(item) for item in args.codes.replace("，", ",").split(",") if item.strip()]:
            row = spot[spot["代码"] == code] if "代码" in spot.columns else pd.DataFrame()
            name = str(row.iloc[0].get("名称", "")) if not row.empty else ""
            amount = parse_number(row.iloc[0].get("成交额", 0), 0) if not row.empty else 0
            universe.append({"code": code, "name": name, "amount": amount})
        candidates = pd.DataFrame(universe)
    else:
        candidates = filter_spot_universe(spot, cfg)

    rows = []
    for _, item in candidates.iterrows():
        code = normalize_code(item["code"])
        spot_row_df = spot[spot["代码"] == code] if "代码" in spot.columns else pd.DataFrame()
        spot_row = None if spot_row_df.empty else spot_row_df.iloc[0]
        try:
            rows.append(analyze_code(code, str(item.get("name", "")), spot_row, cfg, positions.get(code)))
        except Exception as exc:
            if args.verbose:
                rows.append(
                    {
                        "code": code,
                        "name": str(item.get("name", "")),
                        "date": "",
                        "price": "",
                        "amount": parse_number(item.get("amount", 0), 0),
                        "action": "ERROR",
                        "rank_score": -1,
                        "reason": str(exc),
                    }
                )

    result = pd.DataFrame(rows)
    if result.empty:
        print("No candidates")
        return
    action_order = {"SELL_ALL": 4, "REDUCE": 3, "BUY": 2, "HOLD": 1, "WATCH": 0, "ERROR": -1}
    result["_action_order"] = result["action"].map(action_order).fillna(0)
    if "amount" not in result.columns:
        result["amount"] = 0.0
    result = result.sort_values(["_action_order", "rank_score", "amount"], ascending=[False, False, False]).drop(columns=["_action_order"])
    if not args.show_watch:
        result = result[result["action"].isin(["BUY", "SELL_ALL", "REDUCE", "HOLD"])]
    output = result.head(args.top if args.top > 0 else len(result))
    date_label = dt.date.today().strftime("%Y%m%d")
    output_file = SIGNAL_DIR / f"{date_label}_stock_breakout_signals.csv"
    output.to_csv(output_file, index=False)
    print(output.to_string(index=False))
    print(f"\nsaved: {output_file}")


def run_backtest(args: argparse.Namespace) -> None:
    if not args.codes:
        raise RuntimeError("个股回测请先用 --codes 指定股票代码，避免全市场批量请求过慢")
    cfg = StockBreakoutConfig(
        start=args.start,
        end=args.end,
        adjust=args.adjust,
        breakout_window=args.breakout_window,
        volume_window=args.volume_window,
        volume_ratio=args.volume_ratio,
        min_amount=args.min_amount,
        min_price=args.min_price,
        max_price=args.max_price,
        max_daily_pct=args.max_daily_pct,
        top=args.top,
        stop_loss=args.stop_loss,
        trailing_stop=args.trailing_stop,
        use_cache=not args.no_cache,
        realtime=False,
        retries=args.retries,
        initial_cash=args.cash,
    )
    output_dir = ROOT / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for code in [normalize_code(item) for item in args.codes.replace("，", ",").split(",") if item.strip()]:
        try:
            trades, equity = backtest_code(code, cfg)
            prefix = f"stock_breakout_{code}_{args.start}_{args.end}"
            trades.to_csv(output_dir / f"{prefix}_trades.csv", index=False)
            equity.to_csv(output_dir / f"{prefix}_equity.csv", index=False)
            rows.append(calc_summary(code, equity, trades))
        except Exception as exc:
            rows.append({"code": code, "error": str(exc)})
    result = pd.DataFrame(rows)
    summary_file = output_dir / f"stock_breakout_summary_{args.start}_{args.end}.csv"
    result.to_csv(summary_file, index=False)
    print(result.to_string(index=False))
    print(f"\nsaved: {summary_file}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A股个股趋势突破信号扫描")
    parser.add_argument("--mode", choices=["scan", "backtest"], default="scan", help="scan生成信号；backtest对指定股票回测")
    parser.add_argument("--codes", default="", help="只扫描指定股票，多个用逗号分隔；为空则扫描全市场过滤后的候选")
    parser.add_argument("--positions", default=str(POSITIONS_FILE), help="持仓CSV，可选列：code,shares,entry_price,peak_price,entry_date,notes")
    parser.add_argument("--start", default="20250101", help="日线开始日期")
    parser.add_argument("--end", default=dt.date.today().strftime("%Y%m%d"), help="日线结束日期")
    parser.add_argument("--adjust", choices=["qfq", "hfq", ""], default="qfq", help="复权方式")
    parser.add_argument("--breakout-window", type=int, default=60, help="突破新高窗口")
    parser.add_argument("--volume-window", type=int, default=5, help="成交量均线窗口")
    parser.add_argument("--volume-ratio", type=float, default=1.5, help="放量倍数")
    parser.add_argument("--min-amount", type=float, default=100_000_000.0, help="最低当日成交额")
    parser.add_argument("--min-price", type=float, default=5.0, help="最低股价")
    parser.add_argument("--max-price", type=float, default=120.0, help="最高股价")
    parser.add_argument("--max-daily-pct", type=float, default=9.8, help="过滤接近涨停的股票，避免无法成交")
    parser.add_argument("--max-scan", type=int, default=300, help="全市场预过滤后最多拉取多少只日线")
    parser.add_argument("--top", type=int, default=30, help="输出前N条；0表示全部输出")
    parser.add_argument("--cash", type=float, default=100_000.0, help="回测初始资金")
    parser.add_argument("--stop-loss", type=float, default=0.08, help="持仓成本止损比例")
    parser.add_argument("--trailing-stop", type=float, default=0.12, help="持仓高点回撤止盈/止损比例")
    parser.add_argument("--realtime", action="store_true", help="使用实时行情更新最后一根K线")
    parser.add_argument("--no-cache", action="store_true", help="不使用本地日线缓存")
    parser.add_argument("--retries", type=int, default=3, help="行情接口失败重试次数")
    parser.add_argument("--show-watch", action="store_true", help="输出未触发买卖的观察标的")
    parser.add_argument("--verbose", action="store_true", help="输出单票错误信息")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.mode == "backtest":
            run_backtest(args)
        else:
            run_scan(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
