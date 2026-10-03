package com.template;

import java.io.IOException;
import java.time.Instant;
import java.time.DayOfWeek;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.EnumSet;
import java.util.EnumMap;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;

public class Main {
    private static final long ONE_MINUTE_MS = 60_000L;
    private static final long THIRTY_MINUTES_MS = 30 * ONE_MINUTE_MS;
    private static final long ONE_HOUR_MS = 60 * ONE_MINUTE_MS;
    private static final int MIN_STOP_POINTS = 70;
    private static final int MAX_STOP_POINTS_EXCLUSIVE = 400;
    private static final int STOP_STEP_POINTS = 50;
    private static final int MIN_TARGET_POINTS = 200;
    private static final int MAX_TARGET_POINTS = 1000;
    private static final int TARGET_STEP_POINTS = 100;
    private static final DateTimeFormatter IST_FORMAT = DateTimeFormatter
            .ofPattern("yyyy-MM-dd HH:mm")
            .withZone(ZoneId.of("Asia/Kolkata"));
    private static final List<StopTarget> BUY_COMBINATIONS = List.of(
            new StopTarget(70, 300), new StopTarget(270, 300));
    private static final List<StopTarget> SELL_COMBINATIONS = List.of(
            new StopTarget(70, 400), new StopTarget(70, 600),
            new StopTarget(70, 800), new StopTarget(220, 800));
        private static final ScheduleRule AVOID = new ScheduleRule(true, 0, 0);
        private static final Map<Integer, ScheduleRule> BUY_TIME_RULES = buildSchedule(
            timeRule("00:00", "00:30", AVOID),
            timeRule("00:30", "01:00", tradeRule(70, 300)),
            timeRule("01:00", "01:30", AVOID),
            timeRule("01:30", "03:30", tradeRule(70, 300)),
            timeRule("03:30", "04:00", AVOID),
            timeRule("04:00", "05:00", tradeRule(70, 300)),
            timeRule("05:00", "05:30", AVOID),
            timeRule("05:30", "07:00", tradeRule(270, 300)),
            timeRule("07:00", "09:00", tradeRule(70, 300)),
            timeRule("09:00", "09:30", AVOID),
            timeRule("09:30", "10:00", tradeRule(70, 300)),
            timeRule("10:00", "11:00", tradeRule(70, 300)),
            timeRule("11:00", "11:30", AVOID),
            timeRule("11:30", "13:30", tradeRule(70, 300)),
            timeRule("13:30", "16:00", AVOID),
            timeRule("16:00", "17:00", tradeRule(70, 300)),
            timeRule("17:00", "17:30", AVOID),
            timeRule("17:30", "18:30", tradeRule(70, 300)),
            timeRule("18:30", "19:00", AVOID),
            timeRule("19:00", "19:30", tradeRule(70, 300)),
            timeRule("19:30", "20:00", AVOID),
            timeRule("20:00", "20:30", AVOID),
            timeRule("20:30", "22:30", tradeRule(70, 300)),
            timeRule("22:30", "23:00", tradeRule(270, 300)),
            timeRule("23:00", "24:00", tradeRule(70, 300)));
        private static final Map<Integer, ScheduleRule> SELL_TIME_RULES = buildSchedule(
            timeRule("00:00", "00:30", tradeRule(220, 800)),
            timeRule("00:30", "01:00", tradeRule(70, 800)),
            timeRule("01:00", "01:30", AVOID),
            timeRule("01:30", "02:00", tradeRule(70, 800)),
            timeRule("02:00", "03:00", AVOID),
            timeRule("03:00", "03:30", tradeRule(70, 600)),
            timeRule("03:30", "04:00", AVOID),
            timeRule("04:00", "04:30", tradeRule(70, 600)),
            timeRule("04:30", "05:30", AVOID),
            timeRule("05:30", "06:00", tradeRule(70, 600)),
            timeRule("06:00", "06:30", tradeRule(70, 800)),
            timeRule("06:30", "07:00", tradeRule(70, 400)),
            timeRule("07:00", "07:30", tradeRule(70, 800)),
            timeRule("07:30", "08:00", tradeRule(70, 400)),
            timeRule("08:00", "08:30", AVOID),
            timeRule("08:30", "09:00", tradeRule(70, 800)),
            timeRule("09:00", "09:30", AVOID),
            timeRule("09:30", "10:00", AVOID),
            timeRule("10:00", "11:00", AVOID),
            timeRule("11:00", "11:30", tradeRule(70, 600)),
            timeRule("11:30", "12:30", AVOID),
            timeRule("12:30", "13:00", tradeRule(70, 600)),
            timeRule("13:00", "14:00", tradeRule(70, 400)),
            timeRule("14:00", "16:30", AVOID),
            timeRule("16:30", "17:00", tradeRule(70, 600)),
            timeRule("17:00", "17:30", tradeRule(70, 800)),
            timeRule("17:30", "18:00", tradeRule(220, 800)),
            timeRule("18:00", "19:00", tradeRule(70, 800)),
            timeRule("19:00", "19:30", tradeRule(70, 600)),
            timeRule("19:30", "20:00", AVOID),
            timeRule("20:00", "20:30", tradeRule(70, 400)),
            timeRule("20:30", "21:00", AVOID),
            timeRule("21:00", "21:30", tradeRule(70, 400)),
            timeRule("21:30", "23:00", tradeRule(70, 400)),
            timeRule("23:00", "23:30", AVOID),
            timeRule("23:30", "24:00", tradeRule(70, 600)));
        private static final Map<Side, EnumSet<DayOfWeek>> BEST_DAYS = Map.of(
            Side.LONG, EnumSet.of(DayOfWeek.THURSDAY, DayOfWeek.MONDAY,
                DayOfWeek.TUESDAY, DayOfWeek.SUNDAY),
            Side.SHORT, EnumSet.of(DayOfWeek.MONDAY, DayOfWeek.WEDNESDAY,
                DayOfWeek.SUNDAY));

    public static void main(String[] args) throws IOException {
        List<List<String>> candles1h = dataRows(Csvreader.read1hCSV());
        List<List<String>> candles30m = dataRows(Csvreader.read30mCSV());
        List<List<String>> candles1m = dataRows(Csvreader.read1mCSV());

        Map<Long, Double> hourlyOpen = new HashMap<>();
        for (List<String> candle : candles1h) {
            hourlyOpen.put(Long.parseLong(candle.get(0)), Double.parseDouble(candle.get(1)));
        }

        List<Candle> minuteCandles = new ArrayList<>();
        for (List<String> candle : candles1m) {
            minuteCandles.add(new Candle(
                    Long.parseLong(candle.get(0)),
                    Double.parseDouble(candle.get(1)),
                    Double.parseDouble(candle.get(2)),
                    Double.parseDouble(candle.get(3)),
                    Double.parseDouble(candle.get(4))));
        }

        List<Signal> signals = buildSignals(candles30m, minuteCandles, hourlyOpen);
        printEntryCandles(signals, minuteCandles);
        runGridSearch(signals, minuteCandles);
        runRequestedCombinations(signals, minuteCandles);
        runScheduledBacktest(signals, minuteCandles);
    }

    private static void printEntryCandles(List<Signal> signals, List<Candle> candles) {
        System.out.println("\nCandidate entry candles (IST):");
        System.out.println("entry_time_ist,side,30m_prev_high_minus_360,1h_open_minus_120,30m_prev_low_plus_360,1h_open_plus_120");
        for (Signal signal : signals) {
            Candle candle = candles.get(signal.entryIndex);
            String longTouchLevel = signal.side == Side.LONG
                ? formatPrice(signal.thirtyMinuteLevel) : "";
            String longEntryLevel = signal.side == Side.LONG
                ? formatPrice(signal.entryPrice) : "";
            String shortTouchLevel = signal.side == Side.SHORT
                ? formatPrice(signal.thirtyMinuteLevel) : "";
            String shortEntryLevel = signal.side == Side.SHORT
                ? formatPrice(signal.entryPrice) : "";
            System.out.printf(Locale.ROOT, "%s,%s,%s,%s,%s,%s%n",
                formatIst(candle.timestamp), signal.side, longTouchLevel,
                longEntryLevel, shortTouchLevel, shortEntryLevel);
        }
    }

    private static String formatIst(long timestamp) {
        return IST_FORMAT.format(Instant.ofEpochMilli(timestamp));
    }

    private static String formatPrice(double price) {
        return String.format(Locale.ROOT, "%.2f", price);
    }

    private static List<Signal> buildSignals(List<List<String>> candles30m,
                                            List<Candle> minuteCandles,
                                            Map<Long, Double> hourlyOpen) {
        List<Signal> signals = new ArrayList<>();
        for (int setupIndex = 1; setupIndex < candles30m.size(); setupIndex++) {
            List<String> previousThirtyMinute = candles30m.get(setupIndex - 1);
            long setupStart = Long.parseLong(candles30m.get(setupIndex).get(0));
            long setupEnd = setupStart + THIRTY_MINUTES_MS;
            long hourStart = Math.floorDiv(setupStart, ONE_HOUR_MS) * ONE_HOUR_MS;
            Double oneHourOpen = hourlyOpen.get(hourStart);
            if (oneHourOpen == null) {
                continue;
            }

            double oneHourHighLevel = oneHourOpen + 120;
            double oneHourLowLevel = oneHourOpen - 120;
            double previousThirtyMinuteHigh = Double.parseDouble(previousThirtyMinute.get(2));
            double previousThirtyMinuteLow = Double.parseDouble(previousThirtyMinute.get(3));
            double longSupport = previousThirtyMinuteHigh - 360;
            double shortResistance = previousThirtyMinuteLow + 360;

            if (longSupport < oneHourLowLevel) {
                int entryIndex = findEntryIndex(minuteCandles, setupStart, setupEnd,
                        longSupport, oneHourLowLevel, Side.LONG);
                if (entryIndex >= 0) {
                    signals.add(new Signal(setupStart, entryIndex, Side.LONG,
                        longSupport, oneHourLowLevel));
                }
            }

            if (shortResistance > oneHourHighLevel) {
                int entryIndex = findEntryIndex(minuteCandles, setupStart, setupEnd,
                        shortResistance, oneHourHighLevel, Side.SHORT);
                if (entryIndex >= 0) {
                    signals.add(new Signal(setupStart, entryIndex, Side.SHORT,
                        shortResistance, oneHourHighLevel));
                }
            }
        }

        signals.sort(Comparator.comparingInt(Signal::entryIndex)
                .thenComparing(signal -> signal.side));
        return signals;
    }

    private static int findEntryIndex(List<Candle> candles, long setupStart, long setupEnd,
                                      double touchLevel, double entryLevel, Side side) {
        int low = 0;
        int high = candles.size();
        while (low < high) {
            int middle = low + (high - low) / 2;
            if (candles.get(middle).timestamp < setupStart) {
                low = middle + 1;
            } else {
                high = middle;
            }
        }

        int touchIndex = -1;
        for (int i = low; i < candles.size(); i++) {
            Candle candle = candles.get(i);
            if (candle.timestamp >= setupEnd) {
                break;
            }

            if (touchIndex < 0) {
                boolean touched = side == Side.LONG
                        ? candle.low <= touchLevel
                        : candle.high >= touchLevel;
                if (touched) {
                    touchIndex = i;
                }
            } else if (i > touchIndex) {
                boolean entered = side == Side.LONG
                        ? candle.high >= entryLevel
                        : candle.low <= entryLevel;
                if (entered) {
                    return i;
                }
            }
        }
        return -1;
    }

    private static void runGridSearch(List<Signal> signals, List<Candle> candles) {
        List<Evaluation> longResults = new ArrayList<>();
        List<Evaluation> shortResults = new ArrayList<>();
        List<Evaluation> combinedResults = new ArrayList<>();

        for (int stopPoints = MIN_STOP_POINTS;
             stopPoints < MAX_STOP_POINTS_EXCLUSIVE;
             stopPoints += STOP_STEP_POINTS) {
            for (int targetPoints = MIN_TARGET_POINTS;
                 targetPoints <= MAX_TARGET_POINTS;
                 targetPoints += TARGET_STEP_POINTS) {
                longResults.add(evaluate(signals, candles, Side.LONG, stopPoints, targetPoints));
                shortResults.add(evaluate(signals, candles, Side.SHORT, stopPoints, targetPoints));
                combinedResults.add(evaluate(signals, candles, Side.BOTH, stopPoints, targetPoints));
            }
        }

        Comparator<Evaluation> profitabilityOrder = Comparator
                .comparingDouble(Evaluation::netPnlPoints).reversed()
                .thenComparing(Comparator.comparingDouble(Evaluation::netR).reversed())
                .thenComparing(Comparator.comparingDouble(Evaluation::winRate).reversed());
        longResults.sort(profitabilityOrder);
        shortResults.sort(profitabilityOrder);
        combinedResults.sort(profitabilityOrder);

        System.out.println("signals,long=" + countSignals(signals, Side.LONG)
                + ",short=" + countSignals(signals, Side.SHORT)
                + ",total=" + signals.size());
        printResults("LONG", longResults);
        printResults("SHORT", shortResults);
        printResults("COMBINED", combinedResults);
    }

    private static void runRequestedCombinations(List<Signal> signals, List<Candle> candles) {
        printRequestedSide("BUY", Side.LONG, BUY_COMBINATIONS, signals, candles);
        printRequestedSide("SELL", Side.SHORT, SELL_COMBINATIONS, signals, candles);
    }

    private static ScheduleRule tradeRule(int stopPoints, int targetPoints) {
        return new ScheduleRule(false, stopPoints, targetPoints);
    }

    private static ScheduleEntry timeRule(String start, String end, ScheduleRule rule) {
        return new ScheduleEntry(toHalfHourSlot(start), toHalfHourSlot(end), rule);
    }

    private static Map<Integer, ScheduleRule> buildSchedule(ScheduleEntry... entries) {
        Map<Integer, ScheduleRule> schedule = new HashMap<>();
        for (ScheduleEntry entry : entries) {
            for (int slot = entry.startSlot; slot < entry.endSlot; slot++) {
                if (schedule.put(slot, entry.rule) != null) {
                    throw new IllegalArgumentException("Overlapping schedule at half-hour slot " + slot);
                }
            }
        }
        if (schedule.size() != 48) {
            throw new IllegalArgumentException("Schedule must define all 48 half-hour slots");
        }
        return Map.copyOf(schedule);
    }

    private static int toHalfHourSlot(String time) {
        if (time.equals("24:00")) {
            return 48;
        }
        String[] parts = time.split(":");
        int hour = Integer.parseInt(parts[0]);
        int minute = Integer.parseInt(parts[1]);
        if (hour < 0 || hour > 23 || (minute != 0 && minute != 30)) {
            throw new IllegalArgumentException("Invalid half-hour boundary: " + time);
        }
        return hour * 2 + minute / 30;
    }

    private static boolean isExcludedStopDay(Side side, ScheduleRule rule, DayOfWeek day) {
        if (side == Side.LONG) {
            return (rule.stopPoints == 70 && day == DayOfWeek.SATURDAY)
                    || (rule.stopPoints == 270 && day == DayOfWeek.WEDNESDAY);
        }
        return day == DayOfWeek.FRIDAY || day == DayOfWeek.SATURDAY;
    }

        private static void runScheduledBacktest(List<Signal> signals, List<Candle> candles)
            throws IOException {
        List<ScheduledSignal> scheduledSignals = new ArrayList<>();
        int avoidedByHour = 0;
        int unlistedTimes = 0;
        int avoidedByDay = 0;

        for (Signal signal : signals) {
            ZonedDateTime entryTime = Instant.ofEpochMilli(
                    candles.get(signal.entryIndex).timestamp).atZone(ZoneId.of("Asia/Kolkata"));
            Map<Integer, ScheduleRule> timeRules = signal.side == Side.LONG
                    ? BUY_TIME_RULES : SELL_TIME_RULES;
            ScheduleRule rule = timeRules.get(halfHourBucket(entryTime));
            if (rule == null) {
                unlistedTimes++;
            } else if (rule.avoid) {
                avoidedByHour++;
            } else if (!BEST_DAYS.get(signal.side).contains(entryTime.getDayOfWeek())
                    || isExcludedStopDay(signal.side, rule, entryTime.getDayOfWeek())) {
                avoidedByDay++;
            } else {
                scheduledSignals.add(new ScheduledSignal(signal, rule));
            }
        }

        int entries = 0;
        int wins = 0;
        int losses = 0;
        int openTrades = 0;
        int skippedWhileOpen = 0;
        int nextAvailableIndex = -1;
        double netPnlPoints = 0;
        double netR = 0;
        Map<DayOfWeek, BucketStats> weekdayStats = new EnumMap<>(DayOfWeek.class);
        Map<Integer, BucketStats> halfHourStats = new TreeMap<>();
        List<TakenTrade> takenTrades = new ArrayList<>();

        for (ScheduledSignal scheduled : scheduledSignals) {
            Signal signal = scheduled.signal;
            if (signal.entryIndex <= nextAvailableIndex) {
                skippedWhileOpen++;
                continue;
            }

            entries++;
            Exit exit = findExit(candles, signal,
                    scheduled.rule.stopPoints, scheduled.rule.targetPoints);
            nextAvailableIndex = exit == null ? candles.size() : exit.candleIndex;
                takenTrades.add(new TakenTrade(signal, scheduled.rule, exit));

            if (exit == null) {
                openTrades++;
            } else {
                if (exit.win) {
                    wins++;
                } else {
                    losses++;
                }
                netPnlPoints += exit.pnlPoints;
                netR += exit.pnlPoints / scheduled.rule.stopPoints;
            }

            ZonedDateTime entryTime = Instant.ofEpochMilli(
                    candles.get(signal.entryIndex).timestamp).atZone(ZoneId.of("Asia/Kolkata"));
            weekdayStats.computeIfAbsent(entryTime.getDayOfWeek(), ignored -> new BucketStats())
                    .add(exit);
                halfHourStats.computeIfAbsent(halfHourBucket(entryTime), ignored -> new BucketStats())
                    .add(exit);
        }

        int closedTrades = wins + losses;
        double winRate = closedTrades == 0 ? 0 : wins * 100.0 / closedTrades;
        double expectancy = entries == 0 ? 0 : netPnlPoints / entries;
        String summary = String.format(Locale.ROOT,
            "signals=%d,eligible_by_schedule=%d,entries=%d,wins=%d,losses=%d,open=%d,skipped_while_open=%d,avoided_by_time=%d,avoided_by_day=%d,unlisted_times=%d,win_rate_pct=%.2f,net_points=%.2f,net_R=%.2f,expectancy_points=%.2f",
            signals.size(), scheduledSignals.size(), entries, wins, losses, openTrades,
            skippedWhileOpen, avoidedByHour, avoidedByDay, unlistedTimes,
            winRate, netPnlPoints, netR, expectancy);
        appendScheduleLog(summary, takenTrades, weekdayStats, halfHourStats,
            signals, candles);
        System.out.println("Scheduled strategy summary and dimensions logged to backtest_schedule.log");
    }

        private static void appendScheduleLog(String summary, List<TakenTrade> takenTrades,
                         Map<DayOfWeek, BucketStats> weekdayStats,
                         Map<Integer, BucketStats> halfHourStats,
                         List<Signal> signals, List<Candle> candles)
            throws IOException {
        StringBuilder logEntry = new StringBuilder()
            .append("TRADES TAKEN (IST):\n")
            .append("trade,setup_start_ist,side,entry_time_ist,entry_price,touch_level,sl_points,tp_points,stop_price,target_price,exit_time_ist,exit_price,result,pnl_points\n");

        for (int i = 0; i < takenTrades.size(); i++) {
            TakenTrade trade = takenTrades.get(i);
            Signal signal = trade.signal;
            ScheduleRule rule = trade.rule;
            Candle entryCandle = candles.get(signal.entryIndex);
            double stopPrice = signal.side == Side.LONG
                ? signal.entryPrice - rule.stopPoints
                : signal.entryPrice + rule.stopPoints;
            double targetPrice = signal.side == Side.LONG
                ? signal.entryPrice + rule.targetPoints
                : signal.entryPrice - rule.targetPoints;
            String exitTime = trade.exit == null ? ""
                : formatIst(candles.get(trade.exit.candleIndex).timestamp);
            String exitPrice = trade.exit == null ? ""
                : formatPrice(signal.side == Side.LONG
                    ? signal.entryPrice + trade.exit.pnlPoints
                    : signal.entryPrice - trade.exit.pnlPoints);
            String result = trade.exit == null ? "OPEN" : trade.exit.win ? "WIN" : "LOSS";
            String pnl = trade.exit == null ? "" : formatPrice(trade.exit.pnlPoints);
            logEntry.append(String.format(Locale.ROOT,
                "%d,%s,%s,%s,%.2f,%.2f,%d,%d,%.2f,%.2f,%s,%s,%s,%s%n",
                i + 1, formatIst(signal.setupStart), signal.side,
                formatIst(entryCandle.timestamp), signal.entryPrice,
                signal.thirtyMinuteLevel, rule.stopPoints, rule.targetPoints,
                stopPrice, targetPrice, exitTime, exitPrice, result, pnl));
        }

        logEntry.append("\nSCHEDULE-FILTERED STRATEGY (IST):\n")
            .append(summary).append('\n')
            .append("dimension,bucket,entries,wins,losses,open,win_rate_pct,net_points,expectancy_points\n");

        for (DayOfWeek day : DayOfWeek.values()) {
            BucketStats stats = weekdayStats.get(day);
            if (stats != null) {
            appendCalendarLogRow(logEntry, "weekday", day.toString(), stats);
            }
        }
        for (Map.Entry<Integer, BucketStats> entry : halfHourStats.entrySet()) {
            appendCalendarLogRow(logEntry, "half_hour_ist",
                halfHourLabel(entry.getKey()), entry.getValue());
        }
        logEntry.append('\n');
        appendComboAnalysisLog(logEntry, "BUY", Side.LONG,
            BUY_COMBINATIONS, signals, candles);
        appendComboAnalysisLog(logEntry, "SELL", Side.SHORT,
            SELL_COMBINATIONS, signals, candles);
        logEntry.append('\n');

        Files.writeString(Path.of("backtest_schedule.log"), logEntry,
            StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        }

        private static void appendComboAnalysisLog(StringBuilder output, String label, Side side,
                               List<StopTarget> combinations,
                               List<Signal> signals, List<Candle> candles) {
        List<Evaluation> results = new ArrayList<>();
        for (StopTarget combination : combinations) {
            results.add(evaluate(signals, candles, side,
                combination.stopPoints, combination.targetPoints));
        }
        List<Evaluation> byReturn = results.stream()
            .sorted(Comparator.comparingDouble(Evaluation::netPnlPoints).reversed()
                .thenComparing(Comparator.comparingDouble(
                    Evaluation::expectancyPoints).reversed()))
            .toList();
        List<Evaluation> byExpectancy = results.stream()
            .sorted(Comparator.comparingDouble(Evaluation::expectancyPoints).reversed()
                .thenComparing(Comparator.comparingDouble(
                    Evaluation::netPnlPoints).reversed()))
            .toList();

        output.append(label).append(" REQUESTED SL/TP COMBINATIONS (unfiltered signals):\n")
            .append("rank_return,rank_expectancy,sl_points,tp_points,rr,entries,wins,losses,open,skipped,win_rate_pct,net_points,expectancy_points,net_r,best_return,best_expectancy\n");
        for (Evaluation result : results) {
            int returnRank = byReturn.indexOf(result) + 1;
            int expectancyRank = byExpectancy.indexOf(result) + 1;
            output.append(String.format(Locale.ROOT,
                "%d,%d,%d,%d,%.3f,%d,%d,%d,%d,%d,%.2f,%.2f,%.2f,%.2f,%s,%s%n",
                returnRank, expectancyRank, result.stopPoints, result.targetPoints,
                (double) result.targetPoints / result.stopPoints,
                result.entries, result.wins, result.losses, result.openTrades,
                result.skipped, result.winRate(), result.netPnlPoints,
                result.expectancyPoints(), result.netR,
                returnRank == 1 ? "BEST" : "", expectancyRank == 1 ? "BEST" : ""));
        }
        output.append('\n');
        }

        private static int halfHourBucket(ZonedDateTime time) {
        return time.getHour() * 2 + time.getMinute() / 30;
        }

        private static String halfHourLabel(int bucket) {
        int startMinute = bucket * 30;
        int endMinute = (startMinute + 30) % (24 * 60);
        return String.format(Locale.ROOT, "%02d:%02d-%02d:%02d",
            startMinute / 60, startMinute % 60, endMinute / 60, endMinute % 60);
        }

        private static void appendCalendarLogRow(StringBuilder output, String dimension,
                             String bucket, BucketStats stats) {
        output.append(String.format(Locale.ROOT, "%s,%s,%d,%d,%d,%d,%.2f,%.2f,%.2f%n",
            dimension, bucket, stats.entries, stats.wins, stats.losses, stats.open,
            stats.winRate(), stats.netPnlPoints, stats.expectancyPoints()));
        }

    private static void printRequestedSide(String label, Side side,
                                           List<StopTarget> combinations,
                                           List<Signal> signals, List<Candle> candles) {
        List<Evaluation> results = new ArrayList<>();
        for (StopTarget combination : combinations) {
            results.add(evaluate(signals, candles, side,
                    combination.stopPoints, combination.targetPoints));
            printCalendarAnalysis(label, side, combination, signals, candles);
        }

        Comparator<Evaluation> returnOrder = Comparator
                .comparingDouble(Evaluation::netPnlPoints).reversed()
                .thenComparing(Comparator.comparingDouble(Evaluation::expectancyPoints).reversed());
        Comparator<Evaluation> expectancyOrder = Comparator
                .comparingDouble(Evaluation::expectancyPoints).reversed()
                .thenComparing(Comparator.comparingDouble(Evaluation::netPnlPoints).reversed());

        System.out.println("\n" + label + " requested SL/TP combinations ranked by net return:");
        printCombinationResults(results.stream().sorted(returnOrder).toList());
        System.out.println("\n" + label + " requested SL/TP combinations ranked by expectancy:");
        printCombinationResults(results.stream().sorted(expectancyOrder).toList());
    }

    private static void printCalendarAnalysis(String label, Side side, StopTarget combination,
                                              List<Signal> signals, List<Candle> candles) {
        Map<DayOfWeek, BucketStats> weekdayStats = new EnumMap<>(DayOfWeek.class);
        Map<Integer, BucketStats> halfHourStats = new TreeMap<>();
        int nextAvailableIndex = -1;

        for (Signal signal : signals) {
            if (signal.side != side || signal.entryIndex <= nextAvailableIndex) {
                continue;
            }

            Exit exit = findExit(candles, signal, combination.stopPoints, combination.targetPoints);
            nextAvailableIndex = exit == null ? candles.size() : exit.candleIndex;

            ZonedDateTime entryTime = Instant.ofEpochMilli(
                    candles.get(signal.entryIndex).timestamp).atZone(ZoneId.of("Asia/Kolkata"));
            weekdayStats.computeIfAbsent(entryTime.getDayOfWeek(), ignored -> new BucketStats())
                    .add(exit);
                halfHourStats.computeIfAbsent(halfHourBucket(entryTime), ignored -> new BucketStats())
                    .add(exit);
        }

        System.out.printf("\n%s SL/TP %d/%d weekday analysis (IST):%n",
                label, combination.stopPoints, combination.targetPoints);
        printCalendarRows("weekday", weekdayStats);
        System.out.printf("%s SL/TP %d/%d half-hour-of-day analysis (IST):%n",
                label, combination.stopPoints, combination.targetPoints);
        for (Map.Entry<Integer, BucketStats> entry : halfHourStats.entrySet()) {
            printCalendarRow("half_hour_ist", halfHourLabel(entry.getKey()), entry.getValue());
        }
    }

    private static void printCalendarRows(String dimension, Map<DayOfWeek, BucketStats> stats) {
        System.out.println("dimension,bucket,entries,wins,losses,open,win_rate_pct,net_points,expectancy_points");
        for (DayOfWeek day : DayOfWeek.values()) {
            BucketStats bucket = stats.get(day);
            if (bucket != null) {
                printCalendarRow(dimension, day.toString(), bucket);
            }
        }
    }

    private static void printCalendarRow(String dimension, String label, BucketStats stats) {
        System.out.printf(Locale.ROOT, "%s,%s,%d,%d,%d,%d,%.2f,%.2f,%.2f%n",
                dimension, label, stats.entries, stats.wins, stats.losses, stats.open,
                stats.winRate(), stats.netPnlPoints, stats.expectancyPoints());
    }

    private static void printCombinationResults(List<Evaluation> results) {
        System.out.println("sl_points,tp_points,rr,entries,wins,losses,open,skipped,win_rate_pct,net_points,expectancy_points,net_r");
        for (Evaluation result : results) {
            System.out.printf(Locale.ROOT, "%d,%d,%.3f,%d,%d,%d,%d,%d,%.2f,%.2f,%.2f,%.2f%n",
                    result.stopPoints, result.targetPoints,
                    (double) result.targetPoints / result.stopPoints,
                    result.entries, result.wins, result.losses, result.openTrades,
                    result.skipped, result.winRate(), result.netPnlPoints,
                    result.expectancyPoints(), result.netR);
        }
    }

    private static Evaluation evaluate(List<Signal> signals, List<Candle> candles,
                                       Side mode, int stopPoints, int targetPoints) {
        int entries = 0;
        int wins = 0;
        int losses = 0;
        int openTrades = 0;
        int skipped = 0;
        int nextAvailableIndex = -1;
        double netPnlPoints = 0;
        double netR = 0;

        for (Signal signal : signals) {
            if (mode != Side.BOTH && signal.side != mode) {
                continue;
            }
            if (signal.entryIndex <= nextAvailableIndex) {
                skipped++;
                continue;
            }

            entries++;
            Exit exit = findExit(candles, signal, stopPoints, targetPoints);
            if (exit == null) {
                openTrades++;
                nextAvailableIndex = candles.size();
                continue;
            }

            nextAvailableIndex = exit.candleIndex;
            if (exit.win) {
                wins++;
            } else {
                losses++;
            }
            netPnlPoints += exit.pnlPoints;
            netR += exit.pnlPoints / stopPoints;
        }

        return new Evaluation(mode, stopPoints, targetPoints, entries, wins, losses,
                openTrades, skipped, netPnlPoints, netR);
    }

    private static Exit findExit(List<Candle> candles, Signal signal,
                                 int stopPoints, int targetPoints) {
        double stopPrice = signal.side == Side.LONG
                ? signal.entryPrice - stopPoints
                : signal.entryPrice + stopPoints;
        double targetPrice = signal.side == Side.LONG
                ? signal.entryPrice + targetPoints
                : signal.entryPrice - targetPoints;

        for (int i = signal.entryIndex + 1; i < candles.size(); i++) {
            Candle candle = candles.get(i);
            boolean stopHit = signal.side == Side.LONG
                    ? candle.low <= stopPrice
                    : candle.high >= stopPrice;
            boolean targetHit = signal.side == Side.LONG
                    ? candle.high >= targetPrice
                    : candle.low <= targetPrice;
            if (stopHit) {
                return new Exit(i, false, -stopPoints);
            }
            if (targetHit) {
                return new Exit(i, true, targetPoints);
            }
        }
        return null;
    }

    private static void printResults(String label, List<Evaluation> results) {
        System.out.println("\n" + label + " (ranked by net points):");
        System.out.println("rank,sl_points,target_points,rr,entries,wins,losses,open,skipped,win_rate_pct,net_points,net_r,expectancy_points");
        for (int i = 0; i < results.size(); i++) {
            Evaluation result = results.get(i);
            System.out.printf(Locale.ROOT,
                    "%d,%d,%d,%.3f,%d,%d,%d,%d,%d,%.2f,%.2f,%.2f,%.2f%n",
                    i + 1, result.stopPoints, result.targetPoints,
                    (double) result.targetPoints / result.stopPoints,
                    result.entries, result.wins, result.losses, result.openTrades,
                    result.skipped, result.winRate(), result.netPnlPoints,
                    result.netR, result.expectancyPoints());
        }

        if (!results.isEmpty()) {
            Evaluation best = results.get(0);
            System.out.printf(Locale.ROOT,
                    "Best %s: SL=%d, target=%d, RR=%.3f, WR=%.2f%%, net_points=%.2f, net_R=%.2f%n",
                    label, best.stopPoints, best.targetPoints,
                    (double) best.targetPoints / best.stopPoints,
                    best.winRate(), best.netPnlPoints, best.netR);
        }
    }

    private static long countSignals(List<Signal> signals, Side side) {
        return signals.stream().filter(signal -> signal.side == side).count();
    }

    private static List<List<String>> dataRows(List<List<String>> rows) {
        List<List<String>> result = new ArrayList<>();
        for (List<String> row : rows) {
            try {
                Long.parseLong(row.get(0));
                result.add(row);
            } catch (NumberFormatException ignored) {
                // CSV column-name rows are not candle data.
            }
        }
        return result;
    }

    private enum Side {
        LONG,
        SHORT,
        BOTH
    }

    private record Candle(long timestamp, double open, double high, double low, double close) {
    }

    private record Signal(long setupStart, int entryIndex, Side side,
                          double thirtyMinuteLevel, double entryPrice) {
    }

    private record StopTarget(int stopPoints, int targetPoints) {
    }

    private record ScheduleRule(boolean avoid, int stopPoints, int targetPoints) {
    }

    private record ScheduleEntry(int startSlot, int endSlot, ScheduleRule rule) {
    }

    private record ScheduledSignal(Signal signal, ScheduleRule rule) {
    }

    private record TakenTrade(Signal signal, ScheduleRule rule, Exit exit) {
    }

    private record Exit(int candleIndex, boolean win, double pnlPoints) {
    }

    private record Evaluation(Side side, int stopPoints, int targetPoints,
                              int entries, int wins, int losses, int openTrades,
                              int skipped, double netPnlPoints, double netR) {
        private double winRate() {
            int closed = wins + losses;
            return closed == 0 ? 0 : wins * 100.0 / closed;
        }

        private double expectancyPoints() {
            return entries == 0 ? 0 : netPnlPoints / entries;
        }
    }

    private static final class BucketStats {
        private int entries;
        private int wins;
        private int losses;
        private int open;
        private double netPnlPoints;

        private void add(Exit exit) {
            entries++;
            if (exit == null) {
                open++;
            } else if (exit.win) {
                wins++;
                netPnlPoints += exit.pnlPoints;
            } else {
                losses++;
                netPnlPoints += exit.pnlPoints;
            }
        }

        private double winRate() {
            int closed = wins + losses;
            return closed == 0 ? 0 : wins * 100.0 / closed;
        }

        private double expectancyPoints() {
            return entries == 0 ? 0 : netPnlPoints / entries;
        }
    }
}