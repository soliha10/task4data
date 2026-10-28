from __future__ import annotations

import json
import os
from datetime import date

import requests

import analysis
import store_service

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-2.5-flash"
REQUEST_TIMEOUT_SECONDS = 90
TEMPERATURE = 0.1
MAX_STEPS = 8
MAX_HISTORY_TURNS = 20
MAX_ERROR_LENGTH = 300
MAX_QUERY_ROWS = 100
DEFAULT_QUERY_ROWS = 25
DEFAULT_GROUPS = 20
DEFAULT_CUSTOMERS = 5
DEFAULT_RANKING = 10
DEFAULT_DISTINCT = 1000

SORT_DIRECTIONS = ["asc", "desc"]
DASHBOARD_TABS = ["revenue", "customers", "authors", "explorer"]
AGGREGATE_METRICS = ["count", "sum", "avg", "min", "max", "median", "nunique"]
CUSTOMER_RANKINGS = ["spent", "orders", "books"]
REVENUE_GRANULARITIES = ["total", "day", "month", "year"]
NO_ANSWER = "(no answer)"
STEP_LIMIT_REPLY = "Sorry, I could not finish this request within the step limit."


class AssistantError(Exception):
    pass


def _schema(kind: str, description: str | None = None, **extra) -> dict:
    schema = {"type": kind, **extra}
    return {**schema, "description": description} if description else schema


def _string(description: str | None = None, enum: list[str] | None = None) -> dict:
    return _schema("STRING", description, **({"enum": enum} if enum else {}))


def _integer(description: str | None = None) -> dict:
    return _schema("INTEGER", description)


def _string_list(description: str | None = None) -> dict:
    return _schema("ARRAY", description, items=_string())


def _fn(name: str, description: str, properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"name": name, "description": description,
            "parameters": {"type": "OBJECT", "properties": properties, "required": list(required)}}


CONDITION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "field": _string("Column name of the table."),
        "op": _string(enum=list(store_service.FILTER_OPS)),
        "value": _string("Single value (numbers and dates as strings, dates YYYY-MM-DD)."),
        "values": _string_list("Several values: string ops match ANY of them; 'between' uses [min, max]."),
        "negate": _schema("BOOLEAN", "Invert this condition."),
    },
    "required": ["field", "op"],
}

FILTER_SCHEMA = _schema(
    "OBJECT", "Row filter. Conditions are combined with AND (match='all', default) or OR (match='any').",
    properties={"match": _string(enum=["all", "any"]), "conditions": _schema("ARRAY", items=CONDITION_SCHEMA)})

STORE_PARAM = _string("Store name: STORE1..STORE5. Use the currently open store unless the user names another.")
TABLE_PARAM = _string(enum=list(store_service.TABLES))
DATE_RANGE_PARAMS = {"start_date": _string(), "end_date": _string()}
STORE_ONLY = {"store": STORE_PARAM}
STORE_AND_TABLE = {"store": STORE_PARAM, "table": TABLE_PARAM}
EUR_RATE = analysis.EUR_TO_USD

TOOLS = [
    _fn("get_store_summary", "Key dashboard metrics of a store: top 5 revenue days, unique (reconciled) users, "
        "unique author sets, most popular author set, best buyer with all alias ids, totals, date range, data quality.",
        STORE_ONLY, ("store",)),
    _fn("compare_stores", "The same key metrics for all stores side by side.", {}),
    _fn("query_rows", f"Return rows of a table matching a filter (max {MAX_QUERY_ROWS}) plus the exact total count of matches.",
        {**STORE_AND_TABLE, "filter": FILTER_SCHEMA, "sort_by": _string(), "sort_dir": _string(enum=SORT_DIRECTIONS),
         "limit": _integer(), "fields": _string_list()},
        ("store", "table")),
    _fn("aggregate", "Exact count/sum/avg/min/max/median/nunique of a field, optionally grouped by columns and "
        "filtered. Use for every 'how many', 'total', 'average', 'which genre has most' question.",
        {**STORE_AND_TABLE, "metric": _string(enum=AGGREGATE_METRICS),
         "field": _string("Column to aggregate (omit for plain row count)."), "group_by": _string_list(),
         "filter": FILTER_SCHEMA, "sort": _string(enum=SORT_DIRECTIONS),
         "limit": _integer(f"Max groups returned (default {DEFAULT_GROUPS}).")},
        ("store", "table", "metric")),
    _fn("revenue", f"Revenue in USD (EUR converted at {EUR_RATE}), orders and books sold for a date range, "
        "optionally as a day/month/year series.",
        {**STORE_ONLY, **DATE_RANGE_PARAMS, "granularity": _string(enum=REVENUE_GRANULARITIES)}, ("store",)),
    _fn("top_customers", "Top real customers (aliases merged) by spending, orders or books bought, with all their user ids.",
        {**STORE_ONLY, "n": _integer(), "by": _string(enum=CUSTOMER_RANKINGS)}, ("store",)),
    _fn("find_person", "Look up users by id, name, e-mail, phone or address fragment; returns each real person with all alias records.",
        {**STORE_ONLY, "text": _string()}, ("store", "text")),
    _fn("author_stats", "Books, copies sold and revenue of an author (by name fragment), incl. co-authored books.",
        {**STORE_ONLY, "author": _string()}, ("store", "author")),
    _fn("author_set_ranking", "Author sets ranked by copies sold.", {**STORE_ONLY, "n": _integer()}, ("store",)),
    _fn("distinct_values", "Distinct values of a column with row counts (e.g. all book titles, genres, publishers).",
        {**STORE_AND_TABLE, "field": _string(), "limit": _integer()}, ("store", "table", "field")),
    _fn("titles_with_person_names", "Heuristic list of book titles containing first/last names known from the data. "
        "Review it: drop common words (Summer, Star, Moon, Green...) and add famous names it missed "
        "(check distinct_values of title).", STORE_ONLY, ("store",)),
    _fn("ui_open_store", "Switch the dashboard to another store.", STORE_ONLY, ("store",)),
    _fn("ui_open_tab", "Open a dashboard tab.", {"tab": _string(enum=DASHBOARD_TABS)}, ("tab",)),
    _fn("ui_filter_table", "Show only rows matching the filter in the explorer table (opens the explorer tab). "
        "To 'filter out'/hide rows, use negate=true or not_in. For exact title lists use field=title op=in values=[...].",
        {**STORE_AND_TABLE, "filter": FILTER_SCHEMA, "description": _string("Short human label of the filter.")},
        ("store", "table", "filter", "description")),
    _fn("ui_clear_filter", "Remove the filter from an explorer table.", {"table": TABLE_PARAM}, ("table",)),
    _fn("ui_sort_table", "Sort an explorer table.",
        {"table": TABLE_PARAM, "field": _string(), "dir": _string(enum=SORT_DIRECTIONS)}, ("table", "field")),
    _fn("ui_chart_range", "Zoom the daily revenue chart to a date range (omit both to reset).", DATE_RANGE_PARAMS),
]


def system_prompt(current_store: str) -> str:
    table_columns = "; ".join(f"{name}: {', '.join(store_service.columns(name))}" for name in store_service.TABLES)
    return f"""You are the analytics assistant of a BI dashboard about online book stores. Today is {date.today():%Y-%m-%d}.
Stores: {', '.join(store_service.STORES)}. The user currently has {current_store} open; use it unless another store is named.
Tables and columns -> {table_columns}.
Notes: prices are in USD (EUR converted at {EUR_RATE}). orders.paid_price = quantity * unit_price_usd. users.person_id groups
alias records of the same real person (only one of name/address/phone/email differs), so 'unique/real users' means
distinct person_id. books.author is a comma separated author set; books.sold_qty is copies sold.

Rules:
- NEVER compute, count, sum, estimate or recall numbers yourself. Every number, list or fact about the data must come
  from a tool result in this conversation. If tools cannot answer, say so.
- Prefer one precise aggregate/query call over fetching many rows. Report exact values (money with 2 decimals, '$').
- Commands that change what the dashboard shows (filter, hide, show, sort, open, switch, zoom) must be executed with
  ui_* tools, then confirm briefly what was done and how many rows match (from the tool result).
- For subjective classification (e.g. titles containing people's names) get the candidate values with tools, decide,
  then filter using exact values with op 'in'/'not_in'.
- Answer in the user's language, concisely; use short bullet lists for multiple items. Dates as YYYY-MM-DD.
"""


def _int_arg(args: dict, key: str, default: int) -> int:
    return int(args.get(key) or default)


def _query_rows(args: dict) -> dict:
    limit = store_service.clamp(_int_arg(args, "limit", DEFAULT_QUERY_ROWS), 1, MAX_QUERY_ROWS)
    return store_service.query(args["store"], args["table"], args.get("filter"), args.get("sort_by"),
                               args.get("sort_dir", "asc"), limit, fields=args.get("fields"))


def _aggregate(args: dict) -> dict:
    return store_service.aggregate(args["store"], args["table"], args.get("metric", "count"), args.get("field"),
                                   args.get("group_by"), args.get("filter"), args.get("sort", "desc"),
                                   _int_arg(args, "limit", DEFAULT_GROUPS))


def _revenue(args: dict) -> dict:
    return store_service.revenue(args["store"], args.get("start_date"), args.get("end_date"),
                                 args.get("granularity", "total"))


def _top_customers(args: dict) -> dict:
    count = _int_arg(args, "n", DEFAULT_CUSTOMERS)
    return {"customers": store_service.top_customers(args["store"], count, args.get("by") or "spent")}


def _author_set_ranking(args: dict) -> dict:
    return {"ranking": store_service.author_set_ranking(args["store"], _int_arg(args, "n", DEFAULT_RANKING))}


def _distinct_values(args: dict) -> dict:
    return store_service.distinct_values(args["store"], args["table"], args["field"],
                                         _int_arg(args, "limit", DEFAULT_DISTINCT))


DATA_TOOLS = {
    "get_store_summary": lambda args: store_service.compact_summary(args["store"]),
    "compare_stores": lambda args: {"stores": [store_service.compact_summary(name) for name in store_service.STORES]},
    "query_rows": _query_rows,
    "aggregate": _aggregate,
    "revenue": _revenue,
    "top_customers": _top_customers,
    "find_person": lambda args: {"people": store_service.find_person(args["store"], args["text"])},
    "author_stats": lambda args: store_service.author_stats(args["store"], args["author"]),
    "author_set_ranking": _author_set_ranking,
    "distinct_values": _distinct_values,
    "titles_with_person_names": lambda args: store_service.titles_with_person_names(args["store"]),
}


def _record(actions: list, action_type: str, **fields) -> dict:
    actions.append({"type": action_type, **fields})
    return {"ok": True}


def _ui_open_store(args: dict, actions: list) -> dict:
    store_name = store_service.normalize_store(args["store"])
    return {**_record(actions, "open_store", store=store_name), "store": store_name}


def _ui_filter_table(args: dict, actions: list) -> dict:
    store_name = store_service.normalize_store(args["store"])
    matching_rows = store_service.query(store_name, args["table"], args["filter"], limit=1)["total"]
    _record(actions, "filter_table", store=store_name, table=args["table"], filter=args["filter"],
            description=args.get("description", ""))
    return {"ok": True, "matching_rows": matching_rows,
            "total_rows": len(store_service.table(store_name, args["table"]))}


def _ui_sort_table(args: dict, actions: list) -> dict:
    if args["field"] not in store_service.columns(args["table"]):
        raise ValueError(f"Unknown field '{args['field']}'")
    return _record(actions, "sort_table", table=args["table"], field=args["field"], dir=args.get("dir", "asc"))


UI_TOOLS = {
    "ui_open_store": _ui_open_store,
    "ui_open_tab": lambda args, actions: _record(actions, "open_tab", tab=args["tab"]),
    "ui_filter_table": _ui_filter_table,
    "ui_clear_filter": lambda args, actions: _record(actions, "clear_filter", table=args["table"]),
    "ui_sort_table": _ui_sort_table,
    "ui_chart_range": lambda args, actions: _record(actions, "chart_range", start=args.get("start_date"),
                                                    end=args.get("end_date")),
}


def execute(name: str, args: dict, actions: list) -> dict:
    args = dict(args or {})
    if name in DATA_TOOLS:
        return DATA_TOOLS[name](args)
    if name in UI_TOOLS:
        return UI_TOOLS[name](args, actions)
    raise ValueError(f"Unknown tool {name}")


def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise AssistantError("GEMINI_API_KEY is not configured on the server.")
    return key


def _request_body(contents: list, system: str) -> dict:
    return {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "tools": [{"functionDeclarations": TOOLS}],
        "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
        "generationConfig": {"temperature": TEMPERATURE},
    }


def _error_message(response: requests.Response) -> str:
    try:
        return response.json().get("error", {}).get("message", response.text)
    except ValueError:
        return response.text


def _call_gemini(contents: list, system: str) -> dict:
    key = _api_key()
    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
    response = requests.post(GEMINI_URL.format(model=model), json=_request_body(contents, system),
                             timeout=REQUEST_TIMEOUT_SECONDS,
                             headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    if response.status_code != 200:
        raise AssistantError(f"Gemini API error {response.status_code}: {_error_message(response)[:MAX_ERROR_LENGTH]}")
    return response.json()


def _text_content(turn: dict) -> dict:
    role = "model" if turn.get("role") == "assistant" else "user"
    return {"role": role, "parts": [{"text": str(turn.get("text", ""))}]}


def _conversation(history: list[dict]) -> list[dict]:
    contents = [_text_content(turn) for turn in history[-MAX_HISTORY_TURNS:] if turn.get("text")]
    if not contents or contents[-1]["role"] != "user":
        raise AssistantError("Empty question.")
    return contents


def _model_parts(data: dict) -> list:
    candidates = data.get("candidates") or []
    if not candidates:
        reason = data.get("promptFeedback", {}).get("blockReason", "no answer")
        raise AssistantError(f"The model returned no answer ({reason}).")
    content = candidates[0].get("content") or {"role": "model", "parts": []}
    return content.get("parts") or []


def _reply_text(parts: list) -> str:
    return "".join(part.get("text", "") for part in parts if not part.get("thought")).strip()


def _plain_json(value) -> dict:
    return json.loads(json.dumps(value, default=str))


def _function_response(call: dict, actions: list, trace: list) -> dict:
    name, args = call.get("name"), call.get("args") or {}
    try:
        result = execute(name, args, actions)
    except (ValueError, KeyError, TypeError) as error:
        result = {"error": str(error)}
    trace.append({"tool": name, "args": args})
    return {"functionResponse": {"name": name, "response": _plain_json(result)}}


def _answer(reply: str, actions: list, trace: list) -> dict:
    return {"reply": reply, "actions": actions, "tool_calls": trace}


def chat(history: list[dict], current_store: str | None) -> dict:
    current_store = store_service.normalize_store(current_store or store_service.STORES[0])
    contents = _conversation(history)
    system = system_prompt(current_store)
    actions: list = []
    trace: list = []
    for _ in range(MAX_STEPS):
        parts = _model_parts(_call_gemini(contents, system))
        calls = [part["functionCall"] for part in parts if "functionCall" in part]
        if not calls:
            return _answer(_reply_text(parts) or NO_ANSWER, actions, trace)
        contents.append({"role": "model", "parts": parts})
        contents.append({"role": "user", "parts": [_function_response(call, actions, trace) for call in calls]})
    return _answer(STEP_LIMIT_REPLY, actions, trace)
