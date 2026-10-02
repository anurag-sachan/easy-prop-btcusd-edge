#!/usr/bin/env bash

set -u

# ============================================================
# Binance BTCUSDT Spot Kline Downloader
# macOS-compatible
#
# Date range: 2023-04-25 through 2026-10-01 inclusive
# Timeframes: 1m, 30m, 1h
# CSV output: src/main/java/com/template/binance_csv/datewise_binance_csv_spot/YYYY-MM-DD/<timeframe>/
# Normalized columns: timestamp,open,high,low,close,volume,trades
# ============================================================

SYMBOL="BTCUSDT"

START_DATE="${START_DATE:-2023-04-25}"
END_DATE="${END_DATE:-2026-10-01}"

TIMEFRAMES=("1m" "30m" "1h")

BASE_URL="https://data.binance.vision/data/spot/daily/klines"

OUTPUT_DIR="./src/main/java/com/template/binance_csv/datewise_binance_csv_spot"

MAX_RETRIES=5

FAILED_FILE="$OUTPUT_DIR/failed_downloads.txt"
UNAVAILABLE_FILE="$OUTPUT_DIR/unavailable_downloads.txt"

# ------------------------------------------------------------
# Requirements
# ------------------------------------------------------------

for cmd in awk curl date unzip; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "ERROR: '$cmd' is required but not installed."
        exit 1
    fi
done

normalize_csv() {
    awk -F, '
        BEGIN { OFS = "," }
        NR == 1 {
            print "timestamp", "open", "high", "low", "close", "volume", "trades"
            print "open_time", "open", "high", "low", "close", "volume", "count"
            if ($1 == "timestamp" && NF == 7) {
                standardized = 1
                next
            }
        }
        NR == 2 && standardized && $1 == "open_time" { next }
        $1 ~ /^[0-9]+$/ {
            timestamp = $1
            if (length(timestamp) > 13) {
                timestamp = substr(timestamp, 1, length(timestamp) - 3)
            }
            if (NF >= 9) {
                print timestamp, $2, $3, $4, $5, $6, $9
            } else if (NF >= 7) {
                print timestamp, $2, $3, $4, $5, $6, $7
            }
        }
    ' "$1" > "$2"
}

mkdir -p "$OUTPUT_DIR"

# Start fresh failure list
: > "$FAILED_FILE"
: > "$UNAVAILABLE_FILE"

# ------------------------------------------------------------
# Counters
# ------------------------------------------------------------

TOTAL=0
DOWNLOADED=0
EXISTING=0
FAILED=0
UNAVAILABLE=0

# ------------------------------------------------------------
# Date iterator
# ------------------------------------------------------------

current_date="$START_DATE"

while true; do

    echo
    echo "============================================================"
    echo "DATE: $current_date"
    echo "============================================================"

    DATE_DIR="$OUTPUT_DIR/$current_date"
    mkdir -p "$DATE_DIR"

    # --------------------------------------------------------
    # Download each timeframe
    # --------------------------------------------------------

    for TF in "${TIMEFRAMES[@]}"; do

        TOTAL=$((TOTAL + 1))

        TF_DIR="$DATE_DIR/$TF"
        mkdir -p "$TF_DIR"

        CSV_NAME="${SYMBOL}-${TF}-${current_date}.csv"
        ZIP_NAME="${CSV_NAME%.csv}.zip"

        URL="${BASE_URL}/${SYMBOL}/${TF}/${ZIP_NAME}"

        OUTPUT_FILE="$TF_DIR/$CSV_NAME"
        ZIP_FILE="$TF_DIR/$ZIP_NAME"
        CSV_TEMP="$OUTPUT_FILE.tmp"
        RAW_TEMP="$OUTPUT_FILE.raw.tmp"

        echo
        echo "[$TF] $current_date"

        # ----------------------------------------------------
        # Already downloaded?
        # ----------------------------------------------------

        if [[ -s "$OUTPUT_FILE" ]]; then
            if normalize_csv "$OUTPUT_FILE" "$CSV_TEMP" && [[ -s "$CSV_TEMP" ]]; then
                mv "$CSV_TEMP" "$OUTPUT_FILE"
                echo "  Existing CSV normalized."
                EXISTING=$((EXISTING + 1))
            else
                rm -f "$CSV_TEMP"
                echo "  ERROR: Could not normalize $OUTPUT_FILE"
                FAILED=$((FAILED + 1))
            fi
            continue
        fi

        # ----------------------------------------------------
        # Download with retries
        # ----------------------------------------------------

        SUCCESS=0

        for ((attempt=1; attempt<=MAX_RETRIES; attempt++)); do

            echo "  Download attempt $attempt/$MAX_RETRIES"

            HTTP_CODE=$(curl \
                --location \
                --silent \
                --show-error \
                --fail \
                --retry 2 \
                --retry-delay 2 \
                --connect-timeout 20 \
                --max-time 300 \
                --output "$ZIP_FILE.tmp" \
                --write-out "%{http_code}" \
                "$URL" 2>/dev/null)

            if [[ "$HTTP_CODE" == "200" ]] && \
               unzip -t -q "$ZIP_FILE.tmp" >/dev/null 2>&1; then
                mv "$ZIP_FILE.tmp" "$ZIP_FILE"
                if unzip -p "$ZIP_FILE" "$CSV_NAME" > "$RAW_TEMP" && \
                   [[ -s "$RAW_TEMP" ]] && \
                   normalize_csv "$RAW_TEMP" "$CSV_TEMP" && [[ -s "$CSV_TEMP" ]]; then
                    mv "$CSV_TEMP" "$OUTPUT_FILE"
                    rm -f "$RAW_TEMP"
                    rm -f "$ZIP_FILE"
                    echo "  OK: normalized $CSV_NAME"
                    DOWNLOADED=$((DOWNLOADED + 1))
                    SUCCESS=1
                    break
                fi

                rm -f "$CSV_TEMP" "$RAW_TEMP" "$ZIP_FILE"
                echo "  Archive did not contain a valid, normalizable CSV."
                HTTP_CODE="invalid-archive"
                break

            else

                rm -f "$ZIP_FILE.tmp"

                echo "  Failed (HTTP $HTTP_CODE)"

                if [[ "$HTTP_CODE" == "404" ]]; then
                    echo "  No archive for this date/timeframe."
                    echo "$URL" >> "$UNAVAILABLE_FILE"
                    UNAVAILABLE=$((UNAVAILABLE + 1))
                    break
                fi

                sleep $((attempt * 2))
            fi

        done

        # ----------------------------------------------------
        # Record failure
        # ----------------------------------------------------

        if [[ "$SUCCESS" -eq 0 ]]; then

            if [[ "$HTTP_CODE" != "404" ]]; then
                echo "  FAILED: $URL"

                echo "$URL" >> "$FAILED_FILE"

                FAILED=$((FAILED + 1))
            fi
        fi

    done

    # --------------------------------------------------------
    # Stop when END_DATE reached
    # --------------------------------------------------------

    if [[ "$current_date" == "$END_DATE" ]]; then
        break
    fi

    # --------------------------------------------------------
    # macOS/BSD date:
    # Add one day to current date
    # --------------------------------------------------------

    current_date=$(date -j -v+1d -f "%Y-%m-%d" "$current_date" "+%Y-%m-%d")

done

# ============================================================
# Final report
# ============================================================

echo
echo
echo "============================================================"
echo "DOWNLOAD COMPLETE"
echo "============================================================"

echo "Expected files : $TOTAL"
echo "Downloaded     : $DOWNLOADED"
echo "Already existed: $EXISTING"
echo "Failed         : $FAILED"
echo "Unavailable    : $UNAVAILABLE"

echo

if [[ "$FAILED" -gt 0 ]]; then

    echo "Failed URLs:"
    cat "$FAILED_FILE"

    echo
    echo "Re-run the script to retry failed files."

else

    if [[ "$UNAVAILABLE" -eq 0 ]]; then
        echo "All requested files downloaded successfully."
    else
        echo "Downloads completed; unavailable archives are listed in $UNAVAILABLE_FILE."
    fi

    rm -f "$FAILED_FILE"

fi

echo
echo "Data directory:"
echo "$OUTPUT_DIR"
echo