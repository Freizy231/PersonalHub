# ADR-0008：普通知识关联、FTS、PDF 与证据学习

2026-11-12｜已实现于 v0.8.0｜schema 0006_knowledge_foundation → 0007_phase2。用户明确要求 Phase 2 全部实现后统一验收，覆盖旧版“每小版本等待浏览器”执行建议；验收事实仍须分开报告。

## 决策与理由

1. 继续模块化单体，SQLite + 普通本地文件 + 单进程轻量轮询。没有新 Python/npm 依赖，没有修改原六模块、旧库、根环境或 .env。Resource 仅公共身份，具体知识/文献/学习字段在独立表。UUID 不随改名、文件目录切换改变。
2. Task、Event、Note、Collection、Document、Book、Bookmark、Course、学习实体共用 Tag；按类型计数。同名正规化与 CAS/幂等沿用 ADR-0007。普通 API 仅 personal；vault 没有普通身份、标签、索引、关系或占位。
3. Collection 非嵌套有序集合；编辑只替换可见成员，隐藏成员原边保留。类型关联 related/material/output 保存一份。knowledge_node→Task 的 practice 以 learning_node_tasks 为唯一权威，通用关联页投影该表，避免两个可写关系漂移。路线上下级同路线、服务端拒环，深度上限 64。Task 通过关联表绑定知识点，本期不重建 Task 增加单值 learning_node_id（一个练习可支持多知识点）。
4. 搜索是可重建普通投影，不是事实库。FTS5 trigram 对三个以上字符做字面短语检索，一至两个字符明确使用字面扫描，不宣称短词使用 FTS。普通修改后台每 2 秒、每批 200 项更新；显式全量重建修正外部变化并重建 Markdown UUID 反链。查询必须过滤当前隐私/删除/Resource version/学习祖先，并安全打开和核验当前文件。只缓存 inode/大小/mtime/ctime/hash 验证结论，不缓存正文或授权；外改触发重新 hash。单次 10,001 候选后提示缩小范围，分页结果不虚构总量。
5. 原设计 PyMuPDF 当前环境未安装，官方 PyPI 不可达；不运行未知下载代码，不为 PDF 改动旧环境。本期使用已安装系统 Poppler 的 pdfinfo/pdftotext，以固定参数数组、无 shell 的隔离子进程调用。PyMuPDF 仍为未来可替换适配器，AGPL/商业许可须在引入或分发时单独核实。Poppler 为系统独立程序，不打包二进制；分发需遵守其 GPL 及发行版许可。
6. 解析限制：每 PDF 100 MiB、每 UI 批 20 份；每次 pdfinfo/pdftotext 墙钟 30 秒、CPU 25 秒、512 MiB 地址空间、16 MiB 输出、2,000 页。每次解析复制已经校验的原件至私有临时目录；子进程 no_new_privs + Landlock 只读所选输入/系统库字体目录 + seccomp 拒绝网络/进程创建/ptrace 等。隔离失败返回 PDF_SANDBOX_UNAVAILABLE，不静默降级；Linux 依赖须在迁移机器复测。对 OS/系统库漏洞不能承诺绝对安全，需保持系统 Poppler 更新。
7. 加密文件明确 password_required；本期不收集/保存/传参 PDF 密码。用户在专业阅读器本机解锁后，自主另存解密副本再导入。无文本状态 no_text，不冒称已 OCR；无 OCR、内置完整阅读器、批注编辑或外部模型。
8. PDF Blob SHA-256 唯一，Document 对 Blob 唯一；并发文件锁竞争明确可重试。原始输入文件不修改；导入的 original_name 与提取元数据/人工修正分别保留。receiving→prepared→不可覆盖文件发布→DB业务提交；异常留在途文件/意图，不猜提交；只有本次已确认 committed 暂存可删除。启动把未提交意图标 interrupted、running 提取改 queued；不自动清历史/孤儿。提取结果单独不可覆盖 text 文件；延迟取消不发布成功指针。
9. 阅读器只支持四个固定绝对程序路径，无自定义参数/命令；检查可执行、非软链、非组/其他可写。60秒预览绑定资料/配置版本，明确确认且一次消费；starting 后崩溃显示 uncertain，不自动重放启动。启动成功仅表示进程已发起，不能推测用户已阅读或同步进度。
10. 学习时间独立记录，最少 1 秒、最多 24 小时且不能未来；没有计时器（手动记录已满足本期）。掌握程度只能经显式验收改变。verified 必须有标准、结论和至少一个非空普通 Note 或已完成 Task。证据保存标题/版本/hash与标准快照；后续编辑标变化，不改历史。私密化证据会隐藏该验收及相关周统计；当前投影显示不可访问，不泄漏旧结论/计数。
11. 阶段复盘使用 NoteService 文件协议。子 Note 幂等键由父操作稳定派生；Note 成功而最终关联冲突时可能留普通笔记，明确可恢复，不伪称跨文件事务原子。相同请求重试复用，不能无条件覆盖。
12. 全量备份 manifest v3 包括 notes/vault/library 三棵受管树，原件、提取文本、孤儿/在途文件都保全；0007 快照缺 v3 拒绝。旧 revision 的 v1/v2 可读，且目录若存在未列文件则拒绝。维护锁排除运行服务，文件锁与 DB 审计保证一致性；恢复只写全新根并最后移除 incomplete 标记。普通 ZIP 是可移植导出，上限 256 MiB，不含私密库/凭据/索引/在途文件；整库恢复使用完整快照，不宣称 ZIP 可直接合并导入。
13. 首页配置继续 schema_version 1，原五组件必须保留，新 reading/learning/weekly 可选。读旧行时只在响应中追加隐藏组件，不改原存储/顺序；明确保存才写回。未知配置仍拒绝覆盖。新增组件隐藏时不请求其数据。

## 迁移和回退

已发布 0000～0005 不改；0006/0007 发布前在隔离数据上验证。Resource.kind CHECK 的两次 parent-copy 仅限维护事务，FK 临时关闭，提交前逐表原行/索引/触发器/integrity/FK核对；自定义 trigger 或不可反射索引拒绝迁移，不能删掉后硬迁。没有启动 create_all/自动 Alembic、无破坏性 downgrade。

回退必须选对应 v0.7 源码包及升级前完整快照，恢复到新根；保留 v0.8 活动根、新数据与完整备份。v0.7 无法识别 v0.8 文献文件，不能用旧程序备份当前根。尚无异盘灾备，不混淆密文字节一致与私密内容解密验收。
