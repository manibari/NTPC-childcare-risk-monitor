# Design System — Smart Watchdog

繼承 `~/code/rivendell/dashboard-next/DESIGN.md`（Peter 2026-09-12 指定）：單一森林綠強調 `--accent #2d4a3e`、Geist / Geist Mono 自架、Lucide 線性 icon、無藍紫 chrome、狀態色只用於狀態、卡片只在互動時、無陰影、內嵌 SVG 圖表三型（line / horizontal bar / heatmap，sequential greens）、desktop-only。

## 本專案衍生決策（2026-09-12，/gstack-autoplan Design Pass 5）

| 項目 | 決定 | 理由 |
|---|---|---|
| CJK fallback | `"Geist", "PingFang TC", "Noto Sans TC", sans-serif`；mono `"Geist Mono", "PingFang TC", monospace` | rivendell 未定 CJK |
| 風險等級 | 高 `--status-err`、中 `--status-warn`、低 `--text-muted`；一律 8px 色點 + 文字 | 低風險 ≠ 健康，不用綠；色彩不當唯一訊號 |
| 立案別／類別 | 純文字或 mono chip（surface 底、1px 邊、2px 圓角），**不上色** | 類別不是狀態 |
| 連坐 | chip 虛線邊（沿用 rivendell `optional` 修飾） | 「待確認」語意 |
| 兒安條款（§30/§33/§43） | 允許 `--status-err` | 是狀態 |
| 關聯圖 | 負責人／法人節點 `--accent`；被罰園 `--status-err` 邊；連坐園 `--accent-bg` + 虛線 | 對齊三態 |
| 側欄 | `--surface` + `--border`，承辦區／維護區以分隔線分組 | 不用深色側欄 |
| 分數 | mono 黑，不依等級染色；理由用白話句 + 無刻度長條 | 科長可轉述 |
| 圖表 | 每頁 ≤ 2 張主圖；禁 pie、3D、動畫 | rivendell 規則 |
| 目標寬 | 1440（最小 1280）；表格橫向捲動 + 首欄凍結 | desktop-only |
| 無障礙 | 鍵盤全覆蓋、focus ring `--accent` 2px、抽屜 dialog + focus trap、`aria-sort`、圖表文字摘要、對比 ≥ 4.5:1 | Pass 6 |

圖表樣式 SoT：`~/.claude/skills/chart-design/styles/ntpc-smart-watchdog.md`（同步為森林綠）。
