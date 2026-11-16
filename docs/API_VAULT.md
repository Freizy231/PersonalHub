# v0.5 私密 Note API

前缀`/api/v1/vault`。所有端点要求普通本机会话和当前0005_ordinary_tags（普通SQL版本，vault容器仍PHV1）；HUB_INCOMPLETE拒绝。非GET要求同源Origin及X-CSRF-Token。输入extra=forbid；校验错误不回显密码/正文；写请求体上限1MiB。所有响应no-store，无access log。KDF/加密/文件操作在工作线程。

| 方法与路径 | 输入 | 返回/行为 |
| --- | --- | --- |
| GET /status | 无 | initialized/unlocked/expires_in/idle_seconds/absolute_seconds；无私密计数/ID，轮询不续期 |
| POST /setup | password、confirmation、acknowledge_no_recovery=true | 首次设密并解锁；已有库/孤儿暂存不能覆盖 |
| POST /unlock | password | 解锁绑定当前普通会话；错误密码/篡改统一403 VAULT_UNLOCK_FAILED；相同普通会话可显式重新输入密码解锁另一面板，重新验证后开启新的期限 |
| POST /lock | 无 | 当前owner锁定，重复安全；撤销其他会话不允许 |
| POST /touch | 无 | 明确页面活动续5分钟空闲，不续30分钟绝对期限 |
| POST /password | old_password、password、confirmation、acknowledge_no_recovery=true | 旧密码验证、新salt/key原子重加密后锁定 |
| GET /notes | trash=false、limit=20(1..100)、offset=0(0..1000000) | items、total，仅私密范围；更新时间/UUID倒序；列表不返回正文/修订 |
| GET /notes/{UUID} | 无 | 私密Note全部字段及revisions（受整个容器16MiB限制） |
| POST /notes | title、body、note_kind、local_date | 200 Note；必须Idempotency-Key UUID |
| PUT /notes/{UUID} | 上述字段+expected_version | 200 Note；必须Idempotency-Key UUID |
| POST /notes/{UUID}/trash、/restore | expected_version | 200 Note；必须Idempotency-Key UUID，保留全部历史 |

password/confirmation/old_password：12..256 Unicode字符，必须合法UTF-8，不trim；重复密码仅匹配校验，不落盘。title共享普通Note验证（trim后非空、最多500字符），body最多200000字符，可空；note_kind仅private_journal/treehole，local_date是严格YYYY-MM-DD有效日期或NULL。expected_version严格正整数。没有任意路径、privacy降级、原始密钥、导出/导入/执行端点。

私密Note：id UUID、title/body/note_kind/local_date、created_at/updated_at UTC字符串、deleted_at UTC或NULL、version严格递增、revisions数组。每次明确保存追加完整内容快照，元素title/body/note_kind/local_date/version/updated_at；软删/恢复增version但不制造内容修订。历史恢复仅由客户端放入草稿后PUT新修订，不覆盖历史。列表分页，当前详情一次返回整个单项历史，未来容量升级须单独评估。

幂等作用域独立vault，摘要及rid映射在密文operations；同key同payload重试返回目标当前事实（可能已删除/再编辑），不重放旧快照，不重复修订；同key不同payload/rid/action为409。编辑expected_version冲突409 VERSION_CONFLICT。无自动淘汰，4096操作或1000Note或容器16MiB达到上限返回409 VAULT_CAPACITY，旧库不改。失败请求不自动换key/另存；锁定清除客户端草稿/key后先解锁核对现有事实。

423 VAULT_LOCKED：含普通会话到期；401 SESSION_REQUIRED：普通会话无效。409 VAULT_BUSY：同进程其他会话已解锁或其他进程持锁；409 VAULT_EXISTS/VAULT_CHANGED/VAULT_FILE_UNSAFE/VAULT_RECOVERY_REQUIRED分别表示存在、外改、路径权限异常、无活动容器但有恢复文件。外部变化拒绝覆盖并锁定。错密码后按2、4、8、16、30秒进程级退避，期间429 VAULT_RATE_LIMIT；这不能阻止离线密码穷举，需高强度密码。

保存/改密写入或fsync失败返回脱敏500并锁定。替换前旧库完整；替换后异常结果不确定，重新解锁检查，不把500理解为必定未写入。新密码替换后的异常可能已生效。失败密文.tmp保留在完整备份中，不能为“修复”删除唯一副本或重建库。

普通GET /notes、Markdown/ZIP、Task/Event/Activity、未来普通搜索均没有任何私密Note身份或计数，私密数据从未进入普通库。无私密明文导出/云AI/跨库关联/私密标签或索引。
