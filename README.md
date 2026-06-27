# UA Box

UA Box 是一个轻量 Linux 命令行运维面板，整合系统管理、Docker、rclone、面板工具、网络测试和常用脚本入口。

当前版本：**v0.5.0**

适合 VPS、独服、轻量服务器的日常管理。脚本以 Bash 编写，尽量减少依赖；部分系统级操作需要 root 权限。

## 快速安装

一键安装到 `/root/ua-box`：

```bash
curl -fsSL https://raw.githubusercontent.com/fanassasj/ua-box/main/install.sh | bash
```

如果没有 `curl`，可以使用：

```bash
wget -qO- https://raw.githubusercontent.com/fanassasj/ua-box/main/install.sh | bash
```

也可以用 Git 安装，适合需要本地修改或提交代码的场景：

```bash
git clone https://github.com/fanassasj/ua-box.git /root/ua-box
cd /root/ua-box
chmod +x ua-box install-local.sh
./install-local.sh
ua
```

也可以直接在项目目录运行：

```bash
cd /root/ua-box
./ua-box
```

安装脚本会创建两个入口：

```bash
/usr/local/bin/ua
/usr/local/bin/ua-box
```

如果 `/usr/local/bin/ua` 已存在且不是符号链接，安装脚本不会自动覆盖。

## 主菜单

```text
1.  系统信息
2.  系统工具
3.  常用工具
4.  面板工具
5.  V2bX 管理
6.  Docker 管理
7.  工作区管理
8.  rclone 管理
9.  测试脚本合集
10. UA 设置
```

## 功能概览

### 系统工具

- 系统更新、系统清理
- 查看端口占用状态
- ROOT 密码登录模式
- 用户/密码生成器
- 系统时区调整
- 切换系统更新源
- 定时任务管理
- 虚拟内存大小调整
- 命令行历史记录查看/清理
- **修改 hostname**
- **BBR 拥塞控制**（一键开启/关闭，自动检测内核版本，写入 sysctl 永久生效）
- **防火墙管理**（自动识别 ufw / firewalld / iptables，支持查看规则、放行/删除端口）
- **一键初始化优化**（系统更新 + 常用工具批量安装 + Swap 自动设为内存 2 倍 + Docker + 可选 hostname）
- 一键重装系统
- 开放所有端口
- 重启服务器
- 限流自动关机
- 卸载 UA Box

### 常用工具

支持单独安装/卸载常用工具，也支持一键安装推荐工具包：

```text
curl wget sudo socat htop btop iftop unzip tar tmux vim nano git jq ncdu fzf lsof net-tools
```

### 面板工具

- WARP 管理
- Caddy 反代管理
- Nginx Proxy Manager 可视化面板
- it-tools 工具箱
- 哪吒探针管理

面板工具顶部会显示统一状态页，包括安装状态、运行状态、端口和访问地址。Nginx Proxy Manager 会自动识别容器名，例如 `npm` 或 `nginx-proxy-manager`，避免后续管理混淆。

### Docker 管理

- Docker 安装/卸载
- Docker 镜像源配置，支持多个备用安装源
- 容器创建、启动、停止、重启、删除
- 查看容器日志、网络、资源占用
- 镜像、网络、卷、Compose 管理
- Docker 占用分析和清理

### rclone 管理

- 安装/更新 rclone
- 配置 rclone
- 查看远程盘
- 列出远程目录
- copy / sync
- 挂载和卸载远程盘
- 查看 rclone mount 进程
- 打开 rclone ncdu
- 配置安全摘要和 remote 连接检测

配置检测会先逐个测试 remote 连接，再显示摘要；不会输出 token、密码、client_secret 等敏感字段。

### 测试脚本合集

- ChatGPT 解锁状态检测
- Region 流媒体解锁测试
- yeahwu 流媒体解锁检测
- IP 质量体检
- besttrace / mtr_trace / nxtrace 回程测试
- Superspeed / i-abc / NetQuality 网络质量测试
- YABS 综合性能测试
- Geekbench 5 CPU 测试
- bench.sh
- 融合怪测评
- speedtest-cli
- iperf3
- Ping / 路由追踪
- 自定义脚本 URL

### UA 状态检查

`ua status` 会汇总关键异常：

- Docker 服务状态和异常容器
- rclone remote 连接失败
- systemd 服务未运行
- CPU、内存、磁盘、系统负载高占用
- 最近 UA Box 操作日志

确认无需处理的异常可通过 `ua ignore` 管理，不需要手动编辑配置文件。

## 快捷命令

```bash
ua                 # 打开主菜单
ua ps              # 系统信息
ua sys             # 系统工具
ua tools           # 常用工具
ua panel           # 面板工具
ua nezha           # 哪吒探针管理
ua v2bx            # V2bX 管理
ua docker          # Docker 管理
ua work            # 工作区管理
ua rclone          # rclone 管理
ua test            # 测试脚本合集
ua status          # UA 状态检查
ua ignore          # 异常忽略管理
ua ignore current  # 忽略当前异常项
ua ignore rclone quark # 忽略指定 rclone remote 异常
ua config          # 编辑配置文件
ua log             # 查看最近日志
ua backup          # 备份当前脚本
ua update          # 按配置 URL 更新脚本
ua init            # 一键初始化优化（更新+工具+Swap+Docker）
ua bbr             # BBR 拥塞控制管理
ua fw              # 防火墙管理
ua install curl jq # 安装指定工具
```

## 配置文件

默认路径：

```bash
/root/ua-box/config
/root/ua-box/logs/ua-box.log
/root/ua-box/backups/
```

可以通过以下命令编辑配置：

```bash
ua config
```

常用配置项：

```bash
DEFAULT_WORKSPACE_PREFIX="work"
DEFAULT_RCLONE_MOUNT_ARGS="--vfs-cache-mode writes"
UA_BOX_UPDATE_URL="https://raw.githubusercontent.com/fanassasj/ua-box/main/ua-box"
UA_IGNORE_CONTAINERS="ms-oauth2-proxy"
UA_IGNORE_SERVICES=""
UA_IGNORE_RCLONE_REMOTES=""
UA_STATUS_DISK_WARN=80
UA_STATUS_DISK_CRIT=90
```

`UA_IGNORE_CONTAINERS`、`UA_IGNORE_SERVICES` 和 `UA_IGNORE_RCLONE_REMOTES` 用空格分隔多个名称，用于隐藏已确认无需处理的历史异常。推荐通过 `ua ignore` 写入这些配置。

## 更新

如果已经通过 Git 安装：

```bash
cd /root/ua-box
git pull
./install-local.sh
```

也可以在配置文件里设置 `UA_BOX_UPDATE_URL`，然后执行：

```bash
ua update
```

推荐 raw 地址：

```bash
UA_BOX_UPDATE_URL="https://raw.githubusercontent.com/fanassasj/ua-box/main/ua-box"
```

## 安全说明

- 高风险操作会二次确认，支持输入 `yes` 或 `y` 继续，输入 `n` 取消。
- 高风险操作会显示影响范围和对象/命令，例如重装系统、开放所有端口、卸载脚本、清空定时任务、删除容器数据。
- rclone 配置检测不会显示 token、密码、client_secret。
- `logs/`、`backups/`、运行状态文件不建议上传到公开仓库。
- 如果 `config` 中写入私有 URL、token、密码或账号信息，公开前需要先清理。

## 外部脚本来源

UA Box 只在选择对应菜单项时下载并执行外部脚本：

- Nezha: `https://github.com/nezhahq/scripts`
- V2bX: `https://github.com/wyx2685/V2bX-script`
- Docker: `https://get.docker.com`
- WARP: `https://gitlab.com/fscarmen/warp`
- rclone: `https://rclone.org/install.sh`
- YABS: `https://github.com/masonr/yet-another-bench-script`
- OpenAI Checker: `https://github.com/missuo/OpenAI-Checker`
- RegionRestrictionCheck: `https://github.com/lmc999/RegionRestrictionCheck`
- yeahwu unlock check: `https://github.com/yeahwu/check`
- mtr_trace: `https://github.com/zhucaidan/mtr_trace`
- i-abc Speedtest: `https://github.com/i-abc/Speedtest`
- 融合怪: `https://gitlab.com/spiritysdx/za`

## 更新日志

### v0.5.0

- 新增「修改 hostname」功能，支持合法性校验并同步更新 `/etc/hosts`
- 新增「一键初始化优化」：系统更新 + 常用工具批量安装（失败自动逐包重试）+ Swap 自动设为内存 2 倍 + Docker 安装 + 可选 hostname 修改，完成后打印详细结果报告
- 新增「BBR 拥塞控制」：一键开启/关闭 BBR，自动检测内核版本（需 ≥ 4.9），写入 `/etc/sysctl.d/` 永久生效
- 新增「防火墙管理」：自动识别 ufw / firewalld / iptables，支持查看规则、放行端口（TCP/UDP）、删除规则
- 新增快捷命令 `ua init` / `ua bbr` / `ua fw`
- 优化 `ua update` 空 URL 提示，直接显示推荐配置地址

### v0.4.x

- 早期版本迭代，基础运维功能逐步完善

## 许可证

未指定许可证。公开使用前建议补充 `LICENSE` 文件。
