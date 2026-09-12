# AWS ECR → EC2 部署

GitHub Actions 在 PR 執行 pytest、Docker build、Compose 啟動及 HTTP smoke test。
PR 和 main push 只執行 CI，不會部署。只有在 GitHub Actions → Test and deploy to AWS
→ Run workflow 選擇 main 手動觸發，並通過測試後，才以 OIDC assume
`arn:aws:iam::861560493301:role/github-actions-ecr`，推送 commit SHA 標籤至 ECR，
透過 SSH 在 `44.249.44.27` 執行 Docker Compose。
目前 build 使用 linux/amd64；EC2 必須是 x86_64，若為 Graviton 需調整 build 平台。

區域已設定為 `us-west-2`，ECR repository 為 `hackthron_0912`，SSH 使用者為 `ubuntu`
（將提供的 `ubnutu` 視為拼字誤植）。如需調整，修改 workflow 的 `env`。

## GitHub Actions Secrets

到 repository → Settings → Secrets and variables → Actions 新增：

| Secret | 內容 |
|---|---|
| `EC2_SSH_KEY` | 可登入該使用者的完整、無 passphrase 私鑰，含 BEGIN/END |
| `EC2_KNOWN_HOSTS` | `44.249.44.27 ssh-ed25519 AAAA...` 格式的主機公鑰記錄 |

不需要 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY。SSH port 預設 22。

可在 EC2 的可信任 Console/既有 SSH 連線執行下列命令，將輸出放入
`EC2_KNOWN_HOSTS`，不需把主機私鑰交給 GitHub：

```bash
sudo awk '{print "44.249.44.27 " $1 " " $2}' /etc/ssh/ssh_host_ed25519_key.pub
```

OIDC role trust policy 的 `aud` 應為 `sts.amazonaws.com`，`sub` 應允許
`repo:mumigood/NTPC-childcare-risk-monitor:ref:refs/heads/main`。
workflow 沒有指定 GitHub Environment，不使用 environment 形式的 sub。
ECR role 需要 GetAuthorizationToken 及該 repository 的 push 權限；
EC2 instance role 需要 GetAuthorizationToken、BatchGetImage、GetDownloadUrlForLayer、BatchCheckLayerAvailability。

## EC2 前置設定

Docker 已安裝以外，也需要 AWS CLI、Docker Compose v2（支援 `up --wait`）、curl，
以及 SSH 使用者可以不加 sudo 執行 Docker。可先登入主機確認：

```bash
aws --version
aws sts get-caller-identity
docker info
docker compose version
```

EC2 需要能連出 ECR、S3（image layers）和 Docker Hub（Nginx image）。
Security Group / 主機防火牆需允許 GitHub runner 連入 TCP 22，允許你的測試 IP 連入 TCP 12020。
GitHub hosted runner 的出口 IP 會變動；若 SSH 來源有限制，需使用固定出口 runner 或更新允許範圍。

部署路徑是 SSH 使用者的 `~/watchdog`。可選的應用金鑰放在 EC2 的 `~/watchdog/.env`：

```dotenv
ANTHROPIC_API_KEY=
GOOGLE_MAPS_API_KEY=
X_DEMO_TOKEN=
```

```bash
chmod 600 ~/watchdog/.env
```

不設定 API key 也能使用基本畫面。Compose 的 APP_IMAGE 由 workflow 注入。

## 連線與資料

部署完成後開啟 http://44.249.44.27:12020/ 。
Nginx `12020 → 80` 代理至內網 `app:8765`，不對外發布 8765。
健康檢查： http://44.249.44.27:12020/api/v1/health ，應有 `ok: true`、`db: true`。
這是 HTTP 測試入口；對外正式服務可另外配置網域與 TLS。

`watchdog_watchdog-data` Docker named volume 保留 SQLite、模型及執行資料。
只有首次啟動複製去識別 demo DB；重新部署不覆寫 DB。
請勿執行 `docker compose down -v`，那會刪除資料卷。
新 image 內的模型也不會覆寫既有 volume 裡的模型；模型更新需另行操作。

部署會先 pull，再重建容器並等待健康檢查。重建時有短暫停機。
失敗會讓 Actions 顯示失敗，不會自動回滾資料库或 image。
最後一次成功部署的 image 記在 `~/watchdog/.image.env`。

```bash
cd ~/watchdog
# 日常檢查，.env 裡的 API keys 仍會由 Compose 自動讀取
export $(cat .image.env)
docker compose ps
docker compose logs --tail=100
```

若要回滾，選擇 ECR 中先前成功的完整 commit SHA image URI：

```bash
cd ~/watchdog
bash deploy/deploy.sh us-west-2 861560493301.dkr.ecr.us-west-2.amazonaws.com/hackthron_0912:REPLACE_WITH_40_CHARACTER_COMMIT_SHA
```

請替換區域、repository 和 SHA。回滾 image 不會還原 DB，資料備份需另外安排。

官方參考：[GitHub AWS OIDC](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)、
[AWS ECR image 操作](https://docs.aws.amazon.com/AmazonECR/latest/userguide/getting-started-cli.html)。
