"use strict";

const get = (id) => document.getElementById(id);
const telegram = window.Telegram?.WebApp;
const localHost = ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);
const telegramAuth = telegram?.initData ? "tma " + telegram.initData : "";
let authorization = telegramAuth;
let settings = {};
let chats = [];
let settingsSaving = false;
let connectionState = null;
let connectionLoading = false;
let connectionRevision = 0;
let approvalPending = false;
let accessLoading = false;
let chatsLoading = false;
let statsLoading = false;
const pendingChats = new Set();
const tabs = Array.from(document.querySelectorAll('[role="tab"]'));

telegram?.ready();
telegram?.expand();

function feedback(id, message = "", error = false) {
    const target = get(id);
    target.textContent = message;
    target.dataset.error = String(error);
    target.hidden = !message;
}

function showGate(title, description, { local = false, retry = false } = {}) {
    get("dashboard").hidden = true;
    get("gate").hidden = false;
    get("gateTitle").textContent = title;
    get("gateMessage").textContent = description;
    get("localForm").hidden = !local;
    get("retryAccess").hidden = !retry;
    get("accessLabel").textContent = "Вход";
    get("accessLabel").dataset.authenticated = "false";
}

function requireLogin(failed = false) {
    if (telegramAuth) {
        showGate("Нужен доступ администратора", "Откройте Mini App снова из меню бота в аккаунте администратора.");
    } else if (localHost) {
        const description = failed
            ? "Не удалось войти. Проверьте LOCAL_ADMIN_TOKEN в настройках сервера."
            : "Для доступа с этого компьютера введите токен LOCAL_ADMIN_TOKEN.";
        showGate("Локальный вход", description, { local: true });
    } else {
        showGate("Откройте панель в Telegram", "Используйте кнопку Mini App в чате с ботом.");
    }
}

async function request(path, payload) {
    const sentAuthorization = authorization;
    const headers = { Authorization: sentAuthorization };
    const options = { cache: "no-store", headers };
    if (payload !== undefined) {
        headers["Content-Type"] = "application/json";
        options.method = "POST";
        options.body = JSON.stringify(payload);
    }

    let response;
    try {
        response = await fetch(path, options);
    } catch {
        throw new Error("Нет связи с сервером. Попробуйте ещё раз.");
    }
    if (response.status === 401 || response.status === 403) {
        if (authorization === sentAuthorization) {
            authorization = "";
            clearSensitiveFields();
            requireLogin(true);
        }
        throw new Error("Доступ к панели истёк.");
    }

    let data;
    try {
        data = await response.json();
    } catch {
        throw new Error("Сервер вернул неожиданный ответ.");
    }
    if (!response.ok) {
        const detail = typeof data.detail === "string" ? data.detail : "";
        const fallback = response.status === 422
            ? "Проверьте заполненные поля и допустимые значения."
            : "Сервер не смог выполнить запрос.";
        throw new Error(detail || fallback);
    }
    return data;
}

function clearSensitiveFields() {
    for (const input of get("dashboard").querySelectorAll('input[type="password"]')) {
        input.value = "";
    }
    get("previewLog").querySelectorAll(".bubble").forEach((bubble) => bubble.remove());
    get("previewEmpty").hidden = false;
    get("previewMessage").value = "";
}

function renderKeyStatus() {
    const indicator = get("customKeyStatus");
    const present = settings.has_custom_api_key ?? null;
    indicator.dataset.present = String(present);
    indicator.textContent = present === null ? "Статус неизвестен" : present ? "Настроен" : "Не задан";
}

function renderPause() {
    const paused = settings.is_paused === "1";
    const labels = {
        no_bot: "Бот не запущен",
        needs_approval: "Нужно подтверждение",
        waiting_for_connection: "Ждём подключение",
        disconnected: "Отключён",
        missing_rights: "Нет прав",
        provider_missing: "Нужен провайдер",
        paused: "На паузе",
        ready: "Готов к ответам",
    };
    const status = connectionState?.reply_status;
    let tone = "unknown";
    if (status === "ready") tone = "active";
    else if (status === "no_bot" || status === "disconnected") tone = "error";
    else if (labels[status]) tone = "paused";
    if (connectionLoading) tone = "unknown";
    get("overviewTitle").closest(".overview").dataset.state = tone;
    get("replyStatus").textContent = connectionLoading
        ? "Проверяем"
        : labels[status] || "Статус неизвестен";
    get("replySummary").textContent = connectionLoading
        ? "Обновляем сведения о боте и аккаунте Telegram."
        : connectionState?.message || "Не удалось проверить подключение. Нажмите «Обновить» в карточке аккаунта.";
    const button = get("pauseButton");
    button.textContent = paused ? "Возобновить автоответы" : "Поставить на паузу";
    button.disabled = settingsSaving;
}

function permissionLabel(value, allowed, denied) {
    return value === true ? allowed : value === false ? denied : "Неизвестно";
}

function renderConnection() {
    const state = connectionState;
    const account = state?.account;
    const bot = state?.bot;
    get("refreshConnection").disabled = connectionLoading || approvalPending;
    get("connectionPanel").setAttribute("aria-busy", String(connectionLoading || approvalPending));
    get("accountName").textContent = account
        ? account.display_name || account.username || "Аккаунт Telegram"
        : state ? "Аккаунт пока не выбран" : "Подключение не проверено";
    get("accountUsername").textContent = account?.username ? "@" + account.username.replace(/^@/, "") : "";
    get("accountId").textContent = account?.user_id ? "ID " + account.user_id : "";
    get("personaName").textContent = "Профиль ответов — " + (state?.persona?.name || "Андрей");
    get("connectionAdmin").textContent = state?.admin?.user_id ? "ID " + state.admin.user_id : "Неизвестно";
    const botName = bot?.username ? "@" + bot.username.replace(/^@/, "") : "Бот";
    get("connectionBot").textContent = bot
        ? !bot.configured ? "Не настроен" : bot.running ? botName + " · запущен" : botName + " · остановлен"
        : "Неизвестно";
    get("accountEnabled").textContent = permissionLabel(account?.is_enabled, "Подключён", "Отключён");
    get("accountCanReply").textContent = permissionLabel(account?.can_reply, "Есть", "Нет");
    get("botChatAccess").textContent = permissionLabel(bot?.chat_access_enabled, "Включён", "Выключен");
    const storage = state?.storage?.backend || "";
    const storageNames = { firebase: "Firebase", sqlite: "SQLite (локально)" };
    get("connectionStorageRow").hidden = !storage;
    get("connectionStorage").textContent = storageNames[storage.toLowerCase()] || storage;

    const waiting = state?.reply_status === "waiting_for_connection";
    const disconnected = state?.reply_status === "disconnected";
    const missingRights = state?.reply_status === "missing_rights";
    const hint = get("connectionHint");
    hint.hidden = !waiting && !disconnected && !missingRights;
    hint.textContent = missingRights
        ? "В аккаунте Андрея откройте Настройки → Автоматизация чатов и разрешите боту отвечать в нужных чатах. Затем обновите статус."
        : "В аккаунте Андрея откройте Настройки → Автоматизация чатов и выберите этого бота. Если подключение уже включено, выключите и включите его снова, затем нажмите «Обновить».";

    const updated = account?.updated_at ? new Date(account.updated_at * 1000) : null;
    const updatedLabel = get("connectionUpdated");
    updatedLabel.hidden = !updated || !Number.isFinite(updated.getTime());
    updatedLabel.textContent = updatedLabel.hidden ? "" : "Последнее событие Telegram · " + updated.toLocaleString("ru");

    const approvals = state?.pending || [];
    get("pendingApprovals").hidden = !approvals.length;
    const list = get("approvalList");
    list.replaceChildren();
    for (const approval of approvals) {
        const row = document.createElement("li");
        row.className = "approval-row";
        const identity = document.createElement("div");
        const name = document.createElement("strong");
        name.textContent = approval.name || "Аккаунт Telegram";
        const id = document.createElement("small");
        id.textContent = "ID " + approval.user_id;
        identity.append(name, id);
        const button = document.createElement("button");
        button.type = "button";
        button.className = "button";
        button.textContent = "Подтвердить аккаунт";
        button.setAttribute("aria-label", "Подтвердить аккаунт «" + name.textContent + "», ID " + approval.user_id);
        button.disabled = connectionLoading || approvalPending || settingsSaving;
        button.addEventListener("click", () => approveConnection(approval));
        row.append(identity, button);
        list.append(row);
    }
    renderPause();
}

async function loadConnection() {
    if (approvalPending) return true;
    const revision = ++connectionRevision;
    connectionLoading = true;
    feedback("connectionFeedback", "Проверяем подключение…");
    renderConnection();
    try {
        const [state, currentSettings] = await Promise.all([
            request("/api/connection"),
            request("/api/settings"),
        ]);
        if (revision !== connectionRevision) return true;
        connectionState = state;
        settings = currentSettings.settings;
        renderKeyStatus();
        feedback("connectionFeedback");
        return true;
    } catch (error) {
        if (revision === connectionRevision) {
            connectionState = null;
            feedback("connectionFeedback", error.message, true);
        }
        return false;
    } finally {
        if (revision === connectionRevision) {
            connectionLoading = false;
            renderConnection();
        }
    }
}

async function approveConnection(approval) {
    if (approvalPending || connectionLoading || settingsSaving) return;
    approvalPending = true;
    const revision = ++connectionRevision;
    feedback("connectionFeedback", "Подтверждаем аккаунт…");
    renderConnection();
    try {
        const state = await request("/api/connection/approve", { request_id: approval.id });
        if (revision === connectionRevision) connectionState = state;
        feedback("connectionFeedback", "Аккаунт подтверждён. Проверьте статус автоответов.");
    } catch (error) {
        feedback("connectionFeedback", error.message, true);
    } finally {
        approvalPending = false;
        renderConnection();
    }
}

get("refreshConnection").addEventListener("click", loadConnection);

function updateTemperature() {
    get("temperatureValue").value = Number(get("temperature").value).toLocaleString("ru", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
}

function fillSettings(data) {
    settings = data.settings;
    get("temperature").value = settings.temperature;
    updateTemperature();
    get("customBaseUrl").value = settings.custom_base_url || "";
    get("customModelName").value = settings.custom_model_name || "";
    get("typing").checked = settings.simulate_typing === "1";
    get("split").checked = settings.split_messages === "1";
    get("minDelay").value = settings.min_delay;
    get("maxDelay").value = settings.max_delay;
    get("takeover").value = settings.human_takeover_minutes;
    get("customPrompt").value = settings.custom_prompt || "";
    renderKeyStatus();
    renderPause();
}

async function connect() {
    if (!authorization) {
        requireLogin();
        return;
    }
    if (accessLoading) return;
    accessLoading = true;
    showGate("Проверяем доступ", "Подключаемся к серверу…");
    try {
        fillSettings(await request("/api/settings"));
        get("gate").hidden = true;
        get("dashboard").hidden = false;
        get("accessLabel").textContent = telegramAuth ? "Администратор" : "Локальный вход";
        get("accessLabel").dataset.authenticated = "true";
        await loadConnection();
        const current = tabs.find((tab) => tab.getAttribute("aria-selected") === "true");
        selectTab(current);
    } catch (error) {
        if (authorization) {
            showGate("Не удалось открыть панель", error.message, { retry: true, local: localHost && !telegramAuth });
        }
    } finally {
        accessLoading = false;
    }
}

function selectTab(selected, focus = false) {
    for (const tab of tabs) {
        const active = tab === selected;
        tab.setAttribute("aria-selected", String(active));
        tab.tabIndex = active ? 0 : -1;
        get(tab.getAttribute("aria-controls")).hidden = !active;
    }
    if (focus) selected.focus();
    if (selected.id === "tab-chats") loadChats();
    if (selected.id === "tab-stats") loadStats();
}

for (const [index, tab] of tabs.entries()) {
    tab.addEventListener("click", () => selectTab(tab));
    tab.addEventListener("keydown", (event) => {
        const target = {
            ArrowLeft: (index + tabs.length - 1) % tabs.length,
            ArrowRight: (index + 1) % tabs.length,
            Home: 0,
            End: tabs.length - 1,
        }[event.key];
        if (target === undefined) return;
        event.preventDefault();
        selectTab(tabs[target], true);
    });
}
get("openChats").addEventListener("click", () => selectTab(get("tab-chats"), true));
get("temperature").addEventListener("input", updateTemperature);
get("retryAccess").addEventListener("click", connect);
get("localForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = get("localToken");
    if (!input.value.trim() || accessLoading) return;
    authorization = "Bearer " + input.value.trim();
    input.value = "";
    connect();
});

async function save(payload, button, feedbackId, onSaved) {
    if (settingsSaving || approvalPending) {
        feedback(feedbackId, "Дождитесь завершения предыдущего действия.", true);
        return;
    }
    settingsSaving = true;
    const pauseButton = get("pauseButton");
    pauseButton.disabled = true;
    button.disabled = true;
    feedback(feedbackId, "Сохраняем…");
    renderConnection();
    try {
        await request("/api/settings", payload);
        for (const [key, value] of Object.entries(payload)) {
            if (key.endsWith("_api_key")) {
                settings["has_" + key] = Boolean(value);
            } else {
                settings[key] = value;
            }
        }
        const confirmation = await onSaved?.();
        const connectionUpdated = await loadConnection();
        const statusMessage = connectionUpdated ? "" : " Статус подключения обновить не удалось.";
        feedback(feedbackId, (confirmation || "Сохранено.") + statusMessage);
    } catch (error) {
        feedback(feedbackId, error.message, true);
    } finally {
        settingsSaving = false;
        button.disabled = false;
        renderConnection();
    }
}

get("pauseButton").addEventListener("click", async (event) => {
    const pause = settings.is_paused === "1" ? "0" : "1";
    await save({ is_paused: pause }, event.currentTarget, "pauseFeedback");
});
get("customForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const payload = {
        custom_base_url: get("customBaseUrl").value.trim(),
        custom_model_name: get("customModelName").value.trim(),
        temperature: get("temperature").value,
    };
    const key = get("customApiKey");
    const keyValue = key.value.trim();
    if (keyValue.includes("****")) {
        feedback("customFeedback", "Введите полный ключ без маскировки или оставьте поле пустым.", true);
        return;
    }
    if (keyValue) payload.custom_api_key = keyValue;
    save(payload, event.submitter || event.currentTarget.querySelector('button[type="submit"]'), "customFeedback", () => {
        if (payload.custom_api_key === key.value.trim()) key.value = "";
        settings.has_custom_api_key = null;
        renderKeyStatus();
    });
});
get("behaviorForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const min = get("minDelay").value;
    const max = get("maxDelay").value;
    if (Number(min) > Number(max)) {
        feedback("behaviorFeedback", "Максимальная задержка должна быть не меньше минимальной.", true);
        return;
    }
    save({
        min_delay: min,
        max_delay: max,
        human_takeover_minutes: get("takeover").value,
        simulate_typing: get("typing").checked ? "1" : "0",
        split_messages: get("split").checked ? "1" : "0",
    }, event.submitter || event.currentTarget.querySelector('button[type="submit"]'), "behaviorFeedback");
});
get("promptForm").addEventListener("submit", (event) => {
    event.preventDefault();
    save({ custom_prompt: get("customPrompt").value }, event.submitter || event.currentTarget.querySelector('button[type="submit"]'), "promptFeedback");
});

function renderChats() {
    const list = get("chatList");
    list.replaceChildren();
    const filter = get("chatSearch").value.trim().toLocaleLowerCase("ru").replace(/^@/, "");
    const visibleChats = chats.filter((chat) =>
        [chat.chat_title, chat.username, chat.chat_id].some((value) =>
            String(value ?? "").toLocaleLowerCase("ru").includes(filter)
        )
    );
    if (!visibleChats.length) {
        const item = document.createElement("li");
        item.className = "muted";
        item.textContent = chats.length
            ? "Совпадений нет."
            : "Пока нет чатов. Они появятся после сообщений в подключённых диалогах Telegram Business.";
        list.append(item);
        return;
    }

    for (const chat of visibleChats) {
        const item = document.createElement("li");
        item.className = "chat-row";
        const text = document.createElement("div");
        const name = document.createElement("strong");
        name.textContent = chat.chat_title || "Личный чат";
        const subtitle = document.createElement("small");
        subtitle.textContent = chat.username ? "@" + chat.username : "ID " + chat.chat_id;
        text.append(name, subtitle);
        if (chat.last_reply_error) {
            const error = document.createElement("small");
            error.className = "chat-error";
            error.textContent = "Последняя ошибка · " + chat.last_reply_error;
            text.append(error);
        }
        const control = document.createElement("input");
        control.className = "switch";
        control.type = "checkbox";
        control.setAttribute("role", "switch");
        control.setAttribute("aria-label", "Автоответы в чате «" + name.textContent + "»");
        control.checked = chat.is_enabled === true || Number(chat.is_enabled) === 1;
        control.disabled = pendingChats.has(chat.chat_id) || chatsLoading;
        control.addEventListener("change", () => toggleChat(chat, control));
        item.append(text, control);
        list.append(item);
    }
}

async function toggleChat(chat, control) {
    const id = chat.chat_id;
    pendingChats.add(id);
    control.disabled = true;
    get("refreshChats").disabled = true;
    feedback("chatFeedback", "Сохраняем…");
    try {
        const data = await request("/api/chats/toggle", { chat_id: id, is_enabled: control.checked });
        chat.is_enabled = data.is_enabled;
        feedback("chatFeedback", data.is_enabled ? "Автоответы в чате включены." : "Автоответы в чате выключены.");
    } catch (error) {
        feedback("chatFeedback", error.message, true);
    } finally {
        pendingChats.delete(id);
        control.checked = chat.is_enabled === true || Number(chat.is_enabled) === 1;
        control.disabled = false;
        get("refreshChats").disabled = Boolean(pendingChats.size);
        if (!control.isConnected) renderChats();
    }
}

async function loadChats() {
    if (chatsLoading || pendingChats.size) return;
    chatsLoading = true;
    get("refreshChats").disabled = true;
    for (const control of get("chatList").querySelectorAll("input")) control.disabled = true;
    feedback("chatFeedback", "Загружаем чаты…");
    try {
        const data = await request("/api/chats");
        chats = data.chats;
        feedback("chatFeedback");
    } catch (error) {
        feedback("chatFeedback", error.message, true);
    } finally {
        chatsLoading = false;
        get("refreshChats").disabled = false;
        renderChats();
    }
}
get("refreshChats").addEventListener("click", loadChats);
get("chatSearch").addEventListener("input", renderChats);

async function loadStats() {
    if (statsLoading) return;
    statsLoading = true;
    get("refreshStats").disabled = true;
    feedback("statsFeedback", "Загружаем статистику…");
    try {
        const { stats } = await request("/api/stats");
        const statsFields = {
            totalSent: stats.total_sent,
            totalReceived: stats.total_received,
            totalChats: stats.total_chats,
            activeChats: stats.active_chats,
        };
        for (const [id, count] of Object.entries(statsFields)) {
            get(id).textContent = count.toLocaleString("ru");
        }
        feedback("statsFeedback");
        await loadConnection();
    } catch (error) {
        feedback("statsFeedback", error.message, true);
    } finally {
        statsLoading = false;
        get("refreshStats").disabled = false;
    }
}
get("refreshStats").addEventListener("click", loadStats);

function addBubble(label, content, outgoing = false) {
    const log = get("previewLog");
    const bubble = document.createElement("div");
    bubble.className = outgoing ? "bubble bubble-user" : "bubble";
    const tag = document.createElement("span");
    tag.className = "bubble-label";
    tag.textContent = label;
    bubble.append(tag, document.createTextNode(content));
    get("previewEmpty").hidden = true;
    log.append(bubble);
    const bubbles = log.querySelectorAll(".bubble");
    if (bubbles.length > 30) bubbles[0].remove();
    log.scrollTop = log.scrollHeight;
}

get("previewForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.submitter || event.currentTarget.querySelector('button[type="submit"]');
    const input = get("previewMessage");
    const message = input.value.trim();
    if (!message || button.disabled) return;
    button.disabled = true;
    input.value = "";
    addBubble("Вы", message, true);
    feedback("previewFeedback", "Готовим ответ…");
    try {
        const data = await request("/api/test", { message });
        if (!data.chunks?.length) throw new Error("Модель вернула пустой ответ.");
        for (const chunk of data.chunks) addBubble("ИИ", chunk);
        feedback("previewFeedback");
    } catch (error) {
        if (authorization && !input.value) input.value = message;
        feedback("previewFeedback", error.message, true);
    } finally {
        button.disabled = false;
    }
});

connect();
