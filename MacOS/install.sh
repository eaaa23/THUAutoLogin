#!/usr/bin/env bash
#
# Installs both native components and wires them up to Chrome.
#
#   host #1  ->  ~/Library/Application Support/THUAutoLogin/bin/thu-autologin-host
#                + native messaging manifest for Chrome
#   host #2  ->  ~/Applications/THUAutoLoginKeyAgent.app
#                + LaunchAgent so it starts at login
#
# Nothing here needs sudo. Override with:
#   APP_INSTALL_DIR=/Applications bash install.sh
#   EXTENSION_ID=<id>          bash install.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # this MacOS/ directory
ROOT="$(cd "$HERE/.." && pwd)"                          # repository root
EXT_DIR="$ROOT/THUAutoLogin"                            # shared with the Windows side
BUILD_DIR="$HERE/build"

SUPPORT_DIR="$HOME/Library/Application Support/THUAutoLogin"
BIN_DIR="$SUPPORT_DIR/bin"
NM_DIR="$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts"
LA_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/THUAutoLogin"
APP_INSTALL_DIR="${APP_INSTALL_DIR:-$HOME/Applications}"

HOST_NAME="com.thu.autologin.host"
AGENT_LABEL="com.thu.autologin.keyagent"
APP_NAME="THUAutoLoginKeyAgent"
APP_DEST="$APP_INSTALL_DIR/$APP_NAME.app"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ -d "$EXT_DIR" ] || die "extension directory not found: $EXT_DIR"
[ -d "$BUILD_DIR/$APP_NAME.app" ] || die "build artifacts missing; run ./build.sh first"

# ---------------------------------------------------------------------------
# A system-wide install (from MacInstaller/build-pkg.sh) uses the SAME native
# messaging host name, and Chrome consults the per-user directory before the
# system one. Running both therefore leaves the per-user manifest shadowing the
# system one, with a different extension ID — which shows up as
# "Access to the specified native messaging host is forbidden".
#
# Refuse to create that state unless the user explicitly asks for it.
# ---------------------------------------------------------------------------
SYSTEM_NM_DIR="/Library/Google/Chrome/NativeMessagingHosts"
SYSTEM_NM_MANIFEST="$SYSTEM_NM_DIR/$HOST_NAME.json"
SYSTEM_SUPPORT="/Library/Application Support/THUAutoLogin"

if [ -f "$SYSTEM_NM_MANIFEST" ] && [ "${FORCE:-0}" != "1" ]; then
  say ""
  say "检测到已安装的 .pkg 版本（系统级）："
  say "    $SYSTEM_NM_MANIFEST"
  say ""
  say "它和本脚本安装的用户级清单同名，但扩展 ID 不同。Chrome 会优先使用用户级清单，"
  say "两者并存会让浏览器拒绝连接原生主机（Access to the specified native messaging"
  say "host is forbidden）。"
  say ""
  say "请二选一："
  say "  1. 想用 .pkg 安装 —— 不要再运行本脚本，或者先卸载系统级安装："
  say "         sudo \"$SYSTEM_SUPPORT/uninstall.sh\""
  say "  2. 想用本脚本（开发）—— 先移除系统级安装，再重新运行："
  say "         sudo rm -f \"$SYSTEM_NM_MANIFEST\""
  say "         sudo rm -rf \"$SYSTEM_SUPPORT\" \"/Applications/$APP_NAME.app\""
  say ""
  say "确认要强制继续（会覆盖用户级清单）：FORCE=1 bash install.sh"
  exit 1
fi

# ---------------------------------------------------------------------------
# Determine the extension ID.
#
# Chrome derives an unpacked extension's ID from the SHA-256 of its absolute
# path: take the first 32 hex digits and map 0-9a-f onto a-p. That means moving
# the folder changes the ID, and this script has to be re-run.
# ---------------------------------------------------------------------------
path_to_id() {
  printf '%s' "$1" | shasum -a 256 | cut -c1-32 | tr '0-9a-f' 'a-p'
}

EXTENSION_ID="${EXTENSION_ID:-$(path_to_id "$EXT_DIR")}"
say "extension directory : $EXT_DIR"
say "extension id        : $EXTENSION_ID"

if [ -z "$EXTENSION_ID" ]; then
  die "could not determine the extension id; pass EXTENSION_ID=<id>"
fi

# ---------------------------------------------------------------------------
# Host #1: binary + Chrome native messaging manifest
# ---------------------------------------------------------------------------
say ""
say "==> installing native messaging host (host #1)"
mkdir -p "$BIN_DIR" "$NM_DIR" "$LOG_DIR"
install -m 0755 "$BUILD_DIR/thu-autologin-host" "$BIN_DIR/thu-autologin-host"

NM_MANIFEST="$NM_DIR/$HOST_NAME.json"
cat > "$NM_MANIFEST" <<JSON
{
  "name": "$HOST_NAME",
  "description": "THU Auto Login native messaging host",
  "path": "$BIN_DIR/thu-autologin-host",
  "type": "stdio",
  "allowed_origins": [
    "chrome-extension://$EXTENSION_ID/"
  ]
}
JSON
say "    binary   : $BIN_DIR/thu-autologin-host"
say "    manifest : $NM_MANIFEST"

# ---------------------------------------------------------------------------
# Host #2: the app bundle
# ---------------------------------------------------------------------------
say ""
say "==> installing key agent app (host #2)"
mkdir -p "$APP_INSTALL_DIR"
rm -rf "$APP_DEST"
cp -R "$BUILD_DIR/$APP_NAME.app" "$APP_DEST"

# Make sure LaunchServices knows about it, so `open -b $AGENT_LABEL` resolves.
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
if [ -x "$LSREGISTER" ]; then
  "$LSREGISTER" -f "$APP_DEST" >/dev/null 2>&1 || true
fi
say "    app      : $APP_DEST"

# ---------------------------------------------------------------------------
# LaunchAgent: start at login, restart only if it dies unexpectedly
# ---------------------------------------------------------------------------
say ""
say "==> installing LaunchAgent (start at login)"
mkdir -p "$LA_DIR"
AGENT_PLIST="$LA_DIR/$AGENT_LABEL.plist"

cat > "$AGENT_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$AGENT_LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>$APP_DEST/Contents/MacOS/$APP_NAME</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<dict>
		<key>SuccessfulExit</key>
		<false/>
	</dict>
	<key>ProcessType</key>
	<string>Adaptive</string>
	<key>StandardOutPath</key>
	<string>$LOG_DIR/keyagent.log</string>
	<key>StandardErrorPath</key>
	<string>$LOG_DIR/keyagent.log</string>
</dict>
</plist>
PLIST

say "    plist    : $AGENT_PLIST"

# Reload it: bootout is expected to fail when nothing is loaded yet.
launchctl bootout "gui/$UID/$AGENT_LABEL" >/dev/null 2>&1 || true

BOOTSTRAP_ERR="$(launchctl bootstrap "gui/$UID" "$AGENT_PLIST" 2>&1)" && BOOTSTRAP_OK=1 || BOOTSTRAP_OK=0
LOAD_ERR=""
if [ "$BOOTSTRAP_OK" = "1" ]; then
  say "    loaded   : launchctl bootstrap"
else
  LOAD_ERR="$(launchctl load -w "$AGENT_PLIST" 2>&1)" && LOAD_OK=1 || LOAD_OK=0
  if [ "${LOAD_OK:-0}" = "1" ]; then
    say "    loaded   : launchctl load (legacy fallback)"
  else
    say "    warning  : could not register the LaunchAgent"
    [ -n "$BOOTSTRAP_ERR" ] && say "               bootstrap: $BOOTSTRAP_ERR"
    [ -n "$LOAD_ERR" ] && say "               load:      $LOAD_ERR"
    say "               Start it once manually: open -a \"$APP_DEST\""
  fi
fi

# The load commands can report success without the service actually being up,
# so confirm against launchd rather than trusting the exit code.
if launchctl print "gui/$UID/$AGENT_LABEL" >/dev/null 2>&1; then
  say "    verified : service registered with launchd"
else
  say "    note     : service is not registered with launchd yet"
fi

# Give it a moment, then report whether the socket came up.
SOCKET="$SUPPORT_DIR/keyagent.sock"
for _ in $(seq 1 24); do
  [ -S "$SOCKET" ] && break
  sleep 0.25
done

say ""
if [ -S "$SOCKET" ]; then
  say "key agent is listening on $SOCKET"
else
  say "key agent socket not present yet."
  say "Check the log: $LOG_DIR/keyagent.log"
  say "You may need to allow it under System Settings > General > Login Items."
fi

# ---------------------------------------------------------------------------
# Next steps
# ---------------------------------------------------------------------------
cat <<NEXT

--------------------------------------------------------------------
Done. Two manual steps remain, both required.

1. Grant Accessibility (控制权限) to the key agent
   System Settings > Privacy & Security > Accessibility
   -> enable "$APP_NAME"

   The agent also shows a THU item in the menu bar; use
   "打开"辅助功能"设置…" there, then "自检" to confirm.

2. Reload the Chrome extension with the new permissions
   chrome://extensions -> reload "THU Auto Login"
   (the debugger permission was removed and nativeMessaging added)

Verify from the extension side:
   the menu bar icon shows "THU" without a warning marker.

Uninstall with: bash "$HERE/uninstall.sh"
--------------------------------------------------------------------
NEXT
