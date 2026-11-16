# v0.9 个人项目 API

2026-11-12｜前缀`/api/v1`。已登录本机会话；全部写请求遵守现有Origin/CSRF契约，参数错误只返回受控信息。不在请求中提供shell、程序路径或工作目录执行参数。

## 1. 通用约定

资源/里程碑/关联ID均UUID。除`opened`、`directory-check`和移除关联外，下面POST/PUT要求UUID格式`Idempotency-Key`。相同操作/key/body不会重复创建或复活已删对象；创建记录的重放返回原Note/关联身份，关联可能已被另一次操作解除，须重读列表核对；其他响应返回当前可见资源/子事实。变更body必须新key。项目修改提供`expected_version`，里程碑编辑另提供`expected_milestone_version`；冲突409保留草稿，不能盲目重放覆盖。里程碑/关联修改递增Project版本；独立Task状态只递增Task版本。

读取项目及全部子视图均要求personal且未删除；列表回收站、lifecycle专门处理软删。隐藏项目返回404，独立Task/Note保持可访问；项目上下文不暴露隐藏端、隐藏里程碑名称或ID。归档项目可读和编辑基本信息以重启，项目任务归属/里程碑/记录/出向关联修改返回`PROJECT_ARCHIVED`；共享Task/Note可在所属模块独立修改。归档不等于私密保护。

## 2. 路由

| 方法/路径 | 参数与结果 |
| --- | --- |
| GET `/projects` | q≤256；status为空或planned/active/paused/completed/archived；trash=false；tag_id可选；recent=false；limit默认30、1～100；offset默认0、≤1000000。返回items/total/limit/offset；每项含tags及progress |
| POST `/projects` | ProjectWrite；返回公共Resource与项目字段/tags |
| GET `/projects/{pid}` | 公共Resource、项目字段、tags、progress |
| PUT `/projects/{pid}` | 完整ProjectWrite及expected_version；返回当前项目（需进度时GET） |
| POST `/projects/{pid}/opened` | 无body；显式导航后记录最近打开时间，返回status=recorded；不改Resource版本或Task，不在GET写入 |
| POST `/projects/{pid}/directory-check` | 无body；返回status及scope=directory_metadata_only，不写业务状态 |
| POST `/projects/{pid}/lifecycle/{trash\|restore}` | expected_version；软删除/恢复，不删除子事实或文件 |
| GET `/projects/{pid}/milestones` | trash=false，按position/id排序；返回items含progress |
| POST `/projects/{pid}/milestones` | MilestoneWrite；返回里程碑 |
| PUT `/projects/{pid}/milestones/{mid}` | MilestoneWrite，校验两个版本；返回里程碑 |
| POST `/projects/{pid}/milestones/{mid}/{trash\|restore}` | expected_version + expected_milestone_version；返回里程碑 |
| GET `/projects/{pid}/tasks` | limit/offset同列表；返回当前可见共享Task的link_id/id/title/version/status/priority/planned_date/milestone_id/milestone_title |
| POST `/projects/{pid}/tasks` | 共享TaskCreate + expected_version + 可选milestone_id；创建Task和归属同事务，返回资源身份 |
| POST `/projects/{pid}/tasks/attach` | task_id、expected_version、可选milestone_id；同项目重新分配里程碑；其他项目已拥有时409，不自动转移 |
| GET `/projects/{pid}/entries` | limit/offset；返回id/note_id/title/entry_kind/created_at/version/note_kind |
| POST `/projects/{pid}/entries` | title 1～500、Markdown body 1～100000、entry_kind=log/experiment/summary、expected_version；返回entry_id/note_id |
| POST `/projects/{pid}/entries/attach` | note_id、entry_kind、expected_version；同Note已有关联时调整角色；返回项目 |
| POST `/projects/{pid}/links/{eid}/remove` | expected_version；只解除专用任务/笔记边，返回status=unlinked；非幂等删除，丢失响应先重读核对 |

ProjectWrite：title必填1～500；description/objective各≤10000默认空；status默认planned；local_directory/repository_url各≤2048默认空；start_date/target_date可空ISO日期，目标不能早于开始；创建expected_version默认0，修改必须当前正版本。目录是绝对POSIX路径，拒绝根目录/重复分隔/点路径/回退/反斜杠/控制字符和超过128组件。仓库只接受经共享safe_url校验的HTTP(S)地址，不发起抓取。

MilestoneWrite：title 1～500，description≤10000，target_date可空，status=todo/doing/done（默认todo），position整数0～1000000默认0；expected_version为当前Project版本；创建expected_milestone_version默认0，修改必须当前里程碑版本。每项目含回收站最多1000里程碑，拒绝超限、不裁剪历史。

progress={done,total,percent,denominator}。统计当前personal/未删除且非cancelled项目Task，done/total向下取整百分比；total=0时percent=null。任务列表仍显示已取消任务供核对，分母明确排除；里程碑状态由用户验收，不自动计算。

## 3. 文件、关联与错误

目录检查逐组件`O_DIRECTORY|O_NOFOLLOW`打开并关闭，不列目录、不读文件、不复制、不执行；返回available/missing/not_configured/unavailable_or_unsafe，不包含底层异常路径。最近项目组件默认隐藏，旧偏好读取追加禁用配置但不自动落盘。

普通Resource API/Tag/Collection/Search接收project；通用context双向投影专用project_task与log/experiment/summary关系，移除委派同一ProjectService，不建立第二套可写边。PDF/书籍使用material，学习知识点使用related，输出使用output。稳定链接`hub://resource/{id}`及`/resource/{id}`。

日志创建沿用NoteService不可覆盖修订。Note先通过稳定子key保存，再确认父关联；若期间Project版本冲突/被归档/软删，已保存Note仍留在普通资料库，可核对后用attach关联，不能声称整个跨文件流程原子。未知结果保持原key/body重试；已确定409后重读项目，先检查普通笔记，避免另key重复创建。

常见受控错误：RESOURCE_NOT_FOUND/MILESTONE_NOT_FOUND/RELATION_NOT_FOUND（404）、VERSION_CONFLICT/PROJECT_ARCHIVED/TASK_ALREADY_ASSIGNED/PROJECT_STATE_CONFLICT/MILESTONE_STATE_CONFLICT/IDEMPOTENCY_CONFLICT（409）、PROJECT_MILESTONE_LIMIT（422）、DATABASE_UPGRADE_REQUIRED（503）。未知新schema拒写；不自动初始化或升级。
