# v0.7 普通标签 API

所有接口本机会话认证，PUT额外同源Origin/X-CSRF-Token/UUID Idempotency-Key。请求体上限65536字节，非法DTO422且不回显输入。缺库/旧revision/HUB_INCOMPLETE拒绝，不创建空库或自动迁移。工作线程SQL，普通连接FK=ON，BEGIN IMMEDIATE短事务。schema=0005_ordinary_tags。

## 目录 GET /api/v1/tags

q可选，原始最多256字符（UI最多64），规范化后1..64；空查询不限名称。NFKC/空白折叠/casefold后按字面子串匹配，SQL转义百分号/下划线/反斜杠，不是全文搜索或通配符。limit默认50、1..100，offset0..1000000。返回items/total/limit/offset，item={id,name,version,usage_count}，按normalized_name、id稳定排序。

仅列出关联当前未删除personal Note的未删除Tag，usage_count只计这些Note。只关联回收站、private、其他类型或无人使用的标签不出现；不暴露隐藏资源身份/数量。普通标签本身均为明文分类，不能录入私密名称。保留占位不是私密标签库。

## GET /api/v1/resources/{UUID}/tags

本版只开放未删除personal Note，未来Task复用同服务；Task/vault/未知/已删除/非personal统一404 RESOURCE_TAGS_NOT_FOUND。返回：

```json
{
  "resource_id": "33333333-3333-4333-8333-333333333333",
  "resource_version": 2,
  "tags": [{"id":"44444444-4444-4444-8444-444444444444","name":"AI","version":1,
    "created_at":"2026-11-12T00:00:00.000000Z","updated_at":"2026-11-12T00:00:00.000000Z","usage_count":1}]
}
```

GET不写默认值、活动、索引或幂等记录。

## PUT /api/v1/resources/{UUID}/tags

必填：names（0..20字符串）、expected_version（严格整数>=1）、expected_tag_versions（当前GET tags的UUID→严格正整数version完整映射，最多20项；无标签为{}）。未知字段拒绝。示例：

```json
{"names":["AI","逆向工程"],"expected_version":2,
 "expected_tag_versions":{"44444444-4444-4444-8444-444444444444":1}}
```

名称先NFKC、连续Unicode空白折叠为一个空格并trim，显示保留大小写，规范键再casefold；显示和键均1..64码点。Unicode C类（控制/格式/代理/私用/未分配）拒绝。重复规范键422，不自动忽略重复、不按逗号拆分、不翻译/合并同义词。空数组明确移除全部关联。

事务内验证Resource及完整Tag版本映射，任一变化409 VERSION_CONFLICT；防全局改名后旧页面重新创建旧名。已有规范键复用UUID和原显示名，不存在才创建。保留deleted占位409 TAG_NAME_RESERVED，不自动恢复。替换集合，仅移除不需要的边；Tag本身/Note/Markdown不删除。集合不同才增Resource.version/updated_at并写tags_updated Activity(fields=[tags])；不生成正文修订。集合相同不增Resource版本，仍记幂等操作。返回当前GET结构。

同key同规范化payload重试返回**当前**集合/Resource版本，而非旧快照，不再增版本。请求hash包含原names顺序及expected字段；同key不同内容或其他操作409 IDEMPOTENCY_CONFLICT。配置、边、Resource、Activity、幂等同事务，任何失败全部回滚。重放仍验证目标当前可见；已删除/隐藏则404，不复活。

## PUT /api/v1/tags/{UUID}

输入{name,expected_version}，后者是Tag版本，不是Resource版本。name原始1..256，规范化后同上述名称约束。只能改至少关联一个可见普通Note的Tag，其他统一404 TAG_NOT_FOUND。

Tag CAS冲突409 VERSION_CONFLICT；目标规范键被另一Tag占用（含保留占位）409 TAG_NAME_CONFLICT，无自动合并。显示名变化才增Tag.version/updated_at；不变不增。所有引用共享新名称，不改Note正文/修订/Resource版本。返回Tag元数据及过滤后的usage_count；同key重试返回当前事实，记录/改名同事务。不记录名称到Activity。

前端在保存前明确提示共享改名影响；当前可见计数不能视为包含回收站的总数。无法在普通UI管理私密标签。

## 笔记列表与导出

GET /api/v1/notes新增tag_id可选UUID；与原scope/type、分页取交集。未知/当前无匹配Tag返回空列表，不回显标签身份；trash范围只列当前personal回收站Note。每项新增tags=[{id,name,version}]，已过滤隐私/软删Tag，正文与历史行为不变。API过滤单Tag，多TagAND/OR后续评估。

普通ZIP notes.json每个已授权Note新增当前tags（含普通回收站），不含private资源/仅private标签；不是历史标签快照。完整snapshot v2保存所有Tag/边/幂等及notes/vault树，恢复可继续编辑。没有标签永久删除/合并/软删恢复、JSON导入、Task标签写入、private索引或FTS接口。

## 错误与交互

401会话/403来源或CSRF；422名称/UUID/字段/版本映射格式；413 TAG_REQUEST_TOO_LARGE；409版本/幂等/名称冲突；404不可见；503数据库/恢复状态。

失败保留输入；未知结果保留相同key/path/payload重试，变内容不暗中换key。409版本冲突只读核对，需确认放弃草稿再重读。明确422或名称占用拒绝尚未提交，可改正后新key重试。重新读取失败继续保留草稿和原pending。关闭/导航/刷新受草稿或未知结果保护，迟到响应不能在组件卸载后回写。
