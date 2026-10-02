package com.template;

import java.io.BufferedReader;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.stream.Stream;

public class Csvreader{
    private static final Path datewiseFolder = Paths.get(
            "src/main/java/com/template/binance_csv/datewise_binance_csv");

    public static List<List<String>> read1mCSV() throws IOException{
        return readTimeframeCSV("1m");
    }

    public static List<List<String>> read30mCSV() throws IOException{
        return readTimeframeCSV("30m");
    }

    public static List<List<String>> read1hCSV() throws IOException{
        return readTimeframeCSV("1h");
    }

    private static List<List<String>> readTimeframeCSV(String timeframe) throws IOException {
        if (!Files.isDirectory(datewiseFolder)) {
            throw new IOException("Datewise CSV folder not found: " + datewiseFolder);
        }

        String filenamePrefix = "BTCUSDT-" + timeframe + "-";
        List<Path> files;
        try (Stream<Path> paths = Files.walk(datewiseFolder)) {
            files = paths
                    .filter(Files::isRegularFile)
                    .filter(path -> path.getFileName().toString().startsWith(filenamePrefix))
                    .filter(path -> path.getFileName().toString().endsWith(".csv"))
                    .sorted()
                    .toList();
        }
        if (files.isEmpty()) {
            throw new IOException("No " + timeframe + " CSV files found under " + datewiseFolder);
        }

        List<List<String>> records = new ArrayList<>();
        for (Path file : files) {
            try (BufferedReader reader = Files.newBufferedReader(file)) {
                String line;
                while ((line = reader.readLine()) != null) {
                    String[] values = line.split(",");
                    try {
                        Long.parseLong(values[0]);
                        records.add(Arrays.asList(values));
                    } catch (NumberFormatException ignored) {
                        // Skip CSV header rows.
                    }
            }
        }
        }

        records.sort(Comparator.comparingLong(row -> Long.parseLong(row.get(0))));
        return records;
    }
}