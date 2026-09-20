#!/usr/bin/env bash
# Builds the key agent menu-bar app (host #2) into build/THUAutoLoginKeyAgent.app.
#
# Signing matters here: TCC keys the Accessibility grant to the code signature.
# An ad-hoc signature is derived from the binary hash, so *every rebuild* looks
# like a brand new app and the grant must be given again. Signing with a stable
# certificate (a self-signed one is enough) makes the grant survive rebuilds.
# Set CODESIGN_IDENTITY to choose one explicitly.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
BUILD_DIR="$ROOT/build"
APP_NAME="THUAutoLoginKeyAgent"
APP="$BUILD_DIR/$APP_NAME.app"

mkdir -p "$BUILD_DIR"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"

case "$(uname -m)" in
  arm64) TARGET="arm64-apple-macos11.0" ;;
  *)     TARGET="x86_64-apple-macos10.15" ;;
esac

swiftc -O -target "$TARGET" \
  -o "$APP/Contents/MacOS/$APP_NAME" \
  -framework AppKit -framework ApplicationServices -framework CoreGraphics \
  "$ROOT/shared/SocketWire.swift" \
  "$HERE/main.swift"

IDENTITY="${CODESIGN_IDENTITY:-}"
if [ -z "$IDENTITY" ]; then
  # First available code-signing identity, if the user has one.
  IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null | awk -F'"' '/"/{print $2; exit}')"
fi

if [ -n "$IDENTITY" ]; then
  echo "signing with: $IDENTITY"
  codesign --force --sign "$IDENTITY" --identifier com.thu.autologin.keyagent "$APP"
else
  echo "warning: no code-signing identity found; falling back to ad-hoc signing." >&2
  echo "         The Accessibility grant will be reset every time you rebuild." >&2
  echo "         See README for how to create a self-signed certificate." >&2
  codesign --force --sign - --identifier com.thu.autologin.keyagent "$APP"
fi

codesign --verify --verbose=2 "$APP" 2>&1 | sed 's/^/  /' || true
echo "built: $APP"
