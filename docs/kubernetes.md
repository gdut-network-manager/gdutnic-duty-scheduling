# Kubernetes 部署

本文覆盖镜像构建、Helm 安装、三种入口拓扑、两种 MySQL 模式、两种 Secret 模式，以及真实客户端 IP 的配置方式。

## 1. 部署前需要知道的事

- 应用是**单副本有状态服务**：登录会话、限流计数器、推送/在线状态 worker 都在进程内；`/app/data` 保存 `session_secret.txt`、`root_admin.json`、日志和备份。chart 固定 `replicaCount: 1` + `replicaStrategy: Recreate`，需要一块 `ReadWriteOnce` PVC。
- 数据库表结构在应用连接 MySQL 时自动创建/补齐，不需要单独的 migration Job。
- 启动流程是 `init_data` → 等待 `check_database` → `uvicorn`，入口脚本已经包含重试。
- 没有 `/healthz` 之外的外部依赖探针；`/readyz` 会执行 `SELECT 1`。
- 容器内不执行 `start.sh`，也不会创建 venv 或联网安装依赖。

## 2. 构建镜像

本地构建：

```bash
docker build -t registry.gdutnic.com/gdutnic-duty-scheduling/duty-scheduling:dev .
```

CI 会在默认分支和 tag 上构建并推送：

- 默认分支：`<短SHA>` + `latest`
- `v*` tag：额外推送同名 tag
- 已存在的镜像默认跳过，需要强制重建时设置 `FORCE_REBUILD=true`

基础镜像和 pip 默认走：

- `registry.gdut.edu.cn/docker/library/python:3.12-slim`
- `https://mirrors.gdut.edu.cn/pypi/web/simple`

可以通过 Docker build args 覆盖：

```bash
docker build \
  --build-arg PYTHON_IMAGE=registry.gdut.edu.cn/docker/library/python:3.12-slim \
  --build-arg PIP_INDEX_URL=https://mirrors.gdut.edu.cn/pypi/web/simple \
  -t ... .
```

## 3. Secret：两种模式

### 3.1 chart 自动生成（默认）

```yaml
secrets:
  create: true
  data: {}
```

首次 `helm install` 会用 `randAlphaNum` 生成 `MYSQL_PASSWORD`、`SESSION_SECRET`、`KEYCLOAK_CLIENT_SECRET`、`MYSQL_ROOT_PASSWORD`；`helm upgrade` 通过 `lookup` 保留已有值。

查看生成的值：

```bash
kubectl -n gdutnic get secret duty-scheduling-secrets \
  -o jsonpath='{.data.SESSION_SECRET}' | base64 -d
```

### 3.2 引用已有 Secret

```bash
kubectl -n gdutnic create secret generic duty-scheduling-secrets \
  --from-literal=MYSQL_PASSWORD='...' \
  --from-literal=SESSION_SECRET="$(openssl rand -hex 32)" \
  --from-literal=KEYCLOAK_CLIENT_SECRET='...' \
  --from-literal=MYSQL_ROOT_PASSWORD='...'
```

```yaml
secrets:
  create: false
  name: duty-scheduling-secrets
```

至少需要 `MYSQL_PASSWORD`、`SESSION_SECRET`；`KEYCLOAK_CLIENT_SECRET` 可选；`mysql.enabled=true` 时还需要 `MYSQL_ROOT_PASSWORD`。

### 3.3 环境变量优先级

应用只读进程环境变量，`/app/data/.env` 是 `load_dotenv` 补的默认值，且不会覆盖已存在的环境变量，所以**不需要改 PVC 里的 `data/.env`**。容器里的优先级是：

1. `extraEnv`（原始 env 列表，支持 `valueFrom`）
2. `environment`（键值对）
3. chart 字段（`database.*`、`keycloak.*`、`clientIpHeader`、`publicBaseUrl` 等）
4. `envFromSecrets`

Kubernetes 规定 `env` 高于 `envFrom`，因此 `envFromSecrets` 只能提供 chart 没有定义的变量。要覆盖 `MYSQL_HOST`、`KEYCLOAK_ISSUER` 这类已由 chart 渲染的变量，用 `environment` 或 `extraEnv`：

```yaml
environment:
  KEYCLOAK_ENABLED: "true"
  KEYCLOAK_ISSUER: https://sso.example.com

extraEnv:
  - name: MYSQL_PASSWORD
    valueFrom:
      secretKeyRef:
        name: my-mysql
        key: password
```

值为空的 chart 字段不会渲染，避免用空串遮蔽 Secret 里的值。另外，后台「其他设置」保存过的配置存在数据库里，SSO 等设置一旦在后台保存过，就优先于环境变量回退。

## 4. MySQL：两种模式

### 4.1 外部 MySQL（默认）

```yaml
mysql:
  enabled: false
database:
  host: mysql.example.internal
  port: 3306
  name: gdutnic
  user: gdutnic
```

数据库需要提前创建，应用账号需要建表、加字段和读写权限。需要 TLS 时把 CA 放到 PVC 的 `certs/` 下，或用 `extraVolumes`/`extraVolumeMounts` 挂载，然后通过 `environment` 设置 `MYSQL_SSL_CA`（相对路径按 `/app/data` 解析）。

### 4.2 chart 内置 MySQL

```yaml
mysql:
  enabled: true
  persistence:
    size: 8Gi
database:
  name: gdutnic
  user: gdutnic
```

会创建一个单副本 `StatefulSet`（`registry.gdut.edu.cn/docker/library/mysql:8.4`）和 PVC，应用自动连接 `<release>-mysql:3306`。内置 MySQL 适合测试或小规模部署；正式环境建议用统一托管的 MySQL，备份和升级更简单。

## 5. 三种入口拓扑

以下命令默认 release 名为 `duty-scheduling`、namespace 为 `gdutnic`，这样资源名就是 `duty-scheduling`、`duty-scheduling-secrets`、`duty-scheduling-mysql`。

### 5.1 Ingress（可在外层再套 Nginx）

```bash
helm upgrade --install duty-scheduling ./helm \
  -n gdutnic --create-namespace \
  --set image.tag=<短SHA> \
  --set clientIpHeader=X-Real-Client-IP \
  --set publicBaseUrl=https://duty.example.com \
  --set sessionHttpsOnly=1 \
  --set ingress.enabled=true \
  --set ingress.className=nginx \
  --set ingress.hosts[0].host=duty.example.com \
  --set ingress.hosts[0].paths[0].path=/ \
  --set ingress.hosts[0].paths[0].pathType=Prefix
```

如果 TLS 在 ingress-nginx 终止：

```bash
  --set ingress.tls[0].secretName=duty-tls \
  --set ingress.tls[0].hosts[0]=duty.example.com
```

如果 TLS 在最外层 Nginx 终止，**不要**给 Ingress 配 `tls`，否则 ingress-nginx 可能把已经是 HTTP 的内部请求再 308 跳到 HTTPS，形成回环。

### 5.2 不用 Ingress，外层 Nginx 直接打 NodePort

```bash
helm upgrade --install duty-scheduling ./helm \
  -n gdutnic --create-namespace \
  --set image.tag=<短SHA> \
  --set clientIpHeader=X-Real-Client-IP \
  --set publicBaseUrl=https://duty.example.com \
  --set sessionHttpsOnly=1 \
  --set ingress.enabled=false \
  --set service.type=NodePort \
  --set service.nodePort=30525
```

外层 Nginx 的 upstream 指向 `<任一节点IP>:30525`。也可以把 `service.type` 设为 `LoadBalancer`，由云厂商/金属 LB 分配地址。

### 5.3 不用 Ingress，外层 Nginx 在集群内

```bash
helm upgrade --install duty-scheduling ./helm \
  -n gdutnic --create-namespace \
  --set image.tag=<短SHA> \
  --set clientIpHeader=X-Real-Client-IP \
  --set publicBaseUrl=https://duty.example.com \
  --set sessionHttpsOnly=1 \
  --set ingress.enabled=false \
  --set service.type=ClusterIP
```

upstream 用 `http://duty-scheduling.gdutnic.svc.cluster.local:80`。

### 5.4 完整示例：外层 Nginx → ingress-nginx

`attendance.gdutnic.com` 的完整配置见 [`nginx-attendance.gdutnic.com.conf`](nginx-attendance.gdutnic.com.conf)，包含：

- 80 跳 443、TLS 与 HSTS
- upstream 指向 ingress-nginx，默认走 HTTP，避免内外两层重复终止 TLS
- `proxy_set_header Host $host` 让 Ingress 规则匹配
- 用 `X-Real-Client-IP $remote_addr` 覆盖写真实 IP，配合 `clientIpHeader`

要点：

- TLS 在最外层终止时，Ingress **不要**配 `tls`，也不要开 `ssl-redirect`，否则 ingress-nginx 会把内部 HTTP 请求再次 308 到 HTTPS，形成回环。
- ingress-nginx 默认 `proxy-body-size` 只有 1m，导入名单/备份会 413，需要在 Ingress 注解里放开（示例文件头部已列出）。
- 外层 Nginx 到 ingress 如果走 HTTPS，需要 `proxy_ssl_server_name on` 并设置 SNI；证书由内部 CA 签发时再配 `proxy_ssl_trusted_certificate`。
- ingress-nginx 想在自己的日志里看到真实 IP，需要在 controller ConfigMap 里设 `use-forwarded-headers: "true"` 和 `compute-full-forwarded-for: "true"`；应用本身只读 `X-Real-Client-IP`，不依赖这两项。

## 6. 真实客户端 IP（IP 签到/限流）

应用只读取 `clientIpHeader` 指定的头，默认空 = 不信任任何头，直接使用 socket peer。生产环境在最外层 Nginx 上**覆盖写**：

```nginx
location / {
    proxy_pass http://duty_backend;
    proxy_http_version 1.1;

    proxy_set_header Host $host;
    proxy_set_header X-Real-Client-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

要点：

- 必须用 `proxy_set_header` 覆盖，不能用 `add_header` 追加；否则客户端可以自己伪造同名头。
- 应用只需要 `X-Real-Client-IP`；`X-Forwarded-For` 和 `X-Forwarded-Proto` 是给其他代理/排障用的，应用不读。
- ingress-nginx 会原样透传未知头，不需要改 controller 的全局配置。
- 启用后，登录限流、`/api/network-access`、打卡/外勤的网段限制都会使用真实 IP。项目现有的「打卡设置 → 来源 IP 限制」会直接生效。
- 如果入口可以被绕过（直接访问 NodePort/Ingress/LoadBalancer），这个头就可以被伪造。请只把入口暴露给最外层 Nginx。

验证：

```bash
curl -s https://duty.example.com/api/network-access
# {"ip":"1.2.3.4","checkin":true,"field":true}
```

## 7. HTTPS 与 OAuth 回调

- TLS 在最外层 Nginx 终止时，设置 `sessionHttpsOnly=1`，浏览器侧 Cookie 会带 `Secure`。
- 设置 `publicBaseUrl=https://duty.example.com`。MCP/Outline 的 OAuth 回调会用这个地址拼接，不再依赖 `request.base_url`；Keycloak 仍使用后台配置的 `KEYCLOAK_REDIRECT_URI`。
- 如果站点还要通过纯 HTTP 访问，保持 `sessionHttpsOnly=0`。

## 8. 安装后检查

```bash
kubectl -n gdutnic rollout status deploy/duty-scheduling

kubectl -n gdutnic port-forward svc/duty-scheduling 50025:80
curl http://127.0.0.1:50025/healthz
curl http://127.0.0.1:50025/readyz
```

- `/healthz` 返回 `{"status":"ok"}` 只代表进程活着。
- `/readyz` 返回 `{"status":"ready"}` 才代表数据库可用；数据库不可用时返回 503，Pod 会被摘流量但不会重启。
- 首次打开站点会引导创建原始管理员；凭据在 PVC 的 `/app/data/root_admin.json`。

## 9. 升级、回滚、备份

```bash
# 升级
helm upgrade duty-scheduling oci://registry.gdutnic.com/gdutnic-duty-scheduling/duty-scheduling-helm \
  --version 0.1.<pipeline-id> -n gdutnic

# 历史与回滚
helm history duty-scheduling -n gdutnic
helm rollback duty-scheduling <revision> -n gdutnic
```

升级时旧 Pod 会先停止（`Recreate`），会有短暂停机；这是单副本 + RWO PVC 的固有代价。

备份：

```bash
# 内置 MySQL
kubectl -n gdutnic exec duty-scheduling-mysql-0 -- \
  sh -c 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --events gdutnic' \
  > gdutnic-$(date +%F).sql

# 运行数据（session secret、原始管理员、日志、备份）
kubectl -n gdutnic cp duty-scheduling-<pod>:/app/data ./data-backup
```

外部 MySQL 用你们现有的备份方案；`/app/data` 仍建议定期备份，尤其是 `root_admin.json` 和 `session_secret.txt`。

### 卸载与数据保留

默认 `keepDataOnUninstall: true`，PVC 和 chart 生成的 Secret 都带 `helm.sh/resource-policy: keep`。`helm uninstall` 只删除 Deployment、Service、Ingress 等工作负载，保留：

- `<release>-data` PVC：`/app/data` 下的 `root_admin.json`（原始管理员密码）、`session_secret.txt`、日志和备份
- chart 生成的 Secret：`MYSQL_PASSWORD`、`SESSION_SECRET`、`KEYCLOAK_CLIENT_SECRET`
- 内置 MySQL 的 PVC：由 StatefulSet `volumeClaimTemplates` 创建，Kubernetes 本来就不会随 StatefulSet 删除

保留 Secret 很重要：重装时 chart 会用 `lookup` 复用原有值，不会重新生成数据库密码；否则新密码和旧数据目录/数据库对不上。

确实要连数据一起清除时，先卸载再手动删除：

```bash
helm uninstall duty-scheduling -n gdutnic \
  && kubectl -n gdutnic delete pvc duty-scheduling-data \
  && kubectl -n gdutnic delete secret duty-scheduling-secrets \
  && kubectl -n gdutnic delete pvc data-duty-scheduling-mysql-0   # 内置 MySQL
```

`data-duty-scheduling-mysql-0` 是 `<volumeClaimTemplate name>-<release>-mysql-0`，只有 `mysql.enabled=true` 时才存在。把 `keepDataOnUninstall` 设为 `false` 也可以恢复成 Helm 默认行为。

重新安装时要用**相同的 release 名和命名空间**，Helm 会接管保留下来的 PVC/Secret 并复用里面的值。如果 Helm 报 `exists and cannot be imported into the current release`（例如资源是手工创建或从别的 release 迁移过来的），补上归属信息再装：

```bash
kubectl -n gdutnic annotate pvc duty-scheduling-data \
  meta.helm.sh/release-name=duty-scheduling \
  meta.helm.sh/release-namespace=gdutnic --overwrite
kubectl -n gdutnic annotate secret duty-scheduling-secrets \
  meta.helm.sh/release-name=duty-scheduling \
  meta.helm.sh/release-namespace=gdutnic --overwrite
kubectl -n gdutnic label pvc duty-scheduling-data app.kubernetes.io/managed-by=Helm --overwrite
kubectl -n gdutnic label secret duty-scheduling-secrets app.kubernetes.io/managed-by=Helm --overwrite
```

## 10. CI 变量与 Runner

参考项目的约定：

| 变量 | 用途 | 必需 |
|---|---|---|
| `HARBORHOST` | Harbor 地址，例如 `registry.gdutnic.com` | 是 |
| `USERNAME` / `PASSWORD` | Harbor 账号 | 是 |
| `SONAR_HOST_URL` / `SONAR_TOKEN` | SonarQube；未配置则跳过 | 否 |
| `GITHUB_REPO` / `GITHUB_TOKEN` | GitHub 镜像；未配置则跳过 | 否 |
| `COSIGN_PRIVATE_KEY` / `COSIGN_PASSWORD` | cosign 签名；未配置则跳过 | 否 |
| `FORCE_REBUILD` | `true` 时即使镜像已存在也重建 | 否 |
| `KUBECONFIG_DATA` | base64 编码的 kubeconfig；未配置则跳过部署 | 否 |
| `VALUES_BASE64` | base64 编码的部署 values（见下）；未配置则跳过部署 | 否 |

`DEPLOY_NAMESPACE`、`DEPLOY_RELEASE`、`DEPLOY_TIMEOUT` 可在 `.gitlab-ci.yml` 的 `helm-deploy.variables` 里改，默认 `gdutnic` / `duty-scheduling` / `10m`。

### 自动部署

`helm-deploy` 只在默认分支运行，依赖 `docker-build-and-push`、`helm-build` 和 `image-sign`，用 CI 产出的 chart 版本和镜像 tag 执行。部署用的 ServiceAccount 建议只授予目标命名空间的权限，因此**命名空间要提前建好**，job 不会执行 `--create-namespace`：

```bash
helm upgrade --install duty-scheduling \
  oci://${HARBORHOST}/gdutnic-duty-scheduling/duty-scheduling-helm \
  --version 0.1.<pipeline-id> \
  -n gdutnic \
  --values custom.yaml \
  --set image.tag=<短SHA> \
  --atomic --timeout 10m --debug
```

`--debug` 会在 job 日志里打印 USER-SUPPLIED VALUES 和渲染后的 MANIFEST，排查失败很方便，但也会暴露其中的敏感内容：`secrets.create=true` 时 Secret 的 base64 data 会直接出现在日志里，`VALUES_BASE64` 里的明文密码同样会被打印。因此生产部署应使用 `secrets.create=false` 引用已有 Secret，并不在 values 里写明文密码。

准备 `KUBECONFIG_DATA`：

```bash
base64 -w0 ~/.kube/config > kubeconfig.b64
# 把 kubeconfig.b64 的内容粘到 GitLab 变量 KUBECONFIG_DATA
```

准备 `VALUES_BASE64`，内容就是部署用的 values 文件，例如：

```yaml
clientIpHeader: X-Real-Client-IP
publicBaseUrl: https://attendance.gdutnic.com
sessionHttpsOnly: "1"

secrets:
  create: false
  name: duty-scheduling-secrets

mysql:
  enabled: false
database:
  host: mysql.example.internal
  port: 3306
  name: gdutnic
  user: gdutnic

keycloak:
  enabled: "true"
  issuer: https://sso.example.com/realms/gdutnic
  clientId: duty
  redirectUri: https://attendance.gdutnic.com/api/auth/keycloak/callback

ingress:
  enabled: true
  className: nginx
  tls: []            # TLS 在外层 Nginx 终止
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "0"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "300"
  hosts:
    - host: attendance.gdutnic.com
      paths:
        - path: /
          pathType: Prefix
```

```bash
base64 -w0 values-production.yaml > values.b64
# 把 values.b64 的内容粘到 GitLab 变量 VALUES_BASE64
```

密码等敏感值不要写进 `VALUES_BASE64`：用 `secrets.create: false` + `secrets.name` 引用已有 Secret，或放在 `extraEnv` 的 `valueFrom` 里。`image.tag` 由流水线用当次构建的 tag 覆盖，不需要在 values 里写。

### cosign 密钥

签名任务基于 `ubuntu:22.04`，从校园镜像的 Debian trixie 源下载 cosign 2.x 的 deb 包安装（只取 deb，不引入 trixie 的库）：

```bash
printf '%s\n' 'deb [trusted=yes] http://mirrors.gdut.edu.cn/debian trixie main' \
  > /etc/apt/sources.list.d/debian-trixie.list
apt-get update -y
cd /tmp && apt-get download cosign && dpkg -i cosign_*.deb
```

因此用本机任意较新版本的 `cosign generate-key-pair` 生成的密钥都可以直接使用，包括 `ENCRYPTED SIGSTORE PRIVATE KEY` 和 scrypt KDF 的新格式：

```bash
export COSIGN_PASSWORD='请替换为一个强密码'
cosign generate-key-pair
# 生成 cosign.key（私钥，带密码加密）和 cosign.pub（公钥）
```

`COSIGN_PRIVATE_KEY` 支持三种格式，签名任务会自动识别：

1. **原始 PEM**：`cosign.key` 的完整内容，含 `-----BEGIN ... PRIVATE KEY-----` 行。GitLab 变量值如果无法保留换行，不要用这种。
2. **base64（推荐）**：适合 GitLab 网页单行输入框。
   ```bash
   base64 -w0 cosign.key > cosign.key.b64
   ```
   把 `cosign.key.b64` 的内容粘贴到变量值即可。
3. **File 类型变量**：变量类型选 `File`，签名任务会直接读取 GitLab 写出的临时文件。

`COSIGN_PASSWORD` 填生成密钥时 `COSIGN_PASSWORD` 的值；如果生成时用了空密码，也要把变量定义为空值。`cosign.pub` 不需要放进 CI，可提交到仓库或另行保存，用于验证签名：

```bash
cosign verify --key cosign.pub <镜像>
```

签名使用 `--tlog-upload=false`，不依赖公网 Rekor，适合内网 Harbor。

Runner tag：

- `dev`：Sonar、Helm、签名
- `Docker`：Docker 构建（需要可用的 Docker daemon，和参考项目一致）

镜像与 chart 路径：

- 镜像：`${HARBORHOST}/gdutnic-duty-scheduling/duty-scheduling`
- Chart：`oci://${HARBORHOST}/gdutnic-duty-scheduling/duty-scheduling-helm`

## 11. 已知限制

- 不支持多副本/HPA：内存状态和 RWO PVC 会冲突。
- 不支持子路径部署（`/duty`），Ingress 请用根路径 `/`。
- chart 不包含 MySQL 自动备份 CronJob；备份命令见上。
- 外层 Nginx 必须覆盖写 `X-Real-Client-IP`；入口可被绕过时该头可伪造。
- 默认 8 次管理员登录失败 / 10 分钟、50 次值班账号失败 / 5 分钟，按解析后的 IP 计数。
