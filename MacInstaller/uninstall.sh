#!/bin/bash
#
# Removes the system-wide THU Auto Login installation created by
# THUAutoLogin-<version>.pkg.
#
# Re-executes itself under sudo, because it has to remove things from
# /Applications and /Library.
set -u

AGENT_LABEL="com.thu.autologin.keyagent"
SUPPORT_DIR="/Library/Application Support/THUAutoLogin"
AGENT_APP="/Applications/THUAutoLoginKeyAgent.app"
NM_MANIFEST="/Library/Google/Chrome/NativeMessagingHosts/com.thu.autologin.host.json"
PKG_ID="com.thu.autologin.pkg"

if [ "$(id -u)" != "0" ]; then
  echo "需要管理员权限来删除 /Applications 和 /Library 下的文件。"
  exec sudo -p "请输入密码: " "$0" "$@"
fi

say() { printf '%s\n' "$*"; }

say "==> 停止并移除 LaunchAgent"
CONSOLE_USER="$(stat -f '%Su' /dev/console 2>/dev/null || echo '')"

# Cover every real user, not just whoever is at the console right now, so a
# leftover agent never survives for a user who is merely logged out.
for home in /Users/*; do
  [ -d "$home" ] || continue
  user="$(basename "$home")"
  plist="$home/Library/LaunchAgents/$AGENT_LABEL.plist"
  uid_num="$(id -u "$user" 2>/dev/null || true)"
  [ -n "$uid_num" ] || continue

  launchctl bootout "gui/$uid_num/$AGENT_LABEL" 2>/dev/null || true
  if [ -f "$plist" ]; then
    rm -f "$plist"
    say "    已移除 $plist"
  fi
done

# The agent may also have been started directly; pkill is best effort.
pkill -f "$AGENT_APP/Contents/MacOS/" >/dev/null 2>&1 || true

say "==> 移除应用"
rm -rf "$AGENT_APP"
say "    已移除 $AGENT_APP"

say "==> 移除主机程序与扩展"
rm -rf "$SUPPORT_DIR"
say "    已移除 $SUPPORT_DIR"

say "==> 移除原生消息清单"
rm -f "$NM_MANIFEST"
say "    已移除 $NM_MANIFEST"

say "==> 注销安装包收据"
pkgutil --forget "$PKG_ID" >/dev/null 2>&1 && say "    已注销 $PKG_ID" || true

cat <<'NEXT'

--------------------------------------------------------------------
卸载完成。还有两件事脚本做不到：

1. 系统设置 → 隐私与安全性 → 辅助功能
   里面可能残留 "THUAutoLoginKeyAgent"，选中后按减号删除。

2. Chrome 里已加载的扩展需要在 chrome://extensions 手动移除。

建议随后重启 Chrome，让它忘掉已删除的原生消息主机。
--------------------------------------------------------------------
NEXT
