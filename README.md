# Key Switch · 本地密钥切换台

同一台机器上的多个 AI 客户端共享若干上游 API 站点时，额度用完 / key 失效往往意味着：挨个改配置、重启客户端。Key Switch 把这件事变成**页面上点一下**——所有客户端统一连接本机网关，网关按你当前选中的站点和密钥转发请求。

## 它解决什么问题

| 场景 | 之前 | 用 Key Switch |
|---|---|---|
| 站点 A 日额度耗尽 | 改配置 + 重启客户端 | 页面点一下切到 B，后续请求即时生效 |
| key 轮换 | 改环境变量 / 改配置文件 | 页面粘贴新 key 保存即可 |
| 新增一个站点 / 换服务商 | 又要改一轮配置 | 页面「+ 添加站点」，填地址和 key 即可参与切换 |
| 多客户端共用多站点 | 每个客户端单独配 | 一处切换，四个客户端同时生效 |

## 四个受控客户端

页面上每个站点卡片都有 **「全站切此站」** 按钮：一次点击，把 Codex、Claude Code、Claude 桌面端、WorkBuddy 全部切到该站点，即时生效、无需重启任何客户端。

| 客户端 | 控制方式 | 说明 |
|---|---|---|
| Codex（CLI / 桌面端） | 本机网关 18791 | 网关转发；切换时自动把会话级 provider 快照、认证 key 与兜底线路一起归位 |
| Claude Code（CLI） | cc-switch 托管 | 若本机装了 cc-switch，直接写其配置库，热生效 |
| Claude 桌面端 | cc-switch 托管 | 同上（`claude-desktop` 线路，base_url 自动补 `/v1`） |
| WorkBuddy | 本机网关 18791 | OpenAI Chat 协议，模型名透传 |

> **注意**：Claude 线路由本工具写入 cc-switch 后，请不要再在 cc-switch 界面里手动切供应商，否则会互相覆盖。未安装 cc-switch 时，Claude Code 走本机网关（`~/.claude/settings.json`）。

### Claude 模型名自动映射

Claude 客户端发出的模型名（`claude-opus-*` / `claude-sonnet-*` / `claude-haiku-*`）上游站点往往不认识。网关会在转发前按规则改写模型名，默认映射到通用 OpenAI 模型，可用环境变量覆盖：

```
KS_MAP_HAIKU=gpt-4o-mini   # claude-haiku-* → ?
KS_MAP_OPUS=gpt-4o         # claude-opus-* → ?
KS_MAP_SONNET=gpt-4o       # claude-sonnet-* → ?
KS_MAP_FALLBACK=gpt-4o     # 其它 claude-* → ?
```

非 claude 开头的模型名原样透传，不影响 Codex / WorkBuddy 通道。

## 快速开始

要求：Windows + Python 3.10+（纯标准库，无第三方依赖）。

```
python server.py            # 启动并监听 127.0.0.1:18791
# 或双击 start.cmd：启动 watchdog 守护 + 自动打开页面
```

首次运行会在本目录生成 `vault.dpapi`（加密配置库），页面里为每个站点填入**接口地址 + key**，然后点击「接入」按引导修改对应客户端配置。此后切换站点对后续请求即时生效，无需重启客户端。

三个通道的初始站点是占位符，请编辑为你的真实上游地址。

## 站点自助管理

页面支持完全自助的站点管理，不依赖预置配置：

- **添加站点**：每个通道右上角「+ 添加站点」，填名称、API 地址和 Key 保存即可，马上可以「检测连接」和「切换使用」（每个通道最多 8 个）。
- **编辑站点 / 更新密钥**：卡片上「编辑站点」可改名称与地址；「更新密钥」粘贴新 key，留空则保留原值。
- **删除站点**：备用站点卡片上的「删除」即删（正在使用的站点需先切换走再删）。
- **检测连接**：区分密钥无效（401）、额度耗尽（429 quota）、限流（429）与网络不可达。

地址格式：Codex / WorkBuddy 通道填 `/v1` 接口地址（也兼容 `/keys` 管理页地址，会自动转换）；Claude 通道填站点根地址（不带 `/v1`）。

## 客户端接入要点

**Codex（config.toml）**

```toml
model_provider = "custom_gpt6"

[model_providers.custom_gpt6]
name = "Local Key Switcher"
base_url = "http://127.0.0.1:18791/v1"
experimental_bearer_token = "<页面接入时写入的本地 token>"
wire_api = "responses"
requires_openai_auth = false
```

**Claude Code（~/.claude/settings.json）**

```json
{ "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:18791",
    "ANTHROPIC_AUTH_TOKEN": "<页面接入时写入的本地 token>"
} }
```

页面上的「接入」按钮会自动完成以上改写（改前自动备份原文件）。首次接入后需要重启一次客户端；之后切换免重启。

## 安全设计

- **密钥不落明文**：上游 key 与本地 token 用 Windows DPAPI（当前用户级）加密存于 `vault.dpapi`，页面与状态接口只返回脱敏值（`sk-6214…f31e`）。
- **仅绑定回环地址**：服务只监听 127.0.0.1，外部不可达。
- **Host / CSRF 校验**：非本机 Host 头、无 CSRF token 的管理操作一律 403。
- **本地凭据路由**：转发请求必须携带通道专用 token，与上游 key 完全解耦——客户端配置里永远不会出现真实 key。
- 管理页面带 CSP、X-Frame-Options 等基础防护。

## 文件说明

| 文件 | 作用 |
|---|---|
| `server.py` | 网关 + 管理页 + 状态 API（单文件核心） |
| `index.html` | 管理页面（无构建，原生 JS） |
| `watchdog.py` | 守护进程：每 20 秒检查端口，掉线自动拉起网关（命名互斥体防重复） |
| `ensure.py` | 一次性检查：网关不在则启动后退出 |
| `start.cmd` | 启动器：拉起 watchdog + 打开页面 |
| `verify.py` | 结构性自检：脱敏、鉴权拒绝路径、路由（不依赖外部上游） |

运行产物 `vault.dpapi` / `watchdog.log` 已在 `.gitignore` 中排除，**切勿提交或分发 vault.dpapi**。

## 已知限制

- 仅支持 Windows（DPAPI 加密依赖）。
- 上游为 HTTP 明文链路时，密钥会以明文经过链路——建议上游启用 HTTPS。
- 流式转发为 HTTP/SSE（chunked），不支持 WebSocket 升级。
- 管理页检测（`/models` 探测）只验证鉴权与连通，不代表剩余额度。

## License

MIT
