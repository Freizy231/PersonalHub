"""MCP（Model Context Protocol）接入（M8 第 4–5 项）。

范围与信任模型见 docs/PROPOSAL_MCP.md。要点：

- 本包只负责**连接与代理**，不做授权判断。MCP 工具投影成普通的
  ToolDefinition，与内建工具走完全相同的 Broker / Policy / 审批链路。
- 不采信 Server 自述的风险级别：MCP 工具一律按 WRITE 处理（需审批）。
- Server 的配置默认关闭，必须用户显式启用。
"""
