# NTPC-childcare-risk-monitor — 小小守護員 Smart Watchdog

新北市政府 AI 黑客松・教育局命題「AI × 鑑識會計，教保機構智慧風險預警管理系統」的參賽作品。

**一句話**：在有限稽查人力下，給承辦一份「這季該去哪、為什麼、跑得完」的行程與證據包。
主軸是**回頭客**（有裁罰園 48% 再犯、貢獻 75% 事件）。分數＝邏輯迴歸再犯模型（walk-forward AUC 0.64 vs 按次數排序 0.61，核准門檻在模型頁），規則（回頭客燈號）永遠是 fallback；
排程用 CP-SAT 在人力約束下最佳化，可設各區配額與目標；財報是附錄三燈，不宣稱能預測裁罰。

## Quickstart（clone → 真資料畫面 ≤ 5 分鐘，不需 raw PDF、不需 OCR、不需 API key）

```bash
git clone https://github.com/mumigood/NTPC-childcare-risk-monitor && cd NTPC-childcare-risk-monitor
make bootstrap        # pip install → 沒有本地資料就用 data/demo/watchdog-demo.sqlite → 評分 → 排程 → 開 http://localhost:8765/
```

- 要抓最新公開資料：`make update FETCH=1`（kiang 鏡像的全國教保資訊網兩個 JSON → `raw-web/<日期>/` → `data/`）。
- 要重訓模型：`make update TRAIN=1`（walk-forward 回測；模型只在勝過「按次數排序」0.02 邊際時才會被核准）。
- 問答抽屜：在 `.env` 設定 LLM provider、model、API key 與 base URL（見下節及 `.env.example`）；設定不完整就停用，其餘功能不受影響。
- 測試：`make test`（32 條，含姓名性質測試：任何 API 回應不得含負責人／行為人姓名）。

## 畫面（`web/index.html`，rivendell 設計系統，FastAPI 靜態單頁）

主線：總覽（1,216 園地址點地圖）／風險排名／園所詳情（分數與貢獻、具體違規、輿情、負責人關聯）／稽查排程（週 × 稽查員、各區配額、目標預設）／本季名單（分數＋根因＋稽查重點；同負責人待確認）／財務體檢（38 非營利園三燈）。
維護：模型（訓練／混淆矩陣／核准）／回測／資料品質／設定。右下「問資料」抽屜只讀去識別 view。

## 管線（`scripts/`）

| 階段 | 檔案 | 產出 |
|---|---|---|
| ingest | `ingest.py` | `data/kiang_*.json`（不跟轉址、檔案驟縮即沿用舊快照） |
| build | `db.py` | `src_*` 單一交易重建、`app_*` 永不 drop、`v_*` 去識別 view；園×日期事件去重 |
| linker | `linker.py` | 負責人／委辦法人持久代碼、同名（>5 km）旗標、兩層名單 |
| train | `features.py` `train.py` `approve.py` `roi.py` | 觀察點（事件+1 天、季末）、31–365 天標籤、walk-forward 365 天空窗、ROI 重播 |
| score | `score.py` | 規則分 → 分桶經驗再犯率 → 等級；無紀錄園不出屬性分 |
| schedule | `schedule.py` | CP-SAT：容量硬約束、名單必訪、釘選／排除、同負責人同週加分 |
| all | `update.py` | 一條 CLI，exit 0／1 部分／2 中止／64 用法；每階段寫 `app_pipeline_runs` |

## 數字（事件層，新北，資料日期 2026-09-11）

1,216 園 · 裁罰 1,474 列 → 1,004 事件 · 487 園有紀錄 · 回頭客 234（48%）· 兩次裁罰間隔 63% 在一年內。
回測（2021–2025）：邏輯迴歸 AUC 0.64、前 100 覆蓋 43%；規則 0.63／42%；GBDT 0.61／44%；按次數 0.61／43%。門檻「中」（≥18%）的匯總混淆矩陣：recall 0.56、precision 0.20、κ 0.11——排序工具，不是預言機。
ROI 重播：同樣 312 次／季人力，規則排程命中次年被罰園 40–53%，輪流稽查 25–28%。

## 個資

園名為公開登記名稱；負責人／行為人姓名在架構層擋住（API 與問答只讀 `v_*` view，view 無姓名欄；demo DB 已清空姓名欄）。

## 文件

`task_plan.md`（實作計畫與決策）· `docs/requirements/`、`docs/flows/`、`docs/design/`（SD + 圖）· `docs/reviews/2026-09-12-autoplan.md`（四階段審查）· `docs/exploration.md`（探索與財報結論）· `mockups/smart-watchdog-v2.html`。
主辦方 raw PDF（1.8 GB）與 OCR 流程見 `docs/exploration.md` 與 `scripts/ocr_*.py`。

## AWS CI/CD

Docker + ECR + EC2（Docker Compose YAML），Nginx 對外 port `12020`。
GitHub Secrets、EC2 前置設定與部署方式見 [AWS 部署文件](docs/aws-deployment.md)。

## LLM provider 設定

先 `cp .env.example .env`，再填入金鑰；`.env` 不可 commit，更新後重啟應用程式。
本機開發可以使用 Anthropic Claude API；正式部署固定使用 **AWS Bedrock OpenAI-compatible API，region 為 `us-west-2`**。

| `.env` 欄位 | 本機 Anthropic | 正式 Bedrock |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `bedrock_openai` |
| `LLM_MODEL` | 可存取的 Claude model ID | `openai.gpt-5.6-luna`（CD 指定） |
| `OPENAI_BASE_URL` | `https://api.anthropic.com/v1` | `https://bedrock-mantle.us-west-2.api.aws/openai/v1` |
| `ANTHROPIC_API_KEY` | Anthropic key | 僅相容舊 Secret 名稱 |
| `OPENAI_API_KEY` | 不使用 | Bedrock API key |
| `AWS_DEFAULT_REGION` | `us-west-2` | `us-west-2` |

應用程式從環境變數／`.env` 讀取 provider、model、API key 和 base URL，既有程序環境優先於 `.env`。
Bedrock base URL 未填時使用上述 west-2 預設；Anthropic 必須明確設定 endpoint。
模型優先讀 `LLM_MODEL`，並相容舊的 `WATCHDOG_AGENT_MODEL`；沒有寫死的應用程式模型預設。
CD 固定指定 Bedrock provider、west-2 endpoint、region 和 `openai.gpt-5.6-luna`，產生遠端 `.env`；本機無須修改程式即可切換。

你目前 repo 的 `ANTHROPIC_API_KEY` 若已放 Bedrock key，可繼續使用：CD 優先取 Secret `OPENAI_API_KEY`，未填才取 `ANTHROPIC_API_KEY`，寫成遠端 `OPENAI_API_KEY`。
應用程式在 `bedrock_openai` 模式也支援同樣的金鑰優先序；`anthropic` 模式只使用真正的 Anthropic key，不會依 key 名稱自動切換 provider。

`app/llm.py` 集中設定驗證與 OpenAI client 建立；`app/agent.py` 使用共同的 Chat Completions messages、function tools 與 tool results，業務邏輯不判斷 provider。
本機與正式 provider 不一定一致，模型能力與輸出也不能假設完全相同；盡量維持相同介面、工具契約、錯誤處理和只讀資料限制。
新增 LLM 功能必須確認 **Bedrock OpenAI-compatible deployment path** 能正常運作，尤其是工具呼叫及模型支援的參數。

驗證：`python -m pytest tests/test_llm.py` 以實際 OpenAI SDK 搭配模擬 HTTP transport 驗證兩條路徑；發布前需在 Bedrock 實際測試一般回答與工具查詢，確認模型權限和 API key 有效。
Anthropic 官方相容層適合本機測試，不使用其專屬 caching／thinking／strict schema 功能作為共同介面保證。
參考：[Anthropic OpenAI compatibility](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk)、[AWS Bedrock OpenAI SDK 範例](https://aws.amazon.com/blogs/machine-learning/introducing-gemma-4-models-on-amazon-bedrock/)。
