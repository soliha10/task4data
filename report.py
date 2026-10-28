import json

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

import store_service
from settings import REPORTS_DIR

CHART_SIZE_INCHES = (11, 4)
CHART_DPI = 120
RESULTS_FILE = "results.json"


def store_answers(summary: dict) -> dict:
    return {
        "top_5_days": [day["date"] for day in summary["top_days"]],
        "unique_users": summary["unique_users"],
        "unique_author_sets": summary["unique_author_sets"],
        "most_popular_author": summary["most_popular_author"]["authors"],
        "best_buyer": summary["best_buyer"]["user_ids"],
    }


def save_revenue_chart(store_name: str, summary: dict) -> None:
    revenue = pd.DataFrame(summary["daily_revenue"])
    revenue["date"] = pd.to_datetime(revenue["date"])
    figure, axes = plt.subplots(figsize=CHART_SIZE_INCHES)
    axes.plot(revenue["date"], revenue["revenue"], linewidth=1)
    axes.set_title(f"{store_name} — daily revenue, USD")
    axes.set_ylabel("USD")
    axes.grid(alpha=0.3)
    figure.tight_layout()
    figure.savefig(REPORTS_DIR / f"{store_name}_daily_revenue.png", dpi=CHART_DPI)
    plt.close(figure)


def main() -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    results = {}
    for store_name in store_service.STORES:
        summary = store_service.summary(store_name)
        results[store_name] = store_answers(summary)
        save_revenue_chart(store_name, summary)
    report = json.dumps(results, indent=2)
    (REPORTS_DIR / RESULTS_FILE).write_text(report)
    print(report)


if __name__ == "__main__":
    main()
