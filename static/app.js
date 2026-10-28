(() => {
  const PAGE_SIZE = 25;
  const SEARCH_DELAY_MS = 250;
  const SKELETON_DELAY_MS = 150;
  const SKELETON_ROWS = 8;
  const Y_LABEL_WIDTH = 56;
  const HISTORY_KEY = "chat-history";
  const HISTORY_LIMIT = 30;
  const DESKTOP_CHAT = "(min-width: 1200px)";
  const DESKTOP_NAV = "(min-width: 992px)";
  const DEFAULT_VIEW = { offset: 0, sort: null, dir: "asc", q: "", filter: null, label: "" };
  const NUMERIC = new Set(["id", "user_id", "person_id", "book_id", "year", "n_authors", "sold_qty", "orders", "revenue",
    "aliases", "spent", "quantity", "unit_price", "unit_price_usd", "paid_price", "month", "hour"]);
  const MONEY = new Set(["revenue", "spent", "unit_price_usd", "paid_price"]);
  const HIDDEN = { orders: new Set(["year_month", "year", "month", "weekday", "hour", "author"]) };
  const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const KPI_FIELDS = { "kpi-revenue": "total_revenue", "kpi-users": "unique_users", "kpi-sets": "unique_author_sets", "kpi-orders": "orders" };

  const $ = (selector) => document.querySelector(selector);
  const usd = (value, digits = 2) => "$" + Number(value).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const num = (value) => Number(value).toLocaleString("en-US");
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => HTML_ESCAPES[char]);
  const utcTime = (date, time = "00:00:00") => new Date(`${date}T${time}Z`).getTime();
  const columnLabel = (column) => column.charAt(0).toUpperCase() + column.slice(1).replace(/_/g, " ");
  const alignClass = (column) => (NUMERIC.has(column) ? "text-end" : "");
  const getJson = (url) => fetch(url).then((response) => response.json());
  const cssVar = (name) => getComputedStyle(document.body).getPropertyValue(name).trim();
  const isDark = () => document.documentElement.getAttribute("data-bs-theme") === "dark";
  const initials = (name) => String(name).replace(/^(mr|mrs|ms|miss|dr|prof)\.?\s+/i, "").split(/\s+/)
    .filter(Boolean).slice(0, 2).map((part) => part[0].toUpperCase()).join("");
  const avatar = (name, size = "avatar-sm") => `<span class="avatar avatar-soft ${size}">${esc(initials(name))}</span>`;

  const storageGet = (store, key) => { try { return store.getItem(key); } catch { return null; } };
  const storageSet = (store, key, value) => { try { store.setItem(key, value); return true; } catch { return false; } };

  const readHistory = () => {
    try { return JSON.parse(storageGet(sessionStorage, HISTORY_KEY) || "[]"); } catch { return []; }
  };

  const state = {
    store: window.STORES[0],
    summary: null,
    summaries: {},
    chart: null,
    brush: null,
    spark: null,
    range: null,
    table: window.TABLES[0],
    columns: null,
    views: Object.fromEntries(window.TABLES.map((table) => [table, { ...DEFAULT_VIEW }])),
    history: readHistory(),
  };

  const view = (table) => state.views[table];
  const currentView = () => view(state.table);

  const saveHistory = () => storageSet(sessionStorage, HISTORY_KEY, JSON.stringify(state.history.slice(-HISTORY_LIMIT)));

  const highlightButton = (groupSelector, attribute, value) => {
    document.querySelectorAll(`${groupSelector} [data-${attribute}]`).forEach((button) => {
      const active = button.dataset[attribute] === value;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  };

  const setText = (texts) => Object.entries(texts).forEach(([selector, text]) => { $(selector).textContent = text; });

  const rankedRows = (items, cells) => items.map((item, index) =>
    `<tr><td class="text-secondary">${index + 1}</td>${cells(item)}</tr>`).join("");

  const showChartReset = (visible) => $("#chart-reset").classList.toggle("d-none", !visible);

  const loadSummary = (store) => {
    state.summaries[store] ??= getJson(`/api/summary/${store}`);
    return state.summaries[store];
  };

  const monthlyRevenue = (series) => {
    const months = new Map();
    series.forEach((day) => months.set(day.date.slice(0, 7), (months.get(day.date.slice(0, 7)) || 0) + day.revenue));
    return [...months.values()].map((value) => Math.round(value * 100) / 100);
  };

  const renderKpis = (summary) => setText({
    "#kpi-revenue": usd(summary.total_revenue),
    "#kpi-revenue-sub": `${usd(summary.total_revenue / Math.max(summary.daily_revenue.length, 1))} / selling day`,
    "#kpi-users": num(summary.unique_users),
    "#kpi-users-sub": `${num(summary.user_records)} user records`,
    "#kpi-sets": num(summary.unique_author_sets),
    "#kpi-sets-sub": `${num(summary.books)} books`,
    "#kpi-orders": num(summary.orders),
    "#kpi-orders-sub": `${summary.daily_revenue.length} selling days`,
  });

  const renderDeltas = async (store) => {
    const summaries = await Promise.all(window.STORES.map(loadSummary));
    if (store !== state.store) return;
    const current = summaries[window.STORES.indexOf(store)];
    Object.entries(KPI_FIELDS).forEach(([id, field]) => {
      const average = summaries.reduce((sum, summary) => sum + summary[field], 0) / summaries.length;
      const delta = average ? ((current[field] - average) / average) * 100 : 0;
      const node = $(`#${id}-delta`);
      node.className = `kpi-delta ${delta >= 0 ? "up" : "down"}`;
      node.textContent = `${delta >= 0 ? "+" : "−"}${Math.abs(delta).toFixed(1)}%`;
      node.title = `Compared with the average of all ${summaries.length} stores`;
      node.nextElementSibling.textContent = "vs avg";
    });
  };

  const renderSparkline = (series) => {
    state.spark?.destroy();
    state.spark = new ApexCharts($("#kpi-revenue-spark"), {
      chart: { type: "area", height: 32, sparkline: { enabled: true }, animations: { enabled: false } },
      series: [{ name: "Monthly revenue", data: monthlyRevenue(series) }],
      stroke: { width: 1.5, curve: "smooth" },
      fill: { type: "gradient", gradient: { opacityFrom: 0.16, opacityTo: 0 } },
      colors: [cssVar("--tblr-primary")],
      tooltip: { enabled: false },
    });
    state.spark.render();
  };

  const renderTopDays = (topDays) => {
    const best = topDays[0]?.revenue || 1;
    $("#top-days").innerHTML = topDays.map((day, index) => `
      <li class="rank-item">
        <span class="rank-badge">${index + 1}</span>
        <div class="flex-fill min-w-0">
          <div class="d-flex align-items-baseline gap-2"><span class="rank-date">${esc(day.date)}</span><span class="rank-value">${usd(day.revenue)}</span></div>
          <div class="rank-bar"><div style="width:${(day.revenue / best) * 100}%"></div></div>
        </div>
      </li>`).join("");
  };

  const renderCustomers = (summary, customers) => {
    const buyer = summary.best_buyer;
    setText({
      "#cust-unique": num(summary.unique_users),
      "#cust-records": `from ${num(summary.user_records)} records — ${num(summary.user_records - summary.unique_users)} alias records merged`,
      "#buyer-name": buyer.names.join(" / "),
      "#buyer-ids": `[${buyer.user_ids.join(", ")}]`,
      "#buyer-aliases": `${buyer.user_ids.length} ${buyer.user_ids.length === 1 ? "record" : "records merged"}`,
      "#buyer-emails": buyer.emails.join(", "),
      "#buyer-spent": usd(buyer.total_spent),
      "#buyer-avatar": initials(buyer.names[0] || "?"),
    });
    $("#top-customers").innerHTML = rankedRows(customers, (customer) => `
      <td><div class="d-flex align-items-center gap-2">${avatar(customer.names[0] || "?", "avatar-xs")}<span class="text-truncate">${esc(customer.names.join(" / "))}</span></div></td>
      <td><code>[${customer.user_ids.join(", ")}]</code></td>
      <td class="text-end">${customer.orders}</td><td class="text-end fw-medium">${usd(customer.spent_usd)}</td>`);
  };

  const renderAuthors = (summary, authors) => {
    const popular = summary.most_popular_author;
    const best = authors[0]?.books_sold || 1;
    setText({
      "#auth-sets": num(summary.unique_author_sets),
      "#auth-books": `across ${num(summary.books)} books`,
      "#auth-top-sold": num(popular.books_sold),
    });
    $("#auth-top").innerHTML = popular.authors.map((authorSet) => `
      <div class="author-set">${authorSet.map((name) => `
        <div class="author-row">${avatar(name)}<span class="text-truncate">${esc(name)}</span></div>`).join("")}
      </div>`).join("");
    $("#top-authors").innerHTML = rankedRows(authors, (author) => `
      <td>${esc(author.author_set)}</td>
      <td class="text-end fw-medium">${num(author.books_sold)}</td>
      <td class="d-none d-md-table-cell"><div class="bar-cell"><div style="width:${(author.books_sold / best) * 100}%"></div></div></td>
      <td class="text-end">${usd(author.revenue_usd)}</td>`);
  };

  const chartTheme = () => ({
    mode: isDark() ? "dark" : "light",
    accent: cssVar("--tblr-primary"),
    text: cssVar("--tblr-secondary"),
    grid: cssVar("--tblr-border-color"),
  });

  const fullRange = () => {
    const [firstDay, lastDay] = state.summary.date_range;
    return { min: utcTime(firstDay), max: utcTime(lastDay, "23:59:59") };
  };

  const isFullRange = ({ min, max }) => {
    const full = fullRange();
    return min <= full.min && max >= full.max;
  };

  const onBrush = (xaxis) => {
    state.range = isFullRange(xaxis) ? null : { min: xaxis.min, max: xaxis.max };
    showChartReset(Boolean(state.range));
  };

  const renderChart = (series) => {
    const data = series.map((day) => [utcTime(day.date), day.revenue]);
    const theme = chartTheme();
    const range = state.range || fullRange();
    const shared = {
      theme: { mode: theme.mode },
      colors: [theme.accent],
      dataLabels: { enabled: false },
      grid: { borderColor: theme.grid, strokeDashArray: 0 },
      xaxis: { type: "datetime", labels: { datetimeUTC: true, style: { colors: theme.text } }, axisBorder: { color: theme.grid }, axisTicks: { color: theme.grid } },
      series: [{ name: "Revenue", data }],
    };
    state.chart?.destroy();
    state.brush?.destroy();
    state.chart = new ApexCharts($("#revenue-chart"), {
      ...shared,
      chart: {
        id: "revenue-main", type: "area", height: parseInt(cssVar("--chart-height"), 10) || 300, fontFamily: "inherit",
        background: "transparent", foreColor: theme.text, animations: { enabled: false },
        toolbar: { show: true, autoSelected: "pan", tools: { download: true, selection: false, zoom: false, zoomin: false, zoomout: false, pan: false, reset: false } },
        zoom: { enabled: false },
      },
      stroke: { width: 1.5, curve: "straight" },
      fill: { type: "gradient", gradient: { shadeIntensity: 1, opacityFrom: 0.18, opacityTo: 0, stops: [0, 100] } },
      xaxis: { ...shared.xaxis, min: range.min, max: range.max },
      yaxis: { labels: { minWidth: Y_LABEL_WIDTH, maxWidth: Y_LABEL_WIDTH, formatter: (value) => usd(Math.round(value), 0), style: { colors: theme.text } } },
      tooltip: { theme: theme.mode, x: { format: "yyyy-MM-dd" }, y: { formatter: (value) => usd(value) } },
      markers: { size: 0, hover: { size: 4 } },
    });
    state.chart.render();
    state.brush = new ApexCharts($("#revenue-brush"), {
      ...shared,
      chart: {
        id: "revenue-brush", type: "area", height: parseInt(cssVar("--brush-height"), 10) || 84, fontFamily: "inherit",
        background: "transparent", foreColor: theme.text, animations: { enabled: false },
        brush: { target: "revenue-main", enabled: true, autoScaleYaxis: true },
        selection: { enabled: true, xaxis: range, fill: { color: theme.accent, opacity: 0.08 }, stroke: { color: theme.accent, width: 1 } },
        events: { selection: (_, { xaxis }) => onBrush(xaxis), brushScrolled: (_, { xaxis }) => onBrush(xaxis) },
      },
      stroke: { width: 1 },
      fill: { type: "solid", opacity: 0.12 },
      yaxis: { tickAmount: 1, labels: { minWidth: Y_LABEL_WIDTH, maxWidth: Y_LABEL_WIDTH, formatter: () => "" } },
      grid: { ...shared.grid, yaxis: { lines: { show: false } } },
      tooltip: { enabled: false },
    });
    state.brush.render();
    showChartReset(Boolean(state.range));
  };

  const renderCharts = () => {
    if (!state.summary) return;
    renderChart(state.summary.daily_revenue);
    renderSparkline(state.summary.daily_revenue);
  };

  const renderSummary = (summary, rankings) => {
    $("#date-range").innerHTML = `<i class="ti ti-calendar"></i>${esc(summary.date_range[0])} — ${esc(summary.date_range[1])}`;
    renderKpis(summary);
    renderTopDays(summary.top_days);
    renderCustomers(summary, rankings.customers);
    renderAuthors(summary, rankings.authors);
    state.range = null;
    renderCharts();
  };

  const openTab = (tab) => {
    const link = document.querySelector(`#main-tabs [data-tab="${tab}"]`);
    if (link) bootstrap.Tab.getOrCreateInstance(link).show();
  };

  const applyRange = (range) => {
    state.range = range && !isFullRange(range) ? range : null;
    const target = state.range || fullRange();
    state.brush.updateOptions({ chart: { selection: { xaxis: target } } }, false, false);
    state.chart.updateOptions({ xaxis: { min: target.min, max: target.max } }, false, false);
    showChartReset(Boolean(state.range));
  };

  const chartRange = (start, end) => {
    openTab("revenue");
    if (!start && !end) {
      applyRange(null);
      return;
    }
    const [firstDay, lastDay] = state.summary.date_range;
    applyRange({ min: utcTime(start || firstDay), max: utcTime(end || lastDay, "23:59:59") });
  };

  const tableParams = (tableView) => {
    const params = new URLSearchParams({ limit: PAGE_SIZE, offset: tableView.offset, dir: tableView.dir });
    if (tableView.sort) params.set("sort", tableView.sort);
    if (tableView.q) params.set("q", tableView.q);
    if (tableView.filter) params.set("filter", JSON.stringify(tableView.filter));
    return params;
  };

  const headerCell = (column, tableView) => {
    const sorted = tableView.sort === column;
    const arrow = sorted ? ` <i class="ti ti-arrow-${tableView.dir === "asc" ? "up" : "down"}"></i>` : "";
    return `<th class="sortable ${sorted ? "sorted" : ""} ${alignClass(column)}" data-col="${column}"
      aria-sort="${sorted ? (tableView.dir === "asc" ? "ascending" : "descending") : "none"}">${columnLabel(column)}${arrow}</th>`;
  };

  const bodyCell = (column, value) => {
    const text = value == null ? "" : MONEY.has(column) ? usd(value) : value;
    return `<td class="${alignClass(column)}" title="${esc(text)}">${esc(text)}</td>`;
  };

  const emptyState = (columns) => `<tr><td colspan="${columns.length}" class="empty-cell">
    <div class="empty">
      <div class="empty-icon"><i class="ti ti-database-search"></i></div>
      <p class="empty-title">No rows match</p>
      <p class="empty-subtitle text-secondary">Try a different search or remove the filter.</p>
      <div class="empty-action"><button class="btn btn-outline-primary btn-sm" type="button" data-action="reset-view"><i class="ti ti-filter-off me-1"></i>Clear search and filter</button></div>
    </div></td></tr>`;

  const renderRows = (rows, columns) => {
    if (!rows.length) return emptyState(columns);
    return rows.map((row) => `<tr>${columns.map((column) => bodyCell(column, row[column])).join("")}</tr>`).join("");
  };

  const renderSkeleton = () => {
    const count = state.columns?.length || 6;
    const cell = (index) => `<td><span class="placeholder col-${[8, 6, 10, 5, 7][index % 5]}"></span></td>`;
    $("#data-table tbody").innerHTML = Array.from({ length: SKELETON_ROWS },
      () => `<tr class="skeleton-row placeholder-glow">${Array.from({ length: count }, (_, index) => cell(index)).join("")}</tr>`).join("");
  };

  const renderTableFooter = (table, tableView, total) => {
    const end = Math.min(tableView.offset + PAGE_SIZE, total);
    $("#table-info").textContent = total ? `${num(tableView.offset + 1)}–${num(end)} of ${num(total)} ${table}` : `0 ${table}`;
    $("#prev-page").disabled = tableView.offset === 0;
    $("#next-page").disabled = end >= total;
    $("#active-filter").classList.toggle("d-none", !tableView.filter);
    $("#active-filter-text").textContent = tableView.filter ? `Filter: ${tableView.label || "custom"} · ${num(total)} rows` : "";
  };

  let tableRequest = 0;
  const loadTable = async () => {
    const request = ++tableRequest;
    const table = state.table;
    const tableView = view(table);
    const skeletonTimer = setTimeout(() => { if (request === tableRequest) renderSkeleton(); }, SKELETON_DELAY_MS);
    let result;
    try {
      result = await getJson(`/api/table/${state.store}/${table}?${tableParams(tableView)}`);
    } finally {
      clearTimeout(skeletonTimer);
    }
    if (request !== tableRequest) return;
    if (result.error) {
      $("#table-info").textContent = result.error;
      return;
    }
    const columns = result.columns.filter((column) => !HIDDEN[table]?.has(column));
    state.columns = columns;
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
    const [summary, rankings] = await Promise.all([loadSummary(store), getJson(`/api/rankings/${store}`)]);
    if (store !== state.store) return;
    state.summary = summary;
    renderSummary(summary, rankings);
    renderDeltas(store);
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

  const scrollChat = () => { $("#chat-log").scrollTop = $("#chat-log").scrollHeight; };

  const appendChatNode = (className, html) => {
    const node = document.createElement("div");
    node.className = className;
    node.innerHTML = html;
    $("#chat-log").appendChild(node);
    return node;
  };

  const addMessage = (role, text, tools) => {
    const message = appendChatNode(`chat-msg ${role}`, formatMessage(text));
    if (tools?.length) appendChatNode("chat-tools", `<i class="ti ti-tool"></i>${tools.map((tool) => esc(tool.tool)).join(", ")}`);
    scrollChat();
    return message;
  };

  const addTyping = () => {
    const node = appendChatNode("chat-msg bot typing", "<span></span><span></span><span></span>");
    node.setAttribute("role", "status");
    node.setAttribute("aria-label", "Assistant is typing");
    scrollChat();
    return node;
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

  const revealDashboard = () => {
    if (!matchMedia(DESKTOP_CHAT).matches) bootstrap.Offcanvas.getInstance($("#chat-panel"))?.hide();
  };

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
    if (data.actions?.length) revealDashboard();
    for (const action of data.actions || []) await applyAction(action);
  };

  const growInput = () => {
    const input = $("#chat-input");
    input.style.height = "auto";
    input.style.height = `${input.scrollHeight + 2}px`;
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
    growInput();
    setChatBusy(true);
    const pending = addTyping();
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

  const resetView = (event) => {
    if (!event.target.closest("[data-action='reset-view']")) return;
    $("#table-search").value = "";
    updateCurrentView({ q: "", filter: null, label: "", offset: 0 });
  };

  const toggleTheme = () => {
    const theme = isDark() ? "light" : "dark";
    document.documentElement.setAttribute("data-bs-theme", theme);
    storageSet(localStorage, "theme", theme);
    renderCharts();
  };

  const toggleSidebar = () => {
    const mini = document.documentElement.classList.toggle("sidebar-mini");
    storageSet(localStorage, "sidebar-mini", mini ? "1" : "0");
    $("#sidebar-toggle").setAttribute("aria-label", mini ? "Expand sidebar" : "Collapse sidebar");
    setTimeout(() => window.dispatchEvent(new Event("resize")), 220);
  };

  const closeMobileNav = () => {
    if (!matchMedia(DESKTOP_NAV).matches) bootstrap.Collapse.getInstance($("#sidebar-menu"))?.hide();
  };

  const copyBuyerIds = async () => {
    const label = $("#buyer-copy span");
    try {
      await navigator.clipboard.writeText($("#buyer-ids").textContent);
      label.textContent = "Copied";
    } catch {
      label.textContent = "Copy failed";
    }
    setTimeout(() => { label.textContent = "Copy"; }, 1500);
  };

  const bindGroup = (selector, attribute, handler) => document.querySelectorAll(`${selector} [data-${attribute}]`)
    .forEach((button) => button.addEventListener("click", () => handler(button.dataset[attribute])));

  bindGroup("#store-switch", "store", (store) => { closeMobileNav(); openStore(store); });
  bindGroup("#table-switch", "table", switchTable);
  $("#main-tabs").addEventListener("click", (event) => { if (event.target.closest("[data-tab]")) closeMobileNav(); });
  $("#main-tabs").addEventListener("shown.bs.tab", (event) => {
    if (event.target.dataset.tab === "revenue") window.dispatchEvent(new Event("resize"));
  });
  $("#data-table thead").addEventListener("click", sortByHeader);
  $("#data-table tbody").addEventListener("click", resetView);
  $("#table-search").addEventListener("input", searchTable);
  $("#prev-page").addEventListener("click", () => updateCurrentView({ offset: Math.max(0, currentView().offset - PAGE_SIZE) }));
  $("#next-page").addEventListener("click", () => updateCurrentView({ offset: currentView().offset + PAGE_SIZE }));
  $("#clear-filter").addEventListener("click", () => applyAction({ type: "clear_filter", table: state.table }));
  $("#chart-reset").addEventListener("click", () => chartRange(null, null));
  $("#buyer-copy").addEventListener("click", copyBuyerIds);
  $("#theme-toggle").addEventListener("click", toggleTheme);
  $("#sidebar-toggle").addEventListener("click", toggleSidebar);
  $("#chat-form").addEventListener("submit", (event) => { event.preventDefault(); send($("#chat-input").value); });
  $("#chat-input").addEventListener("input", growInput);
  $("#chat-input").addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    if (!$("#chat-send").disabled) $("#chat-form").requestSubmit();
  });
  $("#chat-examples").addEventListener("click", (event) => {
    const chip = event.target.closest(".chat-chip");
    if (chip) send(chip.textContent);
  });
  $("#chat-reset").addEventListener("click", resetChat);

  state.history.forEach((turn) => addMessage(turn.role === "user" ? "user" : "bot", turn.text));
  if (state.history.length) $("#chat-examples").classList.add("d-none");

  openStore(state.store);
})();
