# 剩余安全风险验证记录

日期：2026-11-09（Asia/Shanghai）。范围：本地工作区当前源码，使用临时 SQLite 数据库、实际登录生成的 Cookie 和 FastAPI HTTP 请求测试；未访问生产数据，也未发送邮件或连接真实 Wiki。

## 结论

复现并修复了 4 个权限／信息保护问题，以及 1 个排班数据完整性问题。下述检查通过不代表所有业务逻辑或部署环境均已证明安全。

## 中等：已修复

### SEC-01：管理员改密后旧管理会话仍有效

- 规则：FASTAPI-AUTH-001，会话撤销。
- 位置：`server/app.py:159`、`server/app.py:556`；`server/member_store.py:112`。
- 证据：原管理 Cookie 仅签名用户名，管理会话回退仅检查 `admin_ok` 和用户名。真实管理员登录后重置密码，再请求日志接口仍得到 200。
- 影响：已持有旧管理员会话的人在改密后仍可继续操作，直至 Cookie 到期。
- 修复：管理 Cookie 和管理会话均绑定密码版本，每次管理员鉴权读取当前状态，不使用身份缓存；旧版本凭证失效。
- 验证：`test_admin_password_reset_revokes_old_admin_session`：改密后旧会话返回 401。
- 部署影响：原有不带版本的管理会话也失效，管理员需要重新登录。

### SEC-02：待确认外勤接口可以删除已确认记录

- 规则：FASTAPI-AUTHZ-001，对象状态授权。
- 位置：`server/app.py` 的 `member_field_reject`；`server/field_duty.py:96`。
- 证据：原接口调用管理员覆盖函数 `set_participation(username, False)`；真实成员可通过 `/pending/{username}` 删除已确认记录，原测试返回 200。
- 影响：只有“外勤确认”权限的成员可以绕过待确认状态，撤销已经确认的记录。
- 修复：新增 `reject_pending`，通过带 `status=PENDING` 条件的 DELETE 原子限制操作范围，其他状态返回 404。
- 验证：待确认记录可退回，已确认记录不可删除；撤销权限后 POST／DELETE 均返回 403。
- 边界：现有权限设计明确允许持有 `field_confirm` 的成员确认其他人的外勤；这一行为本身不计作越权。

### SEC-03：数据控制台泄露模型和 SMTP 凭据

- 规则：FASTAPI-RESP-001，敏感字段过滤。
- 位置：`server/data_admin.py:115`、`server/data_admin.py:325`。
- 证据：原设置脱敏只匹配 `mcp/qq/oauth/password/secret/key` 等关键词，漏掉 `model`、`smtp` 整段配置；只有数据权限的管理员可以从预览响应读到其中的测试密钥和密码。
- 影响：数据查看权限可以读取本应由专用配置入口保护的外部服务凭据。
- 修复：补充模型、SMTP 配置脱敏，并禁止通过数据编辑接口覆盖包含凭据的设置。
- 验证：HTTP 预览不包含测试凭据；直接修改含凭据的设置返回 400，原配置保持不变。

### SEC-04：特殊排班权限可以修改或删除普通班次

- 规则：FASTAPI-AUTHZ-001，对象类型授权。
- 位置：`server/app.py:1180`、`server/app.py:1191`；`server/schedule_store.py:115`、`server/schedule_store.py:147`。
- 证据：原特殊排班更新和删除接口只校验页面权限，没有检查目标班次类型。仅授予 `special` 的真实管理员可把普通班次 ID 传入并修改其人员，原测试返回 200。
- 影响：特殊排班操作权限能够越界修改普通排班。
- 修复：特殊排班接口传入 `expected_special=True`；更新在事务中验证已有记录类型，删除语句包含类型条件。
- 验证：针对普通班次的 PUT／DELETE 返回 404，人员分配不变。

## 低：已修复

### SEC-05：发布跳过冲突时仍会删除现有班次

- 规则：业务状态一致性。
- 位置：`server/weekly_store.py:100`；`tests/test_weekly.py:163`。
- 证据：原发布循环边检查冲突边删除未签到班次，随后遇到已签到冲突时跳过；回归用例中一次跳过发布导致原有未签到班次丢失。
- 影响：合法排班发布可能意外删除班次和人员安排；这不是匿名代码执行漏洞。
- 修复：收集待替换 ID，全部冲突检查通过后才执行删除。
- 验证：发生冲突时发布结果为跳过，已有已签到和未签到班次均保留。

## 已验证的其他边界

- 自动枚举 77 个受保护管理员操作，匿名和普通成员会话全部被拒绝；只授予日志权限的管理员不能调用其他受保护管理操作。包含数据库预览、修改、单条／批量删除接口。
- 普通成员即使访问秘密管理路径，仍不能登录管理端或读取数据控制台。
- 使用真实成员会话验证 MCP：未配置管理员地址时不能通过请求地址触发连接；已配置时只能使用管理员地址。连接调用通过 mock 截获，未向内网目标发送请求。
- 签到使用会话身份：在请求体伪造其他成员姓名不能替其签到，目标记录保持未签到。
- 个人改密必须验证旧密码，只修改当前会话所属账号；请求体中的其他用户名不会改变目标。改密后旧成员会话失效，当前会话可以继续使用。
- CSV 使用文本解析和 textarea 的 `value`，未发现 `eval`、pickle、对象构造式反序列化执行路径。脚本、公式和原型属性样式文本保持普通姓名数据；服务端拒绝对象类型姓名，SQL 注入样式姓名不会改变数据库结构。未以此宣称整个前端均不存在 XSS。
- 现有排班、签到时间窗口、连班、调班和发布事务相关测试纳入回归。

## 保留的策略与验证边界

- 管理员失败 8 次后限制 10 分钟（按来源与账号双计数）；成员端密码登录按来源 IP 独立计数，失败 50 次后暂停 5 分钟，两套计数互不影响。此前一版曾取消成员限流，已恢复。
- **已知未修改的密码策略风险**：`server/member_store.py:4` 仍使用公开固定初始密码 `gg2017`，批量创建和重置账号沿用此密码，没有强制首次改密。未改密账号仍存在被猜中密码的风险，不能将其描述为已消除。
- 默认会员组包含 `field_confirm`，可以跨成员确认／退回待确认登记，代码也未禁止自我确认。这是当前权限策略；未擅自改变默认权限或引入双人审批。
- 管理员可以配置可信内网 Wiki；没有封禁所有私网地址。本次验证的是普通请求无法选择目标和利用重定向扩大访问范围，不是管理员配置错误、DNS 或部署出口策略的全面验证。
- 不包含部署环境的外部黑盒复扫、MySQL 实库验证、浏览器端完整渗透测试、并发压力或所有业务组合穷举。
- 修复没有发布到运行中的服务；需要部署并重启后再用扫描器复测。

## 测试入口

最终结果：Python 共 301 项，290 项通过、11 项 MySQL 集成测试按配置跳过；Node 共 35 项，全部通过。语法编译和 `git diff --check` 通过。Python 测试另输出了一条 SQLite 连接未关闭的 ResourceWarning，未导致测试失败，未在本轮扩展处理连接生命周期。

```sh
RUN_MYSQL_TESTS=0 DB_BACKEND=sqlite .venv/bin/python -m unittest discover -s tests
node --test tests/*.cjs
git diff --check
```

新增边界测试：`tests/test_security_boundaries.py`；CSV 恶意文本回归：`tests/test_weekly_import.cjs`；发布冲突回归：`tests/test_weekly.py`。

## 2026-11-10 复核：SSO、会话租约与数据控制台

针对本轮新增功能（GDUTNIC 单点登录、服务端登录租约、打卡网络规则）做了一轮独立复核，发现并修复 6 个问题，另有 1 项判断为既有设计不做修改。

## 严重：已修复

### SEC-06：数据控制台改写启动标记可重新打开管理员注册

- 规则：FASTAPI-AUTH-001，引导流程保护。
- 位置：`server/app.py` 的 `admin_register`；`server/data_admin.py` 的 `settings` 表定义与 `update_row`。
- 证据：原 `admin_register` 只检查 `admin_store.has_user()`，它读取数据库里的 `setup_done`。数据控制台把 `settings` 表整体设为可写，只有「密钥类」键被拦截，`setup_done` 不在其中，因此持有数据权限的账号可以清空该值，再调用注册接口拿到完整的原始管理员会话，全程不需要原始管理员密码。
- 修复：注册接口改为同时要求原始管理员凭据文件不存在；数据控制台只允许写白名单内的设置键（当前仅 `chat_max_mb`），其余设置一律走各自专用入口。
- 验证：`test_data_console_cannot_reopen_registration`，改写被拒绝；即使删除该行，注册接口仍返回 409。

### SEC-07：SSO 自动匹配可以让管理员权限被接管

- 规则：FASTAPI-AUTHZ-001，账号绑定。
- 位置：`server/keycloak.py` 的回调自动匹配分支。
- 证据：配置中的自动匹配字段只排除了原始管理员，没有排除普通管理员账号。只持有「SSO 设置」权限的人可以把匹配字段指向一个管理员用户名，再用自己的 SSO 身份登录，直接以该管理员身份进入管理端，不需要任何本地密码。
- 修复：自动匹配只作用于不持有任何管理员权限的账号；管理员账号与原始管理员都必须先验证本地密码，也可以由管理员在「用户管理」中显式解绑。
- 验证：`test_automatic_matching_never_links_a_privileged_account`、`test_automatic_matching_never_links_the_root_account`。

## 中等：已修复

### SEC-08：成员端密码登录没有限流

- 规则：FASTAPI-AUTH-002，暴力破解防护。
- 位置：`server/app.py` 的 `member_login`。
- 证据：本轮改动删除了 `MEMBER_LIMITER`，成员登录变成不设上限的密码猜测接口；管理端的双计数限流不覆盖这条路径。
- 修复：恢复按来源 IP 的成员登录限流。按使用方要求放宽为同一来源失败 50 次后暂停 5 分钟（共享值班设备上容易输错），与管理员计数独立。
- 验证：`test_member_login_is_throttled_per_source` 与 `test_member_login_has_its_own_throttle_separate_from_admin_failures`。

### SEC-09：设置页任一卡片可读取 LLM 与 QQ 凭据

- 规则：FASTAPI-RESP-001，敏感字段过滤。
- 位置：`server/admin_store.py` 的 `model_public`、`qq_public`。
- 证据：`/api/admin/model` 与 `/api/admin/qq` 直接回显明文 API Key 与 QQ AppSecret，而「其他设置」页面由「值班时间」「系统时间」「SMTP」中的任意一项打开。只持有 SMTP 设置权限的账号即可读到这两组凭据。
- 修复：读取接口只返回 `••••••` 与「是否已设置」；保存时掩码值表示保留原值，按提供方保存的密钥同样处理。
- 验证：`test_settings_cards_do_not_expose_other_cards_credentials`。

### SEC-10：数据控制台可删除审计日志

- 规则：审计完整性。
- 位置：`server/data_admin.py` 的 `operation_log` 表定义。
- 证据：该表原为「不可编辑但可删除」，持有数据权限的账号可以清除自己留下或即将留下的操作记录。
- 修复：`operation_log` 设为不可删除，预览行也标记为不可删除。
- 验证：`test_data_console_cannot_erase_the_audit_trail`。

### SEC-11：数据控制台可停用或删除管理员账号

- 规则：FASTAPI-AUTHZ-001，对象类型授权。
- 位置：`server/data_admin.py` 的 `member_accounts` 变更路径。
- 证据：账号表允许改 `active` 并允许删除，只做了原始管理员排除。持有数据权限但没有「用户管理」权限的账号可以停用或删除管理员，进而阻止其登录，日志与审计也只按数据权限记录。
- 修复：新增管理员账号保护，没有「用户管理」权限时，数据控制台不能修改或删除持有管理员权限的账号；操作者本人与拥有「用户管理」权限的账号不受影响。预览中的这些行标记为不可删除。
- 验证：`test_data_console_cannot_disable_an_administrator`。

## 低：已修复

### SEC-12：`/api/admin/qq` 因装饰器误用必然 500

- 位置：`server/app.py` 的 `_qq_bot`。
- 证据：一个同步函数被 `@asynccontextmanager` 装饰，调用方拿到上下文管理器而不是模块对象，`public_status` 属性不存在。该接口从未成功返回过，错误处理不影响会话，但属于实际故障。
- 修复：移除误加的装饰器，保留延迟导入。
- 验证：包含在 SEC-09 的测试中。

## 复核后未修改的项

- **「查看概览」权限可以新增、修改、删除单日班次与整周排班**：这是既有产品设计，概览页本身就提供「新增班次」和「删除整周排班」。本轮沿用该设计，未拆分新的写权限；如需收紧，应作为独立功能变更处理，并同步调整权限树与界面。

## 测试入口（2026-11-10）

最终结果：Python 共 362 项，350 项通过、12 项 MySQL 集成测试按环境跳过；Node 共 50 项全部通过。

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -t tests
node --test tests/'*.cjs'
```

## 2026-11-10 复核第二批：数据控制台盲区、坏数据回退与前端重试

上一轮修复后的独立复核又发现 5 个问题，均已修复。复核脚本的自动校验阶段因脚本缺陷没有执行，以下结论均为逐条人工核对源码与实测。

### 高：已修复

#### SEC-13：仅持数据权限可重新启用或删除已停用的管理员

- 规则：FASTAPI-AUTHZ-001，对象状态授权。
- 位置：`server/data_admin.py` 的 `_privileged`。
- 证据：SEC-11 的守卫调用 `permissions.is_admin`，而该函数对停用账号返回空权限集。管理员被「用户管理」停用后，仅持数据权限的账号即可把 `active` 改回 1 或直接删除该行，绕过 SEC-11。
- 修复：守卫改为直接读取存储的权限组，忽略 `active`；原始管理员同样视为受保护。
- 验证：`test_data_console_cannot_touch_a_disabled_administrator_either`。

### 中：已修复

#### SEC-14：打卡规则数据损坏会让成员端接口全部 500

- 规则：可用性 / 失败模式。
- 位置：`server/checkin_access.py` 的 `load`。
- 证据：`load` 直接 `normalize` 存储值，任何非预期结构都抛 `ValueError`。`/api/network-access`、`/api/member/status`、`/api/member/schedules`、`/api/member/field-duty` 都读取它，于是一行坏数据会让所有成员无法签到、前端按钮永久置灰。
- 修复：解析失败时回退为「不限制」，并在管理端重新保存即可修复；不再向下抛出。
- 验证：`test_corrupt_checkin_rules_do_not_break_member_pages`。

#### SEC-15：设置卡片读取失败后控件永久禁用

- 规则：可用性。
- 位置：`web/checkin-access.js`、`web/sso-settings.js`。
- 证据：启用控件后只在成功分支重新启用，请求失败时表单保持 disabled，除切换标签或刷新外无法恢复，管理员也就无法重新保存来修复 SEC-14 的坏数据。
- 修复：在 `finally` 中恢复控件。
- 验证：随 SEC-14 的接口用例人工复核。

#### SEC-16：考勤长轮询失败后固定 2 秒重试

- 规则：可用性 / 自伤型压力。
- 位置：`web/attendance-live.js` 的 `poll`。
- 证据：除 401 外的任何失败都按 2 秒重试，且 `onAttendance` 抛错同样如此。接口异常时每个打开的标签页都会持续高频请求；后端 502 时问题被放大。
- 修复：真正发出请求且失败时按 2→30 秒指数退避，成功后复位；未在看的页面不发请求，保持原有短间隔以便切回即恢复。
- 验证：`tests/test_attendance_live.cjs` 的「a failing long poll backs off…」。

## 第二批复核后未修改的项

- **`/api/chat/title` 没有登录校验**：属于未启用的旧版 Wiki 助手（`web/app.js` 不在任何页面加载，`/api/chat` 本身要登录），且该路由在基准快照中即如此。真正启用该界面时应统一走 `_require_mcp_user`；本轮不扩大改动范围。
- **`/api/defaults` 向未登录访客返回管理员配置的 MCP 地址**：同上，属于未启用功能，且本轮已改为只读取管理员配置、不再接受会话中的旧地址。按现有部署不构成对外暴露。
- **`activity_at` 由客户端提供**：这是设计取舍——浏览器只能报告「用户确实操作过」的时刻，服务端校验格式、有限窗口（60 秒内）且不允许回退，另有无响应当心跳兜底。无论是否可被脚本伪造，都无法让账号获得超出既有权限的能力，因此保留。
