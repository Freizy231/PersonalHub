# v0.10 项目专业工具 API

2026-11-12｜P3-02｜前缀`/api/v1/project-tools`。全部接口要求本机会话；写接口额外受Origin/CSRF限制。只开放固定适配器与服务端安装候选，不接受任意路径/命令/参数/环境/prompt；AI改造系统仍未实现。

## 安装发现与配置

| 路由 | 契约 |
| --- | --- |
| GET `''` | 返回items（已登记配置）、installations（当前可用安装）、adapters（含未安装的类型）。纯读取，不注册/安装/执行 |
| POST `''` | name 1～100、adapter terminal/vscode/codex、installation_id为目录GET给出的64字符指纹，expected_version默认0；UUID Idempotency-Key必填。每adapter最多一配置，返回公共profile |
| PUT `/{profile_id}` | 同字段+当前expected_version；同类型重命名/重绑安装，递增version，保留enabled；不能改adapter。要求幂等键 |
| POST `/{profile_id}/state` | expected_version≥1、严格布尔enabled；要求幂等键。启停也递增版本，即使原程序已缺失仍可停用 |
| GET `/history` | 可选project_id UUID；limit默认30、1～100；offset默认0、≤1000000。返回当前可见项目的items/total/limit/offset，按created_at/id倒序 |

profile公共字段：id/name/adapter/installation_id/enabled/version/created_at/updated_at；列表额外available。identity_json为内部程序身份，不对外开放编辑；程序更新、安装路径/symlink变化后available=false，需要用户选择新安装显式重新保存，不在GET重绑。

固定候选：terminal依赖/usr/bin/gnome-terminal + /usr/bin/bash；vscode选择/usr/bin/code或/usr/local/bin/code，或/usr/bin/snap + /snap/code/current/usr/share/code/code的固定Snap适配器；codex依赖GNOME Terminal和当前用户~/.local/bin/codex或/usr/local/bin/codex或/usr/bin/codex。不存在/权限异常的候选不出现，不下载或执行help探测。这里只支持已列适配器，其他终端/编辑器以后单独加服务端适配，不提供“自定义shell”。

程序身份绑定source/resolved路径与dev/ino/size/mtime_ns/ctime_ns/mode/uid/gid。symlink只允许出现在固定已知安装候选中，解析目标及其父目录必须符合权限要求；readonly系统挂载允许root UID映射和Snap模式位，普通可写文件/目录拒绝其他用户所有或组/其他用户可写。项目目录本身逐组件NOFOLLOW，根目录、相对路径、回退和控制字符等按Project既有校验拒绝。

## 预览与一次性确认

| 路由 | 契约 |
| --- | --- |
| POST `/preview` | project_id/profile_id UUID、expected_project_version≥1、UUID Idempotency-Key；验证未删personal未归档项目、已启用且身份一致程序和目录；返回60秒预览 |
| POST `/confirm/{preview_id}` | 严格JSON `{ "confirmed": true }`，拒绝false/1/字符串；preview_id就是一次性执行身份，不需另一个幂等键；返回启动历史项 |

预览返回id/project_id/title/profile_name/adapter/arguments（数组）/directory/expires_at/warning。绑定Project版本、profile版本、程序身份、目录路径及全部父组件dev/inode；改变后拒绝。相同预览请求key/body返回原预览，过期也不自动刷新TTL，需要明确新请求。配置保存/启停幂等重试返回当前配置，不重复创建或切换。

参数固定：

- Terminal：gnome-terminal --window --working-directory=项目目录 -- bash --noprofile --norc。打开干净交互Shell，不读Bash启动脚本，不拼接`-c`命令。
- VS Code：固定本机Code或snap run code，参数--new-window --disable-extensions -- 项目目录；不关闭工作区信任，不自动执行任务。禁用扩展影响已有扩展能力是本版明确取舍。
- Codex：GNOME Terminal内启动已登记Codex，固定--cd 项目目录 --sandbox read-only --ask-for-approval on-request --no-daemon；无初始prompt、exec、自动批准或绕过信任标志。程序仍可能读取自身配置/账户；Hub不读取/传入用户API Token，不代发模型任务。

确认在短事务内先插入UNIQUE(preview_id)的starting记录并消费预览，再于事务外Popen(shell=False、参数数组、DEVNULL、独立会话、白名单环境)。当前目录由持有的NOFOLLOW目录fd经/proc/self/fd与pass_fds交给子进程；启动前再校验路径/程序身份。避免继承GITHUB_TOKEN、API Key、LD_PRELOAD、BASH_ENV、PYTHONPATH等；仅保留桌面会话/语言/HOME等必要项及固定PATH。原目录字符串仍会交给专业应用，外部程序后续路径访问/配置行为由它负责，不宣称完整TOCTOU隔离或恶意代码沙箱。

历史status：starting（内部预约）、started（请求已发起，并非GUI打开证明）、failed（本机无法启动或短时非0退出）、uncertain。API将starting投影为uncertain；崩溃/记录结果失败不自动重执行。重复确认先按当前项目隐私授权，再返回已有历史，过期/已停用/已归档也不再次执行；并发只有首个预约者能Popen。网络丢失后保留原preview_id重试/查历史，不能盲目生成另一个预览启动；用户新预览的再次打开是新的明确操作。

历史项含id/preview_id/project_id/profile_id/status/error_code/created_at/updated_at/title/profile_name/adapter；名称是当前项目/配置名称，不是假装保存的历史名称快照。私密/已删项目不出列表/总数/单次重放，普通归档项目历史可查。归档阻止新预览及尚未预约的确认，已预约操作按预约时授权处理。

## 错误与恢复

404：RESOURCE_NOT_FOUND/TOOL_PROFILE_NOT_FOUND/TOOL_PREVIEW_NOT_FOUND。409：VERSION_CONFLICT/IDEMPOTENCY_CONFLICT/TOOL_INSTALLATION_CHANGED/TOOL_PROFILE_EXISTS/TOOL_PROFILE_DISABLED/TOOL_PREVIEW_EXPIRED/TOOL_PREVIEW_CHANGED/PROJECT_ARCHIVED。目录/权限等422：PROJECT_DIRECTORY_REQUIRED/PROJECT_DIRECTORY_UNAVAILABLE/TOOL_UNSAFE_PATH/TOOL_UNAVAILABLE/TOOL_ADAPTER_IMMUTABLE。DTO错误422，缺会话401/来源或CSRF403；未知schema503拒写。历史error_code仅受控TOOL_START_FAILED/TOOL_EXITED，不存原始异常/输出。

完整snapshot v3保存配置/预览/历史，恢复不会执行；已预约预览恢复后只读已有状态。换机身份不同须重绑。机器相关表不纳入普通ZIP（包含它们的幂等操作记录也不在普通ZIP），ZIP仍是个人知识导出。首次部署无预注册配置，活动根不写演示/启动记录。备份不包含外部应用/源码/外部CLI凭据或模型。
