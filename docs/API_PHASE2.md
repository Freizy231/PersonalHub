# v0.8 普通知识、资料库与学习 API

所有 `/api/v1/*` 业务接口要求本机会话；写入另要求可信 Origin + CSRF。JSON 请求上限 1 MiB（标签/偏好仍 64 KiB），无参数/异常原文回显。UUID 路径由 DTO 验证。创建和重要保存用 UUID `Idempotency-Key`，key 全局唯一；内容不同重复 key 返回409；重放返回当前事实，仍执行隐私校验。普通写入不接受 private。未知数据库 revision 拒写，不在启动迁移。

| API | 语义 |
| --- | --- |
| GET /resources?q=&kind=&limit=&offset= | 当前可见资源目录，名称字面查询；limit≤100 |
| GET /resources/{id} /context | 稳定身份，双向类型关联、Markdown outgoing/backlinks；严格过滤另一端及学习祖先 |
| POST /resources/{id}/relations | `{expected_version,target_id,relation_type}`；related/material/practice/output；practice 限知识点→Task，写入唯一学习任务边 |
| POST /resources/{id}/relations/{edge}/remove | `{expected_version}`；只允许源资源解除边，不删除独立资料 |
| GET /collections?trash= | 有序普通集合列表 |
| POST /collections、PUT /collections/{id} | `{title,description,members:[UUID],expected_version}`，最多1000成员，不允许嵌套/重复；隐藏成员保留 |
| POST /collections/{id}/trash、/restore | CAS 软删/恢复集合，不删除成员 |
| GET /search?q=&kind=&limit=&offset= | 字面全文/短词查询；返回 title/snippet/kind/stable URL、total/strategy/truncated。正文作为纯文本展示 |
| POST /search/rebuild | 显式全量重建普通索引及 UUID 反向链接，返回 indexed/skipped；无上传/外部请求 |
| GET /exports/ordinary | 256 MiB 上限 ZIP，manifest 哈希、data.json、普通 Markdown 修订/PDF/提取文本；含普通回收站；无云、密码、vault、旧intel |

## 文献与文件

`POST /documents/import?name=文件名.pdf`：原始 application/pdf 流，不是 multipart；由前端一批最多20份顺序提交，各自 UUID key/结果可重试。服务端流式每份100 MiB、签名 `%PDF-`、SHA-256，原件文件按 hash存储。返回 `{document,duplicate}`。相同内容只保留一个 Blob/Document；回收站重复提示恢复；private 冲突只给中性失败，不回身份/标题/hash。上传中断保留意图和在途文件，不产生半条文献。磁盘错误返回失败且保留恢复材料。

`GET /documents?trash=&state=&q=&tag_id=&category=&limit=&offset=`：q标题字面匹配，分类完全匹配，标签稳定 UUID；状态 unread/reading/completed/paused；limit≤100。`GET /documents/{id}` 详情含 tags/bookmarks/提取状态/原始元数据，内部文件相对路径不返回。

`PUT /documents/{id}` 完整编辑 DTO：title、expected_version、document_type(paper/book/report/other)、authors、year?、doi、source_url、abstract、category、read_state、current_page；Idempotency-Key。当前页0表示未开始，非0须有已知page_count且不越界。元数据取值不会覆盖 metadata_title/author/subject/created 原始提取结果。

`PUT /documents/{id}/bookmark`：expected_version、page、label、remove=false；每文献每页一项，合法页码；remove解除；版本冲突不最后写入获胜。`POST /documents/{id}/retry|cancel|trash|restore`：expected_version。提取状态 queued/running/ready/no_text/password_required/failed/cancelled；重启补偿，失败保留 error_code，取消不发布迟到成功。`GET /documents/jobs` 最近100个当前可见提取任务；`GET /documents/{id}/original` 校验哈希后 attachment 下载，不内嵌解析。

密码文件不提供密码 API；仅提示用外部阅读器解锁后自主另存副本。默认不做OCR/网页抓正文/远程元数据查询。

## 书籍、网页和课程

`GET /catalog/{kind}` / `GET /catalog/{kind}/{id}` / `POST /catalog/{kind}` / `PUT /catalog/{kind}/{id}` / `POST .../trash|restore`；kind=book/bookmark/course，保存用 Idempotency-Key。列表支持 q/trash/limit/offset/tag_id/category（课程无category）；书籍无文件也可建。

- Book：title/expected_version、authors/isbn/publisher、year?、category、read_state、total_pages?、current_page、progress_percent、rating?(1..5)、description。已知总页数按页重算百分比；未知时手动百分比。
- Bookmark：title/expected_version、url(HTTP/S、无凭据)、summary、read_state、category；不发请求或自动提取正文。
- Course：title/expected_version、provider/url/description、status(planned/active/completed/paused)、total_units≥1/completed_units≤total_units；课程完成不修改知识掌握。

## 外部阅读器

GET/POST `/readers`、PUT `/readers/{profile}`：name/executable/enabled/expected_version；白名单 `/usr/bin/{evince,okular,mupdf,zathura}`，不接收任意args。GET返回本机可用程序列表。

POST `/readers/preview`：document_id/profile_id；返回预览ID、argv、expires_at。POST `/readers/confirm/{preview}`：`{confirmed:true}`；60秒有效，绑定版本；同预览不重复执行。GET `/readers/history` 最近100个可见启动记录。`starting` 崩溃重读为 uncertain；started不表示阅读完成。真实 GUI 启动还须在用户桌面验收；测试用模拟进程验证安全调用契约。

## 学习

资源 CRUD：`GET /learning/{kind}`、`GET .../{id}`、POST/PUT、POST trash/restore；kind=learning_goal/learning_path/knowledge_node，创建/保存 UUID key；列表limit≤100、offset、trash。共同 title/description/expected_version：

- Goal：target_date?、status(active/completed/paused)、acceptance_criteria。
- Path：goal_id?、status。同一资源不生成临时个人Project。
- Node：path_id、parent_id?、position≥0、acceptance_criteria。mastery不能直接写DTO；同路线、无环，创建后不跨路线迁移。

GET `/learning/paths/{id}/tree`：有序深度投影+verified/total及分母口径。所有可见未删除节点分别验收；隐藏祖先连子项ID也不返回。GET `/learning/nodes/{id}/context`：节点、练习、学习记录、历史验收/证据。

POST `/learning/nodes/{id}/practice`：expected_version/title/description/planned_date?，原子创建共享Task+唯一绑定边；一般已有Task也可经类型关系practice绑定。Task完成不自动验收。

POST `/learning/sessions`：node_id、started_at/ended_at（须明确时区）、reflection_note_id?；1秒～24小时、结束不在未来。UUID key。手动记录，不提供自动计时器；不改变Resource mastery。

POST `/learning/nodes/{id}/assessments`：expected_version/mastery(unlearned/exposed/practiced/verified)/conclusion/evidence:[UUID]；verified要求非空标准与结论、至少已完成Task或非空普通Note。不可覆盖历史，保存标准/证据版本/hash快照；后续更改标changed；隐藏证据时不返回该验收/统计。

GET/POST `/learning/paths/{id}/reviews`：POST expected_version、title/body、period_start/end。普通Note协议创建Markdown，返回review_id/resource_id；幂等子操作避免失败重试重复正文。GET过滤不可访问Note。

GET `/learning/overview`：路线verified/total、本周学习秒数、有证据验收次数；周一为周起点，本机日历；时间只计时长。
