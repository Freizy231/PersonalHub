# 普通 Markdown Note API 与文件契约

普通Note首次交付v0.4.0 / P1-03a；当前v0.7 schema=`0005_ordinary_tags`。会话、Origin、CSRF、白名单DTO、脱敏错误沿用现有接口。当前只创建personal普通笔记；不开放private_journal/treehole/任意privacy或文件路径写入。所有正文均留本机，不解析HTML、不加载远程图片。

## 1. 端点

| 接口 | 输入 / 结果 |
| --- | --- |
| GET /api/v1/notes | scope=all默认/quick/journal/knowledge/experiment/trash；limit默认20，1..100；offset0..1000000；可选tag_id UUID（与scope交集），每项新增tags=[id/name/version]；返回items/total/limit/offset，不包含正文 |
| GET /api/v1/notes/{UUID} | 当前普通未删除Note事实与body，文件不存在/不符则409 |
| POST /api/v1/notes | NoteCreate，UUID Idempotency-Key必填；201首次/200重放 |
| PUT /api/v1/notes/{UUID} | NoteEdit完整表单，UUID Idempotency-Key必填；expected_version与expected_hash同时检查；200 |
| POST /api/v1/notes/{UUID}/trash | expected_version；软删除、文件和历史保留 |
| POST /api/v1/notes/{UUID}/restore | expected_version；恢复资源，不改写文件 |
| GET /api/v1/notes/{UUID}/revisions | limit默认20、1..100，offset；分页倒序修订元数据，不含正文 |
| GET /api/v1/notes/{UUID}/revisions/{UUID} | 选中历史修订元数据+body，验证文件hash |
| GET /api/v1/notes/{UUID}/markdown | 当前已保存正文的Markdown附件，文件名只含UUID |
| GET /api/v1/notes/export | ZIP附件，普通笔记/回收站/全部成功修订Markdown + notes.json/活动；排除private与未提交文件 |

NoteCreate：title（trim后1..500）、body（默认空，最多200000字符、合法UTF-8）、note_kind（quick默认/journal/knowledge/experiment）、local_date（YYYY-MM-DD/null）。普通journal可多篇同日，无唯一日期约束。PUT同四字段，额外expected_version严格正整数、expected_hash小写64位SHA-256。PUT是完整替换；未提供字段按默认值处理。title是DB权威，正文文件不复制frontmatter。

Note DTO：Resource字段id/kind/title/summary/privacy_level/created_at/updated_at/deleted_at/version，加note_kind/local_date/body_path/content_sha256/format=markdown；列表另带storage_state=ready/missing/conflict（动态派生），详情另带body。列表单项文件异常不隐藏资源，明确标记；读取与列表不会修复或改DB。revisions包括id/note_id/version/body_path/content_sha256/title/note_kind/local_date/created_at。

## 2. 保存、重试与修订

每次保存均新增Markdown修订，**包括仅改标题或相同内容再次明确保存**；成功后Resource.version+1。软删/恢复也增Resource版本，但不产生新正文，因此修订version允许跳号。修订不可覆盖，历史恢复先读入草稿，再PUT创建新修订，绝不回退计数或删除较新事实。

创建和编辑都支持持久UUID幂等键，所有业务共用命名空间。编辑operation_kind含目标UUID，请求hash含完整正文与原版本/hash；同key同内容重试先找结果再做过期检查，响应丢失不会重复修订；同key不同内容/目标/操作409。重放返回原资源当前状态，已删不复活，不可见404。创建和编辑失败后前端保留草稿/key/payload；结果未知时禁止悄悄换内容或key再发。401/403/422或明确版本/文件冲突后可修正；用户另存新笔记是新操作。

存储步骤：取得notes-files.lock→校验现有Resource/version/hash与当前文件→提交file_operations prepared→写同目录暂存并fsync→无覆盖link为新修订并fsync目录→单DB事务校验原事实、提交Resource/Note/Revision/Activity/幂等与operation committed。原正文不replace。正常FK开启；文件写不占用DB写事务。崩溃可能留下prepared/暂存/未引用最终文件，旧成功修订和DB指针仍有效。

`notes-check`只读诊断；`notes-reconcile`将未提交prepared标记orphaned或conflict，保留所有文件，不猜测缺失标题/业务事实、不自动完成保存。成功事务与committed状态同提交；正常不会留下“事实已提交但operation仍prepared”。恢复后用户原请求可安全重试；未成功文件不自动清理。

## 3. 冲突、隐私与限制

- stale version：409 VERSION_CONFLICT；原hash不符：409 NOTE_HASH_CONFLICT。
- 磁盘文件hash不符：409 NOTE_FILE_CONFLICT；缺失：409 NOTE_FILE_MISSING；路径/符号链接/文件类型/权限/大小异常：409 NOTE_PATH_UNSAFE/NOTE_FILE_UNSAFE。不会覆盖原磁盘内容；已保存原文不可读取时列表仍可见异常项，用户先核对备份/磁盘或复制外部正文另建笔记。
- 笔记写锁被占用：409 NOTE_FILES_BUSY，保留草稿原key稍后重试。锁不删inode，不无限等待。
- 普通查询、历史、Markdown、ZIP、变更/幂等重放均过滤private及普通未删除范围；只有回收站列表/恢复与ZIP明确包含普通软删除。不可见和未知统一404 NOTE_NOT_FOUND，不通过历史或附件旁路泄漏。
- Activity只记中性字段名，operation_requests仅请求hash与资源ID，file_operations仅中性ID/路径/hash/状态，正文不写日志或SQLite。
- ZIP含notes.json（format_version1，schema_revision0003_notes）与每个成功修订的原UTF-8 `.md`；累计正文超过64MiB返回413 NOTE_EXPORT_TOO_LARGE，改用完整备份或逐项Markdown。ZIP不是原子全Hub备份，也没有ZIP/JSON导入。
- 当前没有标签、双链、全文检索、自动保存、所见即所得或受控外部编辑导入。普通journal不是加密日记，敏感录入继续关闭。

## 4. 全Hub备份与恢复

`backend.manage backup`现在备份SQLite与整个notes树，含回收站、全部修订、未引用/暂存文件；维护前停API与外部编辑器，持维护排他锁和文件锁。DB原manifest不改，新增最后发布的hub-files.json（format=personal-hub-full-snapshot、format_version1、state=verified、revision、database_sha256、files相对路径→sha256/size、范围）。清单中的文件与实际树精确匹配，所有DB正文引用/修订hash均验证，文件源在复制后二次核对；文件不一致/超1MiB/非普通或符号链接即拒绝，保留失败输出供核对。

恢复先验证全套快照，目标必须全新。0003及后续没有hub-files清单即拒绝，即使当时Note为空；老0000..0002 SQLite-only备份仍可诊断隔离恢复。完整恢复生成`db/hub.db`、`notes/`、新的空config目录，同时保留`database.sqlite3`只读诊断来源（独立副本）。完整文件校验后最后写hub-restore-verification.json并移除HUB_INCOMPLETE.json；中断标记存在的db/hub.db拒绝业务读写和启动ready状态。恢复不会覆盖或切换活动根，使用新根需显式serve --data-root，并重新生成本机入口。

备份不含旧intel.db/.env/启动凭据/外部目录/未来vault，仍同盘无物理灾备。普通文件权限0600、目录0700，路径逐级descriptor+NOFOLLOW，API不接收任意路径。外部编辑器不遵守锁，备份前必须停用，不能保证恶意并发变更的快照。更多原因见[ADR-0004](ADR_0004_NOTE_FILES.md)。

## v0.7分类扩展

标签单独使用[API_TAGS](API_TAGS.md)，不能把names塞入NoteCreate/NoteEdit。分类变更会增Resource版本而不增加正文修订，编辑仍检查公共version与正文hash；共享改名用Tag独立版本，赋名还需完整Tag版本映射。普通ZIP notes.json的每篇Note包含当前tags（含普通回收站、排除private），历史Markdown不回溯分类。全量snapshot仍v2含notes/vault及数据库Tag/边，不只备份SQLite。
