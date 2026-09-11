# Smart Watchdog — 實作計畫

## Goal
在既有 SQLite 四張表之上長出「觀察點 → 模型 → 分數 → 連坐 → 名單 → 匯出」管線與 8 畫面 demo，
並產出黑客松提案 deck。依 `docs/design/2026-09-12-smart-watchdog-sd.md`（SD）與 `docs/requirements/smart-watchdog.md`（US-1..7）。

槓桿順序（lever-first）：先把**回測數字**跑出來（Phase 2），因為它決定 deck 主張是「模型」還是「規則」；
畫面與 deck 之後才有東西可放。

## Phases

### Phase 0: 資料層加固（SD §2 Delta 的 `~` 與持久表）
- **Status**: `not_started`
- **Tasks**:
  - [ ] `build_db.py`：`preschools.id` PK、`monthly` INTEGER、`penalties.penalty_id` AUTOINCREMENT、`is_safety`(§30/33/43)
  - [ ] `build_db.py` 改為只重建來源表；新增 `scripts/db.py`（連線、schema、持久表建表：models/backtests/scores/watchlist/season_list/feedback/pipeline_runs/settings/evaluations）
  - [ ] `settings` 預設：high=75, mid=50, top_n=100, watch_window_months=12, stale_days=90, data_asof
  - [ ] `pipeline_runs` 寫入 helper（stage, n_rows, seconds, n_failed, ok, message）
  - [ ] 驗證：`sqlite3 .schema`、筆數不變（7,688 / 7,065 / 155）
- **Notes**: 準公共判定 = `pre_public` 非空（SD §8-7）

### Phase 1: 負責人／法人勾稽與連坐（Linker；US-3）
- **Status**: `not_started`
- **Tasks**:
  - [ ] `scripts/linker.py`：owner → linkers(kind=owner)、園名括號「委託⋯辦理」→ linkers(kind=operator)；code O-xxx / L-xxx；n_schools
  - [ ] `preschool_linkers`：同名判定（同 owner、地址距離 > 5 km → same_name_flag=1）；`excluded_by_user` 依 (kind,key_name,preschool_id) 回填
  - [ ] `watchlist`：reason=penalised（12 個月內有裁罰）/ owner_link / operator_link；source_preschool_id、source_penalty_id、linker_id；is_current
  - [ ] 驗證：新北 119 位多園負責人、連坐拉進 ≈96 園（對照 docs/exploration.md）
- **Notes**: 連坐不進分數（SD §7）

### Phase 2: 觀察點、特徵、訓練、回測、核准（US-4）
- **Status**: `not_started`
- **Tasks**:
  - [ ] `scripts/features.py`：觀察點 (a) 每筆裁罰 +1 天 (b) 每季末全園；20 個特徵只用 asof 之前資料；`label_repeat_12m`、`label_available`
  - [ ] `scripts/train.py`：時間切分（按 obs_year 逐年 walk-forward）；GBDT（sklearn HistGradientBoosting）+ 邏輯迴歸對照；三 baseline（隨機／按次數／按最近距今）；AUC、PR-AUC、top50/100/200 覆蓋率、提前天數中位；寫 models(status=trained)+backtests；`beats_baseline`
  - [ ] `scripts/approve.py`：首版自動 active；之後 AUC 降 >0.05 留 trained 並警示
  - [ ] 驗證：正例 ≥ 50；回測表可重跑（固定 seed）；把數字寫進 findings.md（deck 用）
- **Notes**: 主檔屬性是現在快照（SD §8-1），deck 註明

### Phase 3: 評分（Scorer；US-1/US-2 資料面）
- **Status**: `not_started`
- **Tasks**:
  - [ ] `scripts/score.py`：active model 對 data_asof 當天觀察點評分；`beats_baseline=0` → rule_count；無裁罰史 → attribute；score = 機率分位數 ×100；level 依 settings；top_features（SHAP 或 permutation，前 5）；is_current 換批
  - [ ] 驗證：1,216 列、rank 1..1216、等級分布合理
- **Notes**:

### Phase 4: API 與匯出（SD §4；US-5/6/7 資料面；ops #2 #3 #4 #6）
- **Status**: `not_started`
- **Tasks**:
  - [ ] FastAPI `app/`：/overview /rankings /preschools/{id} /linkers/{code}/graph PUT linkers exclude /backtest /season-list /export /data-quality /settings /feedback；統一錯誤形狀；匿名化白名單
  - [ ] `scripts/export.py`：CSV(UTF-8 BOM)/xlsx；空名單 422
  - [ ] 契約測試：每端點成功 + 主要錯誤碼各一
- **Notes**: API 綁 localhost

### Phase 5: Web 8 畫面（mockup 已定；US-1..7 畫面面）
- **Status**: `not_started`
- **Tasks**:
  - [ ] Next.js + antd v6 scaffold（ChimesFlow 契約；lockfile 決定 pnpm/npm）
  - [ ] 總覽、排名（篩選/加入名單/stale 警示/錯誤卡）、詳情（時間軸/特徵/連坐來源/財務燈號）、關聯圖、回測儀表（含「不優於規則」警示）、匯出對話框、資料品質、設定、回饋按鈕
  - [ ] 對照 mockup 截圖逐頁比對
- **Notes**:

### Phase 6: 更新管線 CLI 與抓取（US-6）
- **Status**: `not_started`
- **Tasks**:
  - [ ] `scripts/ingest.py`：kiang 兩 JSON、新北裁罰公告、全國網基礎評鑑（30 分鐘 spike；不通則 evaluations 空）；失敗沿用上次檔
  - [ ] `scripts/update.py`：九階段串接、pipeline_runs、DBBuilder 筆數驟降中止、data_asof 更新、exit code 0/1/2
  - [ ] 驗證：`--no-fetch` 全程可重跑 < 5 分鐘
- **Notes**:

### Phase 7: 驗收（G4）
- **Status**: `not_started`
- **Tasks**:
  - [ ] `/gstack-review`（每個 Phase 收尾）
  - [ ] `/qa-dataflow`：拿 SD §6 四張 target 圖對 actual，出落差表
  - [ ] `/gstack-qa` + `/gstack-design-review`（8 畫面）
  - [ ] `/gstack-careful` 於任何刪表 / 重建前
- **Notes**:

### Phase 8: 提案 deck
- **Status**: `not_started`
- **Tasks**:
  - [ ] storyline.md（Peter 主筆，AI 補洞）→ `/slide-office-hours` red team → signed-off
  - [ ] `/sales-deck-design` 或 `/slide-workflow` 生成；圖表走 `/chart-design`
  - [ ] `/de-slopify` 文字打磨；`/gstack-document-release`
- **Notes**: 主張依 Phase 2 數字決定（模型 vs 規則）

## Key Decisions

| Decision | Rationale | Date |
|----------|-----------|------|
| 回頭客（再犯）當主軸 | 62% 被罰園是回頭客，貢獻 88% 裁罰 | 2026-09-12 |
| 觀察點從每次裁罰後起算 + 每季 | Peter 決定；每季提供無裁罰史園的評分與 top-N 基準 | 2026-09-12 |
| 連坐只加旗標不進分數 | 檢定：連坐園 12 個月被罰 11% vs 基準 7%，訊號弱 | 2026-09-12 |
| 財報只當財務燈號附錄 | 被罰 9 園財務比率與未罰無顯著差異；公共化園裁罰 47–87% 為 §33 人的行為 | 2026-09-12 |
| SQLite 單檔 | ≤ 10k 主檔列、單使用者（CLAUDE.md Right-size） | 2026-09-12 |
| 模型不優於「按次數排序」時自動改規則 | 命題要辨識率；不能把更差的模型端出去 | 2026-09-12 |
| Phase 2 先於畫面 | 回測數字決定 deck 主張 | 2026-09-12 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| 全國教保資訊網 punishSearch POST 回 500 | 1 | 改用新北公告頁 + kiang 備份；評鑑 POST 留 Phase 6 spike |
| pandas groupby.apply 與 `name` 欄位衝突 | 1 | 改 `g["name"]` |
| draw_sd_diagrams 文字重疊 ×6、FK 箭頭指錯表 | 2 | 重排實體圖為四角環；FK 走左側 lane |
