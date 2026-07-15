# src/mcp - MCP Client 模块
#
# 本模块实现 MCP (Model Context Protocol) 的客户端端，
# 支持通过 stdio (本地进程) 和 HTTP (远程服务器) 两种 transport 连接外部 MCP server。
#
# 与 src/agent/mcp_server.py (port 17328) 无关 ——
# 那个模块是将 wx-assist 本地工具暴露给外部的 MCP server；
# 本模块是连接外部 MCP server 的 client 端。
