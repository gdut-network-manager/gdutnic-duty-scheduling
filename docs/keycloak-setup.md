
> 本应用不自带任何身份提供方地址：`.env.example` 与后台输入框默认都是空的，先填写你自己部署的 Keycloak 地址才能启用 SSO。下文出现的地址只是本协会部署的示例。

## 已确认的公开信息（本协会部署，仅供参考）



- issuer
- 支持 Authorization Code、PKCE S256、UserInfo。
- 声明支持 `preferred_username`、`name`、`given_name`、`family_name` 等字段。

声明支持某字段不代表当前 Client 一定会收到该字段；真实用户名需要注册本应用的 Client 并完成一次交互登录后确认。未使用管理控制台的内置 Client 代替业务 Client。

## Keycloak 端需要的设置

在 `nic` Realm 创建专用 OpenID Connect Client
1. 开启 Standard flow；使用 Authorization Code + PKCE S256。
2. 可以使用 public Client；也支持开启 Client authentication 的 confidential Client，使用 `client_secret_post`。
3. Valid redirect URIs 填写精确地址：`https://排班系统域名/api/auth/keycloak/callback`。不要使用通配符。
4. 为本应用分配 `profile` scope，确认 `preferred_username` 和需要匹配本地姓名的字段映射到 ID Token 或 UserInfo。
5. 本应用只在服务器请求 token、JWKS、UserInfo，不要求开放跨域浏览器接口。

## 应用端配置（推荐在管理员后台完成）

原始管理员登录后，进入“其他设置 → SSO 设置”。其他管理员需先由权限组授予独立的“SSO 设置”权限。

1. 填写身份提供方地址：`https://你的Keycloak域名/realms/你的Realm`
2. 填写本应用的客户端标识，选择公开客户端或机密客户端；机密客户端需填写密钥。
3. 填写精确的登录回调地址：`https://排班系统域名/api/auth/keycloak/callback`，并在 Keycloak 配置相同的有效重定向 URI。
4. 首次接入将自动匹配字段保持为“不自动匹配”，实际登录后再确认字段。也支持自定义组织维护的字段。
5. 可以先保存未启用的配置草稿；“测试服务连接”只验证服务发现，不保存配置或自动启用，也不能替代真实客户端登录。
6. 打开“在登录页显示 SSO”，保存完整配置后立即生效，无需重启。关闭并保存后，两个登录页都隐藏 SSO 入口。

SSO 默认关闭，未配置时不显示按钮；身份提供方地址默认留空，必须自己填写。客户端密钥只保存在服务器数据库，不通过读取接口回显；编辑时留空保持原密钥，也可以明确清除。公开客户端不保存密钥。含凭据的 SSO 配置在数据控制台中脱敏且禁止直接改写。

后台配置优先于环境配置。旧部署或自动化部署也可使用环境变量作为初始值，但必须明确设置 `KEYCLOAK_ENABLED=true` 才启用：

```dotenv
KEYCLOAK_ENABLED=false
KEYCLOAK_ISSUER=
KEYCLOAK_CLIENT_ID=
KEYCLOAK_CLIENT_SECRET=
KEYCLOAK_REDIRECT_URI=
KEYCLOAK_USERNAME_CLAIM=
```

更改环境变量需要重启；在后台保存过配置后，数据库配置始终优先。后台保存会取消未完成的 SSO 登录／绑定事务，用户需要重新开始。HTTPS 部署设置 `SESSION_HTTPS_ONLY=true`。本机测试可使用精确的 `http://127.0.0.1:端口/api/auth/keycloak/callback`，并在 Keycloak 单独允许这一地址。

## 用户流程

- 成员和管理员登录页均在原登录按钮左侧显示“通过GDUTNIC”。管理员入口仍要求原有管理入口校验。
- 已绑定身份直接登录原本地账号；账号权限、停用状态仍由本应用决定，不从 SSO 的角色字段导入管理员权限。
- 用户名字段尚未确认时，保持后台自动匹配字段为“不自动匹配”，首次登录进入本地账号绑定页。
- 输入本地用户名和密码，验证成功后显示：`是否将 <SSO的用户名> 绑定到 <本地用户名> ？`。
- 只有点击“确认绑定”才保存绑定并登录；取消提示框不会创建绑定。
- 读取不到用户名，但已验证到合法的 SSO `sub` 时，可以手动绑定；如果身份验证失败或缺少 `sub`，则拒绝登录，不能绑定未知身份。
- 原始管理员可从管理入口手动绑定，首次绑定始终验证本地密码，不凭同名自动绑定。

## 确认自动匹配字段

完成一次 SSO 登录并停留在绑定页后，在同一浏览器访问 `/api/auth/keycloak/binding`。该接口仅返回当前待绑定身份的几个用户名相关字段，不返回 ID Token、Access Token 或密钥；不要对外转发会话 Cookie。

确认哪个**由组织管理、用户不可任意修改**的字段与本地账号姓名精确一致后，再在后台选择“登录用户名（preferred_username）”（或已验证的自定义字段）并保存。仅活跃、未绑定、符合当前成员／管理员入口权限的同名本地账号可以首次自动绑定。不要使用可由用户自行编辑的昵称字段来自动关联有权限的本地账号；如果无法保证字段可信，保持手动密码绑定。

## 身份与安全边界

绑定关系使用 issuer + sub，而非可改名的显示名称。一个本地账号只能绑定一个 SSO 身份，一个 SSO 身份只能绑定一个本地账号，冲突不会静默覆盖。原账号删除重建后不会继承旧绑定；密码修改不解除绑定。持有用户管理权限的管理员可在用户管理中对已绑定账号点击“解绑SSO”。解除后会撤销该账号的 SSO 登录，保留密码登录；即使已启用同名匹配，重新绑定也必须验证本地密码。旧版未标记登录方式的会话会在本次数据库升级时失效，需要重新登录。登录按钮使用组织现有图标。

协议验证包括 state、nonce、PKCE、ID Token 非对称签名、issuer、audience、有效期及 UserInfo 的 sub 一致性。所有发现端点限制在配置的 SSO 同源，HTTP 不自动跟随重定向。临时登录事务、验证码和本地密码验证结果仅保存在服务端，10 分钟失效；浏览器会话只保存随机事务 ID。不保存本地明文密码或 SSO Access Token。

管理员本地密码验证继续使用现有 8 次失败／10 分钟限制；普通成员的本地密码登录与绑定验证不新增限流，遵循现有要求。启用 SSO 不会强制用户放弃本地密码登录，也不实现 Keycloak 后台注销事件同步；退出本站只退出本站会话。

## 尚需提供／完成

- 本应用专用的 Client ID，以及 public／confidential 类型。
- 排班系统实际访问网址，以确定并注册精确回调地址。
- 若为 confidential，由有权限的管理员在后台填写客户端密钥。
- 一次用户自己完成的 Keycloak 交互登录，以确认实际返回的用户名字段。无需向开发者提供 SSO 密码。

## 验证

`tests/test_keycloak.py` 使用临时 SQLite 数据、真实本地密码、模拟 OIDC 响应及本地生成的签名 Token 验证绑定流程和安全边界。真实 Realm 的公开 discovery 已检查，但没有业务 Client 配置前，不能把模拟测试描述为真实 SSO 用户登录成功。

协议参考：[Keycloak 官方 OIDC 端点](https://www.keycloak.org/securing-apps/oidc-layers)、[PyJWT 签名验证](https://pyjwt.readthedocs.io/en/stable/usage.html)。

本轮验证结果：Python 共 334 项（323 项通过、11 项 MySQL 集成测试跳过），Node 41 项通过；SSO 测试共 24 项。浏览器隔离预览已验证默认隐藏、后台保存启用／停用、登录入口即时同步，以及 390px 移动端布局。既有绑定流程使用模拟 SSO 身份验证，不代表已完成真实 Keycloak 用户登录。
