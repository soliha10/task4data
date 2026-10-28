from __future__ import annotations

import operator
import re
from functools import lru_cache

import numpy as np
import pandas as pd

import analysis
from settings import DATA_DIR

STORES = [folder.name for folder in analysis.store_folders(DATA_DIR)]
TABLES = ("books", "users", "orders")

STRING_OPS = ("equals", "not_equals", "contains", "not_contains", "starts_with", "ends_with", "regex", "in", "not_in")
NUMERIC_OPS = ("gt", "gte", "lt", "lte", "between")
NULL_OPS = ("is_null", "not_null")
FILTER_OPS = STRING_OPS + NUMERIC_OPS + NULL_OPS
EQUALITY_OPS = {"equals", "not_equals", "in", "not_in"}
NEGATED_EQUALITY_OPS = {"not_equals", "not_in"}
COMPARISONS = {"gt": operator.gt, "gte": operator.ge, "lt": operator.lt, "lte": operator.le}
AGG_FUNCS = {"count", "sum", "avg", "mean", "min", "max", "nunique", "median"}
REVENUE_PERIOD_COLUMNS = {"day": "date", "month": "year_month", "year": "year"}

MAX_REVENUE_POINTS = 400
MAX_AUTHOR_TITLES = 50
MAX_PEOPLE_MATCHES = 10
MIN_NAME_LENGTH = 3

PERSON_COLUMNS = ["id", "name", "address", "phone", "email"]
PERSON_SEARCH_COLUMNS = ("name", "email", "address")
BOOK_COLUMNS = ["id", "title", "author", "genre", "publisher", "year", "authors"]
ORDER_COLUMNS = ["id", "user_id", "person_id", "book_id", "title", "author_set", "quantity", "currency",
                 "unit_price_orig", "unit_price_usd", "paid_price", "timestamp", "date"]
MONTH_FORMAT = "%Y-%m"
TIMESTAMP_FORMAT = f"{analysis.DATE_FORMAT} %H:%M:%S"

HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "prof", "rev", "fr", "sen", "rep", "gov", "pres", "amb", "hon", "msgr"}
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v", "md", "phd", "dds", "dvm", "jd", "esq", "ret", "dc", "cpa", "lld"}
STOPWORDS = {"the", "a", "an", "of", "and", "in", "to", "on", "for", "is", "it", "my", "me", "be", "at", "by", "as"}
NON_NAME_WORDS = HONORIFICS | NAME_SUFFIXES | STOPWORDS

WORD_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")
NON_DIGITS_RE = re.compile(r"\D")


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def _available(kind: str, name, options) -> ValueError:
    return ValueError(f"Unknown {kind} '{name}'. Available: {', '.join(options)}")


def _unknown_field(field, frame: pd.DataFrame) -> ValueError:
    return ValueError(f"Unknown field '{field}'. Fields: {', '.join(frame.columns)}")


def normalize_store(store: str | None) -> str:
    name = str(store or "").strip().upper().replace(" ", "")
    if name.isdigit():
        name = f"STORE{name}"
    if name not in STORES:
        raise _available("store", store, STORES)
    return name


def _to_python(value):
    return value.item() if hasattr(value, "item") else value


def _round(value):
    value = _to_python(value)
    return round(value, 2) if isinstance(value, float) else value


def _money(values: pd.Series) -> float:
    return round(float(values.sum()), 2)


def _count(values: pd.Series) -> int:
    return int(values.sum())


@lru_cache(maxsize=None)
def _load_store(name: str) -> analysis.Store:
    return analysis.load_store(DATA_DIR / name)


def store_data(name: str) -> analysis.Store:
    return _load_store(normalize_store(name))


@lru_cache(maxsize=None)
def _summary(name: str) -> dict:
    return analysis.summary(_load_store(name))


def summary(name: str) -> dict:
    return _summary(normalize_store(name))


def compact_summary(name: str) -> dict:
    return analysis.without_series(summary(name))


def _fill_counts(frame: pd.DataFrame, columns: list[str]) -> None:
    frame[columns] = frame[columns].fillna(0).astype("int64")


def _fill_money(frame: pd.DataFrame, column: str) -> None:
    frame[column] = frame[column].fillna(0).round(2)


def _merge_stats(frame: pd.DataFrame, stats: pd.DataFrame) -> pd.DataFrame:
    return frame.merge(stats, left_on="id", right_index=True, how="left")


def _books_table(store: analysis.Store) -> pd.DataFrame:
    sold = store.orders.groupby("book_id").agg(sold_qty=("quantity", "sum"), orders=("id", "count"),
                                               revenue=("paid_price", "sum"))
    books = store.books[BOOK_COLUMNS].copy()
    books["n_authors"] = books["authors"].map(len)
    books = _merge_stats(books.drop(columns="authors"), sold)
    _fill_counts(books, ["sold_qty", "orders"])
    _fill_money(books, "revenue")
    return books


def _users_table(store: analysis.Store) -> pd.DataFrame:
    spent = store.orders.groupby("user_id").agg(orders=("id", "count"), spent=("paid_price", "sum"))
    users = store.users[PERSON_COLUMNS + ["person_id"]].copy()
    users["aliases"] = users.groupby("person_id")["id"].transform("count")
    users = _merge_stats(users, spent)
    _fill_counts(users, ["orders"])
    _fill_money(users, "spent")
    return users


def _add_time_parts(orders: pd.DataFrame) -> None:
    timestamp = orders["timestamp"].dt
    orders["date"] = orders["date"].dt.strftime(analysis.DATE_FORMAT)
    orders["year_month"] = timestamp.strftime(MONTH_FORMAT)
    orders["year"] = timestamp.year
    orders["month"] = timestamp.month
    orders["weekday"] = timestamp.day_name()
    orders["hour"] = timestamp.hour
    orders["timestamp"] = timestamp.strftime(TIMESTAMP_FORMAT)


def _orders_table(store: analysis.Store) -> pd.DataFrame:
    titles = store.books[["id", "title"]].rename(columns={"id": "book_id"})
    orders = store.orders.merge(titles, on="book_id", how="left")[ORDER_COLUMNS].copy()
    orders = orders.rename(columns={"author_set": "author", "unit_price_orig": "unit_price"})
    _add_time_parts(orders)
    return orders


TABLE_BUILDERS = {"books": _books_table, "users": _users_table, "orders": _orders_table}


@lru_cache(maxsize=None)
def _table(name: str, table_name: str) -> pd.DataFrame:
    build = TABLE_BUILDERS.get(table_name)
    if build is None:
        raise _available("table", table_name, TABLES)
    return build(_load_store(name)).sort_values("id").reset_index(drop=True)


def table(name: str, table_name: str) -> pd.DataFrame:
    return _table(normalize_store(name), table_name)


def columns(table_name: str) -> list[str]:
    return list(table(STORES[0], table_name).columns)


def _as_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _condition_values(condition: dict) -> list:
    values = condition.get("values")
    if values is None or values == []:
        values = [condition.get("value")] if condition.get("value") is not None else []
    return [value for value in values if value is not None]


def _require_values(op: str, values: list) -> None:
    if not values:
        raise ValueError(f"Operator '{op}' needs a value")


def _blank(column: pd.Series) -> pd.Series:
    return column.isna() | (column.astype("string") == "")


def _numeric_mask(column: pd.Series, op: str, values: list) -> pd.Series:
    numeric = pd.api.types.is_numeric_dtype(column)
    compared = column if numeric else column.astype("string")
    bounds = [_as_number(value) if numeric else str(value) for value in values]
    _require_values(op, bounds)
    if op != "between":
        return COMPARISONS[op](compared, bounds[0])
    if len(bounds) < 2:
        raise ValueError("'between' needs two values: [min, max] (inclusive)")
    return (compared >= bounds[0]) & (compared <= bounds[1])


def _any_pattern(values: list) -> str:
    return "|".join(map(re.escape, values))


def _regex_mask(column: pd.Series, values: list) -> pd.Series:
    pattern = "|".join(f"(?:{value})" for value in values)
    try:
        return column.astype("string").fillna("").str.contains(pattern, case=False, regex=True)
    except re.error as error:
        raise ValueError(f"Invalid regex: {error}") from error


def _text_mask(column: pd.Series, op: str, values: list) -> pd.Series:
    if op == "regex":
        return _regex_mask(column, values)
    text = column.astype("string").str.lower().fillna("")
    needles = [str(value).lower() for value in values]
    matchers = {
        "equals": lambda: text.isin(needles),
        "in": lambda: text.isin(needles),
        "not_equals": lambda: ~text.isin(needles),
        "not_in": lambda: ~text.isin(needles),
        "contains": lambda: text.str.contains(_any_pattern(needles), regex=True),
        "not_contains": lambda: ~text.str.contains(_any_pattern(needles), regex=True),
        "starts_with": lambda: text.str.startswith(tuple(needles)),
        "ends_with": lambda: text.str.endswith(tuple(needles)),
    }
    return matchers[op]()


def _string_mask(column: pd.Series, op: str, values: list) -> pd.Series:
    _require_values(op, values)
    if not (pd.api.types.is_numeric_dtype(column) and op in EQUALITY_OPS):
        return _text_mask(column, op, values)
    mask = column.isin([_as_number(value) for value in values])
    return ~mask if op in NEGATED_EQUALITY_OPS else mask


def _operator_mask(column: pd.Series, op: str, values: list) -> pd.Series:
    if op == "is_null":
        return _blank(column)
    if op == "not_null":
        return ~_blank(column)
    if op in NUMERIC_OPS:
        return _numeric_mask(column, op, values)
    if op in STRING_OPS:
        return _string_mask(column, op, values)
    raise ValueError(f"Unknown operator '{op}'")


def _condition_mask(frame: pd.DataFrame, condition: dict) -> pd.Series:
    field = condition.get("field")
    if field not in frame.columns:
        raise _unknown_field(field, frame)
    op = condition.get("op", "equals")
    mask = _operator_mask(frame[field], op, _condition_values(condition)).fillna(False).astype(bool)
    return ~mask if condition.get("negate") else mask


def apply_filter(frame: pd.DataFrame, row_filter: dict | None) -> pd.DataFrame:
    if not row_filter or not row_filter.get("conditions"):
        return frame
    combine = operator.or_ if row_filter.get("match") == "any" else operator.and_
    masks = [_condition_mask(frame, condition) for condition in row_filter["conditions"]]
    combined = masks[0]
    for mask in masks[1:]:
        combined = combine(combined, mask)
    return frame[combined]


def jsonable(frame: pd.DataFrame) -> list[dict]:
    return frame.replace({np.nan: None}).astype(object).where(frame.notna(), None).to_dict(orient="records")


def _search(frame: pd.DataFrame, text: str) -> pd.DataFrame:
    text_columns = [column for column in frame.columns
                    if frame[column].dtype == object or str(frame[column].dtype) == "string"]
    needle = text.lower()
    hit = pd.Series(False, index=frame.index)
    for column in text_columns:
        hit |= frame[column].astype("string").str.lower().str.contains(needle, regex=False).fillna(False)
    return frame[hit]


def _sort(frame: pd.DataFrame, sort_by: str, sort_dir: str) -> pd.DataFrame:
    if sort_by not in frame.columns:
        raise ValueError(f"Unknown sort field '{sort_by}'")
    return frame.sort_values(sort_by, ascending=(sort_dir != "desc"), kind="stable")


def query(store_name: str, table_name: str, row_filter: dict | None = None, sort_by: str | None = None,
          sort_dir: str = "asc", limit: int = 25, offset: int = 0, search: str | None = None,
          fields: list[str] | None = None) -> dict:
    frame = apply_filter(table(store_name, table_name), row_filter)
    if search:
        frame = _search(frame, search)
    if sort_by:
        frame = _sort(frame, sort_by, sort_dir)
    page = frame.iloc[offset: offset + limit]
    if fields:
        page = page[[field for field in fields if field in page.columns]]
    return {"total": len(frame), "offset": offset, "rows": jsonable(page)}


def _validated_metric(metric: str, field: str | None) -> str:
    metric = metric.lower()
    if metric not in AGG_FUNCS:
        raise ValueError(f"metric must be one of {sorted(AGG_FUNCS)}")
    metric = "mean" if metric == "avg" else metric
    if metric != "count" and not field:
        raise ValueError(f"metric '{metric}' needs a field")
    return metric


def _require_fields(frame: pd.DataFrame, fields: list[str | None]) -> None:
    for field in fields:
        if field and field not in frame.columns:
            raise _unknown_field(field, frame)


def _aggregate_value(frame: pd.DataFrame, metric: str, field: str | None):
    if metric != "count":
        return getattr(frame[field], metric)()
    return len(frame) if field is None else int(frame[field].notna().sum())


def _grouped_values(frame: pd.DataFrame, metric: str, field: str | None, group_by: list[str]) -> pd.Series:
    groups = frame.groupby(group_by, dropna=False)
    if metric == "count" and field is None:
        return groups.size()
    return groups[field].agg(metric)


def _group_row(group_by: list[str], key, value) -> dict:
    keys = key if isinstance(key, tuple) else (key,)
    row = {column: (None if pd.isna(part) else _to_python(part)) for column, part in zip(group_by, keys)}
    row["value"] = _round(value)
    return row


def aggregate(store_name: str, table_name: str, metric: str = "count", field: str | None = None,
              group_by: list[str] | None = None, row_filter: dict | None = None, sort: str = "desc",
              limit: int = 20) -> dict:
    frame = apply_filter(table(store_name, table_name), row_filter)
    metric = _validated_metric(metric, field)
    _require_fields(frame, [field, *(group_by or [])])
    result = {"matched_rows": len(frame), "metric": metric, "field": field}
    if not group_by:
        return {**result, "value": _round(_aggregate_value(frame, metric, field))}
    values = _grouped_values(frame, metric, field, group_by).sort_values(ascending=(sort == "asc"), kind="stable")
    rows = [_group_row(group_by, key, value) for key, value in values.head(limit).items()]
    return {**result, "group_by": group_by, "total_groups": len(values), "groups": rows}


def _revenue_series(orders: pd.DataFrame, granularity: str) -> list[dict] | None:
    period_column = REVENUE_PERIOD_COLUMNS.get(granularity)
    if not period_column:
        return None
    totals = orders.groupby(period_column)["paid_price"].sum().round(2)
    return [{"period": str(period), "revenue_usd": float(value)} for period, value in totals.items()][:MAX_REVENUE_POINTS]


def revenue(store_name: str, start_date: str | None = None, end_date: str | None = None,
            granularity: str = "total") -> dict:
    orders = table(store_name, "orders")
    if start_date:
        orders = orders[orders["date"] >= start_date]
    if end_date:
        orders = orders[orders["date"] <= end_date]
    has_orders = len(orders) > 0
    result = {"orders": len(orders), "books_sold": _count(orders["quantity"]),
              "revenue_usd": _money(orders["paid_price"]),
              "start_date": orders["date"].min() if has_orders else None,
              "end_date": orders["date"].max() if has_orders else None}
    series = _revenue_series(orders, granularity)
    return result if series is None else {**result, "series": series}


def top_customers(store_name: str, count: int = 5, by: str = "spent") -> list[dict]:
    store = store_data(store_name)
    ranking = store.orders.groupby("person_id").agg(spent=("paid_price_dec", "sum"), orders=("id", "count"),
                                                    books=("quantity", "sum"))
    ranking["spent"] = ranking["spent"].map(float)
    ranking = ranking.sort_values(by if by in ranking.columns else "spent", ascending=False).head(count)
    return [{**analysis.person_identity(store.users, person_id), "spent_usd": round(float(row["spent"]), 2),
             "orders": int(row["orders"]), "books_bought": int(row["books"])}
            for person_id, row in ranking.iterrows()]


def _person_hits(users: pd.DataFrame, text: str) -> pd.Series:
    needle = str(text).strip().lower()
    hit = pd.Series(False, index=users.index)
    for column in PERSON_SEARCH_COLUMNS:
        hit |= users[column].str.lower().str.contains(needle, regex=False, na=False)
    digits = NON_DIGITS_RE.sub("", needle)
    if digits:
        hit |= users["phone_norm"].str.contains(digits, regex=False, na=False) | (users["id"].astype(str) == digits)
    return hit


def find_person(store_name: str, text: str) -> list[dict]:
    store = store_data(store_name)
    users = store.users
    people = users.loc[_person_hits(users, text), "person_id"].unique()[:MAX_PEOPLE_MATCHES]
    spent = store.orders.groupby("person_id")["paid_price_dec"].sum()
    return [{"person_id": int(person_id), "total_spent_usd": float(spent.get(person_id, 0)),
             "records": jsonable(users.loc[users["person_id"] == person_id, PERSON_COLUMNS])}
            for person_id in people]


def author_stats(store_name: str, author: str) -> dict:
    store = store_data(store_name)
    needle = author.strip().lower()

    def matches(name: str) -> bool:
        return needle in name.lower()

    books = store.books[store.books["authors"].map(lambda authors: any(map(matches, authors)))]
    orders = store.orders[store.orders["book_id"].isin(books["id"])]
    sold_by_set = orders.groupby("author_set")["quantity"].sum().sort_values(ascending=False)
    return {"matched_authors": sorted({name for authors in books["authors"] for name in authors if matches(name)}),
            "books": int(len(books)), "titles": sorted(books["title"].unique().tolist())[:MAX_AUTHOR_TITLES],
            "books_sold": _count(orders["quantity"]), "revenue_usd": _money(orders["paid_price"]),
            "sold_by_author_set": [{"author_set": author_set, "books_sold": int(sold)}
                                   for author_set, sold in sold_by_set.items()]}


def author_set_ranking(store_name: str, count: int = 10) -> list[dict]:
    orders = store_data(store_name).orders
    ranking = orders.groupby("author_set").agg(books_sold=("quantity", "sum"), revenue=("paid_price", "sum"))
    ranking = ranking.sort_values(["books_sold", "revenue"], ascending=False).head(count)
    return [{"author_set": author_set, "books_sold": int(row.books_sold), "revenue_usd": round(float(row.revenue), 2)}
            for author_set, row in ranking.iterrows()]


def distinct_values(store_name: str, table_name: str, field: str, limit: int = 1000) -> dict:
    frame = table(store_name, table_name)
    if field not in frame.columns:
        raise ValueError(f"Unknown field '{field}'")
    counts = frame[field].value_counts(dropna=True)
    return {"distinct": int(len(counts)),
            "values": [{"value": _round(value), "rows": int(rows)} for value, rows in counts.head(limit).items()]}


def _name_tokens(full_name: str) -> list[str]:
    return [token for token in WORD_RE.findall(full_name) if token.lower() not in NON_NAME_WORDS]


@lru_cache(maxsize=None)
def _known_names(store_name: str) -> frozenset:
    store = _load_store(store_name)
    people = list(store.users["name"].dropna()) + [author for authors in store.books["authors"] for author in authors]
    names = set()
    for full_name in people:
        tokens = _name_tokens(full_name)
        if len(tokens) >= 2:
            names.update({tokens[0].lower(), tokens[-1].lower()})
    return frozenset(names)


def _names_in_title(title: str, known_names: frozenset) -> list[str]:
    words = [word.lower().removesuffix("'s") for word in WORD_RE.findall(title)]
    return sorted({word for word in words if len(word) >= MIN_NAME_LENGTH and word in known_names} - NON_NAME_WORDS)


def titles_with_person_names(store_name: str) -> dict:
    name = normalize_store(store_name)
    known_names = _known_names(name)
    titles = []
    for title in sorted(table(name, "books")["title"].dropna().unique()):
        matched = _names_in_title(title, known_names)
        if matched:
            titles.append({"title": title, "matched_names": matched})
    return {"candidates": len(titles), "titles": titles}
