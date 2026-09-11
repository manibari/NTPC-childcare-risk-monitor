# Findings — Smart Watchdog

## 資料事實（已驗證，見 docs/exploration.md）
- 新北 1,216 園：私立 869 / 公立 294 / 非營利 53；裁罰 1,474 筆 / 487 園（2017-05 ~ 2026-04）
- 裁罰率：私立 52%、非營利 28%、公立 8%；回頭客 303 園 = 62%，貢獻 88% 裁罰
- 自身再犯（每次裁罰後 12 個月）35%；連坐（同負責人他園）11% vs 基準 7%
- 119 位多園負責人持 316 園；連坐可拉進 96 未裁罰園；非營利園的勾稽單位是委辦法人（25 個）
- 財報：38 非營利園 155 園年；被罰 9 園 vs 未罰 29 園比率無顯著差異，只有「每核定名額收入」偏低（p=0.04, n=9）
- 其他收入 = 其他支出（94% 園年），是補助過手，不是業外損益
- 公共化園裁罰法條：非營利 47%、公立 87% 為 §33 不當對待

## 資料來源
- kiang 備份：`https://kiang.github.io/ap.ece.moe.edu.tw/{preschools,punish_all}.json`（punish_all 以行為人為鍵，每筆 `id` = 園所 id）
- 新北裁罰公告：`kidedu.ntpc.edu.tw/app/index.php?Plugin=o_tpckedu&Action=o_tpckeduschpenalty&Url=1&Page=N&SchArea=0..3`（只到園所層級）
- 全國網表單：pubSearch / evaSearch / punishSearch（ASP.NET，新北=03；POST 回 500 未除錯）
- 非營利財報 PDF 除封面全掃描；macOS Vision OCR 0.8 s/頁；恆等式通過 BS 148 / IS 151（155 園年）

## 待 Phase 2 填入：回測數字
- AUC / PR-AUC / top50-100-200 / 提前天數 / 三 baseline / 分年：（未跑）
