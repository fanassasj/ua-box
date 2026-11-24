# UA Box

UA Box 是一个轻量 Linux 命令行运维面板，整合系统管理、Docker、rclone、面板工具、网络测试和常用脚本入口。

当前版本：**v0.6.4**

适合 VPS、独服、轻量服务器的日常管理。脚本以 Bash 编写，尽量减少依赖；部分系统级操作需要 root 权限。

## 快速安装

一键安装到 `/root/ua-box`（包含 `curl` 自动前置安装）：

```bash
apt-get install curl -y && curl -fsSL https://raw.githubusercontent.com/fanassasj/ua-box/main/install.sh | bash
```

或者使用通用 curl / wget 一键指令：

```bash
curl -fsSL https://raw.githubusercontent.com/fanassasj/ua-box/main/install.sh | bash
```

如果没有 `curl`，也可以直接使用 `wget`：

```bash
wget -qO- https://raw.githubusercontent.com/fanassasj/ua-box/main/install.sh | bash
```

也可以用 Git 安装，适合需要本地修改或提交代码的场景：

```bash
git clone https://github.com/fanassasj/ua-box.git /root/ua-box
cd /root/ua-box
chmod +x ua-box
./ua-box install-links
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
5.  一键初始化优化
6.  Docker 管理
7.  工作区管理
8.  rclone 管理
9.  测试脚本合集
10. UA 设置
```

## 功能概览

### 系统工具

- 系统更新、系统清理
- **查看端口占用状态** (内置 awk 引擎智能排版过滤长路径噪音，纯净展示协议/端口/进程)
- 用户/密码生成器
- 系统时区调整
- 切换系统更新源
- 定时任务管理
- 虚拟内存大小调整
- 命令行历史记录查看/清理
- **修改 hostname**
- **BBR 拥塞控制**（一键开启/关闭，自动检测内核版本，写入 sysctl 永久生效）
- **防火墙管理**（自动识别 ufw / firewalld / iptables，支持查看规则、放行/删除端口）
- **SSH 安全加固与防暴破**（支持修改默认端口 + 防火墙自动放行、一键导入/抓取 GitHub 密钥公钥、全系统用户 SSH 权限智能诊断、当前活跃 SSH 会话/连接追踪、Fail2ban 防暴破与解封、密码/Root 登录管理）
- **一键初始化优化**（Swap → 更新系统 → 安装常用工具 → 安装 Docker → 检查系统状态）
  - 512MB、1GB、2GB 内存建议 2GB Swap；4GB 内存建议 1～2GB，默认选择 2GB。更大内存沿用原有建议（内存 2 倍，最多 8GB）。
  - 已有活动 Swap 保留；新建时至少保留 2GB 磁盘余量，空间受限时缩小建议大小。Swap 未就绪则停止初始化，避免低内存机器直接执行更新。
  - 原有可选 hostname、网络/系统环境调优和 BBR 保留，在 Docker 安装后、最终状态检查前执行。
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
- Caddy 反代管理（支持宿主机原生部署与 Docker 容器化部署，双栈自适应管理）
- Nginx Proxy Manager 可视化面板
- **it-tools 工具箱**（支持 Docker 与 Docker Compose 双模部署）
- **File Browser 文件浏览器**（选择管理目录，默认 `/root/filebrowser/files`；支持 Docker 与 Docker Compose 双模部署）
- **OpenList 网盘系统** (AList 社区开源分支，支持 Docker 和 Docker Compose 双模部署，内置管理员密码一键重置与日志提取)
- **Uptime Kuma 服务监控面板** (轻量级自建站点与 Docker 健康状态监控，支持 Docker 和 Docker Compose 双模部署)

面板工具顶部会显示统一状态页，包含静态与动态端口映射识别。检测到已安装 Caddy 时，部署面板工具后可一键生成 Caddy 反代配置（自动申请 SSL / 隐藏暴露端口），亦支持在各面板控制台随时一键挂载 Caddy 反代。

### Docker 管理

- Docker 安装/卸载
- Docker 镜像源配置，支持多个备用安装源
- 容器创建、启动、停止、重启、删除（列表分类显示 `docker run` 与 `Docker Compose` 运行状态）
- 查看容器日志、网络、资源占用
- 镜像、网络、卷管理
- Docker 占用分析和清理
- **限制 Docker 容器日志最大体积** (一键/安全合并配置 daemon.json 日志容量限制，防爆磁盘)
- **Docker Compose 项目控制台** (秒级从容器 Labels 抓取工作路径，独立操作 Up/Down/Start/Stop/Restart/Logs/Pull)

### rclone 管理

- 安装/更新 rclone
- 配置 rclone
- 查看远程盘
- 列出远程目录
- copy / sync
- 挂载和卸载远程盘（支持开机自启/Systemd守护，内置异常自动重启的频率限制）
- 查看 rclone mount 进程
- 打开 rclone ncdu
- 配置安全摘要和 remote 连接检测
- **手动编辑 rclone.conf 配置文件**（编辑前自动生成精确时间戳备份文件）

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

- Docker 服务状态、异常容器和 Docker Compose 部分停止项目
- rclone remote 连接失败
- systemd 服务未运行
- Caddy SSL 证书即将过期（< 7 天）预警
- CPU、内存、磁盘、系统负载高占用
- 最近 UA Box 操作日志

支持通过 `ua status --notify` 或 `ua notify` 配置 Telegram Bot / 通用 Webhook（兼容企微/飞书/钉钉/Discord/Bark）定时自动巡检并在发现异常时主动推送告警。确认无需处理的异常可通过 `ua ignore` 管理，不需要手动编辑配置文件。

### 只读诊断与告警状态

```bash
ua doctor                # 只读检查依赖、配置权限、目录访问条件和功能前提
ua doctor --json         # 结构化报告，适合自动检查
ua notify                # 配置渠道；选项 7 设置重复告警冷却时间
ua status --notify       # 执行一次巡检，按状态变化和冷却时间通知
ua notify test           # 测试渠道，不改变去重或恢复记录
```

`doctor` 不创建日志、备份、状态或临时目录，不执行配置文件，不安装依赖、不自动修复、不启动服务，也不发送消息。它检查 Bash/Python 和基本工具、配置语法及权限、目录访问条件、可用磁盘空间、Docker 的只读连接及 Unix Socket 前提、Compose 配置输出能力、rclone/FUSE/systemd、bbolt 与通知依赖。配置里的动态 Shell 语句只标记为无法静态确认，不执行，也不显示凭据或连接地址。退出码为 `0`（通过）、`1`（存在提醒）、`2`（存在错误）；可选功能的 `SKIP` 表示未具备相应条件。访问权限只按现有元数据判断，不通过写文件验证；诊断通过不代表业务恢复验收通过。

告警默认冷却 **3600 秒**。每个故障对象有独立标识，指标数值变化不会绕过去重；新故障、严重级别变化和下一次巡检确认的恢复无需等待冷却。持续故障到期后再次提醒，恢复通知确认送达后只发一次。冷却可设置为 `0–604800` 秒，`0` 表示每次巡检都提醒。

通知按渠道分别确认和记账：一个渠道失败只重试该渠道，平台未确认时不更新其去重状态。状态默认保存在 `state/notify/state.json`，目录权限 `700`、文件权限 `600`，可通过 `UA_NOTIFY_STATE_DIR` 指定独立绝对路径；状态只保留故障信息和渠道指纹，不保存 Token、Webhook 地址或密码。`UA_NOTIFY_COOLDOWN_SECONDS` 可覆盖默认冷却时间。Cron 和手动巡检共用文件锁，避免并发重复发送；状态文件损坏、权限不安全或写入失败会报错，不会静默重置。

告警定时任务使用专属标记 `# ua-box:status-notify`。开启/关闭只管理带该标记的任务，以及当前入口或脚本路径对应、由旧版生成的精确任务格式；其他程序的 `status --notify`、自定义计划、注释和环境设置均保留。设置前保存权限为 `600` 的 `backups/crontab-before-*.bak`，复核任务未被其他进程修改，安装后再核对结果。读取失败不会按空任务覆盖；写入失败或结果不一致不会提示成功。本工具的任务设置和巡检共用锁；不遵守此锁的外部编辑仍可能在最终复核后竞争，结果不一致时需人工核对保护副本。

恢复必须基于同一对象的明确正常结果：删除容器/项目、移除 remote、健康检查仍在启动、跳过检查、服务状态未知或探测失败都不会被当作恢复。明确加入忽略列表的对象会静默退役，不发送“已恢复”。通知是至少一次送达设计：若进程在平台接收后、保存确认记录前中断，或响应在网络中丢失，下次可能重复发送；没有平台幂等接口时不能承诺严格只发送一次。

## 更新与迁移的可靠性

更新与迁移共用一个持久化恢复引擎。操作前记录原容器 ID、名称、运行状态、网络、原目录身份及文件清单，日志使用临时文件替换与 `fsync`。SIGINT/SIGTERM 会触发恢复；进程被 SIGKILL 或主机重启后，可通过恢复记录继续核对实际状态，不能依赖内存中的完成标记。

- **容器恢复**：各网络分别恢复，单个网络失败不会跳过旧容器启动。恢复后重新读取容器 ID、名称、运行状态、健康状态和网络；任何步骤或日志持久化失败都不报告完整恢复成功。日志磁盘不可写时继续尝试启动旧容器，并向终端输出实际观测状态。
- **保留容器的重启隔离**：在日志中记录原始 `RestartPolicy` 后，将旧容器设为 `restart=no` 并读取实际值确认，随后才停写或切换目录。候选容器同样以 `no` 创建，验证通过并记录提升意图后才恢复正式策略。回滚先隔离、停止候选容器，再恢复被选定旧容器的原策略（含 on-failure 重试次数）。保留的其他实例保持 `no`；隔离、策略恢复或实际值校验失败均不报告恢复成功。日志包含旧容器与保留候选的实测策略，支持按日志重复恢复。
- **原始数据保留**：停写后在同一文件系统的私有兄弟目录建立完整候选副本；数据库验证通过后使用 Linux `renameat2(RENAME_EXCHANGE)` 原子交换。新版本只使用候选数据，失败时交换回原始目录，保留失败版本数据用于检查。更新成功也保留旧容器和原始目录，不自动删除容器、数据卷或恢复副本。
- **迁移不混用旧文件**：版本 2 归档携带完整目录根、文件数量、大小、SHA-256、权限及数据库验证记录。独立解包并验证全部内容后整目录切换，目标中的额外文件留在原目录副本，不混入恢复结果。多个目录通过日志协调，切换期间要求全部使用者停写；这不是跨目录的单次原子操作。
- **回滚不复制覆盖原件**：根据持久化的设备号、inode 和原内容清单核对实际位置，使用原子交换恢复，不向唯一原始副本写入。发现未知目标身份、原件校验失败、挂载点、跨文件系统或不支持原子交换时拒绝盲目覆盖。重复恢复不撤销旧服务重新启动后的合法写入。
- **恢复记录**：`backups/.container-update-<名称>.json` 保存当前操作，`backups/update-*.json` 保留已完成操作；目录记录位于 `backups/data-*.json` 或 `backups/pre-import-*/journal.json`。原始目录保存在目标旁权限为 `700` 的 `.ua-recovery-*` 目录。配置快照和日志可能含凭据，权限为 `600`，请勿公开。它们均不自动清理。
- **归档兼容性**：只接受版本 2 完整性清单，旧版无清单归档不能自动恢复数据库。拒绝链接、特殊文件、路径越界、重复成员及未知配置赋值；原有归档保持不变。安装器仍先暂存校验，再逐文件原子替换，失败恢复原文件。

项目部署模板对应的数据库与策略：

| 面板 | 默认持久化位置 | 数据库 | 一致性与独立验证 |
|---|---|---|---|
| Uptime Kuma 1 | `/root/uptime-kuma/kuma.db` | SQLite | 完整停写副本 → SQLite backup API → 完整性/外键检查 → 独立解包重验 |
| OpenList | `/root/openlist/data/data.db`，以配置为准 | 默认 SQLite，可配置外部数据库 | 识别配置与实际文件；SQLite 使用同一验证流程，外部配置阻止自动操作 |
| Nginx Proxy Manager | 默认 `/root/nginx-proxy-manager/data/database.sqlite`，以实际配置为准 | 默认 SQLite，可配置外部数据库 | 共用实际后端识别函数；配置文件优先于环境变量，不能按残留文件推断 |
| File Browser | `/root/filebrowser/filebrowser.db` | Bolt/bbolt，不能当成 SQLite | 停写完整副本 → 官方 `bbolt check` → 独立解包再校验 |
| Caddy、it-tools | 配置、证书或无数据库 | 无内置业务数据库 | 完整目录清单与文件内容校验 |

上游依据：[Kuma 1 数据库实现](https://github.com/louislam/uptime-kuma/blob/1.23.16/server/database.js)、[OpenList 数据库配置](https://openlistteam.github.io/docs/config/configuration.html)、[NPM 数据库配置](https://nginxproxymanager.com/setup/)、[File Browser Bolt 存储](https://github.com/filebrowser/filebrowser/blob/master/storage/bolt/bolt.go)。

**安全边界**：自动更新仅支持本机 Unix Socket Docker、独立目录挂载及可验证的本地数据库。导出和迁移要求所有相关容器/宿主进程已经停写；运行中的共享卷使用者会阻止切换。外部 MySQL/PostgreSQL 等数据库必须先进行原生一致性逻辑备份，并在独立实例完成恢复演练；本实现未提供自动外部数据库恢复，因此明确阻止这类操作，不会把文件归档认定为数据库备份。旧版 File Browser 的全盘可写挂载 `/:/srv` 也会阻止自动更新，因为无法为全盘写入提供安全回退；本轮不会更改已有容器挂载。其他自定义数据库路径或存储驱动必须先验证适配。

NPM 更新、导出及迁移采用同一后端识别结果：读取正式容器的 inspect 配置，并用 `docker cp` 只读获取该容器实际选择的 JSON 配置（包含镜像层旧配置），不执行容器命令。外部 MySQL/PostgreSQL、参数不完整、配置读取失败、自定义启动命令或运行实例不唯一时明确拒绝；日志不输出密码、配置内容或连接字符串。导出记录脱敏的后端类型、配置来源和实际 SQLite 路径；迁移核对源记录及已有目标实例的实际配置。旧 NPM 归档缺少后端记录时拒绝自动恢复，避免把遗留 SQLite 当成当前数据库。

容器更新和迁移需要 Python 3；bbolt 备份还需要官方 `bbolt` 命令，缺少校验器时失败退出。未配置健康检查的容器只验证进程持续运行，不能据此确认全部业务功能。硬件断电、远程存储及真实 Docker 引擎版本差异仍需独立环境演练。

```bash
ua verify-backup /path/to/backup.tar.gz   # 独立解包、清单及数据库验证，不恢复生产目录
ua recover /path/to/journal.json         # 确认后按实际身份继续恢复；不会盲目覆盖未知数据
```

## 开发验证

测试只使用模拟 Docker/防火墙/服务命令、临时 SQLite/bbolt 数据库及隔离子进程，不操作现有容器或生产数据库。另使用 `systemd-escape` 和 `systemd-analyze verify` 检查服务名及临时单元文件语法，不启动服务。完整测试要求 `bbolt v1.4.3` 位于 PATH，缺少时真实 bbolt 测试会失败，不会跳过。

```bash
# 将官方校验器安装到选定的测试工具目录，不必安装到系统目录
GOBIN=/tmp/ua-test-bin go install go.etcd.io/bbolt/cmd/bbolt@v1.4.3
export PATH="/tmp/ua-test-bin:$PATH"
bash -n ua-box
bash -n install.sh
bash -n install-local.sh
shellcheck ua-box install.sh install-local.sh
python3 -m unittest discover -s tests -v
```

Docker 策略模拟器覆盖 `always`、`unless-stopped`、`on-failure`、手动停止和 daemon 重启的差异，并检查模拟运行实例是否竞争端口或写入相同目录；它不实现真实端口分配、外部编排器或 Docker 网络驱动。本轮 P1 测试未使用真实 Docker，真实信号、SQLite 和 bbolt 验证均只发生在隔离测试文件/进程中。

测试覆盖网络部分恢复、重命名与创建响应中断、SQLite WAL/结构迁移回退、bbolt 损坏、文件缺失/哈希不符、旧残留文件、复制失败、ENOSPC、日志写入失败、真实 SIGINT/SIGTERM/SIGKILL 和重复恢复。原有 17 项场景保留，其中容器成功路径加强为断言旧容器和原始数据均保留，迁移夹具升级为带完整清单的目录备份。GitHub Actions 使用固定版本 bbolt 执行同一套测试，并运行 Bash 语法和 ShellCheck 错误级检查；完整 ShellCheck 仍可能报告原项目的 warning/style 提示。

`tests/test_doctor_notifications.py` 另外验证完整 doctor 命令不会执行配置或创建目录，并验证通知去重、冷却、严重级别变化、单项恢复、未知状态、单渠道失败、锁竞争及状态写入失败。所有 Docker、服务状态、rclone 和发送操作使用模拟命令；测试不调用真实 Docker 或通知平台。

## 快捷命令

```bash
ua                 # 打开主菜单
ua ps              # 系统信息
ua sys             # 系统工具
ua tools           # 常用工具
ua panel           # 面板工具
ua docker          # Docker 管理
ua work            # 工作区管理
ua rclone          # rclone 管理
ua test            # 测试脚本合集
ua top             # 打开动态 Live 实时系统监控控制台 (CPU/内存/网络网速/容器)
ua fzf             # 打开 fzf 全功能交互式模糊搜索导航面板
ua status          # UA 状态检查
ua doctor          # 只读诊断（加 --json 输出结构化报告）
ua status --notify # 执行异常巡检并在发现异常时主动推送告警
ua notify          # 打开状态巡检主动告警配置菜单 (Telegram / Webhook)
ua export          # 一键打包导出全部面板配置与数据（支持联动上传 rclone）
ua import          # 从已验证的版本 2 归档整目录恢复
ua verify-backup <归档> # 独立验证备份
ua recover <记录>  # 核对实际状态并恢复
ua ignore          # 异常忽略管理
ua ignore current  # 忽略当前异常项
ua ignore rclone quark # 忽略指定 rclone remote 异常
ua settings        # 打开 UA Box 设置菜单
ua config          # 编辑配置文件
ua log             # 查看最近日志
ua backup          # 备份当前脚本
ua update          # 按配置 URL 更新脚本
ua init            # 一键初始化优化（Swap→更新→工具→Docker→状态检查）
ua bbr             # BBR 拥塞控制管理
ua fw              # 防火墙管理
ua ssh             # SSH 安全加固与防暴破
ua ssh-port        # 修改 SSH 监听端口
ua ssh-key         # 导入/安装 SSH 密钥公钥
ua f2b             # Fail2ban 防暴破管理
ua fb              # 打开 File Browser 文件浏览器管理
ua ol              # 打开 OpenList 网盘系统管理
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
UA_MAX_BACKUPS=10
GITHUB_PROXY_URLS="https://ghp.ci https://gh-proxy.com https://ghproxy.net"
UA_NOTIFY_TG_BOT_TOKEN=""
UA_NOTIFY_TG_CHAT_ID=""
UA_NOTIFY_WEBHOOK_URL=""
```

`UA_IGNORE_CONTAINERS`、`UA_IGNORE_SERVICES` 和 `UA_IGNORE_RCLONE_REMOTES` 用空格分隔多个名称，用于隐藏已确认无需处理的历史异常。推荐通过 `ua ignore` 写入这些配置。

## 更新

如果已经通过 Git 安装：

```bash
cd /root/ua-box
git pull
./ua-box install-links
```

也可以在配置文件里设置 `UA_BOX_UPDATE_URL`，然后执行：

```bash
ua update
```

Git 更新会先显示当前分支和上游，拒绝分离 HEAD、缺少上游、已跟踪文件有本地修改或无法快进的情况。获取上游后先验证目标脚本，再按已确认的提交快进；不自动 stash、reset 或创建合并提交。URL 更新不显示或记录完整地址，替换前检查 UA Box 标识、Bash 语法及内嵌 Python 语法。脚本备份失败、更新失败或快捷入口设置失败会明确返回错误，未完整核验时不重新执行新版本。

磁盘测速在待测文件系统创建独占的 `.ua-io.*` 临时目录，核对目录身份后只清理该目录，不覆盖当前目录的同名用户文件。默认写入 `256 MiB`，可通过 `UA_IO_TEST_MB` 设置 `1–4096`，`UA_IO_TEST_DIR` 指定待测目录（默认当前目录）；开始前至少为文件以外保留 `128 MiB` 可用空间。写入失败、写入量不足或被中断时不报告成功速率。不支持 direct I/O 的文件系统会明确失败，不会静默换成其他测速方式。

推荐 raw 地址：

```bash
UA_BOX_UPDATE_URL="https://raw.githubusercontent.com/fanassasj/ua-box/main/ua-box"
```

## 安全说明

- `UA_BOX_TMP_DIR` 指定临时文件的父目录；程序只清理自己创建的随机子目录。SIGINT/SIGTERM 分别退出为 130/143，监控界面的信号处理不影响返回后的菜单。
- 批量容器操作先显示实际名单并确认，排除 `-ua-old-`、`-ua-failed-` 保留实例；已停止且带恢复操作标签的容器不参与批量启动，需使用恢复记录处理。
- 切换端口绑定沿用原镜像 ID、环境、挂载、网络和其他端口；保留原容器并隔离重启策略。失败时核对身份恢复，未验证实际运行端口时不会提示成功。此操作不做数据库迁移或数据库回退。
- Compose 端口切换通过实际容器标签定位项目及文件，在同目录暂存并用 `docker compose config --format json` 对比完整生效配置。当前支持单文件、单一明确的短格式 TCP 端口；多文件、变量/范围/长格式端口等不能可靠改写的配置会在停止容器前拒绝。不会运行项目级 `compose down`。
- Docker 日志、IPv6 和手动配置编辑统一在暂存文件上校验，保留其他配置；缺少 `dockerd --validate` 或原 JSON 无效时拒绝修改。应用前确认重启影响；重启失败时恢复原配置并核验服务，原始备份不会被覆盖。进程被 SIGKILL 或宿主机掉电后的服务恢复仍需人工核对保留的备份。
- 容器端口访问菜单管理当前 DOCKER-USER 的 DROP 规则。未指定来源时预检 IPv4/IPv6 两个地址族；缺少工具或链时拒绝操作。规则未自动持久化，不能替代外部连通性测试，也不会绕过其他网络限制。
- 创建容器的命令参数和下载 URL 不进入审计日志，日志权限为 `600`。不会自动清理已有日志中的历史内容。
- 新建面板不再使用递归 `777`。File Browser 默认管理独立目录，选择系统目录需要再次确认；凭据按实际镜像启动日志或版本说明获取。
- SSH 切换端口前检查占用及防火墙结果，切换后核验监听；Fail2ban 仅更新 `[sshd]` 配置段。仍需保留原终端并用独立连接测试云安全组和端到端访问。
- rclone 服务使用 `systemd-escape --path` 编码挂载路径，拒绝覆盖已有同名服务，校验单元文件和实际挂载结果；缺少 FUSE 时停止并提示安装。
- 告警识别 `unhealthy` 并排除隔离保留实例。Webhook 使用渠道对应的 JSON 和业务响应校验；可在 `ua notify` 选择 `auto/wecom/feishu/dingtalk/discord/bark`，自建地址需明确类型。HTTP 成功不代表平台业务成功；真实推送尚需使用测试渠道验证。
- 高风险操作会二次确认，支持输入 `yes` 或 `y` 继续，输入 `n` 取消。
- 高风险操作会显示影响范围和对象/命令，例如重装系统、开放所有端口、卸载脚本、清空定时任务、删除容器数据。
- rclone 配置检测不会显示 token、密码、client_secret。
- `logs/`、`backups/`、运行状态文件不建议上传到公开仓库。
- 如果 `config` 中写入私有 URL、token、密码或账号信息，公开前需要先清理。

## 外部脚本来源

UA Box 只在选择对应菜单项时下载并执行外部脚本：

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

### v0.6.4

- 新增**「状态巡检主动告警推送 (`ua notify` / `ua status --notify`)」**：支持配置 Telegram Bot 与通用 Webhook（兼容企微/飞书/钉钉/Discord/Bark），支持一键开启每 30 分钟 Cron 自动后台巡检，在发现容器停止、磁盘满、Rclone 掉线或 SSL 证书即将到期时主动告警。
- 新增**「面板配置与数据一键迁移 / 备份中心 (`ua export` / `ua import`)」**：自动扫描打包 UA Box 配置、Caddy 配置与证书、OpenList、File Browser、Uptime Kuma、NPM、it-tools、`rclone.conf` 及挂载服务，支持一键联动上传至 Rclone 远程网盘与安全校验恢复。
- 新增**「面板容器一键无损更新镜像版本」**：在 NPM、OpenList、File Browser、Uptime Kuma、it-tools 管理菜单中新增无损升级选项，自动兼容 Compose 与 `docker run` 部署，完整保留原端口安全策略与 `ua-net` 内网连接。
- 新增**「Caddy 反代站点映射看板与 SSL 证书到期预警」**：Caddy 状态页直观列出所有已配置域名与反代目标，`ua status` 自动检测剩余有效期 `< 7 天` 的 SSL 证书并预警。
- 安全与防呆加固：
  - SSH 禁用密码认证前强制检测系统是否存在有效公钥（`authorized_keys`），防止无密钥锁死服务器。
  - UFW 启用防火墙前自动保底放行当前 SSH 监听端口，防止启用瞬间 SSH 失联。
  - 防火墙管理面板新增 Docker `0.0.0.0` 端口绕过 UFW/Firewalld 风险自动扫描与提醒。
  - Fail2ban 规则动态联动自定义 SSH 端口，修改 SSH 端口时自动同步更新 `/etc/fail2ban/jail.local`。
- 稳定性与底层修复：
  - 升级 Caddyfile 块解析器为 `awk` 花括号深度追踪，支持嵌套子块安全删除/覆写，并在校验失败时自动回滚备份。
  - 修复容器切换 `127.0.0.1` / `0.0.0.0` 端口模式后脱离 `ua-net` 网络导致 Caddy 容器反代 502 的问题。
  - 修复流量统计与限流自动关机未排除 Docker/虚拟网卡导致流量重复累加的问题。
  - 新增全模块历史备份文件自动轮转清理（`UA_MAX_BACKUPS=10`）、GitHub 加速代理池多源轮询回退（`GITHUB_PROXY_URLS`）及 Rclone 挂载 FUSE 依赖自动检测安装。

### v0.6.3

- 面板全面支持**「Docker Run / Docker Compose 双轨自选部署模式」**（涵盖 OpenList、NPM、File Browser、it-tools、Uptime Kuma），部署交互统一体验。
- 重构**「一键安全切换引擎」**：实现 127.0.0.1 安全防扫模式与公网 0.0.0.0 直连模式智能热切换，自动兼容传统 Docker 容器与 Compose Yaml 栈，配置更新零阻塞。
- 重构**「Nginx Proxy Manager」**底层：迁移至 Compose 纯净架构，原生补齐 81 管理端口防扫加密及一键反代链路，100% 融入 UA-Net 安全代理网络。
- 完善**「Caddy 反代管理器」**：新增可视化反代规则单点删除功能与 Caddy (系统级/容器级) 彻底清理卸载模块。

### v0.6.2

- 新增「Docker IPv6 双栈原生网络支持」：在 Docker 管理菜单中提供一键配置功能（`"ipv6": true`, `"ip6tables": true`, `"fixed-cidr-v6": "fd00:172:17::/64"`），为桥接网络提供零配置的公网 IPv6 NAT66 出站与端口映射能力。
- 新增「Uptime Kuma 开源监控面板」：面板工具模块新增 Uptime Kuma 服务与站点健康监控，支持 Docker Run / Docker Compose 双模部署与全自动 Caddy 域名反代联动。
- 新增「Docker Compose / Docker 引擎版本双显」：Docker 管理面板自动检测并同屏展示 Docker 引擎与 Docker Compose 插件最新版本。
- 新增「rclone 空配置自动初始化与编辑」：解决当 rclone 未配置或文件为空时 `13. 编辑 rclone.conf` 报错退出的问题，自动创建路径及空白文件，支持智能无伤备份与手工编辑。
- 优化「`ua top` 动态 Live 监控无闪烁重绘」：使用 ANSI `\033[H` / `\033[K` 光标原地重绘机制替代原有的清屏刷新，消除全屏闪烁；并新增了 Swap 虚拟内存 bar、系统 Load Average、TCP ESTABLISHED 实时连接数统计以及 `C` / `M` 键按 CPU/内存 交互式排序功能。
- 优化「面板工具全自动 Caddy 反代集成」：所有面板工具在安装成功后及菜单选项中，均支持自动检测/自动一键安装 Caddy 并配置域名 HTTPS 证书。
- 优化「一键安装脚本与 curl 自动检测」：在安装命令中添加了推荐的 `apt-get install curl -y` 前置保底命令，并在 `install.sh` 中集成了缺失 `curl` 时的多包管理器自动补全安装逻辑。

### v0.6.1

- 新增「系统端口占用美化排版」：重写端口状态查看逻辑，智能过滤 `ss` / `netstat` 输出中的长路径 (如 docker-proxy、Cgroup) 噪音，干净整洁地提取并对齐协议、地址、端口与纯净进程名 (PID)。
- 新增「全系统用户 SSH 权限诊断」：SSH 安全面板新增全范围用户（Root + UID≥1000 正常 shell 用户）扫描，以直观表格形式输出 Home 目录、.ssh、authorized_keys 的严格权限 (StrictModes) 验证结果及状态警告。
- 新增「SSH 活跃连接追踪」：SSH 安全面板新增活跃终端查看功能，双维度整合系统 `who` 登录会话与底层 `ss` (ESTABLISHED) 状态网络连接，精准排查连接来源。

### v0.6.0

- 新增「Docker Compose 项目控制台」：支持秒级从容器元数据 Labels 提取项目目录，提供 Up/Down/Start/Stop/Restart/Logs/Pull 全生命周期管理。
- 新增「Docker 容器日志最大体积限制」：提供一键在 daemon.json 中合并或限制日志体积（50MB x 3），通过内置 JSON 语法校验与自动备份防止损坏。
- 新增「状态面板与异常监控联动」：工具总览与 `ua status` 增加对 Docker Compose 状态比率的读取与偏离（部分停止）报警。
- 新增「Caddy 部署 Docker 双模支持」：Caddy 管理支持原生服务与 Docker 容器化双模，并对配置与证书路径实现持久化宿主机映射。
- 新增「工作区退出常驻向导」：Tmux 会话右下角以红底白字高亮常驻退出快捷键（`退出: Ctrl+B, D`），彻底防止会话误杀。
- 新增「RClone 开机挂载防死循环」：Systemd 自启服务内置启动频率限制（300秒限5次），防 CPU 空转。
- 新增「RClone 配置文件手动编辑」：增加一键编辑 `rclone.conf` 入口（附带精确时间戳自动备份）。
- 新增「File Browser 文件浏览器」：一键在宿主机部署极简 Web 文件管理器，支持服务器全盘文件可视化上传、下载与分享。
- 新增「OpenList 网盘系统」：集成 OpenList (AList 分支) 部署，提供 Docker Run 与 Docker Compose 双模部署，并支持管理员初始密码日志提取与一键重置。
- 新增「系统虚拟化细节友好映射」：自动识别并美化输出显示（如将 `google` 映射为 `GOOGLE CLOUD (GCP)`）。
- 新增「全局软链接安装逻辑内置」：将原 install-local.sh 的配置逻辑直接整合至 ua-box install-links 命令行，同时在 ua update 更新后自动修复环境。
- 优化「BBR 拥塞控制」：增加对 LXC/OpenVZ 架构容器的自动拦截，防止写入无效内核配置；增加系统可用算法双重校验；并扩展了半连接队列等高级网络调优参数。
- 优化「防火墙与 NPM 管理权限」：全局或关键模块加入 root 前置提权校验，防止非 root 账号执行产生无用报错。
- 优化「主菜单布局与视觉观感」：主界面重构为极简双栏网格，对中英文字符进行了像素级对齐，并统一规范了分割线。
- 优化「工作区删除控制」：工作区删除选项支持了通过活跃会话编号、工作区后缀数字进行快速输入与匹配。
- 优化 Docker 容器列表，清晰区分普通 `docker run` 与 `Docker Compose` 状态。
- 彻底移除老旧的哪吒探针管理模块与外部脚本。

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
