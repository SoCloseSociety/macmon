#!/usr/bin/env bash
# AegisForge Android -- reproducible build. Runs the off-device unit tests first,
# then assembles the release APK and copies it to dist/aegisforge-<code>.apk.
#
# Toolchain (same recipe as the fleet's Android apps): AGP 8.9.3 needs Gradle
# 8.11.1 (the wrapper handles that) and a JDK 17. The release is signed with the
# local debug keystore (~/.android/debug.keystore) -- stable across releases, so
# a phone can `install -r` without INSTALL_FAILED_UPDATE_INCOMPATIBLE. No release
# keystore lives in the repo (a key in git is a secret in git). See README.
set -euo pipefail
cd "$(dirname "$0")"

# 1. A JDK 17. Android Studio's bundled JBR is the easy one; override with JAVA17.
if [ -z "${JAVA17:-}" ]; then
  for c in \
    "/Applications/Android Studio.app/Contents/jbr/Contents/Home" \
    "${HOME}/.sdkman/candidates/java/17"* ; do
    [ -x "$c/bin/java" ] && { JAVA17="$c"; break; }
  done
fi
[ -n "${JAVA17:-}" ] && [ -x "$JAVA17/bin/java" ] || {
  echo "no JDK 17 found. Set JAVA17=/path/to/jdk17 (AGP 8.9 refuses newer JDKs)."; exit 1; }
export JAVA_HOME="$JAVA17"
echo "==> JDK: $("$JAVA_HOME/bin/java" -version 2>&1 | head -1)"

# 2. The Android SDK. local.properties (git-ignored) or ANDROID_HOME.
SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}"
[ -d "$SDK" ] || { echo "no Android SDK at $SDK. Set ANDROID_HOME."; exit 1; }
[ -f local.properties ] || echo "sdk.dir=$SDK" > local.properties
echo "==> SDK: $SDK"

VERSION_CODE="$(sed -n 's/^VERSION_CODE=//p' version.properties | tr -d '[:space:]')"

# 3. Tests, then the release APK.
echo "==> Unit tests (off-device)"
./gradlew --no-daemon testReleaseUnitTest
echo "==> assembleRelease"
./gradlew --no-daemon assembleRelease

APK_SRC="app/build/outputs/apk/release/app-release.apk"
[ -f "$APK_SRC" ] || { echo "build failed: $APK_SRC not produced"; exit 1; }
mkdir -p dist
APK="dist/aegisforge-${VERSION_CODE}.apk"
cp "$APK_SRC" "$APK"
echo "==> Built: $APK"

# 4. Badging / signature proof (if build-tools' aapt is on hand).
AAPT="$(ls "$SDK"/build-tools/*/aapt2 2>/dev/null | sort | tail -1 || true)"
if [ -n "$AAPT" ]; then
  echo "==> aapt2 badging"
  "$AAPT" dump badging "$APK" 2>/dev/null | grep -E "package:|application-label:|uses-permission" || true
fi
echo "==> Install on a connected device/emulator: adb install -r '$APK'"
