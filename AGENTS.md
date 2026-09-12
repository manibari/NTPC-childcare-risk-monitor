# 專案開發規範

## LLM provider 與部署契約

- Local development 可以使用 Anthropic Claude API；production deployment 固定使用 AWS Bedrock OpenAI-compatible API。
- AWS region 固定為 `us-west-2`。Bedrock base URL 預設為 `https://bedrock-mantle.us-west-2.api.aws/openai/v1`。
- Provider、model、API key、base URL 由 `.env`／程序環境控制：`LLM_PROVIDER`、`LLM_MODEL`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`ANTHROPIC_API_KEY`、`AWS_DEFAULT_REGION`。不得把金鑰寫進程式或 commit。
- CD 固定寫入 `LLM_PROVIDER=bedrock_openai`、`LLM_MODEL=openai.gpt-5.6-luna` 與 west-2 endpoint／region；本機模型可在 `.env` 自行指定。
- `bedrock_openai` 優先使用 `OPENAI_API_KEY`；為相容目前 repo，未設定時可以讀取 `ANTHROPIC_API_KEY` 中的 Bedrock key。`anthropic` 模式只能使用真正的 Anthropic key，不可由金鑰名稱猜 provider。
- `app/llm.py` 負責 provider/config abstraction 和 OpenAI client。業務邏輯共用 OpenAI Chat Completions messages、function tools、tool results；不要在 AgentService 增加 provider-specific 分支。
- 不應假設 local provider 與 production provider 完全一致；盡量保持相同行為、介面、只讀工具限制與錯誤處理，避免依賴單一 provider 的專屬功能。
- 新增或修改 LLM 功能，必須確認 Bedrock OpenAI-compatible deployment path 也可正常運作。執行 `python -m pytest tests/test_llm.py` 與相關測試，發布前以正式模型驗證一般回答、工具呼叫往返和錯誤處理；模擬測試不等於 live Bedrock 驗證。
- `.env.example` 僅放範例／空金鑰；Secret 由 GitHub Actions 注入遠端 `.env`（權限 `600`）。不得輸出 secret 或將上游完整錯誤暴露給使用者。

## 專案結構與驗證

- `app/main.py`：FastAPI；`app/agent.py`：只讀資料問答；`app/llm.py`：LLM 設定與 client。
- `scripts/`：資料處理與評分；`web/`：前端；`tests/`：pytest；`compose.yaml` 與 `.github/workflows/aws.yaml`：部署。
- 完整測試：`python -m pytest`。部署說明見 `docs/aws-deployment.md`，設定例子見 `README.md` 和 `.env.example`。
