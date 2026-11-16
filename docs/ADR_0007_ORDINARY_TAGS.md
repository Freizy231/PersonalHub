# ADR-0007：普通共享标签与笔记筛选

2026-11-12｜v0.7 / P1-05b第一批。仅普通Note纵向闭环，Task后续复用同一Tag服务；不实现Collection/FTS/vault标签。

## 数据与名称

新增0005_ordinary_tags，只增加tags/resource_tags两表。Tag不继承Resource：UUID主键、name、normalized_name唯一、version、created_at/updated_at/deleted_at；resource_tags复合主键(resource_id,tag_id)，FK均RESTRICT，反向(tag_id,resource_id)索引。移除只删除边，不删标签或笔记。无标签删除/合并/恢复API，保留同名占位；未来软删/合并单独设计，禁止自动重建冲突身份。

名称先NFKC，再将连续Unicode空白折为一个ASCII空格并trim；显示名保留大小写，匹配键再casefold。显示名和匹配键均1..64 Unicode码点，拒绝控制/格式/代理/私用/未分配字符；不按逗号拆分，不翻译、不去重音、不自动合并同义词。每笔记至多20标签；请求重复规范化名称422。首次明确保存创建Tag，随后相同键复用原UUID/显示名。未关联任何可见资源的标签不出现在普通目录和联想中，但仍可在显式赋名时复用。

## 边界与写入

本版只支持当前未删除privacy=personal且kind=note的资源，未知UUID/Task/vault UUID统一404 RESOURCE_TAGS_NOT_FOUND。目录、计数、筛选均JOIN当前可见普通Note；不返回仅隐藏/回收站/其他类型资源使用的标签。笔记回收站保留边，恢复重新显示；回收站列表可按已选标签筛选，不从回收站提供全局标签联想。历史正文不回溯标签；ZIP包含当前标签和边，完整snapshot含全部表。

PUT /resources/{UUID}/tags接收names、expected_version、expected_tag_versions（当前关联Tag UUID→version完整映射）与UUID幂等键，BEGIN IMMEDIATE内验证Resource版本、解析或创建Tag、替换边、Resource版本/时间、Activity与幂等同事务。标签变更不写Markdown/修订，版本因此可跳号。不变的集合不增Resource版本。重试返回当前可见集合/版本，不回放旧快照；全局改名不改资源正文版本，Tag独立CAS；集合保存额外校验完整Tag版本映射，防并发改名后旧编辑器按旧名称创建新标签。标签改名不能占用另一规范键（含保留占位），409 TAG_NAME_CONFLICT。共享改名经前端明确确认，服务校验tag version；对所有引用生效，不逐个改正文/历史/时间。Tag操作幂等不复制名称到Activity。

## 界面和恢复

笔记行单独“标签”入口，不与正文编辑同时打开。新笔记先保存再分类。标签草稿受导航/刷新/关闭确认保护；未知写结果保留原key/payload，改动不能偷偷发新请求；409保留草稿、重新读取需确认。改名与集合编辑互斥，重读失败不清草稿，迟到响应在卸载后忽略。完整笔记页有分页/字面名称搜索的标签目录和单标签筛选；首页快速记录不加载标签目录。

SQLite内原子事务覆盖新事实，正常连接FK=ON；不触碰用户vault/既有Markdown。升级先完整快照、副本演练、全原表指纹/索引/触发器及文件校验。完整备份仍hub-files.json v2。旧0004需显式upgrade，无启动迁移、无破坏性downgrade。
