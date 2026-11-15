#!/bin/sh
# Builds the debug APK with the user-space toolchain from tools/setup-toolchain.sh.
set -eu
cd "$(dirname "$0")"
export JAVA_HOME="${JAVA_HOME_21:-$HOME/.local/share/jdks/jdk-21}"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Android/Sdk}"
[ -f gradlew ] || "$HOME/.local/share/gradle-8.9/bin/gradle" wrapper --gradle-version 8.9 -q
./gradlew --console=plain -q "${@:-assembleDebug}"
[ -f app/build/outputs/apk/debug/app-debug.apk ] && echo "APK: $(pwd)/app/build/outputs/apk/debug/app-debug.apk"
