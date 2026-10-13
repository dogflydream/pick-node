# 代理节点批量体检工具

一键批量检测代理节点的**真实出口地区**，以及**是否适合使用 Google AI 服务**
（Google / Gemini / Antigravity）。

输入一行一个代理 URI，输出每个节点的：出口 IP、出口国家、Google 可达性、
Gemini 与 Antigravity 可用性、延迟、综合可用性。

```
输入: proxiers.txt (132 个节点)
  ↓  每个节点启动一个独立内核实例，独立本地端口
  ↓  通过该端口发起真实网络请求 (Cloudflare trace / Google / Gemini / Antigravity)
输出: results.json          全量明细
      proxiers_google.txt   可用节点代理池

查看: Web 面板 (推荐)  →  python web/server.py
      CLI 实时输出      →  python main.py
```

---

## 1. 特性

- **多协议**：VLESS（含 Reality / Vision / ws / gRPC）、VMess、Trojan、
  Shadowsocks（含 2022）、Hysteria2（含端口跳跃 `mport` 与证书 pin `pinSHA256`）、TUIC
- **每节点独立实例**：自动分配本地端口（默认 20001 起），自动生成临时配置，
  测试完立即回收进程与端口，不留孤儿进程
- **有界并发**：默认 10 并发，可调至 50 / 100，内存与 CPU 不会随节点数线性爆炸
- **内核自动探测**：自动在 v2rayN 目录中找到 `sing-box.exe` / `xray.exe`，
  按协议自动选内核（Hysteria2 / TUIC 仅 sing-box 支持）
- **不污染系统**：不读取系统代理设置，不会误用本机 v2rayN 的出口
- **Web 面板**：深色数据面板，可按地区/协议/可用性/风险筛选、排序、搜索、勾选，
  一键把可用节点导出成一行一个 URI
- **网页端导入节点**：粘贴或拖入 `.txt` 即可导入，自动去重、自动忽略无效行，
  导入前先告诉你会新增多少 —— 不需要懂命令行或手动改文件
- **网页端重新检测**：勾选节点即可在页面触发真实检测，
  实时看进度，结果合并写回（不会冲掉其它节点的历史结果）
- **CLI**：无 GUI 依赖，也能纯命令行跑，实时进度输出


---

## 2. 环境要求

| 项目 | 要求 |
| --- | --- |
| 系统 | Windows 10 / 11 |
| Python | 3.10+（本项目用 3.13 验证） |
| uv | 推荐，用于一键同步依赖（见 §3.1；没有也可用内置 venv） |
| 内核 | `sing-box.exe` 与 `xray.exe`（v2rayN 自带即可） |
| 依赖 | `httpx[socks]`、`fastapi` + `uvicorn`（Web 面板需要） |

本项目已在以下版本实测通过：

```
sing-box version 1.13.4
Xray 26.3.27
fastapi 0.143.0 / uvicorn 0.54.0
```

内核默认从 `C:\software\v2rayN-windows-64\bin` 下自动探测（会识别
`sing_box\` 与 `xray\` 子目录）。如路径不同，修改 `.env` 里的 `V2RAYN_BIN`，
或直接指定 `SING_BOX_PATH` / `XRAY_PATH`。

---

## 3. 安装与运行

### 3.1 安装依赖

推荐用 [uv](https://docs.astral.sh/uv/)：一条命令完成虚拟环境创建与依赖同步。

```powershell
cd proxy-tester-3.0.0

# 安装 uv (已装过可跳过)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 建虚拟环境并同步依赖
uv venv
uv sync
```

`uv sync` 会按 `pyproject.toml` / `uv.lock` 装好 `httpx[socks]`、`fastapi`、`uvicorn`，
不需要手动逐个 `pip install`。

没有 uv 也可以用标准库的 venv：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install "httpx[socks]" fastapi uvicorn
```

### 3.2 准备配置

```powershell
copy .env.example .env
```

然后编辑 `.env`，把 `V2RAYN_BIN` 改成你机器上 v2rayN 的 `bin` 目录。工具会在该目录下
自动寻找 `sing_box\` 与 `xray\` 子目录，通常不需要另装内核。

### 3.3 运行

**Web 面板（推荐）**：

```powershell
.venv\Scripts\python.exe web\server.py
```

然后浏览器打开 `http://127.0.0.1:8787`。在页面右上角点「导入节点」粘贴代理链接
（一行一个），再点「重新检测」即可。

换端口：`--port 9000`；改代码后自动重载：`--reload`。

**命令行**：

```powershell
.venv\Scripts\python.exe main.py                  # 用 .env 与默认文件名
.venv\Scripts\python.exe main.py -c 20            # 20 并发
.venv\Scripts\python.exe main.py --only-usable    # 代理池只留综合可用节点
```

**跑测试**（可选）：

```powershell
.venv\Scripts\python.exe tests\test_parser.py
.venv\Scripts\python.exe tests\test_verdicts.py
.venv\Scripts\python.exe tests\test_import.py
```

### 3.4 发布包里有什么

发布包（`proxy-tester-3.0.0-src.zip`）**只有源码**，不含节点、结果与内核。

| 有 | 没有（需自备） |
| --- | --- |
| `core/` `web/` `main.py` 全部源码 | 代理节点链接（`proxiers.txt`） |
| `README.md` 与配置模板 `.env.example` | 内核 `sing-box.exe` / `xray.exe` |
| `tests/` 回归测试、`tools/` 构建脚本 | 虚拟环境与历史结果 |

> 内核直接用你已装的 v2rayN 自带的那份即可，工具会自动在
> `V2RAYN_BIN` 下寻找 `sing_box\` 与 `xray\` 子目录。

### 3.5 节点文件格式

命令行模式默认读取 `proxiers.txt`，把代理 URI 一行一个写进去：

```
vless://uuid@example.com:443?encryption=none&security=reality&sni=...&pbk=...&sid=...#节点名
hysteria2://password@example.com:35000?sni=www.apple.com&mport=35000-39000#节点名
trojan://password@example.com:443?sni=example.com#节点名
ss://base64methodpass@example.com:8388#节点名
```

- 空行、`#` 与 `//` 开头的行会被忽略
- 重复节点会自动去重
- 解析失败的行会被跳过并打印原因，不会中断整体流程
- **用 Web 面板的话不用手写这个文件**，页面里「导入节点」会帮你写

### 3.6 命令行输出示例

```
[OK]   VLESS     SG   1973ms  AWS新加坡01      Gemini AVAILABLE Antigravity AVAILABLE
[NO]   HYSTERIA2 HK   1691ms  香港无限7        <访问 google.com 被重定向到 com.hk>
[NO]   VLESS     US   1019ms  美国圣何塞02     <Google 未通过 (fail)>
```

---

## 4. 命令行参数

| 参数 | 说明 |
| --- | --- |
| `-i, --input` | 节点文件，默认 `proxiers.txt` |
| `-o, --output` | 结果 JSON，默认 `results.json` |
| `--pool` | 代理池输出，默认 `proxiers_google.txt` |
| `-c, --concurrency` | 最大并发实例数（默认 10） |
| `--limit` | 只测前 N 个节点（快速试跑） |
| `--kernel` | `auto` / `sing-box` / `xray` |
| `--timeout` | 单请求超时秒数（默认 15） |
| `--start-timeout` | 等待实例监听秒数（默认 12） |
| ~~`--no-antigravity`~~ | 已失效：本版本 Antigravity 跟随 Gemini、不再独立探测；参数保留仅为兼容旧脚本 |
| `--only-usable` | 代理池只写综合可用节点 |
| `--clean-configs` | 结束后清理临时配置 |
| `-q, --quiet` | 精简输出 |

常用示例：

```powershell
# 20 并发
.venv\Scripts\python.exe main.py -c 20

# 先拿 10 个快速验证
.venv\Scripts\python.exe main.py --limit 10

# 只要能用 Gemini 的节点进代理池
.venv\Scripts\python.exe main.py --only-usable

# 强制用 xray
.venv\Scripts\python.exe main.py --kernel xray
```

---

## 5. Web 面板

命令行不方便逐条看结果，Web 面板把 `results.json` 变成一个可筛选、可排序、
可一键导出 URI 的界面。

### 5.1 启动

```powershell
.venv\Scripts\python.exe web\server.py
```

然后浏览器打开 **http://127.0.0.1:8787**

```
.venv\Scripts\python.exe web\server.py --port 9000    # 换个端口
.venv\Scripts\python.exe web\server.py --reload       # 开发模式, 改代码自动重载
```

启动后会打印数据文件、节点数、访问地址。

> **安全提示**：面板默认只监听 `127.0.0.1`，只有本机能访问。
> 结果里含出口 IP 与节点凭据，**不要**用 `--host 0.0.0.0` 暴露到公网。

### 5.2 界面功能

| 区域 | 能做什么 |
| --- | --- |
| 指标卡 | 潜在可用 / 总节点 / Google 可达 / Gemini 可用 / Antigravity / **链路 RTT** / **首字节** |
| 搜索 | 按节点名、服务器地址、出口 IP 搜索（支持中文） |
| 地区 / 协议 / Gemini / 风险 | 下拉筛选，带数量统计 |
| 只看潜在可用 | 开关，默认开启（只看能用的，关掉可看全部并排查失败原因） |
| 排序 | 推荐优先 / 链路 RTT 最低 / 首字节最快 / 总耗时最短 / 按地区 / 按协议 / 按名称；也可点表头切换升降序 |
| 表格 | 节点名、协议、出口 IP、地区、Google / 风险 / Gemini / Antigravity 状态、**三个延迟指标**，全部带颜色标识 |
| 勾选 | 表头全选，或逐条勾选 |
| 出口地区分布 | 点地区 chip 可直接筛选 |
| 重新载入 | 重新读取 `results.json`（测试跑完不用重启面板） |

快捷键：`/` 聚焦搜索框，`Esc` 清空搜索。

### 5.3 导出可用节点 URI（核心功能）

界面上有两个导出入口：

- **导出潜在可用 URI** —— 一键导出所有潜在可用节点（默认行为）
- **复制所选 / 导出所选** —— 先勾选若干节点，再只导出这些

点击后**同时做两件事**：把 URI 复制到剪贴板，并下载一个 `.txt` 文件
（文件名形如 `nodes_usable_20261013_095727.txt`），内容是一行一个原始 URI，
可直接粘贴进 v2rayN 或其他工具。

「潜在可用」的判定标准（与后端 `core/store.py` 中 `is_potentially_usable` 一致）：

1. 链路可达（拿到了出口 IP）
2. Google 访问成功
3. **未命中风险重定向**（`risk_level != "high"`，即没被 Google 判定为代理/VPN 出口）
4. Gemini 未被明确拒绝（`available` 或 `likely`；`blocked` / `unavailable` 排除）

> 说明：`likely` 表示「出口地区不在已知白名单，但页面可达」，仍算潜在可用，
> 所以会被导出。如果你只想要最稳妥的那批，用 CLI 的 `--only-usable`
> （对应严格判定）。

### 5.4 在网页端导入新节点

**不需要懂命令行，也不需要手动编辑 `proxiers.txt`。**

点右上角 **导入节点**，然后把代理链接粘进去就行：

- **一行一个**链接，从机场网页/群聊/邮件里直接复制粘贴
- 也可以把 `.txt` 文件**拖进**输入框
- 支持 vless / vmess / trojan / ss / hysteria2 / tuic
- **重复的会自动去掉**，不用自己清理（同一个节点换个备注名也算重复）
- 空行、`#` 开头的注释行会自动忽略

粘贴后**立刻显示会导入什么**，确认无误再点按钮：

```
● 可新增 3    ● 重复跳过 1    ● 无法识别 1

准备导入 3 个新节点，1 个已存在会自动跳过，1 行无法识别。   [清空] [导入 3 个节点]
```

点 **查看明细** 能看到具体哪一行被跳过、为什么（例如
`不是合法的 URI (缺少 scheme://)`）。

导入只是**把链接写进节点文件**，不会自动开测。导入后点
**重新检测** 即可测试新节点。

两个新手常问的点：

- **判重看的是连接信息**（服务器/端口/UUID/密码），不是备注名。
  所以同一个节点写成不同名字，仍然只保留一条 —— 这是刻意的。
- 导入**不会覆盖**原有节点，只把真正新的追加到文件末尾。

### 5.5 在网页端直接重新检测

不用回命令行 —— 面板右上角的 **重新检测** 按钮可以直接触发真实网络检测。

流程：

1. （可选）**勾选**要重测的节点，或先筛选（例如只看「风险 = 高风险」）再全选
2. 点 **重新检测** → 确认条会告诉你本次范围
   - 有勾选：`已勾选 N 个节点`
   - 没勾选：`将检测节点文件中的全部节点`
3. 可调整 **并发数**（默认取 `.env` 里的 `CONCURRENCY`）
4. 点 **开始检测** → 顶部出现进度条，**实时**显示
   - `已完成 / 总数` 与百分比
   - 已用时间
   - 逐条结果会即时更新到表格对应行（新行有高亮动画）
   - 运行时可随时点 **停止**（剩下的节点不再启动，已跑完的结果照常写回）

检测完成后会自动刷新表格与统计，并把结果写回 `results.json` 与
`proxiers_google.txt`。

**关于写回方式（重要）**：采用**合并写回** —— 只覆盖本次测到的节点，
其它节点的历史结果原样保留。所以你可以放心地"只重测几个可疑节点"，
不会把已有的 132 条结果冲掉。

几点行为说明：

- 同一时间**只允许一个检测任务**，重复点击会返回 409 并提示等待
- 页面刷新后如果任务还在跑，会自动**重新接上**进度
- 进度用 SSE 推送；若浏览器/中间层不支持，会自动降级为轮询
- 检测期间 `results.json` **始终是合法 JSON**（不是先清空再写），
  所以在跑的时候刷新页面也不会读到半截文件

### 5.6 HTTP 接口

面板同时是一套简单的只读 API，方便脚本对接：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/summary` | 汇总统计 + 地区分布 |
| GET | `/api/facets` | 筛选项候选值（国家/协议/Gemini）及计数 |
| GET | `/api/nodes` | 节点列表，支持 `search` `country` `protocol` `gemini` `risk` `usable_only` `sort` `order` `limit` |
| GET | `/api/meta` | 数据文件、条数、更新时间 |
| GET | `/api/export` | 导出 URI。`scope=usable\|strict\|all`，`ids=a,b,c` 指定节点，`download=true` 触发附件下载 |
| POST | `/api/reload` | 强制重新读取结果文件 |
| POST | `/api/nodes/preview` | 预览导入（不写文件）。body: `{"text": "<多行 URI>"}` |
| POST | `/api/nodes/import` | 导入节点，自动去重。body: `{"text": "<多行 URI>"}` |
| POST | `/api/retest` | 启动重新检测。body: `{"node_ids": [...], "concurrency": 10, "limit": 0}` |
| GET | `/api/retest/status` | 查询检测状态（`since=N` 只取新增结果） |
| POST | `/api/retest/cancel` | 停止当前检测 |
| GET | `/api/retest/events` | SSE 实时进度流（`event: progress` / `done`） |

示例：

```powershell
# 拿到 80 个潜在可用节点的 URI（一行一个）
curl.exe "http://127.0.0.1:8787/api/export?scope=usable"

# 只要日本节点里 Gemini 可用的
curl.exe "http://127.0.0.1:8787/api/nodes?country=JP&gemini=available&sort=latency"

# 只重测高风险节点
curl.exe -X POST "http://127.0.0.1:8787/api/retest" -H "Content-Type: application/json" `
  -d '{\"node_ids\":[\"a1b2c3d4e5f6\"],\"concurrency\":5}'
```

---

## 6. 配置文件 `.env`

命令行参数优先级高于 `.env`。

```ini
V2RAYN_BIN=C:\software\v2rayN-windows-64\bin
KERNEL=auto                  # auto | sing-box | xray

NODES_FILE=proxiers.txt
RESULTS_FILE=results.json
POOL_FILE=proxiers_google.txt
ONLY_USABLE=false

CONCURRENCY=10               # 同时测试的节点数
REQUEST_TIMEOUT=15           # 单请求超时(秒)
START_TIMEOUT=12             # 等待实例监听(秒)
CHECK_ANTIGRAVITY=true
```

---

## 7. 输出格式

### 7.1 `results.json`

```json
[
  {
    "node": "vless://uuid@pq.aws63.yydjc.top:443?...",
    "tag": "AWS新加坡01 | 电信移动联通推荐",
    "node_id": "a266c9d0c910",
    "protocol": "VLESS",
    "server": "pq.aws63.yydjc.top",
    "port": 443,
    "local_port": 20003,
    "kernel": "sing-box",
    "ip": "67.159.48.146",
    "country": "SG",
    "country_name": "Singapore",
    "city": "",
    "asn_org": "",
    "country_source": "cloudflare-trace",
    "google": "success",
    "google_status": 200,
    "google_final_url": "https://www.google.com.hk/",
    "gemini": "available",
    "gemini_reason": "出口地区 SG (支持列表), 页面可达",
    "antigravity": "available",
    "antigravity_reason": "跟随 Gemini (Google 与 Gemini 均可用)",
    "rtt_min_ms": 199,
    "rtt_samples": [199, 214, 231],
    "connect_ms": 412,
    "ttfb_ms": 1084,
    "latency": 1412,
    "reachable": true,
    "ok": true,
    "confidence": "high",
    "error": "",
    "errors": []
  }
]
```

字段含义：

| 字段 | 说明 |
| --- | --- |
| `ip` / `country` | 出口 IP 与 ISO 国家码（来自 Cloudflare trace，权威） |
| `country_source` | 地区结论来源，便于追溯 |
| `google` | `success` / `fail` |
| `risk_level` | `high` / `low` / `none` —— 是否被 Google 判定为代理/VPN 出口 |
| `risk_flags` | 命中的风险关键词，如 `["hkredirect","com.hk"]` |
| `hk_redirect` | 是否命中 `pref=hkredirect` 本地化跳转 |
| `gemini` | `available` / `likely` / `blocked` / `unavailable` |
| `antigravity` | 与 `gemini` 始终一致（跟随 Gemini） |
| `rtt_min_ms` | **链路往返（小请求连测取最小值）—— 推荐用这个比快慢** |
| `rtt_samples` | RTT 的原始采样，便于判断稳定性 |
| `connect_ms` | 小请求的连接建立（TCP+TLS）耗时 |
| `ttfb_ms` | `google.com` 首字节时间（不含下载页面） |
| `latency` | `google.com` 端到端总耗时（含下载首页，旧口径） |
| `reachable` | 代理链路本身是否通 |
| `ok` | 综合可用：链路通 + Google 可达 + Gemini 未被明确拒绝 |
| `confidence` | 结论置信度 `high` / `medium` / `low` |

结果按「可用 → 延迟低」排序。

### 7.2 `proxiers_google.txt`

可用节点的原始 URI，一行一个，可直接被其他工具消费。

---

## 8. 判定逻辑与已知边界

这一节说明工具**为什么这样判定**，以及**哪些结论不能仅凭本工具下最终结论**。

### 8.1 出口地区

主来源为 `https://www.cloudflare.com/cdn-cgi/trace`，返回纯文本含 `ip=` 与 `loc=`，
由 Cloudflare 边缘节点权威判定，速度快、无配额。

> 注意：多数免费 geo-IP API 对同一 IP 可能给出**互相矛盾**的国家
> （实测某 IP：ipinfo 判 US、ip-api 判 CN）。因此本工具以 trace 为准，
> 其他来源仅作补充，不做结论依据。

> **出口地区只用于展示与参考，不参与"可用/不可用"的否决。**
> 是否可用看 §8.2 的重定向判据 —— 实测二者并不一致。

### 8.2 核心判据：`www.google.com` 是否被重定向到 `www.google.com.hk`

**这是判断节点能否用于 Gemini / Antigravity 最可靠的一条。**

```
访问 https://www.google.com
  最终落到 https://www.google.com.hk/   ->   确定不可用
  仍停在  https://www.google.com/       ->   未被重定向, 可继续判定
```

**为什么不看出口地区**：这两件事是独立的，实测数据如下（132 个节点）：

| 现象 | 数量 |
| --- | --- |
| 出口是 HK，**但访问 google.com 不跳转** | **21 / 24** |
| 出口**不是** HK，却跳转到 com.hk | **32**（JP 11 / US 12 / SG 7 / GB 1 / VN 1） |

所以「出口在 HK 就不能用」是错的 —— 会误杀那 21 个好节点；反过来只看地区，
又会漏掉 32 个出口在美日新、实际已被降级的节点。（早期版本犯过前者，已修正。）

**为什么这条能作为判据**：在纯 HTTP 层，跳转与否是**唯一**能区分二者的信号。
两类节点访问 `gemini.google.com` 都返回 200 与约 792KB 应用外壳，
页面内没有国家码、也没有 "not available in your country" 文案 ——
Gemini 真正的门禁在浏览器端 / 登录后 API 层，从页面本身看不出差别。

### 8.3 Gemini / Antigravity 判定分层

| 判定 | 含义 |
| --- | --- |
| `blocked` | 命中明确反面信号：**跳转到 `com.hk`**、`gemini/app` 被送到 `/sorry/`、HTTP 429 |
| `available` | 未命中上述信号，Google 与 Gemini 均可达，且出口地区在支持列表 |
| `likely` | 未命中反面信号，但出口地区不在已知列表 —— **建议登录实测** |
| `unavailable` | 请求层面就失败（超时 / TLS 错误 / 连接被拒），或 Google 未通过 |

> **建议**：把 `available` 当作可靠候选，把 `likely` 当作待验证候选。
> 最终能否使用 Gemini，仍取决于账号与登录状态，建议对筛选结果做一次人工复测。

**判定链条（本版本）**：

```
Google 可达 ─┐
Gemini 可达 ─┼─►  都 OK  ──►  未被重定向到 com.hk  ──►  Gemini 可用  ──►  Antigravity 跟随
            │
   风险重定向 ─┴─►  命中即否决（即使上面都 OK）
```

**Antigravity 不做独立探测，直接跟随 Google + Gemini 的结论。**
原因是上一版本独立请求 `antigravity.google` 的误判率很高（实测抽检 6 个判死的
节点，重试后 4 个其实可用，67% 假阴性）：这些共享出口会间歇性丢连接，
"单次失败即判死" 把抖动当成了结论；而且该探测排在请求序列最后，更容易撞上限流。
另外 `antigravity.google` 首页只证明静态页能下载，与客户端登录后能否真正使用
（用户反馈会报 `token: eof`）并没有可靠对应关系。

**硬性不变式**（有单元测试守着，见 `tests/test_verdicts.py`）：

- Google 未通过时，Gemini 一律不得判为 `available` / `likely`
- Antigravity 必须与 Gemini 结论完全一致
- 跳转到 `com.hk` 的节点必须被排除，无论其出口在哪个地区
- **出口地区本身不构成否决条件**（HK / CN 出口只要不跳转，仍可判可用）
- 任何阶段异常都不能留下 `unknown` 被当作可用

### 8.4 延迟指标：三个口径，别混用

工具采集三个延迟，**都包含经过代理链路的开销**，但测量范围不同：

| 字段 | 测的是什么 | 用途 |
| --- | --- | --- |
| `rtt_min_ms` | 对 `cloudflare.com/cdn-cgi/trace` **连测 3 次取最小值**。无重定向、body 仅几百字节 | **推荐**用它比较节点快慢 —— 最干净、抖动最小 |
| `connect_ms` | 该请求的连接建立（TCP + TLS 握手）耗时 | 看握手开销 |
| `ttfb_ms` | 访问 `google.com` 的**首字节**时间（不含 body 下载） | 反映"打开 Google 要等多久" |
| `latency` | 访问 `google.com` 的**端到端**耗时（含到 `google.com.hk` 的 302 跳转 + 下载约 50KB 首页） | 旧口径，仅为兼容保留 |

> **为什么不建议用 `latency` 排名**：它混入了重定向与首页 body 下载的时间。
> 实测同一批美国节点 `rtt ≈ 210ms` 而 `latency ≈ 2600ms` —— 差的这 2.4 秒
> 基本是服务端处理与传页面，不是链路慢。
>
> **为什么 RTT 取最小值**：首次请求要建连（DNS + TCP + TLS），后续请求复用连接
> 会明显更快。实测样本 `[1928, 614, 467]` —— 取最小值才代表这条链路的真实水平。

默认排序（推荐优先）也用 `rtt_min_ms`。

### 8.5 风险标记（代理/VPN 检测）

Google 会把「机场/代理出口」识别出来并降级处理。这类节点**即使出口在支持地区、
页面也返回 200，Gemini 与 Antigravity 实际仍不可用**。

识别信号按优先级：

| 优先级 | 信号 | 说明 |
| --- | --- | --- |
| 1（主判据） | 访问 `www.google.com` 被重定向到 `www.google.com.hk` | 见 §8.2，最可靠 |
| 2 | `gemini.google.com/app` 被 302 送到 `google.com/sorry` | Google 风控直接拦截 |
| 3 | URL 中出现 `sorry` / `captcha` / `unusual` 等词 | 弱信号，单独命中记 `low` |

命中 1 或 2 即标记 `risk_level = "high"`，并**直接判定 Gemini 与 Antigravity
不可用**，同时从「潜在可用」集合中排除。

结果字段：

```json
{
  "google_redirected": true,
  "hk_redirect": true,
  "risk_flags": ["hkredirect", "com.hk"],
  "risk_level": "high",
  "risk_reason": "访问 www.google.com 被重定向到 www.google.com.hk —— 该出口确定不可用"
}
```

Web 面板中对应 **风险** 列（`代理/VPN` / `可疑` / `正常`）与 **风险** 筛选器。

> **实测效果**（本机 132 个节点）：只看是否跳转 `com.hk` 后，潜在可用从
> **43 升到 63** —— 多出来的正是 18 个「HK 出口但未跳转」的节点，
> 它们此前被「按地区封杀」误伤。同时 34 个确实跳转的节点被正确排除
> （其中只有 2 个出口在 HK）。

### 8.6 为什么不会被本机 v2rayN 干扰

工具显式设置 `trust_env=False`。这一点非常关键：

本机若装有 v2rayN 并设置了系统代理（如 `127.0.0.1:3067`），
未关闭该选项时 HTTP 客户端会读取系统/环境代理设置，
导致**所有节点都从 v2rayN 的出口出去**，全部被误判为可用。
本工具已规避该问题，每个节点都走自己的独立链路。

---

## 9. 项目结构

```
proxy-tester/
├── main.py                  # CLI 入口、并发编排、输出
├── core/
│   ├── parser.py            # 代理 URI 解析（多协议 -> 统一结构）
│   ├── config_gen.py        # 生成 sing-box / xray 配置
│   ├── launcher.py          # 进程与端口生命周期管理
│   ├── detector.py          # 出口 IP / 地区 / 服务可用性 / 风险重定向检测
│   ├── tester.py            # 单节点流水线、并发调度与取消
│   ├── retest.py            # 网页端「重新检测」作业管理、节点导入与合并写回
│   ├── store.py             # 结果读取/筛选/排序/导出（CLI 与 Web 共用）
│   └── util.py              # 控制台编码、内核探测、无 BOM 写文件
├── web/                     # Web 面板
│   ├── server.py            # FastAPI 后端（查询 / 导出 / 重新检测 / SSE）
│   └── static/
│       ├── index.html       # 页面结构
│       ├── style.css        # 深色数据面板样式
│       └── app.js           # 筛选/排序/勾选/导出/重测逻辑（原生 JS，无构建）
├── tests/                   # 回归测试
├── configs/                 # 自动生成的临时配置与日志
├── kernels/                 # （可选）放置内核可执行文件
├── .env                     # 配置
├── proxiers.txt             # 输入节点
├── results.json             # 输出明细
└── proxiers_google.txt      # 输出代理池
```

---

## 10. 常见问题

**Q: 提示 `[错误] 未找到 sing-box.exe / xray.exe`**

在 `.env` 中设置 `V2RAYN_BIN`，或直接指定可执行文件路径：

```ini
SING_BOX_PATH=D:\somewhere\sing-box.exe
XRAY_PATH=D:\somewhere\xray.exe
```

**Q: 大量节点显示 `Proxy Server could not connect: General SOCKS server failure`**

这是**节点本身不可用**（服务端拒绝、已过期、或网络不可达），不是工具故障。
实例已成功监听本地端口，只是流量转发不出去。

**Q: 出现 `SSL: UNEXPECTED_EOF_WHILE_READING`**

通常表示节点服务端在 TLS 握手阶段断开，属于节点侧问题（常见于已失效的
Hysteria2 节点）。

**Q: 并发开多少合适？**

每个并发节点对应一个内核进程（约 50MB 内存）。10~20 并发对普通机器足够；
100 并发约需 200MB+ 内存与较多端口。建议先从 10 开始。

**Q: `configs/` 目录越来越大？**

每次运行都会保留临时配置便于排查。用 `--clean-configs` 可自动清理。

**Q: 测试中途想停止？**

直接 `Ctrl+C`。工具会捕获中断并回收全部子进程；同时注册了 `atexit` 兜底，
异常退出也不会留下孤儿进程。

**Q: 面板打开是空白 / 提示「结果文件不存在」？**

说明还没跑过测试。先执行 `python main.py` 生成 `results.json`，
或在面板上点「重新载入」。

**Q: 跑完测试后，面板数据没变？**

面板每次请求都会检查 `results.json` 的修改时间并自动重载。
若仍没更新，点右上角「重新载入」强制刷新。

**Q: 导出按钮点了没反应 / 没复制成功？**

导出会同时触发下载和复制。浏览器在非安全上下文或权限被拒时，
`navigator.clipboard` 可能不可用，此时会自动回退到旧的复制方式；
如果仍失败，用下载下来的 `.txt` 文件即可（提示会告诉你是哪种情况）。

**Q: 面板端口 8787 被占用？**

`python web/server.py --port 9000` 换一个端口。

**Q: 网页端「重新检测」会不会冲掉已有结果？**

不会。采用合并写回：以 `node_id` 为键，只覆盖本次测到的节点，其余原样保留。
所以可以放心只重测几个可疑节点。只有节点文件里已删除、或从未测过的节点，
才会新增/缺失。

**Q: 网页端导入后为什么节点数没变？**

说明那些节点**已经在了**（判重看的是服务器/端口/UUID/密码，不看备注名）。
面板会直接告诉你「这 N 个节点都已经在列表里了，不需要重复导入」。
想确认文件确实变了，看「总节点」数字或 `proxiers.txt` 行数。

**Q: 网页端导入会不会覆盖我原有的节点？**

不会。只把**真正新的**追加到 `proxiers.txt` 末尾，原有内容一字不动。
导入也不会自动开测 —— 需要再点「重新检测」。

**Q: 检测正在跑的时候能导入吗？**

不能，会提示等检测结束。这是为了避免检测读节点文件的同时被写入。

**Q: 网页端检测点「停止」后，已跑完的结果还算数吗？**

算数。停止只会让**还没启动**的节点不再执行，已经测完的结果照常合并写回
`results.json`，任务状态标记为 `cancelled`。

**Q: 关掉页面后检测会中断吗？**

不会。检测在服务器后台线程里跑，关掉浏览器它继续；重新打开页面会自动
接上正在运行的任务并继续显示进度。

**Q: 想让局域网其他机器访问面板？**

加 `--host 0.0.0.0`，但**强烈不建议**：`results.json` 含节点凭据与出口 IP，
暴露到网络等于把这些信息共享出去。默认只允许本机访问。

---

## 11. 实测参考

在本机对 132 个节点（82 个 Hysteria2 + 50 个 VLESS）运行，12 并发：

```
总节点        : 132
综合可用      : 63
Google 可达   : 103
Gemini 可用   : 40  (可能可用 23)
Antigravity   : 40  (跟随 Gemini)
高风险(代理/VPN): 35
链路 RTT      : 平均 362 ms   最快 96 ms      <- 推荐比较口径
Google 首字节 : 平均 2314 ms   最快 370 ms
Google 端到端 : 平均 2902 ms   最快 370 ms   (含下载首页)
出口地区分布  : HK:24, JP:23, US:21, SG:10, TH:7, NL:4, DE:2, KR:2, CA:2, ...
总耗时        : 183.8 s
```

> **可用数是浮动的**：节点池里的共享出口会随订阅状态、时段、限流而变化。
> 同一批节点在不同时间跑，可用数可能有明显差异。
> 排查时先看错误类型：如果是大量 `SOCKS/代理连接失败` 或 `TLS 断开`，
> 多半是节点侧问题，可以先在裸 TCP 层验证（DNS 能解析但端口连不上）。
>
> **判定只看"是否跳转 com.hk"，不看出口地区**：HK 出口只要不跳转就仍算可用
> （实测 24 个 HK 节点里 18 个可用）。被排除的是 `risk_level=high` 的节点，
> 它们分布在 HK / SG / JP / US / VN / GB 各地。
