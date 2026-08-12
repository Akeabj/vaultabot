const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
}

// Sent to the backend on every authenticated call so it can verify who's
// really asking (see vaulta_common.validate_init_data on the server side).
const initData = tg ? tg.initData : "";

const screens = {
  menu: document.getElementById("screen-menu"),
  coins: document.getElementById("screen-coins"),
  amount: document.getElementById("screen-amount"),
  summary: document.getElementById("screen-summary"),
  prices: document.getElementById("screen-prices"),
  orders: document.getElementById("screen-orders"),
  settings: document.getElementById("screen-settings"),
};

function showScreen(name) {
  Object.values(screens).forEach(s => s.classList.remove("active"));
  screens[name].classList.add("active");
}

document.querySelectorAll("[data-back]").forEach(btn => {
  btn.addEventListener("click", () => showScreen(btn.dataset.back));
});

let state = { symbol: null, amount: null, quote: null, lastCoins: null, lastPrices: null, lastOrders: null };

// ==============================
// i18n
// ==============================

const TRANSLATIONS = {
  en: {
    brandSubtitle: "Buy crypto with Stars.",
    brandTagline: "Fast. Secure. Simple.",
    popularCoins: "Popular Coins",
    buyCryptoCta: "Buy Crypto with Stars",
    buyCrypto: "Buy Crypto",
    livePrices: "Live Prices",
    myOrders: "My Orders",
    settings: "Settings",
    back: "Back",
    chooseCoin: "Choose a Coin",
    continue: "Continue",
    orderSummary: "Order Summary",
    walletAddress: "Wallet Address",
    walletPlaceholder: "Paste your wallet address",
    walletPlaceholderFor: "Paste your {label} address",
    payWithStars: "Pay with Stars ⭐",
    loading: "Loading…",
    failedCoins: "Failed to load coins.",
    failedPrices: "Failed to load prices.",
    failedOrders: "Failed to load orders.",
    noOrders: "No orders yet.",
    amountError: "Enter a positive number, e.g. 0.5",
    walletError: "That doesn't look like a valid wallet address.",
    quoteFailed: "Quote failed. Try again.",
    orderFailed: "Order failed. Try again.",
    paymentNotCompleted: "Payment was not completed.",
    paymentSuccess: "Payment successful! Order: ",
    amount: "Amount",
    price: "Price",
    subtotal: "Subtotal",
    commission: "Commission",
    buffer: "Buffer",
    total: "Total",
    stars: "Stars",
    statusPending: "Pending",
    statusPaid: "Paid",
    statusCompleted: "Completed",
    statusCancelled: "Cancelled",
    language: "Language",
    appearance: "Appearance",
    english: "English",
    russian: "Russian",
    light: "Light",
    dark: "Dark",
  },
  ru: {
    brandSubtitle: "Покупайте крипту за Stars.",
    brandTagline: "Быстро. Надёжно. Просто.",
    popularCoins: "Популярные монеты",
    buyCryptoCta: "Купить крипту за Stars",
    buyCrypto: "Купить крипту",
    livePrices: "Курсы валют",
    myOrders: "Мои заказы",
    settings: "Настройки",
    back: "Назад",
    chooseCoin: "Выберите монету",
    continue: "Продолжить",
    orderSummary: "Детали заказа",
    walletAddress: "Адрес кошелька",
    walletPlaceholder: "Вставьте адрес кошелька",
    walletPlaceholderFor: "Вставьте адрес {label}",
    payWithStars: "Оплатить Stars ⭐",
    loading: "Загрузка…",
    failedCoins: "Не удалось загрузить монеты.",
    failedPrices: "Не удалось загрузить курсы.",
    failedOrders: "Не удалось загрузить заказы.",
    noOrders: "Заказов пока нет.",
    amountError: "Введите положительное число, например 0.5",
    walletError: "Это не похоже на действительный адрес кошелька.",
    quoteFailed: "Не удалось получить курс. Попробуйте снова.",
    orderFailed: "Не удалось создать заказ. Попробуйте снова.",
    paymentNotCompleted: "Платёж не был завершён.",
    paymentSuccess: "Оплата прошла успешно! Заказ: ",
    amount: "Количество",
    price: "Цена",
    subtotal: "Подытог",
    commission: "Комиссия",
    buffer: "Буфер",
    total: "Итого",
    stars: "Stars",
    statusPending: "Ожидание",
    statusPaid: "Оплачен",
    statusCompleted: "Выполнен",
    statusCancelled: "Отменён",
    language: "Язык",
    appearance: "Оформление",
    english: "Английский",
    russian: "Русский",
    light: "Светлая",
    dark: "Тёмная",
  },
};

const STATUS_KEYS = {
  pending: "statusPending",
  paid: "statusPaid",
  completed: "statusCompleted",
  cancelled: "statusCancelled",
};

let currentLang = localStorage.getItem("vaulta_lang") || "en";
let currentTheme = document.documentElement.getAttribute("data-theme") || "dark";

function t(key) {
  return (TRANSLATIONS[currentLang] && TRANSLATIONS[currentLang][key]) || TRANSLATIONS.en[key] || key;
}

function applyTranslations() {
  document.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  document.documentElement.lang = currentLang;
  highlightHomeSubtitle();
}

// "Stars" is Telegram's own currency name and stays untranslated in both
// languages (see TRANSLATIONS above), so it's safe to highlight it in gold
// wherever it appears in the home subtitle, regardless of active language.
function highlightHomeSubtitle() {
  const el = document.querySelector(".home-subtitle");
  if (!el) return;
  el.innerHTML = t("brandSubtitle").replace(/Stars\.?/, '<span class="accent-word">$&</span>');
}

function updateSegmentedUI() {
  document.querySelectorAll("#lang-segmented .segment").forEach(b => {
    b.classList.toggle("active", b.dataset.value === currentLang);
  });
  document.querySelectorAll("#theme-segmented .segment").forEach(b => {
    b.classList.toggle("active", b.dataset.value === currentTheme);
  });
}

function setLang(lang) {
  currentLang = lang;
  localStorage.setItem("vaulta_lang", lang);
  applyTranslations();
  updateSegmentedUI();
  // Re-render any screens that already have data loaded, so open screens
  // (or ones the user goes back to) reflect the new language immediately.
  if (state.lastCoins) renderCoinList();
  if (state.lastPrices) renderPrices();
  if (state.lastOrders) renderOrders();
  if (state.quote) renderSummary();
}

function setTheme(theme) {
  currentTheme = theme;
  localStorage.setItem("vaulta_theme", theme);
  document.documentElement.setAttribute("data-theme", theme);
  document.getElementById("color-scheme-meta").setAttribute("content", theme);
  updateSegmentedUI();
}

document.querySelectorAll("#lang-segmented .segment").forEach(b => {
  b.addEventListener("click", () => setLang(b.dataset.value));
});
document.querySelectorAll("#theme-segmented .segment").forEach(b => {
  b.addEventListener("click", () => setTheme(b.dataset.value));
});

// ==============================
// Menu / Home
// ==============================

document.getElementById("btn-buy").addEventListener("click", loadCoins);
document.getElementById("btn-prices").addEventListener("click", loadPrices);
document.getElementById("btn-orders").addEventListener("click", loadOrders);
document.getElementById("btn-settings").addEventListener("click", () => showScreen("settings"));

function money(n) {
  return "$" + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// iOS's decimal keypad shows "," instead of "." in a lot of regions, and
// some layouts land a ":" on that same key — normalize both to "." as the
// user types, since the input is type="text" (a type="number" input just
// rejects "," outright and silently drops it).
function sanitizeDecimalInput(raw) {
  let s = raw.replace(/[,:]/g, ".");
  s = s.replace(/[^0-9.]/g, "");
  const firstDot = s.indexOf(".");
  if (firstDot !== -1) {
    s = s.slice(0, firstDot + 1) + s.slice(firstDot + 1).replace(/\./g, "");
  }
  return s;
}

const amountInputEl = document.getElementById("amount-input");
amountInputEl.addEventListener("input", (e) => {
  const sanitized = sanitizeDecimalInput(e.target.value);
  if (sanitized !== e.target.value) e.target.value = sanitized;
});

// ---- Popular Coins (home screen cards) ----

async function loadPopularCoins() {
  try {
    const res = await fetch("/api/popular");
    if (res.ok) {
      const data = await res.json();
      if (data.coins && data.coins.length) {
        renderPopularCoins(data.coins);
        return;
      }
    }
    throw new Error(`/api/popular unavailable (status ${res.status})`);
  } catch (e) {
    // /api/popular is a newer endpoint — if it 404s (backend not yet
    // redeployed with it), errors, or comes back empty, fall back to the
    // coins/prices endpoints that have always existed, and apply the same
    // "prefer SOL/BTC/XRP" rule client-side. This isn't tied to purchase
    // history either way — it's just whatever's configured in coins.json.
    console.warn("Falling back for Popular Coins:", e.message);
    await loadPopularCoinsFallback();
  }
}

async function loadPopularCoinsFallback() {
  const box = document.getElementById("popular-coins");
  try {
    const [coinsRes, pricesRes] = await Promise.all([fetch("/api/coins"), fetch("/api/prices")]);
    const coinsData = await coinsRes.json();
    const pricesData = await pricesRes.json();

    const available = (coinsData.coins || []).map(c => c.symbol);
    const preferred = ["SOL", "BTC", "XRP"];
    let symbols = preferred.filter(s => available.includes(s));
    for (const s of available) {
      if (symbols.length >= 3) break;
      if (!symbols.includes(s)) symbols.push(s);
    }
    symbols = symbols.slice(0, 3);

    const coins = symbols.map(s => ({
      symbol: s,
      price: pricesData[s] ?? null,
      change_24h: null, // not available without /api/popular
    }));
    renderPopularCoins(coins);
  } catch (e) {
    console.error("Popular Coins fallback also failed:", e);
    box.innerHTML = "";
  }
}

function renderPopularCoins(coins) {
  const box = document.getElementById("popular-coins");
  if (!coins.length) {
    box.innerHTML = "";
    return;
  }
  box.innerHTML = coins
    .map(c => {
      const hasChange = typeof c.change_24h === "number";
      const positive = hasChange && c.change_24h > 0;
      const negative = hasChange && c.change_24h < 0;
      const changeClass = positive ? "positive" : negative ? "negative" : "neutral";
      const changeText = hasChange ? `${positive ? "+" : ""}${c.change_24h.toFixed(2)}%` : "—";
      const logo = c.image
        ? `<img class="coin-card-logo" src="${c.image}" alt="${c.symbol}" data-fallback="${c.symbol.slice(0, 2)}">`
        : `<span class="coin-card-badge">${c.symbol.slice(0, 2)}</span>`;
      return `
        <div class="coin-card">
          ${logo}
          <span class="coin-card-symbol">${c.symbol}</span>
          <span class="coin-card-price">${c.price != null ? money(c.price) : "—"}</span>
          <span class="coin-card-change ${changeClass}"><span class="arrow"></span>${changeText}</span>
        </div>
      `;
    })
    .join("");

  // If a logo image 404s or fails to load, swap it for the monogram badge
  // instead of leaving a broken-image icon.
  box.querySelectorAll(".coin-card-logo").forEach(img => {
    img.addEventListener(
      "error",
      () => {
        const badge = document.createElement("span");
        badge.className = "coin-card-badge";
        badge.textContent = img.dataset.fallback;
        img.replaceWith(badge);
      },
      { once: true }
    );
  });
}

// ==============================
// Coins
// ==============================

async function loadCoins() {
  showScreen("coins");
  const list = document.getElementById("coin-list");
  list.innerHTML = `<div class="loading-text">${t("loading")}</div>`;
  try {
    const res = await fetch("/api/coins");
    const data = await res.json();
    state.lastCoins = data.coins;
    renderCoinList();
  } catch (e) {
    state.lastCoins = null;
    list.innerHTML = `<div class="empty-text">${t("failedCoins")}</div>`;
  }
}

function renderCoinList() {
  const list = document.getElementById("coin-list");
  list.innerHTML = "";
  state.lastCoins.forEach(c => {
    const el = document.createElement("button");
    el.className = "row row-tap";
    el.innerHTML = `
      <span class="coin-badge">${c.symbol.slice(0, 2)}</span>
      <span class="row-text">
        <span class="row-title">${c.symbol}</span>
        <span class="row-sub">${c.label}</span>
      </span>
      <span class="row-chevron">›</span>
    `;
    el.addEventListener("click", () => selectCoin(c.symbol));
    list.appendChild(el);
  });
}

function selectCoin(symbol) {
  state.symbol = symbol;
  document.getElementById("amount-coin-title").textContent = symbol;
  document.getElementById("amount-input").value = "";
  document.getElementById("amount-error").textContent = "";
  showScreen("amount");
  setTimeout(() => document.getElementById("amount-input").focus(), 250);
}

// ==============================
// Amount / Quote
// ==============================

document.getElementById("btn-quote").addEventListener("click", async () => {
  const errorEl = document.getElementById("amount-error");
  errorEl.textContent = "";
  const amount = parseFloat(sanitizeDecimalInput(document.getElementById("amount-input").value));
  if (!amount || amount <= 0) {
    errorEl.textContent = t("amountError");
    return;
  }
  state.amount = amount;
  try {
    const res = await fetch("/api/quote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: state.symbol, amount }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || t("quoteFailed"));
    state.quote = data;
    renderSummary();
    showScreen("summary");
  } catch (e) {
    errorEl.textContent = e.message;
  }
});

function renderSummary() {
  const q = state.quote;
  document.getElementById("summary-box").innerHTML = `
    <div class="detail-row"><span class="label">${t("amount")}</span><span class="value">${q.amount} ${q.coin}</span></div>
    <div class="detail-row"><span class="label">${t("price")}</span><span class="value">${money(q.price)}</span></div>
    <div class="detail-row"><span class="label">${t("subtotal")}</span><span class="value">${money(q.usd)}</span></div>
    <div class="detail-row"><span class="label">${t("commission")}</span><span class="value">${money(q.commission)}</span></div>
    <div class="detail-row"><span class="label">${t("buffer")}</span><span class="value">${money(q.buffer)}</span></div>
    <div class="detail-row total"><span class="label">${t("total")}</span><span class="value">${money(q.usd_total)}</span></div>
    <div class="detail-row stars"><span class="label">${t("stars")}</span><span class="value">${q.stars.toLocaleString()} ⭐</span></div>
  `;
  document.getElementById("wallet-input").placeholder = t("walletPlaceholderFor").replace("{label}", q.wallet_label);
  document.getElementById("wallet-input").value = "";
  document.getElementById("summary-error").textContent = "";
}

document.getElementById("btn-pay").addEventListener("click", async () => {
  const errorEl = document.getElementById("summary-error");
  errorEl.textContent = "";
  const wallet = document.getElementById("wallet-input").value.trim();
  if (wallet.length < 20 || wallet.includes(" ")) {
    errorEl.textContent = t("walletError");
    return;
  }
  try {
    const res = await fetch("/api/order", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData, symbol: state.symbol, amount: state.amount, wallet }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || t("orderFailed"));

    if (tg && tg.openInvoice) {
      tg.openInvoice(data.invoice_url, (status) => {
        if (status === "paid") {
          tg.showAlert(t("paymentSuccess") + data.order_id);
          showScreen("menu");
        } else if (status === "failed" || status === "cancelled") {
          errorEl.textContent = t("paymentNotCompleted");
        }
      });
    } else {
      // Fallback for testing in a regular browser, outside Telegram.
      window.open(data.invoice_url, "_blank");
    }
  } catch (e) {
    errorEl.textContent = e.message;
  }
});

// ==============================
// Prices
// ==============================

async function loadPrices() {
  showScreen("prices");
  const box = document.getElementById("prices-box");
  box.innerHTML = `<div class="loading-text">${t("loading")}</div>`;
  try {
    const res = await fetch("/api/prices");
    const data = await res.json();
    state.lastPrices = data;
    renderPrices();
  } catch (e) {
    state.lastPrices = null;
    box.innerHTML = `<div class="empty-text">${t("failedPrices")}</div>`;
  }
}

function renderPrices() {
  const box = document.getElementById("prices-box");
  box.innerHTML = Object.entries(state.lastPrices)
    .map(([symbol, price]) => `
      <div class="row">
        <span class="coin-badge">${symbol.slice(0, 2)}</span>
        <span class="row-title">${symbol}</span>
        <span class="row-value">${price !== null ? money(price) : "—"}</span>
      </div>
    `)
    .join("");
}

// ==============================
// Orders
// ==============================

async function loadOrders() {
  showScreen("orders");
  const box = document.getElementById("orders-box");
  box.innerHTML = `<div class="loading-text">${t("loading")}</div>`;
  try {
    const res = await fetch(`/api/orders?initData=${encodeURIComponent(initData)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || t("failedOrders"));
    state.lastOrders = data.orders;
    renderOrders();
  } catch (e) {
    state.lastOrders = null;
    box.innerHTML = `<div class="empty-text">${t("failedOrders")}</div>`;
  }
}

function renderOrders() {
  const box = document.getElementById("orders-box");
  if (state.lastOrders.length === 0) {
    box.innerHTML = `<div class="empty-text">${t("noOrders")}</div>`;
    return;
  }
  box.innerHTML = state.lastOrders
    .map(o => `
      <div class="row">
        <span class="status-dot status-${o.status}"></span>
        <span class="row-text">
          <span class="row-title">${o.amount} ${o.coin}</span>
          <span class="row-sub">${o.order_id}</span>
        </span>
        <span class="row-value status-${o.status}">${t(STATUS_KEYS[o.status] || o.status)}</span>
      </div>
    `)
    .join("");
}

// ==============================
// Init
// ==============================

applyTranslations();
updateSegmentedUI();
loadPopularCoins();