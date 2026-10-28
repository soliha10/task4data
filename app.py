import json
import os
from functools import wraps
from http import HTTPStatus

from flask import Flask, jsonify, render_template, request

import assistant
import store_service

DEFAULT_PORT = 5050
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 200
RANKING_SIZE = 10

app = Flask(__name__)


def handles(status: HTTPStatus, *errors: type[Exception]):
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            try:
                return view(*args, **kwargs)
            except errors as error:
                return jsonify(error=str(error)), status
        return wrapper
    return decorator


def _optional_arg(name: str) -> str | None:
    return request.args.get(name) or None


def _row_filter() -> dict | None:
    raw_filter = _optional_arg("filter")
    return json.loads(raw_filter) if raw_filter else None


def _page() -> tuple[int, int]:
    limit = store_service.clamp(int(request.args.get("limit", DEFAULT_PAGE_SIZE)), 1, MAX_PAGE_SIZE)
    offset = max(0, int(request.args.get("offset", 0)))
    return limit, offset


@app.get("/")
def index():
    return render_template("index.html", stores=store_service.STORES, tables=store_service.TABLES)


@app.get("/api/summary/<store>")
@handles(HTTPStatus.NOT_FOUND, ValueError)
def api_summary(store):
    return jsonify(store_service.summary(store))


@app.get("/api/rankings/<store>")
@handles(HTTPStatus.NOT_FOUND, ValueError)
def api_rankings(store):
    return jsonify(authors=store_service.author_set_ranking(store, RANKING_SIZE),
                   customers=store_service.top_customers(store, RANKING_SIZE))


@app.get("/api/table/<store>/<table>")
@handles(HTTPStatus.BAD_REQUEST, ValueError)
def api_table(store, table):
    row_filter = _row_filter()
    limit, offset = _page()
    result = store_service.query(store, table, row_filter, _optional_arg("sort"), request.args.get("dir", "asc"),
                                 limit, offset, _optional_arg("q"))
    return jsonify({**result, "columns": store_service.columns(table)})


@app.post("/api/chat")
@handles(HTTPStatus.BAD_REQUEST, ValueError)
@handles(HTTPStatus.BAD_GATEWAY, assistant.AssistantError)
def api_chat():
    payload = request.get_json(silent=True) or {}
    return jsonify(assistant.chat(payload.get("history") or [], payload.get("store")))


@app.get("/healthz")
def healthz():
    return "ok"


def warm_cache() -> None:
    for store_name in store_service.STORES:
        store_service.summary(store_name)


warm_cache()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", DEFAULT_PORT)))
