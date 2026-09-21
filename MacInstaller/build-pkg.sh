#!/usr/bin/env bash
#
# Builds the all-in-one macOS installer package for THU Auto Login.
#
#   MacInstaller/build/THUAutoLogin-<version>.pkg     (installer)
#   MacInstaller/build/THUAutoLogin-<version>.dmg     (optional, wraps the pkg)
#
# The package carries ALREADY COMPILED host components, so the person running it
# needs no Xcode, no compiler and no Python. Compilation happens here, on the
# build machine, via MacOS/build.sh.
#
# Installed layout (system-wide, so every user on the Mac gets the same
# extension ID and therefore the same native messaging manifest):
#
#   /Applications/THUAutoLoginKeyAgent.app
#   /Library/Application Support/THUAutoLogin/bin/thu-autologin-host
#   /Library/Application Support/THUAutoLogin/extension/            Chrome 扩展
#   /Library/Application Support/THUAutoLogin/com.thu.autologin.keyagent.plist
#   /Library/Application Support/THUAutoLogin/uninstall.sh
#   /Library/Google/Chrome/NativeMessagingHosts/com.thu.autologin.host.json
#
# Usage:
#   ./build-pkg.sh              build everything (compiles first)
#   ./build-pkg.sh --skip-build reuse MacOS/build artifacts
#   ./build-pkg.sh --dmg        also produce a .dmg wrapping the .pkg
#   ./build-pkg.sh --sign "Developer ID Installer: ..."
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

VERSION="1.0.0"
PKG_ID="com.thu.autologin.pkg"
AGENT_LABEL="com.thu.autologin.keyagent"
HOST_NAME="com.thu.autologin.host"
APP_NAME="THUAutoLoginKeyAgent"

EXT_SRC="$ROOT/THUAutoLogin"
MACOS_BUILD="$ROOT/MacOS/build"
WORK="$HERE/build"
PAYLOAD="$WORK/payload"
SCRIPTS="$WORK/scripts"
COMPONENT_PKG="$WORK/component.pkg"
FINAL_PKG="$WORK/THUAutoLogin-$VERSION.pkg"

# Where the extension lands once installed. The extension ID is derived from
# this path, so it must match what the postinstall/manifest generation uses.
EXT_INSTALL_DIR="/Library/Application Support/THUAutoLogin/extension"
SUPPORT_INSTALL_DIR="/Library/Application Support/THUAutoLogin"
NM_INSTALL_DIR="/Library/Google/Chrome/NativeMessagingHosts"

SKIP_BUILD=0
MAKE_DMG=0
SIGN_IDENTITY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1; shift ;;
    --dmg)        MAKE_DMG=1; shift ;;
    --sign)       SIGN_IDENTITY="${2:-}"; shift 2 ;;
    -h|--help)    sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ -d "$EXT_SRC" ] || die "extension directory not found: $EXT_SRC"
[ -f "$HERE/distribution.xml" ] || die "distribution.xml not found"

# ---------------------------------------------------------------------------
# 1. Compile the native components
# ---------------------------------------------------------------------------
if [ "$SKIP_BUILD" = "1" ]; then
  say "==> skipping build (--skip-build)"
else
  say "==> building native components (needs Xcode Command Line Tools)"
  bash "$ROOT/MacOS/build.sh"
fi

[ -x "$MACOS_BUILD/thu-autologin-host" ] || die "missing $MACOS_BUILD/thu-autologin-host (run MacOS/build.sh)"
[ -d "$MACOS_BUILD/$APP_NAME.app" ] || die "missing $MACOS_BUILD/$APP_NAME.app (run MacOS/build.sh)"

# ---------------------------------------------------------------------------
# 2. Extension ID
#
# Chrome derives an unpacked extension's ID from the SHA-256 of its absolute
# path: first 32 hex digits, with 0-9a-f mapped onto a-p. Because the package
# installs to a fixed system path, this ID is identical for every user.
# ---------------------------------------------------------------------------
extension_id_for() {
  printf '%s' "$1" | shasum -a 256 | cut -c1-32 | tr '0-9a-f' 'a-p'
}

EXTENSION_ID="$(extension_id_for "$EXT_INSTALL_DIR")"
say ""
say "==> extension id"
say "    install dir : $EXT_INSTALL_DIR"
say "    extension id: $EXTENSION_ID"

# ---------------------------------------------------------------------------
# 3. Stage the payload
# ---------------------------------------------------------------------------
say ""
say "==> staging payload"
rm -rf "$WORK"
mkdir -p "$PAYLOAD/Applications"
mkdir -p "$PAYLOAD$SUPPORT_INSTALL_DIR/bin"
mkdir -p "$PAYLOAD$EXT_INSTALL_DIR"
mkdir -p "$PAYLOAD$SUPPORT_INSTALL_DIR"
mkdir -p "$PAYLOAD$NM_INSTALL_DIR"

# ditto preserves bundle metadata and the code signature better than cp -R.
ditto "$MACOS_BUILD/$APP_NAME.app" "$PAYLOAD/Applications/$APP_NAME.app"
install -m 0755 "$MACOS_BUILD/thu-autologin-host" "$PAYLOAD$SUPPORT_INSTALL_DIR/bin/thu-autologin-host"

# The extension, world-readable so every user can load it.
ditto "$EXT_SRC" "$PAYLOAD$EXT_INSTALL_DIR"
chmod -R a+rX "$PAYLOAD$EXT_INSTALL_DIR"

# LaunchAgent template. @HOME@ is substituted per user by the postinstall script,
# because a system package cannot know which user will run the agent.
install -m 0644 "$HERE/launchagent.plist.in" \
  "$PAYLOAD$SUPPORT_INSTALL_DIR/$AGENT_LABEL.plist"

install -m 0755 "$HERE/uninstall.sh" "$PAYLOAD$SUPPORT_INSTALL_DIR/uninstall.sh"

# Native messaging manifest, resolved at build time. System-wide, so it applies
# to every user without any per-user step.
cat > "$PAYLOAD$NM_INSTALL_DIR/$HOST_NAME.json" <<JSON
{
  "name": "$HOST_NAME",
  "description": "THU Auto Login native messaging host",
  "path": "$SUPPORT_INSTALL_DIR/bin/thu-autologin-host",
  "type": "stdio",
  "allowed_origins": [
    "chrome-extension://$EXTENSION_ID/"
  ]
}
JSON
chmod 0644 "$PAYLOAD$NM_INSTALL_DIR/$HOST_NAME.json"

say "    $(find "$PAYLOAD" -type f | wc -l | tr -d ' ') files staged"

# ---------------------------------------------------------------------------
# 4. postinstall script (needs the extension id baked in for its final message)
# ---------------------------------------------------------------------------
mkdir -p "$SCRIPTS"
sed "s|@@EXTENSION_ID@@|$EXTENSION_ID|g" "$HERE/scripts/postinstall" > "$SCRIPTS/postinstall"
chmod 0755 "$SCRIPTS/postinstall"

# ---------------------------------------------------------------------------
# 5. Component package + product archive
# ---------------------------------------------------------------------------
say ""
say "==> pkgbuild (component)"
pkgbuild \
  --root "$PAYLOAD" \
  --scripts "$SCRIPTS" \
  --identifier "$PKG_ID" \
  --version "$VERSION" \
  --install-location / \
  --ownership recommended \
  "$COMPONENT_PKG"

# distribution.xml is templated so the version stays single-sourced.
#
# The package carries a binary built for this machine's architecture, so declare
# that architecture: refusing to install on the wrong CPU is much better than
# installing a binary that cannot run.
BIN_ARCH="$(lipo -archs "$MACOS_BUILD/thu-autologin-host" 2>/dev/null | awk '{print $1}')"
case "$BIN_ARCH" in
  arm64)  HOST_ARCH="arm64" ;;
  x86_64) HOST_ARCH="x86_64" ;;
  *)      HOST_ARCH="arm64,x86_64"
          say "    warning: could not detect the binary architecture (got '$BIN_ARCH'); allowing both" ;;
esac
say "    binary arch : $BIN_ARCH"

DIST="$WORK/distribution.xml"
sed -e "s|@@VERSION@@|$VERSION|g" \
    -e "s|@@PKG_ID@@|$PKG_ID|g" \
    -e "s|@@EXTENSION_ID@@|$EXTENSION_ID|g" \
    -e "s|@@HOST_ARCH@@|$HOST_ARCH|g" \
    "$HERE/distribution.xml" > "$DIST"

say ""
say "==> productbuild (installer)"
PRODUCTBUILD_ARGS=(
  --distribution "$DIST"
  --resources "$HERE/resources"
  --package-path "$WORK"
)
[ -n "$SIGN_IDENTITY" ] && PRODUCTBUILD_ARGS+=(--sign "$SIGN_IDENTITY")
productbuild "${PRODUCTBUILD_ARGS[@]}" "$FINAL_PKG"

say "    $FINAL_PKG"

# ---------------------------------------------------------------------------
# 6. Optional disk image
# ---------------------------------------------------------------------------
if [ "$MAKE_DMG" = "1" ]; then
  DMG="$WORK/THUAutoLogin-$VERSION.dmg"
  STAGE="$WORK/dmg"
  rm -rf "$STAGE"
  mkdir -p "$STAGE"
  cp "$FINAL_PKG" "$STAGE/"
  if [ -f "$HERE/resources/安装说明.txt" ]; then
    cp "$HERE/resources/安装说明.txt" "$STAGE/"
    chmod 0644 "$STAGE/安装说明.txt"
  fi
  rm -f "$DMG"
  # Fail-soft: hdiutil has to attach a temporary disk image, which restricted
  # environments can block. The .pkg is the real deliverable, so a DMG failure
  # must not lose it.
  if hdiutil create -volname "THU Auto Login $VERSION" \
       -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null; then
    say "    $DMG"
  else
    say "    warning: hdiutil could not create the .dmg; the .pkg above is still usable" >&2
  fi
fi

say ""
say "done."
say ""
say "安装包内已包含编译好的主机程序，最终用户无需 Xcode / 编译器 / Python。"
say "安装后仍需两步手动操作（由安装器的完成页面提示）："
say "  1. 系统设置 → 隐私与安全性 → 辅助功能 勾选 $APP_NAME"
say "  2. chrome://extensions 加载未打包扩展：$EXT_INSTALL_DIR"
