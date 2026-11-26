#!/usr/bin/env bash
set -euo pipefail
project=$1
gradle -p "$project" assembleDebug assembleDebugAndroidTest --no-daemon --stacktrace
adb install -r "$project/build/outputs/apk/debug/platform-runtime-android-debug.apk"
adb install -r "$project/build/outputs/apk/androidTest/debug/platform-runtime-android-debug-androidTest.apk"

for phase in seed relaunch; do
    adb shell am force-stop proof.runtime
    adb logcat -c
    result=$(adb shell am instrument -w -e phase "$phase" proof.runtime.test/androidx.test.runner.AndroidJUnitRunner)
    printf '%s\n' "$result"
    grep -F 'OK (1 test)' <<< "$result"
    adb logcat -d -s System.out:I | grep -F 'BN_V3_PROOF: PASS:'
done
