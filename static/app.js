(() => {
  const PAGE_SIZE = 25;
  const SEARCH_DELAY_MS = 250;
  const HISTORY_KEY = "chat-history";
  const HISTORY_LIMIT = 30;
  const DEFAULT_VIEW = { offset: 0, sort: null, dir: "asc", q: "", filter: null, label: "" };
  const NUMERIC = new Set(["id", "user_id", "person_id", "book_id", "year", "n_authors", "sold_qty", "orders", "revenue",
    "aliases", "spent", "quantity", "unit_price", "unit_price_usd", "paid_price", "month", "hour"]);
  const MONEY = new Set(["revenue", "spent", "unit_price_usd", "paid_price"]);
  const HIDDEN = { orders: new Set(["year_month", "year", "month", "weekday", "hour", "author"]) };
  const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const CHART_OPTIONS = {
    chart: {
      type: "area", height: 320, fontFamily: "inherit", animations: { enabled: false },
      toolbar: { show: true, tools: { download: true, selection: false, zoom: true, zoomin: true, zoomout: true, pan: false, reset: true } },
      zoom: { type: "x", enabled: true },
    },
    dataLabels: { enabled: false },
    stroke: { width: 1.5, curve: "straight" },
    fill: { type: "gradient", gradient: { opacityFrom: 0.25, opacityTo: 0.02 } },
    colors: ["#206bc4"],
    xaxis: { type: "datetime", labels: { datetimeUTC: true } },
    grid: { strokeDashArray: 4 },
  };

  const $ = (selector) => document.querySelector(selector);
  const usd = (value, digits = 2) => "$" + Number(value).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const num = (value) => Number(value).toLocaleString("en-US");
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => HTML_ESCAPES[char]);
  const utcTime = (date, time = "00:00:00") => new Date(`${date}T${time}Z`).getTime();
  const alignClass = (column) => (NUMERIC.has(column) ? "text-end" : "");
  const getJson = (url) => fetch(url).then((response) => response.json());

  const readHistory = () => {
    try { return JSON.parse(sessionStorage.getItem(HISTORY_KEY) || "[]"); } catch { return []; }
  };

  const state = {
    store: window.STORES[0],
    summary: null,
    chart: null,
    table: window.TABLES[0],
    views: Object.fromEntries(window.TABLES.map((table) => [table, { ...DEFAULT_VIEW }])),
    history: readHistory(),
  };

  const view = (table) => state.views[table];
  const currentView = () => view(state.table);

  const saveHistory = () => {
    try {
      sessionStorage.setItem(HISTORY_KEY, JSON.stringify(state.history.slice(-HISTORY_LIMIT)));
      return true;
    } catch {
      return false;
    }
  };

  const highlightButton = (groupSelector, attribute, value) => {
    document.querySelectorAll(`${groupSelector} [data-${attribute}]`).forEach((button) => {
      const active = button.dataset[attribute] === value;
      button.classList.toggle("btn-primary", active);
      button.classList.toggle("btn-outline-secondary", !active);
    });
  };

  const setText = (texts) => Object.entries(texts).forEach(([selector, text]) => { $(selector).textContent = text; });

  const rankedRows = (items, cells) => items.map((item, index) =>
    `<tr><td class="text-secondary">${index + 1}</td>${cells(item)}</tr>`).join("");

  const showChartReset = (visible) => $("#chart-reset").classList.toggle("d-none", !visible);

  const renderKpis = (summary) => setText({
    "#kpi-revenue": usd(summary.total_revenue),
    "#kpi-users": num(summary.unique_users),
    "#kpi-users-sub": `${num(summary.user_records)} user records`,
    "#kpi-sets": num(summary.unique_author_sets),
    "#kpi-sets-sub": `${num(summary.books)} books`,
    "#kpi-orders": num(summary.orders),
    "#kpi-orders-sub": `${summary.daily_revenue.length} selling days`,
  });

  const renderTopDays = (topDays) => {
    const best = topDays[0]?.revenue || 1;
    $("#top-days").innerHTML = rankedRows(topDays, (day) => `
      <td class="fw-medium font-monospace">${day.date}</td><td class="text-end">${usd(day.revenue)}</td>
      <td class="d-none d-md-table-cell"><div class="bar-cell"><div style="width:${(day.revenue / best) * 100}%"></div></div></td>`);
  };

  const renderCustomers = (summary, customers) => {
    const buyer = summary.best_buyer;
    setText({
      "#cust-unique": num(summary.unique_users),
      "#cust-records": `from ${num(summary.user_records)} records — ${num(summary.user_records - summary.unique_users)} alias records merged`,
      "#buyer-name": buyer.names.join(" / "),
      "#buyer-ids": `[${buyer.user_ids.join(", ")}]`,
      "#buyer-emails": buyer.emails.join(", "),
      "#buyer-spent": `Total spent ${usd(buyer.total_spent)}`,
    });
    $("#top-customers").innerHTML = rankedRows(customers, (customer) => `
      <td>${esc(customer.names.join(" / "))}</td><td><code>[${customer.user_ids.join(", ")}]</code></td>
      <td class="text-end">${customer.orders}</td><td class="text-end">${usd(customer.spent_usd)}</td>`);
  };

  const renderAuthors = (summary, authors) => {
    const popular = summary.most_popular_author;
    setText({
      "#auth-sets": num(summary.unique_author_sets),
      "#auth-books": `across ${num(summary.books)} books`,
      "#auth-top-sold": `${num(popular.books_sold)} copies sold`,
    });
    $("#auth-top").innerHTML = popular.authors.map((authorSet) => esc(authorSet.join(", "))).join("<br>");
    $("#top-authors").innerHTML = rankedRows(authors, (author) => `
      <td>${esc(author.author_set)}</td>
      <td class="text-end">${num(author.books_sold)}</td><td class="text-end">${usd(author.revenue_usd)}</td>`);
  };

  const renderChart = (series) => {
    const data = series.map((day) => [utcTime(day.date), day.revenue]);
    state.chart?.destroy();
    state.chart = new ApexCharts($("#revenue-chart"), {
      ...CHART_OPTIONS,
      series: [{ name: "Revenue", data }],
      yaxis: { labels: { formatter: (value) => usd(Math.round(value), 0) } },
      tooltip: { x: { format: "yyyy-MM-dd" }, y: { formatter: (value) => usd(value) } },
    });
    state.chart.render();
    showChartReset(false);
  };

  const renderSummary = (summary, rankings) => {
    $("#date-range").innerHTML = `<i class="ti ti-calendar me-1"></i>${summary.date_range[0]} — ${summary.date_range[1]}`;
    renderKpis(summary);
    renderTopDays(summary.top_days);
    renderCustomers(summary, rankings.customers);
    renderAuthors(summary, rankings.authors);
    renderChart(summary.daily_revenue);
  };

  const openTab = (tab) => {
    const link = document.querySelector(`#main-tabs [data-tab="${tab}"]`);
    if (link) bootstrap.Tab.getOrCreateInstance(link).show();
  };

  const chartRange = (start, end) => {
    openTab("revenue");
    if (!start && !end) {
      state.chart.resetSeries();
      showChartReset(false);
      return;
    }
    const [firstDay, lastDay] = state.summary.date_range;
    state.chart.zoomX(utcTime(start || firstDay), utcTime(end || lastDay, "23:59:59"));
    showChartReset(true);
  };

  const tableParams = (tableView) => {
    const params = new URLSearchParams({ limit: PAGE_SIZE, offset: tableView.offset, dir: tableView.dir });
    if (tableView.sort) params.set("sort", tableView.sort);
    if (tableView.q) params.set("q", tableView.q);
    if (tableView.filter) params.set("filter", JSON.stringify(tableView.filter));
    return params;
  };

  const headerCell = (column, tableView) => {
    const arrow = tableView.sort === column ? (tableView.dir === "asc" ? " ↑" : " ↓") : "";
    return `<th class="sortable ${alignClass(column)}" data-col="${column}">${column.replace(/_/g, " ")}${arrow}</th>`;
  };

  const bodyCell = (column, value) => {
    const text = value == null ? "" : MONEY.has(column) ? usd(value) : value;
    return `<td class="${alignClass(column)}" title="${esc(text)}">${esc(text)}</td>`;
  };

  const renderRows = (rows, columns) => {
    if (!rows.length) return `<tr><td colspan="${columns.length}" class="text-center text-secondary py-4">No rows match</td></tr>`;
    return rows.map((row) => `<tr>${columns.map((column) => bodyCell(column, row[column])).join("")}</tr>`).join("");
  };

  const renderTableFooter = (table, tableView, total) => {
    const end = Math.min(tableView.offset + PAGE_SIZE, total);
    $("#table-info").textContent = total ? `${num(tableView.offset + 1)}–${num(end)} of ${num(total)} ${table}` : `0 ${table}`;
    $("#prev-page").disabled = tableView.offset === 0;
    $("#next-page").disabled = end >= total;
    $("#active-filter").classList.toggle("d-none", !tableView.filter);
    $("#active-filter-text").textContent = tableView.filter ? `AI filter: ${tableView.label || "custom"} — ${num(total)} rows` : "";
  };

  const loadTable = async () => {
    const table = state.table;
    const tableView = view(table);
    const result = await getJson(`/api/table/${state.store}/${table}?${tableParams(tableView)}`);
    if (result.error) {
      $("#table-info").textContent = result.error;
      return;
    }
    const columns = result.columns.filter((column) => !HIDDEN[table]?.has(column));
    $("#data-table thead").innerHTML = `<tr>${columns.map((column) => headerCell(column, tableView)).join("")}</tr>`;
    $("#data-table tbody").innerHTML = renderRows(result.rows, columns);
    renderTableFooter(table, tableView, result.total);
  };

  const updateCurrentView = (changes) => {
    Object.assign(currentView(), changes);
    loadTable();
  };

  const switchTable = (table) => {
    state.table = table;
    highlightButton("#table-switch", "table", table);
    $("#table-search").value = view(table).q;
    loadTable();
  };

  const openStore = async (store) => {
    state.store = store;
    highlightButton("#store-switch", "store", store);
    $("#store-title").textContent = store;
    const [summary, rankings] = await Promise.all([getJson(`/api/summary/${store}`), getJson(`/api/rankings/${store}`)]);
    state.summary = summary;
    renderSummary(summary, rankings);
    loadTable();
  };

  const showInExplorer = (table, changes) => {
    Object.assign(view(table), { ...changes, offset: 0 });
    openTab("explorer");
    switchTable(table);
  };

  const ACTIONS = {
    open_store: (action) => openStore(action.store),
    open_tab: (action) => openTab(action.tab),
    filter_table: async (action) => {
      if (action.store && action.store !== state.store) await openStore(action.store);
      showInExplorer(action.table, { filter: action.filter, label: action.description });
    },
    clear_filter: (action) => showInExplorer(action.table, { filter: null, label: "" }),
    sort_table: (action) => showInExplorer(action.table, { sort: action.field, dir: action.dir || "asc" }),
    chart_range: (action) => chartRange(action.start, action.end),
  };

  const applyAction = async (action) => ACTIONS[action.type]?.(action);

  const formatMessage = (text) => esc(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");

  const appendChatNode = (className, html) => {
    const node = document.createElement("div");
    node.className = className;
    node.innerHTML = html;
    $("#chat-log").appendChild(node);
    return node;
  };

  const addMessage = (role, text, tools) => {
    const message = appendChatNode(`chat-msg ${role}`, formatMessage(text));
    if (tools?.length) appendChatNode("chat-tools", `<i class="ti ti-tool me-1"></i>${tools.map((tool) => esc(tool.tool)).join(", ")}`);
    $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
    return message;
  };

  const remember = (role, text) => {
    state.history.push({ role, text });
    saveHistory();
  };

  const forgetLastQuestion = () => {
    state.history.pop();
    saveHistory();
  };

  const askAssistant = () => fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ history: state.history, store: state.store }),
  });

  const handleReply = async (response, pending) => {
    const data = await response.json();
    pending.remove();
    if (!response.ok) {
      addMessage("error", data.error || "Request failed");
      forgetLastQuestion();
      return;
    }
    addMessage("bot", data.reply, data.tool_calls);
    remember("assistant", data.reply);
    for (const action of data.actions || []) await applyAction(action);
  };

  const setChatBusy = (busy) => {
    $("#chat-send").disabled = busy;
    if (!busy) $("#chat-input").focus();
  };

  const send = async (rawText) => {
    const text = rawText.trim();
    if (!text) return;
    $("#chat-examples")?.classList.add("d-none");
    addMessage("user", text);
    remember("user", text);
    $("#chat-input").value = "";
    setChatBusy(true);
    const pending = addMessage("bot", "Thinking…");
    try {
      await handleReply(await askAssistant(), pending);
    } catch (error) {
      pending.remove();
      addMessage("error", "Network error: " + error.message);
      forgetLastQuestion();
    } finally {
      setChatBusy(false);
    }
  };

  const resetChat = () => {
    state.history = [];
    saveHistory();
    $("#chat-log").querySelectorAll(".chat-msg, .chat-tools").forEach((node) => node.remove());
    $("#chat-examples").classList.remove("d-none");
  };

  const sortByHeader = (event) => {
    const header = event.target.closest("th[data-col]");
    if (!header) return;
    const tableView = currentView();
    const column = header.dataset.col;
    const dir = tableView.sort === column && tableView.dir === "asc" ? "desc" : "asc";
    updateCurrentView({ sort: column, dir, offset: 0 });
  };

  let searchTimer;
  const searchTable = (event) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => updateCurrentView({ q: event.target.value, offset: 0 }), SEARCH_DELAY_MS);
  };

  const bindGroup = (selector, attribute, handler) => document.querySelectorAll(`${selector} [data-${attribute}]`)
    .forEach((button) => button.addEventListener("click", () => handler(button.dataset[attribute])));

  bindGroup("#store-switch", "store", openStore);
  bindGroup("#table-switch", "table", switchTable);
  $("#data-table thead").addEventListener("click", sortByHeader);
  $("#table-search").addEventListener("input", searchTable);
  $("#prev-page").addEventListener("click", () => updateCurrentView({ offset: Math.max(0, currentView().offset - PAGE_SIZE) }));
  $("#next-page").addEventListener("click", () => updateCurrentView({ offset: currentView().offset + PAGE_SIZE }));
  $("#clear-filter").addEventListener("click", () => applyAction({ type: "clear_filter", table: state.table }));
  $("#chart-reset").addEventListener("click", () => chartRange(null, null));
  $("#chat-form").addEventListener("submit", (event) => { event.preventDefault(); send($("#chat-input").value); });
  $("#chat-examples").addEventListener("click", (event) => { if (event.target.matches("button")) send(event.target.textContent); });
  $("#chat-reset").addEventListener("click", resetChat);

  state.history.forEach((turn) => addMessage(turn.role === "user" ? "user" : "bot", turn.text));
  if (state.history.length) $("#chat-examples").classList.add("d-none");

  openStore(state.store);
})();
