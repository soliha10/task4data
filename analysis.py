from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd
import yaml

from settings import DATA_DIR

EUR_TO_USD = Decimal("1.2")
CENT = Decimal("0.01")
DATE_FORMAT = "%Y-%m-%d"
STORE_FOLDER_PATTERN = "STORE*"
PHONE_DIGITS = 10
TOP_DAYS_COUNT = 5

NULL_TOKENS = {"": None, "NULL": None, "null": None, "NaN": None, "nan": None}
IDENTITY_FIELDS = ("name", "address", "phone", "email")
BOOK_TEXT_COLUMNS = ("title", "author", "genre", "publisher")

MONTHS = {month: number for number, month in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(am|pm)?")
PRICE_NUMBER_RE = re.compile(r"\d+(?:\.\d*)?")
DIGITS_RE = re.compile(r"\d+")


def _full_year(text: str) -> int:
    year = int(text)
    return year + 2000 if year < 100 else year


def _month_number(name: str) -> int:
    return MONTHS[name[:3]]


def _iso_date(match: re.Match) -> tuple[int, int, int]:
    return int(match[1]), int(match[2]), int(match[3])


def _us_date(match: re.Match) -> tuple[int, int, int]:
    return _full_year(match[3]), int(match[1]), int(match[2])


def _european_date(match: re.Match) -> tuple[int, int, int]:
    return int(match[3]), int(match[2]), int(match[1])


def _day_month_name_year(match: re.Match) -> tuple[int, int, int]:
    return int(match[3]), _month_number(match[2]), int(match[1])


def _month_name_day_year(match: re.Match) -> tuple[int, int, int]:
    return int(match[3]), _month_number(match[1]), int(match[2])


DATE_PATTERNS = [
    (re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"), _iso_date),
    (re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})"), _us_date),
    (re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})"), _european_date),
    (re.compile(r"(\d{1,2})-([a-z]+)-(\d{4})"), _day_month_name_year),
    (re.compile(r"([a-z]{3,})\s+(\d{1,2})\s+(\d{4})"), _month_name_day_year),
]


def _is_missing(raw) -> bool:
    return raw is None or (isinstance(raw, float) and pd.isna(raw))


def _to_24_hour(hour: int, meridiem: str | None) -> int:
    if meridiem == "pm" and hour < 12:
        return hour + 12
    if meridiem == "am" and hour == 12:
        return 0
    return hour


def _extract_time(text: str) -> tuple[str, tuple[int, int, int]]:
    match = TIME_RE.search(text)
    if not match:
        return text, (0, 0, 0)
    clock = (_to_24_hour(int(match[1]), match[4]), int(match[2]), int(match[3] or 0))
    return f"{text[:match.start()]} {text[match.end():]}", clock


def _extract_date(text: str) -> tuple[int, int, int] | None:
    for pattern, build in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            return build(match)
    return None


def parse_timestamp(raw) -> pd.Timestamp:
    if _is_missing(raw):
        return pd.NaT
    text = str(raw).strip().lower().replace("a.m.", "am").replace("p.m.", "pm")
    text, (hour, minute, second) = _extract_time(text)
    try:
        date_parts = _extract_date(text)
        if date_parts is None:
            return pd.NaT
        year, month, day = date_parts
        return pd.Timestamp(year=year, month=month, day=day, hour=hour, minute=minute, second=second)
    except (KeyError, ValueError):
        return pd.NaT


def _price_amount(text: str) -> Decimal | None:
    if "¢" in text:
        parts = DIGITS_RE.findall(text)
        return Decimal(f"{parts[0]}.{parts[1].zfill(2)}") if len(parts) == 2 else None
    match = PRICE_NUMBER_RE.search(text)
    return Decimal(match[0].rstrip(".") or "0") if match else None


def parse_price(raw) -> tuple[Decimal | None, str | None]:
    if _is_missing(raw):
        return None, None
    text = str(raw).strip()
    currency = "EUR" if ("€" in text or "EUR" in text.upper()) else "USD"
    return _price_amount(text), currency


def to_usd(amount: Decimal | None, currency: str | None) -> Decimal | None:
    if amount is None:
        return None
    return amount * EUR_TO_USD if currency == "EUR" else amount


def cents(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def split_authors(raw) -> tuple[str, ...]:
    if _is_missing(raw):
        return ()
    names = {re.sub(r"\s+", " ", author).strip() for author in str(raw).split(",")}
    return tuple(sorted(name for name in names if name))


class UnionFind:
    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, first, second):
        first_root, second_root = self.find(first), self.find(second)
        if first_root != second_root:
            self.parent[max(first_root, second_root)] = min(first_root, second_root)


def _identity_keys(users: pd.DataFrame) -> pd.DataFrame:
    keys = pd.DataFrame({field: users[f"{field}_norm"].to_numpy() for field in IDENTITY_FIELDS},
                        index=users["id"].to_numpy())
    for column in keys:
        missing = keys[column].isna() | (keys[column] == "")
        keys.loc[missing, column] = [f"__missing_{column}_{user_id}" for user_id in keys.index[missing]]
    return keys


def reconcile_users(users: pd.DataFrame) -> pd.Series:
    keys = _identity_keys(users)
    union_find = UnionFind(keys.index)
    for ignored_field in IDENTITY_FIELDS:
        matching_fields = [field for field in IDENTITY_FIELDS if field != ignored_field]
        for group in keys.groupby(matching_fields).groups.values():
            first, *others = list(group)
            for other in others:
                union_find.union(first, other)
    return pd.Series({user_id: union_find.find(user_id) for user_id in keys.index}, name="person_id")


@dataclass
class Store:
    name: str
    users: pd.DataFrame
    orders: pd.DataFrame
    books: pd.DataFrame
    quality: dict


def _with_integer_ids(frame: pd.DataFrame) -> pd.DataFrame:
    frame["id"] = pd.to_numeric(frame["id"], errors="coerce")
    frame = frame.dropna(subset=["id"]).drop_duplicates(subset=["id"])
    frame["id"] = frame["id"].astype("int64")
    return frame


def _add_user_normalizations(users: pd.DataFrame) -> None:
    users["name_norm"] = users["name"].str.lower().str.replace(r"\s+", " ", regex=True)
    users["address_norm"] = users["address"].str.lower().str.replace(r"[^a-z0-9]+", " ", regex=True).str.strip()
    users["phone_norm"] = users["phone"].str.replace(r"\D", "", regex=True).str[-PHONE_DIGITS:]
    users["email_norm"] = users["email"].str.lower()


def _load_users(folder: Path, quality: dict) -> pd.DataFrame:
    users = pd.read_csv(folder / "users.csv", dtype=str, keep_default_na=False)
    users = users.apply(lambda column: column.str.strip()).replace(NULL_TOKENS)
    quality["users_raw"] = len(users)
    users = users.drop_duplicates()
    quality["users_bad_id"] = int(pd.to_numeric(users["id"], errors="coerce").isna().sum())
    users = _with_integer_ids(users)
    _add_user_normalizations(users)
    users["person_id"] = users["id"].map(reconcile_users(users))
    quality["users_missing_address"] = int(users["address"].isna().sum())
    return users


def _load_books(folder: Path, quality: dict) -> pd.DataFrame:
    books = pd.DataFrame(yaml.safe_load((folder / "books.yaml").read_text()))
    books.columns = [str(column).lstrip(":") for column in books.columns]
    quality["books_raw"] = len(books)
    books = _with_integer_ids(books)
    books["year"] = pd.to_numeric(books["year"], errors="coerce").astype("Int64")
    for column in BOOK_TEXT_COLUMNS:
        books[column] = books[column].astype("string").str.strip()
    books["authors"] = books["author"].map(split_authors)
    books["author_set"] = books["authors"].map(", ".join)
    return books


def _parse_order_values(orders: pd.DataFrame) -> None:
    orders["timestamp"] = orders["timestamp"].map(parse_timestamp)
    prices = orders["unit_price"].map(parse_price)
    orders["currency"] = prices.map(lambda price: price[1])
    orders["unit_price_orig"] = prices.map(lambda price: price[0])
    orders["unit_price_usd"] = [to_usd(amount, currency) for amount, currency in prices]
    orders["quantity"] = pd.to_numeric(orders["quantity"], errors="coerce")


def _drop_invalid_orders(orders: pd.DataFrame, quality: dict) -> pd.DataFrame:
    bad_timestamp = orders["timestamp"].isna()
    bad_price = orders["unit_price_usd"].isna()
    bad_quantity = orders["quantity"].isna() | (orders["quantity"] <= 0)
    quality["orders_bad_timestamp"] = int(bad_timestamp.sum())
    quality["orders_bad_price"] = int(bad_price.sum())
    quality["orders_bad_quantity"] = int(bad_quantity.sum())
    return orders[~(bad_timestamp | bad_price | bad_quantity)].copy()


def _add_order_amounts(orders: pd.DataFrame) -> None:
    orders["quantity"] = orders["quantity"].astype("int64")
    orders["paid_price_dec"] = [cents(quantity * price)
                                for quantity, price in zip(orders["quantity"], orders["unit_price_usd"])]
    orders["paid_price"] = orders["paid_price_dec"].astype(float)
    orders["unit_price_usd"] = orders["unit_price_usd"].map(lambda price: float(cents(price)))
    orders["unit_price_orig"] = orders["unit_price_orig"].astype(float)
    orders["date"] = orders["timestamp"].dt.normalize()
    for part in ("year", "month", "day"):
        orders[part] = getattr(orders["timestamp"].dt, part)


def _id_lookup(frame: pd.DataFrame, key: str, value: str) -> pd.DataFrame:
    return frame[["id", value]].rename(columns={"id": key})


def _load_orders(folder: Path, users: pd.DataFrame, books: pd.DataFrame, quality: dict) -> pd.DataFrame:
    orders = pd.read_parquet(folder / "orders.parquet")
    quality["orders_raw"] = len(orders)
    orders = orders.drop_duplicates().drop_duplicates(subset=["id"])
    quality["orders_duplicates_removed"] = quality["orders_raw"] - len(orders)
    _parse_order_values(orders)
    orders = _drop_invalid_orders(orders, quality)
    _add_order_amounts(orders)
    orders = orders.merge(_id_lookup(users, "user_id", "person_id"), on="user_id", how="left")
    orders = orders.merge(_id_lookup(books, "book_id", "author_set"), on="book_id", how="left")
    quality["orders_unknown_user"] = int(orders["person_id"].isna().sum())
    quality["orders_unknown_book"] = int(orders["author_set"].isna().sum())
    quality["orders_clean"] = len(orders)
    return orders


def load_store(folder: Path) -> Store:
    folder = Path(folder)
    quality: dict = {}
    users = _load_users(folder, quality)
    books = _load_books(folder, quality)
    orders = _load_orders(folder, users, books, quality)
    return Store(folder.name, users, orders, books, quality)


def store_folders(data_dir: Path = DATA_DIR) -> list[Path]:
    return sorted(path for path in data_dir.glob(STORE_FOLDER_PATTERN) if path.is_dir())


def person_identity(users: pd.DataFrame, person_id) -> dict:
    records = users[users["person_id"] == person_id]
    return {
        "user_ids": sorted(int(user_id) for user_id in records["id"]),
        "names": sorted(set(records["name"].dropna())),
        "emails": sorted(set(records["email"].dropna())),
    }


def daily_revenue(store: Store) -> pd.DataFrame:
    revenue = store.orders.groupby("date")["paid_price_dec"].sum().map(float).rename("revenue").reset_index()
    revenue["date"] = revenue["date"].dt.strftime(DATE_FORMAT)
    return revenue


def top_days(store: Store, count: int = TOP_DAYS_COUNT) -> list[dict]:
    best = daily_revenue(store).sort_values(["revenue", "date"], ascending=[False, True]).head(count)
    return [{"date": day, "revenue": round(revenue, 2)} for day, revenue in zip(best["date"], best["revenue"])]


def unique_users(store: Store) -> int:
    return int(store.users["person_id"].nunique())


def unique_author_sets(store: Store) -> int:
    return int(store.books.loc[store.books["authors"].map(len) > 0, "author_set"].nunique())


def most_popular_author_set(store: Store) -> dict:
    sold = store.orders.groupby("author_set")["quantity"].sum().sort_values(ascending=False)
    leaders = sold[sold == sold.iloc[0]]
    return {"authors": [author_set.split(", ") for author_set in leaders.index], "books_sold": int(sold.iloc[0])}


def best_buyer(store: Store) -> dict:
    spending = store.orders.groupby("person_id")["paid_price_dec"].sum().sort_values(ascending=False)
    identity = person_identity(store.users, spending.index[0])
    return {
        "user_ids": identity["user_ids"],
        "total_spent": float(spending.iloc[0]),
        "names": identity["names"],
        "emails": identity["emails"],
    }


def summary(store: Store) -> dict:
    revenue = daily_revenue(store)
    return {
        "store": store.name,
        "top_days": top_days(store),
        "unique_users": unique_users(store),
        "user_records": int(len(store.users)),
        "unique_author_sets": unique_author_sets(store),
        "most_popular_author": most_popular_author_set(store),
        "best_buyer": best_buyer(store),
        "daily_revenue": revenue.to_dict(orient="records"),
        "total_revenue": float(sum(store.orders["paid_price_dec"])),
        "orders": int(len(store.orders)),
        "books": int(len(store.books)),
        "date_range": [revenue["date"].min(), revenue["date"].max()],
        "quality": store.quality,
    }


def without_series(store_summary: dict) -> dict:
    return {key: value for key, value in store_summary.items() if key != "daily_revenue"}


def main() -> None:
    for folder in store_folders():
        print(json.dumps(without_series(summary(load_store(folder))), indent=1, default=str), flush=True)


if __name__ == "__main__":
    main()
