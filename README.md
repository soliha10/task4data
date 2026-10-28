# Bookstore Analytics — Task #4 (Data Engineering)

BI dashboard over five book-store datasets (`data/STORE1..STORE5`: `users.csv`, `orders.parquet`, `books.yaml`)
with an AI assistant that answers questions and controls the dashboard.

## Part 1 — data processing (`analysis.py`)

| Step | How |
|---|---|
| Timestamps | ~200 spellings (`09:28,10-Dec-2025`, `2025-06-25;05:00:01 A.M.`, `Fri Apr 10 15:32:26 2026`, `04/23/26`, `28.11.2025`…) parsed by a regex parser: time part (12/24h, am/pm, `A.M.`) + date part. `MM/DD/YY` is US order (first part is never > 12), `DD.MM.YYYY` is European. 0 unparsed rows. |
| Prices | `$ 35`, `USD25.25`, `EUR 26.50`, `€33¢50`, `35$50¢`… → amount + currency, using `Decimal`. EUR × 1.2 → USD. |
| `paid_price` | `quantity * unit_price_usd`, rounded half-up to cents. |
| Date | `date`, `year`, `month`, `day` extracted from the timestamp. |
| Cleaning | trimmed strings, `NULL`/empty → missing, duplicates by row/id dropped, invalid quantities/prices removed (counts kept in `quality`). |
| Real users | Records are the same person if **at least 3 of 4** fields match (name, address, phone, e-mail — only one may change). Phones normalized to digits, e-mails lower-cased, addresses normalized. Pairs are merged transitively with union-find → `person_id`. Missing values never match. |
| Author sets | `author` split on commas, trimmed, sorted → set. Count of distinct non-empty sets. |
| Most popular author | Author set with the most copies sold (sum of `quantity`). |
| Best buyer | Person with the highest total `paid_price`; all their user ids (aliases) are listed. |
| Chart | ApexCharts in the dashboard; `report.py` also saves matplotlib PNGs to `reports/`. |

### Results

| Store | Top 5 days | Unique users | Author sets | Most popular author(s) | Best buyer |
|---|---|---|---|---|---|
| STORE1 | 2025-08-20, 2025-09-22, 2026-01-15, 2025-09-03, 2025-10-17 | 3221 | 324 | Darron Ernser, Miss Lorina Kertzmann | [48403, 48904, 50056] |
| STORE2 | 2025-11-16, 2025-12-12, 2025-12-10, 2025-09-19, 2025-11-19 | 3166 | 281 | Kenneth Hamill, Marcus McKenzie | [51014] |
| STORE3 | 2025-10-09, 2025-09-21, 2025-09-04, 2025-09-30, 2025-09-01 | 2955 | 307 | Loreta Metz | [52881, 53869, 54846] |
| STORE4 | 2025-11-24, 2025-11-17, 2025-11-28, 2025-11-13, 2025-09-21 | 2850 | 299 | Emil Rempel, Heriberto Kutch, Merle Rice | [53347, 54809] |
| STORE5 | 2025-10-20, 2025-11-28, 2025-09-27, 2025-10-17, 2025-12-05 | 2987 | 311 | Dulcie Bode | [52895, 53969] |

## Dashboard

Flask + [Tabler](https://tabler.io) (Bootstrap 5 admin template) + ApexCharts.
Store switcher, KPI strip and tabs: **Revenue** (top 5 days `YYYY-MM-dd`, daily revenue chart),
**Customers** (unique users, best buyer ids), **Authors** (unique author sets, most popular authors),
**Data explorer** (books/users/orders with search, sorting, paging and AI filters).

## Parts 2 & 3 — AI assistant (`assistant.py`)

The chat panel sends the conversation to `/api/chat`. The server calls **Google Gemini** with function calling.
The model never receives a data dump and is instructed never to compute numbers itself; every figure comes
from deterministic pandas tools in `store_service.py`:

- `get_store_summary`, `compare_stores`, `query_rows` (rows + exact total), `aggregate` (count/sum/avg/min/max/median/nunique with group-by and filters),
  `revenue` (date range, day/month/year series), `top_customers`, `find_person`, `author_stats`, `author_set_ranking`, `distinct_values`,
  `titles_with_person_names` (name-corpus candidates that the model reviews).
- Dashboard control: `ui_open_store`, `ui_open_tab`, `ui_filter_table`, `ui_clear_filter`, `ui_sort_table`, `ui_chart_range`.
  The server validates the filter and returns the exact number of matching rows; the browser applies the action.

A filter is structured JSON (`{match, conditions:[{field, op, value|values, negate}]}`), so for example
"select users with e-mails started with s or p" becomes `{field: "email", op: "starts_with", values: ["s","p"]}`.

Example prompts: *How many unique users are in STORE3?* · *Revenue in December 2025?* · *Which genre sold the most copies?* ·
*Who is the best buyer and what are their ids?* · *Filter out books that have people names in the title* ·
*Show orders paid in EUR over $100* · *Zoom the chart to November 2025* · *Compare all stores by revenue*.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export GEMINI_API_KEY=...        # https://aistudio.google.com/apikey
.venv/bin/python app.py          # http://localhost:5050
.venv/bin/python report.py       # prints answers, saves reports/*.png
```

## Deploy (Render)

New → Blueprint → this repository (`render.yaml`), then set `GEMINI_API_KEY` in the service environment.
