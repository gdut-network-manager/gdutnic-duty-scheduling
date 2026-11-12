# duty-scheduling Helm Chart

FastAPI 排班签到服务的 Kubernetes chart。应用本身是**单副本有状态服务**：会话、登录限流、后台 worker 都在进程内，`/app/data` 需要持久卷，因此模板固定使用 `Recreate`，不要把副本数调大。

## 安装

```bash
helm upgrade --install duty-scheduling ./helm \
  -n gdutnic --create-namespace \
  --set image.tag=<commit-sha> \
  --set clientIpHeader=X-Real-Client-IP \
  --set publicBaseUrl=https://duty.example.com \
  --set sessionHttpsOnly=1
```

从 Harbor OCI 安装：

```bash
helm upgrade --install duty-scheduling \
  oci://registry.gdutnic.com/gdutnic-duty-scheduling/duty-scheduling-helm \
  --version 0.1.<pipeline-id> \
  -n gdutnic --create-namespace
```

## 关键 values

| Value | 说明 | 默认 |
|---|---|---|
| `image.repository` / `image.tag` | 镜像地址；tag 默认取 `appVersion`（CI 写入短 SHA） | Harbor 路径 / `0.1.0` |
| `replicaCount` | 必须保持 `1` | `1` |
| `persistence.*` | `/app/data` 的 PVC（session secret、原始管理员、日志、备份） | `1Gi` RWO |
| `secrets.create` | `true` 由 chart 生成；`false` 引用已有 Secret | `true` |
| `secrets.name` | `create=false` 时必填 | 空 |
| `mysql.enabled` | `true` 在 chart 内起 MySQL；`false` 用外部库 | `false` |
| `database.host/port/name/user` | 外部库连接信息；内置 MySQL 时只用 name/user | `mysql.example.internal` |
| `mysql.persistence.*` | 内置 MySQL 的 PVC | `8Gi` |
| `service.type` | `ClusterIP` / `NodePort` / `LoadBalancer` | `ClusterIP` |
| `ingress.enabled` | 是否创建 Ingress | `false` |
| `clientIpHeader` | 最外层代理写入的真实 IP 头；空 = 不信任任何头 | 空 |
| `publicBaseUrl` | MCP OAuth 回调使用的公开地址 | 空 |
| `sessionHttpsOnly` | `1` 时 Cookie 只走 HTTPS；空 = 不设置 | 空 |
| `environment` | 额外非敏感环境变量，覆盖上面这些字段渲染出的同名变量 | `{}` |
| `extraEnv` | 原始 env 列表，支持 `valueFrom`，优先级最高 | `[]` |
| `envFromSecrets` | 额外导入的 Secret 列表（只能是 chart 未定义的变量） | `[]` |

## 环境变量优先级

Kubernetes 规定容器 `env` 的优先级高于 `envFrom`，所以 chart 渲染出来的变量会遮住 `envFromSecrets` 里的同名键。chart 内部的顺序是：

1. 上面这些 values 字段（`database.*`、`keycloak.*` 等）
2. `environment`
3. `extraEnv`
4. `envFromSecrets`（最低，且只在 chart 没有定义该变量时生效）

值为空字符串的字段不会渲染成 `KEYCLOAK_ISSUER=""`，而是直接省略，避免把 Secret 里的值遮成空串。需要从已有 Secret 注入数据库密码这类变量时用 `extraEnv`：

```yaml
extraEnv:
  - name: MYSQL_PASSWORD
    valueFrom:
      secretKeyRef:
        name: my-mysql
        key: password
```

应用侧读取顺序是“进程环境变量 > `/app/data/.env`”，因此不需要改 PVC 里的 `data/.env`；`data/.env` 只是非容器部署的默认值。后台「其他设置」里保存过的设置存在数据库，优先于环境变量回退。

## 两种 Secret 模式

```yaml
# chart 生成（默认）
secrets:
  create: true
  data: {}
```

`helm upgrade` 会通过 `lookup` 保留已生成的随机值，不会轮换密码。

```yaml
# 引用已有 Secret
secrets:
  create: false
  name: duty-scheduling-secrets
```

已有 Secret 至少需要 `MYSQL_PASSWORD`、`SESSION_SECRET`；`KEYCLOAK_CLIENT_SECRET` 可选；`mysql.enabled=true` 时还需要 `MYSQL_ROOT_PASSWORD`。

## 探针

- `/healthz`：进程存活，不查数据库，用于 liveness/startup。
- `/readyz`：`SELECT 1`，用于 readiness，数据库不可用时只摘流量、不重启 Pod。

## 更多

三种入口拓扑、外部 Nginx 配置、备份和升级步骤见仓库的 [`docs/kubernetes.md`](../docs/kubernetes.md)。
