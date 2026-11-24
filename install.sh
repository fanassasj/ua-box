#!/usr/bin/env bash
set -e

REPO_RAW="${UA_BOX_REPO_RAW:-https://raw.githubusercontent.com/fanassasj/ua-box/main}"
INSTALL_DIR="${UA_BOX_INSTALL_DIR:-/root/ua-box}"

ensure_curl() {
  if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    echo "检测到未安装 curl，正在自动安装 curl 工具..."
    if command -v apt-get >/dev/null 2>&1; then
      apt-get update -qq && apt-get install -y -qq curl ca-certificates
    elif command -v yum >/dev/null 2>&1; then
      yum install -y curl ca-certificates
    elif command -v dnf >/dev/null 2>&1; then
      dnf install -y curl ca-certificates
    elif command -v apk >/dev/null 2>&1; then
      apk add --no-cache curl ca-certificates
    fi
  fi
}

download() {
  local url="$1"
  local output="$2"

  ensure_curl
  if command -v curl >/dev/null 2>&1; then
    curl --connect-timeout 10 --max-time 120 -fsSL "$url" -o "$output"
  elif command -v wget >/dev/null 2>&1; then
    wget --timeout=15 --tries=2 -qO "$output" "$url"
  else
    echo "未找到 curl 或 wget，且无法自动安装依赖。"
    exit 1
  fi
}

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "请使用 root 用户运行安装命令。"
    exit 1
  fi
}

need_root
mkdir -p "$INSTALL_DIR"
# 暂存目录与目标位于同一文件系统，替换使用原子 rename。
STAGE_DIR="$(mktemp -d "$INSTALL_DIR/.ua-install.XXXXXX")"
chmod 700 "$STAGE_DIR"
COMMITTED=0
CHANGED_FILES=()
cleanup_install() {
  local name rollback_failed=0
  if [ "$COMMITTED" -eq 0 ]; then
    for name in "${CHANGED_FILES[@]}"; do
      if [ -f "$STAGE_DIR/previous/$name" ]; then
        mv -f "$STAGE_DIR/previous/$name" "$INSTALL_DIR/$name" || rollback_failed=1
      else
        rm -f "$INSTALL_DIR/$name" || rollback_failed=1
      fi
    done
  fi
  if [ "$rollback_failed" -eq 0 ]; then
    rm -rf "$STAGE_DIR"
  else
    echo "自动恢复未完成，原文件保留在: $STAGE_DIR/previous" >&2
  fi
}
trap cleanup_install EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

FILES=(ua-box README.md .gitignore)
if [ ! -e "$INSTALL_DIR/config" ]; then
  FILES+=(config)
else
  echo "检测到已有配置文件，保留: $INSTALL_DIR/config"
fi
for name in "${FILES[@]}"; do
  download "$REPO_RAW/$name" "$STAGE_DIR/$name"
done
bash -n "$STAGE_DIR/ua-box"
grep -q '^UA_BOX_VERSION=' "$STAGE_DIR/ua-box"
grep -q '^create_local_links()' "$STAGE_DIR/ua-box"
[ ! -f "$STAGE_DIR/config" ] || bash -n "$STAGE_DIR/config"
chmod 755 "$STAGE_DIR/ua-box"
[ ! -f "$STAGE_DIR/config" ] || chmod 600 "$STAGE_DIR/config"
mkdir "$STAGE_DIR/previous"
for name in "${FILES[@]}"; do
  # 不跟随目标符号链接或覆盖目录。
  if [ -L "$INSTALL_DIR/$name" ] || [ -d "$INSTALL_DIR/$name" ]; then
    echo "安装目标必须是普通文件: $INSTALL_DIR/$name" >&2
    exit 1
  fi
  [ ! -f "$INSTALL_DIR/$name" ] || cp -p "$INSTALL_DIR/$name" "$STAGE_DIR/previous/$name"
  CHANGED_FILES+=("$name")
  mv -f "$STAGE_DIR/$name" "$INSTALL_DIR/$name"
done
"$INSTALL_DIR/ua-box" install-links
COMMITTED=1
echo "安装完成，运行 ua 打开面板。"
