#!/usr/bin/env bash
# Live2D 一站式服务启动脚本
#   ./start.sh          启动服务（自动打开浏览器）
#   ./start.sh --setup  仅准备环境（构建 psd2live）
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(dirname "$HERE")"

# 载入本地 token 配置（stack/.env），省去每次 export
if [ -f "$HERE/.env" ]; then
  set -a; . "$HERE/.env"; set +a
fi

PSD2LIVE_DIR="$WORKSPACE/psd2live"
APP="$PSD2LIVE_DIR/build/compose/binaries/main/app/psd2live.app"
LAUNCHER="$APP/Contents/MacOS/PSD2Live"
PORT="${PORT:-8770}"

say() { printf '\033[1;34m▸\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m!\033[0m %s\n' "$*"; }

# ---------- 1. JDK（仅构建需要；运行时用 app 自带 JRE）----------
ensure_jdk() {
  if [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/java" ]; then
    ok "使用已有 JAVA_HOME: $JAVA_HOME"; return
  fi
  local jh_file="$HERE/tools/java_home.txt"
  if [ -f "$jh_file" ] && [ -x "$(cat "$jh_file")/bin/java" ]; then
    export JAVA_HOME="$(cat "$jh_file")"
    ok "使用自带 JDK 21: $JAVA_HOME"; return
  fi
  if command -v java >/dev/null 2>&1 && java -version 2>&1 | grep -q '"21'; then
    ok "使用系统 JDK 21"; return
  fi
  say "未找到 JDK 21，正在下载 Temurin 21（约 190MB）..."
  mkdir -p "$HERE/tools"; cd "$HERE/tools"
  local arch; [ "$(uname -m)" = "arm64" ] && arch=aarch64 || arch=x64
  curl -sL -o jdk21.tar.gz \
    "https://api.adoptium.net/v3/binary/latest/21/ga/mac/${arch}/jdk/hotspot/normal/eclipse"
  tar xzf jdk21.tar.gz && rm -f jdk21.tar.gz
  echo "$HERE/tools/$(ls -d jdk-*/ | head -1)Contents/Home" > "$HERE/tools/java_home.txt"
  export JAVA_HOME="$(cat "$HERE/tools/java_home.txt")"
  ok "JDK 21 就绪: $JAVA_HOME"
  cd "$HERE"
}

# ---------- 2. 构建 psd2live ----------
ensure_psd2live() {
  if [ -x "$LAUNCHER" ]; then ok "psd2live 已构建"; return; fi
  say "首次运行：构建 psd2live（约 5-10 分钟，含 Gradle 依赖下载）"
  ensure_jdk
  cd "$PSD2LIVE_DIR"
  ./gradlew --no-daemon -Dorg.gradle.jvmargs="-Xmx3g" createDistributable
  [ -x "$LAUNCHER" ] || { warn "构建失败：未生成 $LAUNCHER"; exit 1; }
  ok "psd2live 构建完成"
  cd "$HERE"
}

# ---------- 3. Token 检查 ----------
check_token() {
  if [ -n "${HF_TOKEN:-}" ]; then
    ok "HF_TOKEN 已配置（额度充足）"
  elif [ -n "${MODELSCOPE_TOKEN:-}" ]; then
    ok "MODELSCOPE_TOKEN 已配置"
  else
    warn "未配置任何 token —— 匿名额度每天仅 1~2 次拆分！"
    warn "  HuggingFace（免费）: https://huggingface.co/settings/tokens"
    warn "    export HF_TOKEN=hf_xxx"
    warn "  ModelScope（国内推荐，免费）: https://modelscope.cn/my/myaccesstoken"
    warn "    export MODELSCOPE_TOKEN=ms_xxx"
  fi
}

case "${1:-}" in
  --setup) ensure_psd2live; ensure_jdk; ok "环境准备完成"; exit 0 ;;
  --help|-h) sed -n '2,4p' "$0"; exit 0 ;;
esac

ensure_psd2live
check_token
say "启动服务 http://127.0.0.1:$PORT ..."
exec python3 "$HERE/serve.py" --port "$PORT" --open