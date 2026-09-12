<!-- /autoplan restore point: /Users/manibari/.gstack/projects/NTPC-childcare-risk-monitor/main-autoplan-restore-20260912-014852.md -->
# Task Plan — Smart Watchdog：教保機構稽查覆核與人力配置（US-1~10）

> 2026-09-12 立案，同日過 `/gstack-autoplan`（CEO / Design / Eng / DX 四階段，Codex + Claude 子代理雙聲道，
> 4×6/6 共識，46 條決策，最終 gate Peter 全部接受）。審查全文：`docs/reviews/2026-09-12-autoplan.md`。
> SoT 鏈：`docs/requirements/smart-watchdog.md`（US-1~10）→ `docs/flows/smart-watchdog-flow.md` →
> `mockups/smart-watchdog.html` → `docs/design/2026-09-12-smart-watchdog-sd.md`（SD Full + 5 圖）。
>
> goal：在有限稽查人力下，給承辦一份「這季該去哪、為什麼、跑得完」的行程與證據包；
> 交付 = 可操作 demo（真資料）+ 提案 deck。歸位：新北 AI 黑客松・教育局命題。

## 架構定調（review 定案，不再重議）

1. **定位＝稽查覆核與資源配置工具，不是預言機（D1）**：預測的是「未來 12 個月內被裁罰」不是傷害；deck 首頁與總覽頁尾明寫 surveillance bias；提供「從未被稽查園覆蓋率」警語。
2. **事件層為唯一計數單位（Eng C2，實查）**：`penalty_events` = 園×日期去重（1,474 列 → 1,004 事件，3 列重複）。回頭客 234/487 = **48%**、貢獻 **75%**、事件後 12 個月再犯 **26–33%**。deck 一律用事件層數字。
3. **規則為主、模型驗證（gate 品味 1）**：回頭客燈號（事件次數 × 近期性 × 兒安條款）是排序主幹；GBDT 只在回測「前 100 覆蓋率且 AUC 皆優於按次數排序」時取代規則做細排序。無裁罰史園一律「無紀錄」，不出屬性分（E3）。
4. **人力是系統內約束（E1，Peter）**：settings 存 `n_inspectors`、`visits_per_inspector_week`、`quarter_weeks`；CP-SAT 排程輸出 週×稽查員 行程；覆蓋為軟約束（權重 risk_01）、容量硬約束、season_list 必訪、釘選固定、排除移除、停辦園不排、同 owner-link 與同區同週加分；`max_time 20s`、seed 固定；INFEASIBLE/UNKNOWN → 422 帶原因。
5. **連坐＝人工確認線索（E5）**：名單分「已裁罰」「連坐待確認」兩層；不進分數；排除同名同步反映排名／匯出／圖。
6. **匿名化架構層擋（Eng H1，跨階段主題 2）**：API 與問答只讀 `v_*` 去識別 view（無 actor_name/owner/operator/tel/address）；園名用公開登記名稱（Peter 2026-09-12「不要用去識別化，看不懂」；`anonymize_titles` 預設 0，只在公開展示時開）；姓名仍架構層擋；契約測試「任何回應 grep 姓名清單 = 0」為 REGRESSION 級。
7. **表分兩族（Eng A1）**：`src_*` 每次更新單一交易 DROP/CREATE（WAL、busy_timeout、schema 斷言、筆數驟降中止、孤兒檢查）；`app_*` 持久永不 drop；不 rename 檔。
8. **時間切分零洩漏（Eng C1/A4/A5）**：觀察點 = 事件 +1 天 + 季末，`asof ≥ reg_date`；標籤 = asof 後 31–365 天內新事件；特徵分事件史（進回測）與快照屬性（`snapshot=1` 不進回測）；walk-forward 測試年 Y 訓練集 `asof ≤ Y-01-01 − 365d`；`models` 含 `feature_hash`、`eval_year`、`seed`；固定 holdout 年比版本；單一 active 用 partial unique index，只有 Approver 改狀態。
9. **Agentic 問答唯讀（E16，Peter；比照 PTI-ARES AgentService）**：工具集 = `sql_readonly`（ro URI + authorizer 只放 v_* + progress 5s + AST allowlist + LIMIT 200）、`explain_score`、`get_schedule`、`get_finance`；無寫入工具；每輪 `app_agent_turns` 留痕；無 Anthropic key 時抽屜 disabled、其餘 100% 可用。
10. **設計系統＝rivendell `dashboard-next/DESIGN.md`（Peter）+ 三條衍生**：CJK fallback `PingFang TC, Noto Sans TC`；低風險 = `--text-muted`（不用綠）；等級 = 8px 色點 + 文字。立案別純文字；圖表內嵌 SVG 三型單綠；無藍紫、無陰影、Lucide。
11. **畫面收斂（gate User Challenge，Peter 接受；2026-09-12 Peter 加「財務體檢」為主線 6）**：主線 6（總覽地圖／排名／詳情含關聯圖 hero／排程／本季名單／財務體檢：38 非營利園三燈總表 + 異常排序 + 五年趨勢，附錄定位不變）+ 維護區 3 次要（回測／資料品質／設定）+ 問答抽屜 + 匯出對話框。
12. **DX 底線（DX 雙聲道）**：`make bootstrap` 從 clone 到真資料排名頁 ≤ 5 分鐘、不需 raw PDF、不需 OCR；`data/demo/watchdog-demo.sqlite` 入 repo；`update.py` 完整 CLI 契約，exit 0/1 部分/2 中止/64 用法；`PipelineError(problem, cause, fix)`；API `/api/v1`、409+state 取代 503。
13. **財報＝每園四面向燈號（E4 擴充，Peter 2026-09-12）**：收入／支出成本／資產負債／餘絀各自紅黃綠 + 總燈號 + 逐年趨勢（`finance.py` → `src_finance_flags`）；內控查核表 V/X 與補助依賴度為 T6 待補；不進分數、不宣稱預測裁罰。

## 兩刀總綱（scope 經 review 確認）

- **刀 1（資料與模型）**：P0 DB 加固 + 事件去重 + tests 骨架 → P1 Linker → P2 觀察點/訓練/回測/ROI → P3 評分 → P3b 排程。
  驗收＝pytest 全綠（含 7 條 REGRESSION 級）+ 回測表可重跑（固定 seed 兩次相等）+ 排程在假設人力（3 人 × 8 次/週 × 13 週）下產生行程與覆蓋率 + findings.md 有事件層數字與 ROI。
- **刀 2（交付）**：P4 API + 匯出 → P4b 問答 → P5 Web 5+3 畫面 → P6 update.py + ingest + bootstrap → P7 驗收（review / qa-dataflow / qa）→ P8 deck。
  驗收＝`make bootstrap` ≤ 5 分鐘 + Playwright 三條 journey + `/qa-dataflow` 拿 SD §6 四張 target 圖比 actual + deck storyline signed-off。
- 刀 1 先行；**P2 回測數字是 go/no-go**：決定 deck 主張是規則還是模型（架構定調 3）。

## Phases

### 刀 1

- [x] **P0 資料層加固**：`scripts/db.py` DBBuilder（src_/app_、單交易重建、WAL、schema 斷言、筆數驟降、孤兒檢查、app_ 永不 drop、`schema_version`）；`penalty_events` 去重；`is_child_safety`；`app_settings` 預設（人力三參數、門檻、top_n、觀察期、stale_days、anonymize_titles）；`app_pipeline_runs`；`pyproject.toml` + `.python-version` + `.env.example`；`tests/` 骨架 + 去重／DB 保留兩條測試。exploration/findings/memory 數字改事件層。
- [x] **P1 Linker**：owner / 委辦法人 → `app_linkers`（持久、首次配碼永不回收）、`preschool_linkers`（同名判定 5 km）、`app_watchlist` 兩層；kiang 覆蓋率驗證腳本（T5）。
- [x] **P2 觀察點／訓練／回測／ROI**：`features.py`（事件史 vs 快照、洩漏 assert、31–365 標籤、≥ reg_date）；`train.py`（walk-forward gap 365、GBDT + 邏輯迴歸、三 baseline、feature_hash/eval_year/seed、`app_models`+`app_backtests`+`app_model_events`）；`approve.py`（單一 active、降幅規則）；`roi.py`（歷史年重播排程 vs 輪流）。**Go/no-go 決定主張。** → **結果（2026-09-12 實跑）：GBDT AUC 0.607 vs 按次數 0.606、前 100 覆蓋 0.435 vs 0.431，邏輯迴歸 AUC 0.640 但覆蓋率持平 → 未達 0.02 邊際，主張＝規則（回頭客燈號），模型只當驗證附錄。ROI 重播 2021–2024：規則排程覆蓋 40–53% 次年裁罰園 vs 輪流 25–28%（含首犯園計入分母）。**
- [x] **P3 評分**：`score.py`（rule → model 條件切換 → 無紀錄；`score_batch_id` 交易切 current；`risk_01`；等級絕對門檻 + 前 N；top 理由白話句）。→ 實跑：規則版 prob_12m = 分桶經驗再犯率（最高 0.385），門檻預設改 高 0.22／中 0.10（Peter 2026-09-12：抽檢寧可錯殺，中門檻＝人力跑得完的最低點，五年回測 recall 0.72）→ 高 18／中 35／低 434／無紀錄 710／停辦 19。
- [x] **P3b 排程**：`schedule.py`（CP-SAT，架構定調 4）+ `app_schedules`/`app_schedule_visits`；候選前 300 + 必訪 + 連坐園。→ 實跑：302 候選、67 必訪、產能 312，全數覆蓋，20s FEASIBLE。

### 刀 2

- [x] **P4 API + 匯出**：FastAPI `/api/v1/*`（overview/rankings/preschools/linkers/backtest/schedule/season-list/export/data-quality/settings/feedback）；`v_*` 去識別 view；錯誤 envelope（request_id/retryable/hint）；409+state；`X-Demo-Token`；契約測試 + 姓名性質測試。
- [x] **P4b 問答**：`app/agent.py` AgentService（架構定調 9）+ `POST /api/v1/ask`（串流）+ `app_agent_turns`；每頁 3 個建議問題；demo 3 題離線快取；T9 稽查重點三行（P2）。
- [x] **P5 Web**：Next.js + 專案 DESIGN.md；主線 6 + 維護區 3 + 抽屜 + 匯出；**總覽用地圖呈現（Peter 2026-09-12）：Leaflet 園所點圖（等級色點）+ 行政區彙總，離線退回長條；每園一個地址點（Peter 2026-09-12「園所要有對應的地址點」，`v_preschools.lng/lat` 全 1,216 園齊全、不帶地址文字）**；八張圖（覆蓋率曲線／提前天數／再犯累積／區×法條熱圖／36 月趨勢／各區派工／產能 vs 覆蓋／該園間隔 vs 全市）；10×5 互動狀態表；desktop 1440；a11y 規格；mockup 先換膚重排（D2/D3）當實作參考 → **已完成 `mockups/smart-watchdog-v2.html`（2026-09-12，rivendell 風格、9 畫面 + 抽屜 + 匯出、OSM 地圖 1,216 點、真資料去識別）**。
- [x] **P6 管線與 bootstrap**：`update.py` 完整契約 + `PipelineError` + 固定 log；`ingest.py`（kiang 兩 JSON、新北公告、評鑑 spike；`raw-web/<date>/`）；`Makefile bootstrap`；`data/demo/watchdog-demo.sqlite`；OCR 產物 release asset；README Quickstart；data_asof = max(event date)。
- [ ] **P7 驗收**：`/gstack-review`（每 Phase 收尾）；`/qa-dataflow`（HARD GATE，target vs actual）；`/gstack-qa` + `/gstack-design-review`；`/gstack-careful` 於刪表前。
- [ ] **P8 deck**：storyline.md（Peter 主筆）→ `/slide-office-hours` signed-off → `/sales-deck-design` → `/de-slopify` → `/gstack-document-release`。首頁承認限制；主視覺＝關聯圖 + 覆蓋率曲線；數字用事件層 + ROI。

## What already exists（reuse，不重造）

- `scripts/build_db.py`（重寫成 DBBuilder，保留 kiang 解析）、`ocr_batch.py`、`parse_statements.py`（總計列錨定 + 恆等式）、`compare_ratios.py`（拆 reconcile/ratios）
- `~/code/Verdandi-OR/apps/api/engine/scheduling.py`：CP-SAT 建模習慣（max_time、seed、覆蓋型約束）
- `~/code/PTI-ARES/docs/design/2026-09-11-prepare-check-agentic-sd.md`：AgentService 唯讀工具集 + agent_turn 留痕
- `~/code/rivendell/dashboard-next/DESIGN.md`：設計系統；`~/.claude/skills/chart-design/styles/ntpc-smart-watchdog.md`：圖表樣式（改森林綠）
- `docs/design/diagrams/*`：四張 target 圖（qa-dataflow 對照用）

## NOT in scope（明列，防 silent drop）

- 社群輿情特徵（TODOS；只允許 P8 一頁 spike 若時間有餘）
- 承辦回饋回流成標籤（TODOS）
- 多使用者、登入、稽核日誌、排程自動執行、部署到政府環境
- 私立園財務（無公開資料）；把連坐當模型特徵；LLM 寫入任何判定／名單／排程
- 手機版、深色模式、多輪對話記憶
- Postgres／多程序

## Failure modes（新路徑逐條）

| 路徑 | 生產失敗情境 | 測試 | 錯誤處理 | 使用者可見？ |
|---|---|---|---|---|
| src_ 重建 | kiang 改欄位 / 筆數驟降 / 中斷 | P0 ★ | schema 斷言、>20% 中止、單交易 | CLI PipelineError ✅ |
| 事件去重 | 同案多法條算兩次 | P0 ★ | 園×日期 | 數字正確 ✅ |
| 特徵 | asof 後資料混入 | P2 ★ | assert 中止 | CLI ✅ |
| 訓練 | 正例 < 50 / 標籤窗超出 | P2 | 不產版本 + 警示 | 設定頁 ✅ |
| 核准 | 兩個 active | P2 ★ | partial unique index | 不可能 ✅ |
| 評分 | 批次中斷半新半舊 | P3 ★ | score_batch 交易切換 | 一致 ✅ |
| 排程 | 容量 0 / 不可行 / 逾時 / 停辦園 | P3b | 422 原因 / 近似解旗標 / 排除 | 排程頁 ✅ |
| 問答 | 注入 / 非 SELECT / 姓名欄 / LLM 掛 | P4b ★ | authorizer + AST + LIMIT + 降級 | 拒答／稍後再試 ✅ |
| 匿名化 | 白名單漏欄 | P4 ★ | v_* view + 性質測試 | — ✅ |
| 匯出 | 空名單 | P4 | 422 | 「無資料」✅ |
| bootstrap | 無 raw / 無 Vision / 無 key | P6 | --skip-ocr、問答 disabled | 仍可跑 ✅ |

無「無測試＋無處理＋靜默」的 critical gap。★ = REGRESSION 級。

## Parallelization

| Step | Modules | Depends on |
|---|---|---|
| P0 | scripts/db, events, tests/, pyproject | — |
| P1 | scripts/linker | P0 |
| P2 | scripts/features, train, approve, roi | P0, P1 |
| P3 | scripts/score | P2 |
| P3b | scripts/schedule | P3 |
| P4 | app/ | P1–P3b 契約（§4 先凍結） |
| P4b | app/agent | P4 |
| P5 | web/, mockups/, DESIGN.md | P4 契約 + D1/D2/D3 |
| P6 | scripts/update, ingest, Makefile | P0 |

Lane A: P0→P1→P2→P3→P3b｜Lane B: P6（只依 P0）｜Lane C: D1→D2→D3（設計，獨立）→ P4→P4b→P5（等 A 的 §4 凍結）。A 與 B、C 前段可平行；衝突點：A 與 C 都碰 §4 契約 → P3 前凍結。

## Implementation Tasks（autoplan 彙整 30 條，依 P 排）

- [ ] **T1 (P1, CC ~2h)** — scheduler — CP-SAT 排程 + 人力 settings + schedules 表 + 釘選/排除重解｜E1/Codex#6｜Verify: 假設人力產生行程、容量 0 → 422
- [ ] **T2 (P1, CC ~30min)** — features — 弱訊號特徵 + 無紀錄 + 兒安燈號｜E2/E3/E12｜Verify: 無裁罰史園 method=none
- [ ] **T3 (P1, CC ~2h)** — agent — AgentService 唯讀四工具 + agent_turns + 匿名化｜E16/S3.1｜Verify: 三題成功、四類拒答
- [ ] **T4/E9 (P1, CC ~2h)** — tests — pytest + Playwright + 7 條 REGRESSION 級｜S6.1｜Verify: 全綠
- [ ] **T5 (P1, CC ~15min)** — data — kiang 覆蓋率驗證｜Claude F5｜Verify: findings.md 有覆蓋率
- [ ] **T7 (P1, CC ~1h)** — backtest — ROI 重播｜E9｜Verify: 多抓幾家／少跑幾趟數字
- [ ] **T10 (P1, CC ~20min)** — docs — SD 補三表、risk_01、失敗表、權限段、§8 SOP 假設（本次已做）
- [ ] **D1 (P1, CC ~30min)** — design-system — 專案 DESIGN.md + chart style（本次已做）
- [ ] **D2 (P1, CC ~1h)** — mockup — 換膚重排：英雄覆蓋率、側欄分區、理由白話句、立案別純文字、等級色點、無陰影
- [ ] **D3 (P1, CC ~1h)** — mockup — 畫面 9 名單、10 排程、問答抽屜
- [ ] **D4 (P1, CC ~20min)** — spec — 10×5 狀態表 + desktop 1440 + a11y 進 requirement（本次已做）
- [ ] **E1 (P1, CC ~1h)** — db — DBBuilder src_/app_ 重寫
- [ ] **E2 (P1, CC ~20min)** — events — penalty_events + 數字修正（文件部分本次已做）
- [ ] **E3 (P1, CC ~1h)** — features — 觀察點 ≥ reg_date、31–365、事件史/快照、洩漏 assert
- [ ] **E4 (P1, CC ~1h)** — train — walk-forward gap 365、feature_hash、holdout、seed
- [ ] **E5 (P1, CC ~30min)** — score — score_batch 交易、絕對門檻 + 前 N
- [ ] **E6 (P1, CC ~30min)** — linker — 持久碼、同名、兩層 watchlist
- [ ] **E7 (P1, CC ~2h)** — schedule —（併 T1）
- [ ] **E8 (P1, CC ~1h)** — agent — sql_readonly 安全 + v_* view（併 T3）
- [ ] **X1 (P1, CC ~30min)** — bootstrap — pyproject、Makefile、README Quickstart、.env.example
- [ ] **X2 (P1, CC ~20min)** — demo-data — demo SQLite 入 repo；OCR 產物 release asset
- [ ] **X3 (P1, CC ~30min)** — cli — update.py 契約 + PipelineError + log
- [ ] **T6 (P2, CC ~1h)** — finance — 內控查核表 + 補助依賴度
- [ ] **T8 (P2, CC ~1h)** — ui — 八張圖表
- [ ] **T9 (P2, CC ~30min)** — llm — 稽查重點三行
- [ ] **D5 (P2, CC ~1h)** — charts — 圖表規格進 SD §4
- [ ] **E10 (P2, CC ~15min)** — ops — data_asof、Demo-Token
- [ ] **X4 (P2, CC ~20min)** — api — /api/v1、cap、envelope、409
- [ ] **X5 (P2, CC ~20min)** — repro — raw-web/<date>、schema_version、--zip-dir

## Key Decisions

- **2026-09-12（Peter 一連串產品裁示，皆已實作）**：園名用真名、負責人姓名顯示（公開登記資料；行為人／教保人員姓名仍不出）；「連坐」一詞改「同負責人」；裁罰事件寫具體違規（`scripts/law_labels.py`）不寫條號；排名表用 v1 精簡版（分數 0–100、全部 1,216 園、分頁）；分數要是模型 → 核准門檻改「單一指標勝過 0.02 且另一指標不落後」，邏輯迴歸 #3 核准為 active（AUC 0.640 vs 0.606），規則永遠是 fallback；維護區新增「模型」頁（訓練／核准／撤銷／留痕）；詳情頁 hero 先放分數／機率／排名／裁罰數，關聯圖下移；新增輿情分析（Google 新聞 RSS + 關鍵字語氣，Google 評價需 `GOOGLE_MAPS_API_KEY`），`sentiment_batch.py --top 100`；排程加各區配額與目標預設（風險優先／併訪／均衡，參考 Verdandi-OR）；本季名單加分數、根因（36 月違規類型）與稽查重點三行；字級整體放大（body 16px）。

- **2026-09-12 P5 前端改為 FastAPI 靜態單頁（web/index.html + 內嵌 SVG 圖表）而非 Next.js**：mockup v2 已是可執行前端、rivendell 規定圖表用內嵌 SVG、`make bootstrap ≤ 5 分鐘` 不需 node；設計系統與畫面完全相同。若日後要多人協作再移植 Next.js。

| Decision | Rationale | Date |
|----------|-----------|------|
| 回頭客（再犯）當主軸，規則為主模型驗證 | 事件層回頭客 48% 貢獻 75%；文獻與兩模型：規則 ≈ 模型 | 2026-09-12 |
| 觀察點事件 +1 天 + 季末；標籤 31–365 天 | Peter 決定裁罰後起算；同案 30 天內不算再犯 | 2026-09-12 |
| 連坐只加旗標、分兩層 | 連坐 11% vs 基準 7%；政治風險 | 2026-09-12 |
| 財報＝每園四面向燈號（取代三燈） | Peter：至少要對每園標財務風險，含收入／成本／資債趨勢；群體比較（10 vs 28）無訊號，改逐園規則 | 2026-09-12 |
| 財報接線走封面完整園名 | 7 園換法人有兩筆同名 id，短名前綴抓錯 | 2026-09-12 |
| SQLite 單檔、src_/app_ 分族 | ≤ 10k 主檔列、單使用者 | 2026-09-12 |
| CP-SAT 人力排程、軟覆蓋硬容量 | Peter 指示；命題第三效益量化 | 2026-09-12 |
| Agentic 唯讀問答 | Peter 指示；LLM=0 競爭風險 | 2026-09-12 |
| rivendell 設計系統 + 畫面主線 5 | Peter 指示；四聲道一致 | 2026-09-12 |
| P2 回測先於畫面 | 數字決定 deck 主張 | 2026-09-12 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| 全國教保資訊網 punishSearch POST 500 | 1 | 改新北公告 + kiang 備份；評鑑 POST 留 P6 spike |
| pandas groupby.apply 與 `name` 欄衝突 | 1 | `g["name"]` |
| draw_sd_diagrams 文字重疊 ×6、FK 箭頭指錯 | 2 | 四角環排法；FK 左側 lane |
| 列層裁罰數字灌水（1,474 列 vs 1,004 事件） | 1 | 事件層重算；deck 改數字 |
| gstack designer 無 OpenAI 影像金鑰 | 1 | 視覺變體跳過，文字規格 + 現有截圖 |

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR (via /autoplan) | 16 proposals, 13 accepted, 2 deferred, 1 user challenge → accepted |
| Codex Review | `/codex review` | Independent 2nd opinion | 4 (voices) | issues folded | CEO 10 / Design 6 HR / Eng 11 / DX 9 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR (PLAN via /autoplan) | 22 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | CLEAR (FULL via /autoplan) | score: 3/10 → 8/10, 24 decisions |
| DX Review | `/plan-devex-review` | Developer experience gaps | 1 | CLEAR (via /autoplan) | score: 3/10 → 7/10, TTHW: 120min → 5min |

- **CODEX:** 四階段 Codex 聲道共 36 條，全部裁定並折入計畫；無 Codex 獨立反對意見。
- **CROSS-MODEL:** 四階段 6/6 共識 × 4，0 分歧；六個跨階段主題全部進架構定調。
- **VERDICT:** CEO + DESIGN + ENG + DX CLEARED — ready to implement.

NO UNRESOLVED DECISIONS
