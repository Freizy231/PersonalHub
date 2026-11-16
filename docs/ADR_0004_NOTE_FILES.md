# ADR-0004：普通Note的不可覆盖修订与全量备份

2026-11-12，v0.4.0 / P1-03a。用户已确认v0.3浏览器验收、授权继续。普通Markdown为本轮范围，独立加密vault仍关闭。

## 选择

采用不可覆盖的Markdown修订：每次成功保存对应`notes/<note UUID>/<revision UUID>.md`，DB notes.body_path/hash指向当前修订，note_revisions保存所有成功修订（含当时标题/类型/归属日）。旧修订不replace。与早期设计的固定notes/<UUID>.md有意不同：写新文件完成fsync后才提交指针，使中断最多留下未引用文件，不会出现DB回滚却正文已覆盖。标题等业务事实以DB为准，不复制可编辑frontmatter；导出保留Markdown与元数据映射。

每次文件写入先在独立事务记录file_operations prepared（中性UUID、相对路径/hash/状态，不存正文），再同目录O_EXCL暂存、fsync、无覆盖link为最终文件、fsync目录；最后单一DB事务提交Resource/Note/Revision/Activity/幂等与operation committed。全局短时notes-files.lock串行化本机Note写入；存储写入与维护互斥。各读请求只验证，不写文件/做修复。只存在本机单人边界，不引入分布式事务。

崩溃在DB提交前：保留prepared及暂存/最终文件；不自动完成未知元数据事务、不删除孤儿。明确维护`notes-check`只读诊断，`notes-reconcile`核对后将未提交prepared标为orphaned或conflict，保留文件与原DB事实。用户原请求重试沿持久幂等记录判断；成功提交但响应丢失不重复。编辑也要求Idempotency-Key，冲突后不能静默覆盖；保存新副本可保留草稿。

外部编辑任何已登记修订：读取/编辑/导出/完整备份检测SHA不符或缺失，返回明确冲突/缺失，不覆盖、不自动导入。用户将外部内容另存导入新普通Note，或用备份核对后恢复原文件；暂无受控外部编辑同步。应用历史恢复是复制选中修订为新版本，保留全部事实。

## 安全与备份

客户端不接受路径、隐私级别或任意文件名；UUID构造目录，文件用NOFOLLOW、0600，目录0700，逐级拒绝symlink/非普通文件/超限内容。文件锁无等待取得失败返回可重试，不能删锁。Markdown仅文本编辑/纯文本预览，无HTML/远程图片加载，无新依赖。

备份与升级前备份扩大到SQLite + 整个notes树（含未引用/暂存文件），停API持维护排他锁与文件锁；SQLite Backup API + 文件清单SHA/大小 + 引用一致性校验 + 源文件二次清单核对，完整标记最后写入。已有sqlite-only备份仅在revision0000..0002接受，0003及以后缺完整文件manifest拒绝恢复。当前完整恢复在全新目录创建db/hub.db与notes/，并保留database.sqlite3诊断文件，启动使用新数据根；旧备份保持原isolated兼容。清单路径白名单/重复/篡改/符号链接/额外文件均拒绝，不覆盖活动目录。外部编辑不遵守进程锁，备份前应停外部编辑器，检测到变化失败保留现场，不宣传可无条件捕获恶意并发变更。

未实现JSON导入、标签/双链/FTS、私密日记、复杂预览、文件自动清理。孤儿保留会占空间，当前无自动保留策略。待未来需要固定工作副本/外部编辑时，新增受控工作区与合并设计，不直接修改不可变修订契约。
