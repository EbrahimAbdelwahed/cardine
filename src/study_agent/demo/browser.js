/* global crypto */
(function () {
  "use strict";

  const SCHEMA_VERSION = 1;
  const MAX_ENTRY_CHARS = 4000;
  const ROUTES = Object.freeze({
    oggi: { label: "Oggi", heading: "Oggi", endpoint: "/api/v1/bootstrap" },
    sessione: { label: "Sessione", heading: "Sessione", endpoint: "/api/v1/session" },
    fonti: { label: "Fonti", heading: "Fonti", endpoint: "/api/v1/materials" },
    proposte: { label: "Proposte", heading: "Proposte", endpoint: "/api/v1/artifacts" },
    verifiche: { label: "Verifiche", heading: "Verifiche", endpoint: "/api/v1/assessments" },
    evidenze: { label: "Evidenze", heading: "Evidenze", endpoint: "/api/v1/evidence" },
    ripasso: { label: "Ripasso", heading: "Ripasso", endpoint: "/api/v1/recall/due" },
    piano: { label: "Piano", heading: "Piano", endpoint: "/api/v1/plan" },
    conflitti: { label: "Conflitti", heading: "Conflitti di contesto", endpoint: "/api/v1/context/conflicts" },
    impostazioni: { label: "Impostazioni", heading: "Impostazioni", endpoint: "/api/v1/settings", private: true },
    login: { label: "Accedi", heading: "Accedi a Cardine", endpoint: null, private: true },
  });

  const CONTINUATION_ROUTES = Object.freeze(new Set(["fonti", "proposte", "verifiche", "evidenze", "ripasso", "piano", "conflitti"]));
  const SETTINGS_ENDPOINTS = Object.freeze({
    read: "/api/v1/settings",
    modelCredential: "/api/v1/settings/model/credential",
    removeCredential: "/api/v1/settings/model/credential/remove",
  });
  const SAFE_ERROR_MESSAGES = Object.freeze({
    400: "La richiesta non è valida. Controlla i dati e riprova.",
    401: "La sessione è scaduta. Accedi di nuovo per continuare.",
    403: "Non hai i permessi per eseguire questa azione.",
    404: "La risorsa richiesta non è disponibile.",
    409: "Lo stato è cambiato. Aggiorna la sezione e riprova.",
    429: "Troppi tentativi. Attendi qualche secondo e riprova.",
    500: "Il servizio non è disponibile. Riprova tra poco.",
    503: "Il servizio non è disponibile. Riprova tra poco.",
    504: "Il provider ha impiegato troppo tempo. Riprova tra poco.",
  });
  const MODEL_ERROR_MESSAGES = Object.freeze({
    tutor_authentication: "La chiave API è stata rifiutata dal provider. Aggiornala in Impostazioni.",
    tutor_model_unavailable: "Il modello configurato non è disponibile per questa chiave.",
    tutor_endpoint_incompatible: "L'endpoint del provider non supporta questa configurazione del modello.",
    tutor_rate_limited: "Il provider ha limitato la richiesta. Attendi e riprova.",
    tutor_timeout: "Il provider non ha risposto entro il tempo previsto.",
    tutor_protocol_error: "Il provider ha restituito una risposta non compatibile.",
    tutor_unavailable: "Il provider del modello non è disponibile in questo momento.",
    tutor_internal_error: "La chat ha riscontrato un errore interno. Riprova; consulta Diagnostica se persiste.",
  });

  const STATUS_LABELS = Object.freeze({
    ready: "pronto",
    working: "in lavorazione",
    needs_learner_input: "attende una risposta",
    suspended: "sospesa",
    conflicted_context: "contesto in conflitto",
    needs_review: "richiede revisione",
    stale: "stato da aggiornare",
    degraded: "funzionalità ridotta",
    recovered: "pronta",
    error: "non disponibile",
  });

  const state = {
    bootstrap: null,
    route: "oggi",
    viewData: null,
    highWaterSequence: 0,
    selectedAnswers: Object.create(null),
    freeAnswers: Object.create(null),
    revealedReviews: Object.create(null),
    lastCommand: null,
    loading: false,
    pendingTurn: null,
    navigationVersion: 0,
    sidebarCollapsed: true,
    auth: { status: "unknown", authenticated: false, mode: "local_repository", csrfToken: "", account: null },
    continuation: null,
    continuationDraft: "",
    chatCourseCreation: null,
    studySetup: null,
    lastTurn: null,
    authProbeUnavailable: false,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const root = $("#view-root");
  const CardineAI = window.CardineAI || {};
  let destroyPrimitiveEnhancements = () => {};
  let destroyCommandSearch = () => {};
  let commandSearchReturnFocus = null;

  function text(value, fallback = "") {
    if (value === null || value === undefined || value === "") return fallback;
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
    return fallback;
  }

  function first(value, keys, fallback = "") {
    if (!value || typeof value !== "object") return fallback;
    for (const key of keys) {
      if (value[key] !== null && value[key] !== undefined && value[key] !== "") return value[key];
    }
    return fallback;
  }

  function array(value) {
    if (Array.isArray(value)) return value;
    if (value && typeof value === "object") {
      for (const key of ["items", "rows", "data", "timeline", "turns", "messages", "sources", "proposals", "presentations", "cards", "conflicts"]) {
        if (Array.isArray(value[key])) return value[key];
      }
    }
    return [];
  }

  function object(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function count(value, keys) {
    const result = first(value, keys, null);
    return typeof result === "number" && Number.isFinite(result) ? String(result) : "—";
  }

  function escapeAttribute(value) {
    return text(value).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/'/g, "&#39;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function statusLabel(status) {
    return STATUS_LABELS[status] || text(status, "stato non dichiarato").replaceAll("_", " ");
  }

  function tone(status) {
    if (["ready", "recovered", "accepted", "committed", "clear", "completed", "graded"].includes(status)) return "positive";
    if (["working", "needs_learner_input", "suspended", "needs_review", "pending", "retryable_conflict", "generated"].includes(status)) return "warning";
    if (["error", "rejected", "conflicted", "conflicted_context", "degraded"].includes(status)) return "error";
    return "neutral";
  }

  function pill(status, label = statusLabel(status)) {
    return `<span class="status-pill" data-tone="${escapeAttribute(tone(status))}">${escapeAttribute(label)}</span>`;
  }

  function sourceRef(value) {
    const source = object(first(object(value), ["source"], value));
    const projection = text(source.projection);
    const sequence = first(source, ["sequence"], null);
    if (!projection || sequence === null || sequence === undefined) return "";
    return `<span class="source-ref">${escapeAttribute(projection)} · seq ${escapeAttribute(sequence)}</span>`;
  }

  function emptyState(title, copy, kind = "empty") {
    const className = kind === "error" ? "error-state" : kind === "unavailable" ? "unavailable-state" : kind === "loading" ? "loading-state" : "empty-state";
    return `<div class="${className}"><h3 class="state-title">${escapeAttribute(title)}</h3><p>${escapeAttribute(copy)}</p></div>`;
  }

  function button(label, route, className = "button button--quiet") {
    return `<button class="${className}" type="button" data-route="${escapeAttribute(route)}">${escapeAttribute(label)}</button>`;
  }

  function aiMarkup(renderer, options, fallback = "") {
    return typeof CardineAI[renderer] === "function" ? CardineAI[renderer](options || {}) : fallback;
  }

  function aiRenderer(renderer, options) {
    return typeof CardineAI[renderer] === "function" ? CardineAI[renderer](options || {}) : "";
  }

  // Named adapters keep the browser integration explicit and easy to audit.
  const aiLoading = (options) => typeof CardineAI.loading === "function" ? CardineAI.loading(options || {}) : "";
  const aiThinking = (options) => typeof CardineAI.thinking === "function" ? CardineAI.thinking(options || {}) : "";
  const aiAnswer = (options) => typeof CardineAI.answer === "function" ? CardineAI.answer(options || {}) : "";
  const aiApproval = (options) => typeof CardineAI.approval === "function" ? CardineAI.approval(options || {}) : "";
  const aiToolStack = (options) => typeof CardineAI.toolStack === "function" ? CardineAI.toolStack(options || {}) : "";
  const aiTaskList = (options) => typeof CardineAI.taskList === "function" ? CardineAI.taskList(options || {}) : "";
  const aiChatPanel = (options) => typeof CardineAI.chatPanel === "function" ? CardineAI.chatPanel(options || {}) : "";
  const aiRecommendation = (options) => typeof CardineAI.recommendation === "function" ? CardineAI.recommendation(options || {}) : "";
  const aiContextGrid = (options) => typeof CardineAI.contextGrid === "function" ? CardineAI.contextGrid(options || {}) : "";
  const aiDiffTable = (options) => typeof CardineAI.diffTable === "function" ? CardineAI.diffTable(options || {}) : "";
  const aiRecordsTable = (options) => typeof CardineAI.recordsTable === "function" ? CardineAI.recordsTable(options || {}) : "";
  const aiFilterTable = (options) => typeof CardineAI.filterTable === "function" ? CardineAI.filterTable(options || {}) : "";
  const aiSidebarSearch = (options) => typeof CardineAI.sidebarSearch === "function" ? CardineAI.sidebarSearch(options || {}) : "";
  const aiCommandSearch = (options) => typeof CardineAI.commandSearchMarkup === "function" ? CardineAI.commandSearchMarkup(options || {}) : "";
  const aiInsightDeck = (options) => typeof CardineAI.insightDeck === "function" ? CardineAI.insightDeck(options || {}) : "";
  const aiCodeBlock = (options) => typeof CardineAI.codeBlock === "function" ? CardineAI.codeBlock(options || {}) : "";
  const aiFineTune = (options) => typeof CardineAI.fineTune === "function" ? CardineAI.fineTune(options || {}) : "";

  function requestId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
    return `ui-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  async function fetchJson(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    if (options.body) headers.set("Content-Type", "application/json");
    const method = text(options.method, "GET").toUpperCase();
    if (method !== "GET" && method !== "HEAD" && !path.endsWith("/auth/login") && state.auth.csrfToken) {
      headers.set("X-CSRF-Token", state.auth.csrfToken);
    }
    const response = await fetch(path, { ...options, headers, cache: "no-store" });
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = null; }
    if (!response.ok) {
      const declaredCode = text(first(object(payload), ["code", "error_code"]), "");
      const safeMessage = MODEL_ERROR_MESSAGES[declaredCode] || SAFE_ERROR_MESSAGES[response.status] || (declaredCode === "invalid_credentials"
        ? "Password non corretta. Riprova."
        : declaredCode === "csrf_failed"
          ? "La sessione non è più valida. Accedi di nuovo."
          : declaredCode === "source_content_unavailable"
            ? "Il testo della fonte non è recuperabile: ripristina o ricarica la fonte prima di continuare."
          : "La richiesta non è riuscita. Riprova tra poco.");
      const error = new Error(safeMessage);
      error.status = response.status;
      error.code = declaredCode;
      error.payload = payload && typeof payload === "object" ? { code: declaredCode } : null;
      if (
        response.status === 401
        && state.auth.mode === "private"
        && !path.startsWith("/api/v1/auth/")
      ) {
        state.auth = {
          status: "unauthenticated",
          authenticated: false,
          mode: "private",
          csrfToken: "",
          account: null,
        };
        error.authExpired = true;
        renderLogin("La sessione è scaduta. Accedi di nuovo per continuare.");
      }
      throw error;
    }
    return payload || {};
  }

  function commandPayload(payload, request = requestId()) {
    return {
      schema_version: SCHEMA_VERSION,
      request_id: request,
      expected_sequence: state.highWaterSequence,
      payload,
    };
  }

  function setStatus(status, message = statusLabel(status)) {
    const bar = $("#global-status");
    const label = $(".status-bar__label", bar);
    if (label) label.textContent = message;
    bar.dataset.status = status;
    const mini = $("#trust-mini");
    mini.dataset.status = status;
    $("#trust-mini-label").textContent = `${statusLabel(status)} · seq ${state.highWaterSequence || "—"}`;
  }

  function updateSequence(sequence) {
    if (typeof sequence === "number" && Number.isFinite(sequence)) state.highWaterSequence = sequence;
    $("#sequence-label").textContent = state.highWaterSequence ? `seq ${state.highWaterSequence}` : "";
    $("#trust-mini-label").textContent = `${statusLabel(state.bootstrap?.shell_status || "ready")} · seq ${state.highWaterSequence || "—"}`;
  }

  function setAccountControl() {
    const control = $("#account-control");
    if (!control) return;
    const name = $("#account-control-name");
    const status = $("#account-control-status");
    const account = object(state.auth.account);
    if (state.auth.authenticated) {
      if (name) name.textContent = text(first(account, ["name", "email", "username"], "Account"), "Account");
      if (status) status.textContent = "sessione privata";
    } else {
      if (name) name.textContent = "Accedi";
      if (status) status.textContent = "area privata";
    }
    control.hidden = false;
  }

  function setChromeVisibility(route = state.route) {
    const dock = $("#continuation-dock");
    const hideDock = !CONTINUATION_ROUTES.has(route) || route === "login" || route === "impostazioni" || route === "oggi" || route === "sessione";
    if (dock) dock.hidden = hideDock;
    const pending = $("#continuation-dock-pending");
    if (pending) pending.hidden = !(state.continuation && state.continuation.pending);
    setAccountControl();
  }

  function continuationSummary(value) {
    const item = object(value);
    const pending = object(first(item, ["continuation", "pending_continuation"], null));
    const last = object(first(item, ["last_turn", "last_turn_summary", "latest_turn"], null));
    const pendingPrompt = text(first(pending, ["prompt", "question", "message", "dialogue_request"], ""), "");
    const lastPrompt = text(first(last, ["summary", "prompt", "content", "text", "message"], ""), "");
    const fingerprint = text(first(pending, ["fingerprint", "continuation_fingerprint"], ""), "");
    if (!pendingPrompt && !lastPrompt && !fingerprint) return null;
    return { pending: Boolean(pendingPrompt || fingerprint), prompt: pendingPrompt || lastPrompt || "Ultimo turno", fingerprint, last };
  }

  function updateContinuationDock(payload = state.viewData || state.bootstrap || {}) {
    const summary = continuationSummary(payload);
    if (summary) {
      state.continuation = summary;
      state.lastTurn = summary.last;
    }
    const dock = $("#continuation-dock");
    if (!dock) return;
    const label = $("#continuation-dock-last-turn .continuation-dock__label");
    if (label) label.textContent = summary?.pending ? "Continua in Chat" : summary?.prompt ? `Ultimo turno · ${summary.prompt}` : "Ultimo turno";
    const form = $("#continuation-dock-form");
    if (form) form.hidden = Boolean(summary?.pending);
    const textarea = $("#continuation-dock-entry");
    if (textarea && textarea.value !== state.continuationDraft) {
      textarea.value = state.continuationDraft;
      resizeComposer(textarea);
    }
    setChromeVisibility(state.route);
  }

  async function loadAuthSession() {
    try {
      // Local repository mode deliberately does not expose the private auth
      // namespace. Discover it first so a local browser has no expected 404.
      const health = object(await fetchJson("/health"));
      const healthMode = text(first(health, ["mode", "access_mode"], ""), "");
      if (healthMode && healthMode !== "private") {
        state.authProbeUnavailable = true;
        state.auth = { status: "local", authenticated: false, mode: healthMode, csrfToken: "", account: null };
        setAccountControl();
        return state.auth;
      }
      const payload = object(await fetchJson("/api/v1/auth/session"));
      const mode = text(first(payload, ["mode", "access_mode"], ""), "");
      const authenticated = payload.authenticated === true || payload.signed_in === true || Boolean(payload.account);
      state.auth = {
        status: authenticated ? "authenticated" : "unauthenticated",
        authenticated,
        mode: mode || (authenticated ? "private" : "local_repository"),
        csrfToken: text(first(payload, ["csrf_token", "csrfToken"], ""), ""),
        account: object(first(payload, ["account", "user", "identity"], null)),
      };
      setAccountControl();
      return state.auth;
    } catch (error) {
      if ([404, 405].includes(error.status)) {
        state.authProbeUnavailable = true;
        state.auth = { status: "local", authenticated: false, mode: "local_repository", csrfToken: "", account: null };
        setAccountControl();
        return state.auth;
      }
      if (error.status === 401) {
        state.auth = { status: "unauthenticated", authenticated: false, mode: "private", csrfToken: "", account: null };
        setAccountControl();
        renderLogin();
        return state.auth;
      }
      state.authProbeUnavailable = true;
      state.auth = { status: "local", authenticated: false, mode: "local_repository", csrfToken: "", account: null };
      setAccountControl();
      return state.auth;
    }
  }

  function renderLogin(errorMessage = "") {
    state.route = "login";
    navActive("login");
    setChromeVisibility("login");
    setView("login", `<section class="login-surface" aria-labelledby="login-heading"><p class="eyebrow">Cardine · area privata</p><h1 id="login-heading">Accedi a Cardine</h1><p class="section-copy">La tua area privata per lo studio locale. La sessione resta attiva solo su questo dispositivo.</p><form class="login-surface__form" id="login-form" data-auth-login><label for="login-password">Password</label><input id="login-password" name="password" type="password" autocomplete="current-password" required inputmode="text"><p class="login-surface__error" id="login-error" ${errorMessage ? "" : "hidden"} role="alert">${escapeAttribute(errorMessage)}</p><button class="button" type="submit">Accedi</button></form><p class="login-surface__note">La password non viene salvata nel browser.</p></section>`);
    $("#login-password")?.focus({ preventScroll: true });
  }

  function renderSettings(payload = {}) {
    const settings = object(payload);
    const account = object(first(settings, ["account", "identity", "user"], state.auth.account));
    const model = object(first(settings, ["model", "model_status"], {}));
    const modelLabel = text(first(model, ["label", "name", "model"], "GPT-5.6 Luna"), "GPT-5.6 Luna");
    const credentialStatus = model.credential_configured === true
      ? "Chiave presente nel runtime: verifica la connessione prima di iniziare la chat."
      : "Nessuna chiave API configurata";
    const accountLabel = text(first(account, ["label", "email", "name", "username"], "Account personale"), "Account personale");
    setView("impostazioni", `<section class="settings-surface" aria-labelledby="settings-heading"><p class="eyebrow">area privata · impostazioni</p><h1 id="settings-heading">Impostazioni</h1><p class="section-copy">Gestisci accesso, dati locali e modello.</p>${settings.error ? `<p class="login-surface__error" role="alert">${escapeAttribute(settings.error)}</p>` : ""}<div class="settings-grid"><section class="settings-card"><h2>Account locale</h2><p>${escapeAttribute(accountLabel)}</p><p>Uscire chiude questa sessione senza eliminare i dati locali.</p><div class="settings-card__actions"><button class="button button--quiet" type="button" data-auth-logout>Esci</button></div></section><section class="settings-card"><h2>Modello</h2><p>Modello attivo: <strong>${escapeAttribute(modelLabel)}</strong>.</p><p>${escapeAttribute(credentialStatus)}</p></section><section class="settings-card"><h2>Chiave API</h2><p>La chiave inserita qui resta disponibile fino al riavvio del servizio. Per mantenerla, configura <code>OPENAI_API_KEY</code> nel secret store del deployment. Cardine non mostra né restituisce il valore.</p><form id="settings-model-form" data-settings-credential autocomplete="off"><label for="settings-credential">Nuova chiave API</label><input id="settings-credential" name="api_key" type="password" autocomplete="off" spellcheck="false" inputmode="text" placeholder="Incolla una nuova chiave" required aria-describedby="credential-settings-help"><p class="field-note" id="credential-settings-help">Cardine non scrive il valore nello storage del browser e svuota il campo subito dopo il salvataggio.</p><div class="settings-card__actions"><button class="button" type="submit">Salva nuova chiave</button><button class="button button--quiet" type="button" data-settings-remove>Rimuovi chiave temporanea</button><span class="settings-card__status" id="credential-settings-status" role="status"></span></div></form></section><section class="settings-card"><h2>Diagnostica preview</h2><p>Mostra solo codici, route e orari locali: non include messaggi, fonti, cookie o chiavi.</p><div id="preview-diagnostics"><p class="field-note">Carico diagnostica locale…</p></div><div class="settings-card__actions"><button class="button button--quiet" type="button" data-diagnostics-refresh>Aggiorna diagnostica</button></div></section><section class="settings-card"><h2>Dati del corso</h2><p>I dati di studio restano nel repository locale e non vengono inclusi nelle impostazioni del browser.</p></section><section class="settings-card"><h2>Privacy</h2><p>Sessione e chiave temporanea vengono rimosse al riavvio. Cardine non salva segreti nello storage del browser.</p></section></div></section>`);
  }

  async function loadSettings(navigationVersion = state.navigationVersion) {
    try {
      const payload = await fetchJson("/api/v1/settings");
      if (navigationVersion !== state.navigationVersion) return;
      state.viewData = payload;
      renderSettings(payload);
      enhanceSettingsSurface();
      await loadWorkspace(navigationVersion);
      await loadDiagnostics(navigationVersion);
    } catch (error) {
      if (navigationVersion !== state.navigationVersion) return;
      if (error.authExpired) return;
      renderSettings({ error: error.message });
    }
  }

  function enhanceSettingsSurface() {
    const cards = $$(".settings-card", root);
    const modelCard = cards[1];
    if (modelCard && !$("[data-settings-check]", modelCard)) {
      modelCard.insertAdjacentHTML("beforeend", `<div class="settings-card__actions"><button class="button button--quiet" type="button" data-settings-check>Verifica decisione tutor</button><span class="settings-card__status" id="model-check-status" role="status"></span></div>`);
    }
    if (!$("#workspace-card", root)) {
      const workspaceCard = document.createElement("section");
      workspaceCard.className = "settings-card";
      workspaceCard.id = "workspace-card";
      workspaceCard.innerHTML = `<h2>Corso e sessione</h2><div id="workspace-manager"><p class="field-note">Carico corsi e sessioni disponibili…</p></div>`;
      cards[2]?.before(workspaceCard);
    }
  }

  async function loadWorkspace(navigationVersion = state.navigationVersion) {
    try {
      const payload = await fetchJson("/api/v1/workspace");
      if (navigationVersion !== state.navigationVersion) return;
      renderWorkspace(payload);
    } catch (error) {
      if (navigationVersion !== state.navigationVersion) return;
      const manager = $("#workspace-manager", root);
      if (manager) manager.innerHTML = `<p class="login-surface__error" role="alert">${escapeAttribute(error.message)}</p>`;
    }
  }

  function workspaceCommand(payload) {
    return {
      schema_version: SCHEMA_VERSION,
      request_id: requestId(),
      expected_sequence: state.highWaterSequence,
      payload,
    };
  }

  async function selectWorkspace(form) {
    const course = form.elements.namedItem("course_id");
    const session = form.elements.namedItem("session_id");
    if (!(course instanceof HTMLSelectElement) || !(session instanceof HTMLSelectElement) || !course.value || !session.value) return;
    await fetchJson("/api/v1/workspace/select", { method: "POST", body: JSON.stringify(workspaceCommand({ course_id: course.value, session_id: session.value })) });
    await loadBootstrap();
    await loadRoute("sessione");
  }

  async function startWorkspaceSession(form) {
    const courseControl = form.elements.namedItem("course_id");
    const input = form.elements.namedItem("session_id");
    const course = courseControl instanceof HTMLSelectElement
      ? courseControl.value
      : (() => {
        const courseValue = first(object(state.bootstrap), ["course"], first(object(state.bootstrap), ["course_id"], ""));
        return typeof courseValue === "object" ? text(object(courseValue).id) : text(courseValue);
      })();
    if (!(input instanceof HTMLInputElement) || !input.value.trim() || !course) return;
    await fetchJson("/api/v1/workspace/sessions", { method: "POST", body: JSON.stringify(workspaceCommand({ course_id: course, session_id: input.value.trim() })) });
    await loadBootstrap();
    await loadRoute("sessione");
  }

  async function createWorkspaceCourse(form) {
    const value = (name) => {
      const item = form.elements.namedItem(name);
      return item instanceof HTMLInputElement ? item.value.trim() : "";
    };
    const courseId = value("course_id");
    const title = value("title");
    const language = value("language");
    const learningGoal = value("learning_goal");
    if (!courseId || !title || !language || !learningGoal) return;
    await fetchJson("/api/v1/workspace/courses", { method: "POST", body: JSON.stringify(workspaceCommand({ course_id: courseId, title, language, learning_goals: [learningGoal] })) });
    await loadWorkspace();
    setStatus("committed", "Corso creato");
  }

  async function createChatCourse(form) {
    const field = (name) => {
      const item = form.elements.namedItem(name);
      return item instanceof HTMLInputElement || item instanceof HTMLSelectElement
        ? item.value.trim()
        : "";
    };
    const title = field("title");
    const language = field("language");
    const learningGoal = field("learning_goal");
    if (!title || !language || !learningGoal) return;
    const draft = object(state.chatCourseCreation);
    const nonce = text(draft.nonce) || requestId().replaceAll("-", "").slice(0, 10);
    const courseId = `${courseCreationSlug(title)}-${nonce}`;
    const sessionId = `${courseId}-inizio`;
    const status = $("#chat-course-creation-status", form);
    const submit = $('button[type="submit"]', form);
    if (submit) submit.disabled = true;
    if (status) status.textContent = "Creo corso e sessione…";
    try {
      const receipt = await fetchJson("/api/v1/chat/course-creation", {
        method: "POST",
        body: JSON.stringify(commandPayload({
          confirmed: true,
          course_id: courseId,
          title,
          language,
          learning_goals: [learningGoal],
          session_id: sessionId,
        })),
      });
      updateSequence(first(receipt, ["high_water_sequence", "sequence"], state.highWaterSequence));
      state.chatCourseCreation = null;
      setStatus("ready", "Nuovo corso pronto");
      await loadBootstrap();
    } catch (error) {
      if (error.authExpired) return;
      if (status) status.textContent = error.message;
      setStatus("error", "Il corso non è stato creato");
    } finally {
      if (submit) submit.disabled = false;
    }
  }

  function renderWorkspace(payload = {}) {
    const manager = $("#workspace-manager", root);
    if (!manager) return;
    const courses = array(payload.courses).map((item) => object(item));
    const selected = object(payload.selected);
    const selectedCourse = text(first(selected, ["course_id"], ""));
    const selectedSession = text(first(selected, ["session_id"], ""));
    const courseOptions = courses.map((course) => `<option value="${escapeAttribute(text(course.id))}" ${text(course.id) === selectedCourse ? "selected" : ""}>${escapeAttribute(text(course.title, text(course.id)))}</option>`).join("");
    const sessions = courses.flatMap((course) => array(course.sessions).map((item) => ({ ...object(item), course_id: text(course.id) })));
    const sessionOptions = sessions.map((session) => `<option value="${escapeAttribute(text(session.id))}" data-course-id="${escapeAttribute(text(session.course_id))}" ${text(session.id) === selectedSession ? "selected" : ""}>${escapeAttribute(text(session.id))} · ${escapeAttribute(text(session.status, "unknown"))}</option>`).join("");
    manager.innerHTML = `<p class="field-note">Seleziona il contesto di studio attivo. Il cambio non modifica i dati canonici.</p><form class="workspace-form" data-workspace-select><label for="workspace-course">Corso</label><select id="workspace-course" name="course_id">${courseOptions || "<option value=\"\">Nessun corso</option>"}</select><label for="workspace-session">Sessione</label><select id="workspace-session" name="session_id">${sessionOptions || "<option value=\"\">Nessuna sessione</option>"}</select><div class="settings-card__actions"><button class="button" type="submit">Usa selezione</button><span class="settings-card__status" id="workspace-status" role="status"></span></div></form><form class="workspace-form workspace-form--new" data-workspace-session><label for="workspace-new-session-course">Corso</label><select id="workspace-new-session-course" name="course_id">${courseOptions || "<option value=\"\">Nessun corso</option>"}</select><label for="workspace-new-session">Nuova sessione</label><input id="workspace-new-session" name="session_id" required maxlength="160" placeholder="es. ripasso-agosto"><div class="settings-card__actions"><button class="button button--quiet" type="submit">Avvia sessione</button></div></form><form class="workspace-form workspace-form--new" data-workspace-course><p class="field-note">Crea un corso vuoto; aggiungerai le fonti dalla sezione Fonti.</p><label for="workspace-new-course-id">ID corso</label><input id="workspace-new-course-id" name="course_id" required maxlength="160"><label for="workspace-new-course-title">Titolo</label><input id="workspace-new-course-title" name="title" required maxlength="240"><label for="workspace-new-course-language">Lingua</label><input id="workspace-new-course-language" name="language" value="it" required maxlength="32"><label for="workspace-new-course-goal">Obiettivo</label><input id="workspace-new-course-goal" name="learning_goal" required maxlength="240"><div class="settings-card__actions"><button class="button button--quiet" type="submit">Crea corso</button></div></form>`;
  }

  async function logout() {
    try {
      await fetchJson("/api/v1/auth/logout", { method: "POST", body: JSON.stringify({}) });
    } catch (_) {
      // An expired session has the same user-visible result as an explicit logout.
    }
    state.auth = { status: "unauthenticated", authenticated: false, mode: "private", csrfToken: "", account: null };
    state.continuation = null;
    renderLogin();
  }

  async function login(form) {
    const password = form.elements.namedItem("password");
    const errorNode = $("#login-error", form);
    const submit = $('button[type="submit"]', form);
    if (!(password instanceof HTMLInputElement) || !password.value) {
      password?.setAttribute("aria-invalid", "true");
      password?.focus({ preventScroll: true });
      return;
    }
    password.removeAttribute("aria-invalid");
    if (submit) submit.disabled = true;
    try {
      await fetchJson("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ password: password.value }),
      });
      password.value = "";
      const auth = await loadAuthSession();
      if (!auth.authenticated) throw new Error("La sessione non è stata aperta.");
      await loadBootstrap();
    } catch (error) {
      password.value = "";
      password.setAttribute("aria-invalid", "true");
      password.setAttribute("aria-describedby", "login-error");
      if (errorNode) {
        errorNode.hidden = false;
        errorNode.textContent = error.status === 401
          ? "Password non corretta. Riprova."
          : error.message;
      }
      password.focus({ preventScroll: true });
    } finally {
      if (submit) submit.disabled = false;
    }
  }

  async function replaceCredential(form) {
    const input = form.elements.namedItem("api_key");
    const status = $("#credential-settings-status", form);
    const submit = $('button[type="submit"]', form);
    if (!(input instanceof HTMLInputElement) || !input.value.trim()) {
      input?.setAttribute("aria-invalid", "true");
      input?.focus({ preventScroll: true });
      return;
    }
    input.removeAttribute("aria-invalid");
    if (submit) submit.disabled = true;
    try {
      await fetchJson(SETTINGS_ENDPOINTS.modelCredential, {
        method: "POST",
        body: JSON.stringify({ api_key: input.value }),
      });
      input.value = "";
      await loadSettings();
      const refreshedStatus = $("#credential-settings-status");
      if (refreshedStatus) refreshedStatus.textContent = "Chiave aggiornata per questa sessione.";
    } catch (error) {
      input.value = "";
      input.setAttribute("aria-invalid", "true");
      if (status) status.textContent = error.message;
      input.focus({ preventScroll: true });
    } finally {
      if (submit) submit.disabled = false;
    }
  }

  async function removeCredential(control) {
    const status = $("#credential-settings-status");
    control.disabled = true;
    try {
      await fetchJson(SETTINGS_ENDPOINTS.removeCredential, {
        method: "POST",
        body: JSON.stringify({}),
      });
      await loadSettings();
      const refreshedStatus = $("#credential-settings-status");
      if (refreshedStatus) refreshedStatus.textContent = "Chiave temporanea rimossa.";
    } catch (error) {
      if (status) status.textContent = error.message;
    } finally {
      control.disabled = false;
    }
  }

  async function checkModelConnection(control) {
    const status = $("#model-check-status");
    control.disabled = true;
    if (status) status.textContent = "Verifico…";
    try {
      const result = await fetchJson("/api/v1/settings/model/check", {
        method: "POST",
        body: JSON.stringify(workspaceCommand({})),
      });
      if (status) status.textContent = result.status === "ok" ? "Decisione del tutor verificata. Il grounding delle fonti viene verificato al primo messaggio." : text(result.message, "Modello non disponibile.");
      await loadDiagnostics();
    } catch (error) {
      if (status) status.textContent = error.message;
    } finally {
      control.disabled = false;
    }
  }

  async function loadDiagnostics(navigationVersion = state.navigationVersion) {
    const target = $("#preview-diagnostics");
    if (!target) return;
    try {
      const payload = object(await fetchJson("/api/v1/diagnostics"));
      if (navigationVersion !== state.navigationVersion) return;
      const entries = array(payload.entries);
      target.innerHTML = entries.length
        ? `<ul class="plain-list">${entries.slice(-8).reverse().map((entry) => {
          const row = object(entry);
          const at = Number.isFinite(row.at_unix) ? new Date(row.at_unix * 1000).toLocaleTimeString() : "orario non disponibile";
          return `<li><code>${escapeAttribute(text(row.category, "evento"))}</code> · ${escapeAttribute(text(row.path, "route"))} · HTTP ${escapeAttribute(text(row.status_code, "—"))} · ${escapeAttribute(at)}</li>`;
        }).join("")}</ul>`
        : `<p class="field-note">Nessun errore registrato in questa esecuzione.</p>`;
    } catch (error) {
      target.innerHTML = `<p class="field-note">Diagnostica non disponibile: ${escapeAttribute(error.message)}</p>`;
    }
  }

  async function refreshBootstrapCounts() {
    try {
      const payload = await fetchJson("/api/v1/bootstrap");
      state.bootstrap = object(payload);
      updateSequence(first(payload, ["high_water_sequence", "sequence"], state.highWaterSequence));
      renderCourse(payload);
      updateCounts(payload);
      updateContinuationDock(payload);
    } catch (_) {
      // The canonical command already committed; a sidebar refresh is
      // advisory and must not turn that success into a false command error.
    }
  }

  function setBusy(busy) {
    state.loading = busy;
    $$('[data-command]').forEach((control) => {
      if (busy) {
        control.dataset.disabledBeforeBusy = String(control.disabled);
        control.disabled = true;
      } else if (Object.hasOwn(control.dataset, "disabledBeforeBusy")) {
        control.disabled = control.dataset.disabledBeforeBusy === "true";
        delete control.dataset.disabledBeforeBusy;
      }
    });
    $$('[data-entry-form]').forEach((form) => {
      form.setAttribute("aria-busy", String(busy));
      const textarea = $("textarea", form);
      if (textarea) syncComposerState(form, textarea);
    });
  }

  function renderCourse(bootstrap) {
    const course = object(bootstrap.course);
    const session = object(bootstrap.session);
    $("#rail-course").textContent = text(course.title, "corso locale");
    const list = $("#recent-session-list");
    const sessionId = text(session.id, "");
    if (sessionId) {
      $("#recent-sessions").hidden = false;
      list.innerHTML = `<button class="recent-session" type="button" data-route="sessione"><span>${escapeAttribute(text(first(session, ["title", "topic"], course.title), "Sessione corrente"))}</span><span class="recent-session__date"> · in corso</span></button>`;
    } else {
      $("#recent-sessions").hidden = true;
    }
    document.title = `${text(course.title, "Cardine")} · Cardine`;
    const trustSessionId = sessionId || "sessione non selezionata";
    const mode = text(first(bootstrap, ["mode"], "local_repository"), "local_repository");
    $("#runtime-label").textContent = "ambiente locale · dati del corso";
    $("#trust-copy").innerHTML = `<p>Corso <strong>${escapeAttribute(text(course.title, "non dichiarato"))}</strong>, sessione <code>${escapeAttribute(trustSessionId)}</code>. Modalità: <strong>${escapeAttribute(mode)}</strong>. Il browser riceve dal servizio locale solo i dati consentiti e non apre SQLite, file di corso, runtime del provider o credenziali.</p><ul><li>Le mutazioni usano request ID e sequenza osservata.</li><li>Il Piano mostra solo fatti attribuiti e non calcola un punteggio.</li><li>Un conflitto di fonte non viene trasformato in conflitto di contesto.</li></ul>`;
  }

  function updateCounts(bootstrap) {
    const counts = object(bootstrap.counts);
    $("[data-count='assessments']").textContent = count(counts, ["assessments", "assessment_count"]);
    $("[data-count='due_reviews']").textContent = count(counts, ["due_reviews", "due_review_count"]);
    $("[data-count='pending_proposals']").textContent = count(counts, ["pending_proposals", "proposal_count"]);
    $("[data-count='context_conflicts']").textContent = count(counts, ["context_conflicts", "conflict_count"]);
    const features = object(bootstrap.features);
    $$("[data-route='ripasso'], [data-route='proposte'], [data-route='verifiche'], [data-route='conflitti']").forEach((control) => {
      const route = control.dataset.route;
      const feature = route === "ripasso" ? "recall" : route === "proposte" ? "artifacts" : route === "verifiche" ? "assessments" : "context_resolution";
      if (features[feature] === false) control.dataset.unavailable = "true";
    });
  }

  function navActive(route) {
    $$("[data-route]").forEach((control) => {
      const active = control.dataset.route === route;
      control.classList.toggle("is-active", active);
      if (active) {
        control.setAttribute("aria-current", "page");
      } else {
        control.removeAttribute("aria-current");
      }
    });
  }

  function setView(route, html) {
    state.route = route;
    navActive(route);
    setChromeVisibility(route);
    destroyPrimitiveEnhancements();
    root.innerHTML = html;
    bindDynamicControls();
    destroyPrimitiveEnhancements = typeof CardineAI.enhance === "function"
      ? CardineAI.enhance(root, {
        populateComposer: false,
        onFollowUp: (prompt, control) => populateComposerPrompt(prompt, control),
        onFineTune: (prompt, control) => populateComposerPrompt(prompt, control),
      })
      : () => {};
    const initialComposer = route === "oggi" ? $("#entry", root) : null;
    if (initialComposer) {
      initialComposer.focus({ preventScroll: true });
    } else {
      $("#main-content").focus({ preventScroll: true });
    }
    updateContinuationDock(state.viewData || state.bootstrap || {});
  }

  function renderLoading(route) {
    const loading = aiLoading({
      label: `Carico ${ROUTES[route]?.heading || "la sezione"}`,
      detail: "Sto leggendo lo stato canonico dal servizio locale.",
    }, emptyState("Caricamento", "Sto leggendo lo stato canonico dal servizio locale.", "loading"));
    setView(route, `<section class="section-grid"><div class="section-grid__main"><p class="section-kicker">${escapeAttribute(ROUTES[route]?.heading || "Cardine")}</p><div class="loading-state">${loading}</div></div><aside class="section-grid__side"><div class="skeleton-card"></div></aside></section>`);
  }

  function renderError(route, error) {
    const message = error && error.message ? error.message : "Il servizio locale non ha risposto.";
    setStatus("error", "La sezione non è disponibile");
    setView(route, `<section class="section-grid"><div class="section-grid__main"><p class="section-kicker">${escapeAttribute(ROUTES[route]?.heading || "Cardine")}</p>${emptyState("Stato non disponibile", message, "error")}<div class="state-actions"><button class="button" type="button" data-retry-route="${escapeAttribute(route)}">Riprova</button>${button("Torna a oggi", "oggi")}</div></div><aside class="section-grid__side">${emptyState("Nessuna cancellazione locale", "L'ultimo stato canonico non viene sostituito da dati inventati.")}</aside></section>`);
  }

  async function loadBootstrap() {
    const bootstrapNavigationVersion = state.navigationVersion;
    setStatus("working", "Caricamento del corso…");
    try {
      const payload = await fetchJson("/api/v1/bootstrap");
      state.bootstrap = object(payload);
      updateSequence(first(payload, ["high_water_sequence", "sequence"], 0));
      renderCourse(payload);
      updateCounts(payload);
      setStatus(text(payload.shell_status, "ready"), `Corso pronto · ${text(object(payload.course).title, "corso locale")}`);
      if (bootstrapNavigationVersion === state.navigationVersion) {
        await loadRoute("oggi", payload);
      }
    } catch (error) {
      if (bootstrapNavigationVersion === state.navigationVersion) {
        if (error.authExpired) return;
        renderError("oggi", error);
        $("#rail-course").textContent = "corso non disponibile · selezione esplicita richiesta";
      }
    }
  }

  async function loadRoute(route, suppliedData = null) {
    if (!ROUTES[route]) route = "oggi";
    const navigationVersion = ++state.navigationVersion;
    if (route === "login") {
      renderLogin();
      return;
    }
    if (route === "impostazioni") {
      if (state.auth.mode === "private" && !state.auth.authenticated) {
        renderLogin("Accedi per aprire le impostazioni.");
        return;
      }
      renderLoading(route);
      await loadSettings(navigationVersion);
      return;
    }
    const featureByRoute = { proposte: "artifacts", verifiche: "assessments", evidenze: "evidence", ripasso: "recall", conflitti: "context_resolution" };
    if (state.bootstrap && object(state.bootstrap.features)[featureByRoute[route]] === false) {
      setStatus("degraded", `${ROUTES[route].heading} non disponibile`);
      renderUnavailable(route);
      return;
    }
    renderLoading(route);
    try {
      const payload = suppliedData || await fetchJson(ROUTES[route].endpoint);
      if (navigationVersion !== state.navigationVersion) return;
      state.viewData = payload;
      updateContinuationDock(payload);
      if (route === "oggi") {
        state.bootstrap = object(payload);
        renderCourse(state.bootstrap);
        updateCounts(state.bootstrap);
      }
      updateSequence(first(payload, ["high_water_sequence", "sequence"], state.highWaterSequence));
      const status = text(first(payload, ["shell_status", "status"], state.bootstrap?.shell_status || "ready"), "ready");
      setStatus(status, `${ROUTES[route].heading} · ${statusLabel(status)}`);
      if (route === "oggi") renderOggi(payload);
      if (route === "sessione") renderSessione(payload);
      if (route === "fonti") renderFonti(payload);
      if (route === "proposte") renderProposte(payload);
      if (route === "verifiche") renderVerifiche(payload);
      if (route === "evidenze") renderEvidenze(payload);
      if (route === "ripasso") renderRipasso(payload);
      if (route === "piano") renderPlan(payload);
      if (route === "conflitti") renderConflitti(payload);
    } catch (error) {
      if (navigationVersion !== state.navigationVersion) return;
      if (error.authExpired) return;
      renderError(route, error);
    }
  }

  function renderUnavailable(route) {
    setView(route, `<section class="section-grid"><section class="section-grid__main"><p class="section-kicker">${escapeAttribute(ROUTES[route]?.heading || "Cardine")}</p>${emptyState("Questa sezione non è ancora attiva", "Il corso collegato non fornisce ancora dati per questa sezione. Tutto il resto continua a funzionare.", "unavailable")}<div class="state-actions">${button("Torna a oggi", "oggi", "button")}</div></section><aside class="section-grid__side">${emptyState("Nessuna degradazione globale", "La capacità opzionale è isolata dalla sessione principale.")}</aside></section>`);
  }

  function renderOggi(payload) {
    const course = object(first(payload, ["course"], state.bootstrap?.course));
    const materials = array(object(payload.materials).items);
    const onboarding = object(payload.onboarding);
    if (!materials.length) {
      renderSourceFirstOnboarding(course, materials);
      return;
    }
    if (onboarding.needs_study_intent && !state.studySetup) {
      renderStudyIntentStep(course, materials);
      return;
    }
    if (onboarding.needs_study_intent && state.studySetup) {
      renderStudyTopicStep(course, materials);
      return;
    }
    const status = text(first(payload, ["shell_status", "status"], state.bootstrap?.shell_status), "ready");
    const suspended = status === "suspended" || status === "needs_learner_input";
    const counts = object(payload.counts);
    const readiness = object(payload.readiness);
    const recall = object(readiness.recall);
    const due = first(counts, ["due_reviews"], first(recall, ["due_count"], 0));
    const pending = first(counts, ["pending_proposals"], 0);
    const conflicts = first(counts, ["context_conflicts"], 0);
    const openWork = Number(due) + Number(pending) + Number(conflicts);
    const today = openWork > 0
      ? `<div class="today-strip" aria-label="Lavoro aperto oggi"><button type="button" data-route="ripasso"><strong>${escapeAttribute(due)}</strong><span>ripassi dovuti</span></button><button type="button" data-route="proposte"><strong>${escapeAttribute(pending)}</strong><span>proposte</span></button><button type="button" data-route="conflitti"><strong>${escapeAttribute(conflicts)}</strong><span>conflitti</span></button></div>`
      : `<p class="today-clear">Non hai ripassi, proposte o conflitti in sospeso. Puoi iniziare con una domanda.</p>`;
    const taskItems = [
      { label: `${due} ripassi dovuti`, detail: "Ripassi già programmati dal corso.", status: Number(due) > 0 ? "open" : "clear" },
      { label: `${pending} proposte`, detail: "Revisioni che attendono una decisione esplicita.", status: Number(pending) > 0 ? "open" : "clear" },
      { label: `${conflicts} conflitti`, detail: "Divergenze learner-context ancora da risolvere.", status: Number(conflicts) > 0 ? "open" : "clear" },
    ];
    const evidence = array(readiness.evidence);
    const insights = evidence.slice(0, 3).map((item) => ({
      title: first(object(item), ["criterion", "concept", "label", "name"], "Evidenza del corso"),
      detail: first(object(item), ["detail", "dimension", "disposition", "status"], "Evidenza canonica disponibile."),
      source: sourceRef(item).replace(/<[^>]+>/g, ""),
    }));
    if (!insights.length) insights.push({ title: "Stato della sessione", detail: `La sessione è ${statusLabel(status)}.`, source: "proiezione locale" });
    const support = `<details class="chat-home__support"><summary>Panoramica di studio</summary><div class="chat-home__support-grid"><div class="ai-home-card">${aiTaskList({ title: "Lavoro aperto", tasks: taskItems })}</div><div class="ai-home-card">${aiRecommendation({ title: openWork > 0 ? "Un passo alla volta" : "Pronto per una domanda", detail: openWork > 0 ? "Scegli una coda già dichiarata dal corso e continua senza cambiare stato dal browser." : "Scrivi al tutor e mantieni la sessione al centro.", prompt: openWork > 0 ? "Aiutami a scegliere il prossimo ripasso" : "Fammi una domanda di ripasso sulle fonti disponibili", actionLabel: openWork > 0 ? "Chiedimi cosa fare" : "Prepara una domanda" })}</div><div class="ai-home-card">${aiInsightDeck({ title: "Segnali utili", insights })}</div></div></details>`;
    const createCourse = state.auth.authenticated
      ? `<button class="chat-home__course-action" type="button" data-open-course-creation>Crea un corso</button>`
      : "";
    setView("oggi", `<section class="chat-home" aria-labelledby="home-heading"><div class="chat-home__center"><p class="eyebrow">${escapeAttribute(text(course.title, "corso locale"))}</p><h1 id="home-heading">${suspended ? "Riprendiamo da dove eravamo?" : "Come vuoi studiare oggi?"}</h1>${entryForm("hero-entry", "Scrivi al tutor", "Chiedi qualsiasi cosa sul corso…")}${createCourse}${renderChatCourseCreation()}${suspended ? `<button class="resume-chat" type="button" data-route="sessione">Riprendi la sessione in corso</button>` : ""}${today}</div>${support}</section>`);
  }

  function renderSourceFirstOnboarding(course, materials) {
    const authenticated = state.auth.mode !== "private" || state.auth.authenticated;
    const uploadBody = authenticated
      ? `<form class="source-upload-form" data-source-upload><label for="source-upload-file">File di testo o Markdown</label><input id="source-upload-file" name="file" type="file" accept=".txt,.md,text/plain,text/markdown"><span class="field-note">Oppure incolla il testo qui sotto. PDF, immagini e altri formati non sono ancora importabili.</span><label for="source-upload-text">Testo della fonte</label><textarea id="source-upload-text" name="content" rows="6" maxlength="196608" placeholder="Incolla appunti, programma o una lezione…"></textarea><label for="source-upload-title">Titolo</label><input id="source-upload-title" name="title" maxlength="240" placeholder="es. Lezione 1 · Emodinamica"><div class="state-actions"><button class="button" type="submit">Aggiungi questa fonte</button><span class="settings-card__status" data-source-upload-status role="status"></span></div></form>`
      : `<p class="field-note">Accedi per aggiungere fonti al corso e iniziare il setup.</p><button class="button" type="button" data-route="login">Accedi</button>`;
    setView("oggi", `<section class="chat-home chat-home--setup" aria-labelledby="home-heading"><div class="chat-home__center"><p class="eyebrow">${escapeAttribute(text(course.title, "corso locale"))} · passo 1 di 3</p><h1 id="home-heading">Partiamo dai materiali.</h1><p class="chat-home__lede">Prima leggiamo le fonti del corso; solo dopo sceglieremo l’argomento iniziale insieme.</p><section class="study-setup-card" aria-labelledby="source-setup-heading"><h2 id="source-setup-heading">Aggiungi una fonte</h2><p>Cardine usa solo le fonti salvate nel repository del corso. Puoi aggiungere una lezione alla volta.</p>${uploadBody}</section></div></section>`);
  }

  function renderStudyIntentStep(course, materials) {
    const sourceNames = materials.slice(0, 3).map((item) => text(object(item).title)).filter(Boolean).join(", ");
    setView("oggi", `<section class="chat-home chat-home--setup" aria-labelledby="home-heading"><div class="chat-home__center"><p class="eyebrow">${escapeAttribute(text(course.title, "corso locale"))} · passo 2 di 3</p><h1 id="home-heading">Ho letto le tue fonti.</h1><p class="chat-home__lede">Ora impostiamo il contesto dello studio, così il primo argomento parte con il ritmo giusto.</p><section class="study-setup-card" aria-labelledby="intent-setup-heading"><h2 id="intent-setup-heading">Il tuo obiettivo</h2><p>${escapeAttribute(String(materials.length))} ${materials.length === 1 ? "fonte è pronta" : "fonti sono pronte"}${sourceNames ? `: ${escapeAttribute(sourceNames)}` : ""}. Inserisci solo ciò che serve per questa sessione.</p><form data-study-setup class="study-setup-form"><label for="study-exam-date">Data dell’esame <span>(facoltativa)</span></label><input id="study-exam-date" name="exam_date" type="date"><label for="study-objective">Obiettivo di studio</label><input id="study-objective" name="objective" required maxlength="240" placeholder="es. capire la fisiologia, non memorizzare a caso"><label for="study-time">Tempo disponibile oggi</label><select id="study-time" name="available_time" required><option value="10 minuti">10 minuti</option><option value="25 minuti" selected>25 minuti</option><option value="45 minuti">45 minuti</option><option value="60 minuti o più">60 minuti o più</option></select><div class="state-actions"><button class="button" type="submit">Continua</button></div></form></section></div></section>`);
  }

  function renderStudyTopicStep(course, materials) {
    const setup = object(state.studySetup);
    const source = object(materials[0]);
    const suggested = text(first(source, ["title"], "la prima fonte"));
    const sourceCount = materials.length === 1 ? "la fonte disponibile" : `le ${materials.length} fonti disponibili`;
    setView("oggi", `<section class="chat-home chat-home--setup" aria-labelledby="home-heading"><div class="chat-home__center"><p class="eyebrow">${escapeAttribute(text(course.title, "corso locale"))} · passo 3 di 3</p><h1 id="home-heading">Scegliamo il primo argomento.</h1><p class="chat-home__lede">Dalla struttura di ${escapeAttribute(sourceCount)} partirei da <strong>${escapeAttribute(suggested)}</strong>. È una proposta, non una decisione automatica.</p><section class="study-setup-card" aria-labelledby="topic-setup-heading"><h2 id="topic-setup-heading">Primo focus</h2><p>Obiettivo: ${escapeAttribute(text(setup.objective))} · Tempo: ${escapeAttribute(text(setup.availableTime))}${setup.examDate ? ` · Esame: ${escapeAttribute(text(setup.examDate))}` : ""}</p><div class="state-actions"><button class="button" type="button" data-study-topic-default="${escapeAttribute(suggested)}">Inizia da ${escapeAttribute(suggested)}</button></div><form class="study-setup-form study-setup-form--priority" data-study-topic><label for="study-topic-custom">Oppure scegli un’altra priorità</label><input id="study-topic-custom" name="topic" required maxlength="240" placeholder="es. le parti più difficili per me"><div class="state-actions"><button class="button button--quiet" type="submit">Usa questa priorità</button></div></form></section></div></section>`);
  }

  function entryForm(id, label, placeholder, buttonClass = "", attributes = "") {
    const textareaId = id === "hero-entry" ? "entry" : `${id}-text`;
    const modeClass = id === "hero-entry" ? "composer--hero" : "composer--session";
    return `<form id="${escapeAttribute(id)}" class="composer ${modeClass}" data-entry-form ${attributes}><label class="visually-hidden" for="${escapeAttribute(textareaId)}">${escapeAttribute(label)}</label><div class="composer__surface"><textarea id="${escapeAttribute(textareaId)}" name="learner_entry" maxlength="${MAX_ENTRY_CHARS}" rows="1" required placeholder="${escapeAttribute(placeholder)}" aria-describedby="${escapeAttribute(textareaId)}-hint"></textarea><div class="composer__toolbar"><button class="composer__add" type="button" data-route="fonti" aria-label="Apri fonti" title="Apri fonti"><span class="icon icon--plus" aria-hidden="true"></span></button><span class="composer__spacer"></span><button class="composer__mode" type="button" data-open-tutor-info aria-label="Stato del tutor e provenienza">GPT-5.6 Luna <span class="icon icon--caret-down" aria-hidden="true"></span></button><span class="visually-hidden composer__hint" id="${escapeAttribute(textareaId)}-hint">Invio invia · Maiusc + Invio va a capo</span><button class="composer__send ${buttonClass}" type="submit" aria-label="Invia messaggio" title="Invia messaggio" disabled><span class="icon icon--arrow-up" aria-hidden="true"></span></button></div></div></form>`;
  }

  function courseCreationIntentTitle(value) {
    const normalized = text(value).trim();
    const slash = normalized.match(/^\/(?:crea|nuovo)-corso(?:\s+(.+))?$/i);
    if (slash) return text(slash[1]).trim();
    const natural = normalized.match(/^(?:crea|creiamo|aggiungi)\s+(?:un\s+|una\s+)?corso(?:\s+(?:di|del|della|per))?\s*(.*)$/i);
    return natural ? text(natural[1]).trim() : null;
  }

  function courseCreationSlug(value) {
    const normalized = text(value)
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/(^-|-$)/g, "")
      .slice(0, 112);
    return normalized || "corso";
  }

  function openChatCourseCreation(seedTitle = "") {
    if (!state.auth.authenticated) {
      loadRoute("login");
      return;
    }
    const previous = object(state.chatCourseCreation);
    state.chatCourseCreation = {
      title: text(seedTitle).trim() || text(previous.title),
      language: text(previous.language, "it"),
      learningGoal: text(previous.learningGoal),
      nonce: text(previous.nonce) || requestId().replaceAll("-", "").slice(0, 10),
    };
    if (state.route === "oggi") {
      renderOggi(state.viewData || state.bootstrap || {});
      focusChatCourseCreation();
    } else {
      loadRoute("oggi").then(focusChatCourseCreation).catch(() => {});
    }
  }

  function focusChatCourseCreation() {
    const title = $("#chat-course-title", root);
    const goal = $("#chat-course-goal", root);
    (title?.value ? goal : title)?.focus({ preventScroll: true });
  }

  function closeChatCourseCreation() {
    state.chatCourseCreation = null;
    if (state.route === "oggi") renderOggi(state.viewData || state.bootstrap || {});
  }

  function renderChatCourseCreation() {
    const draft = object(state.chatCourseCreation);
    if (!Object.keys(draft).length || !state.auth.authenticated) return "";
    return `<section class="chat-course-creation" aria-labelledby="chat-course-creation-heading"><div><p class="eyebrow">nuovo spazio di studio</p><h2 id="chat-course-creation-heading">Creiamo un corso</h2><p>Conferma i dettagli: Cardine creerà il corso, aprirà la prima sessione e la selezionerà.</p></div><form class="chat-course-creation__form" data-chat-course-creation><label for="chat-course-title">Nome del corso</label><input id="chat-course-title" name="title" value="${escapeAttribute(text(draft.title))}" required maxlength="240" autocomplete="off" placeholder="es. Fisiologia umana"><label for="chat-course-language">Lingua</label><select id="chat-course-language" name="language"><option value="it" ${text(draft.language, "it") === "it" ? "selected" : ""}>Italiano</option><option value="en" ${text(draft.language) === "en" ? "selected" : ""}>English</option></select><label for="chat-course-goal">Primo obiettivo</label><input id="chat-course-goal" name="learning_goal" value="${escapeAttribute(text(draft.learningGoal))}" required maxlength="240" autocomplete="off" placeholder="es. Collegare funzione e fisiopatologia"><p class="field-note">Il corso verrà creato solo quando confermi qui sotto.</p><div class="state-actions"><button class="button" type="submit">Crea e inizia a studiare</button><button class="button button--quiet" type="button" data-close-course-creation>Annulla</button><span class="settings-card__status" id="chat-course-creation-status" role="status"></span></div></form></section>`;
  }

  function populateComposerPrompt(prompt, control = null) {
    const value = text(prompt).trim();
    if (!value) return;
    const continuationComposer = control?.closest(".continuation") ? $("#continuation-entry-text", root) : null;
    const composer = continuationComposer || $("#session-entry-text", root) || $("#entry", root);
    if (composer) {
      composer.value = value;
      composer.dispatchEvent(new Event("input", { bubbles: true }));
      composer.focus({ preventScroll: true });
      return;
    }
    loadRoute("oggi").then(() => {
      const homeComposer = $("#entry", root);
      if (!homeComposer) return;
      homeComposer.value = value;
      homeComposer.dispatchEvent(new Event("input", { bubbles: true }));
      homeComposer.focus({ preventScroll: true });
    }).catch(() => {});
  }

  function renderSessione(payload) {
    const snapshot = object(first(payload, ["snapshot", "session", "view"], payload));
    const session = object(first(snapshot, ["session"], state.bootstrap?.session));
    const messageCandidates = array(
      first(snapshot, ["timeline", "turns", "messages", "conversation"], [])
    );
    const messages = messageCandidates.filter((message) => {
      const item = object(message);
      const role = text(first(item, ["role", "speaker", "who"], "")).toLowerCase();
      const content = text(first(item, ["text", "content", "message"], "")).trim();
      return ["assistant", "tutor", "learner", "user", "student"].includes(role) && Boolean(content);
    });
    const learnerEntry = text(first(snapshot, ["learner_entry"], ""));
    const displayMessages = learnerEntry
      ? [{ role: "learner", content: learnerEntry }, ...messages]
      : messages;
    const status = text(first(snapshot, ["shell_status", "status"], state.bootstrap?.shell_status), "ready");
    const continuation = object(first(snapshot, ["continuation", "pending_continuation"], null));
    state.continuation = Object.keys(continuation).length ? continuationSummary({ continuation }) : null;
    let lastAssistantIndex = -1;
    displayMessages.forEach((message, index) => {
      const messageRole = text(first(object(message), ["role", "speaker", "who"], "assistant"), "assistant").toLowerCase();
      if (!["learner", "user", "student"].includes(messageRole)) lastAssistantIndex = index;
    });
    const thread = displayMessages.length
      ? displayMessages.map((message, index) => renderMessage(message, index === lastAssistantIndex)).join("")
      : emptyState(
          "Nessun turno registrato",
          "La sessione non contiene ancora una conversazione canonica."
        );
    const continuationFingerprint = first(continuation, ["fingerprint", "continuation_fingerprint"], "");
    const continuationPrompt = first(continuation, ["prompt", "question", "message"], "Il tutor attende una risposta.");
    const continuationApproval = continuation && Object.keys(continuation).length
      ? aiApproval({
        title: "Conferma il prossimo passo",
        detail: "La continuazione resta sospesa finché non scegli come rispondere.",
        choices: [{ label: "Prepara la risposta", action: continuationPrompt, prompt: continuationPrompt }],
      })
      : "";
    const continuationHtml = continuation && Object.keys(continuation).length ? `<div class="continuation"><p class="section-kicker">richiesta del tutor</p><p class="continuation__prompt">${escapeAttribute(continuationPrompt)}</p>${continuationApproval}${continuationFingerprint ? entryForm("continuation-entry", "Risposta", "Scrivi la risposta…", "", `data-fingerprint="${escapeAttribute(continuationFingerprint)}"`) : emptyState("Continuazione non disponibile", "Il servizio non ha restituito il riferimento opaco necessario per riprendere.")}</div>` : "";
    const compactTranscript = messages.length
      ? aiChatPanel({
        title: "Trascrizione compatta",
        status,
        messages: displayMessages,
        composer: false,
      })
      : emptyState(
        "Trascrizione non disponibile",
        "Il DTO della sessione espone solo lo stato operativo, non turni conversazionali dichiarati."
      );
    const activityDisclosure = `<details class="ai-session-activity"><summary>Attività e trascrizione compatta</summary><div class="ai-session-activity__grid">${aiThinking({
      summary: "Trace di ragionamento non esposto",
      hint: "Cardine mostra solo attività dichiarata dal contratto.",
      steps: [{ label: "Risposta canonica disponibile", detail: "Il DTO non espone pensieri interni del modello.", status: "unavailable" }],
    })}${aiToolStack({
      title: "Attività tecnica",
      tools: [{ label: "Strumenti usati", detail: "Il servizio non ha dichiarato strumenti usati in questa conversazione.", status: "unavailable" }],
    })}${compactTranscript}</div></details>`;
    const createCourse = state.auth.authenticated
      ? `<button class="text-button" type="button" data-open-course-creation>Crea un corso</button>`
      : "";
    setView("sessione", `<section class="chat-session" data-ai-chat-ready="true" aria-labelledby="conversation-heading"><header class="conversation-header"><div><h1 id="conversation-heading">${escapeAttribute(text(first(snapshot, ["title", "topic"], object(state.bootstrap?.course).title), "Sessione di studio"))}</h1><p>${escapeAttribute(statusLabel(status))}</p></div><div class="conversation-header__actions">${createCourse}<button class="text-button" type="button" data-route="fonti">Fonti</button></div></header><div class="conversation-scroll"><div class="conversation-column"><div class="session-thread">${thread}</div>${continuationHtml}${activityDisclosure}</div></div><div class="conversation-composer-dock"><div class="conversation-column">${entryForm("session-entry", "Scrivi al tutor", "Rispondi al tutor…")}</div></div></section>`);
    updateContinuationDock(snapshot);
  }

  function renderMessage(message, showFineTune = false) {
    const item = object(message);
    const role = text(first(item, ["role", "speaker", "who"], "assistant"), "assistant").toLowerCase();
    const learner = role === "learner" || role === "user" || role === "student";
    const content = first(item, ["text", "content", "detail", "message"], "");
    const citation = object(first(item, ["citation", "provenance", "source"], null));
    if (learner) {
      return `<article class="thread-message thread-message--learner"><p class="thread-message__role">tu</p><p class="thread-message__text">${escapeAttribute(text(content, "Messaggio senza testo visualizzabile."))}</p></article>`;
    }
    const citations = Object.keys(citation).length ? [citation] : [];
    const followUps = array(first(item, ["follow_ups", "followUps", "suggestions", "actions"], []));
    const thinking = array(first(item, ["thinking", "trace", "steps", "activity"], []));
    const tools = array(first(item, ["tools", "tool_activity", "capabilities", "retrieval"], []));
    const answer = aiAnswer({ answer: text(content, "Messaggio senza testo visualizzabile."), citations, followUps, status: first(item, ["status", "state"], "ready"), reveal: showFineTune });
    const thinkingView = thinking.length ? aiThinking({ steps: thinking, summary: "Come ho costruito questa risposta" }) : "";
    const toolsView = tools.length ? aiToolStack({ tools, title: "Attività dichiarata" }) : "";
    const fineTune = showFineTune ? aiFineTune({
      title: "Continua nel modo che ti serve",
      detail: "Queste opzioni preparano solo un follow-up locale.",
      styles: [
        { label: "Più breve", prompt: "Rispondi di nuovo in modo più breve e diretto." },
        { label: "Con esempi", prompt: "Rispondi di nuovo usando esempi clinici concreti." },
        { label: "Fammi una domanda", prompt: "Fammi una domanda di richiamo attivo su questo punto." },
      ],
    }) : "";
    const legacyCitation = Object.keys(citation).length ? `<button class="provenance-chip" type="button" data-provenance='${escapeAttribute(JSON.stringify(citation))}'>fonte · ${escapeAttribute(first(citation, ["locator", "title", "revision"], "metadati disponibili"))}</button>` : "";
    return `<article class="thread-message thread-message--assistant"><p class="thread-message__role">${escapeAttribute(role === "system" ? "sistema" : "tutor")}</p>${answer}${thinkingView}${toolsView}${legacyCitation}${fineTune}</article>`;
  }

  function renderFonti(payload) {
    const materials = array(payload);
    const rows = materials.length ? materials.map((item) => renderSource(item)).join("") : emptyState("Nessuna fonte collegata", "Il catalogo del corso non ha restituito materiali disponibili.");
    const contextCards = materials.slice(0, 6).map((item) => {
      const source = object(item);
      return {
        title: first(source, ["title", "name", "label"], "Fonte senza titolo"),
        detail: first(source, ["excerpt", "quote", "description"], "Fonte indicizzata. Apri la provenienza per vederne un estratto."),
        source: first(source, ["locator", "revision", "checksum_sha256", "checksum"], "Provenienza disponibile."),
      };
    });
    const recordRows = materials.map((item) => {
      const source = object(item);
      return {
        title: first(source, ["title", "name", "label"], "Fonte senza titolo"),
        revision: first(source, ["revision", "revision_id", "version"], "non dichiarata"),
        type: first(source, ["type", "kind", "role"], "materiale"),
        chunks: first(source, ["chunk_count", "chunks", "fragment_count"], "—"),
      };
    });
    const contextView = aiContextGrid({ title: "Contesto selezionato", cards: contextCards });
    const recordsView = aiRecordsTable({ title: "Registro delle fonti", columns: [{ key: "title", label: "Fonte" }, { key: "revision", label: "Revisione" }, { key: "type", label: "Tipo" }, { key: "chunks", label: "Frammenti" }], records: recordRows });
    const filterView = aiFilterTable({ title: "Filtra i materiali", columns: [{ key: "title", label: "Fonte" }, { key: "revision", label: "Revisione" }, { key: "type", label: "Tipo" }], records: recordRows });
    const searchView = aiSidebarSearch({ placeholder: "Cerca in Cardine", shortcut: "/" });
    setView("fonti", `<section class="section-grid"><section class="section-grid__main" aria-labelledby="material-heading"><p class="section-kicker">libreria del corso</p><h1 class="section-title" id="material-heading">Fonti del corso</h1><p class="section-copy">Di ogni fonte vedi titolo, revisione, checksum e un estratto. Il testo completo resta nel repository del corso.</p><div class="ai-fonts-search">${searchView}</div><ul class="source-list">${rows}</ul><div class="ai-fonts-context">${contextView}</div><details class="ai-fonts-records"><summary>Esplora il registro delle revisioni</summary>${recordsView}${filterView}</details></section><aside class="section-grid__side"><div class="side-card"><p class="section-kicker">aggiunta fonte</p><h2 class="side-card__title">Aggiungere fonti non è ancora possibile</h2><p class="side-card__copy">Le fonti arrivano dal repository del corso. Aggiungi il file al repository e ricarica la pagina.</p></div></aside></section>`);
  }

  function renderSource(item) {
    const source = object(item);
    const title = first(source, ["title", "name", "label"], "Fonte senza titolo");
    const revision = first(source, ["revision", "revision_id", "version"], "revisione non indicata");
    const checksum = first(source, ["checksum_sha256", "checksum", "sha256"], "checksum non dichiarato");
    const type = first(source, ["type", "kind", "role"], "materiale");
    const chunks = first(source, ["chunk_count", "chunks", "fragment_count"], "—");
    return `<li class="source-row"><div><h2 class="source-row__title">${escapeAttribute(title)}</h2><p class="source-row__meta">${escapeAttribute(revision)} · <span class="checksum" title="${escapeAttribute(checksum)}">${escapeAttribute(checksum)}</span></p></div><div class="source-row__value source-row__type">${escapeAttribute(type)}</div><div class="source-row__value">${escapeAttribute(chunks)}</div><div class="source-row__button"><button class="button button--quiet" type="button" data-provenance='${escapeAttribute(JSON.stringify({ title, revision, checksum, type, excerpt: first(source, ["excerpt", "quote"], "") }))}'>Vedi provenienza</button></div></li>`;
  }

  function renderProposte(payload) {
    const proposals = array(payload);
    const rows = proposals.length ? proposals.map(renderProposal).join("") : emptyState("Nessuna proposta da decidere", "Le proposte generate non vengono considerate accettate finché non esiste una decisione esplicita.");
    const diffRows = proposals.slice(0, 12).map((item) => {
      const proposal = object(item);
      const status = text(first(proposal, ["status", "state"], "pending"), "pending");
      const revisionId = first(proposal, ["revision_id", "id"], "non dichiarata");
      return { label: first(proposal, ["kind", "origin"], "Proposta di artefatto"), before: "generata", after: `${status} · revisione ${revisionId}` };
    });
    const diffView = aiDiffTable({ title: "Confronto delle proposte", rows: diffRows, status: proposals.length ? "ready" : "neutral" });
    const approvalView = aiApproval({ title: "Decidi con calma", detail: "La decisione canonica resta nei pulsanti della singola proposta; questo follow-up serve solo a chiedere chiarimenti.", choices: [{ label: "Spiegami cosa cambia", action: "spiega proposta", prompt: "Spiegami cosa cambia nella proposta corrente" }] });
    const pendingCount = proposals.filter((item) => ["pending", "proposed"].includes(text(first(object(item), ["status", "state"], "pending")))).length;
    const recommendationView = pendingCount ? aiRecommendation({ title: "Rivedi una proposta", detail: `${pendingCount} proposte attendono una decisione esplicita.`, prompt: "Aiutami a rivedere una proposta", actionLabel: "Chiedimi un riepilogo" }) : "";
    setView("proposte", `<section class="section-grid"><section class="section-grid__main" aria-labelledby="proposal-heading"><p class="section-kicker">artifact lifecycle · decisione umana</p><h1 class="section-title" id="proposal-heading">Proposte</h1><p class="section-copy">Generato non significa approvato. Ogni decisione è legata a revisione, sequenza e request ID.</p><div class="card-list">${rows}</div><div class="ai-proposals-diff">${diffView}</div>${approvalView}${recommendationView}</section><aside class="section-grid__side"><div class="side-card"><p class="section-kicker">regola di stato</p><h2 class="side-card__title">Nessun “accetta tutto”</h2><p class="side-card__copy">Le decisioni restano individuali per mantenere provenance e idempotenza verificabili.</p></div></aside></section>`);
  }

  function renderProposal(item) {
    const proposal = object(item);
    const revisionId = first(proposal, ["revision_id", "id"], "");
    const status = text(first(proposal, ["status", "state"], "pending"), "pending");
    const title = first(proposal, ["kind", "origin"], "Proposta di artefatto");
    const provenance = object(first(proposal, ["provenance"], {}));
    const commitments = array(first(provenance, ["source_commitments"], []));
    const pending = status === "proposed" || status === "pending";
    const enrollmentStatus = text(first(proposal, ["enrollment_status"], ""), "");
    const enrollment = status === "accepted" && proposal.kind === "flashcard" && revisionId
      ? enrollmentStatus === "not_enrolled"
        ? `<button class="button button--quiet" type="button" data-command="enroll" data-revision-id="${escapeAttribute(revisionId)}">Attiva ripasso</button>`
        : enrollmentStatus && enrollmentStatus !== "enrolled"
          ? `<p class="card__meta">Ripasso: ${escapeAttribute(enrollmentStatus)}. Puoi riprovare quando il servizio è disponibile.</p>`
          : enrollmentStatus === "enrolled" ? `<p class="card__meta">Ripasso attivo · la card entrerà nella coda quando sarà dovuta.</p>` : ""
      : "";
    const actions = pending && revisionId ? `<div class="card__actions"><button class="decision-button" type="button" data-command="artifact" data-decision="accepted" data-revision-id="${escapeAttribute(revisionId)}">Accetta</button><button class="decision-button decision-button--reject" type="button" data-command="artifact" data-decision="rejected" data-revision-id="${escapeAttribute(revisionId)}">Rifiuta</button></div>` : pending ? `<p class="card__meta">Decisione non disponibile: manca l’identificativo opaco della revisione.</p>` : "";
    const enrollmentActions = enrollment ? `<div class="card__actions">${enrollment}</div>` : "";
    return `<article class="card card--strong"><div class="card__header"><h2 class="card__title">${escapeAttribute(title)}</h2>${pill(status)}</div><p class="card__body">Revisione ${escapeAttribute(revisionId || "non dichiarata")} · ${escapeAttribute(first(proposal, ["session_id"], "sessione non dichiarata"))}</p><p class="card__meta">${escapeAttribute(commitments.length)} impegni di fonte · nessun contenuto atteso esposto</p>${actions}${enrollmentActions}</article>`;
  }

  function renderVerifiche(payload) {
    const assessments = array(payload);
    const rows = assessments.length ? assessments.map(renderAssessment).join("") : emptyState("Nessuna verifica disponibile", "Il servizio assessment non ha restituito presentazioni per questa sessione.");
    setView("verifiche", `<section class="section-grid"><section class="section-grid__main" aria-labelledby="assessment-heading"><p class="section-kicker">verifiche · tentativo prima del voto</p><h1 class="section-title" id="assessment-heading">Verifiche</h1><p class="section-copy">Una risposta viene registrata prima della valutazione. Le fonti possono restare nascoste fino alla consegna.</p><div class="card-list">${rows}</div></section><aside class="section-grid__side"><div class="side-card"><p class="section-kicker">stato</p><h2 class="side-card__title">Grade separato dal tentativo</h2><p class="side-card__copy">Il browser non crea voti e non corregge risposte: invia solo comandi al servizio assessment.</p></div></aside></section>`);
  }

  function renderAssessment(item) {
    const assessment = object(item);
    const presentationId = first(assessment, ["presentation_id", "id"], "");
    const attemptId = first(assessment, ["attempt_id"], "");
    const question = first(assessment, ["question", "prompt", "title"], "Domanda senza testo");
    const format = text(first(assessment, ["format", "kind", "type"], "single_choice"), "single_choice");
    const multiple = format === "multiple_choice";
    const free = format === "free_response";
    const revisionId = first(assessment, ["revision_id"], "");
    const canAttempt = assessment.can_attempt !== false && !attemptId && Boolean(presentationId);
    const canGrade = assessment.can_grade === true && Boolean(attemptId) && !free;
    const options = array(first(assessment, ["options", "choices"], []));
    const selected = state.selectedAnswers[presentationId];
    const selectedOptions = Array.isArray(selected) ? selected : selected ? [selected] : [];
    const status = text(first(assessment, ["status", "state"], "ready"), "ready");
    const choices = options.length ? options.map((option, index) => {
      const choice = typeof option === "string" ? { value: option, label: option } : object(option);
      const value = first(choice, ["value", "id", "letter"], String(index));
      const label = first(choice, ["label", "text", "value"], value);
      const checked = selectedOptions.includes(value) ? " checked" : "";
      return `<li><label class="choice-button ${checked ? "is-selected" : ""}"><input type="${multiple ? "checkbox" : "radio"}" name="assessment-${escapeAttribute(presentationId)}" value="${escapeAttribute(value)}" data-choice="${escapeAttribute(value)}" data-presentation-id="${escapeAttribute(presentationId)}"${checked}><span class="choice-button__letter">${escapeAttribute(first(choice, ["letter"], String.fromCharCode(65 + index)))}</span><span class="choice-button__label">${escapeAttribute(label)}</span></label></li>`;
    }).join("") : emptyState("Opzioni non disponibili", "Questa domanda non propone risposte a scelta.");
    const freeControl = free && presentationId && !attemptId ? `<label class="visually-hidden" for="assessment-${escapeAttribute(presentationId)}-response">Risposta libera</label><textarea class="assessment-response" id="assessment-${escapeAttribute(presentationId)}-response" data-free-response="${escapeAttribute(presentationId)}" maxlength="${MAX_ENTRY_CHARS}" rows="4" placeholder="Scrivi la tua risposta…">${escapeAttribute(state.freeAnswers[presentationId] || "")}</textarea>` : "";
    const grade = first(assessment, ["grade", "result", "feedback"], null);
    const gradeHistory = array(first(assessment, ["grade_history"], []));
    const contests = array(first(assessment, ["contests"], []));
    const lifecycleHistory = gradeHistory.length ? `<section class="assessment-lifecycle" aria-label="Cronologia valutazioni"><h3 class="assessment-lifecycle__title">Cronologia valutazioni</h3><ol class="assessment-lifecycle__list">${gradeHistory.map((entry) => {
      const record = object(entry);
      const score = object(first(record, ["score"], {}));
      const numerator = first(score, ["numerator"], null);
      const denominator = first(score, ["denominator"], null);
      const scoreText = Number.isInteger(numerator) && Number.isInteger(denominator) ? `${numerator}/${denominator}` : "esito non dichiarato";
      const gradeId = first(record, ["grade_id"], "grade non dichiarato");
      const predecessor = first(record, ["supersedes_grade_id"], "");
      const contest = contests.find((item) => first(object(item), ["grade_id"], "") === gradeId);
      const disposition = contest ? "contestata" : record.active === true ? "attiva" : "superata";
      const contestMeta = contest ? ` · contestata ${escapeAttribute(first(object(contest), ["contested_at"], ""))}` : "";
      const predecessorMeta = predecessor ? ` · supera ${escapeAttribute(predecessor)}` : "";
      return `<li class="assessment-lifecycle__item"><span class="assessment-lifecycle__state">${escapeAttribute(disposition)}</span><span class="assessment-lifecycle__id">${escapeAttribute(gradeId)}</span><span class="assessment-lifecycle__score">${escapeAttribute(scoreText)}</span><span class="assessment-lifecycle__meta">${escapeAttribute(first(record, ["lifecycle", "status"], "stato non dichiarato"))}${predecessorMeta}${contestMeta}</span></li>`;
    }).join("")}</ol></section>` : "";
    const hasResponse = free ? Boolean(text(state.freeAnswers[presentationId]).trim()) : selectedOptions.length > 0;
    const attemptAction = canAttempt
      ? `<div class="card__actions"><button class="button" type="button" data-command="assessment-attempt" data-presentation-id="${escapeAttribute(presentationId)}" data-assessment-format="${escapeAttribute(format)}"${hasResponse ? "" : " disabled"}>Registra tentativo</button></div>`
      : canGrade
        ? `<div class="card__actions"><button class="button button--quiet" type="button" data-command="assessment-grade" data-attempt-id="${escapeAttribute(attemptId)}">Richiedi valutazione</button></div>`
      : !presentationId && revisionId
        ? `<div class="card__actions"><button class="button" type="button" data-command="assessment-present" data-revision-id="${escapeAttribute(revisionId)}">Presenta verifica</button></div>`
        : !presentationId ? `<p class="card__meta">Azioni non disponibili: manca l’identificativo della presentazione.</p>` : "";
    return `<article class="assessment-card"><div class="card__header"><p class="section-kicker">${escapeAttribute(format)}</p>${pill(status)}</div><h2 class="assessment-card__prompt">${escapeAttribute(question)}</h2>${freeControl}<fieldset class="choice-fieldset"${free || !presentationId || attemptId ? " hidden" : ""}><legend class="visually-hidden">Scegli una risposta</legend><ol class="choice-list">${choices}</ol></fieldset>${grade ? `<p class="assessment-feedback">${escapeAttribute(typeof grade === "string" ? grade : first(object(grade), ["message", "summary", "label"], "Esito disponibile."))}</p>` : ""}${lifecycleHistory}${attemptAction}</article>`;
  }

  function renderEvidenze(payload) {
    const evidence = array(payload);
    const throughSequence = first(payload, ["through_sequence"], state.highWaterSequence);
    const rows = evidence.length ? evidence.map((item) => {
      const row = object(item);
      const numerator = first(row, ["numerator"], null);
      const denominator = first(row, ["denominator"], null);
      const estimate = Number.isInteger(numerator) && Number.isInteger(denominator) ? `${numerator}/${denominator}` : first(row, ["estimate", "value", "score"], "—");
      const criterion = first(row, ["criterion", "concept", "label", "name"], "Criterio senza nome");
      const detail = first(row, ["dimension", "disposition", "status", "detail"], "evidenza canonica");
      const refs = array(first(row, ["references", "citations", "evidence"], []));
      return `<article class="evidence-row"><div class="evidence-row__estimate">${escapeAttribute(estimate)}</div><div><h2 class="evidence-row__concept">${escapeAttribute(criterion)}</h2><p class="evidence-row__detail">${escapeAttribute(detail)}</p>${refs.length ? `<div class="reference-list">${refs.map((ref) => `<button class="provenance-chip" type="button" data-provenance='${escapeAttribute(JSON.stringify(ref))}'>${escapeAttribute(first(object(ref), ["locator", "title", "revision"], typeof ref === "string" ? ref : "riferimento"))}</button>`).join("")}</div>` : ""}</div></article>`;
    }).join("") : emptyState("Nessuna evidenza proiettata", "Le proiezioni vengono ricostruite dal ledger assessment e dalle fonti disponibili.");
    const insights = evidence.slice(0, 8).map((item) => {
      const row = object(item);
      return {
        title: first(row, ["criterion", "concept", "label", "name"], "Criterio senza nome"),
        detail: first(row, ["detail", "dimension", "disposition", "status"], "Evidenza canonica disponibile."),
        source: first(row, ["through_sequence", "sequence", "projection"], throughSequence || "sequenza non dichiarata"),
      };
    });
    const insightView = aiInsightDeck({ title: "Evidenze da tenere a mente", insights });
    setView("evidenze", `<section class="section-grid"><section class="section-grid__main" aria-labelledby="evidence-heading"><p class="section-kicker">proiezione replayabile</p><h1 class="section-title" id="evidence-heading">Evidenze per criterio</h1><p class="evidence-note">Queste sono stime di evidenza e riferimenti, non una percentuale generica di padronanza.</p>${insightView}${rows}</section><aside class="section-grid__side"><div class="side-card"><p class="section-kicker">integrità</p><h2 class="side-card__title">Sequenza ${escapeAttribute(throughSequence || "—")}</h2><p class="side-card__copy">Le evidenze sono lette al high-water mark restituito dal servizio.</p></div></aside></section>`);
  }

  function renderRipasso(payload) {
    const availabilityStatus = text(first(payload, ["status"], "empty"), "empty");
    if (availabilityStatus === "not_configured" || availabilityStatus === "unavailable") {
      setView("ripasso", `<section class="section-grid"><section class="section-grid__main" aria-labelledby="review-heading"><p class="section-kicker">ripasso · coda del giorno</p><h1 class="section-title" id="review-heading">Ripasso</h1>${emptyState("Ripasso non disponibile", first(payload, ["message"], "Il ripasso programmato non è configurato."), "unavailable")}</section><aside class="section-grid__side"><div class="side-card"><p class="section-kicker">stato</p><h2 class="side-card__title">Configurazione esplicita</h2><p class="side-card__copy">Nessuna data viene calcolata nel browser; configura un adapter scheduler e riprova.</p></div></aside></section>`);
      return;
    }
    const due = array(payload);
    if (!due.length) {
      setView("ripasso", `<section class="section-grid"><section class="section-grid__main" aria-labelledby="review-heading"><p class="section-kicker">ripasso · coda del giorno</p><h1 class="section-title" id="review-heading">Ripasso</h1>${emptyState("Nessun ripasso dovuto", "Non ci sono card da ripassare oggi.")}</section><aside class="section-grid__side"><div class="side-card"><p class="section-kicker">recall</p><h2 class="side-card__title">Stato vuoto</h2><p class="side-card__copy">Un'assenza di card non viene sostituita da una coda inventata.</p></div></aside></section>`);
      return;
    }
    const firstCard = object(due[0]);
    const revisionId = first(firstCard, ["revision_id", "id"], "");
    const revealed = state.revealedReviews[revisionId] === true;
    const position = `1 / ${due.length}`;
    const ticks = due.slice(0, 24).map((_, index) => `<span class="review-progress__tick ${index === 0 ? "is-current" : ""}"></span>`).join("");
    const front = first(firstCard, ["front", "question", "prompt"], "Contenuto della card non disponibile.");
    const back = first(firstCard, ["back", "answer", "response"], "Risposta non disponibile fino alla rivelazione.");
    const citation = first(firstCard, ["citation", "provenance", "source"], null);
    const reviewActions = revealed && revisionId ? `<div class="rating-list" style="margin-top:22px">${[["again", "Ancora"], ["hard", "Difficile"], ["good", "Bene"], ["easy", "Facile"]].map(([value, label]) => `<button class="rating-button" type="button" data-command="review" data-revision-id="${escapeAttribute(revisionId)}" data-rating="${value}">${label}<span class="rating-button__next">registra decisione</span></button>`).join("")}</div>` : revealed ? emptyState("Decisione non disponibile", "Manca l’identificativo opaco della revisione.", "unavailable") : "";
    setView("ripasso", `<section class="section-grid"><section class="section-grid__main" aria-labelledby="review-heading"><p class="section-kicker">ripasso · coda del giorno</p><h1 class="section-title" id="review-heading">Ripasso</h1><div class="review-card"><div class="review-progress"><span>${escapeAttribute(position)}</span><span class="review-progress__bar">${ticks}</span></div><div class="review-card__front">${escapeAttribute(front)}</div>${revealed ? `<div class="review-card__back">${escapeAttribute(back)}</div>` : revisionId ? `<button class="button" type="button" data-reveal-review="${escapeAttribute(revisionId)}">Mostra risposta</button>` : emptyState("Card senza identificativo", "La rivelazione è sospesa finché il servizio non restituisce la revisione.", "unavailable")}${citation ? `<button class="provenance-chip" type="button" data-provenance='${escapeAttribute(JSON.stringify(citation))}'>fonte · ${escapeAttribute(first(object(citation), ["locator", "title"], typeof citation === "string" ? citation : "metadati"))}</button>` : ""}${reviewActions}</div></section><aside class="section-grid__side"><div class="side-card"><p class="section-kicker">stato</p><h2 class="side-card__title">${escapeAttribute(text(firstCard.status, "due"))}</h2><p class="side-card__copy">La stessa coda viene usata su desktop e mobile. Il browser non calcola la prossima data.</p></div></aside></section>`);
  }

  function renderPlan(payload) {
    const plan = object(payload);
    const readiness = object(first(plan, ["readiness"], plan));
    if (text(plan.status, "ready") === "unavailable") {
      setView("piano", `<section class="section-grid"><section class="section-grid__main" aria-labelledby="plan-heading"><p class="section-kicker">progresso · piano</p><h1 class="section-title" id="plan-heading">Piano verso l'esame</h1>${emptyState("Piano non disponibile", first(plan, ["message"], "Non esiste un owner canonico per questa composizione."), "unavailable")}</section><aside class="section-grid__side">${emptyState("Nessuna degradazione globale", "Le superfici disponibili restano intatte.")}</aside></section>`);
      return;
    }
    const exam = object(first(readiness, ["exam"], plan));
    const dateValue = first(exam, ["date", "exam_date"], first(readiness, ["exam_date"], "Data non configurata"));
    const days = first(exam, ["days_remaining"], first(readiness, ["days_remaining"], null));
    const goals = array(readiness.learning_goals).map((item) => `${escapeAttribute(text(first(object(item), ["value"], item)))} ${sourceRef(item)}`).join("<br>") || "Nessun obiettivo configurato";
    const styles = array(readiness.assessment_styles).map((item) => `${escapeAttribute(text(first(object(item), ["value"], item)))} ${sourceRef(item)}`).join("<br>") || "Nessuno stile configurato";
    const constraints = array(readiness.constraints);
    const blueprints = array(readiness.blueprints);
    const counts = array(readiness.artifact_counts);
    const evidence = array(readiness.evidence);
    const recall = object(readiness.recall);
    const constraintRows = constraints.map((item) => {
      const row = object(item);
      return `<li><strong>${escapeAttribute(text(row.kind, "vincolo"))}</strong>: ${escapeAttribute(text(row.value, "non dichiarato"))} ${pill(text(row.status, "active"))} ${sourceRef(row)}</li>`;
    }).join("") || "<li>Nessun vincolo learner attivo.</li>";
    const blueprintRows = blueprints.map((item) => {
      const row = object(item);
      const observations = [...array(row.observed_topics), ...array(row.observed_formats)].map((value) => text(first(object(value), ["value"], ""))).filter(Boolean);
      const limitations = array(row.limitations).map((value) => text(value)).filter(Boolean);
      return `<li><strong>Blueprint osservativo</strong> · campione ${escapeAttribute(text(row.sample_size, "—"))}${observations.length ? ` · ${escapeAttribute(observations.join(", "))}` : ""}${limitations.length ? ` · limiti: ${escapeAttribute(limitations.join(", "))}` : ""} ${sourceRef(row)}</li>`;
    }).join("") || "<li>Nessuna osservazione blueprint accettata.</li>";
    const countRows = counts.map((item) => {
      const row = object(item);
      return `<li>${escapeAttribute(text(row.kind, "artefatto"))}: ${escapeAttribute(text(row.pending, "0"))} proposte · ${escapeAttribute(text(row.accepted, "0"))} accettati ${sourceRef(row)}</li>`;
    }).join("") || "<li>Nessun artefatto canonico.</li>";
    const evidenceCopy = evidence.length ? `${evidence.length} righe di evidenza con riferimenti canonici. ${sourceRef(evidence[0])}` : "Nessuna evidenza assessment disponibile.";
    const recallCopy = recall.available ? `${escapeAttribute(text(recall.due_count, "0"))} revisioni dovute.` : "Recall non configurato.";
    const examSources = object(exam.sources);
    const planTasks = [
      ...array(readiness.learning_goals).slice(0, 8).map((item) => ({ label: `Obiettivo: ${text(first(object(item), ["value", "label"], item), "non dichiarato")}`, detail: "Obiettivo learner dichiarato.", status: "open" })),
      ...constraints.slice(0, 8).map((item) => ({ label: `Vincolo: ${text(first(object(item), ["kind", "value"], item), "non dichiarato")}`, detail: "Vincolo learner restituito dal contesto.", status: text(first(object(item), ["status", "state"], "active"), "active") })),
    ];
    if (!planTasks.length) planTasks.push({ label: "Nessun lavoro pianificato", detail: "Il servizio non ha restituito obiettivi o vincoli attivi.", status: "clear" });
    const planInsights = evidence.slice(0, 6).map((item) => {
      const row = object(item);
      return { title: first(row, ["criterion", "concept", "label", "name"], "Evidenza"), detail: first(row, ["detail", "dimension", "disposition", "status"], "Evidenza canonica disponibile."), source: first(row, ["through_sequence", "sequence"], "sequenza non dichiarata") };
    });
    if (!planInsights.length) planInsights.push({ title: "Nessuna evidenza", detail: "Il servizio non ha restituito righe assessment per questa proiezione.", source: "stato dichiarato" });
    const taskView = aiTaskList({ title: "Lavoro dichiarato", tasks: planTasks });
    const insightView = aiInsightDeck({ title: "Segnali per l'esame", insights: planInsights });
    const recommendationView = days !== null && days !== undefined
      ? aiRecommendation({ title: "Scegli il prossimo passo", detail: `Il servizio riporta ${days} giorni di calendario configurati.`, prompt: "Aiutami a scegliere un prossimo passo dal piano", actionLabel: "Chiedimi una direzione" })
      : "";
    setView("piano", `<section class="section-grid"><section class="section-grid__main" aria-labelledby="plan-heading"><p class="section-kicker">fatti attribuiti · nessuna agenda</p><h1 class="section-title" id="plan-heading">Piano verso l'esame</h1><div class="fact-grid"><div class="side-card"><p class="section-kicker">data configurata</p><h2 class="side-card__title">${escapeAttribute(text(dateValue, "Data non configurata"))}</h2><p class="side-card__copy">As of: ${escapeAttribute(text(first(readiness, ["as_of_date"], "—")))}</p>${sourceRef(examSources.configured_date)}</div><div class="side-card"><p class="section-kicker">giorni di calendario</p><h2 class="side-card__title">${escapeAttribute(days === null || days === undefined ? "non disponibile" : String(days))}</h2><p class="side-card__copy">Valore derivato dal servizio da data, conflitti e clock UTC.</p>${sourceRef(object(examSources.days_remaining).as_of_date)} ${sourceRef(object(examSources.days_remaining).configured_date)} ${sourceRef(object(examSources.days_remaining).conflict_state)}</div></div>${taskView}${insightView}${recommendationView}<p class="section-copy"><strong>Obiettivi:</strong><br>${goals}<br><strong>Stili di verifica:</strong><br>${styles}</p><h2 class="section-subtitle">Vincoli learner</h2><ul class="plain-list">${constraintRows}</ul><h2 class="section-subtitle">Blueprint e limiti</h2><ul class="plain-list">${blueprintRows}</ul><h2 class="section-subtitle">Artefatti</h2><ul class="plain-list">${countRows}</ul><p class="section-copy">${evidenceCopy} ${escapeAttribute(recallCopy)} ${sourceRef(recall)}</p></section><aside class="section-grid__side"><div class="side-card"><p class="section-kicker">limite esplicito</p><h2 class="side-card__title">Nessun punteggio o priorità.</h2><p class="side-card__copy">Questa vista riporta osservazioni, vincoli e lavoro aperto; non genera agenda, copertura, retention o readiness score.</p></div></aside></section>`);
  }

  function renderConflitti(payload) {
    const conflicts = array(payload);
    const rows = conflicts.length ? conflicts.map(renderConflict).join("") : emptyState("Nessun conflitto di contesto", "Non ci sono divergenze da risolvere fra le tue fonti.");
    setView("conflitti", `<section class="section-grid"><section class="section-grid__main" aria-labelledby="conflict-heading"><p class="section-kicker">learner context · risoluzione esplicita</p><h1 class="section-title" id="conflict-heading">Conflitti di contesto</h1><p class="section-copy">Qui compaiono solo divergenze del contesto dello studente. Un disaccordo tra fonti resta nella provenienza e non viene risolto da questa schermata.</p><div>${rows}</div></section><aside class="section-grid__side"><div class="side-card"><p class="section-kicker">regola</p><h2 class="side-card__title">Nessuna sovrascrittura per recenza.</h2><p class="side-card__copy">La scelta viene inviata al servizio StudyContext con sequenza attesa.</p></div></aside></section>`);
  }

  function renderConflict(item) {
    const conflict = object(item);
    const kind = text(first(conflict, ["kind", "type"], "context"), "context").toLowerCase();
    const title = first(conflict, ["title", "field", "label"], "Divergenza di contesto");
    const status = text(first(conflict, ["status", "state"], "conflicted"), "conflicted");
    const options = array(first(conflict, ["candidates", "options", "choices", "values"], []));
    const sourceConflict = kind.includes("source") || kind.includes("evidence");
    return `<article class="conflict-card" data-kind="${sourceConflict ? "source" : "context"}"><div class="conflict-card__heading"><h2 class="conflict-card__title">${escapeAttribute(title)}</h2>${pill(status)}</div>${sourceConflict ? `<p class="conflict-readonly">Disaccordo tra fonti: sola lettura. Serve un contratto di risoluzione della fonte separato.</p>` : options.length ? `<div class="conflict-card__values">${options.map((option) => { const value = object(option); const statementId = first(value, ["statement_id"], ""); const displayValue = first(value, ["value", "label"], "valore non dichiarato"); return statementId ? `<button class="conflict-option" type="button" data-command="context" data-conflict-kind="${escapeAttribute(first(conflict, ["kind", "type"], "context"))}" data-statement-id="${escapeAttribute(statementId)}"><span class="conflict-option__value">${escapeAttribute(displayValue)}</span><span class="conflict-option__event">scegli StatementId canonico</span></button>` : `<p class="conflict-readonly">Scelta non disponibile: manca lo StatementId canonico.</p>`; }).join("")}</div>` : `<p class="conflict-readonly">Nessuna opzione di risoluzione disponibile.</p>`}</article>`;
  }

  async function submitTurn(form, continuation = false) {
    const textarea = $("textarea", form);
    const value = text(textarea?.value).trim();
    if (state.pendingTurn || state.loading || !value || value.length > MAX_ENTRY_CHARS) return;
    const courseTitle = continuation ? null : courseCreationIntentTitle(value);
    if (state.auth.authenticated && courseTitle !== null) {
      state.continuationDraft = "";
      openChatCourseCreation(courseTitle);
      return;
    }
    const endpoint = continuation ? `/api/v1/session/continuations/${encodeURIComponent(form.dataset.fingerprint || "opaque")}/responses` : "/api/v1/session/turns";
    const payload = continuation ? { response: value } : { content: value };
    if (textarea) {
      textarea.value = "";
      resizeComposer(textarea);
    }
    state.continuationDraft = "";
    await executeCommand(endpoint, payload, form, continuation ? "sessione" : "sessione");
  }

  async function executeCommand(endpoint, payload, form, refreshRoute) {
    const commandNavigationVersion = state.navigationVersion;
    const isTutorTurn = endpoint === "/api/v1/session/turns" || endpoint.includes("/session/continuations/");
    const request = state.lastCommand && state.lastCommand.endpoint === endpoint && JSON.stringify(state.lastCommand.payload) === JSON.stringify(payload) ? state.lastCommand.requestId : requestId();
    state.lastCommand = { endpoint, payload, requestId: request, refreshRoute };
    if (isTutorTurn) {
      state.pendingTurn = { requestId: request, content: text(payload.content || payload.response) };
      renderOptimisticTurn(state.pendingTurn.content);
    }
    setBusy(true);
    setStatus(
      "working",
      "Salvataggio nel registro canonico…"
    );
    try {
      const receipt = await fetchJson(endpoint, { method: "POST", body: JSON.stringify(commandPayload(payload, request)) });
      updateSequence(first(receipt, ["high_water_sequence", "sequence"], state.highWaterSequence));
      const status = text(first(receipt, ["status", "shell_status"], "committed"), "committed");
      setStatus(status, `Comando ${statusLabel(status)}`);
      state.lastCommand = null;
      if (isTutorTurn) state.pendingTurn = null;
      if (endpoint === "/api/v1/session/turns" || endpoint.includes("/session/continuations/")) {
        state.continuationDraft = "";
        const dockEntry = $("#continuation-dock-entry");
        if (dockEntry) { dockEntry.value = ""; resizeComposer(dockEntry); }
      }
      await refreshBootstrapCounts();
      const originIsStillActive = commandNavigationVersion === state.navigationVersion;
      if (originIsStillActive && status === "demo_completed" && refreshRoute === "sessione") {
        state.route = "sessione";
        state.viewData = object(receipt.result);
        renderSessione(state.viewData);
        setStatus("recovered", "Anteprima completata · nessun dato personale salvato");
      } else if (originIsStillActive) {
        await loadRoute(refreshRoute);
      }
      const nextComposer = originIsStillActive ? $("#session-entry-text") : null;
      if (nextComposer) nextComposer.focus({ preventScroll: true });
    } catch (error) {
      if (isTutorTurn) {
        const failedContent = text(state.pendingTurn?.content);
        state.pendingTurn = null;
        removeOptimisticTurn();
        if (failedContent) restoreFailedTurnDraft(failedContent, form);
      }
      setBusy(false);
      if (error.authExpired) return;
      if (commandNavigationVersion === state.navigationVersion) {
        if (isTutorTurn || error.status === 409) await refreshBootstrapCounts();
        setStatus(
          error.status === 409 ? "stale" : "error",
          error.status === 409
            ? "Stato aggiornato: puoi riprovare"
            : isTutorTurn && MODEL_ERROR_MESSAGES[error.code]
              ? MODEL_ERROR_MESSAGES[error.code]
              : error.status === 503 && isTutorTurn
                ? "Il modello non ha prodotto una risposta verificata: controlla Impostazioni"
              : "Comando non registrato"
        );
        showCommandError(error, form);
      }
    }
    setBusy(false);
  }

  function renderOptimisticTurn(content) {
    const safeContent = escapeAttribute(content);
    const outgoing = `<article class="thread-message thread-message--learner" data-optimistic-turn><p class="thread-message__role">tu</p><p class="thread-message__text">${safeContent}</p></article>`;
    const pending = `<article class="thread-message thread-message--assistant thread-message--pending" data-optimistic-turn><p class="thread-message__role">tutor</p><p class="thread-message__text">Sto preparando una risposta basata sulle fonti del corso…</p></article>`;
    const thread = $(".session-thread", root);
    if (thread) {
      thread.insertAdjacentHTML("beforeend", outgoing + pending);
      thread.lastElementChild?.scrollIntoView({ block: "end", behavior: "smooth" });
      return;
    }
    const courseTitle = text(object(state.bootstrap?.course).title, "Sessione di studio");
    setView("sessione", `<section class="chat-session" data-ai-chat-ready="true" aria-labelledby="conversation-heading"><header class="conversation-header"><div><h1 id="conversation-heading">${escapeAttribute(courseTitle)}</h1><p>Messaggio inviato</p></div></header><div class="conversation-scroll"><div class="conversation-column"><div class="session-thread">${outgoing}${pending}</div></div></div><div class="conversation-composer-dock"><div class="conversation-column">${entryForm("session-entry", "Scrivi al tutor", "Prepara il prossimo messaggio…")}</div></div></section>`);
  }

  function removeOptimisticTurn() {
    $$('[data-optimistic-turn]', root).forEach((item) => item.remove());
  }

  function restoreFailedTurnDraft(content, originForm) {
    const origin = originForm && document.contains(originForm) ? $("textarea", originForm) : null;
    const textarea = origin || $("#session-entry-text", root) || $("#continuation-dock-entry");
    if (!textarea || text(textarea.value).trim()) return;
    textarea.value = content;
    state.continuationDraft = content;
    resizeComposer(textarea);
    textarea.focus({ preventScroll: true });
  }

  async function uploadSource(form) {
    const fileInput = $("input[type=file]", form);
    const selected = fileInput?.files?.[0] || null;
    const pasted = text($("textarea[name=content]", form)?.value);
    const status = $("[data-source-upload-status]", form);
    const titleInput = $("input[name=title]", form);
    const extension = selected ? selected.name.split(".").pop()?.toLowerCase() : "md";
    if (selected && !["txt", "md"].includes(extension || "")) {
      if (status) status.textContent = "Sono supportati solo file .txt e .md. PDF e immagini non sono ancora importabili.";
      return;
    }
    if (selected && selected.size > 196608) {
      if (status) status.textContent = "Il file supera il limite di 192 KB per questa prima importazione.";
      return;
    }
    const content = selected ? await selected.text() : pasted;
    if (!text(content).trim()) {
      if (status) status.textContent = "Scegli un file .txt/.md oppure incolla una fonte testuale.";
      return;
    }
    if (new TextEncoder().encode(content).length > 196608) {
      if (status) status.textContent = "Il testo supera il limite di 192 KB per questa prima importazione.";
      return;
    }
    const filename = selected?.name || "appunti-incollati.md";
    const title = text(titleInput?.value).trim() || filename.replace(/\.[^.]+$/, "").replace(/[-_]+/g, " ");
    const submit = $("button[type=submit]", form);
    if (submit) submit.disabled = true;
    if (status) status.textContent = "Salvo e indicizzo la fonte…";
    try {
      const receipt = await fetchJson("/api/v1/sources/upload", {
        method: "POST",
        body: JSON.stringify(commandPayload({ filename, title, content })),
      });
      updateSequence(first(receipt, ["high_water_sequence"], state.highWaterSequence));
      state.studySetup = null;
      await refreshBootstrapCounts();
      await loadRoute("oggi");
    } catch (error) {
      if (status) status.textContent = error.message;
    } finally {
      if (submit) submit.disabled = false;
    }
  }

  function saveStudySetup(form) {
    const objective = text($("[name=objective]", form)?.value).trim();
    const availableTime = text($("[name=available_time]", form)?.value).trim();
    if (!objective || !availableTime) return;
    state.studySetup = {
      objective,
      availableTime,
      examDate: text($("[name=exam_date]", form)?.value).trim(),
    };
    renderOggi(state.viewData || state.bootstrap || {});
  }

  function startSourceFirstStudy(topic) {
    if (state.pendingTurn || state.loading || !text(topic).trim()) return;
    const setup = object(state.studySetup);
    const sourceNames = array(object(state.bootstrap?.materials).items).map((item) => text(object(item).title)).filter(Boolean);
    const prompt = `Ho caricato ${sourceNames.join(", ") || "le fonti del corso"}. Il mio obiettivo è ${text(setup.objective)}; oggi ho ${text(setup.availableTime)}${setup.examDate ? ` e l'esame è il ${text(setup.examDate)}` : ""}. Vorrei iniziare da ${text(topic)}. Guidami passo per passo usando prima le fonti disponibili.`;
    executeCommand("/api/v1/session/turns", { content: prompt }, null, "sessione").catch((error) => setStatus("error", error.message));
  }

  function showCommandError(error, form) {
    if (!form) return;
    const previous = $(".command-error", form);
    if (previous) previous.remove();
    const message = document.createElement("p");
    message.className = "field-note command-error";
    message.setAttribute("role", "alert");
    message.textContent = error.status === 409 ? "La sequenza è cambiata. Ho aggiornato lo stato: puoi riprovare lo stesso comando." : error.message;
    form.append(message);
    if (state.lastCommand && !$("[data-retry-command]", form)) {
      const retry = document.createElement("button");
      retry.className = "text-button";
      retry.type = "button";
      retry.dataset.retryCommand = "true";
      retry.textContent = "Riprova esattamente";
      form.append(retry);
    }
  }

  function bindDynamicControls() {
    $$('[data-entry-form]').forEach((form) => {
      if (form.dataset.bound === "true") return;
      form.dataset.bound = "true";
      form.addEventListener("submit", (event) => { event.preventDefault(); submitTurn(form, form.id === "continuation-entry").catch((error) => showCommandError(error, form)); });
      const textarea = $("textarea", form);
      if (textarea) {
        if (form.dataset.dockForm !== undefined) textarea.value = state.continuationDraft;
        textarea.addEventListener("input", () => {
          if (form.dataset.dockForm !== undefined || form.id === "entry-form" || form.id === "hero-entry" || form.id === "session-entry" || form.id === "continuation-entry") state.continuationDraft = textarea.value;
          resizeComposer(textarea);
        });
        textarea.addEventListener("keydown", (event) => submitComposerFromKeyboard(event, form));
        resizeComposer(textarea);
      }
    });
    $$('[data-reveal-review]').forEach((control) => control.addEventListener("click", () => { state.revealedReviews[control.dataset.revealReview] = true; renderRipasso(state.viewData || {}); }));
    $$('[data-choice]').forEach((control) => control.addEventListener("change", () => {
      const card = control.closest(".assessment-card");
      const controls = $$('[data-choice]', card).filter(
        (item) => item.dataset.presentationId === control.dataset.presentationId
      );
      state.selectedAnswers[control.dataset.presentationId] = control.type === "checkbox"
        ? controls.filter((item) => item.checked).map((item) => item.value)
        : control.value;
      control.closest(".choice-button")?.classList.toggle("is-selected", control.checked);
      const submit = $('[data-command="assessment-attempt"]', card);
      if (submit) {
        submit.disabled = control.type === "checkbox"
          ? !controls.some((item) => item.checked)
          : !control.checked;
      }
    }));
    $$('[data-free-response]').forEach((control) => control.addEventListener("input", () => {
      state.freeAnswers[control.dataset.freeResponse] = control.value;
      const submit = $('[data-command="assessment-attempt"]', control.closest(".assessment-card"));
      if (submit) submit.disabled = !text(control.value).trim();
    }));
    $$('[data-command]').forEach((control) => control.addEventListener("click", () => commandFromControl(control)));
    $$('[data-provenance]').forEach((control) => control.addEventListener("click", () => openProvenance(control.dataset.provenance)));
    $$('[data-retry-route]').forEach((control) => control.addEventListener("click", () => loadRoute(control.dataset.retryRoute)));
    $$('[data-retry-command]').forEach((control) => control.addEventListener("click", () => { if (state.lastCommand) executeCommand(state.lastCommand.endpoint, state.lastCommand.payload, control.parentElement, state.lastCommand.refreshRoute); }));
  }

  function resizeComposer(textarea) {
    textarea.style.height = "auto";
    const height = Math.min(Math.max(textarea.scrollHeight, 46), 180);
    textarea.style.height = `${height}px`;
    textarea.style.overflowY = textarea.scrollHeight > 180 ? "auto" : "hidden";
    const form = textarea.closest("[data-entry-form]");
    if (form) syncComposerState(form, textarea);
  }

  function syncComposerState(form, textarea) {
    const hasValue = Boolean(text(textarea.value).trim());
    form.classList.toggle("has-value", hasValue);
    const send = $(".composer__send", form);
    if (send) send.disabled = Boolean(state.pendingTurn) || state.loading || !hasValue;
  }

  function submitComposerFromKeyboard(event, form) {
    if (event.key !== "Enter" || event.shiftKey) return;
    if (event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    const textarea = $("textarea", form);
    if (state.pendingTurn || state.loading || textarea?.disabled || !text(textarea?.value).trim()) return;
    form.requestSubmit();
  }

  function commandFromControl(control) {
    const kind = control.dataset.command;
    if (kind === "artifact") executeCommand(`/api/v1/artifacts/${encodeURIComponent(control.dataset.revisionId || "")}/decisions`, { decision: control.dataset.decision }, control.closest(".card"), "proposte");
    if (kind === "assessment-attempt") {
      const presentationId = control.dataset.presentationId || "";
      const card = control.closest(".assessment-card");
      const format = control.dataset.assessmentFormat || "single_choice";
      const response = format === "free_response"
        ? { kind: "free_response", text: text(state.freeAnswers[presentationId]).trim() }
        : format === "multiple_choice"
          ? { kind: "multiple_choice", selected_options: Array.isArray(state.selectedAnswers[presentationId]) ? state.selectedAnswers[presentationId] : [] }
          : { kind: "single_choice", selected_option: text(state.selectedAnswers[presentationId]) };
      if (
        (response.kind === "free_response" && !response.text)
        || (response.kind === "single_choice" && !response.selected_option)
        || (response.kind === "multiple_choice" && !response.selected_options.length)
      ) return;
      executeCommand(`/api/v1/assessments/${encodeURIComponent(presentationId)}/attempts`, { response }, card, "verifiche");
    }
    if (kind === "assessment-present") executeCommand(`/api/v1/assessments/${encodeURIComponent(control.dataset.revisionId || "")}/presentations`, {}, control.closest(".assessment-card"), "verifiche");
    if (kind === "assessment-grade") executeCommand(`/api/v1/assessments/${encodeURIComponent(control.dataset.attemptId || "")}/grade`, {}, control.closest(".assessment-card"), "verifiche");
    if (kind === "enroll") executeCommand(`/api/v1/recall/${encodeURIComponent(control.dataset.revisionId || "")}/enrollments`, {}, control.closest(".card"), "proposte");
    if (kind === "review") executeCommand(`/api/v1/recall/${encodeURIComponent(control.dataset.revisionId || "")}/reviews`, { rating: control.dataset.rating }, control.closest(".review-card"), "ripasso");
    if (kind === "context") executeCommand(`/api/v1/context/conflicts/${encodeURIComponent(control.dataset.conflictKind || "context")}/resolve`, { selected_statement_id: control.dataset.statementId }, control.closest(".conflict-card"), "conflitti");
  }

  function openProvenance(serialized) {
    let source = {};
    try { source = object(JSON.parse(serialized)); } catch (_) { source = {}; }
    const title = first(source, ["title", "name"], "Fonte");
    const quote = first(source, ["excerpt", "quote"], "");
    const fields = [["revisione", first(source, ["revision", "revision_id", "version"], "non dichiarata")], ["checksum", first(source, ["checksum_sha256", "checksum", "sha256"], "non dichiarato")], ["locatore", first(source, ["locator", "location"], "non dichiarato")], ["ruolo", first(source, ["role", "trust"], "non dichiarato")]];
    const quoteText = text(quote, "");
    const multilineOrCode = quoteText.includes("\n") || ["code", "source", "snippet"].includes(text(first(source, ["kind", "type", "role"], "")).toLowerCase());
    const excerpt = quoteText
      ? multilineOrCode ? aiCodeBlock({ title, code: quoteText, language: first(source, ["language", "lang"], "testo"), caption: "Estratto della fonte selezionata." }) : `<p class="drawer__quote">${escapeAttribute(quoteText)}</p>`
      : `<p class="empty-state">Nessun estratto disponibile.</p>`;
    $("#drawer-content").innerHTML = `<div class="drawer__body"><h3 class="state-title">${escapeAttribute(title)}</h3>${excerpt}<div class="provenance-meta">${fields.map(([key, value]) => `<div class="provenance-meta__row"><span class="provenance-meta__key">${escapeAttribute(key)}</span><span class="provenance-meta__value">${escapeAttribute(value)}</span></div>`).join("")}</div></div>`;
    $("#provenance-drawer").showModal();
  }

  function commandSearchEntries() {
    const routes = Object.entries(ROUTES).filter(([, config]) => !config.private || state.auth.authenticated).map(([route, config]) => ({
      label: config.heading,
      description: `Apri ${config.label}`,
      keywords: [config.label, config.heading, route],
      route,
    }));
    const prompts = [
      {
        label: "Spiegami un concetto",
        description: "Prepara una richiesta chiara nel composer.",
        keywords: ["chat", "spiega", "studio"],
        prompt: "Spiegami un concetto dalle fonti disponibili, partendo dalle basi.",
      },
      {
        label: "Interrogami",
        description: "Prepara una domanda di richiamo attivo.",
        keywords: ["quiz", "verifica", "domanda"],
        prompt: "Interrogami sulle fonti disponibili, una domanda alla volta.",
      },
      {
        label: "Crea un collegamento clinico",
        description: "Prepara un follow-up orientato al ragionamento.",
        keywords: ["clinica", "caso", "applicazione"],
        prompt: "Collega questo argomento a un caso clinico e fammi ragionare.",
      },
    ];
    if (state.auth.authenticated) {
      prompts.push({
        label: "Crea un corso",
        description: "Apri una conferma chat per un nuovo spazio di studio.",
        keywords: ["corso", "nuovo", "crea", "sessione"],
        action: "course-creation",
      });
    }
    const materials = state.route === "fonti" ? array(state.viewData).slice(0, 30).map((item) => {
      const source = object(item);
      const title = first(source, ["title", "name", "label"], "Fonte");
      return {
        label: title,
        description: `Fonte · ${first(source, ["revision", "revision_id", "version"], "revisione non indicata")}`,
        keywords: ["fonte", first(source, ["type", "kind", "role"], "materiale")],
        route: "fonti",
      };
    }) : [];
    return [...routes, ...prompts, ...materials];
  }

  async function selectCommandSearchEntry(entry) {
    const dialog = $("#command-search");
    if (dialog.open) dialog.close();
    if (entry.route) {
      await loadRoute(entry.route);
      return;
    }
    if (entry.action === "course-creation") {
      openChatCourseCreation();
      return;
    }
    if (!entry.prompt) return;
    let composer = $("textarea", root);
    if (!composer) {
      await loadRoute("oggi");
      composer = $("textarea", root);
    }
    if (!composer) return;
    composer.value = entry.prompt;
    composer.dispatchEvent(new Event("input", { bubbles: true }));
    composer.focus({ preventScroll: true });
  }

  function openCommandSearch(opener = null) {
    const dialog = $("#command-search");
    if (!dialog || typeof CardineAI.commandSearch !== "function") return;
    commandSearchReturnFocus = opener instanceof HTMLElement ? opener : document.activeElement;
    destroyCommandSearch();
    destroyCommandSearch = CardineAI.commandSearch(
      dialog,
      commandSearchEntries(),
      (entry) => { selectCommandSearchEntry(entry).catch(() => {}); }
    );
    if (!dialog.open) dialog.showModal();
    $("#command-search-input")?.focus({ preventScroll: true });
  }

  function openAccountMenu() {
    const dialog = $("#account-menu");
    const body = $("#account-menu-body");
    if (!dialog || !body) return;
    if (state.auth.authenticated) {
      body.innerHTML = `<p>Sessione privata attiva. Le impostazioni non cambiano lo stato del corso.</p><div class="state-actions"><button class="button button--quiet" type="button" data-route="impostazioni">Apri impostazioni</button><button class="button" type="button" data-auth-logout>Esci</button></div>`;
    } else {
      body.innerHTML = `<p>Accedi per aprire le impostazioni e la configurazione del modello.</p><div class="state-actions"><button class="button" type="button" data-route="login">Accedi</button></div>`;
    }
    dialog.showModal();
  }

  function bindStaticControls() {
    document.addEventListener("submit", (event) => {
      const form = event.target instanceof HTMLFormElement ? event.target : null;
      if (!form) return;
      if (form.matches("[data-auth-login]")) {
        event.preventDefault();
        login(form);
      }
      if (form.matches("[data-settings-credential]")) {
        event.preventDefault();
        replaceCredential(form);
      }
      if (form.matches("[data-workspace-select]")) {
        event.preventDefault();
        selectWorkspace(form).catch((error) => setStatus("error", error.message));
      }
      if (form.matches("[data-workspace-session]")) {
        event.preventDefault();
        startWorkspaceSession(form).catch((error) => setStatus("error", error.message));
      }
      if (form.matches("[data-workspace-course]")) {
        event.preventDefault();
        createWorkspaceCourse(form).catch((error) => setStatus("error", error.message));
      }
      if (form.matches("[data-chat-course-creation]")) {
        event.preventDefault();
        createChatCourse(form);
      }
      if (form.matches("[data-source-upload]")) {
        event.preventDefault();
        uploadSource(form);
      }
      if (form.matches("[data-study-setup]")) {
        event.preventDefault();
        saveStudySetup(form);
      }
      if (form.matches("[data-study-topic]")) {
        event.preventDefault();
        startSourceFirstStudy(text($("[name=topic]", form)?.value).trim());
      }
    });
    document.addEventListener("click", (event) => {
      const routeControl = event.target.closest("[data-route]");
      if (routeControl) {
        event.preventDefault();
        routeControl.closest("dialog")?.close();
        const route = routeControl.dataset.route;
        closeMobileRail();
        loadRoute(route);
        return;
      }
      if (event.target.closest("#rail-toggle")) {
        const rail = $("#rail");
        const open = rail.classList.toggle("is-open");
        $("#rail-toggle").setAttribute("aria-expanded", String(open));
        $("#main-content").inert = open;
        $("#rail-backdrop").tabIndex = open ? 0 : -1;
        applyRailState();
        if (open) $("#rail-collapse").focus({ preventScroll: true });
      }
      if (event.target.closest("#rail-collapse")) {
        if (window.matchMedia("(max-width: 700px)").matches) {
          closeMobileRail(true);
        } else {
          state.sidebarCollapsed = !state.sidebarCollapsed;
          applyRailState();
        }
      }
      if (event.target.closest("#rail-backdrop")) closeMobileRail(true);
      const commandSearchControl = event.target.closest("[data-open-command-search]");
      if (commandSearchControl) {
        event.preventDefault();
        closeMobileRail();
        openCommandSearch(commandSearchControl);
      }
      if (event.target.closest("[data-open-course-creation]")) {
        event.preventDefault();
        openChatCourseCreation();
        return;
      }
      if (event.target.closest("[data-close-course-creation]")) {
        event.preventDefault();
        closeChatCourseCreation();
        return;
      }
      const suggestedTopic = event.target.closest("[data-study-topic-default]");
      if (suggestedTopic) {
        event.preventDefault();
        startSourceFirstStudy(suggestedTopic.dataset.studyTopicDefault || "la prima fonte");
        return;
      }
      if (event.target.closest("#trust-mini")) $("#trust-drawer").showModal();
      if (event.target.closest("[data-open-tutor-info]")) $("#trust-drawer").showModal();
      if (event.target.closest("[data-account-control]")) openAccountMenu();
      if (event.target.closest("[data-auth-logout]")) {
        event.preventDefault();
        $("#account-menu")?.close();
        logout();
      }
      if (event.target.closest("[data-settings-remove]")) {
        event.preventDefault();
        removeCredential(event.target.closest("[data-settings-remove]"));
      }
      if (event.target.closest("[data-settings-check]")) {
        event.preventDefault();
        checkModelConnection(event.target.closest("[data-settings-check]"));
      }
      if (event.target.closest("[data-diagnostics-refresh]")) {
        event.preventDefault();
        loadDiagnostics();
      }
      if (event.target.closest("[data-close-drawer]")) event.target.closest("dialog").close();
    });
    window.addEventListener("keydown", (event) => {
      const commandSearchActivator = event.target instanceof Element
        ? event.target.closest("[data-open-command-search]")
        : null;
      if (commandSearchActivator && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        closeMobileRail();
        openCommandSearch(commandSearchActivator);
        return;
      }
      if (event.key === "Escape") {
        $$('dialog[open]').forEach((dialog) => dialog.close());
        closeMobileRail(true);
      }
      if (event.key.toLowerCase() === "o" && event.shiftKey && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        loadRoute("oggi");
      }
      const target = event.target;
      const editing = target instanceof HTMLElement && (
        target.matches("input, textarea, select")
        || target.isContentEditable
      );
      if (event.key === "/" && !editing && !event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault();
        openCommandSearch(target);
      }
    });
    $("#command-search")?.addEventListener("close", () => {
      destroyCommandSearch();
      destroyCommandSearch = () => {};
      if (commandSearchReturnFocus instanceof HTMLElement && commandSearchReturnFocus.isConnected) {
        commandSearchReturnFocus.focus({ preventScroll: true });
      }
      commandSearchReturnFocus = null;
    });
  }

  function closeMobileRail(returnFocus = false) {
    const rail = $("#rail");
    if (!rail.classList.contains("is-open")) return;
    rail.classList.remove("is-open");
    $("#rail-toggle").setAttribute("aria-expanded", "false");
    $("#main-content").inert = false;
    $("#rail-backdrop").tabIndex = -1;
    applyRailState();
    if (returnFocus) $("#rail-toggle").focus({ preventScroll: true });
  }

  function applyRailState() {
    const rail = $("#rail");
    const control = $("#rail-collapse");
    const mobile = window.matchMedia("(max-width: 700px)").matches;
    if (!mobile && rail.classList.contains("is-open")) {
      rail.classList.remove("is-open");
      $("#main-content").inert = false;
      $("#rail-backdrop").tabIndex = -1;
      $("#rail-toggle").setAttribute("aria-expanded", "false");
    }
    const mobileOpen = mobile && rail.classList.contains("is-open");
    rail.classList.toggle("is-collapsed", state.sidebarCollapsed);
    const expanded = mobile ? mobileOpen : !state.sidebarCollapsed;
    const label = mobile
      ? mobileOpen ? "Chiudi barra laterale" : "Apri barra laterale"
      : state.sidebarCollapsed ? "Espandi barra laterale" : "Comprimi barra laterale";
    control.setAttribute("aria-expanded", String(expanded));
    control.setAttribute("aria-label", label);
    control.title = label;
  }

  applyRailState();
  bindStaticControls();
  bindDynamicControls();
  window.addEventListener("resize", applyRailState);
  loadAuthSession().then((auth) => {
    if (auth.mode === "private" && !auth.authenticated) {
      renderLogin();
      return;
    }
    return loadBootstrap();
  });
}());
