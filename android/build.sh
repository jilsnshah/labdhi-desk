#!/bin/bash
# Build the phone app: one web view onto the desk. No Gradle, no libraries -
# the Android build tools are enough for a single screen, and the whole build
# takes a few seconds.
#
#   ./android/build.sh            -> android/build/labdhi-desk.apk
#
# Needs: a JDK, and the Android SDK's platform 34 with build-tools 34.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
out="$here/build"
sdk="${ANDROID_SDK:-$HOME/Library/Android/sdk}"
tools="$sdk/build-tools/34.0.0"
jar="$sdk/platforms/android-34/android.jar"
# d8 cannot read classes from the newest JDKs, so build with 17 when it is here
for candidate in "${JAVA_HOME:-}" /opt/homebrew/opt/openjdk@17 "$(/usr/libexec/java_home -v 17 2>/dev/null || true)" /opt/homebrew/opt/openjdk; do
  [ -n "$candidate" ] && [ -x "$candidate/bin/javac" ] && java_home="$candidate" && break
done

for needed in "$tools/aapt2" "$tools/d8" "$tools/zipalign" "$tools/apksigner" "$jar" "$java_home/bin/javac"; do
  [ -e "$needed" ] || { echo "missing: $needed"; exit 1; }
done

rm -rf "$out"
mkdir -p "$out/res" "$out/classes" "$out/dex"

# 1. resources (the launcher icon) into a flat archive, then linked with the manifest
"$tools/aapt2" compile --dir "$here/res" -o "$out/res/res.zip"
"$tools/aapt2" link -o "$out/app.unsigned.apk" -I "$jar" \
  --manifest "$here/AndroidManifest.xml" \
  --java "$out/gen" --auto-add-overlay "$out/res/res.zip"

# 2. the one class, compiled against the platform and turned into dex
mkdir -p "$out/gen"
"$java_home/bin/javac" --release 17 -nowarn -classpath "$jar" \
  -d "$out/classes" $(find "$here/java" "$out/gen" -name '*.java')
"$tools/d8" --release --min-api 29 --lib "$jar" --output "$out/dex" $(find "$out/classes" -name '*.class')

# 3. dex into the apk, aligned, then signed with a local debug key
(cd "$out/dex" && zip -q "$out/app.unsigned.apk" classes.dex)
"$tools/zipalign" -f -p 4 "$out/app.unsigned.apk" "$out/app.aligned.apk"

key="$here/debug.keystore"
if [ ! -f "$key" ]; then
  "$java_home/bin/keytool" -genkeypair -keystore "$key" -storepass labdhi -keypass labdhi \
    -alias labdhi -keyalg RSA -keysize 2048 -validity 10000 \
    -dname "CN=Labdhi Desk, O=Labdhi Exim, C=IN" >/dev/null 2>&1
fi
"$tools/apksigner" sign --ks "$key" --ks-pass pass:labdhi --key-pass pass:labdhi \
  --out "$out/labdhi-desk.apk" "$out/app.aligned.apk"
rm -f "$out/app.unsigned.apk" "$out/app.aligned.apk" "$out/labdhi-desk.apk.idsig"

echo "built $out/labdhi-desk.apk"
"$tools/apksigner" verify --print-certs "$out/labdhi-desk.apk" | head -3
