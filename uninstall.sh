#!/usr/bin/env bash
#
# Reverses install.sh. Deliberately keeps ~/Library/Application Support/THUAutoLogin
# unless --purge is given, so logs survive a reinstall.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SUPPORT_DIR="$HOME/Library/Application Support/THUAutoLogin"
NM_MANIFEST="$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.thu.autologin.host.json"
LA_DIR="$HOME/Library/LaunchAgents"
AGENT_LABEL="com.thu.autologin.keyagent"
AGENT_PLIST="$LA_DIR/$AGENT_LABEL.plist"
APP_NAME="THUAutoLoginKeyAgent"
APP_INSTALL_DIR="${APP_INSTALL_DIR:-$HOME/Applications}"
APP_DEST="$APP_INSTALL_DIR/$APP_NAME.app"

say() { printf '%s\n' "$*"; }

say "==> stopping and removing the LaunchAgent"
launchctl bootout "gui/$UID/$AGENT_LABEL" >/dev/null 2>&1 || true
launchctl unload -w "$AGENT_PLIST" >/dev/null 2>&1 || true
rm -f "$AGENT_PLIST"

say "==> removing the key agent app"
pkill -f "$APP_DEST/Contents/MacOS/$APP_NAME" >/dev/null 2>&1 || true
rm -rf "$APP_DEST"
rm -f "$SUPPORT_DIR/keyagent.sock"

say "==> removing the native messaging manifest"
rm -f "$NM_MANIFEST"

if [ "${1:-}" = "--purge" ]; then
  say "==> purging support files and logs"
  rm -rf "$SUPPORT_DIR"
  rm -rf "$HOME/Library/Logs/THUAutoLogin"
else
  say "==> keeping $SUPPORT_DIR (use --purge to remove it too)"
fi

cat <<'NEXT'

--------------------------------------------------------------------
Removed. One thing cannot be scripted:

  System Settings > Privacy & Security > Accessibility still lists
  "THUAutoLoginKeyAgent". Select it and press the minus button to
  remove the stale entry.

Restart Chrome so it forgets the native messaging host.
--------------------------------------------------------------------
NEXT
