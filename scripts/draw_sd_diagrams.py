"""Generate the SA/SD diagram set (1600x900 SVG-in-HTML) for docs/design.

Layout is hand-placed (orthogonal, one band per row) per qa-dataflow diagram-spec;
colours come from ~/.claude/skills/chart-design/styles/ntpc-smart-watchdog.md.
"""
import pathlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "docs/design/diagrams"
OUT.mkdir(parents=True, exist_ok=True)
W, H = 1600, 900
FOOT = "依 2026-09-12 設計（target，非實查）· NTPC Smart Watchdog SA/SD · 樣式 styles/ntpc-smart-watchdog.md"

# style tokens (from style file)
C = dict(primary="#1677ff", ink="#14171a", muted="#6b7680", pos="#52c41a", neg="#ff4d4f", warn="#d48806",
         src=("#e6f4ff", "#91caff"), model=("#f9f0ff", "#d3adf7"), store=("#fffbe6", "#ffe58f"),
         deliver=("#f6ffed", "#b7eb8f"), side=("#fafafa", "#d9d9d9"), band=("#f7fafc", "#c3d2dd"))

CSS = """
* { margin:0; padding:0 }
body { background:#fff; font-family:"PingFang TC","Helvetica Neue",Arial,sans-serif; overflow:hidden }
svg { display:block }
.t1 { font-size:32px; font-weight:700; fill:#14171a }
.t2 { font-size:16px; fill:#5a6470 }
.bl { font-size:18px; font-weight:700 }
.bs { font-size:13px; fill:#6b7680 }
.n  { font-size:16px; font-weight:600; fill:#14171a }
.ns { font-size:12.5px; fill:#6b7680 }
.mono { font-family:"SF Mono",Menlo,monospace; font-size:12px; fill:#7d5a12 }
.e  { font-size:12.5px; font-weight:600; fill:#1f4f8f }
.eo { font-size:12.5px; font-weight:600; fill:#a35f00 }
.ed { font-size:12.5px; font-weight:600; fill:#5a7f9e }
.lg { font-size:13px; fill:#454d55 }
.ft { font-size:12px; fill:#7a848e }
.chip { font-size:12px; font-weight:600 }
"""


class Fig:
    def __init__(self, title, sub, q):
        self.parts = []
        self.title, self.sub, self.q = title, sub, q

    def add(self, s):
        self.parts.append(s)

    def band(self, x, y, w, h, label, sub="", fill=None):
        f, s = fill or C["band"]
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{f}" stroke="{s}" stroke-width="1.5"/>')
        self.add(f'<text class="bl" x="{x+18}" y="{y+26}" fill="#2b5a7e">{label}</text>')
        if sub:
            self.add(f'<text class="bs" x="{x+18+len(label)*18+16}" y="{y+26}">{sub}</text>')

    def node(self, x, y, name, sub="", w=190, h=60, kind="box", fill=None, dashed=False, sub2=""):
        f, s = fill or ("#fff", "#5b7f9e")
        da = ' stroke-dasharray="6 4"' if dashed else ""
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{f}" stroke="{s}" stroke-width="2"{da}/>')
        self.add(f'<text class="n" x="{x+14}" y="{y+25}">{name}</text>')
        if sub:
            self.add(f'<text class="ns" x="{x+14}" y="{y+44}">{sub}</text>')
        if sub2:
            self.add(f'<text class="mono" x="{x+14}" y="{y+h-8}">{sub2}</text>')

    def edge(self, pts, label="", kind="flow", at=None, lw=None):
        cls = {"flow": ("#3d474f", "a", "e", ""), "loop": ("#c99a3a", "al", "eo", ""),
               "dash": ("#7d93a8", "ad", "ed", ' stroke-dasharray="6 4"')}[kind]
        d = "M" + " L".join(f"{x} {y}" for x, y in pts)
        self.add(f'<path d="{d}" fill="none" stroke="{cls[0]}" stroke-width="2.2"{cls[3]} marker-end="url(#{cls[1]})"/>')
        if label:
            if at is None:
                (x1, y1), (x2, y2) = pts[0], pts[-1]
                at = ((x1 + x2) / 2, (y1 + y2) / 2)
            w = lw or (len(label) * 8.2 + 16)
            self.add(f'<rect x="{at[0]-w/2:.0f}" y="{at[1]-11}" width="{w:.0f}" height="22" rx="5" fill="#fff" stroke="#dfe4e8"/>')
            self.add(f'<text class="{cls[2]}" x="{at[0]:.0f}" y="{at[1]+4}" text-anchor="middle">{label}</text>')

    def text(self, x, y, s, cls="lg", fill=None, anchor="start"):
        f = f' fill="{fill}"' if fill else ""
        self.add(f'<text class="{cls}" x="{x}" y="{y}" text-anchor="{anchor}"{f}>{s}</text>')

    def render(self, path, nav=None):
        navsvg = ""
        if nav:
            cur, items = nav
            x = 1600 - 18 - 4 * 150 + 10
            for i, it in enumerate(items):
                on = i == cur
                navsvg += (f'<rect x="{x+i*150}" y="18" width="142" height="26" rx="6" fill="{"#1677ff" if on else "#fff"}" stroke="#91caff"/>'
                           f'<text x="{x+i*150+71}" y="36" text-anchor="middle" font-size="12" font-weight="600" fill="{"#fff" if on else "#1f4f8f"}">{it}</text>')
        html = f"""<!doctype html>
<meta charset="utf-8"><title>{self.title}</title><style>{CSS}</style>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
<defs>
<marker id="a" markerWidth="9" markerHeight="7" refX="8.5" refY="3.5" orient="auto"><polygon points="0 0, 9 3.5, 0 7" fill="#3d474f"/></marker>
<marker id="al" markerWidth="9" markerHeight="7" refX="8.5" refY="3.5" orient="auto"><polygon points="0 0, 9 3.5, 0 7" fill="#c99a3a"/></marker>
<marker id="ad" markerWidth="8" markerHeight="6" refX="7.5" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="#7d93a8"/></marker>
</defs>
{navsvg}
<text class="t1" x="30" y="48">{self.title}</text>
<text class="t2" x="30" y="76">{self.sub}</text>
<line x1="30" y1="90" x2="1570" y2="90" stroke="#14171a" stroke-width="3"/>
{''.join(self.parts)}
<text class="ft" x="30" y="884">{FOOT} · 這張圖回答：{self.q}</text>
</svg>"""
        (OUT / path).write_text(html)
        print("wrote", OUT / path)


NAV = ["1/4 模組全景", "2/4 功能關係", "3/4 資料實體", "4/4 關卡實況"]


# ---------------------------------------------------------------- 1/4 modules
def modules():
    f = Fig("模組全景：由哪些零件組成，哪一塊還沒有",
            "沒有邊，這是清單不是流程。綠框 = 既有可用、橘框 = 既有要改、紅虛框 = 待建。下面一條 store rail 標每張表由誰寫、誰讀。",
            "有哪些模組、哪個沒做（回答 1/4；下一張回答誰是誰的前置）")
    ok, chg, todo = ("#f6ffed", "#52c41a"), ("#fffbe6", "#d48806"), ("#fff2f0", "#ff4d4f")
    bands = [
        ("A 整備", 104, [("抓公開資料", "Ingest · 三站 + kiang", todo), ("OCR 財報", "ocr_batch.py · Vision", ok),
                        ("財報解析", "parse_statements / compare_ratios", ok), ("建庫", "build_db.py · 補 PK / is_safety", chg)]),
        ("B 建模", 228, [("觀察點與特徵", "FeatureBuilder · 裁罰後起算 + 每季", todo), ("訓練與回測", "Trainer · 時間切分 · 三 baseline", todo),
                        ("版本核准", "Approver · AUC 降 >0.05 不切換", todo)]),
        ("C 評分與名單", 352, [("負責人／法人勾稽", "Linker · 匿名代碼 O-/L-", todo), ("連坐名單", "Linker · 12 個月觀察期", todo),
                             ("重算分數", "Scorer · 模型 / 規則 / 屬性", todo), ("匯出", "Exporter · 白名單欄位", todo)]),
        ("D 交付", 476, [("API", "FastAPI · 唯讀 + 4 個小寫入", todo), ("Web 8 畫面", "Next.js + antd · mockup 已定", todo),
                        ("更新管線 CLI", "update.py · 九階段 · pipeline_runs", todo)]),
    ]
    for label, y, nodes in bands:
        f.band(30, y, 1540, 112, label)
        for i, (n, s, col) in enumerate(nodes):
            f.node(60 + i * 300, y + 40, n, s, w=280, h=58, fill=col, dashed=(col is todo))
    f.text(1390, 130, "既有 3 · 要改 1 · 待建 10", "lg", "#a33")
    # store rail
    f.band(30, 600, 1540, 250, "Store rail —— 每張表誰寫、誰讀", "黃 = 每次更新整表重建；紫 = 持久（重建時保留）", fill=("#fffdf5", "#e8d9a8"))
    rebuild, persist = ("#fffbe6", "#ffe58f"), ("#f9f0ff", "#d3adf7")
    stores = [
        ("preschools", "建庫 → 特徵/勾稽/API", rebuild), ("penalties", "建庫 → 觀察點/API", rebuild), ("statements · ratios", "財報解析 → 財務燈號", rebuild),
        ("evaluations", "抓取 → 特徵（可缺）", rebuild), ("linkers · preschool_linkers", "勾稽 → 連坐/關聯圖/匯出", rebuild),
        ("observations", "特徵 → 訓練/評分", rebuild), ("models · backtests", "訓練 → 核准/評分/儀表", persist),
        ("scores", "評分 → 排名/詳情/匯出", persist), ("watchlist", "連坐 → 旗標/匯出", persist),
        ("season_list", "承辦 → 匯出", persist), ("feedback", "承辦 → （首版無人讀）", persist),
        ("pipeline_runs · settings", "管線/API → 品質頁/評分", persist),
    ]
    for i, (n, s, col) in enumerate(stores):
        r, c = divmod(i, 6)
        f.node(60 + c * 250, 640 + r * 100, n, s, w=232, h=70, fill=col)
    f.render("dataflow-modules-target-2026-09.html", nav=(0, NAV))


# ---------------------------------------------------------------- 2/4 functions
def functions():
    f = Fig("功能關係圖（target）：誰是誰的前置，交出去的是什麼",
            "每條線標「交給下一個功能的識別碼」。實線 = 必要前置；虛線 = 唯讀旁掛；橘線 = 回頭路。節點名 = 畫面上看得到的名字。",
            "誰是誰的前置、交出去什麼（2/4；下一張回答資料躺在哪）")
    c = [100, 360, 620, 880, 1140]
    yA, yB, yC, yD = 96, 246, 396, 556
    f.band(30, yA, 1540, 120, "A 整備", "把三個網站與一疊 PDF 變成可查詢的表 —— 不隨行：評鑑可缺、私立園無財務")
    f.band(30, yB, 1350, 120, "B 建模", "把裁罰史變成「未來 12 個月會不會再犯」的版本化模型 —— 不隨行：任何姓名")
    f.band(30, yC, 1350, 120, "C 評分與名單", "每園一個分數、一個旗標 —— 財務比率不進分數")
    f.band(30, yD, 1540, 224, "D 交付", "承辦看得懂、拿得走 —— 每頁回饋按鈕寫 feedback 表（首版只收不用）", fill=C["deliver"])
    # side column
    f.add(f'<rect x="1400" y="{yB}" width="170" height="270" rx="12" fill="{C["side"][0]}" stroke="{C["side"][1]}" stroke-width="1.5"/>')
    f.text(1412, yB + 22, "設定與旁掛", "bl", "#2b5a7e")
    # A
    f.node(c[0], 130, "OCR 財報", "131 份掃描 PDF")
    f.node(c[1], 130, "抓公開資料", "主檔 · 裁罰 · 評鑑", fill=C["src"])
    f.node(c[2], 130, "建庫", "重建來源表 · 補 PK")
    f.node(c[3], 130, "財報解析", "恆等式剔錯 → ratios")
    # B
    f.node(c[2], 280, "觀察點與特徵", "裁罰後起算 + 每季", fill=C["model"])
    f.node(c[3], 280, "訓練與回測", "時間切分 · 三 baseline", fill=C["model"])
    f.node(c[4], 280, "版本核准", "首版自動 · 退步不切換", fill=C["model"])
    # C
    f.node(c[0], 430, "本季名單", "承辦手動加入")
    f.node(c[1], 430, "負責人／法人勾稽", "匿名代碼 O-xxx / L-xxx")
    f.node(c[2], 430, "連坐名單", "觀察期 12 個月 · 只加旗標")
    f.node(c[4], 430, "重算分數", "模型 / 規則 / 屬性 三擇一", fill=C["model"])
    # D
    f.node(c[0], 596, "總覽", "各區高風險 · 趨勢", fill=C["deliver"])
    f.node(c[1], 596, "匯出", "CSV / xlsx · 匿名化", fill=C["deliver"])
    f.node(c[2], 596, "關聯圖", "負責人名下園 · 排除同名", fill=C["deliver"])
    f.node(c[3], 596, "園所詳情", "時間軸 · 前 5 特徵 · 財務燈號", fill=C["deliver"])
    f.node(c[4], 596, "風險排名", "篩選 · 加入名單", fill=C["deliver"])
    f.node(1400, 596, "科長", "看總覽 → 分配各區人力", w=170, h=50, fill=("#fff", "#14171a"))
    f.node(1400, 680, "承辦", "排下季稽查行程", w=170, h=50, fill=("#fff", "#14171a"))
    # side nodes
    f.node(1400, 280, "回測儀表", "讀 models/backtests", w=170, h=56, dashed=True, fill=C["side"])
    f.node(1400, 352, "設定", "門檻 · 觀察期 · data_asof", w=170, h=56, fill=C["store"])
    f.node(1400, 424, "財務燈號", "讀 ratios → 詳情頁", w=170, h=56, dashed=True, fill=C["side"])
    f.node(1400, 496, "資料品質", "讀 pipeline_runs", w=170, h=56, dashed=True, fill=C["side"])
    # edges A
    f.edge([(290, 175), (325, 175), (325, 208), (700, 208), (700, 190)], "jsonl", at=(500, 208))
    f.edge([(550, 160), (620, 160)], "json", at=(585, 160), lw=48)
    f.edge([(810, 160), (880, 160)], "fiscal_year", at=(845, 160), lw=90)
    f.edge([(715, 190), (715, 280)], "preschool_id · penalty_id", at=(715, 233))
    f.edge([(660, 190), (660, 232), (455, 232), (455, 430)], "owner / operator", at=(540, 232))
    # edges B
    f.edge([(810, 310), (880, 310)], "obs_id", at=(845, 310), lw=60)
    f.edge([(1070, 310), (1140, 310)], "model_id", at=(1105, 310), lw=76)
    f.edge([(1235, 280), (1235, 262), (975, 262), (975, 280)], "AUC 降 >0.05 → 留 trained 不切換", kind="loop", at=(1105, 262))
    f.edge([(1235, 340), (1235, 430)], "model_id active", at=(1235, 385))
    f.edge([(760, 340), (760, 380), (1190, 380), (1190, 430)], "obs_id @ data_asof", at=(975, 380))
    f.edge([(1330, 310), (1400, 310)], "", kind="dash")
    f.edge([(1400, 380), (1365, 380), (1365, 460), (1330, 460)], "門檻", at=(1365, 420), lw=44)
    # edges C
    f.edge([(550, 460), (620, 460)], "linker_id", at=(585, 460), lw=72)
    f.edge([(1235, 490), (1235, 596)], "score_id · rank · level", at=(1235, 545))
    f.edge([(715, 490), (715, 596)], "watchlist 列", at=(715, 545), lw=90)
    f.edge([(790, 490), (790, 525), (1180, 525), (1180, 596)], "連坐旗標", at=(985, 525), lw=80)
    f.edge([(195, 490), (195, 560), (455, 560), (455, 596)], "preschool_id 集合", at=(325, 560))
    # edges D
    f.edge([(1140, 626), (1070, 626)], "preschool_id", at=(1105, 626), lw=96)
    f.edge([(880, 626), (810, 626)], "linker code", at=(845, 626), lw=88)
    f.edge([(1235, 656), (1235, 700), (60, 700), (60, 460), (100, 460)], "加入 preschool_id", at=(640, 700))
    f.edge([(195, 656), (195, 730), (1300, 730), (1300, 656)], "點行政區 → town 篩選", at=(760, 730))
    f.edge([(455, 656), (455, 758), (1485, 758), (1485, 730)], "CSV / xlsx", at=(970, 758), lw=90)
    f.edge([(770, 596), (770, 575), (745, 575), (745, 490)], "排除同名", kind="loop", at=(830, 575), lw=76)
    f.edge([(100, 626), (45, 626), (45, 160), (100, 160)], "資料過期 → 整條重跑", kind="loop", at=(130, 381), lw=150)
    f.text(30, 806, "圖例：實線 必要前置（標識別碼）· 橘線 回頭路 · 虛線 唯讀旁掛，不產生新實體 · 黑框 = 人（終點）· 紫底 = 建模帶 · 藍底 = 外部來源", "lg")
    f.render("dataflow-functions-target-2026-09.html", nav=(1, NAV))


# ---------------------------------------------------------------- 3/4 entities
def entities():
    f = Fig("資料實體流（target）：資料本身怎麼流、躺在哪、活多久",
            "節點是資料實體不是功能；箭頭標「產生下一個實體的動作」；每格底行是它躺在哪。黃 = 每次更新重建，紫 = 持久，藍 = 檔案，虛線 = 唯讀。",
            "資料躺在哪、誰產生誰（3/4；下一張回答關卡擋不擋得住）")
    R, P, F_, D_ = C["store"], C["model"], C["src"], C["side"]
    c = [60, 330, 600, 870, 1140, 1410]
    yA, yB, yC = 120, 300, 480
    # source-table group (one snapshot rebuilds both)
    f.add('<rect x="320" y="110" width="212" height="272" rx="12" fill="none" stroke="#d48806" stroke-width="1.5" stroke-dasharray="6 4"/>')
    f.text(326, 388, "來源表：同一快照重建", "ns", "#a35f00")
    f.node(c[0], yA, "網頁快照", "主檔 · 裁罰 · 評鑑（可缺）", sub2="data/raw-web/*.json", h=70, fill=F_)
    f.node(c[1], yA, "Preschool", "1,216 園 · 屬性快照", sub2="preschools（重建）", h=70, fill=R)
    f.node(c[2], yA, "Observation", "≈45k 列 · 特徵 + 12 個月標籤", sub2="observations（重建）", h=70, fill=R)
    f.node(c[3], yA, "Model + Backtest", "版本 · status 流轉", sub2="models · backtests（持久）", h=70, fill=P)
    f.node(c[4], yA, "Score", "1,216 列/批 · is_current", sub2="scores（持久）", h=70, fill=P)
    f.node(c[5], yA, "匯出檔", "白名單欄位，不落地", sub2="瀏覽器下載 → 人", w=150, h=70, fill=F_)
    f.node(c[0], yB, "evaluations", "評鑑表；抓不到就空", sub2="evaluations（重建，可缺）", h=70, fill=R, dashed=True)
    f.node(c[1], yB, "Penalty", "1,474 筆 · penalty_id", sub2="penalties（重建）", h=70, fill=R)
    f.node(c[2], yB, "Linker", "負責人 / 委辦法人 · 匿名代碼", sub2="linkers · preschool_linkers", h=70, fill=R)
    f.node(c[3], yB, "WatchlistEntry", "連坐旗標 · is_current", sub2="watchlist（持久）", h=70, fill=P)
    f.node(c[4], yB, "SeasonListEntry", "承辦加入", sub2="season_list（持久）", h=70, fill=P)
    f.node(c[5], yB, "人", "承辦 / 科長", sub2="看排名 · 加入 · 匯出", w=150, h=70, fill=("#fff", "#14171a"))
    f.node(c[0], yC, "OCR 頁", "已有輸出跳過", sub2="data/ocr/*.jsonl", h=70, fill=F_)
    f.node(c[1], yC, "Statement / Ratio", "155 園年 · 恆等式剔錯", sub2="statements · ratios（重建）", h=70, fill=R)
    f.node(c[3], yC, "詳情頁 財務燈號", "唯讀，不產生新實體", sub2="API 回應內", h=70, fill=D_, dashed=True)
    f.node(c[4], yC, "Feedback", "首版只收不用", sub2="feedback（持久）", h=70, fill=P)
    # main chain (row A)
    f.edge([(250, 145), (320, 145)], "重建", at=(285, 145), lw=44)
    f.edge([(520, 145), (600, 145)], "屬性 @ asof", at=(560, 145), lw=90)
    f.edge([(790, 145), (870, 145)], "訓練 + 回測", at=(830, 145), lw=90)
    f.edge([(1060, 145), (1140, 145)], "核准 → 評分", at=(1100, 145), lw=90)
    f.edge([(1330, 145), (1410, 145)], "白名單", at=(1370, 145), lw=60)
    # four-cycle without crossings: Preschool→Linker (left side), Penalty→Observation (around left/top)
    f.edge([(500, 190), (500, 260), (640, 260), (640, 300)], "解析 owner/operator", at=(560, 260))
    f.edge([(330, 320), (292, 320), (292, 104), (700, 104), (700, 120)], "切觀察點 + 貼標籤", at=(292, 250), lw=70)
    f.edge([(520, 335), (600, 335)], "12 個月內裁罰", at=(560, 335), lw=104)
    f.edge([(790, 335), (870, 335)], "連坐規則", at=(830, 335), lw=72)
    f.edge([(425, 370), (425, 420), (965, 420), (965, 370)], "裁罰事件觸發", at=(700, 420), lw=100)
    f.edge([(780, 190), (780, 250), (1160, 250), (1160, 190)], "asof 特徵 → 評分", at=(970, 250))
    f.edge([(1000, 300), (1000, 275), (1250, 275), (1250, 190)], "旗標併入", at=(1125, 275), lw=72)
    f.edge([(1330, 320), (1370, 320), (1370, 175), (1410, 175)], "匯出範圍", at=(1370, 245), lw=76)
    f.edge([(1410, 350), (1330, 350)], "加入", at=(1370, 350), lw=44)
    f.edge([(1485, 300), (1485, 190)], "下載", at=(1485, 245), lw=44)
    f.edge([(250, 515), (330, 515)], "解析 + 剔錯", at=(290, 515), lw=84)
    f.edge([(520, 515), (870, 515)], "唯讀（不進分數）", kind="dash", at=(695, 515))
    f.edge([(1485, 370), (1485, 515), (1330, 515)], "回饋", at=(1485, 445), lw=44)
    f.edge([(1140, 530), (1100, 530), (1100, 580), (640, 580)], "未來：誤判回流成 Observation 標籤（首版無）", kind="dash", at=(880, 580))
    f.edge([(155, 370), (155, 392), (292, 392), (292, 380)], "", kind="dash")
    f.text(60, 410, "評鑑 → 特徵（可缺，虛線）", "ed")
    # loops
    f.edge([(1200, 120), (1200, 96), (155, 96), (155, 120)], "data_asof 過期 → 整條重跑（舊 scores is_current=0）", kind="loop", at=(950, 96))
    f.edge([(965, 190), (965, 215), (900, 215), (900, 190)], "退步不切換 → 新版留 trained", kind="loop", at=(935, 215))
    f.edge([(900, 370), (900, 400), (700, 400), (700, 370)], "同名排除 → 重算該 linker", kind="loop", at=(800, 400))
    # cross-cut
    f.band(30, 640, 1540, 120, "橫向實體 —— 貫穿全流程", "每階段寫一列；設定被評分與連坐讀", fill=("#fffdf5", "#e8d9a8"))
    f.node(60, 680, "PipelineRun", "每階段：筆數 · 耗時 · 失敗 · ok", sub2="pipeline_runs（持久）", w=460, h=66, fill=P)
    f.node(560, 680, "Setting", "門檻 · top N · 觀察期 · stale_days · data_asof", sub2="settings（持久）", w=460, h=66, fill=P)
    f.node(1060, 680, "重建 vs 持久", "build_db 只重建黃色表；紫色表保留、以版本/is_current 分批", sub2="scripts/update.py", w=480, h=66, fill=("#fff", "#5b7f9e"))
    f.text(30, 806, "圖例：黃 每次更新整表重建 · 紫 持久（重建時保留） · 藍 檔案系統 / 不落地 · 灰虛 唯讀消費端 · 橘 回頭路 · 與 §2 Delta 表逐張對帳（evaluations 以虛框標可缺）", "lg")
    f.render("dataflow-entities-target-2026-09.html", nav=(2, NAV))


# ---------------------------------------------------------------- 4/4 gates
def gates():
    f = Fig("關卡實況（target）：路上的檢查擋不擋得住",
            "三態，不是二態。✓ 有防護 · ◐ 只做一半 · ✗ 無防護。讀者是下一個要動這條流程的人：要在某關加東西，先看它現在擋什麼。",
            "每個關卡實際擋什麼、擋不住什麼（4/4；閱讀鏈結束）")
    rows = [
        ("時間切分 label_available", "特徵只用 asof 之前資料；標籤視窗超出資料截至則排除", "✓", "擋不住主檔屬性是「現在」快照（核定人數、負責人可能已變）→ §8-1"),
        ("模型 vs 規則自動切換", "beats_baseline=0 → Scorer 改用按次數排序", "✓", "只比整體 AUC / top100；某些年模型較差仍可能整體切換"),
        ("DBBuilder 筆數驟降中止", "筆數比上次少 >20% 或無新北資料 → 保留舊 DB", "✓", "擋不住「筆數相同但內容錯」"),
        ("恆等式剔錯 BS / IS", "OCR 誤讀 → 該頁科目設空，不進財務燈號", "✓", "兩份報告同位置同錯（機率低）"),
        ("匿名化白名單 API + Exporter", "任何回應與匯出不含 actor_name / owner / operator 原文", "✓", "園名本身含負責人姓名者（附設園）不在擋的範圍"),
        ("來源失敗沿用舊快照", "一站掛掉不停整條；資料品質頁標示", "✓", "data_asof 不前進 → 觸發 stale 警示（刻意）"),
        ("Approver AUC 降 >0.05", "新版留 trained，不自動切換", "◐", "只警示；demo 無登入，任何人可核准"),
        ("連坐同名旗標", "同名負責人標「待人工確認」", "◐", "仍列入名單直到人排除；距離門檻未定 → §8-2"),
        ("stale 警示（>90 天）", "頁首警示色 + 資料截至日", "◐", "不擋匯出，承辦仍可拿過期名單去排"),
        ("feedback 表", "承辦可申訴誤判", "✗", "只收不用，沒有任何流程讀它（首版明知）→ §8-10"),
    ]
    y0 = 112
    heads = [("關卡", 40, 300), ("設計上擋什麼", 350, 470), ("狀態", 830, 70), ("擋不住什麼 / 怎麼被跨過去", 910, 660)]
    f.add(f'<rect x="30" y="{y0}" width="1540" height="40" rx="8" fill="#f0f2f5"/>')
    for h, x, w in heads:
        f.text(x + 10, y0 + 26, h, "bl", "#2b5a7e")
    col = {"✓": ("#f6ffed", "#52c41a"), "◐": ("#fffbe6", "#d48806"), "✗": ("#fff2f0", "#ff4d4f")}
    for i, (g, what, st, miss) in enumerate(rows):
        y = y0 + 48 + i * 66
        fc, sc = col[st]
        f.add(f'<rect x="30" y="{y}" width="1540" height="58" rx="8" fill="{"#fff" if i % 2 else "#fafcff"}" stroke="#e5e9ee"/>')
        f.text(50, y + 36, g, "n")
        f.text(360, y + 36, what, "lg")
        f.add(f'<rect x="840" y="{y+11}" width="50" height="36" rx="8" fill="{fc}" stroke="{sc}" stroke-width="2"/>')
        f.text(865, y + 37, st, "n", sc, anchor="middle")
        f.text(920, y + 36, miss, "lg", "#6b7680" if st == "✓" else "#a35f00" if st == "◐" else "#a33")
    f.text(30, 836, "對照組：前六列真的擋得住。◐ 三列是 demo 階段的取捨，試辦前要補「人核准」與「距離門檻」；✗ 一列首版明知不用，列在這裡是為了不被忘掉。", "lg")
    f.render("dataflow-gates-target-2026-09.html", nav=(3, NAV))


# ---------------------------------------------------------------- ER
def er():
    f = Fig("ER 圖：新表靠哪個鍵串起來、哪幾張重建",
            "每個實體只列 5–8 個關鍵欄位。黃 = 每次更新整表重建；紫 = 持久。箭頭從 FK 指向 PK，箭頭端是 N。",
            "表之間的鍵與 cardinality（§2 的圖形版；流動看 3/4）")
    R, P = C["store"], C["model"]

    def ent(x, y, name, fields, fill, w=250):
        h = 30 + 20 * len(fields)
        f.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill[0]}" stroke="{fill[1]}" stroke-width="2"/>')
        f.add(f'<rect x="{x}" y="{y}" width="{w}" height="28" rx="8" fill="{fill[1]}" opacity=".35"/>')
        f.text(x + 12, y + 20, name, "n")
        for i, fl in enumerate(fields):
            f.text(x + 12, y + 46 + i * 20, fl, "mono", "#14171a")
        return (x, y, w, h)

    ent(40, 110, "preschools（重建）", ["id PK", "title · city · town · type", "owner · operator", "count_approved · monthly", "pre_public · reg_date"], R)
    ent(40, 270, "penalties（重建）", ["penalty_id PK", "preschool_id FK", "date · law_article", "punishment · actor_role", "is_safety"], R)
    ent(40, 430, "evaluations（重建）", ["preschool_id FK", "result · fetched_at"], R)
    ent(40, 520, "statements / ratios（重建）", ["preschool_id FK", "fiscal_year", "bs_* · is_* · 比率", "bs_ok · is_ok"], R)
    ent(370, 110, "linkers（重建）", ["linker_id PK", "kind owner|operator", "key_name UNIQUE(kind,key)", "code O-017 / L-004", "n_schools"], R)
    ent(370, 270, "preschool_linkers（重建）", ["preschool_id FK", "linker_id FK", "same_name_flag", "excluded_by_user（保留）"], R)
    ent(370, 420, "observations（重建）", ["obs_id PK", "preschool_id FK · asof_date", "trigger · penalty_id FK?", "特徵欄 ×20", "label_repeat_12m · label_available"], R)
    ent(700, 110, "models（持久）", ["model_id PK", "trained_at · data_asof · algo", "auc · pr_auc · top100_cov", "baseline_* · beats_baseline", "status trained→active→superseded"], P)
    ent(700, 270, "backtests（持久）", ["model_id FK · obs_year", "n_obs · n_pos", "auc · top50/100/200", "lead_days_median"], P)
    ent(700, 420, "scores（持久）", ["score_id PK", "model_id FK? · preschool_id FK", "asof_date · method", "prob_12m · score · rank · level", "top_features JSON · is_current"], P)
    ent(1030, 110, "watchlist（持久）", ["preschool_id FK · asof_date", "reason penalised|owner|operator", "source_preschool_id FK?", "source_penalty_id FK? · linker_id FK?", "is_current"], P)
    ent(1030, 290, "season_list（持久）", ["preschool_id PK/FK", "added_at · added_by"], P)
    ent(1030, 380, "feedback（持久）", ["feedback_id PK", "page · preschool_id FK?", "text · created_at"], P)
    ent(1360, 110, "settings（持久）", ["key PK", "value"], P, w=210)
    ent(1360, 200, "pipeline_runs（持久）", ["run_id · stage", "started_at · seconds", "n_rows · n_failed · ok"], P, w=210)
    ent(40, 650, "cardinality", ["preschools 1—N penalties / observations / scores / watchlist / feedback", "preschools N—M linkers（via preschool_linkers）· models 1—N backtests / scores", "preschools 1—1 season_list / evaluations · watchlist.source_* → preschools / penalties / linkers"], ("#fff", "#5b7f9e"), w=1100)
    ent(1160, 650, "重建 vs 持久", ["黃：build_db 整表重寫（來源表）", "紫：各模組追加，status / is_current 分版", "excluded_by_user：重建時回填"], ("#fff", "#5b7f9e"), w=410)
    # relations (FK -> PK)
    f.edge([(140, 270), (140, 250)], "", kind="flow")
    f.edge([(40, 470), (34, 470), (34, 200), (40, 200)], "", kind="flow")
    f.edge([(40, 580), (31, 580), (31, 160), (40, 160)], "", kind="flow")
    f.edge([(370, 300), (290, 300), (290, 180)], "N:1", at=(330, 300), lw=40)
    f.edge([(495, 270), (495, 250)], "", kind="flow")
    f.edge([(370, 460), (330, 460), (330, 230), (290, 230)], "N:1", at=(330, 345), lw=40)
    f.edge([(370, 500), (300, 500), (300, 330), (292, 330)], "penalty_id?", at=(300, 415), lw=90)
    f.edge([(825, 270), (825, 250)], "", kind="flow")
    f.edge([(825, 420), (825, 400)], "", kind="flow")
    f.edge([(1030, 150), (1000, 150), (1000, 98), (200, 98), (200, 110)], "preschool_id", at=(600, 98), lw=100)
    f.edge([(1030, 220), (985, 220), (985, 250), (660, 250), (660, 160), (620, 160)], "linker_id?", at=(820, 250), lw=84)
    f.edge([(1155, 290), (1155, 260)], "", kind="flow")
    f.edge([(1155, 380), (1155, 350)], "", kind="flow")
    f.text(30, 806, "FK → PK；箭頭端是 N。scores / observations / watchlist 的 preschool_id 皆指向 preschools.id（圖上只畫代表性幾條，其餘見 cardinality 框）。", "lg")
    f.render("er-smart-watchdog-2026-09.html")


if __name__ == "__main__":
    modules(); functions(); entities(); gates(); er()
