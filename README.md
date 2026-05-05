# UA Box

私人使用的 Linux 命令行面板，参考 `k` 命令的交互方式，保留轻量菜单和常用运维入口。

## 功能

- 系统信息显示
- 系统工具：系统更新/清理、端口占用、ROOT 密码登录、时区、更新源、定时任务、虚拟内存、历史记录、限流自动关机
- 常用工具安装/卸载，支持一键安装推荐工具包
- 面板工具：WARP 管理、Caddy 反代、Nginx Proxy Manager、it-tools、哪吒探针
- 哪吒探针管理
- V2bX 安装管理
- Docker 管理：全局状态、容器、镜像、网络、卷、Compose、daemon.json、清理和卸载
- tmux 工作区模块：work1-work10、SSH 常驻模式、自定义工作区、后台命令
- rclone 管理：配置、远程盘查看、目录列出、复制、同步、挂载、配置安全摘要和连接检测
- 测试脚本菜单：解锁检测、线路测试、性能测试、融合怪、自定义脚本
- UA 状态检查：Docker 异常容器、rclone remote 失败、服务未运行、磁盘/内存/CPU 高占用汇总
- 配置文件、操作日志、脚本备份和自更新
- 高风险操作统一二次确认，支持 `yes/y` 继续、`n` 取消，并显示影响范围和对象/命令

## 使用

在项目目录内运行：

```bash
chmod +x ./ua-box
./ua-box
```

快捷入口：

```bash
./ua-box ps
./ua-box sys
./ua-box tools
./ua-box panel
./ua-box nezha
./ua-box v2bx
./ua-box docker
./ua-box work
./ua-box rclone
./ua-box test
./ua-box status
./ua-box config
./ua-box log
./ua-box backup
./ua-box update
./ua-box install curl wget jq
```

## 安装为全局命令

```bash
cd /root/ua-box
./install-local.sh
ua
```

安装脚本会创建两个入口：

```bash
/usr/local/bin/ua
/usr/local/bin/ua-box
```

如果 `/usr/local/bin/ua` 已存在且不是符号链接，安装脚本不会自动覆盖。

## 配置与日志

默认路径：

```bash
/root/ua-box/config
/root/ua-box/logs/ua-box.log
/root/ua-box/backups/
```

可以用 `ua config` 修改默认脚本 URL、工作区前缀、rclone 挂载参数等配置。

`ua update` 默认不会联网更新。需要先在配置文件里设置：

```bash
UA_BOX_UPDATE_URL="https://example.com/ua-box"
```

如果代码上传到 GitHub，可以把更新地址设置为 raw 文件地址，例如：

```bash
UA_BOX_UPDATE_URL="https://raw.githubusercontent.com/fanassasj/ua-box/main/ua-box"
```

## GitHub 上传

首次上传示例：

```bash
cd /root/ua-box
git init
git add ua-box install-local.sh config README.md .gitignore
git commit -m "Initial UA Box release"
git branch -M main
git remote add origin https://github.com/fanassasj/ua-box.git
git push -u origin main
```

如果远程仓库还没创建，需要先在 GitHub 页面创建 `ua-box` 仓库，或使用 GitHub CLI/API 创建后再 push。

## 外部脚本来源

- Nezha: `https://github.com/nezhahq/scripts`
- V2bX: `https://github.com/wyx2685/V2bX-script`
- Docker: `https://get.docker.com`
- WARP: `https://gitlab.com/fscarmen/warp`
- rclone: `https://rclone.org/install.sh`
- YABS: `https://github.com/masonr/yet-another-bench-script`
- OpenAI Checker: `https://github.com/missuo/OpenAI-Checker`
- yeahwu unlock check: `https://github.com/yeahwu/check`
- mtr_trace: `https://github.com/zhucaidan/mtr_trace`
- i-abc Speedtest: `https://github.com/i-abc/Speedtest`
- 融合怪: `https://gitlab.com/spiritysdx/za`

面板只在你选择对应菜单项时下载并执行外部安装/管理脚本。

## 注意

- `logs/`、`backups/`、运行状态文件不建议上传。
- 如果配置文件里写入了私有 URL、token、密码或账号信息，上传前先清理。
