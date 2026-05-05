#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="/usr/local/bin/ua"

chmod +x "$SCRIPT_DIR/ua-box"
ln -sf "$SCRIPT_DIR/ua-box" /usr/local/bin/ua-box

if [ -e "$TARGET" ] && [ ! -L "$TARGET" ]; then
  echo "检测到 $TARGET 已存在且不是符号链接。"
  echo "为避免覆盖已有命令，未自动设置 ua。"
  echo "确认要替换时，可先备份原文件，再执行：ln -sf $SCRIPT_DIR/ua-box $TARGET"
else
  ln -sf "$SCRIPT_DIR/ua-box" "$TARGET"
  echo "已安装全局命令: ua"
fi

echo "备用命令仍可使用: ua-box"
