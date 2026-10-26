# hermes-napcat

[NapCat](https://github.com/NapNeko/NapCatQQ)（QQ / OneBot v11）平台适配器 + 工具集，以**真正的 Hermes Agent 插件**形式实现——不改 Hermes 源码、不 patch、放进插件目录即可使用。

```
QQ ⇄ NapCat (OneBot v11) ⇄ hermes-napcat ⇄ Hermes Agent Gateway
```

## 功能

**平台适配器（对话）**
- QQ 私聊与群聊直接和 agent 对话（群聊默认仅响应 @机器人，可配置全量响应）
- 消息分块、用户白名单、cron/定时任务结果推送（`NAPCAT_HOME_CHANNEL`）
- 与 Telegram/Discord 等内置平台同级的 gateway 集成

**napcat 工具集（agent 主动能力）**
- `napcat_send_message` —— 主动向任意私聊/群发送消息
- `napcat_send_file` —— 发送本地文件到 QQ
- `napcat_get_group_list` / `napcat_get_group_member_info` —— 群信息查询
- `napcat_group_mute` / `napcat_group_kick` —— 群管理操作（需 bot 为群管理员）

## 前置条件

1. 一个运行中的 [NapCat](https://napneko.github.io/)，已登录 QQ 小号
2. NapCat 网络配置中新建 **WebSocket 服务端（正向 WS）**：
   - 端口如 `3001`，启用 token（推荐），**消息格式选 `array`**
3. Hermes Agent（`hermes gateway` 运行环境）

## 安装

### 方式一：目录插件（推荐）

```bash
git clone https://github.com/<you>/hermes-napcat.git ~/.hermes/plugins/napcat
hermes plugins enable napcat
```

Docker 部署时，把仓库放到挂载的数据卷里，例如 compose 中 `- ./data/hermes:/opt/data`：

```bash
git clone https://github.com/<you>/hermes-napcat.git ./data/hermes/plugins/napcat
docker compose restart hermes
```

### 方式二：pip 安装

```bash
pip install hermes-napcat
```

## 配置

在 `~/.hermes/.env`（或运行 `hermes gateway setup` 交互式配置）：

```bash
NAPCAT_WS_URL=ws://127.0.0.1:3001     # NapCat 正向 WS 地址
NAPCAT_TOKEN=your-onebot-token         # NapCat 里设置的 token
NAPCAT_ALLOWED_USERS=10001,10002       # 用户白名单：允许对话的 QQ 号（逗号分隔）
NAPCAT_ALLOWED_GROUPS=987654321        # 群白名单：允许响应的群号（逗号分隔）
NAPCAT_HOME_CHANNEL=g987654321         # cron 推送默认目标（QQ号 或 g+群号）
# NAPCAT_GROUP_ALL_MESSAGES=1          # 可选：群内不用 @ 也响应
```

**双重白名单（交集语义）**：群消息必须**同时**通过群白名单（`NAPCAT_ALLOWED_GROUPS`，插件层过滤）和用户白名单（`NAPCAT_ALLOWED_USERS`，网关层过滤）才会到达 agent；私聊只查用户白名单。群白名单为空表示不限制群（用户白名单仍然生效）；不在群白名单里的群会被静默丢弃，连配对提示都不会发出。

| 变量 | 必填 | 说明 |
|---|---|---|
| `NAPCAT_WS_URL` | ✅ | NapCat 正向 WebSocket 地址 |
| `NAPCAT_TOKEN` | 建议 | OneBot 鉴权 token（公网必须） |
| `NAPCAT_ALLOWED_USERS` | 建议 | 允许对话的用户列表；默认走配对(pairing)授权 |
| `NAPCAT_ALLOWED_GROUPS` | - | 允许响应的群列表；空 = 不限群；与用户白名单取交集 |
| `NAPCAT_ALLOW_ALL_USERS` | - | `1` 时任何人可对话（危险） |
| `NAPCAT_HOME_CHANNEL` | - | cron/通知投递目标 |
| `NAPCAT_GROUP_ALL_MESSAGES` | - | `1` 时群消息全量响应 |

chat id 约定：私聊为 QQ 号（`123456`），群聊为 `g` + 群号（`g987654321`）。

## 使用

配置好后重启 gateway，QQ 里私聊机器人或群里 @ 它即可对话。agent 可用工具示例：

> "把 /tmp/report.pdf 发到群 987654321"
> "每天早上 9 点把天气预报发到 g987654321"（配合 Hermes cron + `NAPCAT_HOME_CHANNEL`）

## 架构

```
hermes-napcat/
├── plugin.yaml    # 插件清单（环境变量声明 → hermes config UI）
├── __init__.py    # register(ctx)：注册平台 + 工具
├── onebot.py      # OneBot v11 WS 客户端（事件 + API 调用 + 自动重连）
├── adapter.py     # NapCatPlatformAdapter（BasePlatformAdapter 实现）
├── tools.py       # napcat 工具集 handlers
└── schemas.py     # 工具 JSON Schema
```

仅依赖 aiohttp（Hermes 自带），零额外依赖。

## 安全须知

- NapCat 属非官方 QQ 协议实现，**有封号风险**，只用小号
- 公网部署务必设置 `NAPCAT_TOKEN` 并限制 `NAPCAT_ALLOWED_USERS`
- `napcat_group_kick` / `napcat_group_mute` 是针对真实用户的操作，建议在 Hermes 审批策略中开启人工确认

## 开发

```bash
git clone https://github.com/<you>/hermes-napcat.git
cd hermes-napcat
HERMES_PLUGINS_DEBUG=1 hermes gateway run   # 调试加载
```

PR / issue 欢迎。参考资源：[Hermes 平台适配器开发文档](https://hermes-agent.nousresearch.com/docs/zh-Hans/developer-guide/adding-platform-adapters)、[NapCat 文档](https://napneko.github.io/)、官方 NapCat 内置支持 PR（NousResearch/hermes-agent#17917，未合并）。

## License

MIT
