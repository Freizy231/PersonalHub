# Personal Hub 本机运行与数据保护

适用版本：0.9.0（个人项目闭环，保留Phase2资料/学习和原日常/私密库/首页）。当前说明见末节v0.9；中间v0.2～v0.8章节是对应版本历史。Streamlit/Collector继续原README，不触发采集。

## 1. 已选择的本地目录

用户未指定数据/异盘备份路径，本轮采用项目内独立目录作为当前本机方案：

```text
runtime/
├── personal-hub/
│   ├── db/hub.db                  # 新库，当前revision 0009_project_tools
│   ├── notes/<note UUID>/<revision UUID>.md  # 普通正文与历史（不可覆盖）
│   ├── vault/vault.phv             # 首次本机设密后创建的独立密文库
│   ├── library/{blobs,staging,text}/ # PDF原件、在途恢复材料、提取文本
│   └── config/launch-<port>.url    # 临时本机访问凭据，0600，不分享
├── backups/p0-<UTC>/              # 旧情报库的一致性备份和manifest
├── restore-checks/p0-<UTC>/       # 隔离恢复与验证记录
├── wheelhouse/                   # 官方Python依赖轮包，已核对PyPI SHA-256
├── cache/npm/                    # 官方npm依赖缓存，已核对registry integrity
└── releases/                     # 不含个人数据/密钥的源码快照
```

`runtime/`已排除版本管理；资料子目录0700、DB和敏感文件0600。现有`data/intel.db`、`.env`、历史备份保持原路径。不要把整个项目目录公开或上传，仅仅.gitignore不构成访问保护。当前备份同盘，不能防物理磁盘损坏；没有启用自动保留清理或假装已经具备异盘灾备。

## 2. 安装与构建

当前机器已建立`backend/.venv`与前端依赖，旧`.venv`没有改动。新后端锁文件适用于Linux x86_64、Python3.11；实际版本Python3.11.9、Node24.22.2、npm11.19.1。换Python或平台需单独解析/校验并生成该平台锁文件，不盲目升级旧环境。

已安装环境直接运行，无需重装。正常网络环境重建新环境时：

```bash
python3.11 -m venv backend/.venv
backend/.venv/bin/python -m pip install --index-url https://pypi.org/simple --only-binary=:all: --require-hashes -r backend/requirements.lock
```

有本轮官方轮包缓存时可离线安装：

```bash
backend/.venv/bin/python -m pip install --no-index --find-links runtime/wheelhouse --require-hashes -r backend/requirements.lock
```

在`frontend/`运行：

```bash
npm ci --ignore-scripts --registry=https://registry.npmjs.org
npm run build
```

本机离线重装可追加`--offline --cache ../runtime/cache/npm --no-audit --no-fund`。安装时禁用生命周期脚本，Linux原生依赖由匹配平台包提供；构建已实际通过。不能把锁文件的存在当作所有平台都验证过。

## 3. 新库显式初始化与启动

以下命令在项目根运行。**当前新hub.db已初始化，无需再次init**。首次新部署且确认目标没有历史个人数据库时，才执行：

```bash
backend/.venv/bin/python -B -m backend.manage init --data-root runtime/personal-hub
```

目标存在就拒绝，不覆盖、不自动升级；init仅创建0000_foundation空基线；首次新部署随后执行下面的upgrade。当前工作区已显式升级至0009_project_tools，无需重复init/upgrade。升级命令幂等，只认已知新Hub版本，拒绝未知库/旧情报库。常规启动：

```bash
backend/.venv/bin/python -B -m backend.manage upgrade --data-root runtime/personal-hub
backend/.venv/bin/python -B -m backend.manage check --data-root runtime/personal-hub
backend/.venv/bin/python -B -m backend.manage serve --data-root runtime/personal-hub --port 8765
```

仅绑定`127.0.0.1`。启动后用本机编辑器打开`runtime/personal-hub/config/launch-8765.url`，把其中地址在本机浏览器打开；不要截图、复制到聊天或分享该地址。凭据在URL片段中，换取HttpOnly/SameSite=Strict会话后立即移除，不能复用。刷新页面继续使用cookie；会话为滑动有效期（连续8小时无操作过期，最长7天），服务重启失效；需要重新进入时重启服务后用新入口，或用`serve --open`自动打开（凭据只从0600入口文件交给浏览器，不输出到终端或日志）。不要同时启动两个相同端口的实例。

升级/备份前先在原终端Ctrl+C停止新Hub服务。v0.2及后续服务持生命周期共享锁，维护命令须取排他锁，运行时维护会返回HUB_RUNNING_STOP_FIRST；不要删锁文件。升级会自动把备份/副本演练记录写入数据根maintenance/upgrade-<UTC>/，副本先通过再改活动新库；DDL事务内失败回滚并保留现场，不盲目重试/清空。若提交后写报告失败，先运行check确认revision，不假定已回退。

启动服务只写临时启动凭据和维护锁，不创建/迁移数据库。缺少新库时界面显示“未初始化”，版本不匹配时提示，绝不重建空库。可用`PERSONAL_HUB_DATA_ROOT`、`PERSONAL_HUB_PORT`设置独立环境，但不改旧`DB_PATH`/`.env`；serve的显式参数优先。默认port8765，自定义端口的启动入口文件名随端口改变。

v0.4已开放首页快速记录/今日任务/日程/提醒收件箱，Library普通笔记与Life普通记录；Life任务列表、月历选日议程；Task/Event CRUD/回收站/JSON、关联与一次性站内提醒。处理器约5秒运行，页面约15秒刷新，不发送系统通知。普通journal未加密；独立私密库仅在生活→私密库提供专用录入，PDF/学习已开放，AI仍未开放。私密内容及真实元数据只进密文容器，不使用普通Note API。

用户已反馈Phase2浏览器验收基本完成，授权先功能后包装；当前v0.9/P3-01专项验收见releases/v0.9.0.md。此沙箱历史loopback bind受限，当前自动验证为ASGI/真实SQLite/文件与Vue组件/构建，未冒称本版浏览器E2E；不改绑0.0.0.0。

生产同源访问由FastAPI提供`frontend/dist`。目前不承诺Vite开发服务器直接连接API：基础安全配置不允许5173跨来源访问；后续开发代理必须单独限定Origin，不能开启wildcard CORS。

## 4. 旧库审计、备份和隔离恢复

维护工具仅标准库，无需GitHub Token，不导入旧配置/初始化代码。所有路径显式指定，输入必须存在，输出目录必须全新；不要重用示例中的目录名。

```bash
.venv/bin/python -B -m scripts.data_safety audit --source data/intel.db
.venv/bin/python -B -m scripts.data_safety backup --source data/intel.db --output runtime/backups/manual-NEW-ID
.venv/bin/python -B -m scripts.data_safety verify --backup runtime/backups/manual-NEW-ID
.venv/bin/python -B -m scripts.data_safety restore-check --backup runtime/backups/manual-NEW-ID --output runtime/restore-checks/manual-NEW-ID
```

`audit`输出表名/列名/计数/类型敏感行指纹和结构指纹，不输出数据行/URL/Token；指纹不是加密，报告仍应本地保管。`backup`使用SQLite Backup API并固定同一读事务，包含已提交WAL，允许旧Collector继续提交后续观察；备份的是开始时的一致快照。`restore-check`只恢复到新隔离目录，核对结构、版本、所有表字段和`sqlite_sequence`状态，无法替换活动库。

有效备份最后写`manifest.json`；有`INCOMPLETE.json`或无manifest的目录不能恢复，不要手工删标志把失败伪装成成功。错误返回非0/固定错误码，无原始异常中的敏感字符串。超时、中断、磁盘错误不会覆盖旧备份；失败目录保留待核查。当前工具支持数据库user_version≤2，遇到更新版本拒绝备份/恢复，不能用它管理未来hub.db的文件全量灾备。

当前备份范围仅旧SQLite，不含.env/密钥、Markdown/PDF、项目源码、音乐和照片。不能把该工具输出称为未来Personal Hub全量备份。活动路径恢复尚未实现，应按[安全恢复手册](SECURITY_AND_PRIVACY.md)协调停写、保全WAL/SHM和恢复前快照，再独立验证与切换；不要把隔离恢复目录里的.db直接在线覆盖到生产。

## 5. 新Hub数据库、笔记、文献与密文完整备份/隔离恢复

v0.8完整snapshot manifest v3覆盖DB及notes/vault/library全树：普通正文/历史/回收站、未引用/暂存、活动密文库及失败密文.tmp、PDF原件/在途文件/提取文本；不含旧intel.db/.env/密码/启动凭据/外部目录。先停止API与外部编辑器，在项目根运行（NEW-ID为新目录名）：

```bash
backend/.venv/bin/python -B -m backend.manage backup --data-root runtime/personal-hub --output runtime/backups/hub-NEW-ID
backend/.venv/bin/python -B -m backend.manage restore-check --backup runtime/backups/hub-NEW-ID --output 数据根 restore-checks/ 下的隔离恢复目录NEW-ID
```

backup持维护排他锁与notes-files锁，DB使用Backup API/全字段指纹，文件逐项SHA/大小/引用核验、复制后再次核对源树，最后写hub-files.json完整证明。目录变化/缺失/篡改/符号链接/公共权限/超1MiB Note、超16MiB密文/提取文本或超100MiB PDF/在途文件会拒绝，不跳过异常文件。外部编辑器不遵守锁，备份前需停用；失败目录保留，不手动删标记伪装成功。

0007/0008/0009快照必须hub-files v3；旧schema快照v1/v2兼容。0003+备份无hub-files清单一律拒绝，即使当时没有Note。旧0000..0002 SQLite-only仍可恢复到原database.sqlite3诊断布局。完整快照restore-check在**全新目录**产生db/hub.db+notes+存在时的vault/library和新config目录，并保留独立database.sqlite3诊断副本；全部验证后才移除HUB_INCOMPLETE.json，未完成目录拒绝业务访问。不会自动替换活动库。

若需试运行完整恢复的**隔离数据根**，先停原服务，使用新的端口与恢复目录（仅使用本次实际恢复输出替换示例）：

```bash
backend/.venv/bin/python -B -m backend.manage serve --data-root 数据根 restore-checks/ 下的隔离恢复目录NEW-ID --port 8766
```

此后在该目录config/launch-8766.url打开新入口；恢复副本与活动库随后各自变化，不会自动双写/合并。确认恢复无误、保全当前新增事实后才决定长期切换根，不在线覆盖或把两边目录混合。database.sqlite3是诊断快照，运行时权威为db/hub.db。

Task/Event/Reminder JSON、Note ZIP/Markdown是本机开放格式导出，不是跨模块原子备份；无JSON/ZIP导入，也不是自动异盘备份。Note ZIP含普通回收站/全部成功修订/活动，排除private/未提交文件；单次累计正文上限64MiB。下载目录由浏览器决定，导出含个人内容，不默认分享。

## 6. 验证与源码版本快照

```bash
.venv/bin/python -B -m unittest discover -s tests -v
backend/.venv/bin/python -B -m unittest discover -s backend/tests -v
backend/.venv/bin/python -m pip check
```

前端在`frontend/`执行`npm test`（15项组件/日期交互验证，模拟API，非浏览器）和`npm run build`。Backend测试采用HTTPX异步ASGI transport，不需要监听端口；任务测试的周期唤醒event loop仅用于兼容沙箱的跨线程唤醒限制，仍执行真实线程/SQLite；包含生命周期、会话/CSRF/Origin、显式迁移和版本保护。它不等于真实HTTP/浏览器端到端测试。旧Streamlit bare-mode警告不影响既有回归结果。

受限`.git`目录保持原样，当前没有Git提交；临时用经过校验的源码版本快照保证可评审/回退代码：

```bash
.venv/bin/python -B -m scripts.package_release --version 0.10.0 --output runtime/releases/v0.10.0-NEW-ID
```

打包严格白名单源码/文档/锁文件，排除所有.env、数据库、备份、启动凭据、依赖和构建产物，输出不覆盖；manifest记录源码与压缩包SHA-256。代码包不是数据备份；恢复源码时保留当前版本和用户数据，不从包中寻找Token。正常Git工作区恢复后再建立正式提交/标签，不删除受限占位目录。


## 10. v0.3日程与提醒运行

当前工作区0005_ordinary_tags已完成备份/副本演练/升级，**不用init**；已有进程需Ctrl+C后重启才载入新API。其他v0.2～v0.6部署先停服务，执行同一upgrade，再serve；重复upgrade只返回up_to_date。不要在服务运行时删锁或强行迁移。

处理器随单进程API启动，不安装系统服务，不配置cron，不要求APScheduler。设置显示处理器状态：running正常；starting启动中；standby已有进程占用；degraded数据库/版本/权限暂不可用；disabled主动关闭。degraded时先查看设置数据库状态、执行check，必要时停服升级后重启；日志只含错误类型，不打印真实资料。standby实例不自动接管，正常使用单API进程，原持有者退出后重启备用实例。锁文件不可删除解锁。

临时禁用处理器（保留事实、不执行到期处理）：

```bash
PERSONAL_HUB_REMINDERS=0 backend/.venv/bin/python -B -m backend.manage serve --data-root runtime/personal-hub --port 8765
```

恢复时去掉该环境变量重启。24h内到期进入delivered、超过24h进入missed，两者均可在未读收件箱标已读；不是系统通知，不保证精确时间/机器停机时触达。GET没有补投递副作用。Task/Event删除/改期/终态取消pending，恢复需主动重设；不会自动改变已落库历史。

v0.3历史维护实据（继续保留）：

- 升级前：数据根 maintenance/ 下本次升级的 pre-upgrade 快照；同级rehearsal演练通过。
- 升级后：数据根 backups/ 下对应版本的完整快照。
- 隔离恢复：数据根 restore-checks/ 下的隔离恢复目录，全部表指纹一致，FK=0错误/integrity=ok。
- 汇总：本机该版本的验收证据文件（仅版本/计数/维护路径，不含内容或秘密）。所有旧备份继续保留。

Event/Task/Reminder三个JSON下载是开放事实格式，不是全库原子快照，也未实现JSON导入。v0.3当时为SQLite-only；v0.4完整备份已扩大到DB+notes，见第5节。退回v0.2须保全v0.3新增事实并恢复匹配0001的隔离库与程序，禁止旧代码硬读新revision或破坏性downgrade。


## 11.（历史v0.4交付，见当前§15） v0.4普通笔记与中断诊断

Library管理全部普通笔记，Life的普通记录复用同一服务，首页快速记录。每次保存写独立Markdown修订；不要直接编辑/覆盖受管notes目录或手动改数据库。外部编辑引起hash冲突时应用不会覆写；先保全磁盘内容，参考备份核对，或将正文复制另建普通笔记。历史“放入编辑草稿”需明确保存才生成新修订。私密日记仍未实现，不向普通笔记录入真实心事。

正常保存不需运行任何恢复命令。异常停机/磁盘错误后，停API再执行：

```bash
backend/.venv/bin/python -B -m backend.manage notes-check --data-root runtime/personal-hub
backend/.venv/bin/python -B -m backend.manage notes-reconcile --data-root runtime/personal-hub
```

check只读报告prepared及其orphaned/conflict分类、已提交修订异常数量；不输出正文/标题。reconcile仅把当前prepared标记orphaned/conflict，文件仍保留，不修复丢失正文、不猜测提交、不清理孤儿；此前已标记文件也保留在DB/备份。成功保存的旧笔记不受此操作替换。请根据原请求或草稿安全重试；文件丢失/冲突先核对备份，不能清空DB“修复”。没有自动文件保留/清理任务。

本版实际维护实据：

- pre-upgrade：数据根 maintenance/ 下本次升级的 pre-upgrade 快照；同级rehearsal通过。
- 完整快照：数据根 backups/ 下对应版本的完整快照，含hub-files.json。
- 新根隔离恢复：数据根 restore-checks/ 下的隔离恢复目录，db/hub.db与原全表指纹一致，文件清单一致。
- 当前2条Task/5条Activity/2条幂等记录保留，Note/Revision/file_operations为0，无演示笔记；实际有正文与孤儿文件的恢复另在临时测试验证。
- 本机该版本的验收证据文件保留仅版本/计数/路径证据。两库integrity=ok、FK无异常；28原受保护文件未变。

回退先保全当前DB+notes完整快照与本版新事实；v0.3不能硬读0003，须恢复匹配的旧程序+旧库到新目录，不覆盖唯一资料、不做destructive downgrade。所有历史备份继续保留，旧情报仍独立。

## 12. 私密库操作（v0.5历史引入，当前密码/锁定规则继续适用）

当前新库已0005_ordinary_tags，**无需init/upgrade**。先Ctrl+C停止旧Hub，再在项目根执行：

```bash
backend/.venv/bin/python -B -m backend.manage serve --data-root runtime/personal-hub --port 8765
```

从`runtime/personal-hub/config/launch-8765.url`打开新完整地址（不分享地址），确认v0.5.0。生活→私密库或Settings链接进入；密码只在本机设置/输入，不发聊天。当前活动私密库已由用户创建；本会话未解锁/读取内容，没有演示密码。新部署初次才设密。初次自行设密/重复/确认无恢复；错密码或损坏统一拒绝并短暂退避。操作参数/格式见API_VAULT及ADR-0005。

独立加密目录`runtime/personal-hub/vault/vault.phv`，目录0700/文件0600；config/vault-session.lock是锁inode，不能删除绕过持锁。5分钟空闲或30分钟绝对到期、普通退出、服务重启均锁定。状态轮询不续期，页面活动最多30秒touch；隐藏/离开清未存草稿并尝试锁定，同cookie其他面板正常网络下每秒发现锁定。新面板撤销遗留解锁再要求密码。网络请求挂起由客户端已确认期限清空，但挂起/冻结的浏览器无法承诺物理擦除或精确时限。没有自动保存/本地持久草稿，先明确保存再切窗口；自动清空不等待确认。

改密在私密库内，需旧密码、重复新密码、确认，完成锁定；旧备份/密文.tmp仍可能需旧密码。失败则重新解锁核对，replace后fsync故障可能已生效，不假定500等于未写；不要反复初始化/清目录。密码遗忘无后门或云恢复，不要把密码与密文备份放在同一明文文件。

**完整备份/恢复必须使用本版v0.5+工具**。停API与外部编辑器后：

```bash
backend/.venv/bin/python -B -m backend.manage backup --data-root runtime/personal-hub --output runtime/backups/hub-v050-NEW-ID
backend/.venv/bin/python -B -m backend.manage restore-check --backup runtime/backups/hub-v050-NEW-ID --output 数据根 restore-checks/ 下的隔离恢复目录NEW-ID
backend/.venv/bin/python -B -m backend.manage serve --data-root 数据根 restore-checks/ 下的隔离恢复目录NEW-ID --port 8766
```

NEW-ID每次换新，不覆盖。打开恢复根config/launch-8766.url，在本机输入该备份对应密码，核对虚构笔记及历史；原活动库不替换、两根不合并。清单version2最后发布，包括密文hash与.tmp，旧version1在无vault时仍兼容。hash/SQLite一致证明字节恢复，不证明你记得密码；密码只能在本机解锁验证。不得手工删HUB_INCOMPLETE把部分恢复当成功。若HUB_RUNNING_STOP_FIRST，回用户原终端正常Ctrl+C，不能杀其他程序/删锁。

本次活动Hub已存在1条用户普通笔记，未读出其标题/正文。完整快照`数据根 backups/ 下对应版本的完整快照`、隔离恢复`数据根 restore-checks/ 下的隔离恢复目录`已通过；活动vault未创建，带加密内容及失败.tmp的恢复使用临时虚构样例测试。汇总`本机该版本的验收证据文件`。本次无数据库迁移，原任务/笔记/历史保留，旧情报/.env不动。同盘备份不防磁盘损坏。

源码回退先做v0.5完整备份且保留runtime/vault；v0.4虽能读取同0003，但其备份器会漏vault，不能用它备份含私密数据的根。恢复旧程序时仅运行隔离旧根，保全本版新事实，不在线降级或移除加密目录。

## 13. v0.6首页布局启动、升级与恢复（历史交付证据）

v0.6交付时工作区完成0004_dashboard_preferences的备份/副本演练/升级，只需停止旧Hub再启动：

```bash
backend/.venv/bin/python -B -m backend.manage serve --data-root runtime/personal-hub --port 8765
```

打开`runtime/personal-hub/config/launch-8765.url`的新完整入口。不要init、不用重复upgrade。**其他仍0003的v0.5数据根**先正常停服务，再执行`backend/.venv/bin/python -B -m backend.manage upgrade --data-root <该数据根>`，确认成功后serve；不能为升级删锁/强行杀进程，HUB_RUNNING_STOP_FIRST时回原终端Ctrl+C。

首页“调整首页”或Settings“首页布局与快捷入口”可设显隐、用上移下移排序、添加最多12个内部/网页入口。保存前预览，reset只放草稿，明确保存才生效；重启与完整恢复保留。新表首次GET不插行，版本0默认配置是正常状态。名称/URL为普通明文设置，不输入私密内容或凭据；网页HTTP(S)地址暂不支持query/fragment，先预览完整目标再明确点击确认，后端不探测目标。不会启动本机命令或替代专业应用。

两页同时编辑冲突保持草稿且禁写，确认“重新读取布局”后核对；网络丢响应保留同key/payload重试，不改内容另发请求。未知/损坏配置只读回退默认组件+空快捷，保留原行；请停服先完整backup核对备份，不DELETE重建或强制reset。现有Note草稿在重读时受保护，开始调整先确认丢弃；设置草稿离开/刷新也提示。

本次升级前`数据根 maintenance/ 下本次升级的 pre-upgrade 快照`及同级rehearsal，升级后完整快照`数据根 backups/ 下对应版本的完整快照`、全新恢复根`数据根 restore-checks/ 下的隔离恢复目录`。新旧原行核验通过，普通Note与用户vault密文字节不变；新偏好表0行，没有代写演示配置。汇总`本机该版本的验收证据文件`。此为密文恢复验证，不代表已解锁本人私密库。

完整backup/restore-check命令同§5，输出必须全新，快照v2包含SQLite新增表与所有notes/vault，不需新文件清单格式。用恢复根试运行需新端口/新launch；旧0003历史备份恢复后先upgrade再启动v0.6。回退先保全v0.6完整快照与新增偏好/业务事实，匹配旧程序+旧库隔离恢复，不downgrade、在线覆盖或丢vault。旧v0.5不能写0004。当前同盘备份仍非物理灾备，尚未自动清理。

## 14. v0.7普通Note标签（历史交付记录）

当前活动根已升级0005_ordinary_tags，不用init/重复upgrade。先Ctrl+C停止旧Hub，再运行：

```bash
backend/.venv/bin/python -B -m backend.manage serve --data-root runtime/personal-hub --port 8765
```

打开config/launch-8765.url中新完整地址，不分享凭据。资料库或生活普通记录→保存笔记→行内“标签”；每行一名，显式保存；改共享名称会影响所有引用，先确认。目录按名称查找/分页/单Tag筛选；这不是正文全文搜索。首次读取不创建标签，当前未替用户写演示分类。Task和私密库均未开放标签。使用和错误处理详见API_TAGS与ROADMAP §9。

其他0004部署先停服务，执行§3的显式upgrade再serve；版本检查不自动迁移。0005只加tags/resource_tags，副本演练及原表全行/索引/触发器验证通过。完整backup/restore-check沿§5，仍v2包含数据库+notes+vault全部树。普通ZIP新增当前Tag，不含私密资料；没有JSON导入。回退先保全0005完整快照和新标签事实，隔离匹配旧程序/旧根，禁止在线downgrade/覆盖唯一资料。

本次升级前数据根 maintenance/ 下本次升级的 pre-upgrade 快照、同级rehearsal；升级后数据根 backups/ 下对应版本的完整快照及数据根 restore-checks/ 下的隔离恢复目录；汇总本机该版本的验收证据文件。两新表0行，原行/普通文件/vault密文字节不变，未解锁私密内容；28受保护资产不变。232项自动检查/类型与生产构建通过，真实HTTP/浏览器因socket限制未测；同盘非物理灾备。源码包本机对应版本的源码发布包/不含秘密/用户数据/备份/依赖/dist，不是数据备份。


## 15. v0.8 / Phase2 操作（历史，当前见§16）

本节覆盖§7～14历史版本的revision/“尚未开放”状态；保留历史用于对应旧备份，不照旧版命令覆盖当前根。当前活动库0007_phase2，所有旧记录/普通Note/vault密文已保留，无需init/重复upgrade。旧服务Ctrl+C后：

```bash
backend/.venv/bin/python -B -m backend.manage check --data-root runtime/personal-hub
backend/.venv/bin/python -B -m backend.manage serve --data-root runtime/personal-hub --port 8765
```

打开config/launch-8765.url中新地址，确认v0.8.0。资料库默认PDF，可切书籍/网页/课程/Markdown/集合；学习中心直接管理目标、路线、知识点和验收；顶部Ctrl/⌘K搜索。按[统一验收](PHASE2_ACCEPTANCE.md)逐项操作，等待用户验收后再安排Phase3。

系统PDF提取使用已安装/usr/bin/pdfinfo、/usr/bin/pdftotext及Linux隔离，不需要重装Python环境；未安装PyMuPDF，无新依赖。新机器必须核实Poppler、libseccomp/Landlock、受限解析测试，失败返回PDF_SANDBOX_UNAVAILABLE而不裸跑。密码PDF在专业软件本机解锁另存，扫描PDF只管理原件无OCR。解析错误可查看最近作业/重试，取消上传不等同取消已经提交的提取；原文件和失败在途材料保留，不手删library。

阅读器在设置登记当前机器实际安装的固定白名单程序，文献详情预览后确认；无任意路径/args，不自动安装。外部进度手动记录；实际GUI启动待本人，非evince程序参数仍需安装后核实。没有AI、云OCR、网页正文抓取或专业内置阅读器。

普通ZIP在设置下载，上限256MiB，包含普通资料/修订/PDF/关系/证据/提醒/有效首页设置，排除私密库/密钥/旧情报/索引/在途。它不支持直接合并导入，不代替§5完整快照。完整备份/恢复命令不变，但manifest现在v3，**v0.7程序不得备份当前0007根**。修复或回退先保全现有全树和新事实，再以匹配代码/快照新根恢复，不downgrade。

实际升级前数据根 maintenance/ 下本次升级的 pre-upgrade 快照、同级rehearsal；升级后备份数据根 backups/ 下对应版本的完整快照、隔离恢复数据根 restore-checks/ 下的隔离恢复目录。输出已存在，手动备份请使用全新名。汇总本机对应的验收记录，旧表/两受管文件/28保护资产一致；vault未解锁，无演示资料。

本轮根66/后端151/前端55、类型与构建通过；主JS496.14KB/gzip154.70KB。规模脚本scripts/benchmark_phase2.py及本机对应的性能记录报告仅ASGI本机测试，不是浏览器指标。当前socket受限未启动真实HTTP；不要用公网地址绕过。本机浏览器/主题/GUI验收按指南完成。


## 16. v0.9操作与恢复证据（历史，当前见§17）

活动根runtime/personal-hub现为0008_projects，已完成维护，日常无需init/upgrade、重装依赖或重新生成空库。项目根：

```bash
backend/.venv/bin/python -B -m backend.manage check --data-root runtime/personal-hub
backend/.venv/bin/python -B -m backend.manage serve --data-root runtime/personal-hub --port 8765
```

旧进程若存在先在原终端Ctrl+C；打开config/launch-8765.url中新完整入口，不外发凭据。项目导航现开放，基础信息→任务→里程碑→日志/总结→资料关联；首页布局可启用默认隐藏的最近项目。目录检查仅存在性与可访问性，不启动VS Code/Codex/终端；下一批P3-02才做该闭环。

295项自动检查和类型/构建通过；完整快照与新根恢复全表/文件一致，原42张非版本表/6文件保持，新增项目表均0行。详见本机该版本的验收证据文件及[v0.9验收](releases/v0.9.0.md)。本期维护前完整备份maintenance/ 下本次升级的 pre-upgrade 快照；完整备份数据根 backups/ 下对应版本的完整快照；恢复数据根 restore-checks/ 下的隔离恢复目录。历史备份不清理。

后续手动备份/恢复仍按§5停服务并使用全新输出目录。0008同样强制manifest v3；外部项目源码目录不在备份中，需独立管理。源码本机对应版本的源码发布包仅源文件，换机仍需按锁文件安装/构建，不包含dist/环境/数据。旧源码不能读写0008，回退保全当前数据后用对应旧快照与源码恢复新根，不做downgrade或在线覆盖。

用户仅反馈Phase2基本完成，本版浏览器专项仍待本人；环境未新增TCP/GUI实测宣称。按功能优先迭代，右上统一查询/范围下拉与博客式笔记等体验包装尚未实施。


## 17. v0.10操作与恢复证据（历史，当前见§18）

活动根runtime/personal-hub已显式升级0009_project_tools，无需init/重复upgrade、安装或升级依赖。先在原终端Ctrl+C停止旧进程，项目根：

```bash
backend/.venv/bin/python -B -m backend.manage check --data-root runtime/personal-hub
backend/.venv/bin/python -B -m backend.manage serve --data-root runtime/personal-hub --port 8765
```

打开config/launch-8765.url中新入口，不外发凭据。首次设置→项目专业工具：选已检测的GNOME Terminal/VS Code/Codex并命名保存；每类一份，尚未代用户登记。项目登记绝对目录后，专业工具页签选择配置→预览→确认打开。程序升级/可用性变化先在设置重新绑定，禁用配置不执行。没有默认安装器/任意shell/自动Codex任务；Codex需要用户自己的CLI配置，本轮不读/改其凭据。

看到“结果不确定”先核对桌面并刷新记录，原预览重试只查已有预约，不会再次执行；不要盲目另建预览重复打开。“已发起”不是GUI成功证明。外部应用只用于可信项目，恶意样本请在用户自己的隔离实验环境处理。部分目录/程序组可写等权限异常会不可用，先核对安全权限，不关闭检查或换任意命令绕过。只支持固定适配器，其他终端暂不支持。

320项检查、类型/构建通过。升级前完整备份数据根 maintenance/ 下本次升级的 pre-upgrade 快照；完整v3快照数据根 backups/ 下对应版本的完整快照；新根恢复数据根 restore-checks/ 下的隔离恢复目录。46旧非版本表/6受管文件/37保护资产保持，新三表0行；完整数据库/文件比对一致。证据本机该版本的验收证据文件，真实vault未解锁，无真实桌面启动测试。

后续手动备份/恢复仍按§5停服务用全新输出目录，完整v3包含profile/preview/history，恢复不执行。普通ZIP不含机器工具配置与执行审计；外部源码/应用/CLI凭据不在备份内。源码本机对应版本的源码发布包仅源码，换机按锁文件安装/构建。回退匹配旧程序+升级前完整快照恢复新根，保全升级后新资料，不改revision/downgrade/在线覆盖；同盘非物理灾备。详细新功能专项清单见releases/v0.10.0.md。

## 18. v0.11当前操作与恢复证据

当前活动hub.db=0010_diet，`backend.manage check`=ready，不需要init或重复upgrade。停止旧实例后从项目根运行：

```bash
backend/.venv/bin/python -B -m backend.manage serve --data-root runtime/personal-hub --port 8765
```

只在本机使用`runtime/personal-hub/config/launch-8765.url`里的新入口，不打印/分享凭据。生活→饮食先登记食材→食谱→周三餐→采购预览/明确追加→独立清单；冰箱库存为明确手工余额盘点。设置→首页可启用默认隐藏的今日三餐。

本次维护已完成，记录`本机该版本的验收证据文件`：

- 升级前完整快照：`数据根 maintenance/ 下本次升级的 pre-upgrade 快照`。
- 完整v3备份：`数据根 backups/ 下对应版本的完整快照`。
- 全新恢复根：`数据根 restore-checks/ 下的隔离恢复目录`；完整数据库审计与受管文件清单一致，integrity=ok、FK0。
- 40保护资产字节不变；49旧非版本表与6受管文件不变；7张新表全部0行，无生产演示食材/菜单/库存。旧Collector锁inode保持，真实vault未解锁。
- 根66+后端200+前端81=347检查通过，类型/生产构建通过；依赖/引擎约束未改变，仅应用版本变更。真实浏览器/离线桌面/两主题两尺寸验收见版本说明。

旧v0.10完整快照/恢复对应版本的快照目录及其升级前快照、所有更早历史均保留。本期保护基线为本机该版本的protected基线文件，用户资料基线本机该版本的user基线文件；只本机保留，不进源码包。

回退：先完整备份当前根与新录入，再用v0.10匹配源码和本次pre-upgrade快照恢复全新根，核对后人工切换，保留当前0010根，不在线覆盖、不downgrade、不手改revision。新的0010业务资料不自动合并进旧库，需保全导出等待兼容迁移。完整v3备份不含外部音乐/项目/CLI配置、旧intel.db/.env；普通ZIP不能替代灾备。同盘快照仍不能应对物理磁盘故障。

源码包`本机对应版本的源码发布包`（source.tar.gz/manifest），不含用户数据/凭据/依赖/dist；没有Git提交或tag。未初始化的新环境需按既有显式初始化/锁定环境流程，不把本机ready状态当作通用安装脚本。

## 19. v0.12 功能候选、API凭据与回退

活动schema0012_intelligence_assistant，已完成0010升级演练/事务/备份恢复，正常启动命令不变。先读HANDOFF与releases/v0.12.0.md；当前实例ready，无需重复初始化。新机器恢复旧schema才显式upgrade，持锁服务必须在原终端停止，不删除锁或杀未知进程。

```bash
backend/.venv/bin/python -B -m backend.manage check --data-root runtime/personal-hub
backend/.venv/bin/python -B -m backend.manage serve --data-root runtime/personal-hub --port 8765
```

API设置在设置页/AI页：完整公开HTTPS443 endpoint（无query/fragment）、model、密钥、启用。仅支持兼容Chat Completions JSON；不下载或部署Ollama，不自动请求模型列表/测试付费调用。密钥0600只存config/assistant-secrets/UUID，**完整快照故意不含密钥**；恢复新根需重新绑定，不能从根.env寻找替代。旧密钥文件保留，不自行批量清理；服务商撤销由本人执行。开启助手不授权自动发送，每次选材→预览→确认。

财务为独立明文SQLite域，完整备份包含账本；普通ZIP不包含账本，单独JSON导出需确认并安全保存。JSON只追加导入不覆盖，不能恢复回收历史/旧版本时间戳，完整灾备使用v3快照。运动含普通ZIP和单独JSON；新情报表完整备份包含，不在普通资源ZIP中。私密vault仍密文，勿代解锁。

原GitHub采集器/数据库路径不改，新情报明确按钮只读导入；原采集器仍独立调度，不加第二个GitHub写入者。新公开源仅手动单批，未新增网络定时任务。详情保存原始载荷，来源失败不清已有资料；网络/限流错误可按原key核对，不为失败自动连续采集。

本机证据：本机该版本的验收证据文件、v0120-legacy-rehearsal.json与HANDOFF所列快照路径。回退先保全v0.12新数据，匹配旧程序+旧完整备份恢复新根，不能在线覆盖或手改revision。下一迁移0013起，0000～0012不可改。完成本人功能验收前暂停视觉包装。
