# Statistical Edge Backtest

## Data

The backtest reads Binance BTCUSDT Spot candles from:

`src/main/java/com/template/binance_csv/datewise_binance_csv_spot/YYYY-MM-DD/<timeframe>/BTCUSDT-<timeframe>-YYYY-MM-DD.csv`

Required timeframes are `1m`, `30m`, and `1h`. Candle timestamps are Unix milliseconds; entry-hour and weekday filters use `Asia/Kolkata` (IST). The downloader covers 2017-08-17 through 2026-10-01 by default and extracts CSVs into the folder above. This is a separate Spot dataset; the previous datewise Futures data is not overwritten or mixed into the run.

Run `bash download_binance.sh` from the project root to download the configured history. Defaults are 2017-08-17 through 2026-10-01; set `START_DATE` and/or `END_DATE` in the environment to override them. The downloader is resumable: existing nonempty CSV files are skipped. Missing archive dates are recorded separately from failed downloads.

Downloader settings: `SYMBOL=BTCUSDT`, Spot daily klines, `TIMEFRAMES=(1m, 30m, 1h)`, and up to 5 download attempts per file. Each curl request has a 20-second connect timeout and a 300-second maximum transfer time, with up to 2 curl-level retries.

## Strategy

For each 30-minute candle after the first, levels are calculated from the previous 30-minute candle and the 1-hour candle containing the new 30-minute window:

- 1-hour upper level: hourly open + 120 points.
- 1-hour lower level: hourly open - 120 points.
- Long touch level: previous 30-minute high - 360 points.
- Short touch level: previous 30-minute low + 360 points.

BUY setup: the long touch level must be below the 1-hour lower level. During that 30-minute window, a 1-minute low must touch/break the long touch level. A later 1-minute candle must reach the 1-hour lower level; entry is modeled at that level.

SELL setup: the short touch level must be above the 1-hour upper level. During that 30-minute window, a 1-minute high must touch/break the short touch level. A later 1-minute candle must reach the 1-hour upper level; entry is modeled at that level.

The touch and entry must occur on separate 1-minute candles. A setup expires at the end of its 30-minute window if it has not entered. Once entered, the position remains open across later windows until its stop or target is hit, or the available data ends.

## Schedule Filters

The scheduled strategy selects its SL/TP by the entry candle's IST hour. Hour ranges are start-inclusive and end-exclusive. `AVOID` and unlisted hours do not trade. The weekday exclusions apply in addition to the hour schedule.

### BUY hour rules

| IST hour | Rule |
| --- | --- |
| 00:00-01:00 | SL 270 / TP 300 |
| 01:00-02:00 | AVOID |
| 02:00-03:00 | SL 120 / TP 300 |
| 03:00-04:00 | SL 270 / TP 300 |
| 04:00-05:00 | Unlisted, skip |
| 05:00-06:00 | AVOID |
| 06:00-07:00 | Unlisted, skip |
| 07:00-08:00 | SL 270 / TP 300 |
| 08:00-09:00 | AVOID |
| 09:00-10:00 | Unlisted, skip |
| 10:00-11:00 | SL 270 / TP 300 |
| 11:00-12:00 | SL 70 / TP 300 |
| 12:00-13:00 | SL 120 / TP 300 |
| 13:00-15:00 | AVOID |
| 15:00-16:00 | SL 70 / TP 300 |
| 16:00-17:00 | Unlisted, skip |
| 17:00-18:00 | SL 120 / TP 300 |
| 18:00-19:00 | AVOID |
| 19:00-20:00 | SL 270 / TP 300 |
| 20:00-21:00 | SL 120 / TP 300 |
| 21:00-22:00 | Unlisted, skip |
| 22:00-23:00 | SL 270 / TP 300 |
| 23:00-00:00 | SL 120 / TP 300 |

BUY Monday is avoided.

### SELL hour rules

| IST hour | Rule |
| --- | --- |
| 00:00-01:00 | SL 70 / TP 400 |
| 01:00-03:00 | Unlisted, skip |
| 03:00-06:00 | AVOID |
| 06:00-07:00 | SL 70 / TP 800 |
| 07:00-08:00 | SL 70 / TP 400 |
| 08:00-09:00 | SL 220 / TP 800 |
| 09:00-11:00 | Unlisted, skip |
| 11:00-12:00 | AVOID |
| 12:00-13:00 | SL 70 / TP 400 |
| 13:00-14:00 | AVOID |
| 14:00-15:00 | SL 70 / TP 600 |
| 15:00-16:00 | Unlisted, skip |
| 16:00-17:00 | SL 220 / TP 800 |
| 17:00-18:00 | Unlisted, skip |
| 18:00-19:00 | SL 70 / TP 800 |
| 19:00-20:00 | AVOID |
| 20:00-21:00 | SL 220 / TP 800 |
| 21:00-22:00 | SL 220 / TP 800 |
| 22:00-23:00 | SL 70 / TP 400 |
| 23:00-00:00 | SL 220 / TP 800 |

SELL Saturday and Sunday are avoided.

## Backtest Constraints and Variables

- Candle execution timeframe: 1 minute.
- Setup timeframe: 30 minutes; one-hour reference: 1 hour.
- Level offsets: 120 points from hourly open and 360 points from prior 30-minute high/low.
- Exactly one position may be open at a time in each evaluation. Signals arriving while a position is open are skipped.
- Exit scanning begins after the entry candle; the entry candle itself is not also checked for SL/TP.
- If a later candle reaches both stop and target, the stop is counted first.
- An unclosed position at the end of available data is counted as open; it contributes no realized points.
- P&L is reported in raw price points, before commissions, funding, spread, and slippage.
- Win rate is wins divided by closed trades. Expectancy is net realized points divided by entries, including any open entry in the denominator.
- The exploratory grid constants are SL 70 to 370 in 50-point steps and TP 200 to 1,000 in 100-point steps. The specific combo comparison uses BUY 70/300, 120/300, 270/300 and SELL 70/400, 70/600, 70/800, 220/800.

## Output

`Main` continues to print the candidate entries and unfiltered exploratory/combo reports. The scheduled summary and its `dimension,bucket,entries,wins,losses,open,win_rate_pct,net_points,expectancy_points` rows are appended to `backtest_schedule.log` in the project root on every run.

Build with `mvn clean test`, then run with `java -cp target/classes com.template.Main` or launch `com.template.Main` from the IDE.
