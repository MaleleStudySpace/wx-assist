# MCP Client（外部 MCP 服务器接入）

## 一句话说明

让 wx-assist 作为 **MCP client** 连接外部 MCP server（本地 stdio 子进程或远程 HTTP 端点），把手握到的工具清单注入 LLM function calling，实现插件化扩展。用户在 `data/user_mcp.json` 里配置即可，bot 启动时自动握手取工具。

> 与反向 MCP Server（`src/agent/mcp_server.py`，端口 17328）方向相反：
> 反向是把 wx-assist 的工具暴露给外部客户端；本模块是连外部 server、把对方工具拿进来。两者并存互不干扰。

## 数据流总览

```
data/user_mcp.json
    │
    ▼
MCPServerManager.init_from_config
    │  校验配置 → 逐 server 创建客户端 → initialize → list_tools
    ▼
_tool_table: [{server, name, schema}]  （schema 命名 {server}__{tool}）
    │
    ▼
MCPToolRegistry.refresh()（冲突检测：重名跳过 + 告警）
    │
    ▼
ProxyRegistry 替换 tool_executor.registry（接口兼容，Agent 无感）
    │
    ▼
LLM 返回 tool_call "server__tool"
    │
    ▼
MCPToolRegistry.execute → _dispatch_mcp → manager.invoke → client.call_tool
    │
    ▼
result.content[].text 拼接 → 作为 Observation 回填 messages
```

## 配置格式（`data/user_mcp.json`）

```json
{
  "servers": [
    {
      "name": "weather-mcp",
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-brave-search"],
      "cwd": "C:/tools/mcp/",
      "env": {"API_KEY": "xxx"},
      "enabled": true,
      "description": "搜索工具",
      "timeout": 30
    },
    {
      "name": "search-mcp",
      "transport": "http",
      "url": "https://example.com/mcp",
      "headers": {"Authorization": "Bearer xxx"},
      "enabled": true
    }
  ]
}
```

### 字段说明与校验规则

| 字段 | 适用 | 说明 |
|------|------|------|
| `name` | 都 | 必填，2-32 字符，全局唯一 |
| `transport` | 都 | `"stdio"` 或 `"http"`；不填时按 `command→stdio`、`url→http` 推断 |
| `command` | stdio | 必填，可执行文件（找不到自动补 `.cmd` 后缀，如 npx→npx.cmd） |
| `args` | stdio | 可选，命令行参数列表 |
| `cwd` | stdio | 可选，工作目录 |
| `env` | stdio | 可选，传给子进程的环境变量 |
| `url` | http | 必填，远程 MCP 端点 |
| `headers` | http | 可选，HTTP 请求头 |
| `timeout` | 都 | tools/call 超时秒数，默认 30 |
| `enabled` | 都 | 默认 true；false 保留配置但不起动 |
| `description` | 都 | 可选，UI 展示用 |

配置错误（缺必填、枚举非法、重名）在启动时逐个跳过并告警，**不影响其他 server 和 bot 启动**。

## 传输层（`src/mcp/client.py`）

纯标准库 + requests 实现，无第三方 MCP SDK，协议版本 `2024-11-05`。

### StdioClient（本地子进程）

```
__init__:
  subprocess.Popen([command] + args)
    - Windows CREATE_NO_WINDOW 防黑窗
    - env 强制 PYTHONUTF8=1 + PYTHONIOENCODING=utf-8
    - 剔除敏感环境变量（数据库密钥 / DATABASE_URL / DATA_DIR / PYTHONPATH）
  _reader_thread: 后台线程持续 readline → json.loads
    - 按 JSON-RPC id 分发到 _pending{req_id: queue.SimpleQueue}
    - 非 JSON 行（server 的 debug 日志混入 stdout）跳过
  _lock: 串行化请求发送
```

**串行化说明**：JSON-RPC 2.0 over stdio 没有多路复用机制，请求-响应靠 id 匹配。若两个线程同时发请求，reader 线程无法保证把响应交回正确的调用者，因此用一把 `Lock` 串行化所有 tools/call。后果：**单个 stdio server 同时只能处理 1 个工具调用**；不同 server 各自有锁，互不阻塞。

### HttpClient（远程 HTTP）

- `requests.post(url, json=body, headers=headers, timeout=timeout)`
- **兼容 Streamable HTTP**：响应 `Content-Type` 含 `text/event-stream` 时解析首行 `data: ` 字段（SSE），否则 `r.json()`
- HTTP 请求-响应天然 1:1，无需串行锁

## 生命周期管理（`src/mcp/manager.py`）

### 启动

`init_from_config()`：读 JSON（兼容 `{"servers": [...]}` 包装）→ 校验 → 逐个 `_start_one()`（create_client → initialize → list_tools → 注入工具表）。有一个成功才启动心跳线程。

### 心跳 / 降级 / 恢复

```
每 5s 对每个 server 执行 ping()
    │
    ├─ 成功 → 清零连续错误计数
    └─ 失败 → _consecutive_errors[name] += 1
              连续 3 次失败 → 降级 degraded
                - 从 _tool_table 摘除该 server 全部工具（LLM 不再看到）
                - 之后 ping 恢复成功 → 重新 list_tools 并重新注入
```

降级是"摘除工具表"而非断开连接；恢复后自动重新注入，全程无需重启 bot。

### 热管理 API（后端 + 前端 MCPTab 共用）

| 操作 | 行为 |
|------|------|
| 新增 | 校验 → 启动 → 写回 `data/user_mcp.json` |
| 删除 | 关停 client → 从配置移除 |
| 重启 | close 旧 → 重新 spawn/initialize/list_tools |
| 启用/禁用 | 标 `enabled=False` 保留配置，不起动 |
| 工具级开关 | 只更新工具表 disabled 标志，不重新 list_tools |

### 关闭

`shutdown_all()`：停心跳线程 → 逐个 `client.close()`（terminate → 等 3s → kill 兜底）。**在 bot 清理序列中排在第一位**（MCP 子进程可能占用资源，先解除再清理主模块）。

## 安全设计

| 措施 | 说明 |
|------|------|
| 环境变量隔离 | MCP 子进程不继承 wx-assist 敏感 env（数据库密钥 / DATABASE_URL / DATA_DIR / PYTHONPATH），只透传 PATH |
| 工具名冲突保护 | `server__tool` 命名空间隔离；启动时检测重名，冲突的后者跳过整个工具并告警 |
| 反向 MCP Server 隔离 | 暴露给外部客户端的只有本地工具，不含 MCP 注入工具 |
| 超时兜底 | tools/call 默认 30s 超时，3 次连续超时 → 降级摘除，LLM 不会卡死 |

## 后端 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/mcp/servers` | GET | 列出所有 server 配置 + 运行状态 |
| `/api/mcp/servers` | POST | 新增 server（校验 + 启动 + 持久化） |
| `/api/mcp/servers/<name>` | PUT | 更新 server（重建） |
| `/api/mcp/servers/<name>` | DELETE | 删除 server |
| `/api/mcp/servers/<name>/restart` | POST | 重启单个 server |
| `/api/mcp/servers/<name>/toggle` | POST | 启用 / 禁用 |
| `/api/mcp/servers/<name>/tools/<tool>/toggle` | POST | 单个工具启用 / 禁用 |
| `/api/mcp/test` | POST | 测试远程 URL 连通性（initialize + list_tools） |

状态通过 WebSocket 广播（`mcp_servers` 字段），前端 MCPTab 每 10s 轮询刷新。

## 前端 MCPTab

- Server 卡片列表：状态点（running / degraded / error / stopped）、transport 图标、在线数/总数
- 展开查看每个工具详情（参数 schema、必填标记），支持工具级开关
- 添加/编辑弹窗支持**表单 + JSON 粘贴双模式**（兼容 Claude Code 的 `mcpServers` 格式），HTTP 模式支持"测试连接"

## 代码位置

| 组件 | 文件 |
|------|------|
| 配置校验 | `src/mcp/config_schema.py` |
| 传输层（Stdio / HTTP） | `src/mcp/client.py` |
| 生命周期管理 | `src/mcp/manager.py` |
| 工具转换与分发 | `src/mcp/tool_registry.py` |
| bot 集成 | `src/bot.py`（启动序列 / 清理序列） |
| API | `src/web/server.py` |
| 前端 | `ui/src/components/MCPTab.jsx` |
