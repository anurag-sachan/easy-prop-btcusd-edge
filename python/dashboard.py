#!/usr/bin/env python3
"""
Backtest performance dashboard  ->  single self-contained HTML file.

Usage:
    python dashboard.py --trades trades.txt --rules rules.txt --out dashboard.html

Inputs
    --trades : CSV (optional first line "TRADES TAKEN (IST):") with columns
               trade,setup_start_ist,side,entry_time_ist,entry_price,touch_level,
               sl_points,tp_points,stop_price,target_price,exit_time_ist,exit_price,
               result,pnl_points
    --rules  : the Java setup snippet (BUY/SELL_TIME_RULES, BEST_DAYS, *_COMBINATIONS).
               It is parsed with regexes, so edit the rules file and re-run.

Assumptions (edit the CONFIG block to change)
    * Fixed fractional sizing: every trade risks RISK_USD at its own SL, so
      pnl_usd = pnl_points / sl_points * RISK_USD. No compounding.
    * Sessions (IST): ASIA 05:30-13:30, LONDON 13:30-18:00, NY 18:00-05:30.
    * Session / time-of-day / weekday stats are keyed on the SETUP START time
      (that is what the rule grid is keyed on); entry-time buckets are shown in
      section D as well.
    * Round numbers = multiples of 500 (1000-multiples reported separately).
"""
import argparse, re, io, os
from datetime import datetime
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly

# ----------------------------------------------------------------- CONFIG
ACCOUNT_USD = 100_000
RISK_USD = 1_000
DIST_STEP = 20                      # section C bucket width (points)
ROUND_STEP = 500                    # section E round-number grid
SESSIONS = [("ASIA", 330, 810), ("LONDON", 810, 1080)]   # minutes of day; NY = rest
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
GREEN, RED, BLUE, GREY = "#1b9e77", "#d95f02", "#3b6fb6", "#8a8f98"
TEMPLATE = "plotly_white"


# ----------------------------------------------------------------- helpers
def hhmm(slot):  # 0..47 -> "HH:MM"
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


def stats(d):
    n = len(d)
    if n == 0:
        return dict(Trades=0, Wins=0, Losses=0, WinRate=np.nan, NetUSD=0.0, AvgPlannedRR=np.nan,
                    AvgR=np.nan, AvgWinMin=np.nan, AvgLossMin=np.nan, PF=np.nan)
    w, l = d[d.result == "WIN"], d[d.result == "LOSS"]
    gp, gl = w.pnl_usd.sum(), -l.pnl_usd.sum()
    return dict(Trades=n, Wins=len(w), Losses=len(l), WinRate=len(w) / n * 100,
                NetUSD=d.pnl_usd.sum(), AvgPlannedRR=(d.tp_points / d.sl_points).mean(),
                AvgR=d.R.mean(), AvgWinMin=w.dur_min.mean() if len(w) else np.nan,
                AvgLossMin=l.dur_min.mean() if len(l) else np.nan,
                PF=gp / gl if gl > 0 else np.inf)


def f_pct(x): return "-" if pd.isna(x) else f"{x:.1f}%"
def f_usd(x): return "-" if pd.isna(x) else f"{'-' if x < 0 else ''}${abs(x):,.0f}"
def f_num(x, p=2): return "-" if pd.isna(x) else ("inf" if np.isinf(x) else f"{x:.{p}f}")


def html_table(rows, header, cls="", color_cols=()):
    """rows: list of lists of str (or (str, numeric-for-colour) tuples)."""
    out = [f'<div class="tw"><table class="{cls}"><thead><tr>']
    out += [f"<th>{h}</th>" for h in header]
    out.append("</tr></thead><tbody>")
    for r in rows:
        out.append("<tr>")
        for j, c in enumerate(r):
            style = ""
            if isinstance(c, tuple):
                c, v = c
                if v is not None and not pd.isna(v):
                    style = f' class="{"pos" if v > 0 else "neg" if v < 0 else ""}"'
            out.append(f"<td{style}>{c}</td>")
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def fig_html(fig, h=420):
    fig.update_layout(template=TEMPLATE, height=h, margin=dict(l=50, r=20, t=50, b=40),
                      font=dict(size=12), legend=dict(orientation="h", y=1.1, x=0))
    return fig.to_html(full_html=False, include_plotlyjs=False, config=dict(displaylogo=False))


# ----------------------------------------------------------------- loading
def load_trades(path):
    txt = open(path).read().splitlines()
    i = next(k for k, l in enumerate(txt) if l.startswith("trade,"))
    df = pd.read_csv(io.StringIO("\n".join(txt[i:])))
    for c in ("setup_start_ist", "entry_time_ist", "exit_time_ist"):
        df[c] = pd.to_datetime(df[c])
    df = df.sort_values("exit_time_ist").reset_index(drop=True)
    df["R"] = df.pnl_points / df.sl_points
    df["pnl_usd"] = df.R * RISK_USD
    df["dur_min"] = (df.exit_time_ist - df.entry_time_ist).dt.total_seconds() / 60
    df["setup_slot"] = df.setup_start_ist.dt.hour * 2 + df.setup_start_ist.dt.minute // 30
    df["entry_slot"] = df.entry_time_ist.dt.hour * 2 + df.entry_time_ist.dt.minute // 30
    df["sess_open"] = df.setup_start_ist.apply(session_of)
    df["sess_close"] = df.exit_time_ist.apply(session_of)
    df["sess_entry"] = df.entry_time_ist.apply(session_of)
    df["intersession"] = df.sess_entry != df.sess_close
    df["dow"] = df.setup_start_ist.dt.dayofweek
    df["month"] = df.exit_time_ist.dt.to_period("M")
    df["cum_usd"] = df.pnl_usd.cumsum()
    df["equity"] = ACCOUNT_USD + df.cum_usd
    return df


def parse_rules(path):
    t = open(path).read()
    sched = {}
    for key in ("BUY", "SELL"):
        m = re.search(rf"{key}_TIME_RULES\s*=\s*buildSchedule\((.*?)\);", t, re.S)
        arr = [None] * 48
        for a, b, rule in re.findall(r'timeRule\("(\d+:\d+)",\s*"(\d+:\d+)",\s*(AVOID|tradeRule\(\d+,\s*\d+\))\)', m.group(1)):
            s = int(a[:2]) * 2 + int(a[3:]) // 30
            e = int(b[:2]) * 2 + int(b[3:]) // 30
            val = None if rule == "AVOID" else tuple(int(x) for x in re.findall(r"\d+", rule))
            for k in range(s, e):
                arr[k] = val
        sched[key] = arr
    days = {}
    for side, body in re.findall(r"Side\.(LONG|SHORT),\s*EnumSet\.of\((.*?)\)\)?[,)]", t, re.S):
        days[side] = [d.title()[:3] for d in re.findall(r"DayOfWeek\.(\w+)", body)]
    combos = {}
    for key in ("BUY", "SELL"):
        m = re.search(rf"{key}_COMBINATIONS\s*=\s*List\.of\((.*?)\);", t, re.S)
        combos[key] = [tuple(map(int, x)) for x in re.findall(r"StopTarget\((\d+),\s*(\d+)\)", m.group(1))]
    return sched, days, combos


# ----------------------------------------------------------------- sections
def section_A(sched, days, combos):
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
    # legend
    leg = " ".join(f'<span class="chip" style="background:{pal[code[c] % len(pal)]}">{c[0]} / {c[1]}</span>'
                   for c in all_combos) + f' <span class="chip" style="background:{pal[0]}">AVOID</span>'
    # day matrix
    dz, dt = [], []
    for side in ("SHORT", "LONG"):
        dz.append([1 if d in days.get(side, []) else 0 for d in DAYS])
        dt.append(["TRADE" if d in days.get(side, []) else "AVOID" for d in DAYS])
    fig2 = go.Figure(go.Heatmap(z=dz, x=DAYS, y=["SELL (SHORT)", "BUY (LONG)"],
                                colorscale=[[0, "#d9d9d9"], [0.5, "#d9d9d9"], [0.5, "#66c2a5"], [1, "#66c2a5"]],
                                zmin=0, zmax=1, showscale=False, text=dt, texttemplate="%{text}", xgap=3, ygap=3))
    fig2.update_layout(title="Allowed days of week")
    # coverage summary
    rows = []
    for k, lab in (("BUY", "BUY (LONG)"), ("SELL", "SELL (SHORT)")):
        tr = sum(v is not None for v in sched[k])
        rows.append([lab, f"{tr} ({tr / 2:.1f}h)", f"{48 - tr} ({(48 - tr) / 2:.1f}h)",
                     ", ".join(f"{a}/{b}" for a, b in combos[k]), ", ".join(days.get("LONG" if k == "BUY" else "SHORT", []))])
    tbl = html_table(rows, ["Side", "Trading slots", "Avoid slots", "SL/TP combos", "Allowed days"])
    return (f"<p class='legend'>{leg}</p>{fig_html(fig, 230)}{tbl}{fig_html(fig2, 200)}")


def kpis(d):
    s = stats(d)
    eq = ACCOUNT_USD + d.pnl_usd.cumsum()
    dd = (eq - eq.cummax())
    res = d.result.tolist()
    items = [("Net P&L", f_usd(s["NetUSD"]), s["NetUSD"]), ("Return on 100K", f_pct(s["NetUSD"] / ACCOUNT_USD * 100), s["NetUSD"]),
             ("Trades", f"{s['Trades']:,}", None), ("Win rate", f_pct(s["WinRate"]), None),
             ("Avg planned RR", f_num(s["AvgPlannedRR"]), None), ("Expectancy (R/trade)", f_num(s["AvgR"], 3), s["AvgR"]),
             ("Profit factor", f_num(s["PF"]), None), ("Avg win duration", fmt_dur(s["AvgWinMin"]), None),
             ("Avg loss duration", fmt_dur(s["AvgLossMin"]), None),
             ("Max consec. wins", str(max_streak(res, "WIN")), None), ("Max consec. losses", str(max_streak(res, "LOSS")), None),
             ("Max drawdown", f_usd(dd.min()), -1), ("Max DD % of account", f_pct(dd.min() / ACCOUNT_USD * 100), -1)]
    h = "".join(f'<div class="kpi"><div class="kl">{a}</div><div class="kv {"pos" if (c or 0) > 0 else "neg" if (c or 0) < 0 else ""}">{b}</div></div>'
                for a, b, c in items)
    return f'<div class="kpis">{h}</div>'


def section_B(df):
    out = [kpis(df)]
    # --- equity + drawdown overall
    eq = df.set_index("exit_time_ist").equity
    dd = df.equity - df.equity.cummax()
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.04)
    fig.add_trace(go.Scatter(x=eq.index, y=eq.values, mode="lines", line=dict(color=BLUE, width=1.6), name="Equity"), 1, 1)
    fig.add_hline(y=ACCOUNT_USD, line_dash="dot", line_color=GREY, row=1, col=1)
    fig.add_trace(go.Scatter(x=eq.index, y=dd.values, fill="tozeroy", line=dict(color=RED, width=1), name="Drawdown"), 2, 1)
    fig.update_layout(title=f"Overall equity (start ${ACCOUNT_USD:,}, fixed ${RISK_USD:,} risk / trade) and drawdown")
    out.append(fig_html(fig, 520))

    # --- by side
    tot = df.pnl_usd.sum()
    rows = []
    for side in ("LONG", "SHORT"):
        s = stats(df[df.side == side])
        rows.append([side, s["Trades"], f_pct(s["WinRate"]), f_num(s["AvgPlannedRR"]), (f_usd(s["NetUSD"]), s["NetUSD"]),
                     f_pct(s["NetUSD"] / tot * 100) if tot else "-", f_num(s["AvgR"], 3), f_num(s["PF"]),
                     fmt_dur(s["AvgWinMin"]), fmt_dur(s["AvgLossMin"]),
                     max_streak(df[df.side == side].result.tolist(), "WIN"), max_streak(df[df.side == side].result.tolist(), "LOSS")])
    out.append("<h3>Performance by side</h3>" + html_table(rows, ["Side", "Trades", "Win rate", "Avg RR", "Net P&L", "% of total profit",
                                                                "Expectancy R", "PF", "Avg win dur", "Avg loss dur", "Max W streak", "Max L streak"]))

    # --- by session
    rows = []

    def srow(label, d):
        s = stats(d)
        return [label, s["Trades"], f_pct(s["WinRate"]), f_num(s["AvgPlannedRR"]), f_num(s["AvgR"], 3),
                (f_usd(s["NetUSD"]), s["NetUSD"]), fmt_dur(s["AvgWinMin"]), fmt_dur(s["AvgLossMin"])]

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
               html_table(rows, ["Session", "Trades", "Win rate", "Avg RR", "Expectancy R", "Net P&L", "Avg win dur", "Avg loss dur"]))

    # --- half hour (48 columns, transposed)
    metr = ["Trades", "Win rate", "Avg RR", "Net P&L", "% of profit", "Avg win dur", "Avg loss dur"]
    cols = {m: [] for m in metr}
    chart = []
    for sl in range(48):
        d = df[df.setup_slot == sl]
        s = stats(d)
        chart.append(s)
        cols["Trades"].append(str(s["Trades"]))
        cols["Win rate"].append(f_pct(s["WinRate"]))
        cols["Avg RR"].append(f_num(s["AvgPlannedRR"]))
        cols["Net P&L"].append((f_usd(s["NetUSD"]), s["NetUSD"]) if s["Trades"] else "-")
        cols["% of profit"].append(f_pct(s["NetUSD"] / tot * 100) if s["Trades"] else "-")
        cols["Avg win dur"].append(fmt_dur(s["AvgWinMin"]))
        cols["Avg loss dur"].append(fmt_dur(s["AvgLossMin"]))
    rows = [[m] + cols[m] for m in metr]
    out.append("<h3>By half-hour of day (setup start, IST) - 48 columns</h3>" +
               html_table(rows, ["Metric"] + SLOT_LABELS, cls="wide"))
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
    first, last = df.setup_start_ist.min().normalize(), df.setup_start_ist.max().normalize()
    cal = pd.date_range(first, last, freq="D")
    occ = pd.Series(cal.dayofweek).value_counts()
    rows, chart = [], []
    for i, dn in enumerate(DAYS):
        d = df[df.dow == i]
        s = stats(d)
        chart.append(s)
        rows.append([dn, s["Trades"], f_pct(s["WinRate"]), f_num(s["AvgPlannedRR"]), f_num(s["AvgR"], 3),
                     (f_usd(s["NetUSD"]), s["NetUSD"]), f_pct(s["NetUSD"] / tot * 100) if tot else "-",
                     f_num(s["Trades"] / occ.get(i, 1), 2), fmt_dur(s["AvgWinMin"]), fmt_dur(s["AvgLossMin"])])
    out.append("<h3>By day of week (setup day)</h3>" + html_table(
        rows, ["Day", "Trades", "Win rate", "Avg RR", "Expectancy R", "Net P&L", "% of profit", "Avg trades / day", "Avg win dur", "Avg loss dur"]))
    fig = make_subplots(rows=1, cols=2, subplot_titles=("Net P&L by weekday", "Avg trades per day (frequency)"))
    fig.add_trace(go.Bar(x=DAYS, y=[c["NetUSD"] for c in chart], marker_color=[GREEN if c["NetUSD"] >= 0 else RED for c in chart]), 1, 1)
    fig.add_trace(go.Bar(x=DAYS, y=[c["Trades"] / occ.get(i, 1) for i, c in enumerate(chart)], marker_color=BLUE), 1, 2)
    fig.update_layout(showlegend=False)
    out.append(fig_html(fig, 340))

    # --- monthly equity grids
    months = pd.period_range("2023-04", max(df.month.max(), pd.Period("2026-09")), freq="M")
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
    out.append("<h3>Cumulative P&L by month (title = net P&L, trade count; month of exit)</h3>")
    out.append(fig_html(fig, 210 * nr))

    # monthly bar
    mm = df.groupby("month").pnl_usd.sum().reindex(months, fill_value=0)
    fig = go.Figure(go.Bar(x=[m.strftime("%b %y") for m in months], y=mm.values,
                           marker_color=[GREEN if v >= 0 else RED for v in mm.values]))
    fig.update_layout(title="Monthly net P&L ($)")
    out.append(fig_html(fig, 340))
    return "".join(out)


def section_C(df):
    d = df.copy()
    d["dist"] = (d.entry_price - d.touch_level).abs()
    d["bucket"] = (d.dist // DIST_STEP).astype(int) * DIST_STEP
    rows, ch = [], []
    for b in range(0, int(d.bucket.max()) + DIST_STEP, DIST_STEP):
        g = d[d.bucket == b]
        s = stats(g)
        lbl = f"{b}-{b + DIST_STEP}"
        ch.append((lbl, s))
        gl, gs = stats(g[g.side == "LONG"]), stats(g[g.side == "SHORT"])
        rows.append([lbl, s["Trades"], s["Wins"], s["Losses"], f_pct(s["WinRate"]), f_num(s["AvgR"], 3),
                     (f_usd(s["NetUSD"]), s["NetUSD"]), f"{gl['Trades']} / {f_pct(gl['WinRate'])}", f"{gs['Trades']} / {f_pct(gs['WinRate'])}"])
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=[a for a, _ in ch], y=[s["Wins"] for _, s in ch], name="WIN", marker_color=GREEN))
    fig.add_trace(go.Bar(x=[a for a, _ in ch], y=[s["Losses"] for _, s in ch], name="LOSS", marker_color=RED))
    fig.add_trace(go.Scatter(x=[a for a, _ in ch], y=[s["WinRate"] for _, s in ch], name="Win rate %", mode="lines+markers",
                             line=dict(color="#222")), secondary_y=True)
    fig.update_layout(barmode="stack", title=f"|entry_price - touch_level| in {DIST_STEP}-pt steps: wins / losses and win rate")
    fig.update_xaxes(title="distance (points)")
    base = stats(d)
    return (fig_html(fig, 430) + html_table(rows, ["Distance", "Trades", "Wins", "Losses", "Win rate", "Expectancy R", "Net P&L",
                                                 "LONG trades / WR", "SHORT trades / WR"])
            + f"<p class='note'>Baseline win rate {f_pct(base['WinRate'])}. Small buckets are noisy &mdash; check the Trades column.</p>")


def section_D(df):
    out = []
    for col, ttl in (("setup_slot", "setup_start_ist"), ("entry_slot", "entry_time_ist")):
        w = df[df.result == "WIN"].groupby(col).size().reindex(range(48), fill_value=0)
        l = df[df.result == "LOSS"].groupby(col).size().reindex(range(48), fill_value=0)
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Bar(x=SLOT_LABELS, y=w.values, name="WIN", marker_color=GREEN))
        fig.add_trace(go.Bar(x=SLOT_LABELS, y=l.values, name="LOSS", marker_color=RED))
        wr = (w / (w + l).replace(0, np.nan) * 100)
        fig.add_trace(go.Scatter(x=SLOT_LABELS, y=wr.values, name="Win rate %", mode="lines+markers", line=dict(color="#222")),
                      secondary_y=True)
        fig.update_layout(barmode="stack", title=f"Trade count by half-hour of {ttl} (IST)")
        fig.update_xaxes(tickangle=-90, tickfont=dict(size=9))
        out.append(fig_html(fig, 400))
    # side split
    fig = go.Figure()
    for side, col in (("LONG", GREEN), ("SHORT", RED)):
        c = df[df.side == side].groupby("setup_slot").size().reindex(range(48), fill_value=0)
        fig.add_trace(go.Bar(x=SLOT_LABELS, y=c.values, name=side, marker_color=col))
    fig.update_layout(barmode="group", title="Setup start half-hour by side")
    fig.update_xaxes(tickangle=-90, tickfont=dict(size=9))
    out.append(fig_html(fig, 360))
    return "".join(out)


def round_hits(lo, hi, step):
    lo, hi = min(lo, hi), max(lo, hi)
    k0, k1 = int(np.ceil(lo / step)), int(np.floor(hi / step))
    return [k * step for k in range(k0, k1 + 1)]


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
        return [label, s["Trades"], s["Wins"], s["Losses"], f_pct(s["WinRate"]), f_num(s["AvgR"], 3), (f_usd(s["NetUSD"]), s["NetUSD"])]

    hdr = ["Condition", "Trades", "WIN", "LOSS", "Win rate", "Expectancy R", "Net P&L"]
    A = [row("Any 500x between entry & TARGET", d.tp_round), row("&nbsp;&nbsp;1000x between entry & TARGET", d.tp_1000),
         row("&nbsp;&nbsp;500x only (not 1000x) between entry & TARGET", d.tp_500only), row("No round no. between entry & TARGET", ~d.tp_round),
         row("Any 500x between entry & SL", d.sl_round), row("&nbsp;&nbsp;1000x between entry & SL", d.sl_1000),
         row("&nbsp;&nbsp;500x only (not 1000x) between entry & SL", d.sl_500only), row("No round no. between entry & SL", ~d.sl_round)]
    B = [row("Round in TARGET path only", d.tp_round & ~d.sl_round), row("Round in SL path only", ~d.tp_round & d.sl_round),
         row("Round in BOTH paths", d.tp_round & d.sl_round), row("Round in NEITHER path", ~d.tp_round & ~d.sl_round)]
    C = []
    for side in ("LONG", "SHORT"):
        s_ = d.side == side
        C += [row(f"{side}: round in TARGET path", s_ & d.tp_round), row(f"{side}: round in SL path", s_ & d.sl_round),
              row(f"{side}: no round in either", s_ & ~d.tp_round & ~d.sl_round)]
    fig = make_subplots(rows=1, cols=2, subplot_titles=("Round number between entry & TARGET", "Round number between entry & SL"))
    for i, col in enumerate(("tp_round", "sl_round"), 1):
        for flag, lab in ((True, "round present"), (False, "no round")):
            g = d[d[col] == flag]
            fig.add_trace(go.Bar(x=["WIN", "LOSS"], y=[(g.result == "WIN").sum(), (g.result == "LOSS").sum()],
                                 name=lab, marker_color=BLUE if flag else "#c5c9d1", showlegend=(i == 1)), 1, i)
    fig.update_layout(barmode="group")
    return (fig_html(fig, 340) + "<h3>Round number in each path</h3>" + html_table(A, hdr)
            + "<h3>Combined</h3>" + html_table(B, hdr) + "<h3>By side</h3>" + html_table(C, hdr)
            + f"<p class='note'>Round numbers = multiples of {ROUND_STEP} (so every 1000 is included); a level exactly on the entry/TP/SL price counts as between.</p>")


# ----------------------------------------------------------------- page
CSS = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f5f6f8;color:#1d2330}
main{max-width:1500px;margin:0 auto;padding:20px}
h1{margin:0 0 4px} h2{margin:36px 0 10px;padding:8px 12px;background:#1d2330;color:#fff;border-radius:6px;font-size:18px}
h3{margin:22px 0 8px;font-size:15px} .sub{color:#6b7280;margin-bottom:10px}
section{background:#fff;border-radius:8px;padding:6px 16px 16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin:14px 0}
.kpi{background:#f5f6f8;border-radius:6px;padding:10px 12px}.kl{font-size:11px;color:#6b7280}.kv{font-size:20px;font-weight:600}
.pos{color:#1b9e77}.neg{color:#d95f02}
.tw{overflow-x:auto}table{border-collapse:collapse;font-size:12.5px;width:100%}
th,td{padding:5px 9px;border-bottom:1px solid #e5e7eb;text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left;position:sticky;left:0;background:#fff}
th{background:#f0f2f5;font-size:11.5px}table.wide th,table.wide td{padding:4px 6px;font-size:11px}
.chip{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;margin-right:4px}
.note{color:#6b7280;font-size:12px}.legend{margin:10px 0 0}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default="/mnt/user-data/uploads/attachment.txt")
    ap.add_argument("--rules", default="/mnt/user-data/uploads/attachment-d3fce4c1.txt")
    ap.add_argument("--out", default="dashboard.html")
    a = ap.parse_args()

    df = load_trades(a.trades)
    sched, days, combos = parse_rules(a.rules)
    plotly_js = open(os.path.join(os.path.dirname(plotly.__file__), "package_data", "plotly.min.js"), encoding="utf-8").read()

    secs = [("A. Setup constraints - BUY &amp; SELL, AVOID vs TRADE, by time and day", section_A(sched, days, combos)),
            ("B. Performance analysis", section_B(df)),
            ("C. Win / loss by |entry price - touch level|", section_C(df)),
            ("D. Setup / entry time distribution (half-hour)", section_D(df)),
            ("E. Round-number analysis (500 / 1000 multiples)", section_E(df))]
    body = "".join(f"<h2>{t}</h2><section>{h}</section>" for t, h in secs)
    sub = (f"{len(df):,} trades &middot; {df.setup_start_ist.min():%d %b %Y} &rarr; {df.exit_time_ist.max():%d %b %Y} (IST) &middot; "
           f"${RISK_USD:,} risk per trade on ${ACCOUNT_USD:,} account &middot; generated {datetime.now():%Y-%m-%d %H:%M}")
    html = (f"<!doctype html><html><head><meta charset='utf-8'><title>Backtest dashboard</title><style>{CSS}</style>"
            f"<script>{plotly_js}</script></head><body><main><h1>Backtest performance dashboard</h1>"
            f"<div class='sub'>{sub}</div>{body}</main></body></html>")
    open(a.out, "w", encoding="utf-8").write(html)
    print("wrote", a.out, f"{os.path.getsize(a.out) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
