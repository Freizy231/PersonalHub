# v0.6 首页布局与快捷入口 API

当前v0.7普通SQL版本0005_ordinary_tags，首页偏好契约保持v0.6。前缀`/api/v1/dashboard/preferences`，单人普通偏好，不属于Resource或private范围。所有请求要求本机会话；PUT还需同源Origin、X-CSRF-Token与UUID Idempotency-Key，JSON请求体最多65536字节，未知字段拒绝。正常业务连接FK=ON/短事务，SQL工作线程执行；缺库/未升级/未完成恢复拒绝，不隐式初始化或迁移。

## GET

返回`{schema_version:1,widgets:[{id,enabled}],shortcuts:[{id,label,kind,target}],version,state,updated_at}`。widget注册ID：capture/tasks/events/reminders/shortcuts，各一次，数组顺序为显示顺序。enabled严格布尔。state=default/saved/invalid；updated_at为UTC或NULL。

缺配置行返回默认五组件全开、生活计划/普通笔记/技术情报三个内部入口、version0/state=default、updated_at=NULL，GET不插行。损坏JSON/未知schema/未知组件/不安全已存URL返回state=invalid、原version、默认五组件与空快捷列表；原始DB行保留，不自动修复/覆写，不回显坏原文。普通业务页面仍可访问。异常类型/数据库损坏另返回503/固定错误码；不承诺所有磁盘损坏都能解析回退。

## PUT

输入`schema_version(可省默认1)、widgets、shortcuts、expected_version`。schema必须整数1，expected_version严格非负整数，widgets恰5项且各ID唯一；shortcuts最多12项、UUID唯一。单项字段：id(UUID)、label(合法UTF8/去首尾空格后非空/最多80字符，无控制字符)、kind(internal/web)、target(1..2048字符)。额外私密字段/任意path/命令拒绝。

内部target仅`/`、`/life`、`/library`、`/intelligence`、`/settings`、`/life?view=tasks|events|notes|vault`；private链接仅进入需解锁的面板，不隐式解锁。未交付模块不能造任意入口。web仅明确ASCII HTTP(S)绝对地址，合法host/port，无用户密码、query、fragment、空白/控制/反斜杠及编码控制字符；IP格式校验，IPv6 zone拒绝。scheme/host规范化、小写并补根斜杠；不保证目标内容/重定向可信，不验证站点在线、不发网络请求。域名国际化请使用ASCII punycode，路径非ASCII需百分号编码。不要填写私密信息或Token，即使在路径中也会按普通明文配置保存。

BEGIN IMMEDIATE内读取当前版本；首次expected_version=0创建单行version1；之后匹配才更新version+1/updated_at，created_at不变。与operation_requests幂等摘要/结果版本同事务提交；不写Activity或复制资源摘要。无变化的显式PUT也增version，客户端不脏时禁止保存。返回200当前配置。

- 相同key/规范化payload重试返回目标**当前**配置，不新增版本；客户端必须使用返回当前字段，不能将旧草稿绑定到新version。
- 同key不同payload/kind为409 IDEMPOTENCY_CONFLICT，重试不能换key静默写。
- 不同key旧expected_version为409 VERSION_CONFLICT；保留草稿，需确认重新读取核对，不自动最后写入获胜。
- 已存配置state=invalid时409 DASHBOARD_CONFIG_INVALID，保留原始行；本版无“强制覆盖修复”接口。
- 幂等记录故障/SQL提交失败回滚偏好写入；网络丢响应结果不确定，保留原key/payload再重试，不提前显示已保存。

## 物理与维护

Alembic0004_dashboard_preferences只增加同名表，首次保存前为空，服务启动/GET不种默认行。格式schema_version与乐观version独立。完整Hub快照的SQLite已包括此表与幂等信息，文件仍notes/vault全部树，hub-files.json v2不变。恢复新根后同版本程序读取；旧0003快照必须显式upgrade，不能手改revision。

没有DELETE/在线迁移/导入/API Token读取/原生工具执行。恢复默认由UI把默认内容放草稿，用户预览后PUT保存。UI不会自动请求网页、抓favicon或预取链接；外部链接必须预览完整目标并点确认，target=_blank + rel=noopener noreferrer。
