"""插件运行时（M8 第 6–7 项，提案 docs/PROPOSAL_PLUGINS.md）。

本期只实现 **declarative（声明式）插件**：不含可执行代码，
只把已有内建工具组合成新工具。因此本包当前**没有任何执行逻辑**，
全部是"读取 + 校验 + 组合"。

安全边界：

- 插件不能绕过 Broker：组合出来的工具仍然是普通 ToolDefinition，
  走同一条 Policy / 审批 / 审计链路。
- 插件声明的 permissions 只是声明，不参与决策（与 §7.2 一致）。
- composes 只能引用内建工具，不能引用其他插件（避免隐式授权链与依赖环）。
- process（进程）插件在后续阶段加入，届时必须经 bubblewrap 沙箱。
"""
