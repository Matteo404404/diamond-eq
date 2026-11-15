#!/usr/bin/env bash
# Installs the Android build toolchain in user space (no sudo):
#   JDK 21  -> ~/.local/share/jdks/jdk-21   (AGP 8.x does not run on newer system JDKs)
#   SDK     -> ~/Android/Sdk
#   Gradle  -> ~/.local/share/gradle-8.9    (only used to generate the wrapper)
set -euo pipefail
JDK_DIR="$HOME/.local/share/jdks/jdk-21"
SDK_DIR="$HOME/Android/Sdk"
GRADLE_DIR="$HOME/.local/share/gradle-8.9"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [ ! -x "$JDK_DIR/bin/java" ]; then
  echo "== JDK 21"
  mkdir -p "$JDK_DIR"
  curl -fsSL -o "$TMP/jdk.tar.gz" "https://api.adoptium.net/v3/binary/latest/21/ga/linux/x64/jdk/hotspot/normal/eclipse"
  tar -xzf "$TMP/jdk.tar.gz" -C "$JDK_DIR" --strip-components=1
fi
export JAVA_HOME="$JDK_DIR"
export PATH="$JAVA_HOME/bin:$PATH"

if [ ! -x "$SDK_DIR/cmdline-tools/latest/bin/sdkmanager" ]; then
  echo "== Android cmdline-tools"
  mkdir -p "$SDK_DIR/cmdline-tools"
  curl -fsSL -o "$TMP/clt.zip" "https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip"
  (cd "$TMP" && python3 -m zipfile -e clt.zip clt)
  rm -rf "$SDK_DIR/cmdline-tools/latest"
  mv "$TMP/clt/cmdline-tools" "$SDK_DIR/cmdline-tools/latest"
  chmod +x "$SDK_DIR/cmdline-tools/latest/bin/"*
fi
echo "== SDK packages"
set +o pipefail
yes | "$SDK_DIR/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$SDK_DIR" --licenses >/dev/null
yes | "$SDK_DIR/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$SDK_DIR" \
  "platform-tools" "platforms;android-35" "build-tools;35.0.0" | tail -3
set -o pipefail

if [ ! -x "$GRADLE_DIR/bin/gradle" ]; then
  echo "== Gradle 8.9"
  curl -fsSL -o "$TMP/gradle.zip" "https://services.gradle.org/distributions/gradle-8.9-bin.zip"
  (cd "$TMP" && python3 -m zipfile -e gradle.zip g)
  mkdir -p "$(dirname "$GRADLE_DIR")"
  rm -rf "$GRADLE_DIR"
  mv "$TMP/g/gradle-8.9" "$GRADLE_DIR"
  chmod +x "$GRADLE_DIR/bin/gradle"
fi
echo "TOOLCHAIN_OK"
"$JDK_DIR/bin/java" -version 2>&1 | head -1
ls "$SDK_DIR"
