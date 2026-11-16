# v0.12 API 与数据契约

统一前缀 `/api/v1`。全部业务端点要求本机会话，写入要求同源 Origin+CSRF；有 Idempotency-Key 的写端点必须 UUID。JSON 额外字段拒绝、错误不回显输入。时间 UTC RFC3339；业务日期 YYYY-MM-DD、月份 YYYY-MM。新领域版本为正整数 CAS，创建 expected_version=0。

## 财务 `/finance`

- `GET /categories`；`POST /categories`、`PUT /categories/{id}`：name、direction(income/expense)、expected_version；分类方向创建后不可改，名称+方向唯一。
- `GET /entries?month=&trash=false&limit=50&offset=0`：按月分页（limit≤100）。`POST /entries`、`PUT /entries/{id}`：local_date、category_id、currency、amount（十进制字符串）、memo、expected_version。
- `POST /entries/{id}/trash|restore`：expected_version；软删保留；统计排除回收记录。
- `POST /budgets`、`PUT /budgets/{id}`：month、支出category_id、currency、amount（允许0）、expected_version。分类+币种+月份唯一，0预算不同于无预算。
- `GET /report?month=`：币种分组收入/支出/结余、分类合计、预算/已支出/剩余；负剩余如实返回，无换汇/跨币汇总。
- `POST /export`：明确动作返回整个私密明文账本JSON，包括回收站；不要求幂等key（只读）。需用户保护下载文件。
- `POST /import`：categories[{id,name,direction}]、entries[{id,local_date,category_id,currency,amount,memo}]、budgets[{id,month,category_id,currency,amount}]，分别≤500/10000/1000。只追加，稳定ID相同且字段相同跳过，内容不同/自然键冲突/引用缺失整批回滚；不覆盖、自动合并或恢复旧版本。UI只接收≤900KB Hub导出、显示数量预览后确认，回收站不导入；HTTP总请求仍1MiB限制。完整时间戳/版本/回收历史恢复使用完整备份。

金额范围至10^12最小币值，CNY/USD/EUR为100进制，JPY为整数；API无浮点，数据库 INTEGER。Finance无Resource，不进入普通搜索/AI/Activity/ZIP。所有私有端点仍为单人本机会话范围，不引入多用户ACL。

## 运动 `/exercise`

- `GET /month?month=&trash=false`：按日期计划/实际记录，实际月统计；每种列表最多1000项，超限明确拒绝，不静默截断。
- `POST /plans`、`PUT /plans/{id}`：title、local_date、kind(run/walk/strength/cycle/other)、duration_seconds(0..604800)、distance_meters(0..1000000)、task_id/event_id可空、cancelled、expected_version。
- `POST /records`、`PUT /records/{id}`：local_date、kind、duration_seconds(1..604800)、distance_meters、memo、plan_id可空、expected_version。整数单位，不推测卡路里或健康结论。
- `POST /plans|records/{id}/trash|restore`：expected_version；单独 `POST /export` 与普通ZIP均包括运动（隐藏不可见Task/Event引用），完整备份包括全部事实。

计划与原任务/日程之间仅可见引用，不修改其状态；记录删除/计划取消各自独立。实际记录可不关联计划。计划关联日期与原日程时间由用户维护，系统不暗中同步覆盖。

## 情报 `/intelligence`

- `GET /sources` 返回登记来源和明确未接入状态；`POST /sources`、`PUT /sources/{id}`：kind、name、endpoint、enabled(default false)、expected_version。RSS任意公开HTTPS，arXiv默认查询，HN/HF固定接口；kind不可改。
- `POST /sources/{id}/collect`：UUID key，显式手动一批，120秒上限；60秒冷却，同一时刻仅一个活动采集。失败/响应丢失只原key核对，返回run状态；不自动重试/翻页/定时。
- `POST /legacy/import`：UUID key，原intel.db只读一致快照四表导入；全字段JSON+stable_id+批次digest。最多500000行/128MiB序列化数据；不运行旧init或迁移器。
- `GET /items?q=&source=&domain=&state=&favorite=&subscribed=&limit=&offset=`。文本字面查询，跨来源同目标不删除各自事实。
- `GET /items/{id}`：资料、最近100个原始快照、七天基线趋势、相同目标关联。`PUT /items/{id}`：domain、favorite、read_state(new/later/read)、expected_version；采集不覆盖人工整理字段。
- `GET /subscriptions`、`POST /subscriptions`、`PUT /subscriptions/{id}`：keyword、enabled、expected_version；标题/摘要不区分大小写字面命中。
- `GET /runs`：最近100个状态（无完整载荷）；`GET /weekly`：最近7天更新且收藏/稍后读/命中订阅，最多100条人工Markdown模板，暂存在页面，可复制至Note；不调用AI、不发布。

载荷：RSS完整原始字节base64+SHA256信封保存在run.payload_text，entry原始XML保存在快照；HN保存top IDs和逐项原始对象，HF完整响应文本与逐项对象；下载响应≤2MiB，存储信封≤3MiB。旧原始API JSON未知时为null、raw_available=0；原DB逐行字段在legacy表，run_id NULL保留在旧原行不强造FK。快照不可覆盖；Collector失败不改旧成功事实。普通ZIP不含整个新情报库，完整v3快照包含其全部表。

## AI `/assistant`

- `GET /config`：endpoint/model/enabled（有效状态）/version/has_token/needs_rebind；无token值。`PUT /config`：endpoint完整HTTPS无query/fragment、model、enabled、token|null、clear_token、expected_version。留空保留，切换地址必须新密钥；CAS失败后重读，无网络请求。旧密钥不可变文件保留，孤儿不自动清理。
- `POST /preview`：question(≤4000)、resource_ids[]、intelligence_ids[]，合计≤8；普通Resource允许note/document/book/bookmark/course/learning_*节点与recipe；情报显式选择title/URL/source/summary。拒绝vault/finance/Task/Event/Project路径等。返回实际发送问题+标题+正文+截断提示、版本/hash、endpoint/model/config_version、五分钟id+digest；此步不出网。
- `POST /confirm`：preview_id+digest+UUID key；持久一次性预约后立即返回run id/status并后台发一次API。不能用另一个key复用preview；重复相同key查询现状，不再收费发送。没有自动重试、重定向、代理或本地模型部署。
- `GET /runs/{id}`：仅原会话；运行状态、临时回答、来源清单。每次重验当前隐私/版本/hash/配置/预览时效，失效隐藏。回答纯文本显示，不执行HTML/命令，不保证引用正确。
- `POST /runs/{id}/cancel`：停本地任务、清临时回答；不能撤销已经发送的内容或保证远端不计费。服务重启starting→unknown，不重新发送。登出取消/清除本会话结果。

协议为JSON `model,messages,stream:false,max_tokens:1200`，读取 `choices[0].message.content`；响应≤256KiB/文本≤64000字符，总网络45秒，无流式/工具调用。不是所有服务商通用协议；不支持时明确报错，由用户选择兼容API，不能暗中云回退。密钥从本机0600文件读取，不进Prompt/DB运行表/响应/日志/备份。外网仅443公开HTTPS，DNS解析拒绝内网地址并固定连接IP，TLS保留原域名验证；无真实服务配置前仅模拟验证。
