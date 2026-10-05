#!/usr/bin/env python3
"""Generate the backtest performance dashboard (single self-contained HTML) from the Java backtest logs.

Sections
    A. Setup constraints - BUY & SELL, AVOID vs TRADE, by time and day
    B. Performance analysis (incl. spread / commission / alpha / sharpe)
    C. Day-of-month analysis
    D. Setup / entry time distribution (half-hour)
    E. Round-number analysis (500 / 1000 multiples)
    F. Best SL/TP candidate per currently avoided half-hour
    G. Touch-to-entry elapsed timing win/loss analysis (30 one-minute buckets)
    H. Level event minute-of-hour win/loss analysis (60 one-minute buckets)

Usage
    python generate_performance_html.py --raw raw_data_performance.log \
        --schedule backtest_schedule.log --rules <java file with BUY/SELL_TIME_RULES> \
        --avoided-time-search avoided_time_ratio_search.log \
        --output performance_dashboard.html

--rules is the backtest_rules.txt that Main.java rewrites on every run (so section A always reflects the
code that produced the logs). An old-style Java snippet with BUY_TIME_RULES = buildSchedule(...) is
also accepted. If no rules are found, section A shows a note instead of the grid.

Conventions (edit the CONFIG block to change)
    * Main.java already deducts costs: pnl_points and net_R are NET of spread + commission.
      P&L in USD = net_R * RISK_USD.
    * Gross "Expectancy R" = cost-free R (WIN = tp/sl, LOSS = -1). "Alpha R/trade" = mean net_R.
    * Spread cost per trade (R)     = spread_points / sl_points
      Commission cost per trade (R) = commission_percent_of_risk / 100
    * "Sharpe" uses the same formula as Main.java: mean(net_R) / stdev(net_R) * sqrt(n) (sample stdev),
      so table values match the Java log; the KPI card shows the Java summary value when present.
    * Sessions (IST): ASIA 05:30-13:30, LONDON 13:30-18:00, NY 18:00-05:30; session = entry vs exit time.
    * Half-hour / weekday / day-of-month use the SETUP START time; month uses the EXIT time.
"""

from __future__ import annotations

import argparse
import html
import io
import math
import os
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ----------------------------------------------------------------- CONFIG
ACCOUNT_USD = 100_000
RISK_USD = 1_000
ROUND_STEP = 500
SESSIONS = [("ASIA", 330, 810), ("LONDON", 810, 1080)]          # minutes of day (IST); NY = rest
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS_FROM = "2023-04"
MONTHS_TO = "2026-09"
REDUCED_RISK_SLOTS = {9, 24, 34}  # 04:30, 12:00, 17:00 IST
REDUCED_RISK_MULTIPLIER = 0.1
GREEN, RED, BLUE, GREY = "#1b9e77", "#d95f02", "#3b6fb6", "#8a8f98"
TEMPLATE = "plotly_white"


# ---------------------------------------------------------------------------
# CLI / file helpers
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--raw", default="raw_data_performance.log")
    p.add_argument("--schedule", default="backtest_schedule.log")
    p.add_argument("--rules", default="backtest_rules.txt",
                   help="rules file written by Main.java on every run (default: backtest_rules.txt)")
    p.add_argument("--avoided-time-search", default="avoided_time_ratio_search.log",
                   help="separate per-avoided-time SL/TP search log")
    p.add_argument("--output", default="performance_dashboard.html")
    return p.parse_args()


def read_text(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Log file not found: {p}")
    return p.read_text(encoding="utf-8")


def section(text: str, start_marker: str, end_markers: tuple[str, ...] = ()) -> str:
    pos = text.rfind(start_marker)
    if pos < 0:
        return ""
    body = text[pos + len(start_marker):]
    end = len(body)
    for marker in end_markers:
        i = body.find(marker)
        if i >= 0:
            end = min(end, i)
    return body[:end].strip()


def latest_raw_run(raw: str) -> str:
    """Discard older appended backtest runs so every dashboard panel uses the newest run."""
    marker = "RAW PERFORMANCE DATA"
    pos = raw.rfind(marker)
    return raw[pos:] if pos >= 0 else raw


# ---------------------------------------------------------------------------
# Log parsing (unchanged behaviour from the original script)
# ---------------------------------------------------------------------------

NUMERIC_EXACT = {
    "rank", "sl_points", "tp_points", "target_points", "rr", "entries",
    "wins", "losses", "open", "skipped", "win_rate_pct", "net_points",
    "net_r", "net_R", "expectancy_points", "alpha_R_per_trade", "sharpe",
    "entry_price", "touch_level", "stop_price", "target_price", "pnl_points",
    "trade", "day", "minutes", "count", "trades", "risk_dollars",
}


def clean_number(value):
    if pd.isna(value):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "null", "na", "n/a", "—"}:
        return math.nan
    s = s.replace(",", "").replace("$", "").replace("%", "")
    s = s.replace("R", "") if re.fullmatch(r"[-+]?\d*\.?\d+R", s, re.I) else s
    try:
        return float(s)
    except ValueError:
        return value


def normalize_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    for c in df.columns:
        if c in NUMERIC_EXACT:
            df[c] = pd.to_numeric(df[c].map(clean_number), errors="coerce")
            continue
        name = str(c).lower()
        if any(token in name for token in (
            "points", "price", "entry_price", "exit_price", "risk", "alpha", "sharpe",
            "expectancy", "win_rate", "net_r", "rr", "sl_", "tp_", "target",
            "stop", "pnl", "count", "entries", "wins", "losses", "open", "skip",
        )):
            converted = pd.to_numeric(df[c].map(clean_number), errors="coerce")
            if converted.notna().sum() >= max(1, int(len(df) * 0.5)):
                df[c] = converted
    return df


def parse_csv_block(body: str, header_prefix: str | None = None) -> pd.DataFrame:
    lines = [x.strip() for x in body.splitlines() if x.strip()]
    if not lines:
        return pd.DataFrame()
    if header_prefix:
        start = next((i for i, x in enumerate(lines) if x.startswith(header_prefix)), None)
        if start is None:
            return pd.DataFrame()
        lines = lines[start:]
    if len(lines) < 2:
        return pd.DataFrame()
    valid = [lines[0]]
    expected_cols = len(lines[0].split(","))
    for line in lines[1:]:
        if line.startswith(("BUY ", "SELL ", "TRADES ", "SCHEDULE-", "GRID ", "COMBINED ", "SHORT ", "LONG ")):
            break
        if len(line.split(",")) == expected_cols:
            valid.append(line)
    if len(valid) < 2:
        return pd.DataFrame()
    try:
        df = pd.read_csv(io.StringIO("\n".join(valid)))
    except (pd.errors.ParserError, ValueError):
        return pd.DataFrame()
    return normalize_numeric_columns(df)


def parse_settings(raw: str) -> dict[str, float]:
    out: dict[str, float] = {}
    header = raw.splitlines()[1] if len(raw.splitlines()) > 1 else ""
    for key, value in re.findall(r"([A-Za-z_]+)=(-?\d+(?:\.\d+)?)", header):
        out[key] = float(value)
    return out


def parse_summary(raw: str) -> dict[str, float]:
    marker = "SCHEDULE-FILTERED STRATEGY (IST):"
    pos = raw.rfind(marker)
    if pos < 0:
        return {}
    lines = raw[pos + len(marker):].splitlines()
    summary = next((x.strip() for x in lines if x.strip() and "=" in x), "")
    out: dict[str, float] = {}
    for key, value in re.findall(r"([A-Za-z_]+)=(-?\d+(?:\.\d+)?)", summary):
        out[key] = float(value)
    return out


def parse_trades(raw: str) -> pd.DataFrame:
    body = section(raw, "TRADES TAKEN (IST):", ("SCHEDULE-FILTERED STRATEGY (IST):",))
    df = parse_csv_block(body, "trade,setup_start_ist")
    if df.empty:
        return df
    for c in ("entry_time_ist", "touch_time_ist", "exit_time_ist", "setup_start_ist"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return normalize_numeric_columns(df)


def parse_avoided_time_winners(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if not line.startswith("BEST_NET_R,"):
            continue
        parts = [x.strip() for x in line.split(",")]
        if len(parts) >= 6 and parts[1] in {"BUY", "SELL"}:
            side, slot, pair, net_r, entries = parts[1:6]
        elif len(parts) >= 5 and parts[1] in {"BUY", "SELL"}:
            side, pair, net_r, entries = parts[1:5]
            slot = "All trading slots"
        else:
            continue
        if pair == "NO_ELIGIBLE_TRADES":
            rows.append([side, slot, "-", "-", "0"])
            continue
        rows.append([side, slot, pair, net_r.removeprefix("net_R="), entries.removeprefix("entries=")])
    return rows


def parse_rules_file(text: str):
    """Parse backtest_rules.txt (written by Main.java). Returns dict or None."""
    if "BUY_TIME_RULES" not in text or "SELL_TIME_RULES" not in text:
        return None
    blocks, cur = {}, None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("BACKTEST RULES"):
            continue
        m = re.match(r"^([A-Z_]+)(?:\s*\(.*\))?$", line)
        if m and not re.match(r"^[A-Z]+,", line):
            cur = m.group(1)
            blocks[cur] = []
        elif cur:
            blocks[cur].append(line)

    def slot(t):
        return 48 if t == "24:00" else int(t[:2]) * 2 + int(t[3:]) // 30

    sched = {}
    for key in ("BUY", "SELL"):
        arr = [None] * 48
        for ln in blocks.get(f"{key}_TIME_RULES", []):
            parts = ln.split(",")
            val = None if parts[2] == "AVOID" else (int(parts[2]), int(parts[3]))
            for k in range(slot(parts[0]), slot(parts[1])):
                arr[k] = val
        sched[key] = arr
    days = {}
    for ln in blocks.get("BEST_DAYS", []):
        side, *ds = ln.split(",")
        days[side] = [d.title()[:3] for d in ds]
    combos = {k: [tuple(int(x) for x in ln.split(",")) for ln in blocks.get(f"{k}_COMBINATIONS", [])]
              for k in ("BUY", "SELL")}
    extras = {
        "stop_days": [ln.split(",") for ln in blocks.get("EXCLUDED_STOP_DAYS", [])],
        "dom": [int(x) for x in ",".join(blocks.get("EXCLUDED_DAYS_OF_MONTH", [])).split(",") if x.strip()],
        "round": " ".join(blocks.get("ROUND_NUMBER_FILTER", [])),
    }
    return sched, days, combos, extras


def parse_rules(text: str):
    """Rules from backtest_rules.txt, else from an old-style Java snippet. Returns tuple or None."""
    r = parse_rules_file(text)
    if r:
        return r
    return parse_java_rules(text)


def parse_java_rules(text: str):
    """Parse the Java schedule definitions. Returns (sched, days, combos, extras) or None if not found."""
    sched = {}
    for key in ("BUY", "SELL"):
        m = re.search(rf"{key}_TIME_RULES\s*=\s*buildSchedule\((.*?)\);", text, re.S)
        if not m:
            return None
        arr = [None] * 48
        for a, b, rule in re.findall(
                r'timeRule\("(\d+:\d+)",\s*"(\d+:\d+)",\s*(AVOID|tradeRule\(\d+,\s*\d+\))\)', m.group(1)):
            s = int(a[:2]) * 2 + int(a[3:]) // 30
            e = int(b[:2]) * 2 + int(b[3:]) // 30
            val = None if rule == "AVOID" else tuple(int(x) for x in re.findall(r"\d+", rule))
            for k in range(s, e):
                arr[k] = val
        sched[key] = arr
    days = {}
    for side, body in re.findall(r"Side\.(LONG|SHORT),\s*EnumSet\.of\((.*?)\)\)?[,)]", text, re.S):
        days[side] = [d.title()[:3] for d in re.findall(r"DayOfWeek\.(\w+)", body)]
    combos = {}
    for key in ("BUY", "SELL"):
        m = re.search(rf"{key}_COMBINATIONS\s*=\s*List\.of\((.*?)\);", text, re.S)
        combos[key] = [tuple(map(int, x)) for x in re.findall(r"StopTarget\((\d+),\s*(\d+)\)", m.group(1))] if m else []
    return sched, days, combos, {"stop_days": [], "dom": [], "round": ""}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def hhmm(slot):
    return f"{slot // 2:02d}:{(slot % 2) * 30:02d}"


SLOT_LABELS = [hhmm(i) for i in range(48)]


def session_of(ts):
    m = ts.hour * 60 + ts.minute
    for name, a, b in SESSIONS:
        if a <= m < b:
            return name
    return "NY"


def fmt_dur(mins):
    if mins is None or (isinstance(mins, float) and np.isnan(mins)):
        return "-"
    mins = float(mins)
    return f"{int(mins // 60)}h {int(round(mins % 60))}m" if mins >= 60 else f"{mins:.0f}m"


def max_streak(results, target):
    best = cur = 0
    for r in results:
        cur = cur + 1 if r == target else 0
        best = max(best, cur)
    return best


def f_pct(x): return "-" if pd.isna(x) else f"{x:.1f}%"
def f_usd(x): return "-" if pd.isna(x) else f"{'-' if x < 0 else ''}${abs(x):,.0f}"
def f_num(x, p=2): return "-" if pd.isna(x) else ("inf" if np.isinf(x) else f"{x:.{p}f}")


def finite_number(value, default=0.0):
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def html_table(rows, header, cls=""):
    out = [f'<div class="tw"><table class="{cls}"><thead><tr>']
    out += [f"<th>{h}</th>" for h in header]
    out.append("</tr></thead><tbody>")
    for r in rows:
        out.append("<tr>")
        for c in r:
            style = ""
            if isinstance(c, tuple):
                if len(c) == 3:
                    c, v, extra_class = c
                else:
                    c, v = c
                    extra_class = ""
                if v is not None and not pd.isna(v):
                    color_class = "pos" if v > 0 else "neg" if v < 0 else ""
                    classes = " ".join(part for part in (color_class, extra_class) if part)
                    style = f' class="{classes}"' if classes else ""
            out.append(f"<td{style}>{c}</td>")
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def fig_html(fig, h=420):
    fig.update_layout(template=TEMPLATE, height=h, margin=dict(l=50, r=20, t=50, b=40),
                      font=dict(size=12), legend=dict(orientation="h", y=1.1, x=0))
    return fig.to_html(full_html=False, include_plotlyjs=False,
                       config=dict(displaylogo=False, responsive=True))


# ---------------------------------------------------------------------------
# Trade frame + statistics
# ---------------------------------------------------------------------------

def build_frame(trades: pd.DataFrame, settings: dict) -> pd.DataFrame:
    if trades.empty or "result" not in trades.columns:
        raise SystemExit("No trades found in the raw log (expected a 'TRADES TAKEN (IST):' CSV block).")
    df = trades[trades["result"].isin(["WIN", "LOSS"])].copy()
    if df.empty:
        raise SystemExit("No closed (WIN/LOSS) trades found.")
    if "setup_start_ist" not in df.columns or df["setup_start_ist"].isna().all():
        df["setup_start_ist"] = df["entry_time_ist"]
    df = df.dropna(subset=["entry_time_ist", "exit_time_ist"])
    df = df.sort_values("exit_time_ist").reset_index(drop=True)

    risk = finite_number(settings.get("risk_dollars"), RISK_USD) or RISK_USD
    spread = finite_number(settings.get("spread_points"))
    comm_pct = finite_number(settings.get("commission_percent_of_risk", settings.get("commission_pct_of_risk")))

    # Main.java logs net_R after costs and applies any entry-time risk multiplier.
    if "risk_multiplier" not in df.columns:
        df["risk_multiplier"] = 1.0
    df["risk_multiplier"] = pd.to_numeric(df.risk_multiplier, errors="coerce").fillna(1.0)
    df["gross_R"] = np.where(df.result == "WIN", df.tp_points / df.sl_points, -1.0) * df.risk_multiplier
    df["spread_R"] = spread / df.sl_points * df.risk_multiplier
    df["comm_R"] = comm_pct / 100.0 * df.risk_multiplier
    if "net_R" not in df.columns or df["net_R"].isna().all():
        df["net_R"] = df.pnl_points / df.sl_points
    df["net_R"] = pd.to_numeric(df["net_R"], errors="coerce").fillna(df.pnl_points / df.sl_points)

    df["pnl_usd"] = df.net_R * risk
    df["spread_usd"] = df.spread_R * risk
    df["comm_usd"] = df.comm_R * risk
    df["dur_min"] = (df.exit_time_ist - df.entry_time_ist).dt.total_seconds() / 60
    df["setup_slot"] = df.setup_start_ist.dt.hour * 2 + df.setup_start_ist.dt.minute // 30
    df["entry_slot"] = df.entry_time_ist.dt.hour * 2 + df.entry_time_ist.dt.minute // 30
    df["sess_entry"] = df.entry_time_ist.apply(session_of)
    df["sess_close"] = df.exit_time_ist.apply(session_of)
    df["intersession"] = df.sess_entry != df.sess_close
    df["dow"] = df.setup_start_ist.dt.dayofweek
    df["dom"] = df.setup_start_ist.dt.day
    df["month"] = df.exit_time_ist.dt.to_period("M")
    df["equity"] = ACCOUNT_USD + df.pnl_usd.cumsum()
    df.attrs["risk"] = risk
    return df


def stats(d):
    n = len(d)
    if n == 0:
        return dict(Trades=0, Wins=0, Losses=0, WinRate=np.nan, NetUSD=0.0, AvgPlannedRR=np.nan, AvgR=np.nan,
                    Alpha=np.nan, Sharpe=np.nan, SpreadUSD=0.0, CommUSD=0.0,
                    AvgWinMin=np.nan, AvgLossMin=np.nan, PF=np.nan)
    w, l = d[d.result == "WIN"], d[d.result == "LOSS"]
    gp, gl = w.pnl_usd.sum(), -l.pnl_usd.sum()
    sd = d.net_R.std(ddof=1) if n > 1 else np.nan
    sharpe = d.net_R.mean() / sd * math.sqrt(n) if sd and not np.isnan(sd) and sd > 0 else np.nan
    return dict(Trades=n, Wins=len(w), Losses=len(l), WinRate=len(w) / n * 100,
                NetUSD=d.pnl_usd.sum(), AvgPlannedRR=(d.tp_points / d.sl_points).mean(),
                AvgR=d.gross_R.mean(), Alpha=d.net_R.mean(), Sharpe=sharpe,
                SpreadUSD=d.spread_usd.sum(), CommUSD=d.comm_usd.sum(),
                AvgWinMin=w.dur_min.mean() if len(w) else np.nan,
                AvgLossMin=l.dur_min.mean() if len(l) else np.nan,
                PF=gp / gl if gl > 0 else np.inf)


def cost_cells(s):
    """Alpha / Sharpe / Spread / Commission cells appended to performance tables."""
    return [(f_num(s["Alpha"], 3), s["Alpha"]), f_num(s["Sharpe"], 3),
            f_usd(s["SpreadUSD"]) if s["Trades"] else "-", f_usd(s["CommUSD"]) if s["Trades"] else "-"]


COST_HDR = ["Alpha R/trade", "Sharpe", "Spread cost", "Commission"]


# ---------------------------------------------------------------------------
# Section A
# ---------------------------------------------------------------------------

def section_A(rules):
    if rules is None:
        return ("<p class='note'>BUY_TIME_RULES / SELL_TIME_RULES not found. Pass the Java rules file with "
                "<code>--rules</code> to show the constraint grid.</p>")
    sched, days, combos, extras = rules
    all_combos = sorted({c for k in combos for c in combos[k]} | {v for k in sched for v in sched[k] if v})
    code = {None: 0, **{c: i + 1 for i, c in enumerate(all_combos)}}
    pal = ["#d9d9d9", "#66c2a5", "#1b7a5a", "#fdd49e", "#fc8d59", "#d7301f", "#8856a7", "#3b6fb6", "#b2b200"]
    cs = []
    n = len(code)
    for i in range(n):
        cs += [[i / n, pal[i % len(pal)]], [(i + 1) / n, pal[i % len(pal)]]]
    z = [[code[v] for v in sched["SELL"]], [code[v] for v in sched["BUY"]]]
    txt = [[("AVOID" if v is None else f"{v[0]}<br>{v[1]}") for v in sched[k]] for k in ("SELL", "BUY")]
    fig = go.Figure(go.Heatmap(z=z, x=SLOT_LABELS, y=["SELL (SHORT)", "BUY (LONG)"], colorscale=cs, zmin=0,
                               zmax=n, showscale=False, text=txt, texttemplate="%{text}",
                               textfont=dict(size=9), xgap=2, ygap=2,
                               hovertemplate="%{y} %{x}<br>%{text}<extra></extra>"))
    fig.update_xaxes(side="top", tickangle=-90, tickfont=dict(size=9))
    fig.update_layout(title="Setup-time grid (IST) - cell = SL / TP points, grey = AVOID")
    leg = " ".join(f'<span class="chip" style="background:{pal[code[c] % len(pal)]}">{c[0]} / {c[1]}</span>'
                   for c in all_combos) + f' <span class="chip" style="background:{pal[0]}">AVOID</span>'
    dz, dt = [], []
    for side in ("SHORT", "LONG"):
        dz.append([1 if d in days.get(side, []) else 0 for d in DAYS])
        dt.append(["TRADE" if d in days.get(side, []) else "AVOID" for d in DAYS])
    fig2 = go.Figure(go.Heatmap(z=dz, x=DAYS, y=["SELL (SHORT)", "BUY (LONG)"],
                                colorscale=[[0, "#d9d9d9"], [0.5, "#d9d9d9"], [0.5, "#66c2a5"], [1, "#66c2a5"]],
                                zmin=0, zmax=1, showscale=False, text=dt, texttemplate="%{text}", xgap=3, ygap=3))
    fig2.update_layout(title="Allowed days of week")
    rows = []
    for k, lab in (("BUY", "BUY (LONG)"), ("SELL", "SELL (SHORT)")):
        tr = sum(v is not None for v in sched[k])
        rows.append([lab, f"{tr} ({tr / 2:.1f}h)", f"{48 - tr} ({(48 - tr) / 2:.1f}h)",
                     ", ".join(f"{a}/{b}" for a, b in combos[k]),
                     ", ".join(days.get("LONG" if k == "BUY" else "SHORT", []))])
    tbl = html_table(rows, ["Side", "Trading slots", "Avoid slots", "SL/TP combos", "Allowed days"])
    ex = []
    sd = {}
    for side, stop, day in (r[:3] for r in extras["stop_days"] if len(r) >= 3):
        sd.setdefault(side, []).append(f"{day.title()[:3]}" + ("" if stop == "*" else f" (SL {stop})"))
    for side, lab in (("LONG", "BUY (LONG)"), ("SHORT", "SELL (SHORT)")):
        ex.append([lab, ", ".join(sd.get(side, [])) or "-"])
    dom = ", ".join(str(x) for x in extras["dom"]) or "-"
    tbl2 = html_table(ex, ["Side", "Extra weekday exclusions"])
    note = f"<p class='note'>Days of month never traded: <b>{dom}</b>."
    if extras["round"]:
        note += f" Round-number filter: {html.escape(extras['round'])}."
    note += "</p>"
    return f"<p class='legend'>{leg}</p>{fig_html(fig, 230)}{tbl}{fig_html(fig2, 200)}{tbl2}{note}"


# ---------------------------------------------------------------------------
# Section B
# ---------------------------------------------------------------------------

def kpis(d, settings, summary):
    s = stats(d)
    dd = (d.equity - d.equity.cummax())
    res = d.result.tolist()
    spread = finite_number(settings.get("spread_points"))
    comm = finite_number(settings.get("commission_percent_of_risk", settings.get("commission_pct_of_risk")))
    alpha = summary.get("alpha_R_per_trade", s["Alpha"])
    sharpe = summary.get("sharpe", s["Sharpe"])
    monthly_returns = d.groupby("month").pnl_usd.sum() / ACCOUNT_USD * 100
    avg_monthly_return = monthly_returns.mean() if len(monthly_returns) else np.nan
    annual_returns = d.groupby(d.exit_time_ist.dt.year).pnl_usd.sum() / ACCOUNT_USD * 100
    avg_annual_return = annual_returns.mean() if len(annual_returns) else np.nan
    items = [("Net P&L", f_usd(s["NetUSD"]), s["NetUSD"]),
             (f"Return on {ACCOUNT_USD // 1000}K", f_pct(s["NetUSD"] / ACCOUNT_USD * 100), s["NetUSD"]),
             ("Avg annual return", f_pct(avg_annual_return), avg_annual_return),
             ("Avg return by month", f_pct(avg_monthly_return), avg_monthly_return),
             ("Trades", f"{s['Trades']:,}", None), ("Win rate", f_pct(s["WinRate"]), None),
             ("Avg planned RR", f_num(s["AvgPlannedRR"]), None),
             ("Expectancy (R/trade, gross)", f_num(s["AvgR"], 3), s["AvgR"]),
             ("Alpha (R/trade)", f_num(alpha, 4), alpha), ("Sharpe", f_num(sharpe, 3), None),
             ("Profit factor", f_num(s["PF"]), None),
             ("Spread (points)", f_num(spread, 2), None), ("Commission (% of risk)", f"{comm:.4f}%", None),
             ("Total spread cost", f_usd(-s["SpreadUSD"]), -s["SpreadUSD"]),
             ("Total commission", f_usd(-s["CommUSD"]), -s["CommUSD"]),
             ("Avg win duration", fmt_dur(s["AvgWinMin"]), None), ("Avg loss duration", fmt_dur(s["AvgLossMin"]), None),
             ("Max consec. wins", str(max_streak(res, "WIN")), None),
             ("Max consec. losses", str(max_streak(res, "LOSS")), None),
             ("Max drawdown", f_usd(dd.min()), -1), ("Max DD % of account", f_pct(dd.min() / ACCOUNT_USD * 100), -1)]
    h = "".join(f'<div class="kpi"><div class="kl">{html.escape(a)}</div>'
                f'<div class="kv {"pos" if (c or 0) > 0 else "neg" if (c or 0) < 0 else ""}">{html.escape(b)}</div></div>'
                for a, b, c in items)
    return f'<div class="kpis">{h}</div>'


def group_table(df, col, labels, first_hdr, tot, occ=None, per_label="Avg trades / day"):
    rows, chart = [], []
    for key, lab in labels:
        d = df[df[col] == key]
        s = stats(d)
        chart.append(s)
        row = [lab, s["Trades"], f_pct(s["WinRate"]), f_num(s["AvgPlannedRR"]), f_num(s["AvgR"], 3),
               (f_usd(s["NetUSD"]), s["NetUSD"]), f_pct(s["NetUSD"] / tot * 100) if tot and s["Trades"] else "-"]
        row += cost_cells(s)
        row += [f_num(s["Trades"] / occ.get(key, 1), 2), fmt_dur(s["AvgWinMin"]), fmt_dur(s["AvgLossMin"])]
        rows.append(row)
    hdr = [first_hdr, "Trades", "Win rate", "Avg RR", "Expectancy R", "Net P&L", "% of profit"] + COST_HDR + \
          [per_label, "Avg win dur", "Avg loss dur"]
    return html_table(rows, hdr), chart


def section_B(df, settings, summary):
    out = [kpis(df, settings, summary)]
    eq = df.set_index("exit_time_ist").equity
    dd = df.equity.values - df.equity.cummax().values
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.04)
    fig.add_trace(go.Scatter(x=eq.index, y=eq.values, mode="lines", line=dict(color=BLUE, width=1.6), name="Equity"), 1, 1)
    fig.add_hline(y=ACCOUNT_USD, line_dash="dot", line_color=GREY, row=1, col=1)
    fig.add_trace(go.Scatter(x=eq.index, y=dd, fill="tozeroy", line=dict(color=RED, width=1), name="Drawdown"), 2, 1)
    risk = df.attrs["risk"]
    fig.update_layout(title=f"Overall equity (start ${ACCOUNT_USD:,}; risk varies by entry half-hour as listed below; net of spread & commission) and drawdown")
    out.append(fig_html(fig, 520))

    # Calendar-year net return on the starting account value. First/last years
    # may cover only part of a year, so the chart and note make that explicit.
    annual = df.groupby(df.exit_time_ist.dt.year).pnl_usd.sum() / ACCOUNT_USD * 100
    if not annual.empty:
        colors = [GREEN if value >= 0 else RED for value in annual]
        annual_fig = go.Figure(go.Bar(
            x=[str(year) for year in annual.index], y=annual.values,
            marker_color=colors, name="Net return"))
        annual_fig.update_layout(
            title="Calendar-year net return (fixed starting account; first/last years may be partial)",
            yaxis_title="Return (%)", xaxis_title="Exit year",
            template=TEMPLATE, height=350, margin=dict(l=55, r=20, t=55, b=45))
        annual_fig.add_hline(y=0, line_color=GREY, line_width=1)
        out.append(fig_html(annual_fig, 370))

    tot = df.pnl_usd.sum()

    # --- by side
    rows = []
    for side in ("LONG", "SHORT"):
        g = df[df.side == side]
        s = stats(g)
        rows.append([side, s["Trades"], f_pct(s["WinRate"]), f_num(s["AvgPlannedRR"]), (f_usd(s["NetUSD"]), s["NetUSD"]),
                     f_pct(s["NetUSD"] / tot * 100) if tot else "-", f_num(s["AvgR"], 3)] + cost_cells(s) +
                    [f_num(s["PF"]), fmt_dur(s["AvgWinMin"]), fmt_dur(s["AvgLossMin"]),
                     max_streak(g.result.tolist(), "WIN"), max_streak(g.result.tolist(), "LOSS")])
    out.append("<h3>Performance by side</h3>" + html_table(
        rows, ["Side", "Trades", "Win rate", "Avg RR", "Net P&L", "% of total profit", "Expectancy R"] + COST_HDR +
        ["PF", "Avg win dur", "Avg loss dur", "Max W streak", "Max L streak"]))

    # --- by session
    def srow(label, d):
        s = stats(d)
        return [label, s["Trades"], f_pct(s["WinRate"]), f_num(s["AvgPlannedRR"]), f_num(s["AvgR"], 3),
                (f_usd(s["NetUSD"]), s["NetUSD"])] + cost_cells(s) + [fmt_dur(s["AvgWinMin"]), fmt_dur(s["AvgLossMin"])]

    rows = []
    same = df[~df.intersession]
    for name in ("ASIA", "LONDON", "NY"):
        rows.append(srow(f"{name} (open & close in session)", same[same.sess_entry == name]))
    rows.append(srow("<b>All same-session</b>", same))
    inter = df[df.intersession]
    rows.append(srow("<b>INTERSESSION (open &ne; close)</b>", inter))
    for (a, b), g in inter.groupby(["sess_entry", "sess_close"]):
        rows.append(srow(f"&nbsp;&nbsp;{a} &rarr; {b}", g))
    rows.append(srow("<b>TOTAL</b>", df))
    out.append("<h3>By session (IST: ASIA 05:30-13:30, LONDON 13:30-18:00, NY 18:00-05:30; session = entry time vs exit time)</h3>" +
               html_table(rows, ["Session", "Trades", "Win rate", "Avg RR", "Expectancy R", "Net P&L"] + COST_HDR +
                          ["Avg win dur", "Avg loss dur"]))

    # --- half hour (48 columns)
    metr = ["Risk / trade", "Trades", "Win rate", "Avg RR", "Net P&L", "% of profit", "Alpha R/trade", "Sharpe",
            "Spread cost", "Commission", "Avg win dur", "Avg loss dur"]
    cols = {m: [] for m in metr}
    chart = []
    for sl in range(48):
        cols["Risk / trade"].append(f_usd(risk * (REDUCED_RISK_MULTIPLIER if sl in REDUCED_RISK_SLOTS else 1.0)))
        s = stats(df[df.setup_slot == sl])
        chart.append(s)
        has = s["Trades"] > 0
        cols["Trades"].append(str(s["Trades"]))
        cols["Win rate"].append(f_pct(s["WinRate"]))
        cols["Avg RR"].append(f_num(s["AvgPlannedRR"]))
        cols["Net P&L"].append((f_usd(s["NetUSD"]), s["NetUSD"]) if has else "-")
        cols["% of profit"].append(f_pct(s["NetUSD"] / tot * 100) if has and tot else "-")
        cols["Alpha R/trade"].append((f_num(s["Alpha"], 3), s["Alpha"]) if has else "-")
        cols["Sharpe"].append(f_num(s["Sharpe"], 3))
        cols["Spread cost"].append(f_usd(s["SpreadUSD"]) if has else "-")
        cols["Commission"].append(f_usd(s["CommUSD"]) if has else "-")
        cols["Avg win dur"].append(fmt_dur(s["AvgWinMin"]))
        cols["Avg loss dur"].append(fmt_dur(s["AvgLossMin"]))
    out.append("<h3>By half-hour of day (setup start, IST) - 48 columns; risk is applied by matching entry half-hour</h3>" +
               html_table([[m] + cols[m] for m in metr], ["Metric"] + SLOT_LABELS, cls="wide"))
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06, specs=[[{"secondary_y": True}], [{}]])
    fig.add_trace(go.Bar(x=SLOT_LABELS, y=[c["Trades"] for c in chart], marker_color="#c5d3ea", name="Trades"), 1, 1)
    fig.add_trace(go.Scatter(x=SLOT_LABELS, y=[c["WinRate"] for c in chart], mode="lines+markers", line=dict(color=BLUE),
                             name="Win rate %"), 1, 1, secondary_y=True)
    fig.add_trace(go.Bar(x=SLOT_LABELS, y=[c["NetUSD"] for c in chart],
                         marker_color=[GREEN if c["NetUSD"] >= 0 else RED for c in chart], name="Net P&L $"), 2, 1)
    fig.update_xaxes(tickangle=-90, tickfont=dict(size=9))
    fig.update_layout(title="Half-hour: trades & win rate (top), net P&L (bottom)")
    out.append(fig_html(fig, 560))

    # --- weekday
    cal = pd.date_range(df.setup_start_ist.min().normalize(), df.setup_start_ist.max().normalize(), freq="D")
    occ = pd.Series(cal.dayofweek).value_counts().to_dict()
    tbl, chart = group_table(df, "dow", list(enumerate(DAYS)), "Day", tot, occ)
    out.append("<h3>By day of week (setup day)</h3>" + tbl)
    fig = make_subplots(rows=1, cols=2, subplot_titles=("Net P&L by weekday", "Avg trades per day (frequency)"))
    fig.add_trace(go.Bar(x=DAYS, y=[c["NetUSD"] for c in chart],
                         marker_color=[GREEN if c["NetUSD"] >= 0 else RED for c in chart]), 1, 1)
    fig.add_trace(go.Bar(x=DAYS, y=[c["Trades"] / occ.get(i, 1) for i, c in enumerate(chart)], marker_color=BLUE), 1, 2)
    fig.update_layout(showlegend=False)
    out.append(fig_html(fig, 340))

    # --- monthly curves
    start = min(pd.Period(MONTHS_FROM), df.month.min())
    end = max(pd.Period(MONTHS_TO), df.month.max())
    months = pd.period_range(start, end, freq="M")
    nc = 6
    nr = int(np.ceil(len(months) / nc))
    titles = []
    for m in months:
        g = df[df.month == m]
        titles.append(f"{m.strftime('%b %Y')}  {f_usd(g.pnl_usd.sum())} ({len(g)})")
    fig = make_subplots(rows=nr, cols=nc, subplot_titles=titles, vertical_spacing=0.05, horizontal_spacing=0.04)
    for i, m in enumerate(months):
        g = df[df.month == m]
        r, c = i // nc + 1, i % nc + 1
        if len(g):
            y = np.r_[0, g.pnl_usd.cumsum().values]
            x = [pd.Timestamp(m.start_time)] + g.exit_time_ist.tolist()
            col = GREEN if y[-1] >= 0 else RED
            fig.add_trace(go.Scatter(x=x, y=y, mode="lines", line=dict(color=col, width=1.4, shape="hv"),
                                     fill="tozeroy", fillcolor="rgba(150,150,150,.12)", showlegend=False,
                                     hovertemplate="%{x|%d %b %H:%M}<br>$%{y:,.0f}<extra></extra>"), r, c)
        fig.update_xaxes(range=[m.start_time, m.end_time], showticklabels=False, row=r, col=c)
        fig.update_yaxes(tickfont=dict(size=8), row=r, col=c)
    fig.update_annotations(font_size=10)
    out.append("<h3>Cumulative P&amp;L by month (title = net P&amp;L, trade count; month of exit)</h3>")
    out.append(fig_html(fig, 210 * nr))

    mm = df.groupby("month").pnl_usd.sum().reindex(months, fill_value=0)
    fig = go.Figure(go.Bar(x=[m.strftime("%b %y") for m in months], y=mm.values,
                           marker_color=[GREEN if v >= 0 else RED for v in mm.values]))
    fig.update_layout(title="Monthly net P&L ($)")
    out.append(fig_html(fig, 340))
    return "".join(out)


# ---------------------------------------------------------------------------
# Section C - day of month
# ---------------------------------------------------------------------------

def section_C(df):
    tot = df.pnl_usd.sum()
    cal = pd.date_range(df.setup_start_ist.min().normalize(), df.setup_start_ist.max().normalize(), freq="D")
    occ = pd.Series(cal.day).value_counts().to_dict()
    tbl, chart = group_table(df, "dom", [(d, str(d)) for d in range(1, 32)], "Day of month", tot, occ,
                             per_label="Avg trades / occurrence")
    labels = [str(d) for d in range(1, 32)]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07, specs=[[{"secondary_y": True}], [{}]])
    fig.add_trace(go.Bar(x=labels, y=[c["Trades"] for c in chart], marker_color="#c5d3ea", name="Trades"), 1, 1)
    fig.add_trace(go.Scatter(x=labels, y=[c["WinRate"] for c in chart], mode="lines+markers", line=dict(color=BLUE),
                             name="Win rate %"), 1, 1, secondary_y=True)
    fig.add_trace(go.Bar(x=labels, y=[c["NetUSD"] for c in chart],
                         marker_color=[GREEN if c["NetUSD"] >= 0 else RED for c in chart], name="Net P&L $"), 2, 1)
    fig.update_xaxes(title_text="Day of month (setup day)", row=2, col=1)
    fig.update_layout(title="Day of month: trades & win rate (top), net P&L (bottom)")
    return fig_html(fig, 560) + "<h3>By day of month (setup day)</h3>" + tbl


# ---------------------------------------------------------------------------
# Section D
# ---------------------------------------------------------------------------

def section_D(df):
    out = []
    for col, ttl in (("setup_slot", "setup_start_ist"), ("entry_slot", "entry_time_ist")):
        w = df[df.result == "WIN"].groupby(col).size().reindex(range(48), fill_value=0)
        l = df[df.result == "LOSS"].groupby(col).size().reindex(range(48), fill_value=0)
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Bar(x=SLOT_LABELS, y=w.values, name="WIN", marker_color=GREEN))
        fig.add_trace(go.Bar(x=SLOT_LABELS, y=l.values, name="LOSS", marker_color=RED))
        wr = (w / (w + l).replace(0, np.nan) * 100)
        fig.add_trace(go.Scatter(x=SLOT_LABELS, y=wr.values, name="Win rate %", mode="lines+markers",
                                 line=dict(color="#222")), secondary_y=True)
        fig.update_layout(barmode="stack", title=f"Trade count by half-hour of {ttl} (IST)")
        fig.update_xaxes(tickangle=-90, tickfont=dict(size=9))
        out.append(fig_html(fig, 400))
    fig = go.Figure()
    for side, col in (("LONG", GREEN), ("SHORT", RED)):
        c = df[df.side == side].groupby("setup_slot").size().reindex(range(48), fill_value=0)
        fig.add_trace(go.Bar(x=SLOT_LABELS, y=c.values, name=side, marker_color=col))
    fig.update_layout(barmode="group", title="Setup start half-hour by side")
    fig.update_xaxes(tickangle=-90, tickfont=dict(size=9))
    out.append(fig_html(fig, 360))
    return "".join(out)


# ---------------------------------------------------------------------------
# Section E
# ---------------------------------------------------------------------------

def round_hits(lo, hi, step):
    lo, hi = min(lo, hi), max(lo, hi)
    return [k * step for k in range(int(np.ceil(lo / step)), int(np.floor(hi / step)) + 1)]


def section_E(df):
    d = df.copy()
    tp_hits = [round_hits(a, b, ROUND_STEP) for a, b in zip(d.entry_price, d.target_price)]
    sl_hits = [round_hits(a, b, ROUND_STEP) for a, b in zip(d.entry_price, d.stop_price)]
    d["tp_round"] = [len(h) > 0 for h in tp_hits]
    d["sl_round"] = [len(h) > 0 for h in sl_hits]
    d["tp_1000"] = [any(x % 1000 == 0 for x in h) for h in tp_hits]
    d["sl_1000"] = [any(x % 1000 == 0 for x in h) for h in sl_hits]
    d["tp_500only"] = d.tp_round & ~d.tp_1000
    d["sl_500only"] = d.sl_round & ~d.sl_1000

    def row(label, m):
        g = d[m]
        s = stats(g)
        return [label, s["Trades"], s["Wins"], s["Losses"], f_pct(s["WinRate"]), f_num(s["AvgR"], 3),
                (f_usd(s["NetUSD"]), s["NetUSD"])]

    hdr = ["Condition", "Trades", "WIN", "LOSS", "Win rate", "Expectancy R", "Net P&L"]
    A = [row("Any 500x between entry & TARGET", d.tp_round), row("&nbsp;&nbsp;1000x between entry & TARGET", d.tp_1000),
         row("&nbsp;&nbsp;500x only (not 1000x) between entry & TARGET", d.tp_500only),
         row("No round no. between entry & TARGET", ~d.tp_round),
         row("Any 500x between entry & SL", d.sl_round), row("&nbsp;&nbsp;1000x between entry & SL", d.sl_1000),
         row("&nbsp;&nbsp;500x only (not 1000x) between entry & SL", d.sl_500only),
         row("No round no. between entry & SL", ~d.sl_round)]
    B = [row("Round in TARGET path only", d.tp_round & ~d.sl_round), row("Round in SL path only", ~d.tp_round & d.sl_round),
         row("Round in BOTH paths", d.tp_round & d.sl_round), row("Round in NEITHER path", ~d.tp_round & ~d.sl_round)]
    C = []
    for side in ("LONG", "SHORT"):
        s_ = d.side == side
        C += [row(f"{side}: round in TARGET path", s_ & d.tp_round), row(f"{side}: round in SL path", s_ & d.sl_round),
              row(f"{side}: no round in either", s_ & ~d.tp_round & ~d.sl_round)]
    fig = make_subplots(rows=1, cols=2, subplot_titles=("Round number between entry & TARGET",
                                                        "Round number between entry & SL"))
    for i, col in enumerate(("tp_round", "sl_round"), 1):
        for flag, lab in ((True, "round present"), (False, "no round")):
            g = d[d[col] == flag]
            fig.add_trace(go.Bar(x=["WIN", "LOSS"], y=[(g.result == "WIN").sum(), (g.result == "LOSS").sum()],
                                 name=lab, marker_color=BLUE if flag else "#c5c9d1", showlegend=(i == 1)), 1, i)
    fig.update_layout(barmode="group")
    return (fig_html(fig, 340) + "<h3>Round number in each path</h3>" + html_table(A, hdr)
            + "<h3>Combined</h3>" + html_table(B, hdr) + "<h3>By side</h3>" + html_table(C, hdr)
            + f"<p class='note'>Round numbers = multiples of {ROUND_STEP} (so every 1000 is included); "
              "a level exactly on the entry/TP/SL price counts as between.</p>")


# ---------------------------------------------------------------------------
# Section F - SL/TP optimization logs
# ---------------------------------------------------------------------------

def section_F(avoided_text: str) -> str:
    out = ["<p class='note'>Each currently avoided half-hour is evaluated separately; candidates are ranked by cumulative net_R.</p>"]
    winners = parse_avoided_time_winners(avoided_text) if avoided_text else []
    if winners:
        out.append("<h3>Best candidate by currently avoided half-hour</h3>" +
                   html_table(winners, ["Side", "IST half-hour", "SL/TP", "Net R", "Trades"]))
    else:
        out.append("<p class='note'>No avoided-time search results found.</p>")
    return "".join(out)


def section_G(df: pd.DataFrame) -> str:
    required = {"touch_time_ist", "setup_start_ist", "entry_time_ist", "side", "result"}
    if not required.issubset(df.columns):
        return "<p class='note'>Timing analysis needs a fresh Main.java run that writes touch_time_ist to the trade log.</p>"

    d = df[df.result.isin(["WIN", "LOSS"])].copy()
    d["A_minute"] = np.floor((d.touch_time_ist - d.setup_start_ist).dt.total_seconds() / 60).astype(int)
    d["B_minute"] = np.floor((d.entry_time_ist - d.touch_time_ist).dt.total_seconds() / 60).astype(int)
    minutes = list(range(30))
    out = ["<p class='note'>Columns 0–29 are elapsed-minute buckets: 0 covers the first minute of the 30m candle, and 29 covers its final minute before the next candle. For each side, WIN/LOSS entry counts appear first; summed net_R by outcome follows. Only the larger absolute net_R value is colored (green for WIN, red for LOSS); the other stays black. A pale bold highlight marks a magnitude over 2× its counterpart. A is setup open to first touch; B is first touch to entry.</p>"]
    for field, title in (("A_minute", "A. Setup open → 30m touch"), ("B_minute", "B. 30m touch → 1h entry")):
        out.append(f"<h3>{title}</h3>")
        z, count_data = [], []
        ylabels = []
        bucket_rows = {}
        for side in ("LONG", "SHORT"):
            counts_by_outcome, net_by_outcome = {}, {}
            for outcome in ("WIN", "LOSS"):
                subset = d[(d.side == side) & (d.result == outcome)]
                values = subset.groupby(field).net_R.sum().reindex(minutes, fill_value=0)
                counts = subset.groupby(field).size().reindex(minutes, fill_value=0)
                net_by_outcome[outcome] = values
                counts_by_outcome[outcome] = counts
                ylabels.append(f"{side} {outcome}")
                z.append(values.tolist())
                count_data.append(counts.tolist())
            bucket_rows[side] = (counts_by_outcome, net_by_outcome)
        fig = go.Figure(go.Heatmap(z=z, x=minutes, y=ylabels, customdata=count_data,
                                   colorscale=[[0, "#b2182b"], [0.5, "#fff"], [1, "#1a9850"]], zmid=0,
                                   hovertemplate="%{y}<br>Bucket: %{x} min<br>Net R: %{z:.3f}<br>Trades: %{customdata}<extra></extra>",
                                   colorbar=dict(title="Net R")))
        fig.update_layout(title=f"Summed net R by elapsed-minute bucket — {title}",
                          xaxis_title="Elapsed-minute bucket (0–29)", yaxis_title="Side / outcome",
                          height=290, template=TEMPLATE, margin=dict(l=100, r=30, t=55, b=45))
        fig.update_xaxes(dtick=1)
        out.append(fig_html(fig, 310))
        total_counts = d.groupby(field).size().reindex(minutes, fill_value=0)
        out.append("<h4>Total entries — both sides</h4>" +
                   html_table([["Entries"] + [int(v) for v in total_counts.values]],
                              ["All outcomes"] + [str(m) for m in minutes], cls="wide"))
        for side in ("LONG", "SHORT"):
            counts, net = bucket_rows[side]
            side_rows = [
                ["Entries — WIN"] + [int(v) for v in counts["WIN"].values],
                ["Entries — LOSS"] + [int(v) for v in counts["LOSS"].values],
            ]
            win_cells, loss_cells = [], []
            for win_r, loss_r in zip(net["WIN"].values, net["LOSS"].values):
                win_mag, loss_mag = abs(win_r), abs(loss_r)
                if win_mag > loss_mag:
                    emphasis = "bucket-win strong" if win_mag > 2 * loss_mag else "bucket-win"
                    win_cells.append((f_num(win_r, 3), 1, emphasis))
                    loss_cells.append(f_num(loss_r, 3))
                elif loss_mag > win_mag:
                    emphasis = "bucket-loss strong" if loss_mag > 2 * win_mag else "bucket-loss"
                    win_cells.append(f_num(win_r, 3))
                    loss_cells.append((f_num(loss_r, 3), -1, emphasis))
                else:
                    win_cells.append(f_num(win_r, 3))
                    loss_cells.append(f_num(loss_r, 3))
            side_rows.extend([["Net_R — WIN"] + win_cells, ["Net_R — LOSS"] + loss_cells])
            out.append(f"<h4>{side}</h4>" + html_table(side_rows, ["Entries / net_R"] + [str(m) for m in minutes], cls="wide"))
    return "".join(out)


# ---------------------------------------------------------------------------
# Section H - event minute within hour (clock-minute buckets 0–59)
# ---------------------------------------------------------------------------

def section_H(df: pd.DataFrame) -> str:
    required = {"touch_time_ist", "entry_time_ist", "side", "result", "net_R"}
    if not required.issubset(df.columns):
        return "<p class='note'>Minute-of-hour analysis needs touch_time_ist and entry_time_ist in the trade log.</p>"

    d = df[df.result.isin(["WIN", "LOSS"])].copy()
    minute_labels = list(range(60))
    out = ["<p class='note'>Buckets are clock minutes within each IST hour: 0 = HH:00, 59 = HH:59. A groups by the minute the 30m level was touched; B groups by the minute the 1h entry level was reached. Net_R is summed by side and final trade outcome.</p>"]
    for time_col, title in (("touch_time_ist", "A. 30m level touch"),
                            ("entry_time_ist", "B. 1h entry level")):
        minute_col = f"{time_col}_minute_of_hour"
        d[minute_col] = d[time_col].dt.minute
        out.append(f"<h3>{title}</h3>")
        net_by_side, count_by_side = {}, {}
        z, count_data, ylabels = [], [], []
        for side in ("LONG", "SHORT"):
            net_by_side[side], count_by_side[side] = {}, {}
            for outcome in ("WIN", "LOSS"):
                subset = d[(d.side == side) & (d.result == outcome)]
                net = subset.groupby(minute_col).net_R.sum().reindex(minute_labels, fill_value=0)
                counts = subset.groupby(minute_col).size().reindex(minute_labels, fill_value=0)
                net_by_side[side][outcome] = net
                count_by_side[side][outcome] = counts
                z.append(net.tolist())
                count_data.append(counts.tolist())
                ylabels.append(f"{side} {outcome}")

        fig = go.Figure(go.Heatmap(z=z, x=minute_labels, y=ylabels, customdata=count_data,
                                   colorscale=[[0, "#b2182b"], [0.5, "#fff"], [1, "#1a9850"]], zmid=0,
                                   hovertemplate="%{y}<br>IST minute: %{x}<br>Summed net_R: %{z:.3f}<br>Entries: %{customdata}<extra></extra>",
                                   colorbar=dict(title="Summed net_R")))
        fig.update_layout(title=f"Summed net_R by IST minute within the hour — {title}",
                          xaxis_title="Minute within the hour (IST)", yaxis_title="Side / outcome",
                          height=300, template=TEMPLATE, margin=dict(l=100, r=30, t=55, b=45))
        fig.update_xaxes(dtick=5)
        out.append(fig_html(fig, 320))

        total_entries = d.groupby(minute_col).size().reindex(minute_labels, fill_value=0)
        out.append("<h4>Total entries — both sides</h4>" +
                   html_table([["Entries"] + [int(v) for v in total_entries.values]],
                              ["All outcomes"] + [str(m) for m in minute_labels], cls="wide"))
        for side in ("LONG", "SHORT"):
            counts, net = count_by_side[side], net_by_side[side]
            rows = [
                ["Entries — WIN"] + [int(v) for v in counts["WIN"].values],
                ["Entries — LOSS"] + [int(v) for v in counts["LOSS"].values],
            ]
            win_cells, loss_cells = [], []
            for win_r, loss_r in zip(net["WIN"].values, net["LOSS"].values):
                win_mag, loss_mag = abs(win_r), abs(loss_r)
                if win_mag > loss_mag:
                    style = "bucket-win strong" if win_mag > 2 * loss_mag else "bucket-win"
                    win_cells.append((f_num(win_r, 3), 1, style))
                    loss_cells.append(f_num(loss_r, 3))
                elif loss_mag > win_mag:
                    style = "bucket-loss strong" if loss_mag > 2 * win_mag else "bucket-loss"
                    win_cells.append(f_num(win_r, 3))
                    loss_cells.append((f_num(loss_r, 3), -1, style))
                else:
                    win_cells.append(f_num(win_r, 3))
                    loss_cells.append(f_num(loss_r, 3))
            rows.extend([["Net_R — WIN"] + win_cells, ["Net_R — LOSS"] + loss_cells])
            out.append(f"<h4>{side}</h4>" + html_table(rows, ["Entries / net_R"] + [str(m) for m in minute_labels], cls="wide"))
    return "".join(out)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f5f6f8;color:#1d2330}
main{max-width:1500px;margin:0 auto;padding:20px}
h1{margin:0 0 4px} h2{margin:36px 0 10px;padding:8px 12px;background:#1d2330;color:#fff;border-radius:6px;font-size:18px}
h3{margin:22px 0 8px;font-size:15px} .sub{color:#6b7280;margin-bottom:10px}
section{background:#fff;border-radius:8px;padding:6px 16px 16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin:14px 0}
.kpi{background:#f5f6f8;border-radius:6px;padding:10px 12px}.kl{font-size:11px;color:#6b7280}.kv{font-size:20px;font-weight:600}
.pos{color:#1b9e77}.neg{color:#d95f02}
.bucket-win{color:#16803c!important}.bucket-loss{color:#d62728!important}
.bucket-win.strong{background:#d9f2df;font-weight:700}.bucket-loss.strong{background:#ffe0e0;font-weight:700}
.tw{overflow-x:auto}table{border-collapse:collapse;font-size:12.5px;width:100%}
th,td{padding:5px 9px;border-bottom:1px solid #e5e7eb;text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left;position:sticky;left:0;background:#fff}
th{background:#f0f2f5;font-size:11.5px}table.wide th,table.wide td{padding:4px 6px;font-size:11px}
.chip{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;margin-right:4px}
.note{color:#6b7280;font-size:12px}.legend{margin:10px 0 0}
"""


def plotly_script() -> str:
    p = os.path.join(os.path.dirname(plotly.__file__), "package_data", "plotly.min.js")
    if os.path.exists(p):
        return f"<script>{open(p, encoding='utf-8').read()}</script>"
    return '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>'


def make_dashboard(raw_path: str, schedule_path: str, rules_path: str | None, output_path: str,
                   avoided_search_path: str = "avoided_time_ratio_search.log"):
    raw = latest_raw_run(read_text(raw_path))
    schedule = read_text(schedule_path) if Path(schedule_path).exists() else ""
    avoided_search = read_text(avoided_search_path) if Path(avoided_search_path).exists() else ""

    settings = parse_settings(raw)
    summary = parse_summary(raw)
    df = build_frame(parse_trades(raw), settings)

    rules = None
    for src in ([read_text(rules_path)] if rules_path and Path(rules_path).exists() else []) + [raw, schedule]:
        rules = parse_rules(src)
        if rules:
            break
    if rules is None:
        print("WARNING: no rules found (run Main.java to create backtest_rules.txt); section A will be empty.")

    risk = df.attrs["risk"]
    secs = [("A. Setup constraints - BUY &amp; SELL, AVOID vs TRADE, by time and day", section_A(rules)),
            ("B. Performance analysis", section_B(df, settings, summary)),
            ("C. Day of month analysis", section_C(df)),
            ("D. Setup / entry time distribution (half-hour)", section_D(df)),
            ("E. Round-number analysis (500 / 1000 multiples)", section_E(df)),
            ("F. Per-time SL/TP optimization by net_R", section_F(avoided_search)),
            ("G. Setup and entry duration win/loss analysis", section_G(df)),
            ("H. Level event minute-of-hour win/loss analysis", section_H(df))]
    body = "".join(f"<h2>{t}</h2><section>{h}</section>" for t, h in secs)
    sub = (f"{len(df):,} trades &middot; {df.setup_start_ist.min():%d %b %Y} &rarr; {df.exit_time_ist.max():%d %b %Y} (IST) "
           f"&middot; per-entry risk varies by IST half-hour (${risk * REDUCED_RISK_MULTIPLIER:,.0f} at 04:30, 12:00, 17:00; ${risk:,.0f} otherwise) &middot; "
           f"source: {html.escape(Path(raw_path).name)} &middot; generated {datetime.now():%Y-%m-%d %H:%M}")
    doc = (f"<!doctype html><html><head><meta charset='utf-8'><title>Backtest dashboard</title><style>{CSS}</style>"
           f"{plotly_script()}</head><body><main><h1>Backtest performance dashboard</h1>"
           f"<div class='sub'>{sub}</div>{body}</main></body></html>")
    Path(output_path).write_text(doc, encoding="utf-8")
    print(f"Wrote {output_path} ({os.path.getsize(output_path) / 1e6:.1f} MB)")


if __name__ == "__main__":
    a = parse_args()
    make_dashboard(a.raw, a.schedule, a.rules, a.output, a.avoided_time_search)
