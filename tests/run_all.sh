#!/usr/bin/env bash
# 运行本插件的全部适配测试。
#
#   bash tests/run_all.sh
#
# 说明：
# - 套件 1/2 使用内置 astrbot 桩件，任何环境都能跑。
# - 套件 3 需要上游 AstrBot 源码（默认 /tmp/astrbot_ref，可用 ASTRBOT_REF 覆盖）。
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
WS="$(dirname "$REPO")"
PY="${PYTHON:-python3}"
FAILED=0

run() {
  local title="$1"; shift
  echo "===== $title ====="
  if ! "$@"; then
    FAILED=1
  fi
  echo
}

run "1) 平台适配套件（桩件）" \
  env PYTHONPATH="$WS" "$PY" -m "$(basename "$REPO").tests.test_platform_adaptation"

run "2) 导入/注册冒烟" \
  env PYTHONPATH="$WS" "$PY" -m "$(basename "$REPO").tests.test_import_smoke"

if [ -f "${ASTRBOT_REF:-/tmp/astrbot_ref}/astrbot/core/message/components.py" ]; then
  run "3) 上游真实组件校验" "$PY" "$HERE/test_real_components.py"
else
  echo "===== 3) 上游真实组件校验：跳过（未找到 ASTRBOT_REF=${ASTRBOT_REF:-/tmp/astrbot_ref}）====="
  echo
fi

if [ "$FAILED" -eq 0 ]; then
  echo "全部通过 ✅"
else
  echo "存在失败用例 ❌"
fi
exit "$FAILED"
