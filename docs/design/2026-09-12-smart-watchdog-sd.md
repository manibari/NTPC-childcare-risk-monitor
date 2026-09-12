---
feature: smart-watchdog
date: 2026-09-12
scale: full
triggers: [new-store, data-write, first-version]
requirement: "docs/requirements/smart-watchdog.md"
mockup: mockups/smart-watchdog.html
baseline: "首版，無 baseline（greenfield；既有 data/watchdog.sqlite 四張表為本設計的輸入，非宿主系統）"
adr_checked: ["無 docs/adr；無專案 CLAUDE.md；對照 ~/.claude/CLAUDE.md Right-size infra（≤10k rows → SQLite）"]
status: reviewed（2026-09-12 /gstack-autoplan 全數裁定；補丁見 §2 末、§5、§7、§8）
---

# Smart Watchdog 系統設計

> 一句話：在既有 SQLite 四張表之上，加一條「觀察點 → 特徵 → 模型版本 → 分數 → 連坐名單 → 本季名單 → 匯出」的管線，
> 每一步落一張表，前端只讀表；模型不優於規則時自動以規則排序。
> 依 2026-09-12 程式碼實查（`scripts/build_db.py`、`.schema` 於 `data/watchdog.sqlite`）。

## §1 Scope

**對映需求**：US-1 排名、US-2 詳情、US-3 關聯與連坐、US-4 回測、US-5 匯出、US-6 更新、US-7 總覽；營運基線 #2 #3 #4 #6。

| In Scope | Out of Scope |
|----------|-------------|
| 觀察點與特徵表（裁罰後起算 + 每季固定觀察點） | 社群輿情特徵（另立 spike） |
| 再犯模型訓練、時間切分回測、三條 baseline、版本核准 | 自動排程更新（手動 CLI） |
| 分數表（模型分或規則分，二擇一，記錄採用哪個） | 私立園財務 |
| 負責人／委辦法人解析、匿名代碼、連坐名單 | 把連坐當模型特徵 |
| 本季名單、匿名化匯出 | 多使用者、權限、稽核日誌 |
| feedback、pipeline_runs、settings 三張營運表 | API 金鑰、roadmap 頁 |
| FastAPI 唯讀 API + 少量寫入（名單、回饋、設定） | 部署到政府環境 |
| 基礎評鑑抓取（特徵，失敗可缺） | 收費公告抓取（月費已在主檔） |

**本次改動一句話摘要**：資料層不動，往上長一條可回測、可解釋、可重算的評分管線，並把每個中間產物存成表。

## §2 資料模型

### 現況（實查 `data/watchdog.sqlite`，由 `scripts/build_db.py` 每次整表重建）

| 表 / 實體 | 出處 | 關鍵欄位 | 目前誰寫 / 誰讀 |
|---|---|---|---|
| `preschools` | `build_db.py:14`，kiang `preschools.json` 鏡像 | `id` (uuid TEXT), `title`, `city`, `town`, `type`(私立/公立/非營利), `owner`, `operator`(委辦法人，從園名括號抽), `count_approved`, `monthly`, `pre_public`, `reg_date`, `lng/lat` | 寫：build_db；讀：compare_ratios、ad-hoc SQL |
| `penalties` | `build_db.py:21`，kiang `punish_all.json` | `preschool_id`, `date`(YYYY-MM-DD), `law`, `law_article`, `punishment`, `actor_role`(負責人/行為人), `actor_name` | 寫：build_db；讀：ad-hoc |
| `statements` | `parse_statements.py` → `compare_ratios.py` | `preschool_id`, `fiscal_year`, `bs_*`, `is_*`, `bs_ok`, `is_ok` | 寫：build_db；讀：compare_ratios |
| `ratios` | `compare_ratios.py` | `preschool_id`, `fiscal_year`, 13 個比率, `capacity` | 寫：build_db；讀：無（報告用） |
| view `ntpc_penalty_summary` | `build_db.py:52` | 新北 1,216 園裁罰彙總 | 讀：ad-hoc |

**實查注意**：`preschools` 無 PRIMARY KEY 約束、`penalties` 無自有主鍵、`monthly` 是 TEXT、無 `data_asof` 記錄。Delta 一併補。

### Delta

| 動作 | 表.欄位 | 型別 | Null | 預設 | 索引 | 誰寫 | 誰讀 |
|------|---------|------|------|------|------|------|------|
| `~` | `preschools.id` | TEXT → TEXT PRIMARY KEY | 否 | — | PK | build_db | 全部 |
| `~` | `preschools.monthly` | TEXT → INTEGER | 是 | NULL | — | build_db | features |
| `+` | `penalties.penalty_id` | INTEGER PK AUTOINCREMENT | 否 | — | PK | build_db | observations, 詳情 API |
| `+` | `penalties.is_safety` | INTEGER(0/1)，§30/§33/§43 | 否 | 0 | — | build_db | features, 詳情 |
| `+` | `statements.title`, `ratios.preschool_id` | 封面完整園名；由 title 對回 `preschools.id`（7 園換法人有兩筆同名） | 否 | — | (preschool_id, fiscal_year) | StatementParser / DBBuilder | 詳情 API finance |
| `+` | `finance_flags` | `preschool_id`, `code`, `fiscal_year`, `level`(紅/黃/綠/灰), `level_revenue/cost/balance/surplus`, `is_latest`, `direction`, `history`, `reasons` JSON, `dims` JSON, `metrics` JSON | level 否 | — | (preschool_id, fiscal_year) | DBBuilder（`finance.py` 規則，src_ 族整表重建） | 詳情 API finance、匯出 |
| `+` | `linkers` | `linker_id` INTEGER PK, `kind`('owner'/'operator'), `key_name` TEXT, `code` TEXT('O-017'/'L-004'), `n_schools` INT | key_name 否 | — | UNIQUE(kind,key_name) | Linker | watchlist, 關聯圖 API, 匯出 |
| `+` | `preschool_linkers` | `preschool_id`, `linker_id`, `same_name_flag` INT, `excluded_by_user` INT | 否 | 0 | (linker_id), (preschool_id) | Linker；`excluded_by_user` 由 API 寫 | watchlist, 關聯圖 |
| `+` | `observations` | `obs_id` INTEGER PK, `preschool_id`, `asof_date`, `trigger`('penalty'/'quarter'), `penalty_id` (nullable, trigger=penalty 時), 特徵欄（見下）, `label_repeat_12m` INT nullable, `label_available` INT | label 可 NULL（asof+365 > 資料截至） | — | (preschool_id, asof_date), (asof_date) | FeatureBuilder | Trainer, Scorer |
| `+` | `models` | `model_id` INTEGER PK, `trained_at`, `algo`, `params` JSON, `data_asof`, `n_train_obs`, `auc`, `pr_auc`, `top100_cov`, `baseline_count_auc`, `baseline_recency_auc`, `baseline_count_top100`, `beats_baseline` INT, `status`('trained'/'approved'/'rejected'/'active'/'superseded'), `notes` | — | status='trained' | (status) | Trainer 寫；Approver 改 status | Scorer, 回測 API, 設定頁 |
| `+` | `backtests` | `model_id`, `obs_year`, `n_obs`, `n_pos`, `auc`, `pr_auc`, `top50`, `top100`, `top200`, `lead_days_median`, `baseline` JSON | — | — | (model_id) | Trainer | 回測 API |
| `+` | `scores` | `score_id` INTEGER PK, `model_id` nullable, `preschool_id`, `asof_date`, `method`('model'/'rule_count'/'attribute'), `prob_12m` REAL nullable, `score` INT 0–100, `rank` INT, `level`('高'/'中'/'低'), `top_features` JSON, `is_current` INT | prob 在 rule/attribute 時 NULL | — | (asof_date, rank), (preschool_id, is_current) | Scorer | 排名/詳情/總覽 API, Exporter |
| `+` | `watchlist` | `preschool_id`, `asof_date`, `reason`('penalised'/'owner_link'/'operator_link'), `source_preschool_id`, `source_penalty_id`, `linker_id`, `is_current` INT | source_* 在 reason=penalised 時 NULL | — | (preschool_id, is_current) | Linker | 排名 API（連坐旗標）, 詳情, Exporter |
| `+` | `season_list` | `preschool_id` PK, `added_at`, `added_by` TEXT | — | 'demo' | — | API POST/DELETE | Exporter, 排名 API |
| `+` | `feedback` | `feedback_id` PK, `page`, `preschool_id` nullable, `text`, `created_at` | preschool 可 NULL | — | — | API POST | 無（未來標籤） |
| `+` | `pipeline_runs` | `run_id`, `stage`, `started_at`, `seconds`, `n_rows`, `n_failed`, `ok` INT, `message` | — | — | (run_id) | 每個管線階段 | 資料品質 API |
| `+` | `settings` | `key` TEXT PK, `value` TEXT | — | 見 §8 | — | API PUT、Pipeline（data_asof） | 全部 API |
| `+` | `evaluations` | `preschool_id`, `result` TEXT, `fetched_at` | — | — | (preschool_id) | Ingest（可失敗） | FeatureBuilder |
| `+` | `penalty_events`（autoplan Eng A2） | `event_id` PK, `preschool_id`, `date`；園×日期去重（1,474 列→1,004 事件） | 否 | — | (preschool_id, date) | DBBuilder | FeatureBuilder, 詳情（法條明細仍讀 penalties） |
| `~` | `scores` + `risk_01` REAL、`score_batch_id`、`method` 加 'none' | 排程輸入 0–1；整批交易切 current；無裁罰史 = none | — | — | (score_batch_id, rank) | Scorer | Scheduler, API |
| `~` | `models` + `feature_hash`, `eval_year`, `seed`；partial unique index `status='active'`；新增 `model_events` 留痕 | 版本可比、單一 active | — | — | ux_active | Trainer/Approver | Scorer |
| `+` | `schedules` | `schedule_id` PK, `score_batch_id`, `asof_date`, `params` JSON(n_inspectors, visits_per_inspector_week, quarter_weeks, seed), `solver_status`, `objective`, `coverage_pct`, `is_current`, `is_stale` | — | — | (is_current) | Scheduler | 排程 API, Exporter |
| `+` | `schedule_visits` | `schedule_id`, `preschool_id`, `week_no`, `inspector_no`, `rank`, `reason`, `pinned` INT | — | 0 | (schedule_id, week_no) | Scheduler | 排程頁, Exporter |
| `+` | `agent_turns` | `turn_id` PK, `session_id`, `page`, `question`, `answer`, `tool_calls` JSON, `latency_ms`, `created_at` | — | — | (session_id) | AgentService | 資料品質頁 |
| `~` | 所有表加前綴：來源表 `src_*`（整表重建）、持久表 `app_*`（永不 drop）；`linkers` 改為持久（`app_linkers`，首次配碼永不回收） | autoplan Eng A1/A8 | — | — | — | — | — |

**`observations` 特徵欄**（全部由 `penalties` / `preschools` / `linkers` 在 `asof_date` **之前**的資料算出）：
`n_pen_total`, `n_pen_12m`, `n_pen_24m`, `days_since_last`(無則 9999), `n_safety`(§30/33/43), `n_law_8`, `n_law_16`, `n_law_26`, `n_actor_person`(行為人筆數), `n_stop_enroll`(停止招生次數), `owner_n_schools`, `owner_n_pen_12m_other`(同負責人其他園), `operator_n_pen_12m_other`, `type`, `town`, `count_approved`, `monthly`, `is_pre_public`, `age_years`(asof − reg_date), `eval_result`(nullable)。

**觀察點規則**：(a) 每筆裁罰日期 +1 天一個觀察點（裁罰後起算，Peter 決定）；(b) 每季末（3/6/9/12 月底）全部 1,216 園各一個觀察點，供「無裁罰史」園評分與 top-N 覆蓋率計算。標籤：`asof_date` 後 365 天內該園有無新裁罰；`asof+365 > data_asof` 則 `label_available=0`，訓練與回測排除。

**遷移策略**：SQLite 每次整表重建（沿用 build_db 作法），無 backfill 問題；`models`/`scores`/`feedback`/`season_list`/`settings`/`pipeline_runs` 六張為**持久表**，重建時保留（build_db 改為只重建來源表）。Rollback = 換回上一個 `models.status='active'`。

**ER 圖**：`diagrams/er-smart-watchdog-2026-09.html`（[png](diagrams/er-smart-watchdog-2026-09.png)）— 回答：新表之間靠哪個鍵串起來、哪幾張會被重建、哪幾張要保留。

## §3 模組拆分與職責邊界

| 模組 | 負責 | **不負責** | 依賴 |
|------|------|-----------|------|
| `Ingest`（`scripts/ingest.py`） | 抓 kiang 兩個 JSON、新北裁罰公告、全國網基礎評鑑；寫 `data/raw-web/*.json` + `pipeline_runs` | 不解析成表（那是 `DBBuilder`）；不重試超過 3 次；抓不到就標失敗、沿用上次檔 | 網路、三個公開站 |
| `OCR`（既有 `ocr_batch.py`） | 掃描 PDF → `data/ocr/*.jsonl`，已有輸出跳過 | 不解析科目 | pdftoppm、Vision |
| `StatementParser`（既有 `parse_statements.py` + `compare_ratios.py`） | jsonl → statements/ratios CSV，恆等式剔錯 | 不判斷風險；不碰裁罰 | OCR 輸出 |
| `DBBuilder`（既有 `build_db.py`，改） | 重建來源表（preschools/penalties/statements/ratios/evaluations），補主鍵與 `is_safety`；**不動**持久表 | 不算特徵、不算分數 | raw-web、CSV |
| `Linker` | 解析負責人／委辦法人 → `linkers`、`preschool_linkers`；同名判定；產 `watchlist` | 不改分數；不做人名消歧以外的推論 | preschools, penalties, settings(觀察期) |
| `FeatureBuilder` | 產 `observations`（觀察點 + 特徵 + 標籤），保證只用 asof 之前資料 | 不選特徵、不訓練 | penalties, preschools, linkers, evaluations |
| `Trainer` | 時間切分訓練、回測、三 baseline、寫 `models`(status=trained) + `backtests`；判 `beats_baseline` | 不決定是否上線（那是 `Approver`）；不算當期分數 | observations |
| `Approver` | 規則：首版自動 approve；之後 AUC 較 active 降 >0.05 → 留 trained 並警示；否則 approve → active，舊版 superseded | 不重訓 | models, settings |
| `Scorer` | 用 active model 對 `data_asof` 當天觀察點評分；`beats_baseline=0` 時改用 `rule_count`；無裁罰史園用 attribute 分；寫 `scores(is_current)` + 等級 + top_features | 不產連坐（`Linker`）；不決定門檻（`settings`） | models, observations, settings |
| `Exporter` | 依篩選 + season_list 產 CSV/xlsx，欄位白名單，負責人以 `linkers.code` | 不含任何姓名欄；不寫 DB | scores, watchlist, season_list, linkers |
| `API`（FastAPI `app/`） | 唯讀查詢 + 四個小寫入（season_list、feedback、settings、preschool_linkers.excluded_by_user） | 不跑管線（CLI）；不算分數 | SQLite |
| `Web`（Next.js + antd，8 畫面） | 呈現、篩選、加入名單、觸發匯出 | 不做任何計算；不直接讀 SQLite | API |
| `Pipeline`（`scripts/update.py`） | 依序呼叫 Ingest → OCR → StatementParser → DBBuilder → Linker → FeatureBuilder → Trainer → Approver → Scorer；每階段寫 `pipeline_runs`；更新 `settings.data_asof` | 不提供 HTTP 入口 | 以上全部 |

**元件圖**：表格代替 —— 12 個模組、單機、依賴關係已在表中；部署只有一台機器一個 SQLite。

## §4 介面契約

共通：所有回應 JSON；錯誤形狀統一 `{ "error": { "code": string, "message": string, "detail"?: object } }`；呼叫端以 `error.code` 分辨。時間一律 `YYYY-MM-DD`。

### `GET /api/overview` — 總覽 KPI 與四張小圖（US-7）
- **前置**：`scores.is_current` 存在；否則回 `NO_SCORES`
- **輸入**：無
- **成功**：`{ data_asof, n_schools, n_watch, n_watch_penalised, n_watch_linked, n_repeat, n_pen_12m, by_town:[{town,n_repeat,n_pen,n}], by_type:[{type,n,n_pen,rate,n_repeat}], by_year:[{year,n}], by_law:[{article,label,n,is_safety}], model:{model_id,method,auc,beats_baseline} }`
- **錯誤**：`NO_SCORES` 503 → 前端顯示錯誤卡 + 重新計算說明；`DB_MISSING` 503
- **冪等**：純讀

### `GET /api/rankings` — 風險排名（US-1）
- **輸入 query**：`town?`, `type?`（私立|公立|非營利|準公共）, `level?`（高,中,低 逗號）, `linked?`（true|false）, `q?`（園名 substring）, `page=1`, `size=50`
- **成功**：`{ data_asof, stale: bool, total, page, size, items:[{ preschool_id, title, town, type, score, level, method, prob_12m?, n_pen, last_penalty?, count_approved, linker_code?, link_reason?, in_season_list, is_attribute_only }] }`
- **錯誤**：`BAD_FILTER` 400（未知 level/type）；`NO_SCORES` 503
- **冪等**：純讀；`stale` = data_asof 距今 > settings.stale_days

### `GET /api/preschools/{id}` — 園所詳情（US-2）
- **前置**：`id` 來自 rankings
- **成功**：`{ preschool:{…主檔, owner_code, operator_code, eval_result?}, score:{score, level, method, prob_12m?, rank, top_features:[{name,contribution}], is_attribute_only}, penalties:[{penalty_id,date,law_article,law,punishment,actor_role,is_safety}], watch:{reason, source:{preschool_id,title,penalty_date,linker_code}}|null, finance:{fiscal_year, ratios:{人事費率,每核定名額收入,流動比,負債比}, operator_peers:[{title, 人事費率,…}]}|null, season_list:bool }`
- **錯誤**：`NOT_FOUND` 404；`finance` 為 null 時前端隱藏財務區（US-2 AC3）
- **冪等**：純讀；回應**絕不含** `actor_name`、`owner`、`operator` 原文（只給 code）

### `GET /api/linkers/{code}/graph` — 負責人／法人關聯圖（US-3）
- **成功**：`{ linker:{code, kind, n_schools, n_pen_total, n_pen_schools}, nodes:[{preschool_id,title,town,n_pen,last_penalty,level,in_watch,same_name_flag,excluded}], watch_rule:{window_months} }`
- **錯誤**：`NOT_FOUND` 404
- **冪等**：純讀

### `PUT /api/linkers/{code}/schools/{preschool_id}` — 排除同名園
- **輸入**：`{ excluded: bool }`
- **成功**：`{ ok: true, watchlist_recomputed: true }`（同步重算該 linker 的 watchlist 列）
- **錯誤**：`NOT_FOUND` 404；`NOT_SAME_NAME` 409（非同名待確認的園不可排除）
- **冪等**：是；重送同值無變化

### `GET /api/backtest` — 回測儀表（US-4）
- **成功**：`{ active:{model_id, algo, data_asof, auc, pr_auc, top:{50,100,200}, lead_days_median, beats_baseline}, baselines:{random:{50,100,200}, count:{auc,50,100,200}, recency:{auc,50,100,200}}, by_year:[{obs_year,n_obs,n_pos,auc,top100}], pending:{model_id,auc,delta}|null }`
- **錯誤**：`NO_MODEL` 503（尚未訓練）→ 前端顯示「示意」版面
- **冪等**：純讀

### `POST /api/season-list` / `DELETE /api/season-list/{preschool_id}` — 本季名單
- **輸入**：`{ preschool_id }`
- **成功**：`{ ok:true, n_season:int }`
- **錯誤**：`NOT_FOUND` 404
- **冪等**：POST 重送 = no-op（PK）；DELETE 不存在也回 200

### `GET /api/export?format=csv|xlsx&…同 rankings 篩選&scope=season|filtered` — 匯出（US-5）
- **成功**：檔案下載；欄位固定：園名、行政區、立案別、分數、等級、連坐旗標、最近裁罰日期、裁罰次數、負責人代碼
- **錯誤**：`EMPTY_LIST` 422（名單為空，不產檔）；`BAD_FORMAT` 400
- **冪等**：純讀（不記錄匯出；稽核日誌延後）

### `GET /api/data-quality` — 資料品質（US-6，ops #2）
- **成功**：`{ tables:[{name,n}], sources:[{name,last_ok,n,status,message}], last_run:{run_id,started_at,seconds,stages:[{stage,n_rows,seconds,n_failed,ok}]}, ocr:{n_files,n_school_years,bs_ok,is_ok}, missing_owner:int, pending_model:{…}|null }`
- **冪等**：純讀

### `GET /api/settings` / `PUT /api/settings` — 設定（ops #6）
- **PUT 輸入**：`{ high_threshold?, mid_threshold?, top_n_default?, watch_window_months?, stale_days? }`（匿名化不可關）
- **成功**：完整 settings + `version:{app, data_asof, active_model}`
- **錯誤**：`BAD_VALUE` 400（門檻需 0–100 且高>中）
- **冪等**：是

### `POST /api/feedback` — 回饋（ops #4）
- **輸入**：`{ page, preschool_id?, text (1–2000) }`
- **成功**：`{ feedback_id }`
- **錯誤**：`BAD_VALUE` 400
- **冪等**：否（每次新增一筆；前端防連點）

### CLI `python scripts/update.py [--from STAGE] [--no-fetch] [--no-train]` — 更新管線（US-6）
- **輸出**：每階段一行 log + `pipeline_runs`；exit 0 全成功、2 有階段失敗但已沿用舊資料、1 中止（DBBuilder 檢查未過）
- **冪等**：是（整表重建；OCR 跳過已有輸出；模型版本以 `data_asof` 去重，同日重跑不新增版本）

## §5 關鍵流程

### 更新管線（會出錯的那條）

**序列圖**：`diagrams/sequence-update-2026-09.html` — 表格代替（單一 actor 串接九個階段，錯誤分支只有三種，表格更清楚）。

| 階段 | 失敗情況 | 處置 | 後續階段 |
|---|---|---|---|
| Ingest | 某來源 HTTP 失敗 / 解析失敗 | 沿用 `data/raw-web/<source>.json` 上次檔；`pipeline_runs.ok=0`；資料品質頁標示 | 繼續 |
| DBBuilder | 筆數比上次少 >20%，或 preschools 無新北資料 | **中止**（exit 1），保留舊 DB 檔（先寫 `.tmp` 再 rename） | 停 |
| FeatureBuilder | 標籤全部不可用（資料截至太舊） | 中止訓練，僅重算分數用舊 active model | 跳過 Trainer |
| Trainer | 正例 < 50 | 不產新版本，警示 | 跳過 Approver |
| Approver | AUC 較 active 降 > 0.05 | 新版留 `trained`，不切換；設定頁顯示待核准 | 繼續（Scorer 用舊 active） |
| Scorer | active 的 `beats_baseline=0` | method 改 `rule_count`，儀表如實顯示 | 繼續 |
| Scheduler | 容量 0 / INFEASIBLE / UNKNOWN / 逾時 20s | 422 帶原因；逾時回可行解 + `is_stale`/近似旗標；換 score_batch 標 stale | 繼續 |
| AgentService | Claude 429/5xx；非 SELECT / ATTACH / 姓名欄；無根據 | 退避 1 次→「稍後再試」；authorizer 拒→「超出範圍」；拒答列可問範例；無 key → 抽屜 disabled | 不影響其他功能 |

### 模型版本狀態機

| 從 | 到 | 誰觸發 | 條件 |
|----|----|--------|------|
| — | trained | Trainer | 回測完成，寫入 backtests |
| trained | approved | Approver | 無 active，或 AUC ≥ active.auc − 0.05 |
| trained | rejected | Approver / 人 | AUC 降幅 > 0.05 且人未核准（demo 只警示，不自動 reject） |
| approved | active | Scorer 啟動 | 一次只能一個 active |
| active | superseded | 新版 active | 自動 |
| superseded | active | 人（rollback） | 手動 SQL / 未來設定頁 |

## §6 圖組閱讀鏈

1/4 `diagrams/dataflow-modules-target-2026-09.html`（[png](diagrams/dataflow-modules-target-2026-09.png)）— 有哪些模組、哪個沒做（既有 3、要改 1、待建 10）。

## §6a 功能關係圖（target）

檔：`diagrams/dataflow-functions-target-2026-09.html`（[png](diagrams/dataflow-functions-target-2026-09.png)）— 2/4 誰是誰的前置、交出去什麼。

**① 帶表**

| 帶 | 帶進來的是 | **不隨行的是** | 交出去的是 |
|----|-----------|--------------|-----------|
| A 整備 —— 把三個網站與一疊 PDF 變成可查詢的表 | 網頁 JSON、掃描 PDF | 沒有評鑑（可能抓失敗）、沒有私立園財務 | `preschool_id`、`penalty_id`、`fiscal_year` |
| B 建模 —— 把裁罰史變成「未來 12 個月會不會再犯」的版本化模型 | `penalty_id` 序列、園所屬性 | 行為人姓名、負責人姓名（特徵只用計數） | `obs_id`、`model_id`(active) |
| C 評分與名單 —— 每園一個分數、一個旗標 | `model_id`、`asof_date`、`linker_id` | 財務比率（不進分數） | `score_id`、`watchlist` 列、`season_list` |
| D 交付 —— 承辦看得懂、拿得走 | `score_id`、`linker.code` | 任何姓名 | CSV / xlsx → 人 |

**② 識別碼鏈**

```
公開網站/PDF  --(json, jsonl)-->     A 建庫
A 建庫        --(preschool_id)-->    B 觀察點與特徵
A 建庫        --(penalty_id)-->      B 觀察點與特徵（裁罰後起算）
A 建庫        --(owner/operator)-->  C 負責人／法人勾稽
B 觀察點      --(obs_id)-->          B 訓練與回測
B 訓練        --(model_id, trained)--> B 版本核准
B 版本核准    --(model_id, active)--> C 重算分數
B 觀察點      --(obs_id @ data_asof)--> C 重算分數
settings      --(門檻, 觀察期)-->    C 重算分數 / C 連坐名單
C 勾稽        --(linker_id)-->       C 連坐名單
C 重算分數    --(score_id)-->        D 排名 / 詳情 / 總覽
C 連坐名單    --(watchlist 列)-->    D 排名旗標 / 關聯圖
D 排名        --(preschool_id)-->    C 本季名單
C 本季名單    --(preschool_id 集合)--> D 匯出
D 匯出        --(CSV)-->             【承辦】排下季稽查行程
```

**③ 回頭路**

| 回頭路 | 從哪回到哪 | 帶什麼 | 觸發原因 |
|---|---|---|---|
| 資料過期重跑 | D 排名（stale 警示）→ A 抓公開資料 | 無（整條重跑） | data_asof > stale_days |
| 新裁罰進來 | A 建庫 → B 觀察點 | 新 `penalty_id` | 每次更新 |
| 模型退步不切換 | B 版本核准 → B 訓練（留 trained） | `model_id` 待核准 | AUC 降 > 0.05 |
| 模型輸給規則 | B 版本核准 → C 重算分數（改 rule_count） | `beats_baseline=0` | 回測 |
| 同名排除 | D 關聯圖 → C 連坐名單 | `preschool_id`, `excluded` | 承辦手動 |
| 承辦回饋 | D 任一頁 → feedback 表（未來回到 B 標籤） | `preschool_id`, 文字 | 承辦覺得排名不對 |

**④ 旁掛（虛線唯讀）**

- 財務燈號：DBBuilder 依 `finance.py` 規則從 `ratios` 算出 `finance_flags`（src_ 族、每次重建、四面向＋總燈號＋理由），詳情頁讀它 + `linkers(operator)` 做同法人比較；**不進分數、不進 watchlist**。
- 資料品質頁：讀 `pipeline_runs`、各表 COUNT、OCR 恆等式，**不產生新實體**。
- 回測儀表：讀 `models`、`backtests`，不產生新實體。

**⑤ 終點是人做了什麼**

- 匯出的名單 → 承辦排下一季稽查行程；總覽 → 科長分配各區人力。

## §6b 資料實體流（target）

檔：`diagrams/dataflow-entities-target-2026-09.html`（[png](diagrams/dataflow-entities-target-2026-09.png)）— 3/4 資料躺在哪、誰產生誰。

| 實體 | **躺在哪** | 由誰產生（動作） | 下一步誰拿它當輸入 | 生命週期 / 何時失效 |
|---|---|---|---|---|
| 網頁快照 | `data/raw-web/{preschools,punish_all,ntpc_penalty,evaluations}.json` | Ingest --(抓取)--> | DBBuilder | 每次更新覆蓋；失敗時沿用 |
| OCR 頁 | `data/ocr/<stem>.jsonl` | OCR --(辨識)--> | StatementParser | 永久（已有跳過） |
| Preschool | SQLite `preschools` | DBBuilder --(整表重建)--> | FeatureBuilder, Linker, API | 每次更新重建 |
| Penalty | `penalties`（`penalty_id`） | DBBuilder --(整表重建)--> | FeatureBuilder（觀察點）, API | 每次更新重建 |
| Statement / Ratio | `statements`, `ratios` | StatementParser --(解析+剔錯)--> DBBuilder | 詳情頁財務燈號（唯讀） | 每次更新重建 |
| FinanceFlag | `finance_flags` | DBBuilder --(finance.py 規則、同儕中位數)--> | 詳情頁財務燈號、匯出 | 每次更新重建；不進分數 |
| Evaluation | `evaluations` | Ingest --(抓取)--> DBBuilder | FeatureBuilder | 可缺 |
| Linker / PreschoolLinker | `linkers`, `preschool_linkers` | Linker --(解析 owner/operator)--> | watchlist, 關聯圖 API, Exporter(code) | 重建，但 `excluded_by_user` 由 Linker 依 (kind,key_name,preschool_id) 保留 |
| Observation | `observations` | FeatureBuilder --(切觀察點、算特徵、貼標籤)--> | Trainer, Scorer | 每次更新重建 |
| Model + Backtest | `models`, `backtests`（持久） | Trainer --(訓練+回測)--> | Approver → Scorer, 回測 API | 版本永久保留；status 流轉 |
| Score | `scores`（持久，`is_current`） | Scorer --(用 active model 或規則評分)--> | 排名/詳情/總覽 API, Exporter | 新一批寫入時舊批 `is_current=0` |
| WatchlistEntry | `watchlist`（`is_current`） | Linker --(連坐規則)--> | 排名旗標, 詳情, Exporter | 同上 |
| SeasonListEntry | `season_list`（持久） | 承辦 --(加入)--> API | Exporter | 承辦刪除或下季清空 |
| 匯出檔 | 瀏覽器下載，不落地 | Exporter --(白名單欄位)--> | 人 | 回應完即消失 |
| Feedback | `feedback`（持久） | 承辦 --(回饋)--> API | 未來 FeatureBuilder 標籤 | 永久 |
| PipelineRun | `pipeline_runs`（持久） | 每階段 --(記錄)--> | 資料品質 API | 永久 |
| Setting | `settings`（持久） | API PUT / Pipeline(data_asof) | Scorer, Linker, API | 永久 |

**實體鏈**

```
網頁快照 ─(重建)→ Preschool ─┬─(解析負責人/法人)→ Linker ─(連坐)→ WatchlistEntry ─┐
                              │                                                  ├→ 匯出檔 → 人
Penalty ──(切觀察點+標籤)→ Observation ─(訓練)→ Model(+Backtest) ─(核准 active)→ Score ─┘
    ↑                                              ↑                    ↑
    └───(新裁罰, 下次更新)──── 人 ─(回饋)→ Feedback ─(未來標籤)┘        └(退步不切換: 留 trained)
OCR 頁 ─(解析)→ Statement/Ratio ┈┈(唯讀, 財務燈號)┈┈▷ 詳情頁
SeasonListEntry ←(加入)─ 人 ←(看排名)─ Score
```

**回頭箭頭**

| 從 | 回到 | 觸發 | 舊實體怎麼辦 |
|---|---|---|---|
| Score（stale） | 網頁快照 | data_asof 過期 | 舊 scores `is_current=0`，保留 |
| Model(trained) | 不切換 | AUC 降 > 0.05 | 舊 active 保留；新版 status=trained 等人 |
| WatchlistEntry | Linker | 同名排除 | 該 linker 的列重算，其餘不動 |
| Feedback | Observation（未來） | 承辦標「誤判」累積 | 不改現有標籤；另加 `label_source` 欄（未來） |

**唯讀旁掛**：財務燈號讀 `finance_flags`（DBBuilder 重建時從 `ratios` 導出，不進分數）；資料品質讀 `pipeline_runs`；回測儀表讀 `models/backtests` —— 後兩者**不產生新實體**。

> **與 §2 對帳**：§2 Delta 的每張新表（linkers, preschool_linkers, observations, models, backtests, scores, watchlist, season_list, feedback, pipeline_runs, settings, evaluations）都在上表；上表的「網頁快照」「OCR 頁」「匯出檔」不是表，已標躺在檔案系統或不落地。

## §7 NFR 與反證關卡

| 項目 | 值 | 依據 |
|------|-----|------|
| 預期資料筆數 | preschools 7,688（新北 1,216）；penalties 7,065；observations ≈ 1,474 + 1,216 × 季數(≈36) ≈ 45k；scores 1,216/批 | 實查 DB + 觀察點規則 |
| 併發使用者 | 1–3（承辦 + 評審 demo） | 需求 |
| 單次請求資料量 | rankings 一頁 50 列 < 20 KB；graph ≤ 20 節點 | 設計 |
| 外部依賴 | kiang GitHub Pages、kidedu.ntpc.edu.tw、ap.ece.moe.edu.tw | 掛掉 → 沿用上次快照，資料品質頁標示；不影響評分 |
| 儲存 | 單檔 SQLite（≤ 50 MB） | `~/.claude/CLAUDE.md` Right-size：≤10k 主檔列、≤20 使用者 → SQLite |
| 訓練時間 | ≤ 2 分鐘（45k 列、~20 特徵、GBDT） | 估 |

**權限**：單使用者 demo，無登入；API 綁 localhost（Tunnel demo 時寫入端點需 `X-Demo-Token`）；匿名化在**架構層**：API 與 AgentService 只讀 `v_*` 去識別 view（無 actor_name/owner/operator/tel/address），Exporter 白名單再擋一次；demo 一律 `anonymize_titles`。

### 反證關卡（teeth）

圖形版：`diagrams/dataflow-gates-target-2026-09.html`（[png](diagrams/dataflow-gates-target-2026-09.png)）— 4/4 關卡擋不擋得住。

| 關卡 | 設計上擋什麼 | 狀態 | 擋不住什麼 / 怎麼被跨過去 |
|------|------------|------|------------------------|
| 時間切分（`label_available`, 特徵只用 asof 之前） | 標籤洩漏、未來資訊進特徵 | ✓ | 擋得住裁罰序列洩漏；擋不住主檔屬性本身是「現在」的快照（核定人數、負責人可能已變）—— 記進 §8 |
| 模型 vs baseline 自動切換 | 模型比「按次數排序」差時仍拿模型排 | ✓ | 只比 AUC/top100；若模型只在某些年好，仍可能整體切換 |
| Approver AUC 降 > 0.05 不切換 | 壞版本上線 | ◐ | 只警示不阻擋人手動核准；demo 無登入，任何人都能核准 |
| 恆等式剔錯（BS/IS） | OCR 數字誤讀進財務燈號 | ✓ | 擋不住兩份報告同一位置同錯（機率低） |
| 連坐同名旗標 | 同名不同人被連坐 | ◐ | 只標示，仍列入名單直到人排除；地址距離門檻未定（§8） |
| 匿名化白名單（API + Exporter） | 姓名外流 | ✓ | 園名本身是公開資訊，含負責人姓名的園名（如「XX 附設」）不在擋的範圍 |
| stale 警示 | 用過期資料做決定 | ◐ | 只警示不擋匯出 |
| DBBuilder 筆數驟降中止 | 抓到殘缺資料覆蓋好資料 | ✓ | 擋不住「筆數相同但內容錯」 |
| 來源失敗沿用舊快照 | 一個站掛掉整條停 | ✓ | 沿用會讓 data_asof 不變，觸發 stale 警示 —— 這是刻意的 |
| feedback 表 | 誤判無處申訴 | ✗ | 只收不用；沒有任何流程讀它（首版明知） |

## §8 未決事項與假設

| # | 問題 | 目前假設 | 誰能拍板 | 何時必須決定 |
|---|------|---------|---------|-------------|
| 1 | 主檔屬性（核定人數、負責人、月費）只有「現在」快照，回測時當常數用會有輕微洩漏 | 接受；deck 註明 | Peter | 回測前 |
| 2 | 同名負責人消歧的地址距離門檻 | 5 km 內視為同人，否則標同名待確認 | Peter | Linker 實作前 |
| 3 | 觀察點「每季」是否過密（45k 列） | 先每季；訓練慢再改每半年 | 工程判斷 | Trainer 實作時 |
| 4 | 全國網基礎評鑑 POST 回 500 | 先 spike 30 分鐘；不通則 `evaluations` 空、特徵缺 | 工程判斷 | Ingest 實作時 |
| 5 | 模型演算法 | GBDT（LightGBM 或 sklearn HistGB）+ 邏輯迴歸對照；Verdandi-AutoML 可否直接接 | Peter | Trainer 實作前 |
| 6 | 等級門檻預設 | 高 ≥ 75、中 ≥ 50（分數 = 機率分位數 ×100） | Peter | Scorer 實作前 |
| 7 | 準公共園是否獨立立案別 | 主檔 `type` 無準公共，用 `pre_public` 非空判定並在 UI 顯示為準公共 | 工程判斷 | DBBuilder |
| 8 | 契約 vs mockup 差異：mockup 詳情頁顯示「設立年」「月費」「基礎評鑑」，需求 US-2 未列 | 契約已納入（`reg_date`、`monthly`、`eval_result`） | 已補 | — |
| 9 | 匯出是否記錄稽核 | 首版不記 | Peter | 試辦前 |
| 10 | feedback 何時回流成標籤 | 首版只收 | Peter | 試辦後 |
| 11 | 承辦是否有季度訪視配置權（排程輸出「行程」的前提） | 假設有（Peter 要求排程） | Peter（讀稽查 SOP / 訪談承辦） | Phase 5 前 |
| 12 | 人力假設值 | 3 人 × 8 次/週 × 13 週 = 312 席（demo） | Peter | P3b 前 |
| 13 | 列層 vs 事件層數字 | 一律事件層：回頭客 48%、貢獻 75%、12 個月再犯 26–33% | 已定（實查） | — |

---

## 圖檔交付收據

機械檢查：`check-html-figure.mjs --width 1600 --height 900`（playwright 走系統 Chrome）。截圖已 Read 過才寫 passed。

| 圖 | mechanical_check | screenshot | visual_review | rounds | 殘留 warning 裁定 |
|----|-----------------|-----------|--------------|--------|-----------------|
| `diagrams/dataflow-modules-target-2026-09`（1/4 模組全景） | 0 errors, 0 warnings @1600×900 | `diagrams/dataflow-modules-target-2026-09.png` | passed | 0 | 無 |
| `diagrams/dataflow-functions-target-2026-09`（2/4 §6a） | 0 errors, 0 warnings | `…functions-target-2026-09.png` | passed（第一輪肉眼抓到：回頭路標籤出界、旁掛兩邊交叉、一條虛線壓節點，已修） | 2 | 無 |
| `diagrams/dataflow-entities-target-2026-09`（3/4 §6b） | 0 errors, 0 warnings | `…entities-target-2026-09.png` | passed（第一版兩條邊必然交叉，改四角環排法；評鑑改虛框可缺） | 2 | 無 |
| `diagrams/dataflow-gates-target-2026-09`（4/4 關卡實況） | 0 errors, 0 warnings | `…gates-target-2026-09.png` | passed | 0 | 無 |
| `diagrams/er-smart-watchdog-2026-09`（§2 ER） | 0 errors, 0 warnings | `…er-smart-watchdog-2026-09.png` | passed（第一輪：兩條 FK 箭頭指錯表、底部空白帶，已修） | 2 | 無 |
| `components-…`（§3） | 表格代替 —— §3 職責邊界表已完整，12 模組單機無邊界分群需求 | — | — | — | — |
| `sequence-update-…`（§5） | 表格代替 —— 單一 actor 串九階段，失敗分支表已完整 | — | — | — | — |

產生器：`scripts/draw_sd_diagrams.py`（座標手排，樣式來自 `~/.claude/skills/chart-design/styles/ntpc-smart-watchdog.md`）。
四張 target 圖同名版面，實作後 `/qa-dataflow` 出 `dataflow-*-actual-*` 並排比對。

## 交付前 Must-Check

- [x] 檔位 Full，判準在 frontmatter
- [x] §1 一句話摘要
- [x] §2 現況實查（`.schema` + `build_db.py` 行號）
- [x] §3 每模組有「不負責」
- [x] §4 每介面有錯誤形狀 + 冪等性
- [x] Step 1.5：首版無 baseline
- [x] ADR：無 docs/adr；對照 `~/.claude/CLAUDE.md` Right-size（SQLite）
- [x] §6a 每條邊標識別碼、有回頭路
- [x] §6b 每實體標躺在哪；與 §2 Delta 對帳（evaluations 以虛框標可缺）
- [x] §7 三態關卡，✗/◐ 附理由
- [x] §8 每條有「誰能拍板」
- [x] 每張圖機械檢查 + 截圖 + Read
- [x] footer 標日期
