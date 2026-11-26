#!/usr/bin/env bash
set -euo pipefail
cd "$1"
flutter build apk --debug
package=com.example.platform_proof
adb install -r build/app/outputs/flutter-apk/app-debug.apk
adb shell pm clear "$package"

for expected in 'PASS: durable sale saved' 'PASS: persisted sale verified after relaunch'; do
    adb shell am force-stop "$package"
    adb logcat -c
    adb shell am start -W -n "$package/.MainActivity"
    result=
    for attempt in {1..30}; do
        logs=$(adb logcat -d -s flutter:I)
        result=$(grep -F 'BN_V3_PROOF:' <<< "$logs" | tail -1 || true)
        if [[ -n "$result" ]]; then break; fi
        sleep 2
    done
    printf '%s\n' "$result"
    grep -F "BN_V3_PROOF: $expected" <<< "$result"
done
