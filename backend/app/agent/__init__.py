"""Agent 执行引擎（AGENT.md §4.8）。

MVP 采用自研简单循环；复杂工作流以后再评估 LangGraph 等框架。
Agent 不能直接访问数据库、文件系统或 Shell，所有能力都经 Tool Broker。
"""
