# 饮食 API：v0.11 / P3-03

前缀 `/api/v1/diet`。沿用本机会话；所有写需可信Origin、CSRF和UUID `Idempotency-Key`，路径UUID由API验证。GET无写入、扣库存、外部请求或自动建清单。JSON字段extra=forbid；错误不回显输入。409/422不自动修正，保留草稿重新读取。

## 数量、日期、版本

`quantity`必须十进制字符串（不接JSON浮点、负数、指数、NaN），至多三位小数。食谱/库存最大1,000,000提交单位，食谱和购物量必须>0，盘点允许0。购物量（含聚合后手改）最大1,000,000,000提交单位。固定枚举g/kg/ml/l/piece/portion；库存和建议只用g/ml/piece/portion。数量SQLite存千分INTEGER。份数为1～100整数，配方每行按比例向上取整到0.001规范单位，再聚合；kg→g、l→ml，无跨维度换算。

`local_date`为本地DATE，`week_start`必须周一；支持完整周的日期，不接受9999年最后不完整周。CT/UT为UTC微秒Z。创建`expected_version=0`，更新为当前版本；Shopping使用整周清单版本，库存按食材+规范单位版本，其余按当前实体/Resource版本。每次成功修改（即使相同值）版本递增；重复key只返回当前事实，不重复作用。key跨操作/不同内容复用409。

## 路由

| Method / path | 输入 / 输出 |
| --- | --- |
| GET `/ingredients` | q≤80，limit1..100默认50，offset0..10000；items/total/limit/offset |
| POST `/ingredients` | name、expected_version0；返回id/name/version/CT/UT |
| PUT `/ingredients/{id}` | name、expected_version；规范名NFKC/折空白/casefold唯一，冲突不自动合并 |
| GET `/recipes` | q≤200、favorite、trash、limit默认30最大100、offset≤1000000；普通可见食谱分页 |
| GET `/recipes/{id}` | Resource字段+instructions/base_servings/favorite/ingredients（含id/ingredient_id/name/quantity/unit/position） |
| POST `/recipes` | title≤200、instructions≤20000、base_servings默认1、favorite默认false、ingredients≤100、expected_version0 |
| PUT `/recipes/{id}` | 同上，expected_version为Resource版本；食材全量替换同事务，兼容单位的重复食材行拒绝 |
| POST `/recipes/{id}/trash`、`/restore` | expected_version；软删除/恢复，绝不删除食材、菜单、清单和库存 |
| GET `/meals?week_start=DATE` | items为该周当前可见食谱对应的有效菜单，固定7日、最多每天30道 |
| GET `/today?local_date=DATE` | 当日三餐投影，仅前端本地日期，不创建今日菜单 |
| POST `/meals` | local_date/slot/recipe_id/servings/note≤1000/expected_version0；slot=breakfast/lunch/dinner |
| PUT `/meals/{id}` | 同上，expected_version为该安排版本；允许同餐多道食谱 |
| POST `/meals/{id}/remove` | expected_version；软移除，重复同key可核对，不开放物理删除 |
| GET `/shopping?week_start=DATE` | id（可null）、week_start、version（无记录0）、CT/UT、items（含已移除，最多2000） |
| POST `/shopping/{week}` | ingredient_id/quantity/unit/note/purchased/expected_version，手动新增 |
| PUT `/shopping/{week}/{item}` | 同上，编辑当前有效项目；保留origin/source_digest快照出处 |
| POST `/shopping/{week}/{item}/state` | expected_version、removed严格bool；移除/恢复，不物理删除 |
| GET `/suggestions?week_start=DATE` | version/digest/items/policy；每行ingredient_id/name/unit/required/stock/confirmed/quantity和目录/库存版本 |
| POST `/suggestions/{week}/confirm` | expected_version、digest、selected数组（1..500个唯一ingredient_id+规范unit）；只追加勾选且缺口>0的建议 |
| GET `/stock` | q≤80、limit默认50最大100、offset≤10000；items/total/limit/offset，包含0余额 |
| PUT `/stock` | ingredient_id/quantity/规范unit/expected_version；明确绝对余额盘点，不累加/自动消费 |

食材目录最多10000项，无自动删除/合并。购物项目最多每周2000项（含移除历史）；全量有界返回，UI有效项目每20项分页。建议超过500种食材与单位组合拒绝并提示精简/手工清单，不截断造成隐性遗漏。无食材食谱可安排（如外食），不产生采购需求。

## 建议与确认

需求按当前周有效菜单和当前personal未删食谱计算。`quantity=max(required-stock-confirmed,0)`；confirmed含已买和未买的有效购物项，两者都不自动改变库存。已移除不计入。当前库存不跨周预留；若把采购量手动计入库存，必须人工核对已买项目与后续需求，系统不推断库存来源。

预览摘要包含周、菜单/食谱版本、名称与库存版本、需求/已确认量及清单版本。确认在BEGIN IMMEDIATE中重新计算并核对摘要和清单版本，再原子追加全部选中项目。变更返回DIET_PREVIEW_STALE，不自动重算后替用户确认。重复同key不会重复追加；不同key竞争仅一方成功。预览不创建持久表/锁库存。

后续菜单改动只改变建议，不覆盖用户确认的数量/备注/已买/移除状态。购物与库存是普通人工事实，不因来源食谱隐藏而消失。普通食谱的隐私变更会过滤菜单/需求/搜索/资源重放；食材目录和先前人工确认清单为独立普通资料，不能用来保存敏感饮食信息。

## 错误与客户端恢复

DIET_PREVIEW_STALE、VERSION_CONFLICT、IDEMPOTENCY_CONFLICT、INGREDIENT_NAME_EXISTS、DIET_STATE_CONFLICT为409；不存在实体404；数量/日期/容量422。GET无清单返回version0而非写入。网络/5xx结果不确定时保留原key/path/body，只提供原请求重试；4xx保留草稿并要求核对，不盲重试。成功后读列表失败仍明确提示读取失败，不能把原写当失败重复创建。
