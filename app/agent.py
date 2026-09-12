"""P4b AgentService — read-only Q&A over v_* views (架構定調 9, mirrors PTI-ARES AgentService).

Tools: sql_readonly (ro URI + authorizer that only allows v_* + AST-ish allowlist + LIMIT 200 + 5 s progress
abort), explain_score, get_schedule, get_finance. No write tools exist. Every turn is logged to
app_agent_turns. Without ANTHROPIC_API_KEY the service reports disabled and the API returns 409.
"""
from __future__ import annotations

try:  # .env in the repo root: ANTHROPIC_API_KEY / GOOGLE_MAPS_API_KEY / WATCHDOG_DB
    from dotenv import load_dotenv
    import pathlib as _pl
    load_dotenv(_pl.Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import json
import os
import pathlib
import re
import sqlite3
import time
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODEL = os.environ.get("WATCHDOG_AGENT_MODEL", "claude-sonnet-5")
PROVIDERS = {  # env key → (label, default model). First configured one wins unless WATCHDOG_AGENT_PROVIDER is set.
    "anthropic": ("ANTHROPIC_API_KEY", "Claude", os.environ.get("WATCHDOG_AGENT_MODEL", "claude-sonnet-5")),
    "openai": ("OPENAI_API_KEY", "OpenAI", os.environ.get("WATCHDOG_OPENAI_MODEL", "gpt-5")),
    "gemini": ("GEMINI_API_KEY", "Gemini", os.environ.get("WATCHDOG_GEMINI_MODEL", "gemini-2.5-flash")),
}
MAX_ROWS = 200
FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|replace|vacuum|reindex|begin|commit|rollback)\b", re.I)

SYSTEM = """你是新北市教保機構稽查覆核工具「小小守護員」的資料助理，只回答資料能證明的事，繁體中文、結論先行、不超過五句。
可用資料（只讀 view）：v_preschools(id,title,town,type,reg_date,count_approved,is_active,lng,lat)、v_penalties(preschool_id,date,law,law_article,punishment,is_child_safety,event_id)、
v_penalty_events(preschool_id,date,n_rows,articles,is_child_safety,has_stop_enroll)、v_scores(preschool_id,prob_12m,risk_01,score,rank,level,reason,method)、
v_watchlist(preschool_id,tier,reason,source_preschool_id,linker_id)、v_preschool_linkers(preschool_id,code,name,kind,n_schools,same_name_flag,excluded_by_user)、
v_schedule_visits(preschool_id,week_no,inspector_no,rank,reason,pinned)、v_season_list、v_models、v_backtests、v_settings、v_ratios(preschool_id,title,fiscal_year,payload JSON)。
新北市園所請加 city='新北市'。等級：高／中／低／無紀錄／停辦。v_penalties.law 是條文與違規描述原文，回答時說明具體違反什麼（例：不當對待幼兒、超收、師生比），不要只講第幾條。負責人姓名在 v_linkers.name／v_preschool_linkers.name（公開登記資料）；行為人（被罰個人）姓名不存在於資料，也不要猜測。
分數與名單由規則與排程器決定，你不能改；被問到「為什麼」用 explain_score。回答附上你查了哪個 view。"""

TOOLS = [
    {"name": "sql_readonly", "description": "對去識別 view 執行單一 SELECT（自動加 LIMIT 200）。", "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}},
    {"name": "explain_score", "description": "取得某園目前分數、等級、理由與特徵。", "input_schema": {"type": "object", "properties": {"preschool_id": {"type": "string"}, "title": {"type": "string"}}}},
    {"name": "get_schedule", "description": "目前排程摘要（產能、覆蓋率、某週或某園的訪視）。", "input_schema": {"type": "object", "properties": {"week_no": {"type": "integer"}, "preschool_id": {"type": "string"}}}},
    {"name": "get_finance", "description": "某非營利園的財務比率（最近年度）。", "input_schema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}},
]


BANNED_COLUMNS = {"operator", "actor", "actor_name", "address", "tel"}


def _authorizer(action, arg1, arg2, dbname, source):
    """Allow reads only through v_* views; personal-data columns are denied even if a view asked."""
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        if (arg2 or "") in BANNED_COLUMNS:
            return sqlite3.SQLITE_DENY
        tbl, via = arg1 or "", source or ""
        if tbl.startswith("v_") or via.startswith("v_") or tbl.startswith("sqlite_"):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_DENY


class AgentService:
    def __init__(self, db_path: pathlib.Path):
        self.db_path = pathlib.Path(db_path)

    @property
    def available(self) -> list[str]:
        return [k for k, (env, _, _) in PROVIDERS.items() if os.environ.get(env)]

    @property
    def provider(self) -> str | None:
        want = os.environ.get("WATCHDOG_AGENT_PROVIDER")
        av = self.available
        if want in av:
            return want
        return av[0] if av else None

    @property
    def enabled(self) -> bool:
        return self.provider is not None

    def status(self) -> dict:
        return {"enabled": self.enabled, "provider": self.provider, "available": self.available,
                "models": {k: PROVIDERS[k][2] for k in self.available}, "labels": {k: v[1] for k, v in PROVIDERS.items()}}

    # ------------------------------------------------------------------ tools
    def _ro(self) -> sqlite3.Connection:
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        con.set_authorizer(_authorizer)
        t0 = time.time()
        con.set_progress_handler(lambda: 1 if time.time() - t0 > 5 else 0, 10000)
        return con

    def sql_readonly(self, sql: str) -> dict:
        s = sql.strip().rstrip(";")
        if not re.match(r"^(select|with)\b", s, re.I):
            return {"error": "只允許單一 SELECT 查詢"}  # sqlite3.execute itself refuses multi-statement strings; the authorizer is the real gate
        if not re.search(r"\blimit\s+\d+", s, re.I):
            s += f" LIMIT {MAX_ROWS}"
        try:
            con = self._ro()
            rows = [dict(r) for r in con.execute(s).fetchmany(MAX_ROWS)]
            return {"rows": rows, "n": len(rows)}
        except sqlite3.DatabaseError as e:
            return {"error": f"查詢被拒或失敗：{e}"}

    def explain_score(self, preschool_id: str | None = None, title: str | None = None) -> dict:
        con = self._ro()
        if not preschool_id and title:
            r = con.execute("SELECT id FROM v_preschools WHERE title LIKE ? AND city='新北市' LIMIT 1", (f"%{title}%",)).fetchone()
            preschool_id = r["id"] if r else None
        if not preschool_id:
            return {"error": "找不到該園"}
        r = con.execute("SELECT s.*, p.title, p.town FROM v_scores s JOIN v_preschools p ON p.id=s.preschool_id WHERE s.preschool_id=?", (preschool_id,)).fetchone()
        if not r:
            return {"error": "無分數"}
        d = dict(r); d["top_features"] = json.loads(d["top_features"]) if d.get("top_features") else None
        d["watch"] = [dict(w) for w in con.execute("SELECT tier, reason FROM v_watchlist WHERE preschool_id=?", (preschool_id,))]
        return d

    def get_schedule(self, week_no: int | None = None, preschool_id: str | None = None) -> dict:
        con = self._ro()
        s = con.execute("SELECT * FROM v_schedule").fetchone()
        if not s:
            return {"error": "尚無排程"}
        q, args = "SELECT v.*, p.title, p.town FROM v_schedule_visits v JOIN v_preschools p ON p.id=v.preschool_id WHERE 1=1", []
        if week_no:
            q += " AND week_no=?"; args.append(week_no)
        if preschool_id:
            q += " AND v.preschool_id=?"; args.append(preschool_id)
        rows = [dict(r) for r in con.execute(q + " ORDER BY week_no, inspector_no LIMIT 200", args)]
        return {"schedule": dict(s), "visits": rows, "n": len(rows)}

    def get_finance(self, title: str) -> dict:
        con = self._ro()
        r = con.execute("SELECT title, fiscal_year, payload FROM v_ratios WHERE title LIKE ? ORDER BY fiscal_year DESC LIMIT 1", (f"%{title}%",)).fetchone()
        if not r:
            return {"error": "無財報（私立園無公開財報）"}
        pl = json.loads(r["payload"])
        return {"title": r["title"], "fiscal_year": r["fiscal_year"], "ratios": {k: pl.get(k) for k in ("人事費率", "每核定名額收入(千)", "餘絀率", "流動比", "負債比", "現金月數")}}

    def run_tool(self, name: str, args: dict) -> dict:
        fn = {"sql_readonly": self.sql_readonly, "explain_score": self.explain_score, "get_schedule": self.get_schedule, "get_finance": self.get_finance}.get(name)
        return fn(**args) if fn else {"error": f"未知工具 {name}"}

    # ------------------------------------------------------------------ chat
    def ask(self, question: str, page: str = "", session_id: str = "demo", max_turns: int = 6, provider: str | None = None) -> dict:
        provider = provider if provider in self.available else self.provider
        if not provider:
            return {"error": "AGENT_DISABLED", "message": "未設定 ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY，問答停用"}
        calls, t0 = [], time.time()
        runner = {"anthropic": self._ask_anthropic, "openai": self._ask_openai, "gemini": self._ask_gemini}[provider]
        answer = runner(question, calls, max_turns)
        latency = int((time.time() - t0) * 1000)
        con = sqlite3.connect(self.db_path)
        con.execute("INSERT INTO app_agent_turns(session_id, page, question, answer, tool_calls, latency_ms, created_at) VALUES (?,?,?,?,?,?,?)",
                    (session_id, page, question, answer, json.dumps(calls, ensure_ascii=False, default=str), latency, date.today().isoformat()))
        con.commit(); con.close()
        return {"answer": answer, "tool_calls": calls, "latency_ms": latency, "provider": provider, "model": PROVIDERS[provider][2]}

    def _record(self, calls, name, args, out):
        calls.append({"tool": name, "input": args, "n": out.get("n"), "error": out.get("error")})
        return json.dumps(out, ensure_ascii=False, default=str)[:12000]

    # ---- Anthropic (Messages API tool use)
    def _ask_anthropic(self, question: str, calls: list, max_turns: int) -> str:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        messages = [{"role": "user", "content": question}]
        for _ in range(max_turns):
            resp = client.messages.create(model=PROVIDERS["anthropic"][2], max_tokens=1200, system=SYSTEM, tools=TOOLS, messages=messages)
            if resp.stop_reason != "tool_use":
                return "".join(b.text for b in resp.content if b.type == "text")
            messages.append({"role": "assistant", "content": resp.content})
            results = [{"type": "tool_result", "tool_use_id": b.id, "content": self._record(calls, b.name, dict(b.input), self.run_tool(b.name, dict(b.input)))}
                       for b in resp.content if b.type == "tool_use"]
            messages.append({"role": "user", "content": results})
        return "（已達工具呼叫上限，請縮小問題）"

    # ---- OpenAI (Chat Completions function calling)
    def _ask_openai(self, question: str, calls: list, max_turns: int) -> str:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in TOOLS]
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
        for _ in range(max_turns):
            resp = client.chat.completions.create(model=PROVIDERS["openai"][2], messages=messages, tools=tools)
            msg = resp.choices[0].message
            if not msg.tool_calls:
                return msg.content or ""
            messages.append({"role": "assistant", "content": msg.content, "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": self._record(calls, tc.function.name, args, self.run_tool(tc.function.name, args))})
        return "（已達工具呼叫上限，請縮小問題）"

    # ---- Gemini (google-genai function calling, manual loop so every call is logged)
    def _ask_gemini(self, question: str, calls: list, max_turns: int) -> str:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        decls = [types.FunctionDeclaration(name=t["name"], description=t["description"], parameters=t["input_schema"]) for t in TOOLS]
        cfg = types.GenerateContentConfig(system_instruction=SYSTEM, tools=[types.Tool(function_declarations=decls)],
                                          automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        contents = [types.Content(role="user", parts=[types.Part(text=question)])]
        for _ in range(max_turns):
            resp = client.models.generate_content(model=PROVIDERS["gemini"][2], contents=contents, config=cfg)
            cand = resp.candidates[0].content
            fcs = [p.function_call for p in cand.parts if getattr(p, "function_call", None)]
            if not fcs:
                return "".join(p.text or "" for p in cand.parts if getattr(p, "text", None))
            contents.append(cand)
            parts = []
            for fc in fcs:
                args = dict(fc.args or {})
                out = self.run_tool(fc.name, args); self._record(calls, fc.name, args, out)
                parts.append(types.Part.from_function_response(name=fc.name, response={"result": json.loads(json.dumps(out, default=str))}))
            contents.append(types.Content(role="user", parts=parts))
        return "（已達工具呼叫上限，請縮小問題）"
