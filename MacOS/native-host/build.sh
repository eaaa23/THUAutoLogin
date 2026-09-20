#!/usr/bin/env bash
# Builds the native messaging host (host #1) into build/thu-autologin-host.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
BUILD_DIR="$ROOT/build"
OUT="$BUILD_DIR/thu-autologin-host"

mkdir -p "$BUILD_DIR"

case "$(uname -m)" in
  arm64) TARGET="arm64-apple-macos11.0" ;;
  *)     TARGET="x86_64-apple-macos10.15" ;;
esac

swiftc -O -target "$TARGET" \
  -o "$OUT" \
  "$ROOT/shared/SocketWire.swift" \
  "$HERE/main.swift"

echo "built: $OUT"
