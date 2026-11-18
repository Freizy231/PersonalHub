# 如何新增一个能力与一个流程

这份说明是给开发者看的。按它做，不需要改执行器、不需要动迁移。

## 一、新增一个能力

以「给知识点记录一次学习会话」为例。

### 1. 写契约

在 `backend/app/core/capabilities.py` 的调用方（即 `modules/knowledge/adapters.py` 顶部）注册一个 spec：

```python
'learning.session.create': _spec(
    id='learning.session.create', version=1, title='记录学习会话',
    purpose='为知识点记录一段学习时间。',
    effect='create',                                  # read / create / update / outbound
    kinds=frozenset({'knowledge_node'}),
    idempotent=True,
    reversible=True, undo_note='可删除该会话记录。',
    fields=('node_id', 'started_at', 'ended_at', 'note')),
```

字段要求：

- `effect` 决定它需要哪种授权。`create`/`update` 需要本地写入授权。
- `fields` 必须写全。执行器会用 `check_arguments` 拒绝任何未声明的键。
- `kinds` 是范围检查依据；凡是学习范围外的东西都不应出现在这里。

### 2. 写适配器

在 `Adapters` 里加一个方法，并登记到 `self.calls`：

```python
def _session(self, arguments, scope_id, key):
    node_id = _uuid(arguments, 'node_id')
    self._checked(scope_id, 'knowledge_node', node_id)      # 范围与可见性
    payload = Session(expected_version=_version_of(self.database, node_id), ...)
    result = self.learning.session(payload, key)            # 模块自己的公开服务
    return CapabilityResult(f'已记录 {result["minutes"]} 分钟', (result['id'],), {'id': result['id']})
```

规则：

- 调用模块**公开服务**，不要复制路由里的逻辑，也不要绕内部 HTTP。
- 返回真实创建或改动过的 id，界面靠它给你跳转。
- 参数不合法时抛 `CapabilityError`，不要静默使用默认值。

### 3. 如果它可以出现在计划里

在 `planner.CREATABLE` 里加进去，并在 `_required` 里写明必填字段。只读能力不要加：计划里的每一步都会执行，选项不应该消耗一次运行。

### 4. 写测试

至少覆盖：

- 未授权时被拒绝，且没有写入（查数据库行数）。
- 参数越界、未知字段被拒绝。
- 正常路径创建了真实记录，能在对应页面打开。

前端文案：若新增了错误码，必须在 `frontend/src/shared/api.ts` 的 messages 里补一条，否则 `test_error_codes` 会失败。

## 二、新增一个流程模板

模板只是一串能力调用，没有专用代码。两种方式：

### 方式 A：代码里的默认模板

在 `modules/knowledge/templates.py` 加一个函数返回步骤列表，并在 `ensure_default_templates` 里登记。它会随服务启动安装一次，之后不覆盖用户编辑。

### 方式 B：用户自己保存的模板

界面「设置 → AI 权限 → 个人模板」里可以保存、停用、再次运行。也可以在 API 里：

```python
workspace.save_template(TemplateWrite(
    name='二十五分钟精读',
    steps=[TemplateStep(capability_id='notes.create', arguments={'title': '精读问题'})]))
```

模板里的 `$path` / `$node0` 与计划里的含义相同，运行时绑定到真实 id。

## 三、不要做的事

- 不要在 `executor.py` 里加任何流程专用的分支。
- 不要接受模型传来的函数名、SQL、文件路径或命令。
- 不要把 `core/` 的契约与具体业务模块互相 import。
- 不要为了保证「原子」而把多个模块的事务硬拼成一个；分步提交并如实报告已完成与未完成部分。
