#!/usr/bin/env bash
# Builds both native components into ./build.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "$HERE/native-host/build.sh"
bash "$HERE/key-agent/build.sh"

echo
echo "all components built into $HERE/build"
