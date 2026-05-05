#!/usr/bin/env bash
set -e

REPO_RAW="${UA_BOX_REPO_RAW:-https://raw.githubusercontent.com/fanassasj/ua-box/main}"
INSTALL_DIR="${UA_BOX_INSTALL_DIR:-/root/ua-box}"

download() {
  local url="$1"
  local output="$2"

  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$output"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$output" "$url"
  else
    echo "未找到 curl 或 wget，无法下载安装文件。"
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

download "$REPO_RAW/ua-box" "$INSTALL_DIR/ua-box"
download "$REPO_RAW/install-local.sh" "$INSTALL_DIR/install-local.sh"
download "$REPO_RAW/README.md" "$INSTALL_DIR/README.md"
download "$REPO_RAW/.gitignore" "$INSTALL_DIR/.gitignore"

if [ ! -f "$INSTALL_DIR/config" ]; then
  download "$REPO_RAW/config" "$INSTALL_DIR/config"
else
  echo "检测到已有配置文件，保留: $INSTALL_DIR/config"
fi

chmod +x "$INSTALL_DIR/ua-box" "$INSTALL_DIR/install-local.sh"
"$INSTALL_DIR/install-local.sh"

echo "安装完成，运行 ua 打开面板。"
