/* global crypto */
(function () {
  "use strict";

  const SCHEMA_VERSION = 1;
  const MAX_ENTRY_CHARS = 4000;
  /* `label` is the one name a destination has: the rail, the command
     palette and every reference read from it, so a section can never be
     called two different things in two different places. */
  const ROUTES = Object.freeze({
    oggi: { label: "Nuova chat", heading: "Oggi", icon: "icon--plus", endpoint: "/api/v1/bootstrap" },
    sessione: { label: "Chat", heading: "Chat", icon: "icon--chat-circle", endpoint: "/api/v1/session" },
    fonti: { label: "Libreria", heading: "Libreria", icon: "icon--book-open", endpoint: "/api/v1/materials" },
    proposte: { label: "Da approvare", heading: "Da approvare", icon: "icon--note-pencil", endpoint: "/api/v1/artifacts" },
    verifiche: { label: "Verifiche", heading: "Verifiche", icon: "icon--exam", endpoint: "/api/v1/assessments" },
    percorso: { label: "Progressi", heading: "Progressi", icon: "icon--chart-line-up", endpoint: "/api/v1/student-state" },
    ripasso: { label: "Ripasso", heading: "Ripasso", icon: "icon--cards", endpoint: "/api/v1/recall/due" },
    piano: { label: "Piano", heading: "Piano d’esame", icon: "icon--calendar-blank", endpoint: "/api/v1/plan" },
    impostazioni: { label: "Impostazioni", heading: "Impostazioni", icon: "icon--gear", endpoint: "/api/v1/settings", private: true },
    login: { label: "Accedi", heading: "Accedi a Cardine", icon: "icon--gear", endpoint: null, private: true },
  });

  /* A description earns its place by adding something the title does not
     already say. "Fonti · Apri Fonti" is noise, so it does not exist. */
  const ROUTE_DESCRIPTIONS = Object.freeze({
    oggi: "Inizia una nuova conversazione con il tutor",
    sessione: "Riprendi la conversazione in corso",
    fonti: "Fonti del corso e note di studio",
    proposte: "Flashcard e note generate da rivedere",
    verifiche: "Domande da svolgere e valutazioni registrate",
    percorso: "Argomenti studiati e punti difficili",
    ripasso: "La coda di ripasso dovuta oggi",
    piano: "Data d’esame, obiettivi e lavoro aperto",
    impostazioni: "Accesso, modello e dati locali",
  });

  const CONTINUATION_ROUTES = Object.freeze(new Set(["fonti", "proposte", "verifiche", "percorso", "ripasso", "piano"]));
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
    tutor_configuration: "Configura una chiave API valida in Impostazioni prima di inviare messaggi.",
    tutor_execution_failed: "Il tutor non ha prodotto una risposta valida. Apri Diagnostica e riprova; la chiave non è necessariamente la causa.",
    tutor_internal_error: "La chat ha riscontrato un errore interno. Riprova; consulta Diagnostica se persiste.",
  });

  const STATUS_LABELS = Object.freeze({
    ready: "pronto",
    working: "in lavorazione",
    needs_learner_input: "attende una risposta",
    suspended: "sospesa",
    needs_review: "richiede revisione",
    proposed: "da approvare",
    accepted: "approvato",
    rejected: "rifiutato",
    stale: "stato da aggiornare",
    degraded: "funzionalità ridotta",
    recovered: "pronta",
    error: "non disponibile",
    queued: "in coda",
    indexing: "in indicizzazione",
    disabled: "disattivato",
  });

  const MODE_LABELS = Object.freeze({
    local_repository: "repository locale",
    public_demo: "anteprima pubblica",
    setup: "configurazione locale",
    private: "area privata",
  });


  const ARTIFACT_LABELS = Object.freeze({
    flashcard: "Flashcard",
    assessment_item: "Domande di verifica",
    exam_blueprint: "Struttura d’esame",
    study_brief: "Scheda di studio",
  });
  const ASSESSMENT_FORMAT_LABELS = Object.freeze({
    single_choice: "Scelta singola",
    multiple_choice: "Scelta multipla",
    free_response: "Risposta aperta",
  });
  const TRANSIENT_TUTOR_ERROR_CODES = Object.freeze(new Set([
    "tutor_rate_limited",
    "tutor_timeout",
    "tutor_unavailable",
    "tutor_model_unavailable",
  ]));
  const INCOMPLETE_TURN_STATUSES = Object.freeze(new Set([
    "cancelled",
    "failed",
    "interrupted",
    "budget_exhausted",
    "stopped",
    "terminated",
  ]));

  const state = {
    bootstrap: null,
    route: "oggi",
    viewData: null,
    highWaterSequence: 0,
    selectedAnswers: Object.create(null),
    freeAnswers: Object.create(null),
    revealedReviews: Object.create(null),
    review: { snapshot: null, pending: [], saving: false, error: null, scope: "" },
    lastCommand: null,
    loading: false,
    pendingTurn: null,
    navigationVersion: 0,
    sidebarCollapsed: false,
    auth: { status: "unknown", authenticated: false, mode: "local_repository", csrfToken: "", account: null },
    continuation: null,
    continuationDraft: "",
    chatCourseCreation: null,
    studySetup: null,
    lastTurn: null,
    turnActivities: Object.create(null),
    turnCommands: Object.create(null),
    answerFeedback: Object.create(null),
    authProbeUnavailable: false,
    indexingPollToken: 0,
    activityPollToken: 0,
    diagnosticTraceId: "",
    sourceViewerVersion: 0,
    lesson: { query: "", candidates: [], pin: null, answer: null },
    sourceTitles: new Map(),
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

  function activeModelLabel() {
    return text(state.bootstrap?.model?.model, "Tutor");
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

  /* Escapes for both text content and quoted attribute values. One helper,
     one honest name: the previous `escapeAttribute` was applied to text
     nodes too, which worked only by accident. */
  function esc(value) {
    return text(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function statusLabel(status) {
    return STATUS_LABELS[status] || text(status, "stato non dichiarato").replaceAll("_", " ");
  }

  function isTransientTutorError(error) {
    return Boolean(error && TRANSIENT_TUTOR_ERROR_CODES.has(text(error.code, "")));
  }

  function tone(status) {
    if (["ready", "recovered", "accepted", "committed", "clear", "completed", "graded"].includes(status)) return "positive";
    if (["working", "needs_learner_input", "suspended", "needs_review", "pending", "retryable_conflict", "generated"].includes(status)) return "warning";
    if (["error", "rejected", "conflicted", "conflicted_context", "degraded"].includes(status)) return "error";
    return "neutral";
  }

  function pill(status, label = statusLabel(status)) {
    return `<span class="status-pill" data-tone="${esc(tone(status))}">${esc(label)}</span>`;
  }

  /* Opaque service identifiers are shown as a recognisable stub. The full
     value stays one click away in the provenance sheet. */
  function shortId(value, keep = 10) {
    const normalized = text(value);
    const body = normalized.includes(":") ? normalized.slice(normalized.lastIndexOf(":") + 1) : normalized;
    return body.length > keep ? `${body.slice(0, keep)}…` : body;
  }







  function emptyState(title, copy, kind = "empty", actions = []) {
    const className = kind === "error" ? "error-state" : kind === "unavailable" ? "unavailable-state" : kind === "loading" ? "loading-state" : "empty-state";
    const controls = actions.length
      ? `<div class="state-actions">${actions.map((action) => button(action.label, action.route, action.primary ? "button" : "button button--quiet")).join("")}</div>`
      : "";
    return `<div class="${className}"><h3 class="state-title">${esc(title)}</h3><p>${esc(copy)}</p>${controls}</div>`;
  }

  function button(label, route, className = "button button--quiet") {
    return `<button class="${className}" type="button" data-route="${esc(route)}">${esc(label)}</button>`;
  }

  /* One frame for every content page: a title, an optional lede and
     actions, then a single reading column. The service's own rules live in
     the specs; the student's page carries only the student's work. */
  function page({ headingId, title, lede = "", actions = "", body = "", className = "" }) {
    return `<section class="page${className ? ` ${className}` : ""}" aria-labelledby="${esc(headingId)}"><header class="page__header"><div class="page__heading"><h1 class="page__title" id="${esc(headingId)}" tabindex="-1">${esc(title)}</h1>${lede ? `<p class="page__lede">${lede}</p>` : ""}</div>${actions ? `<div class="page__actions">${actions}</div>` : ""}</header>${body}</section>`;
  }

  function routeHeadingId(route) {
    return `${route}-route-heading`;
  }

  /* Titles the student recognises, never an opaque identifier. */
  function sourceTitle(sourceId) {
    return text(state.sourceTitles.get(text(sourceId)));
  }

  const SOURCE_KIND_LABELS = Object.freeze({
    markdown: "Testo",
    text: "Testo",
    pdf: "PDF",
    audio: "Audio",
    transcript: "Trascrizione",
    study_notes: "Note di studio",
  });

  // Named adapters keep the browser integration explicit and easy to audit.
  const aiLoading = (options) => typeof CardineAI.loading === "function" ? CardineAI.loading(options || {}) : "";
  const aiThinking = (options) => typeof CardineAI.thinking === "function" ? CardineAI.thinking(options || {}) : "";
  const aiAnswer = (options) => typeof CardineAI.answer === "function" ? CardineAI.answer(options || {}) : "";
  const aiApproval = (options) => typeof CardineAI.approval === "function" ? CardineAI.approval(options || {}) : "";
  const aiToolStack = (options) => typeof CardineAI.toolStack === "function" ? CardineAI.toolStack(options || {}) : "";
  const aiToolChips = (options) => typeof CardineAI.toolChips === "function" ? CardineAI.toolChips(options || {}) : "";
  const aiChatPanel = (options) => typeof CardineAI.chatPanel === "function" ? CardineAI.chatPanel(options || {}) : "";
  const aiCodeBlock = (options) => typeof CardineAI.codeBlock === "function" ? CardineAI.codeBlock(options || {}) : "";
  const aiFineTune = (options) => typeof CardineAI.fineTune === "function" ? CardineAI.fineTune(options || {}) : "";

  function requestId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
    return `ui-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  async function fetchJson(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
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
      error.payload = payload && typeof payload === "object" ? {
        code: declaredCode,
        commandCommitted: payload.command_committed === true,
        requestId: text(payload.request_id, ""),
        traceId: text(payload.trace_id, ""),
      } : null;
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

  /* ------------------------------------------------------------------ */
  /* Status and alerts                                                   */
  /*                                                                     */
  /* Two channels, always in step: a live region for assistive tech and  */
  /* a visible banner anchored outside #view-root. Nothing that matters  */
  /* is ever announced without also being shown.                         */
  /* ------------------------------------------------------------------ */

  const ALERT_TONES = Object.freeze({ error: "danger", stale: "warning", degraded: "warning", working: "neutral" });

  function setStatus(status, message = statusLabel(status), { alert = true } = {}) {
    const live = $("#global-status");
    if (live) live.textContent = message;
    const mini = $("#trust-mini");
    if (mini) {
      mini.dataset.status = status;
      $("#trust-mini-label").textContent = statusLabel(status);
    }
    if (!alert) return;
    if (status === "error" || status === "stale" || status === "degraded") {
      showAlert({ tone: ALERT_TONES[status], title: message });
    } else if (status === "ready" || status === "recovered" || status === "committed") {
      dismissAlert();
    }
  }

  function showAlert({ tone = "danger", title, detail = "", actions = [] }) {
    const alert = $("#global-alert");
    if (!alert) return;
    alert.dataset.tone = tone;
    $("#global-alert-title").textContent = text(title, "Qualcosa non ha funzionato");
    $("#global-alert-detail").textContent = text(detail, "");
    const slot = $("#global-alert-actions");
    slot.replaceChildren();
    actions.forEach((action) => {
      const control = document.createElement("button");
      control.type = "button";
      control.className = "button button--quiet";
      control.textContent = action.label;
      control.addEventListener("click", () => {
        dismissAlert();
        action.run();
      });
      slot.append(control);
    });
    alert.hidden = false;
  }

  function dismissAlert() {
    const alert = $("#global-alert");
    if (alert) alert.hidden = true;
  }

  /* Overlays leave the way they arrived. Every close path in the product
     goes through here, so no dialog can vanish with a cut. */
  function closeDialog(dialog) {
    if (!dialog || !dialog.open || dialog.dataset.closing !== undefined) return;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (motion) {
      dialog.close();
      return;
    }
    dialog.dataset.closing = "true";
    const finish = () => {
      delete dialog.dataset.closing;
      dialog.close();
    };
    dialog.addEventListener("animationend", finish, { once: true });
    // A missing animation must never leave a dialog stuck open.
    window.setTimeout(() => {
      if (dialog.dataset.closing !== undefined) finish();
    }, 400);
  }

  function closeOpenDialogs() {
    $$("dialog[open]").forEach(closeDialog);
  }

  function updateSequence(sequence) {
    if (typeof sequence === "number" && Number.isFinite(sequence)) state.highWaterSequence = sequence;
  }

  function setAccountControl() {
    const control = $("#account-control");
    if (!control) return;
    if (state.auth.mode === "local_repository") {
      control.hidden = true;
      return;
    }
    const name = $("#account-control-name");
    const status = $("#account-control-status");
    const avatar = $("#account-control-avatar");
    const account = object(state.auth.account);
    if (state.auth.authenticated) {
      const label = text(first(account, ["name", "email", "username"], "Account"), "Account");
      if (name) name.textContent = label;
      if (status) status.textContent = "sessione privata";
      // An avatar stands for a signed-in person. Nobody signed in, no avatar.
      if (avatar) avatar.textContent = label.slice(0, 1).toUpperCase();
    } else {
      if (name) name.textContent = "Accedi";
      if (status) status.textContent = "area privata";
      if (avatar) avatar.textContent = "";
    }
    control.hidden = false;
  }

  /* The conversation lives in Chat. Outside it, a waiting tutor is a one
     line banner with a way back — not a composer parked on every screen. */
  function updatePendingContinuation() {
    const waiting = Boolean(state.continuation && state.continuation.pending);
    if (!waiting || state.route === "sessione" || state.route === "login") return;
    if (!CONTINUATION_ROUTES.has(state.route)) return;
    showAlert({
      tone: "warning",
      title: "Il tutor attende una risposta",
      detail: text(state.continuation.prompt, ""),
      actions: [{ label: "Apri la chat", run: () => loadRoute("sessione") }],
    });
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

  function updateContinuation(payload = state.viewData || state.bootstrap || {}) {
    const summary = continuationSummary(payload);
    if (summary) {
      state.continuation = summary;
      state.lastTurn = summary.last;
    }
    setAccountControl();
    updatePendingContinuation();
  }

  async function loadAuthSession() {
    try {
      // Local repository mode deliberately does not expose the private auth
      // namespace. Discover it first so a local browser has no expected 404.
      const health = object(await fetchJson("/health"));
      const healthMode = text(first(health, ["mode", "access_mode"], ""), "");
      if (healthMode && !["private", "setup"].includes(healthMode)) {
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
    setView("login", `<section class="login-surface" aria-labelledby="login-heading"><p class="eyebrow">Cardine · area privata</p><h1 id="login-heading">Accedi a Cardine</h1><p class="section-copy">La tua area privata per lo studio locale. La sessione resta attiva solo su questo dispositivo.</p><form class="login-surface__form" id="login-form" data-auth-login><label for="login-password">Password</label><span class="password-field"><input id="login-password" name="password" type="password" autocomplete="current-password" required><button class="text-button" type="button" data-toggle-secret="login-password" aria-pressed="false">Mostra</button></span><p class="field-error" id="login-error" ${errorMessage ? "" : "hidden"} role="alert"><span class="icon icon--warning-circle" aria-hidden="true"></span><span>${esc(errorMessage)}</span></p><button class="button" type="submit">Accedi</button></form><p class="login-surface__note">La password non viene salvata nel browser.</p></section>`);
    $("#login-password")?.focus({ preventScroll: true });
  }

  function renderOwnerSetup(errorMessage = "") {
    state.route = "login";
    navActive("login");
    setView("login", `<section class="login-surface" aria-labelledby="setup-heading"><p class="eyebrow">Cardine · configurazione locale</p><h1 id="setup-heading">Proteggi questa preview</h1><p class="section-copy">Scegli una password per aprire l’area privata. La password, il suo verificatore e le chiavi API restano solo nella memoria del servizio e vengono rimossi al riavvio.</p><form class="login-surface__form" id="owner-setup-form" data-auth-setup><label for="setup-password">Password</label><span class="password-field"><input id="setup-password" name="password" type="password" autocomplete="new-password" minlength="12" required><button class="text-button" type="button" data-toggle-secret="setup-password" aria-pressed="false">Mostra</button></span><label for="setup-password-confirm">Conferma password</label><span class="password-field"><input id="setup-password-confirm" name="password_confirm" type="password" autocomplete="new-password" minlength="12" required><button class="text-button" type="button" data-toggle-secret="setup-password-confirm" aria-pressed="false">Mostra</button></span><p class="field-error" id="setup-error" ${errorMessage ? "" : "hidden"} role="alert"><span class="icon icon--warning-circle" aria-hidden="true"></span><span>${esc(errorMessage)}</span></p><button class="button" type="submit">Attiva area privata</button></form><p class="login-surface__note">Usa almeno 12 caratteri. Cardine non salva la password nel browser né nel repository.</p></section>`);
    $("#setup-password")?.focus({ preventScroll: true });
  }

  function renderSettings(payload = {}) {
    const settings = object(payload);
    const settingsMode = text(first(settings, ["mode", "access_mode"], state.auth.mode), state.auth.mode);
    const localMode = settingsMode === "local_repository";
    const account = object(first(settings, ["account", "identity", "user"], state.auth.account));
    const model = object(first(settings, ["model", "model_status"], {}));
    const modelLabel = text(first(model, ["label", "name", "model"], "GPT-5.6 Luna"), "GPT-5.6 Luna");
    const credentialStatus = model.credential_configured === true
      ? "Chiave presente nel runtime: verifica la connessione prima di iniziare la chat."
      : "Nessuna chiave API configurata";
    const accountLabel = text(first(account, ["label", "email", "name", "username"], "Account personale"), "Account personale");
    const accountCard = localMode ? "" : `<section class="settings-card" data-settings-account><h2>Account locale</h2><p>${esc(accountLabel)}</p><p>Uscire chiude questa sessione senza eliminare i dati locali.</p><div class="settings-card__actions"><button class="button button--quiet" type="button" data-auth-logout>Esci</button></div></section>`;
    const eyebrow = localMode ? "ambiente locale · impostazioni" : "area privata · impostazioni";
    const copy = localMode ? "Gestisci il modello e i dati locali." : "Gestisci accesso, dati locali e modello.";
    setView("impostazioni", `<section class="settings-surface" aria-labelledby="settings-heading"><p class="eyebrow">${eyebrow}</p><h1 id="settings-heading">Impostazioni</h1><p class="section-copy">${copy}</p>${settings.error ? `<p class="field-error" role="alert"><span class="icon icon--warning-circle" aria-hidden="true"></span>${esc(settings.error)}</p>` : ""}<div class="settings-grid">${accountCard}<section class="settings-card" data-settings-model><h2>Modello</h2><p>Modello attivo: <strong>${esc(modelLabel)}</strong>.</p><p>${esc(credentialStatus)}</p></section><section class="settings-card" data-settings-credential><h2>Chiave API</h2><p>La chiave inserita qui resta disponibile fino al riavvio del servizio. Per mantenerla, configura <code>OPENAI_API_KEY</code> nel secret store del deployment. Cardine non mostra né restituisce il valore.</p><form id="settings-model-form" data-settings-credential autocomplete="off"><label for="settings-credential">Nuova chiave API</label><span class="password-field"><input id="settings-credential" name="api_key" type="password" autocomplete="new-password" spellcheck="false" placeholder="Incolla una nuova chiave" required aria-describedby="credential-settings-help"><button class="text-button" type="button" data-toggle-secret="settings-credential" aria-pressed="false">Mostra</button></span><p class="field-note" id="credential-settings-help">Cardine non scrive il valore nello storage del browser e svuota il campo subito dopo il salvataggio.</p><div class="settings-card__actions"><button class="button" type="submit">Salva nuova chiave</button><button class="button button--danger" type="button" data-settings-remove>Rimuovi chiave temporanea</button><span class="settings-card__status" id="credential-settings-status" role="status"></span></div></form></section><section class="settings-card settings-card--diagnostics" data-settings-diagnostics><h2>Diagnostica · Decisione tutor</h2><p>Ogni turno mostra solo la decisione validata. Retention locale bounded; nessun testo, prompt, fonte, cookie, chiave o body provider.</p><div id="preview-diagnostics"><p class="field-note">Carico diagnostica locale…</p></div><div class="settings-card__actions"><button class="button button--quiet" type="button" data-diagnostics-refresh>Aggiorna diagnostica</button></div></section><section class="settings-card"><h2>Dati del corso</h2><p>I dati di studio restano nel repository locale e non vengono inclusi nelle impostazioni del browser.</p></section><section class="settings-card"><h2>Privacy</h2><p>Sessione e chiave temporanea vengono rimosse al riavvio. Cardine non salva segreti nello storage del browser.</p></section></div></section>`);
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
      enhanceSettingsSurface();
      await loadDiagnostics(navigationVersion);
    }
  }

  function enhanceSettingsSurface() {
    const modelCard = $("[data-settings-model]", root);
    const credentialCard = $("[data-settings-credential]", root);
    const settingsAvailable = object(state.viewData).settings_available !== false;
    if (!settingsAvailable && credentialCard) {
      patch(credentialCard, "<h2>Configurazione modello</h2><p>Disponibile soltanto nell’area privata della preview locale.</p>");
    }
    if (settingsAvailable && modelCard && !$("[data-settings-check]", modelCard)) {
      modelCard.insertAdjacentHTML("beforeend", `<div class="settings-card__actions"><button class="button button--quiet" type="button" data-settings-check>Verifica decisione tutor</button><span class="settings-card__status" id="model-check-status" role="status"></span></div>`);
    }
    if (!$("#workspace-card", root)) {
      const workspaceCard = document.createElement("section");
      workspaceCard.className = "settings-card";
      workspaceCard.id = "workspace-card";
      patch(workspaceCard, `<h2>Corso e sessione</h2><div id="workspace-manager"><p class="field-note">Carico corsi e sessioni disponibili…</p></div>`);
      credentialCard?.before(workspaceCard);
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
      if (manager) patch(manager, `<p class="field-error" role="alert"><span class="icon icon--warning-circle" aria-hidden="true"></span>${esc(error.message)}</p>`);
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
    if (!canLeaveReviewScope(JSON.stringify([course.value, session.value]))) return;
    await fetchJson("/api/v1/workspace/select", { method: "POST", body: JSON.stringify(workspaceCommand({ course_id: course.value, session_id: session.value })) });
    await loadBootstrap();
    await loadRoute("sessione");
  }

  async function startWorkspaceSession(form) {
    if (!canLeaveReviewScope()) return;
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
    if (!canLeaveReviewScope()) return;
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
    if (!canLeaveReviewScope()) return;
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
    const courseOptions = courses.map((course) => `<option value="${esc(text(course.id))}" ${text(course.id) === selectedCourse ? "selected" : ""}>${esc(text(course.title, text(course.id)))}</option>`).join("");
    const sessions = courses.flatMap((course) => array(course.sessions).map((item) => ({ ...object(item), course_id: text(course.id) })));
    const sessionOptions = sessions.map((session) => `<option value="${esc(text(session.id))}" data-course-id="${esc(text(session.course_id))}" ${text(session.id) === selectedSession ? "selected" : ""}>${esc(text(session.id))} · ${esc(text(session.status, "unknown"))}</option>`).join("");
    patch(manager, `<p class="field-note">Seleziona il contesto di studio attivo. Il cambio non modifica i dati canonici.</p><form class="workspace-form" data-workspace-select><label for="workspace-course">Corso</label><select id="workspace-course" name="course_id">${courseOptions || "<option value=\"\">Nessun corso</option>"}</select><label for="workspace-session">Sessione</label><select id="workspace-session" name="session_id">${sessionOptions || "<option value=\"\">Nessuna sessione</option>"}</select><div class="settings-card__actions"><button class="button" type="submit">Usa selezione</button><span class="settings-card__status" id="workspace-status" role="status"></span></div></form><form class="workspace-form workspace-form--new" data-workspace-session><label for="workspace-new-session-course">Corso</label><select id="workspace-new-session-course" name="course_id">${courseOptions || "<option value=\"\">Nessun corso</option>"}</select><label for="workspace-new-session">Nuova sessione</label><input id="workspace-new-session" name="session_id" required maxlength="160" placeholder="es. ripasso-agosto"><div class="settings-card__actions"><button class="button button--quiet" type="submit">Avvia sessione</button></div></form><form class="workspace-form workspace-form--new" data-workspace-course><p class="field-note">Crea un corso vuoto; aggiungerai le fonti dalla sezione Fonti.</p><label for="workspace-new-course-id">ID corso</label><input id="workspace-new-course-id" name="course_id" required maxlength="160"><label for="workspace-new-course-title">Titolo</label><input id="workspace-new-course-title" name="title" required maxlength="240"><label for="workspace-new-course-language">Lingua</label><input id="workspace-new-course-language" name="language" value="it" required maxlength="32"><label for="workspace-new-course-goal">Obiettivo</label><input id="workspace-new-course-goal" name="learning_goal" required maxlength="240"><div class="settings-card__actions"><button class="button button--quiet" type="submit">Crea corso</button></div></form>`);
  }

  async function logout() {
    if (!canLeaveReviewScope()) return;
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

  async function setupOwner(form) {
    const password = form.elements.namedItem("password");
    const confirmation = form.elements.namedItem("password_confirm");
    const errorNode = $("#setup-error", form);
    const submit = $('button[type="submit"]', form);
    if (!(password instanceof HTMLInputElement) || !(confirmation instanceof HTMLInputElement)) return;
    if (!password.value || password.value.length < 12 || password.value !== confirmation.value) {
      password.setAttribute("aria-invalid", "true");
      confirmation.setAttribute("aria-invalid", "true");
      if (errorNode) {
        errorNode.hidden = false;
        errorNode.textContent = password.value !== confirmation.value
          ? "Le password non coincidono."
          : "Scegli una password di almeno 12 caratteri.";
      }
      password.focus({ preventScroll: true });
      return;
    }
    if (submit) submit.disabled = true;
    try {
      await fetchJson("/api/v1/auth/setup-owner", {
        method: "POST",
        body: JSON.stringify({ password: password.value }),
      });
      const auth = await loadAuthSession();
      if (!auth.authenticated || auth.mode !== "private") throw new Error("L’area privata non è stata attivata.");
      await loadBootstrap();
    } catch (error) {
      password.setAttribute("aria-invalid", "true");
      if (errorNode) {
        errorNode.hidden = false;
        errorNode.textContent = error.message;
      }
      password.focus({ preventScroll: true });
    } finally {
      password.value = "";
      confirmation.value = "";
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
      const traces = array(payload.turn_traces).slice().reverse();
      const retention = object(payload.retention);
      const traceHtml = traces.length ? traces.map((item) => {
        const trace = object(item);
        const traceId = text(trace.trace_id, "trace non disponibile");
        const highlighted = traceId === state.diagnosticTraceId;
        const decision = object(trace.decision);
        const kind = text(decision.kind, "decision non disponibile");
        const reason = kind === "stop" && text(decision.reason, "")
          ? ` · ${esc(text(decision.reason))}`
          : "";
        const operations = array(trace.operations).map((item) => {
          const operation = object(item);
          const details = [
            text(operation.phase), text(operation.status),
            `tentativo ${text(operation.attempt)}`, `${text(operation.duration_ms, "…")} ms`,
            operation.http_status == null ? "" : `HTTP ${text(operation.http_status)}`,
            text(operation.outcome, ""), text(operation.error_code, ""), text(operation.error_kind, ""),
            text(operation.error_type, ""), text(operation.error_location, ""),
          ].filter(Boolean).map(esc).join(" · ");
          return `<li><code>${details}</code></li>`;
        }).join("");
        const omitted = Number(trace.omitted_operations) || 0;
        return `<article class="turn-trace" data-turn-trace="${esc(traceId)}" data-highlighted="${highlighted}"><header><div><strong>${esc(traceId)}</strong><span>${esc(text(trace.status, ""))}</span></div></header><p><code>${esc(kind)}</code>${reason}</p>${operations ? `<ol>${operations}</ol>` : ""}${omitted ? `<p class="field-note">Operazioni precedenti omesse: ${esc(String(omitted))}</p>` : ""}</article>`;
      }).join("") : `<p class="field-note">Nessun turno registrato in questa esecuzione.</p>`;
      patch(target, `<p class="field-note">Memoria locale: ultimi ${esc(text(retention.max_traces, "24"))} turni, inclusi i fallimenti. Payload acquisiti: no. Telemetria esterna: no.</p>${traceHtml}`);
      if (state.diagnosticTraceId) {
        target.querySelector('[data-highlighted="true"]')?.scrollIntoView({ block: "nearest" });
      }
    } catch (error) {
      patch(target, `<p class="field-note">Diagnostica non disponibile: ${esc(error.message)}</p>`);
    }
  }

  async function refreshBootstrapCounts() {
    try {
      const payload = await fetchJson("/api/v1/bootstrap");
      state.bootstrap = object(payload);
      const indexing = object(payload.indexing);
      if (["queued", "indexing"].includes(text(indexing.status))) {
        pollIndexing(null).catch(() => {});
      }
      updateSequence(first(payload, ["high_water_sequence", "sequence"], state.highWaterSequence));
      renderCourse(payload);
      updateCounts(payload);
      updateContinuation(payload);
    } catch (_) {
      // The canonical command already committed; a sidebar refresh is
      // advisory and must not turn that success into a false command error.
    }
  }

  function setBusy(busy) {
    state.loading = busy;
    $(".conversation-scroll", root)?.setAttribute("aria-busy", String(busy));
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
      patch(list, `<button class="recent-session" type="button" data-route="sessione"><span>${esc(text(first(session, ["title", "topic"], course.title), "Sessione corrente"))}</span><span class="recent-session__date"> · in corso</span></button>`);
    } else {
      $("#recent-sessions").hidden = true;
    }
    document.title = `${text(course.title, "Cardine")} · Cardine`;
    const trustSessionId = sessionId || "sessione non selezionata";
    const mode = MODE_LABELS[text(first(bootstrap, ["mode"], "local_repository"))] || "repository locale";
    const consent = object(bootstrap.provider_consent);
    const granted = consent.granted === true;
    patch($("#trust-copy"), `<p>Corso <strong>${esc(text(course.title, "non dichiarato"))}</strong>, sessione <code>${esc(trustSessionId)}</code>. Modalità: <strong>${esc(mode)}</strong>. I tuoi dati restano nel repository locale del corso; la pagina riceve solo ciò che serve a mostrarli.</p><section aria-labelledby="provider-consent-heading"><h3 id="provider-consent-heading">Uso di ${esc(activeModelLabel())}</h3><p>Consenso: <strong>${granted ? "concesso" : "non concesso"}</strong>. Nessuna richiesta al provider parte senza consenso.</p><button class="button button--quiet" type="button" data-provider-consent="${granted ? "revoke" : "grant"}">${granted ? "Revoca consenso" : "Concedi consenso"}</button><span class="settings-card__status" data-provider-consent-status role="status"></span></section>`);
    const runtime = $("#runtime-label");
    if (runtime) runtime.textContent = `ambiente locale · ${mode}`;
  }

  async function changeProviderConsent(control) {
    const action = control.dataset.providerConsent;
    if (!['grant', 'revoke'].includes(action)) return;
    const status = $("[data-provider-consent-status]");
    control.disabled = true;
    if (status) status.textContent = action === "grant" ? "Concessione…" : "Revoca…";
    try {
      const receipt = await fetchJson(`/api/v1/consent/${action}`, {
        method: "POST",
        body: JSON.stringify(commandPayload({})),
      });
      updateSequence(first(receipt, ["high_water_sequence"], state.highWaterSequence));
      await refreshBootstrapCounts();
      renderCourse(state.bootstrap);
    } catch (error) {
      if (status) status.textContent = error.message;
      control.disabled = false;
    }
  }

  /* Counts are a signal, not decoration: a zero is silence, not a badge. */
  function updateCounts(bootstrap) {
    const counts = object(bootstrap.counts);
    setCount("assessments", count(counts, ["assessments", "assessment_count"]));
    setCount("due_reviews", count(counts, ["due_reviews", "due_review_count"]));
    setCount("pending_proposals", count(counts, ["pending_proposals", "proposal_count"]));
    const features = object(bootstrap.features);
    $$("[data-route='ripasso'], [data-route='proposte'], [data-route='verifiche']").forEach((control) => {
      const route = control.dataset.route;
      const feature = route === "ripasso" ? "recall" : route === "proposte" ? "artifacts" : route === "verifiche" ? "assessments" : "assessments";
      if (features[feature] === false) control.dataset.unavailable = "true";
    });
  }

  function setCount(name, value) {
    const badge = $(`[data-count='${name}']`);
    if (!badge) return;
    const visible = value !== "—" && value !== "0";
    badge.textContent = visible ? value : "";
    badge.hidden = !visible;
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

  /* ------------------------------------------------------------------ */
  /* Rendering                                                           */
  /*                                                                     */
  /* Views are patched into the live tree, never assigned over it. That   */
  /* is what keeps scroll offsets, focus, open disclosures and in-flight  */
  /* text alive across a refresh — and it is why a message appended to a  */
  /* form is still attached to the document when the browser paints it.   */
  /* ------------------------------------------------------------------ */

  const PRESERVE_VALUE = new Set(["INPUT", "TEXTAREA"]);
  const NEAR_BOTTOM = 56;
  let conversationScroller = null;

  function nodeKey(node) {
    return node.getAttribute("data-key") || node.id || "";
  }

  function isSameNode(current, next) {
    if (current.nodeType !== next.nodeType) return false;
    if (current.nodeType !== Node.ELEMENT_NODE) return true;
    if (current.tagName !== next.tagName) return false;
    const currentKey = nodeKey(current);
    const nextKey = nodeKey(next);
    if (currentKey || nextKey) return currentKey === nextKey;
    return true;
  }

  function syncAttributes(current, next) {
    // Live activity collapses once on completion. Subsequent refreshes retain
    // the reader's disclosure choice, including opening completed traces.
    if (current.tagName === "DETAILS" && current.classList.contains("ai-tool-chips")
      && current.dataset.state === "running" && next.dataset.state !== "running") current.open = false;
    for (const attribute of Array.from(current.attributes)) {
      // A disclosure the reader opened stays open across a refresh.
      if (attribute.name === "open" && current.tagName === "DETAILS") continue;
      if (!next.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
    }
    for (const attribute of Array.from(next.attributes)) {
      if (attribute.name === "open" && current.tagName === "DETAILS") continue;
      if (current.getAttribute(attribute.name) !== attribute.value) {
        current.setAttribute(attribute.name, attribute.value);
      }
    }
  }

  function morphElement(current, next) {
    syncAttributes(current, next);
    // Never overwrite what the reader is in the middle of typing.
    if (PRESERVE_VALUE.has(current.tagName)) return;
    if (current.tagName === "SELECT") {
      const selected = current.value;
      morphChildren(current, next);
      if (Array.from(current.options).some((option) => option.value === selected)) current.value = selected;
      return;
    }
    morphChildren(current, next);
  }

  function morphChildren(current, next) {
    let existing = current.firstChild;
    let incoming = next.firstChild;
    while (incoming) {
      const following = incoming.nextSibling;
      if (!existing) {
        current.appendChild(document.importNode(incoming, true));
      } else if (isSameNode(existing, incoming)) {
        if (existing.nodeType === Node.ELEMENT_NODE) morphElement(existing, incoming);
        else if (existing.nodeValue !== incoming.nodeValue) existing.nodeValue = incoming.nodeValue;
        existing = existing.nextSibling;
      } else {
        const key = incoming.nodeType === Node.ELEMENT_NODE ? nodeKey(incoming) : "";
        let match = null;
        if (key) {
          for (let candidate = existing; candidate; candidate = candidate.nextSibling) {
            if (candidate.nodeType === Node.ELEMENT_NODE && candidate.tagName === incoming.tagName && nodeKey(candidate) === key) {
              match = candidate;
              break;
            }
          }
        }
        if (match) {
          current.insertBefore(match, existing);
          morphElement(match, incoming);
          existing = match.nextSibling;
        } else {
          current.insertBefore(document.importNode(incoming, true), existing);
        }
      }
      incoming = following;
    }
    while (existing) {
      const following = existing.nextSibling;
      current.removeChild(existing);
      existing = following;
    }
  }

  function patch(container, html) {
    const template = document.createElement("template");
    template.innerHTML = html;
    morphChildren(container, template.content);
  }

  // BeUI Message Scroller interaction pattern, implemented for Cardine's
  // dependency-free shell: reader-owned scrolling, message rail and live edge.
  function enhanceMessageScroller(container) {
    const viewport = $(".conversation-scroll", container);
    const rail = $(".message-scroller__rail", container);
    const latest = $(".message-scroller__latest", container);
    const content = viewport?.firstElementChild;
    if (!viewport || !rail || !latest || !content) return null;
    let following = atEnd();
    let frame = 0;
    let messages = [];
    let targets = [];
    let signature = "";
    let navigating = false;
    let navigationTarget = 0;
    let activeIndex = -1;

    function atEnd() {
      return viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight <= NEAR_BOTTOM;
    }

    function update() {
      frame = 0;
      const overflowing = viewport.scrollHeight > viewport.clientHeight + 1;
      const nextMessages = $$(".session-thread > .thread-message", content);
      const previews = nextMessages.map((message, index) => {
        const learner = message.classList.contains("thread-message--learner");
        const surface = $(".thread-message__text, .ai-answer__markdown", message);
        const excerpt = text(surface?.innerText || surface?.textContent).replace(/\s+/g, " ").trim().slice(0, 144);
        return { label: `${learner ? "tu" : "tutor"} · ${index + 1} di ${nextMessages.length}`, excerpt, learner };
      });
      const nextSignature = JSON.stringify(previews);
      messages = nextMessages;
      if (signature !== nextSignature) {
        signature = nextSignature;
        // Keep the focused rail control alive when output grows.
        previews.forEach((preview, index) => {
          let button = rail.children[index];
          if (!button) {
            button = document.createElement("button");
            button.type = "button";
            button.id = `message-navigation-${index}`;
            button.className = "message-scroller__tick";
            const card = document.createElement("span");
            card.className = "message-scroller__preview";
            card.setAttribute("aria-hidden", "true");
            button.appendChild(card);
            rail.appendChild(button);
          }
          button.dataset.messageIndex = String(index);
          button.dataset.sender = preview.learner ? "learner" : "assistant";
          button.setAttribute("aria-label", `Vai al messaggio di ${preview.label}: ${preview.excerpt}`);
          button.setAttribute("aria-controls", "conversation-viewport");
          button.firstElementChild.textContent = `${preview.label} — ${preview.excerpt}`;
        });
        while (rail.children.length > previews.length) rail.lastElementChild.remove();
        targets = Array.from(rail.children);
      }
      rail.hidden = !overflowing || messages.length < 2;
      latest.hidden = !overflowing || following;
      viewport.setAttribute("aria-busy", String(state.loading));
      const bounds = viewport.getBoundingClientRect();
      let active = 0;
      let distance = Infinity;
      messages.forEach((message, index) => {
        const rect = message.getBoundingClientRect();
        const delta = Math.abs(rect.top + rect.height / 2 - (bounds.top + bounds.height / 2));
        if (delta < distance) { distance = delta; active = index; }
      });
      if (viewport.scrollTop <= NEAR_BOTTOM) active = 0;
      else if (atEnd()) active = messages.length - 1;
      if (active !== activeIndex) {
        activeIndex = active;
        const tick = targets[active];
        if (tick && tick.offsetTop < rail.scrollTop) rail.scrollTop = tick.offsetTop;
        else if (tick && tick.offsetTop + tick.offsetHeight > rail.scrollTop + rail.clientHeight) {
          rail.scrollTop = tick.offsetTop + tick.offsetHeight - rail.clientHeight;
        }
      }
      targets.forEach((button, index) => {
        if (index === active) button.setAttribute("aria-current", "true");
        else button.removeAttribute("aria-current");
      });
    }

    function schedule() {
      if (!frame) frame = requestAnimationFrame(update);
    }

    function grow() {
      // Reflow may happen after rendering (answer reveal, fonts, disclosures).
      // Follow only the state recorded before the content grew.
      if (following && !navigating) viewport.scrollTop = viewport.scrollHeight;
      schedule();
    }

    function onScroll() {
      if (!navigating) following = atEnd();
      schedule();
    }

    function interrupt(event) {
      navigating = false;
      const movingBack = (event?.type === "wheel" && event.deltaY < 0)
        || ["ArrowUp", "PageUp", "Home"].includes(event?.key);
      following = movingBack ? false : atEnd();
      // Cancel an in-flight smooth jump before handing control to the reader.
      viewport.scrollTo({ top: viewport.scrollTop, behavior: "instant" });
      schedule();
    }

    function onKey(event) {
      if (event.target !== viewport) return;
      if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) interrupt(event);
    }

    function jump(event) {
      const button = event.target.closest("[data-message-index]");
      const message = button && messages[Number(button.dataset.messageIndex)];
      if (!message) return;
      following = false;
      navigating = true;
      const top = viewport.scrollTop + message.getBoundingClientRect().top - viewport.getBoundingClientRect().top - 24;
      navigationTarget = Math.max(0, Math.min(top, viewport.scrollHeight - viewport.clientHeight));
      viewport.scrollTo({ top: navigationTarget, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
      schedule();
    }

    function preview(event) {
      const button = event.target.closest("[data-message-index]");
      if (button) rail.parentElement.dataset.messagePreview = button.firstElementChild.textContent;
    }

    function dismissPreview(event) {
      if (event.type !== "keydown" || event.key === "Escape") delete rail.parentElement.dataset.messagePreview;
    }

    function onScrollEnd() {
      // An initial follow can queue scrollend just before a rail jump starts.
      // That stale event must not re-enable following during the new jump.
      if (navigating && Math.abs(viewport.scrollTop - navigationTarget) > 2) return;
      navigating = false;
      following = atEnd();
      schedule();
    }

    function resume() {
      navigating = false;
      following = true;
      viewport.scrollTo({ top: viewport.scrollHeight, behavior: "instant" });
      schedule();
    }

    // Latest is hidden after activation; move keyboard focus into the transcript.
    function returnToLatest() { resume(); viewport.focus({ preventScroll: true }); }
    viewport.addEventListener("scroll", onScroll, { passive: true });
    viewport.addEventListener("scrollend", onScrollEnd);
    viewport.addEventListener("wheel", interrupt, { passive: true });
    viewport.addEventListener("touchstart", interrupt, { passive: true });
    viewport.addEventListener("keydown", onKey);
    rail.addEventListener("click", jump);
    rail.addEventListener("pointerover", preview);
    rail.addEventListener("focusin", preview);
    rail.addEventListener("pointerleave", dismissPreview);
    rail.addEventListener("focusout", dismissPreview);
    rail.addEventListener("keydown", dismissPreview);
    latest.addEventListener("click", returnToLatest);
    const resize = new ResizeObserver(grow);
    resize.observe(content);
    resize.observe(viewport);
    const mutation = new MutationObserver(grow);
    // Streaming reveals words by toggling their visibility class without
    // changing text nodes or geometry, so observe those changes as well.
    mutation.observe(content, {
      attributes: true,
      attributeFilter: ["class"],
      childList: true,
      subtree: true,
      characterData: true,
    });
    update();
    return {
      get following() { return following; },
      resume,
      refresh: schedule,
      destroy() {
        resize.disconnect();
        mutation.disconnect();
        cancelAnimationFrame(frame);
        viewport.removeEventListener("scroll", onScroll);
        viewport.removeEventListener("scrollend", onScrollEnd);
        viewport.removeEventListener("wheel", interrupt);
        viewport.removeEventListener("touchstart", interrupt);
        viewport.removeEventListener("keydown", onKey);
        rail.removeEventListener("click", jump);
        rail.removeEventListener("pointerover", preview);
        rail.removeEventListener("focusin", preview);
        rail.removeEventListener("pointerleave", dismissPreview);
        rail.removeEventListener("focusout", dismissPreview);
        rail.removeEventListener("keydown", dismissPreview);
        latest.removeEventListener("click", returnToLatest);
      },
    };
  }

  function captureScroll() {
    const conversation = $(".conversation-scroll", root);
    return {
      view: root.scrollTop,
      conversation: conversation ? conversation.scrollTop : 0,
      pinned: conversationScroller ? conversationScroller.following : conversation
        ? conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight <= NEAR_BOTTOM
        : true,
    };
  }

  function restoreScroll(snapshot, routeChanged) {
    root.scrollTop = routeChanged ? 0 : snapshot.view;
    const conversation = $(".conversation-scroll", root);
    if (!conversation) return;
    // Follow the conversation only when the reader was already at the end;
    // otherwise leave them exactly where they had scrolled to.
    if (routeChanged || snapshot.pinned) conversation.scrollTop = conversation.scrollHeight;
    else conversation.scrollTop = snapshot.conversation;
  }

  function setView(route, html) {
    const routeChanged = state.route !== route;
    const snapshot = captureScroll();
    const activeId = document.activeElement instanceof HTMLElement ? document.activeElement.id : "";
    state.route = route;
    root.dataset.scrollOwner = route === "sessione" ? "conversation" : "view";
    navActive(route);
    destroyPrimitiveEnhancements();
    conversationScroller?.destroy();
    conversationScroller = null;
    patch(root, html);
    bindDynamicControls();
    destroyPrimitiveEnhancements = typeof CardineAI.enhance === "function"
      ? CardineAI.enhance(root, {
        populateComposer: false,
        onFollowUp: (prompt, control) => populateComposerPrompt(prompt, control),
        onFineTune: (prompt, control) => populateComposerPrompt(prompt, control),
        onRetry: retryAnswer,
        onFeedback: (feedback, control) => {
          const id = control.closest("[data-message-id]")?.dataset.messageId;
          if (id) state.answerFeedback[id] = feedback;
        },
      })
      : () => {};
    syncComposers();
    root.removeAttribute("aria-busy");
    restoreScroll(snapshot, routeChanged);
    conversationScroller = enhanceMessageScroller(root);
    if (!routeChanged) {
      const restored = activeId ? document.getElementById(activeId) : null;
      if (restored && restored !== document.activeElement) restored.focus({ preventScroll: true });
      return;
    }
    const composer = $("#entry", root) || $("#session-entry-text", root);
    if (composer && !composer.disabled) composer.focus({ preventScroll: true });
    else $("#main-content").focus({ preventScroll: true });
  }

  /* Every composer re-measures after every render, whatever put the text
     there: a restored draft, a follow-up prompt, or the reader typing. */
  function syncComposers() {
    $$("[data-entry-form] textarea", root).forEach((textarea) => resizeComposer(textarea));
  }

  function renderLoading(route) {
    if (state.route === route && root.firstElementChild) {
      root.setAttribute("aria-busy", "true");
      return;
    }
    const loading = aiLoading({ label: `Carico ${ROUTES[route]?.heading || "la sezione"}`, detail: "" },
      emptyState("Caricamento", "Un momento…", "loading"));
    setView(route, page({
      headingId: routeHeadingId(route),
      title: ROUTES[route]?.heading || "Cardine",
      body: `<div class="loading-state">${loading}</div><div class="skeleton-stack" aria-hidden="true"><span class="skeleton"></span><span class="skeleton"></span><span class="skeleton"></span></div>`,
    }));
  }

  function renderError(route, error) {
    const message = error && error.message ? error.message : "Il servizio locale non ha risposto.";
    setStatus("error", "La sezione non è disponibile");
    setView(route, page({
      headingId: routeHeadingId(route),
      title: ROUTES[route]?.heading || "Cardine",
      body: `${emptyState("Non riesco a caricare questa sezione", `${message} I tuoi dati sono al sicuro.`, "error")}<div class="state-actions"><button class="button" type="button" data-retry-route="${esc(route)}">Riprova</button>${button("Torna alla home", "oggi")}</div>`,
    }));
  }

  async function loadBootstrap() {
    const bootstrapNavigationVersion = state.navigationVersion;
    setStatus("working", "Caricamento del corso…");
    try {
      const payload = await fetchJson("/api/v1/bootstrap");
      state.bootstrap = object(payload);
      if (["queued", "indexing"].includes(text(object(payload.indexing).status))) {
        pollIndexing(null).catch(() => {});
      }
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
    const featureByRoute = { proposte: "artifacts", verifiche: "assessments", percorso: "student_state", ripasso: "recall" };
    if (state.bootstrap && object(state.bootstrap.features)[featureByRoute[route]] === false) {
      setStatus(text(state.bootstrap?.shell_status, "ready"), `${ROUTES[route].heading} · sezione non attiva`);
      if (route === "ripasso" && state.review.pending.length) {
        renderRipasso({status: "unavailable", high_water_sequence: state.highWaterSequence, items: []});
      } else {
        renderUnavailable(route);
      }
      return;
    }
    renderLoading(route);
    try {
      const payload = suppliedData || await fetchJson(ROUTES[route].endpoint);
      if (navigationVersion !== state.navigationVersion) return false;
      state.viewData = payload;
      updateContinuation(payload);
      if (route === "oggi") {
        state.bootstrap = object(payload);
        renderCourse(state.bootstrap);
        updateCounts(state.bootstrap);
      }
      if (route !== "percorso") updateSequence(first(payload, ["high_water_sequence", "sequence"], state.highWaterSequence));
      const status = text(first(payload, ["shell_status", "status"], state.bootstrap?.shell_status || "ready"), "ready");
      setStatus(status, `${ROUTES[route].heading} · ${statusLabel(status)}`);
      if (route === "oggi") renderOggi(payload);
      if (route === "sessione") renderSessione(payload);
      if (route === "fonti") renderFonti(payload);
      if (route === "proposte") renderProposte(payload);
      if (route === "verifiche") renderVerifiche(payload);
      if (route === "percorso") renderStudentState(payload);
      if (route === "ripasso") renderRipasso(payload);
      if (route === "piano") renderPlan(payload);
      return true;
    } catch (error) {
      if (navigationVersion !== state.navigationVersion) return false;
      if (error.authExpired) return false;
      renderError(route, error);
      return false;
    }
  }

  function renderUnavailable(route) {
    setView(route, page({
      headingId: routeHeadingId(route),
      title: ROUTES[route]?.heading || "Cardine",
      body: emptyState("Questa sezione non è ancora attiva", "Il corso non fornisce ancora dati per questa sezione. Il resto del corso funziona normalmente.", "unavailable", [{ label: "Apri la libreria", route: "fonti", primary: true }, { label: "Torna alla home", route: "oggi" }]),
    }));
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
    const createCourse = state.auth.authenticated
      ? `<button class="chat-home__course-action" type="button" data-open-course-creation>Crea un corso</button>`
      : "";
    setView("oggi", `<section class="chat-home" aria-labelledby="home-heading"><div class="chat-home__center"><p class="chat-home__course">${esc(text(course.title, "Il tuo corso"))}</p><h1 id="home-heading">${suspended ? "Riprendiamo da dove eravamo?" : "Come vuoi studiare oggi?"}</h1>${lessonPinAttachment()}${entryForm("hero-entry", "Scrivi al tutor", "Chiedi qualsiasi cosa sulle tue fonti…")}${renderLessonStudy()}${suspended ? `<button class="resume-chat" type="button" data-route="sessione">Riprendi la conversazione</button>` : ""}${homeAgenda(payload)}${createCourse}${renderChatCourseCreation()}</div></section>`);
  }

  /* Today's work as one quiet list: only what actually needs attention,
     each row a door to the place where that work is done. */
  function homeAgenda(payload) {
    const counts = object(payload.counts);
    const readiness = object(payload.readiness);
    const exam = object(readiness.exam);
    const due = Number(first(counts, ["due_reviews"], first(object(readiness.recall), ["due_count"], 0))) || 0;
    const pending = Number(first(counts, ["pending_proposals"], 0)) || 0;
    const days = first(exam, ["days_remaining"], null);
    const rows = [];
    if (Number.isInteger(days) && days >= 0) {
      rows.push({ route: "piano", label: days === 0 ? "L’esame è oggi" : `${days} ${days === 1 ? "giorno" : "giorni"} all’esame`, detail: text(exam.date), icon: "icon--calendar-blank" });
    }
    if (due > 0) rows.push({ route: "ripasso", label: `${due} card da ripassare`, detail: "Ripasso di oggi", icon: "icon--cards" });
    if (pending > 0) rows.push({ route: "proposte", label: `${pending} ${pending === 1 ? "proposta da approvare" : "proposte da approvare"}`, detail: "Flashcard e note generate", icon: "icon--note-pencil" });
    const pageindex = object(payload.pageindex);
    const pageindexStatus = text(pageindex.status, "empty");
    const structureNote = ["empty", "ready"].includes(pageindexStatus)
      ? ""
      : `<p class="field-note home-agenda__note" data-pageindex-status>Struttura delle lezioni: ${esc(statusLabel(pageindexStatus))}. Il testo resta ricercabile anche se la struttura è ridotta.</p>`;
    if (!rows.length) return `<p class="home-agenda__clear">Sei in pari. Fai una domanda o apri la <button class="text-button" type="button" data-route="fonti">libreria</button>.</p>${structureNote}`;
    return `<nav class="home-agenda" aria-label="Da fare oggi"><ul class="home-agenda__list">${rows.map((row) => `<li><button class="home-agenda__item" type="button" data-route="${esc(row.route)}"><span class="icon ${esc(row.icon)}" aria-hidden="true"></span><span class="home-agenda__label">${esc(row.label)}</span><span class="home-agenda__detail">${esc(row.detail)}</span></button></li>`).join("")}</ul></nav>${structureNote}`;
  }

  /* The lesson picker is a disclosure, not a second hero: the composer stays
     the first thing on the page, and this only chooses which source the chat
     is anchored to. Questions and flashcards are asked in the chat itself. */
  function renderLessonStudy() {
    const lesson = state.lesson || { query: "", candidates: [], pin: null, answer: null };
    const candidates = Array.isArray(lesson.candidates) ? lesson.candidates : [];
    const pin = lessonPin();
    const pinnedId = pin ? text(pin.revision_id) : "";
    const rows = candidates.length
      ? `<ul class="lesson-results">${candidates.map((candidate) => {
        const item = object(candidate);
        const selected = pinnedId && text(item.revision_id) === pinnedId;
        const origin = sourceTitle(item.source_id);
        return `<li class="lesson-results__item"><button class="lesson-results__pick" type="button" data-lesson-select="${esc(text(item.candidate_id))}"${selected ? ' aria-current="true"' : ""}><span class="lesson-results__title">${esc(text(item.section_title, "Lezione"))}</span>${origin ? `<span class="lesson-results__meta">${esc(origin)}</span>` : ""}</button></li>`;
      }).join("")}</ul>`
      : "";
    const empty = !candidates.length && text(lesson.query)
      ? `<p class="field-note lesson-study__empty">Nessuna lezione trovata per «${esc(text(lesson.query))}». Prova con il titolo della lezione o con un argomento.</p>`
      : "";
    const open = Boolean(candidates.length || pin || text(lesson.query));
    return `<details class="lesson-study"${open ? " open" : ""}><summary class="lesson-study__summary">Studia una lezione specifica</summary><div class="lesson-study__body"><p class="field-note">La lezione scelta resta allegata alla chat: le domande e le flashcard usano solo quella fonte.</p><form class="lesson-study__form" data-lesson-search novalidate><div class="field"><label for="lesson-query">Titolo o argomento</label><div class="lesson-study__row"><input id="lesson-query" name="query" type="search" value="${esc(text(lesson.query))}" required maxlength="512" placeholder="es. Lezione 1"><button class="button" type="submit">Cerca</button></div></div></form>${rows}${empty}</div></details>`;
  }

  function lessonPin() {
    const pin = state.lesson && state.lesson.pin;
    return pin && typeof pin === "object" ? object(pin) : null;
  }

  /* A pinned lesson is an attachment on the composer, not a second form:
     the learner asks and asks for flashcards in the chat, as usual. */
  function lessonPinAttachment() {
    const pin = lessonPin();
    if (!pin) return "";
    const origin = sourceTitle(pin.source_id);
    return `<div class="composer-attachment" aria-live="polite"><span class="composer-attachment__label">Lezione allegata</span><span class="composer-attachment__title">${esc(text(pin.section_title, "Lezione"))}</span>${origin ? `<span class="composer-attachment__meta">${esc(origin)}</span>` : ""}<button class="composer-attachment__remove" type="button" data-lesson-unpin aria-label="Rimuovi la lezione allegata" data-tooltip="Rimuovi la lezione allegata">Rimuovi</button></div>`;
  }

  /* A three-step setup shows where you are and lets you go back. The frame
     is identical on every step, so only the card content changes. */
  const SETUP_STEPS = Object.freeze(["Materiali", "Obiettivo", "Argomento"]);

  function wizardSteps(current) {
    return `<nav class="wizard-steps" aria-label="Avanzamento del setup">${SETUP_STEPS.map((label, index) => {
      const state = index + 1 < current ? "done" : index + 1 === current ? "current" : "todo";
      return `<span class="wizard-steps__step" data-state="${state}"${state === "current" ? ' aria-current="step"' : ""}>${esc(label)}</span>`;
    }).join("")}</nav>`;
  }

  function setupView(step, heading, lede, card) {
    setView("oggi", `<section class="chat-home chat-home--setup" aria-labelledby="home-heading"><div class="chat-home__center">${wizardSteps(step)}<h1 id="home-heading">${esc(heading)}</h1><p class="chat-home__lede">${lede}</p>${card}</div></section>`);
  }

  function renderSourceFirstOnboarding(course, materials) {
    const authenticated = state.auth.mode !== "private" || state.auth.authenticated;
    const uploadBody = authenticated
      ? `<form class="source-upload-form" data-source-upload novalidate><label for="source-upload-file">File PDF, testo, Markdown o audio <span class="field-optional">(facoltativo)</span></label><input id="source-upload-file" name="file" type="file" accept=".pdf,.txt,.md,.mp3,.wav,.m4a,.mp4,.ogg,.webm,.flac,.aac"><p class="field-note">I PDF devono contenere testo selezionabile. Scansioni e immagini richiedono OCR e vengono rifiutate senza salvare una fonte.</p><label for="source-upload-text">Testo della fonte <span class="field-required">obbligatorio se non carichi un file</span></label><textarea id="source-upload-text" name="content" rows="6" maxlength="196608" placeholder="Incolla appunti, programma o una lezione…"></textarea><label for="source-upload-title">Titolo <span class="field-optional">(facoltativo)</span></label><input id="source-upload-title" name="title" maxlength="240" placeholder="es. Lezione 1 · Emodinamica"><div class="state-actions"><button class="button" type="submit">Aggiungi fonte / trascrivi audio</button><span class="settings-card__status" data-source-upload-status role="status"></span></div></form>`
      : `<p class="field-note">Accedi per aggiungere fonti al corso e iniziare il setup.</p><div class="state-actions"><button class="button" type="button" data-route="login">Accedi</button></div>`;
    setupView(1, "Partiamo dai materiali.", "Prima leggiamo le fonti del corso; solo dopo sceglieremo l’argomento iniziale insieme.",
      `<section class="study-setup-card" aria-labelledby="source-setup-heading"><h2 id="source-setup-heading">Aggiungi una fonte</h2><p>Cardine usa solo le fonti salvate nel repository del corso. Puoi aggiungere una lezione alla volta.</p>${uploadBody}</section>`);
  }

  function renderStudyIntentStep(course, materials) {
    const sourceNames = materials.slice(0, 3).map((item) => text(object(item).title)).filter(Boolean).join(", ");
    setupView(2, "Ho letto le tue fonti.", "Ora impostiamo il contesto dello studio, così il primo argomento parte con il ritmo giusto.",
      `<section class="study-setup-card" aria-labelledby="intent-setup-heading"><h2 id="intent-setup-heading">Il tuo obiettivo</h2><p>${esc(String(materials.length))} ${materials.length === 1 ? "fonte è pronta" : "fonti sono pronte"}${sourceNames ? `: ${esc(sourceNames)}` : ""}. Inserisci solo ciò che serve per questa sessione.</p><form data-study-setup class="study-setup-form" novalidate><label for="study-objective">Obiettivo di studio <span class="field-required">obbligatorio</span></label><input id="study-objective" name="objective" required maxlength="240" placeholder="es. capire la fisiologia, non memorizzare a caso"><label for="study-time">Tempo disponibile oggi <span class="field-required">obbligatorio</span></label><select id="study-time" name="available_time" required><option value="10 minuti">10 minuti</option><option value="25 minuti" selected>25 minuti</option><option value="45 minuti">45 minuti</option><option value="60 minuti o più">60 minuti o più</option></select><label for="study-exam-date">Data dell’esame <span class="field-optional">(facoltativa)</span></label><input id="study-exam-date" name="exam_date" type="date"><div class="state-actions"><button class="button" type="submit">Continua</button></div></form></section>`);
  }

  function renderStudyTopicStep(course, materials) {
    const setup = object(state.studySetup);
    const source = object(materials[0]);
    const suggested = text(first(source, ["title"], "la prima fonte"));
    const sourceCount = materials.length === 1 ? "della fonte disponibile" : `delle ${materials.length} fonti disponibili`;
    setupView(3, "Scegliamo il primo argomento.", `Dalla struttura ${esc(sourceCount)} partirei da <strong>${esc(suggested)}</strong>. È una proposta, non una decisione automatica.`,
      `<section class="study-setup-card" aria-labelledby="topic-setup-heading"><h2 id="topic-setup-heading">Primo focus</h2><p>Obiettivo: ${esc(text(setup.objective))} · Tempo: ${esc(text(setup.availableTime))}${setup.examDate ? ` · Esame: ${esc(text(setup.examDate))}` : ""}</p><div class="state-actions"><button class="button" type="button" data-study-topic-default="${esc(suggested)}">Inizia da ${esc(suggested)}</button><button class="button button--quiet" type="button" data-study-setup-back>Torna indietro</button></div><form class="study-setup-form study-setup-form--priority" data-study-topic novalidate><label for="study-topic-custom">Oppure scegli un’altra priorità</label><input id="study-topic-custom" name="topic" required maxlength="240" placeholder="es. le parti più difficili per me"><div class="state-actions"><button class="button button--quiet" type="submit">Usa questa priorità</button></div></form></section>`);
  }

  function entryForm(id, label, placeholder, buttonClass = "", attributes = "") {
    const textareaId = id === "hero-entry" ? "entry" : `${id}-text`;
    const modeClass = id === "hero-entry" ? "composer--hero" : "composer--session";
    // The model chip carries no chevron: it opens an information sheet, not
    // a picker, and an affordance has to describe what actually happens.
    return `<form id="${esc(id)}" class="composer ${modeClass}" data-entry-form ${attributes}><label class="visually-hidden" for="${esc(textareaId)}">${esc(label)}</label><div class="composer__surface"><textarea id="${esc(textareaId)}" name="learner_entry" maxlength="${MAX_ENTRY_CHARS}" rows="1" required placeholder="${esc(placeholder)}" aria-describedby="${esc(textareaId)}-hint"></textarea><div class="composer__toolbar"><button class="composer__add" type="button" data-route="fonti" aria-label="Apri fonti" data-tooltip="Apri fonti"><span class="icon icon--plus" aria-hidden="true"></span></button><span class="composer__spacer"></span><span class="char-counter" aria-live="polite" hidden></span><button class="composer__mode" type="button" data-open-tutor-info aria-label="Stato del tutor e provenienza">${esc(activeModelLabel())}</button><span class="visually-hidden composer__hint" id="${esc(textareaId)}-hint">Invio invia · Maiusc + Invio va a capo</span><button class="composer__send ${buttonClass}" type="submit" aria-label="Invia messaggio" data-tooltip="Invia messaggio" disabled><span class="icon icon--arrow-up" aria-hidden="true"></span></button></div></div></form>`;
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
    return `<section class="chat-course-creation" aria-labelledby="chat-course-creation-heading"><div><p class="eyebrow">nuovo spazio di studio</p><h2 id="chat-course-creation-heading">Creiamo un corso</h2><p>Conferma i dettagli: Cardine creerà il corso, aprirà la prima sessione e la selezionerà.</p></div><form class="chat-course-creation__form" data-chat-course-creation><label for="chat-course-title">Nome del corso</label><input id="chat-course-title" name="title" value="${esc(text(draft.title))}" required maxlength="240" autocomplete="off" placeholder="es. Fisiologia umana"><label for="chat-course-language">Lingua</label><select id="chat-course-language" name="language"><option value="it" ${text(draft.language, "it") === "it" ? "selected" : ""}>Italiano</option><option value="en" ${text(draft.language) === "en" ? "selected" : ""}>English</option></select><label for="chat-course-goal">Primo obiettivo</label><input id="chat-course-goal" name="learning_goal" value="${esc(text(draft.learningGoal))}" required maxlength="240" autocomplete="off" placeholder="es. Collegare funzione e fisiopatologia"><p class="field-note">Il corso verrà creato solo quando confermi qui sotto.</p><div class="state-actions"><button class="button" type="submit">Crea e inizia a studiare</button><button class="button button--quiet" type="button" data-close-course-creation>Annulla</button><span class="settings-card__status" id="chat-course-creation-status" role="status"></span></div></form></section>`;
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
    const reconciledTraceId = text(first(payload, ["turn_trace_id", "trace_id"], ""), "");
    if (reconciledTraceId) {
      state.diagnosticTraceId = reconciledTraceId;
    }
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
    displayMessages.forEach((message, index) => {
      const item = object(message);
      const presentationId = text(first(item, ["interaction_id", "presentation_id"], ""), "");
      const remembered = object(state.turnActivities[presentationId]);
      const records = array(remembered.records);
      if (presentationId && records.length) {
        displayMessages[index] = {
          ...item,
          activity_records: records,
          activity_state: text(remembered.state, "settled"),
        };
      }
    });
    const thread = displayMessages.length
      ? displayMessages.map((message, index) => renderMessage(message, index === lastAssistantIndex)).join("")
      : emptyState(
        "Inizia la conversazione",
        "Questa sessione non ha ancora turni registrati. Scrivi qui sotto per aprirla: il tutor risponderà usando solo le fonti del corso.",
        "empty",
        [{ label: "Vedi le fonti del corso", route: "fonti" }]
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
    const continuationHtml = continuation && Object.keys(continuation).length ? `<div class="continuation"><p class="section-kicker">richiesta del tutor</p><p class="continuation__prompt">${esc(continuationPrompt)}</p>${continuationApproval}${continuationFingerprint ? entryForm("continuation-entry", "Risposta", "Scrivi la risposta…", "", `data-fingerprint="${esc(continuationFingerprint)}"`) : emptyState("Continuazione non disponibile", "Manca il riferimento necessario per riprendere la conversazione.")}</div>` : "";
    const createCourse = state.auth.authenticated
      ? `<button class="text-button" type="button" data-open-course-creation>Crea un corso</button>`
      : "";
    const tutorStatus = state.diagnosticTraceId
      ? `<button class="text-button" type="button" data-open-turn-trace="${esc(state.diagnosticTraceId)}">Stato tutor</button>`
      : "";
    setView("sessione", sessionShell({
      title: text(first(snapshot, ["title", "topic"], object(state.bootstrap?.course).title), "Sessione di studio"),
      subtitle: statusLabel(status),
      thread,
      extras: continuationHtml,
      actions: `${createCourse}${tutorStatus}<button class="text-button" type="button" data-route="fonti">Libreria</button>`,
      placeholder: "Rispondi al tutor…",
    }));
    updateContinuation(snapshot);
  }

  /* One chat shell, one definition. The optimistic turn and the committed
     session render the same markup, so they cannot drift apart or invent a
     subtitle that contradicts the real state. */
  function sessionShell({ title, subtitle, thread, extras = "", actions = "", placeholder }) {
    return `<section class="chat-session" data-ai-chat-ready="true" aria-labelledby="conversation-heading"><header class="conversation-header"><div><h1 id="conversation-heading">${esc(title)}</h1><p>${esc(subtitle)}</p></div><div class="conversation-header__actions">${actions}</div></header><div class="message-scroller"><section id="conversation-viewport" class="conversation-scroll" aria-label="Conversazione" tabindex="0"><div class="conversation-column"><div class="session-thread">${thread}</div>${extras}</div></section><nav class="message-scroller__rail" aria-label="Navigazione messaggi" hidden></nav><button class="message-scroller__latest" type="button" hidden>Vai all’ultimo messaggio <span aria-hidden="true">↓</span></button></div><div class="conversation-composer-dock"><div class="conversation-column">${lessonPinAttachment()}${entryForm("session-entry", "Scrivi al tutor", placeholder)}</div></div></section>`;
  }

  function renderMessage(message, showFineTune = false) {
    const item = object(message);
    const role = text(first(item, ["role", "speaker", "who"], "assistant"), "assistant").toLowerCase();
    const learner = role === "learner" || role === "user" || role === "student";
    const content = first(item, ["text", "content", "detail", "message"], "");
    const citation = object(first(item, ["citation", "provenance", "source"], null));
    if (learner) {
      return `<article class="thread-message thread-message--learner"><p class="thread-message__role">tu</p><p class="thread-message__text">${esc(text(content, "Messaggio senza testo visualizzabile."))}</p></article>`;
    }
    const citations = array(first(item, ["citations", "sources"], []));
    if (Object.keys(citation).length) citations.unshift(citation);
    const followUps = array(first(item, ["follow_ups", "followUps", "suggestions", "actions"], []));
    const tools = array(first(item, ["activity_records", "tools", "tool_activity", "capabilities", "retrieval"], []));
    const id = text(first(item, ["interaction_id", "presentation_id"], ""));
    const suggested = followUps.length ? followUps : showFineTune ? [
      { label: "Spiegamelo con un esempio", prompt: "Spiegamelo usando un esempio clinico concreto." },
      { label: "Fammi una domanda di richiamo", prompt: "Fammi una domanda di richiamo attivo su questo punto." },
    ] : [];
    const answer = aiAnswer({
      answer: text(content, "Messaggio senza testo visualizzabile."), citations, followUps: suggested,
      status: first(item, ["status", "state"], "ready"), chat: true,
      canRetry: Boolean(state.turnCommands[id]), feedback: state.answerFeedback[id],
    });
    const toolsView = tools.length ? `<div class="thread-message__activity">${aiToolChips({ records: tools, state: text(first(item, ["activity_state"], "settled"), "settled") })}</div>` : "";
    return `<article class="thread-message thread-message--assistant" ${id ? `data-key="message-${esc(id)}" data-message-id="${esc(id)}"` : ""}><p class="thread-message__role">${esc(role === "system" ? "sistema" : "tutor")}</p>${toolsView}${answer}</article>`;
  }

  async function retryAnswer(control) {
    if (state.pendingTurn || state.loading) return;
    const id = control.closest("[data-message-id]")?.dataset.messageId;
    const original = state.turnCommands[id];
    if (!original) return;
    // Regeneration is a new turn; a transport retry reuses its request key.
    // Keep the original lesson scope instead of using today's composer pin.
    await executeCommand(original.endpoint, original.payload, null, "sessione", requestId());
  }

  function renderFonti(payload) {
    const materials = array(payload);
    state.sourceTitles = new Map(materials.map(object).map((item) => [text(item.source_id), text(first(item, ["title", "name", "label"], ""))]));
    const rows = materials.map((item) => renderSource(item)).join("");
    const canUpload = ["local_repository", "private"].includes(text(first(state.bootstrap, ["mode"], "local_repository")));
    const upload = canUpload ? `<details class="notes-upload sources-disclosure"><summary><span class="icon icon--plus" aria-hidden="true"></span>Aggiungi fonte</summary><form data-source-upload class="source-upload-form"><div class="source-upload-form__field"><label for="notes-source-file">PDF, testo o audio</label><input id="notes-source-file" name="file" type="file" accept=".pdf,.txt,.md,.mp3,.wav,.m4a,.mp4,.ogg,.webm,.flac,.aac"></div><div class="source-upload-form__field"><label for="notes-source-title">Titolo <span class="field-optional">(facoltativo)</span></label><input id="notes-source-title" name="title" maxlength="240" placeholder="es. Lezione 3 · Emodinamica"></div><div class="source-upload-form__field"><label for="notes-source-text">Oppure incolla il testo</label><textarea id="notes-source-text" name="content" rows="4" maxlength="196608"></textarea></div><p class="field-note">I PDF devono avere testo selezionabile. Gli audio vengono trascritti e poi trasformati in note.</p><div class="state-actions"><button class="button" type="submit">Aggiungi</button></div><p class="field-note" data-source-upload-status role="status"></p></form></details>` : "";
    const catalogue = materials.length
      ? `<ul class="source-list" aria-label="Fonti disponibili">${rows}</ul>`
      : emptyState("La libreria è vuota", "Aggiungi un PDF, una sbobina o una registrazione per iniziare a studiare dalle tue fonti.");
    // A refresh of the library (for example when background indexing ends)
    // puts the same job nodes back instead of flashing a placeholder and
    // rebuilding progress the reader is watching. The next job refresh
    // reconciles them as usual.
    const keptJobs = state.route === "fonti" && $("#material-jobs") ? $$(".notes-job[data-key]", $("#material-jobs")) : [];
    const body = `<div class="library"><div class="library__index"><section class="sources-library" aria-labelledby="sources-library-heading"><header class="library__section-header"><h2 id="sources-library-heading" tabindex="-1">Fonti</h2><span class="library__count">${materials.length}</span></header>${upload}${catalogue}</section><section class="sources-notes" aria-labelledby="sources-notes-heading"><header class="library__section-header"><h2 id="sources-notes-heading">Note di studio</h2></header><div id="material-jobs" aria-label="Note di studio" aria-live="polite"><p class="field-note" data-material-jobs-status role="status">Caricamento…</p></div></section></div><aside class="materials-pane" aria-label="Lettura"><section class="materials-viewer" id="materials-viewer" aria-labelledby="materials-viewer-title"><header class="materials-viewer__header"><div><p class="eyebrow" id="materials-viewer-kind">Lettura</p><h2 id="materials-viewer-title" tabindex="-1">Nessun documento aperto</h2></div></header><div class="source-viewer__content" id="materials-viewer-content"><div class="sources-reader-empty"><span class="icon icon--book-open" aria-hidden="true"></span><h3>Apri una fonte</h3><p>Scegli «Apri» su una fonte o su una nota per leggerla qui.</p></div></div></section></aside></div>`;
    setView("fonti", page({
      headingId: "material-heading",
      title: "Libreria",
      lede: "Le fonti del corso e le note di studio generate da esse.",
      className: "sources-page page--wide",
      body,
    }));
    if (keptJobs.length) $("#material-jobs").replaceChildren(...keptJobs);
  }

  function renderSource(item) {
    const source = object(item);
    const title = first(source, ["title", "name", "label"], "Fonte senza titolo");
    const revision = first(source, ["revision", "revision_id", "version"], "non indicata");
    const checksum = first(source, ["checksum_sha256", "checksum", "sha256"], "non dichiarato");
    const type = text(first(source, ["type", "kind", "role"], "materiale"));
    const chunks = first(source, ["chunk_count", "chunks", "fragment_count"], "—");
    const viewer = object(source.viewer);
    const viewerKind = text(viewer.kind);
    const viewerReference = viewerKind && viewerKind !== "unavailable"
      ? {
        source_id: text(source.source_id),
        revision_id: text(first(source, ["revision_id", "revision"])),
        viewer_kind: viewerKind,
        page: null,
      }
      : null;
    // Identifiers and checksums live in the details sheet, one click away,
    // never in the row the student scans to find a lesson.
    const provenance = esc(JSON.stringify({
      title, revision, checksum, type, fragments: String(chunks),
      source_role: first(source, ["source_role", "role"], "non dichiarato"),
      trust_level: first(source, ["trust_level", "trust"], "non dichiarato"),
      excerpt: first(source, ["excerpt", "quote"], ""),
    }));
    const kindLabel = SOURCE_KIND_LABELS[viewerKind] || SOURCE_KIND_LABELS[type] || "Documento";
    let sourceAction = viewerReference
      ? `<button class="button button--quiet button--sm" type="button" aria-pressed="false" data-source-viewer-mode="page" data-source-viewer='${esc(JSON.stringify({ ...viewerReference, title }))}'>Apri</button>`
      : "";
    if (source.can_generate_notes && ["local_repository", "private"].includes(text(first(state.bootstrap, ["mode"], "local_repository")))) {
      sourceAction += `<button class="button button--quiet button--sm" type="button" data-generate-notes='${esc(JSON.stringify({source_id: source.source_id, revision_id: source.revision_id}))}'>Genera note</button>`;
    }
    return `<li class="source-row" data-source-id="${esc(text(source.source_id))}" data-revision-id="${esc(text(source.revision_id))}"><div class="source-row__document"><span class="source-row__icon icon icon--book-open" aria-hidden="true"></span><div class="source-row__text"><h3 class="source-row__title">${esc(title)}</h3><p class="source-row__meta">${esc(kindLabel)}</p></div></div><div class="source-row__button">${sourceAction}<button class="source-row__provenance" type="button" data-provenance='${provenance}' aria-label="Dettagli di ${esc(title)}">Dettagli</button></div></li>`;
  }

  function renderProposte(payload) {
    const proposals = array(payload);
    const isPending = (item) => ["pending", "proposed"].includes(text(first(object(item), ["status", "state"], "pending"), "pending"));
    const pending = proposals.filter(isPending);
    const decided = proposals.filter((item) => !isPending(item));
    const bulkCount = pending.filter((item) => object(item).reviewable === true).length;
    const rows = pending.length
      ? pending.map((item) => renderProposal(item, bulkCount > 1)).join("")
      : emptyState("Niente da approvare", "Quando il tutor genera flashcard o note, le trovi qui prima che entrino nel tuo studio.");
    const decidedView = decided.length ? `<details class="decided-proposals"><summary>Già decise (${decided.length})</summary><div class="card-list">${decided.map((item) => renderProposal(item)).join("")}</div></details>` : "";
    const bulkView = bulkCount > 1 ? `<form class="bulk-decisions" data-artifact-bulk novalidate><p class="field-note">Seleziona più flashcard e applica le decisioni insieme, in un'unica operazione atomica.</p><button class="button button--quiet" type="submit">Applica alle selezionate (<span data-bulk-count>${bulkCount}</span> disponibili)</button></form>` : "";
    setView("proposte", page({
      headingId: "proposal-heading",
      title: "Da approvare",
      lede: "Ciò che il tutor genera entra nel tuo studio solo dopo la tua approvazione.",
      body: `${bulkView}<div class="card-list">${rows}</div>${decidedView}`,
    }));
  }

  function renderProposal(item, bulk = false) {
    const proposal = object(item);
    const revisionId = first(proposal, ["revision_id", "id"], "");
    const status = text(first(proposal, ["status", "state"], "pending"), "pending");
    const kind = text(first(proposal, ["kind", "origin"], ""));
    const title = ARTIFACT_LABELS[kind] || "Proposta";
    const provenance = object(first(proposal, ["provenance"], {}));
    const commitments = array(first(provenance, ["source_commitments"], []));
    const pending = status === "proposed" || status === "pending";
    const review = object(first(proposal, ["review"], {}));
    const reviewable = text(review.status, "unavailable") === "ready" && proposal.kind === "flashcard";
    const reviewContent = reviewable
      ? `<div class="flashcard-review"><p class="flashcard-review__prompt">${esc(text(review.prompt))}</p>${array(review.answer_blocks).map((block) => { const value = object(block); const points = array(value.key_points); return `<section class="flashcard-review__answer"><h3>${esc(text(value.label))}</h3><p>${esc(text(value.text))}</p>${points.length ? `<ul>${points.map((point) => `<li>${esc(text(point))}</li>`).join("")}</ul>` : ""}</section>`; }).join("")}</div>`
      : proposal.kind === "flashcard" ? `<p class="card__meta">Il contenuto di questa flashcard non è leggibile, quindi non può essere approvata.</p>` : "";
    const bulkControl = bulk && pending && reviewable && revisionId
      ? `<label class="bulk-pick"><input type="checkbox" data-bulk-revision="${esc(revisionId)}"> Seleziona <select data-bulk-decision="${esc(revisionId)}" aria-label="Decisione per questa flashcard"><option value="accepted">Accetta</option><option value="rejected">Rifiuta</option></select></label>`
      : "";
    const enrollmentStatus = text(first(proposal, ["enrollment_status"], ""), "");
    const enrollment = status === "accepted" && reviewable && revisionId
      ? enrollmentStatus === "not_enrolled"
        ? `<button class="button button--quiet" type="button" data-command="enroll" data-revision-id="${esc(revisionId)}">Aggiungi al ripasso</button>`
        : enrollmentStatus && enrollmentStatus !== "enrolled"
          ? `<p class="card__meta">Ripasso non attivato (${esc(enrollmentStatus)}). Riprova più tardi.</p>`
          : enrollmentStatus === "enrolled" ? `<p class="card__meta">Nel ripasso: comparirà quando sarà il momento di rivederla.</p>` : ""
      : "";
    const actions = pending && reviewable && revisionId ? `<div class="card__actions"><button class="decision-button" type="button" data-command="artifact" data-decision="accepted" data-revision-id="${esc(revisionId)}">Accetta</button><button class="decision-button decision-button--reject" type="button" data-command="artifact" data-decision="rejected" data-revision-id="${esc(revisionId)}">Rifiuta</button>${bulkControl}</div>` : pending && !reviewable ? "" : pending ? `<p class="card__meta">Decisione non disponibile per questa proposta.</p>` : "";
    const enrollmentActions = enrollment ? `<div class="card__actions">${enrollment}</div>` : "";
    const details = esc(JSON.stringify({ title, revision: revisionId || "non dichiarata", session: first(proposal, ["session_id"], "non dichiarata"), source_commitments: String(commitments.length) }));
    return `<article class="card proposal-card"><div class="card__header"><h2 class="card__title">${esc(title)}</h2><div class="card__header-meta">${pill(status)}<button class="text-button" type="button" data-provenance='${details}'>Dettagli</button></div></div>${reviewContent}${actions}${enrollmentActions}</article>`;
  }

  function renderVerifiche(payload) {
    const assessments = array(payload);
    const rows = assessments.length ? assessments.map(renderAssessment).join("") : emptyState("Nessuna verifica", "Le domande di verifica approvate compariranno qui.");
    setView("verifiche", page({
      headingId: "assessment-heading",
      title: "Verifiche",
      lede: "Rispondi prima, poi chiedi la valutazione.",
      body: `<div class="card-list">${rows}</div>`,
    }));
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
      return `<li><label class="choice-button ${checked ? "is-selected" : ""}"><input type="${multiple ? "checkbox" : "radio"}" name="assessment-${esc(presentationId)}" value="${esc(value)}" data-choice="${esc(value)}" data-presentation-id="${esc(presentationId)}"${checked}><span class="choice-button__letter">${esc(first(choice, ["letter"], String.fromCharCode(65 + index)))}</span><span class="choice-button__label">${esc(label)}</span></label></li>`;
    }).join("") : emptyState("Opzioni non disponibili", "Questa domanda non propone risposte a scelta.");
    const freeControl = free && presentationId && !attemptId ? `<label class="visually-hidden" for="assessment-${esc(presentationId)}-response">Risposta libera</label><textarea class="assessment-response" id="assessment-${esc(presentationId)}-response" data-free-response="${esc(presentationId)}" maxlength="${MAX_ENTRY_CHARS}" rows="4" placeholder="Scrivi la tua risposta…">${esc(state.freeAnswers[presentationId] || "")}</textarea>` : "";
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
      const contestMeta = contest ? ` · contestata ${esc(first(object(contest), ["contested_at"], ""))}` : "";
      const predecessorMeta = predecessor ? ` · supera ${esc(predecessor)}` : "";
      return `<li class="assessment-lifecycle__item"><span class="assessment-lifecycle__state">${esc(disposition)}</span><span class="assessment-lifecycle__id">${esc(gradeId)}</span><span class="assessment-lifecycle__score">${esc(scoreText)}</span><span class="assessment-lifecycle__meta">${esc(first(record, ["lifecycle", "status"], "stato non dichiarato"))}${predecessorMeta}${contestMeta}</span></li>`;
    }).join("")}</ol></section>` : "";
    const hasResponse = free ? Boolean(text(state.freeAnswers[presentationId]).trim()) : selectedOptions.length > 0;
    const attemptAction = canAttempt
      ? `<div class="card__actions"><button class="button" type="button" data-command="assessment-attempt" data-presentation-id="${esc(presentationId)}" data-assessment-format="${esc(format)}"${hasResponse ? "" : " disabled"}>Registra tentativo</button></div>`
      : canGrade
        ? `<div class="card__actions"><button class="button button--quiet" type="button" data-command="assessment-grade" data-attempt-id="${esc(attemptId)}">Richiedi valutazione</button></div>`
      : !presentationId && revisionId
        ? `<div class="card__actions"><button class="button" type="button" data-command="assessment-present" data-revision-id="${esc(revisionId)}">Presenta verifica</button></div>`
        : !presentationId ? `<p class="card__meta">Azioni non disponibili: manca l’identificativo della presentazione.</p>` : "";
    return `<article class="assessment-card"><div class="card__header"><p class="section-kicker">${esc(ASSESSMENT_FORMAT_LABELS[format] || "Domanda")}</p>${pill(status)}</div><h2 class="assessment-card__prompt">${esc(question)}</h2>${freeControl}<fieldset class="choice-fieldset"${free || !presentationId || attemptId ? " hidden" : ""}><legend class="visually-hidden">Scegli una risposta</legend><ol class="choice-list">${choices}</ol></fieldset>${grade ? `<p class="assessment-feedback">${esc(typeof grade === "string" ? grade : first(object(grade), ["message", "summary", "label"], "Esito disponibile."))}</p>` : ""}${lifecycleHistory}${attemptAction}</article>`;
  }

  document.addEventListener("submit", (event) => {
    const form = event.target.closest?.("[data-student-state-form]");
    if (!form) return;
    event.preventDefault();
    const values = new FormData(form);
    executeCommand("/api/v1/student-state", { kind: values.get("kind"), topic: values.get("topic"), summary: values.get("summary") }, form, "percorso");
  });
  document.addEventListener("click", async (event) => {
    const control = event.target.closest?.("[data-student-state-before]");
    if (!control) return;
    try {
      const payload = await fetchJson(`/api/v1/student-state/before/${encodeURIComponent(control.dataset.studentStateBefore)}`);
      renderStudentState(payload);
    } catch (error) { setStatus("degraded", error.message || "Percorso non disponibile"); }
  });

  function renderStudentState(payload) {
    const labels = { topic_covered: "Argomento studiato", learner_signal: "Punto difficile", assessment_activity: "Verifica", context_recorded: "Contesto" };
    const writers = { student: "tu", tutor_agent: "tutor", host: "Cardine" };
    const entries = array(payload.entries);
    const rows = entries.length
      ? `<ol class="journal">${entries.map((entry) => {
        const item = object(entry);
        return `<li class="journal__entry"><p class="journal__meta">${esc(labels[item.kind] || "Attività")} · ${esc(writers[item.recorded_by] || "Cardine")} · <time>${esc(formatDate(text(item.occurred_at).slice(0, 10)))}</time></p><p class="journal__topic">${esc(item.topic)}</p>${item.summary ? `<p class="journal__summary">${esc(item.summary)}</p>` : ""}</li>`;
      }).join("")}</ol>`
      : emptyState("Ancora nessuna attività", "Qui ritroverai gli argomenti studiati e i punti che ti sono sembrati difficili.");
    const more = payload.has_more ? `<div class="state-actions"><button class="button button--quiet" type="button" data-student-state-before="${esc(payload.next_cursor)}">Mostra attività precedenti</button><span class="field-note">${entries.length} di ${esc(payload.total_entries)}</span></div>` : "";
    const form = `<details class="journal-add"><summary>Aggiungi una nota</summary><form data-student-state-form class="journal-add__form"><label>Tipo<select name="kind"><option value="learner_signal">Punto difficile</option><option value="topic_covered">Argomento studiato</option></select></label><label>Argomento<input name="topic" maxlength="120" required></label><label>Dettaglio <span class="field-optional">(facoltativo)</span><textarea name="summary" maxlength="500" rows="3"></textarea></label><div class="state-actions"><button class="button" type="submit">Aggiungi</button></div></form></details>`;
    setView("percorso", page({
      headingId: "student-state-heading",
      title: "Progressi",
      lede: "Cosa hai studiato e dove hai trovato difficoltà.",
      actions: `<button class="button button--quiet" type="button" data-command="student-state-import">Importa cronologia</button>`,
      body: `${form}${rows}${more}`,
    }));
  }

  function reviewScope() {
    return JSON.stringify([object(state.bootstrap?.course).id, object(state.bootstrap?.session).id]);
  }

  function canLeaveReviewScope(targetScope = null) {
    if (!state.review.pending.length || targetScope === state.review.scope) return true;
    showAlert({ tone: "warning", title: "Ci sono valutazioni da salvare", detail: "Completa il salvataggio prima di cambiare corso, sessione o account.", actions: [{ label: "Apri Ripasso", run: () => loadRoute("ripasso") }] });
    return false;
  }

  function reviewSnapshot(payload) {
    const review = state.review;
    const scope = reviewScope();
    if (review.scope !== scope && !review.pending.length) {
      review.snapshot = null;
      review.scope = scope;
      state.revealedReviews = Object.create(null);
    }
    if (!review.snapshot || Number(payload.high_water_sequence || 0) >= Number(review.snapshot.high_water_sequence || 0)) {
      review.snapshot = payload;
    }
    return review.snapshot;
  }

  function visibleReviewItems(payload) {
    const pending = new Set(state.review.pending.map((command) => command.revisionId));
    const due = array(first(payload, ["items", "due", "cards", "queue"], []))
      .filter((card) => !pending.has(text(first(object(card), ["revision_id", "id"]))));
    // An uncertain write is still pending. Restore its exact card for an
    // explicit retry; never allow a different rating to acquire a new identity.
    if (state.review.error && state.review.pending.length) due.unshift(state.review.pending[0].card);
    return due;
  }

  function renderReviewState() {
    if (state.route === "ripasso") renderRipasso(state.review.snapshot || state.viewData || {});
  }

  function rateReview(control) {
    const review = state.review;
    const revisionId = text(control.dataset.revisionId);
    const rating = text(control.dataset.rating);
    const card = visibleReviewItems(review.snapshot || state.viewData || {})[0];
    if (review.error || review.scope !== reviewScope() || !card
      || text(first(object(card), ["revision_id", "id"])) !== revisionId
      || state.revealedReviews[revisionId] !== true
      || !["again", "hard", "good", "easy"].includes(rating)
      || review.pending.some((command) => command.revisionId === revisionId)) return;
    const scope = Object.freeze({course_id: object(state.bootstrap?.course).id, session_id: object(state.bootstrap?.session).id});
    review.pending.push(Object.freeze({ revisionId, rating, requestId: requestId(), card, scope }));
    delete state.revealedReviews[revisionId];
    // The due payload already contains the next card. Painting it does not
    // claim a commit, compute a schedule, or wait for any network round trip.
    renderReviewState();
    $('[data-reveal-review]', root)?.focus({ preventScroll: true });
    void saveReviews();
  }

  async function saveReviews() {
    const review = state.review;
    if (review.saving || review.error || !review.pending.length) return;
    review.saving = true;
    try {
      while (review.pending.length && !review.error) {
        if (review.scope !== reviewScope()) throw new Error("Il contesto di studio è cambiato. Torna al corso e alla sessione del ripasso.");
        const command = review.pending[0];
        const receipt = await fetchJson(`/api/v1/recall/${encodeURIComponent(command.revisionId)}/reviews`, {
          method: "POST", body: JSON.stringify({ ...commandPayload({ rating: command.rating, review_scope: command.scope }, command.requestId), expected_sequence: Math.max(state.highWaterSequence, Number(review.snapshot?.high_water_sequence || 0)) }),
        });
        if (receipt.status !== "committed" || !Array.isArray(receipt.result?.items)
          || !Number.isInteger(receipt.high_water_sequence)
          || receipt.result.high_water_sequence !== receipt.high_water_sequence) {
          throw new Error("La conferma del salvataggio non è completa. Riprova lo stesso salvataggio.");
        }
        updateSequence(Math.max(state.highWaterSequence, receipt.high_water_sequence));
        // Each serialized POST uses the preceding canonical receipt's sequence.
        // The server has already joined the next due queue at that same HWM.
        reviewSnapshot(object(receipt.result));
        review.pending.shift();
        renderReviewState();
      }
      if (!review.pending.length) {
        setStatus("committed", "Valutazioni salvate nel registro canonico");
        // Sidebar counts are advisory and never block the next review.
        void refreshBootstrapCounts();
      }
    } catch (error) {
      review.error = error;
      renderReviewState();
      if (!error.authExpired) setStatus("error", "Salvataggio della valutazione da verificare. Apri Ripasso e riprova.", { alert: false });
    } finally {
      review.saving = false;
    }
  }

  async function recoverReviews(discardRejected = false) {
    const review = state.review;
    if (review.saving || !review.error || review.scope !== reviewScope()) return;
    if (discardRejected && (![400, 404, 409, 422].includes(review.error.status) || review.error.payload?.commandCommitted)) return;
    review.saving = true;
    try {
      const payload = await fetchJson(ROUTES.ripasso.endpoint);
      updateSequence(payload.high_water_sequence);
      review.snapshot = payload;
      if (discardRejected) review.pending.shift();
      review.error = null;
    } catch (error) {
      review.error = error;
    } finally {
      review.saving = false;
      renderReviewState();
    }
    // Retrying an ambiguous result keeps revision, rating and request ID.
    // Only an explicit discard of a rejected command removes an unsaved intent.
    if (!review.error) void saveReviews();
  }

  function renderRipasso(payload) {
    payload = reviewSnapshot(payload);
    const availabilityStatus = text(first(payload, ["status"], "empty"), "empty");
    const due = visibleReviewItems(payload);
    const review = state.review;
    const pendingCopy = review.pending.length ? `<p class="field-note" role="status" data-review-pending>${review.pending.length} ${review.pending.length === 1 ? "valutazione in salvataggio" : "valutazioni in salvataggio"}…</p>` : "";
    const rejected = review.error && [400, 404, 409, 422].includes(review.error.status) && !review.error.payload?.commandCommitted;
    const recovery = review.error ? `<div class="review-recovery" role="alert" data-review-error><p>Il salvataggio non è confermato. Le valutazioni successive sono in pausa.</p><div class="state-actions"><button class="button" type="button" data-review-retry>Riprova lo stesso salvataggio</button>${rejected ? `<button class="button button--quiet" type="button" data-review-discard>Annulla la valutazione rifiutata e continua</button>` : ""}</div></div>` : "";
    const frame = (body) => setView("ripasso", page({ headingId: "review-heading", title: "Ripasso", className: "page--review", body }));
    if (availabilityStatus === "not_configured" || availabilityStatus === "unavailable") {
      frame(`${emptyState("Ripasso non disponibile", first(payload, ["message"], "Il ripasso programmato non è configurato."), "unavailable")}${pendingCopy}${recovery}`);
      return;
    }
    if (!due.length) {
      frame(`${review.pending.length ? emptyState("Salvataggio in corso", "Sto salvando le ultime valutazioni.") : emptyState("Nessuna card per oggi", "Hai finito il ripasso di oggi. Le prossime card compariranno quando sarà il momento.", "empty", [{ label: "Torna alla home", route: "oggi" }])}${pendingCopy}${recovery}`);
      return;
    }
    const firstCard = object(due[0]);
    const revisionId = first(firstCard, ["revision_id", "id"], "");
    const revealed = review.error || state.revealedReviews[revisionId] === true;
    const ticks = due.slice(0, 24).map((_, index) => `<span class="review-progress__tick ${index === 0 ? "is-current" : ""}"></span>`).join("");
    const front = first(firstCard, ["front", "question", "prompt"], "Contenuto della card non disponibile.");
    const back = first(firstCard, ["back", "answer", "response"], "Risposta non disponibile.");
    const citation = first(firstCard, ["citation", "provenance", "source"], null);
    const reviewActions = revealed && revisionId ? `<div class="rating-list" role="group" aria-label="Quanto ricordavi?">${[["again", "Ancora"], ["hard", "Difficile"], ["good", "Bene"], ["easy", "Facile"]].map(([value, label]) => `<button class="rating-button" type="button" data-command="review" data-revision-id="${esc(revisionId)}" data-rating="${value}"${review.error ? " disabled" : ""}>${label}</button>`).join("")}</div>` : revealed ? emptyState("Valutazione non disponibile", "Questa card non può essere valutata.", "unavailable") : "";
    frame(`<div class="review-card"><div class="review-progress"><span>${due.length === 1 ? "Ultima card" : `${due.length} card rimaste`}</span><span class="review-progress__bar" aria-hidden="true">${ticks}</span></div><div class="review-card__front">${esc(front)}</div>${revealed ? `<div class="review-card__back">${esc(back)}</div>` : revisionId ? `<button class="button review-card__reveal" type="button" data-reveal-review="${esc(revisionId)}">Mostra risposta</button>` : emptyState("Card non disponibile", "Questa card non può essere mostrata ora.", "unavailable")}${citation ? `<button class="provenance-chip" type="button" data-provenance='${esc(JSON.stringify(citation))}'>Fonte · ${esc(first(object(citation), ["locator", "title"], typeof citation === "string" ? citation : "dettagli"))}</button>` : ""}${reviewActions}${pendingCopy}${recovery}</div>`);
  }

  function renderPlan(payload) {
    const plan = object(payload);
    const readiness = object(first(plan, ["readiness"], plan));
    if (text(plan.status, "ready") === "unavailable") {
      setView("piano", page({
        headingId: "plan-heading",
        title: "Piano d’esame",
        body: emptyState("Piano non disponibile", first(plan, ["message"], "Questo corso non ha ancora un piano."), "unavailable"),
      }));
      return;
    }
    const exam = object(first(readiness, ["exam"], plan));
    const dateValue = text(first(exam, ["date", "exam_date"], first(readiness, ["exam_date"], "")));
    const days = first(exam, ["days_remaining"], first(readiness, ["days_remaining"], null));
    const recall = object(readiness.recall);
    const examBlock = dateValue && Number.isInteger(days)
      ? `<section class="exam-countdown" aria-label="Data d’esame"><p class="exam-countdown__days"><strong>${esc(String(Math.max(days, 0)))}</strong> ${days === 1 ? "giorno" : "giorni"}</p><p class="exam-countdown__date">all’esame del ${esc(formatDate(dateValue))}</p></section>`
      : `<section class="exam-countdown exam-countdown--empty" aria-label="Data d’esame"><p class="exam-countdown__title">Data d’esame non impostata</p><p class="exam-countdown__date">Quando la data è configurata, qui vedi i giorni che mancano e il lavoro aperto.</p></section>`;
    const counts = array(readiness.artifact_counts).map(object)
      .filter((row) => Number(text(row.pending, "0")) > 0 || Number(text(row.accepted, "0")) > 0);
    const stats = [
      recall.available ? { value: text(recall.due_count, "0"), label: "card da ripassare oggi", route: "ripasso" } : null,
      ...counts.map((row) => ({ value: text(row.accepted, "0"), label: `${(ARTIFACT_LABELS[text(row.kind)] || "Materiali").toLowerCase()} approvate${Number(text(row.pending, "0")) ? ` · ${text(row.pending)} da approvare` : ""}`, route: "proposte" })),
    ].filter(Boolean);
    const statsView = stats.length
      ? `<ul class="plan-stats">${stats.map((stat) => `<li><button type="button" class="plan-stats__item" data-route="${esc(stat.route)}"><strong>${esc(stat.value)}</strong><span>${esc(stat.label)}</span></button></li>`).join("")}</ul>`
      : "";
    const listOf = (items) => array(items).map((item) => text(first(object(item), ["value"], item))).filter(Boolean);
    const constraintItems = array(readiness.constraints).map(object).map((row) => [text(row.kind), text(row.value)].filter(Boolean).join(": ")).filter(Boolean);
    const formatItems = array(readiness.blueprints).map(object).map((row) => [...array(row.observed_topics), ...array(row.observed_formats)].map((value) => text(first(object(value), ["value"], ""))).filter(Boolean).join(", ")).filter(Boolean);
    const groups = [
      ["Obiettivi", listOf(readiness.learning_goals)],
      ["Come verrai valutato", listOf(readiness.assessment_styles)],
      ["Vincoli", constraintItems],
      ["Formato d’esame osservato", formatItems],
    ].filter(([, items]) => items.length);
    const details = groups.length
      ? `<dl class="plan-details">${groups.map(([label, items]) => `<div class="plan-details__group"><dt>${esc(label)}</dt>${items.map((item) => `<dd>${esc(item)}</dd>`).join("")}</div>`).join("")}</dl>`
      : `<p class="page__note">Obiettivi e vincoli del corso compariranno qui quando saranno configurati.</p>`;
    setView("piano", page({
      headingId: "plan-heading",
      title: "Piano d’esame",
      body: `${examBlock}${statsView}${details}`,
    }));
  }

  const MONTHS = Object.freeze(["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]);

  /* Presentation only: the service owns the calendar arithmetic. */
  function formatDate(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(text(value));
    if (!match || !MONTHS[Number(match[2]) - 1]) return text(value);
    return `${Number(match[3])} ${MONTHS[Number(match[2]) - 1]} ${match[1]}`;
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
    // The attached lesson travels with the turn, so the answer and any
    // flashcards asked for in the chat stay inside that one source.
    const pin = lessonPin();
    const payload = {
      ...(continuation ? { response: value } : { content: value }),
      ...(pin ? { lesson_pin: pin } : {}),
    };
    if (textarea) {
      textarea.value = "";
      resizeComposer(textarea);
    }
    state.continuationDraft = "";
    await executeCommand(endpoint, payload, form, continuation ? "sessione" : "sessione");
  }

  async function executeCommand(endpoint, payload, form, refreshRoute, requestOverride = null) {
    const commandNavigationVersion = state.navigationVersion;
    const isTutorTurn = endpoint === "/api/v1/session/turns" || endpoint.includes("/session/continuations/");
    const isFlashcardCommand = endpoint === "/api/v1/lessons/flashcards"
      || (isTutorTurn && /\b(?:genera|crea|generate|create)\b[\s\S]{0,80}\b(?:cards?|flashcards?|schede|carte\s+di\s+studio)\b/i.test(text(payload.content || payload.response)));
    const forcedRequest = text(requestOverride, "");
    const retryingCommand = Boolean(
      forcedRequest
      || state.lastCommand
      && state.lastCommand.endpoint === endpoint
      && JSON.stringify(state.lastCommand.payload) === JSON.stringify(payload)
    );
    const request = forcedRequest || (retryingCommand ? state.lastCommand.requestId : requestId());
    const reusingRequest = Boolean(state.lastCommand?.requestId === request);
    const command = Object.freeze({
      endpoint,
      payload: Object.freeze({ ...payload }),
      requestId: request,
      refreshRoute,
    });
    state.lastCommand = command;
    if (isTutorTurn) {
      state.pendingTurn = {
        requestId: request,
        content: text(payload.content || payload.response),
        awaitingRetryActivity: reusingRequest,
      };
      renderOptimisticTurn(state.pendingTurn.content);
      pollTurnActivity(request).catch(() => {});
    }
    setBusy(true);
    setStatus(
      "working",
      isFlashcardCommand ? "Genero e verifico le proposte flashcard…" : "Salvataggio nel registro canonico…"
    );
    try {
      const receipt = await fetchJson(endpoint, { method: "POST", body: JSON.stringify(commandPayload(payload, request)) });
      const activity = object(receipt.activity);
      const settledRecords = array(receipt.activity_records);
      const presentationId = text(receipt.presentation_id, "");
      if (isTutorTurn && presentationId) {
        if (endpoint === "/api/v1/session/turns") state.turnCommands[presentationId] = command;
      }
      if (isTutorTurn && presentationId && settledRecords.length) {
        state.turnActivities[presentationId] = { records: settledRecords, state: "settled" };
      }
      const flashcardCompleted = text(activity.kind) === "flashcard_generation"
        && text(activity.status) === "completed";
      const traceId = text(receipt.trace_id, "");
      if (traceId) state.diagnosticTraceId = traceId;
      updateSequence(first(receipt, ["high_water_sequence", "sequence"], state.highWaterSequence));
      const status = text(first(receipt, ["status", "shell_status"], "committed"), "committed");
      const terminalTutorTurn = isTutorTurn && INCOMPLETE_TURN_STATUSES.has(status);
      setStatus(status, `Comando ${statusLabel(status)}`);
      const commandIsCurrent = state.lastCommand && state.lastCommand.requestId === request;
      // A successful HTTP response is settled, including the safe fallback
      // for non-transient tutor failures. Only the catch path can retain a
      // command for an explicit transient retry action.
      if (commandIsCurrent) state.lastCommand = null;
      if (isTutorTurn && state.pendingTurn?.requestId === request) state.pendingTurn = null;
      if (isTutorTurn) state.activityPollToken += 1;
      if (endpoint === "/api/v1/session/turns" || endpoint.includes("/session/continuations/")) {
        state.continuationDraft = "";
      }
      const originIsStillActive = commandNavigationVersion === state.navigationVersion;
      let routeRefreshed = false;
      if (originIsStillActive && status === "demo_completed" && refreshRoute === "sessione") {
        state.route = "sessione";
        state.viewData = object(receipt.result);
        renderSessione(state.viewData);
        setStatus("recovered", "Anteprima completata · nessun dato personale salvato");
      } else if (originIsStillActive && terminalTutorTurn) {
        state.route = "sessione";
        state.viewData = object(receipt.result);
        renderSessione(state.viewData);
        dismissAlert();
      } else if (originIsStillActive && isTutorTurn) {
        // The receipt is the only first-delivery carrier for process-local
        // activity records. Rendering it directly keeps the settled chips on
        // the answer without pretending they survive a later reload.
        state.route = "sessione";
        state.viewData = object(receipt.result);
        renderSessione(state.viewData);
      } else if (originIsStillActive) {
        routeRefreshed = await loadRoute((isFlashcardCommand || flashcardCompleted) && status === "completed" ? "proposte" : refreshRoute);
      }
      if (isTutorTurn) {
        void refreshBootstrapCounts();
      } else {
        await refreshBootstrapCounts();
      }
      if (routeRefreshed && refreshRoute === "proposte" && endpoint.includes("/artifacts/") && endpoint.endsWith("/decisions")) {
        const decisions = endpoint === "/api/v1/artifacts/decisions" ? array(payload.decisions) : [payload];
        const accepted = decisions.filter((item) => text(object(item).decision) === "accepted").length;
        const rejected = decisions.filter((item) => text(object(item).decision) === "rejected").length;
        const summary = [
          accepted ? `${accepted} flashcard ${accepted === 1 ? "accettata" : "accettate"}` : "",
          rejected ? `${rejected} flashcard ${rejected === 1 ? "rifiutata" : "rifiutate"}` : "",
        ].filter(Boolean).join(" · ");
        setStatus("committed", `${summary}. La coda delle proposte è aggiornata.`);
      }
      if (isTutorTurn && originIsStillActive && receipt.result) {
        const assistant = $$(".thread-message--assistant", root).at(-1);
        if (assistant && settledRecords.length) {
          const existing = $(".thread-message__activity", assistant);
          if (existing) patch(existing, aiToolChips({ records: settledRecords, state: "settled" }));
        }
      }
      const nextComposer = originIsStillActive ? $("#session-entry-text") : null;
      if (nextComposer) nextComposer.focus({ preventScroll: true });
    } catch (error) {
      const commandCommitted = Boolean(error.payload && error.payload.commandCommitted);
      const traceId = text(error.payload?.traceId, "");
      if (traceId) state.diagnosticTraceId = traceId;
      if (isTutorTurn) {
        const pendingIsCurrent = state.pendingTurn?.requestId === request;
        const failedContent = pendingIsCurrent ? text(state.pendingTurn.content) : "";
        if (pendingIsCurrent) state.pendingTurn = null;
        removeOptimisticTurn();
        if (!commandCommitted && failedContent) restoreFailedTurnDraft(failedContent, form);
      }
      setBusy(false);
      if (error.authExpired) return;
      if (commandNavigationVersion === state.navigationVersion) {
        if (isTutorTurn || error.status === 409) await refreshBootstrapCounts();
        if (isTutorTurn && commandCommitted) await loadRoute("sessione");
        // One diagnosis, phrased once. The live region and the banner say
        // the same thing, so a screen reader and a screen never disagree.
        const conflict = error.status === 409;
        const title = conflict
          ? "Lo stato è cambiato"
          : commandCommitted
            ? "Messaggio salvato, risposta non completata"
            : "Il messaggio non è stato inviato";
        const detail = conflict
          ? "Ho aggiornato la sezione con lo stato corrente: puoi rieseguire lo stesso comando."
          : (isTutorTurn && MODEL_ERROR_MESSAGES[error.code])
            || (error.status === 503 && isTutorTurn
              ? "Il tutor non ha prodotto una risposta verificata. Apri Diagnostica e riprova."
              : text(error.message, "Il servizio locale non ha risposto."));
        setStatus(conflict ? "stale" : "error", `${title}. ${detail}`, { alert: false });
        showCommandError(error, { title, detail });
      }
    }
    setBusy(false);
  }

  function renderOptimisticTurn(content) {
    const safeContent = esc(content);
    const outgoing = `<article class="thread-message thread-message--learner" data-optimistic-turn><p class="thread-message__role">tu</p><p class="thread-message__text">${safeContent}</p></article>`;
    const flashcards = /\b(?:genera|crea|generate|create)\b[\s\S]{0,80}\b(?:cards?|flashcards?|schede|carte\s+di\s+studio)\b/i.test(content);
    const pendingCopy = flashcards
      ? "Sto generando e verificando le proposte flashcard…"
      : "Sto preparando una risposta basata sulle fonti del corso…";
    const pending = `<article class="thread-message thread-message--assistant thread-message--pending" data-optimistic-turn><p class="thread-message__role">tutor</p><div class="thread-message__activity" data-turn-activity aria-live="polite">${aiToolChips({state: "running", records: [], progress_message: pendingCopy})}</div><div data-turn-draft hidden><p class="field-note">Risposta in generazione · da verificare</p><p class="thread-message__text" data-turn-draft-text></p></div></article>`;
    const thread = $(".session-thread", root);
    if (thread) {
      const empty = $(".empty-state", thread);
      if (empty) empty.hidden = true;
      thread.insertAdjacentHTML("beforeend", outgoing + pending);
      const scroller = $(".conversation-scroll", root);
      if (scroller) scroller.scrollTop = scroller.scrollHeight;
      conversationScroller?.resume();
      return;
    }
    setView("sessione", sessionShell({
      title: text(object(state.bootstrap?.course).title, "Sessione di studio"),
      subtitle: statusLabel("working"),
      thread: outgoing + pending,
      placeholder: "Prepara il prossimo messaggio…",
      actions: `<button class="text-button" type="button" data-route="fonti">Libreria</button>`,
    }));
  }

  function removeOptimisticTurn() {
    $$('[data-optimistic-turn]', root).forEach((item) => item.remove());
    const empty = $(".session-thread > .empty-state", root);
    if (empty) empty.hidden = false;
  }

  async function pollTurnActivity(requestId) {
    const token = ++state.activityPollToken;
    const navigationVersion = state.navigationVersion;
    let awaitingRetryActivity = state.pendingTurn?.awaitingRetryActivity === true;
    let failures = 0;
    while (token === state.activityPollToken && navigationVersion === state.navigationVersion && state.pendingTurn?.requestId === requestId && failures < 3) {
      try {
        const [payload, output] = await Promise.all([
          fetchJson(`/api/v1/turns/${encodeURIComponent(requestId)}/activity`),
          fetchJson(`/api/v1/turns/${encodeURIComponent(requestId)}/output`),
        ]);
        if (token !== state.activityPollToken || navigationVersion !== state.navigationVersion || state.pendingTurn?.requestId !== requestId) return;
        if (awaitingRetryActivity && payload.state !== "running") {
          await new Promise((resolve) => window.setTimeout(resolve, 600));
          continue;
        }
        awaitingRetryActivity = false;
        failures = 0;
        const progressMessage = text(payload.progress_message, "");
        const node = $("[data-turn-activity]", root);
        if (node && token === state.activityPollToken) {
          const scroll = captureScroll();
          patch(node, aiToolChips({ ...payload, progress_message: progressMessage }));
          const progressNode = $("[data-turn-progress]", node);
          if (progressNode && progressMessage) progressNode.textContent = progressMessage;
          restoreScroll(scroll, false);
        }
        const draft = $("[data-turn-draft]", root);
        const draftText = $("[data-turn-draft-text]", root);
        if (draft && draftText) {
          const scroll = captureScroll();
          const content = output.state === "generating" ? text(output.text, "") : "";
          draftText.textContent = content;
          draft.hidden = !content;
          restoreScroll(scroll, false);
        }
        await new Promise((resolve) => window.setTimeout(resolve, 100));
      } catch (_) {
        failures += 1;
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
      }
    }
  }

  function restoreFailedTurnDraft(content, originForm) {
    const origin = originForm && document.contains(originForm) ? $("textarea", originForm) : null;
    const textarea = origin || $("#session-entry-text", root) || $("#entry", root);
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
    if (selected && !["txt", "md", "pdf", "mp3", "wav", "m4a", "mp4", "ogg", "webm", "flac", "aac"].includes(extension || "")) {
      if (status) status.textContent = "Sono supportati PDF, testo, Markdown e registrazioni audio.";
      return;
    }
    const isPdf = selected && extension === "pdf";
    const isAudio = selected && ["mp3", "wav", "m4a", "mp4", "ogg", "webm", "flac", "aac"].includes(extension);
    const maximum = isPdf ? 256 * 1024 * 1024 : isAudio ? 128 * 1024 * 1024 : 196608;
    if (selected && selected.size > maximum) {
      if (status) status.textContent = isPdf ? "Il PDF supera il limite di 256 MiB." : "Il file supera il limite di 192 KB.";
      return;
    }
    const content = selected && !isPdf && !isAudio ? await selected.text() : pasted;
    if (!isPdf && !isAudio && !text(content).trim()) {
      if (status) status.textContent = "Scegli un file .txt/.md oppure incolla una fonte testuale.";
      return;
    }
    if (!isPdf && !isAudio && new TextEncoder().encode(content).length > 196608) {
      if (status) status.textContent = "Il testo supera il limite di 192 KB per questa prima importazione.";
      return;
    }
    const filename = selected?.name || "appunti-incollati.md";
    const title = text(titleInput?.value).trim() || filename.replace(/\.[^.]+$/, "").replace(/[-_]+/g, " ");
    const submit = $("button[type=submit]", form);
    if (submit) submit.disabled = true;
    if (status) status.textContent = "Salvo e indicizzo la fonte…";
    try {
      const receipt = isAudio
        ? await fetchJson("/api/v1/sources/import/audio", {
            method: "POST", headers: { "Content-Type": "application/octet-stream",
              "X-File-Name": encodeURIComponent(filename), "X-Source-Title": encodeURIComponent(title),
              "Idempotency-Key": requestId() }, body: selected,
          })
        : isPdf
        ? await fetchJson("/api/v1/sources/import/pdf", {
            method: "POST",
            headers: {
              "Content-Type": "application/pdf",
              "X-File-Name": filename,
              "X-Source-Title": title,
              "Idempotency-Key": requestId(),
            },
            body: selected,
          })
        : await fetchJson("/api/v1/sources/upload", {
            method: "POST",
            body: JSON.stringify(commandPayload({ filename, title, content })),
          });
      updateSequence(first(receipt, ["high_water_sequence"], state.highWaterSequence));
      if (isAudio) {
        await loadRoute("fonti");
        await refreshMaterialJobs();
        return;
      }
      const indexing = object(receipt.indexing);
      const indexingContinues = ["queued", "indexing"].includes(text(indexing.status));
      if (indexingContinues) {
        if (status) status.textContent = "Fonte salvata. Indicizzazione in background…";
        pollIndexing(status).catch(() => {});
      }
      state.studySetup = null;
      await refreshBootstrapCounts();
      await loadRoute(state.route === "fonti" ? "fonti" : "oggi");
    } catch (error) {
      if (status) status.textContent = error.message;
    } finally {
      if (submit) submit.disabled = false;
    }
  }

  let materialPoll = null;
  const materialPreviews = new Map();
  function bindNoteControl(control, event, handler) {
    if (control._notesBound) return;
    control._notesBound = true;
    control.addEventListener(event, handler);
  }

  function notesScope() {
    return JSON.stringify([object(state.bootstrap?.course).id, object(state.bootstrap?.session).id]);
  }

  async function prepareNotes(control) {
    const pane = $("#material-jobs");
    if (!pane || control.disabled) return;
    control.disabled = true;
    const scope = notesScope();
    const pin = JSON.parse(control.dataset.generateNotes);
    // This marker keeps job polling from replacing loading, selection or errors.
    patch(pane, `<section class="notes-job" data-notes-lessons aria-busy="true"><h2>Scegli una lezione</h2><p role="status">Carico la struttura della fonte…</p></section>`);
    const loading = pane.firstElementChild;
    try {
      const prepared = await fetchJson("/api/v1/material-generations/prepare", {
        method: "POST", body: JSON.stringify(commandPayload(pin)),
      });
      if (scope !== notesScope() || !control.isConnected || pane.firstElementChild !== loading) return;
      showNoteStructureSelection(pane, pin, prepared, scope);
    } catch (error) {
      if (scope !== notesScope() || pane.firstElementChild !== loading) return;
      patch(pane, `<section class="notes-job" data-notes-lessons><h2>Struttura non disponibile</h2><p role="status">${esc(error.message)}</p><button class="button" type="button" data-notes-reload>Riprova</button></section>`);
      $('[data-notes-reload]', pane).addEventListener("click", () => prepareNotes(control));
    } finally { control.disabled = false; }
  }

  function showNoteStructureSelection(pane, pin, prepared, scope) {
    const structure = array(prepared.structure);
    const pdf = Number.isInteger(prepared.page_count);
    const status = text(prepared.structure_status, "absent");
    const waiting = ["queued", "indexing"].includes(status);
    const detail = structure.length
      ? "Scegli una lezione o una sezione: verrà elaborato solo il suo testo."
      : waiting ? "La fonte è salvata. La struttura delle lezioni è ancora in elaborazione: aggiorna fra poco."
      : status === "failed" ? "L’estrazione della struttura non è riuscita. Puoi riprovare dopo aver reindicizzato la fonte."
      : "Non è disponibile una struttura delle lezioni per questa fonte.";
    const options = structure.map((lesson, index) => {
      const depth = structure.filter((parent) => parent.start_offset <= lesson.start_offset && parent.end_offset >= lesson.end_offset && (parent.start_offset < lesson.start_offset || parent.end_offset > lesson.end_offset)).length;
      return `<option value="${index}">${esc(`${"› ".repeat(Math.min(depth, 8))}${lesson.title}`)}</option>`;
    }).join("");
    patch(pane, `<section class="notes-job"><h2>Scegli una lezione</h2><p role="status">${esc(detail)}</p><form data-notes-lessons><div class="field"><label for="notes-structure-lesson">Lezione o sezione della fonte</label><select id="notes-structure-lesson" name="structure_lesson"><option value="">Scegli una lezione…</option>${options}${!pdf ? '<option value="whole">Intera fonte</option>' : ""}</select></div><p data-notes-boundary class="field-note"></p><div class="state-actions"><button class="button" type="submit" disabled>Genera note per la lezione scelta</button><button class="button button--quiet" type="button" data-notes-reload>Aggiorna struttura</button>${pdf ? '<button class="button button--quiet" type="button" data-notes-boundaries>Modifica confini PDF / scegli più lezioni</button>' : ""}<button class="button button--quiet" type="button" data-notes-cancel>Annulla</button></div><p data-notes-error role="status"></p></form></section>`);
    const form = $('[data-notes-lessons]', pane);
    const select = $('select', form);
    const submit = $('[type="submit"]', form);
    let submission = null;
    let previous = "";
    select.addEventListener("change", () => {
      if (select.value !== previous) submission = null;
      previous = select.value;
      submit.disabled = !select.value;
      submit.textContent = select.value === "whole" ? "Genera note per l’intera fonte" : "Genera note per la lezione scelta";
      const lesson = select.value !== "" && select.value !== "whole" ? structure[Number(select.value)] : null;
      $('[data-notes-boundary]', form).textContent = lesson ? `${lesson.title} · ${lesson.end_offset - lesson.start_offset} caratteri. Confermi questi confini avviando la generazione.` : select.value === "whole" ? "Verrà elaborata l’intera revisione della fonte." : "";
    });
    $('[data-notes-cancel]', form).addEventListener("click", async () => {
      form.remove();
      await refreshMaterialJobs();
    });
    $('[data-notes-reload]', form).addEventListener("click", () => {
      const control = $$('[data-generate-notes]').find((item) => item.dataset.generateNotes === JSON.stringify(pin));
      if (control) prepareNotes(control);
    });
    if (pdf) $('[data-notes-boundaries]', form).addEventListener("click", () => showNoteBoundaryEditor(pane, pin, prepared, scope));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (scope !== notesScope() || !form.isConnected || !select.value || submit.disabled) return;
      submission ||= commandPayload({...pin, ...(select.value === "whole" ? {} : {structure_lesson: structure[Number(select.value)]})});
      const controls = $$('select, button', form);
      controls.forEach((item) => { item.disabled = true; });
      try {
        const receipt = await fetchJson("/api/v1/material-generations", {method: "POST", body: JSON.stringify(submission)});
        if (scope !== notesScope() || !form.isConnected) return;
        form.remove();
        await refreshMaterialJobs();
        focusMaterialProgress(array(receipt.items)[0]?.job_id);
      } catch (error) {
        if (form.isConnected) $('[data-notes-error]', form).textContent = error.message;
      } finally {
        controls.forEach((item) => { item.disabled = false; });
        submit.disabled = !select.value;
      }
    });
  }

  function showNoteBoundaryEditor(pane, pin, prepared, scope) {
    patch(pane, `<section class="notes-job"><h2>Verifica le lezioni del PDF</h2><p>Correggi titoli e intervalli. Ogni riga: titolo | pagina iniziale | pagina finale. La divisione deve coprire tutte le pagine una volta. Nel passaggio successivo scegli quali lezioni generare.</p><form data-notes-lessons><label for="notes-lesson-ranges">Lezioni del PDF</label><textarea id="notes-lesson-ranges" rows="8">${esc(array(prepared.lessons).map((item) => `${item.title} | ${item.start_page} | ${item.end_page}`).join("\n"))}</textarea><button class="button" type="submit">Conferma confini e scegli lezioni</button><p data-notes-error role="status"></p></form></section>`);
    $("[data-notes-lessons]", pane).addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      try {
        const lessons = $("textarea", form).value.split("\n").filter((line) => line.trim()).map((line) => {
          const parts = line.split("|").map((item) => item.trim());
          if (parts.length !== 3) throw new Error("Ogni riga deve contenere titolo | pagina iniziale | pagina finale.");
          const [title, start, end] = parts;
          return {title, start_page: Number(start), end_page: Number(end)};
        });
        let previous = 0;
        if (!lessons.length || lessons.length > 64) throw new Error("Inserisci da 1 a 64 lezioni.");
        for (const lesson of lessons) {
          if (!lesson.title || lesson.title.length > 240 || !Number.isInteger(lesson.start_page) || !Number.isInteger(lesson.end_page) || lesson.start_page !== previous + 1 || lesson.end_page < lesson.start_page || lesson.end_page > prepared.page_count) {
            throw new Error("La divisione deve coprire tutte le pagine in ordine, senza vuoti o sovrapposizioni.");
          }
          previous = lesson.end_page;
        }
        if (previous !== prepared.page_count) throw new Error("Mancano pagine nella divisione per lezioni.");
        const boundaryEditor = pane.firstElementChild;
        showNoteLessonSelection(pane, pin, lessons, scope, () => pane.replaceChildren(boundaryEditor));
      } catch (error) { $("[data-notes-error]", form).textContent = error.message; }
    });
  }

  function showNoteLessonSelection(pane, pin, lessons, scope, editBoundaries) {
    pane.replaceChildren();
    patch(pane, `<section class="notes-job"><h2>Scegli le lezioni da generare</h2><p>Verranno elaborate solo le lezioni selezionate. Le altre pagine del PDF restano escluse.</p><form data-notes-lessons><fieldset class="notes-lesson-selection"><legend>Lezioni del PDF</legend>${lessons.map((lesson, index) => `<label><input type="checkbox" name="lesson" value="${index}"><span>${esc(lesson.title)} · pagine ${lesson.start_page}–${lesson.end_page}</span></label>`).join("")}</fieldset><div class="state-actions"><button class="button button--quiet" type="button" data-notes-select-all>Seleziona tutte</button><button class="button button--quiet" type="button" data-notes-edit>Modifica confini</button><button class="button" type="submit" disabled>Seleziona almeno una lezione</button></div><p data-notes-error role="status"></p></form></section>`);
    const form = $("[data-notes-lessons]", pane);
    const submit = $('[type="submit"]', form);
    let submission = null;
    let selectionKey = "[]";
    const selectedLessons = () => $$('input[name="lesson"]:checked', form).map((input) => lessons[Number(input.value)]);
    const update = () => {
      const selected = selectedLessons();
      const nextSelectionKey = JSON.stringify(selected);
      if (nextSelectionKey !== selectionKey) submission = null;
      selectionKey = nextSelectionKey;
      const count = selected.length;
      submit.disabled = !count;
      submit.textContent = count ? `Genera note per ${count} ${count === 1 ? "lezione" : "lezioni"}` : "Seleziona almeno una lezione";
    };
    form.addEventListener("change", update);
    $('[data-notes-select-all]', form).addEventListener("click", () => {
      $$('input[name="lesson"]', form).forEach((input) => { input.checked = true; });
      update();
    });
    $('[data-notes-edit]', form).addEventListener("click", editBoundaries);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (scope !== notesScope() || !form.isConnected) return;
      const selected_lessons = selectedLessons();
      if (!selected_lessons.length) return;
      submission ||= commandPayload({...pin, selected_lessons});
      const controls = $$('input, button', form);
      controls.forEach((control) => { control.disabled = true; });
      try {
        const receipt = await fetchJson("/api/v1/material-generations", {method: "POST", body: JSON.stringify(submission)});
        if (scope !== notesScope() || !form.isConnected) return;
        form.remove();
        await refreshMaterialJobs();
        focusMaterialProgress(array(receipt.items)[0]?.job_id);
      } catch (error) { $("[data-notes-error]", form).textContent = error.message; }
      finally { controls.forEach((control) => { control.disabled = false; }); }
    });
  }

  function focusMaterialProgress(jobId) {
    const job = $$('#material-jobs .notes-job[data-key]').find((item) => item.dataset.key === jobId);
    if (!job) return;
    job.tabIndex = -1;
    job.focus({preventScroll: true});
    job.scrollIntoView({block: "nearest"});
  }

  function materialProgress(job, labels) {
    const stage = job.active_stage || job.stage;
    const title = text(job.title, "la lezione");
    if (job.active_stage === "boundaries") return `Sto suddividendo ${title} in segmenti…`;
    if (job.active_stage === "complete_segment" && job.segment_total) {
      return `Sto generando ${title}, segmento ${Number(job.segment_count) + 1} di ${job.segment_total}${job.segment_title && job.segment_title !== title ? ` · ${job.segment_title}` : ""}`;
    }
    if (job.active_stage === "complete_merge") return `Sto unendo i segmenti di ${title}…`;
    if (job.active_stage === "study") return `Sto preparando le note di studio per ${title}…`;
    if (job.active_stage === "proposal") return `Sto preparando l’anteprima di ${title}…`;
    if (stage === "queued") return "In coda: la generazione inizierà appena si libera uno spazio.";
    return labels[stage] || "Preparazione delle note…";
  }

  async function refreshMaterialJobs() {
    clearTimeout(materialPoll);
    if (state.route !== "fonti" || !$("#material-jobs") || $("[data-notes-lessons]")) return;
    const scope = notesScope();
    const pane = $("#material-jobs");
    const payload = await fetchJson("/api/v1/material-generations");
    if (scope !== notesScope() || pane !== $("#material-jobs")) return;
    if (state.route !== "fonti" || !$("#material-jobs") || $("[data-notes-lessons]")) return;
    const labels = {queued: "In coda", transcribing: "Trascrizione audio", boundaries: "Segmentazione",
      complete_segment: "Rielaborazione", complete_merge: "Unione dei segmenti", study: "Versione studio",
      publication_retryable: "Salvataggio in attesa: nuovo tentativo",
      proposal: "Preparazione anteprima", proposed: "Note pronte da revisionare", retryable: "Interrotto: puoi riprendere",
      stale: "Fonte aggiornata: rigenera", failed_terminal: "Generazione non riuscita"};
    const jobs = array(payload.items);
    patch($("#material-jobs"), jobs.length ? jobs.map((job) => `<section class="notes-job" data-key="${esc(job.job_id)}"><h2>${esc(job.title)}</h2><p role="status" aria-atomic="true" data-note-progress>${esc(materialProgress(job, labels))}${job.transcribed_chunks ? ` · ${esc(job.transcribed_chunks)} ${job.transcribed_chunks === 1 ? "blocco trascritto" : "blocchi trascritti"}` : ""}${job.segment_count ? ` · ${esc(job.segment_count)} ${job.segment_count === 1 ? "segmento elaborato" : "segmenti elaborati"}` : ""}</p>${job.segment_total ? `<progress class="notes-progress" value="${esc(job.segment_count)}" max="${esc(job.segment_total)}" aria-label="Segmenti elaborati per ${esc(job.title)}"></progress><p class="field-note">Segmenti completati: ${esc(job.segment_count)} di ${esc(job.segment_total)}</p>` : ""}${job.progress_error ? `<p class="field-note">${esc(job.progress_error)}</p>` : ""}${job.error ? `<p role="status">${esc(job.error)}</p>` : ""}${array(job.outputs).map((output) => `<details class="notes-output" data-key="${esc(output.revision_id)}" data-note-preview-job="${esc(job.job_id)}" data-note-preview-revision="${esc(output.revision_id)}"><summary>${output.variant === "complete" ? "Sbobina completa" : "Materiale studio"} · ${esc(statusLabel(output.status))}</summary><div class="notes-markdown">${materialPreviews.get(output.revision_id) || "Apri per leggere le note."}</div>${array(output.limitations).map((item) => `<p class="field-note">${esc(item)}</p>`).join("")}${output.status === "proposed" ? `<div class="state-actions"><button class="button" data-note-decision="accept" data-note-revision="${esc(output.revision_id)}" data-note-job="${esc(job.job_id)}">Approva</button><button class="button button--quiet" data-note-decision="reject" data-note-revision="${esc(output.revision_id)}" data-note-job="${esc(job.job_id)}">Rifiuta</button></div>` : output.publication === "published" ? `<p>Salvato come fonte di studio.</p><button class="button button--quiet" data-source-viewer-mode="page" data-source-viewer='${esc(JSON.stringify({source_id: output.published_source_id, revision_id: output.published_revision_id, viewer_kind: "markdown", title: output.title}))}'>Apri note</button>` : output.status === "accepted" ? `<p>Approvato. La pubblicazione richiede il materiale completo approvato e una fonte ancora valida.</p>` : ""}</details>`).join("")}${["retryable", "publication_retryable"].includes(job.stage) ? `<button class="button button--quiet" data-note-resume="${esc(job.job_id)}">${job.stage === "publication_retryable" ? "Riprova salvataggio" : "Riprendi generazione"}</button>` : ""}<p data-note-error role="status"></p></section>`).join("") : '<p class="field-note">Nessuna nota ancora. Scegli «Genera note» su una fonte, seleziona le lezioni e rivedi il risultato prima di approvarlo.</p>');
    $$('[data-note-preview-job]').forEach((details) => bindNoteControl(details, "toggle", async () => {
      if (!details.open || materialPreviews.has(details.dataset.notePreviewRevision)) return;
      try {
        const job = await fetchJson(`/api/v1/material-generations/${encodeURIComponent(details.dataset.notePreviewJob)}`);
        const output = array(job.outputs).find((item) => item.revision_id === details.dataset.notePreviewRevision);
        if (output && details.isConnected) {
          const rendered = CardineAI.markdown(output.markdown);
          materialPreviews.set(output.revision_id, rendered);
          patch($(".notes-markdown", details), rendered);
        }
      } catch (error) { $(".notes-markdown", details).textContent = error.message; }
    }));
    $$('[data-note-decision]').forEach((button) => bindNoteControl(button, "click", async () => {
      const decisionScope = notesScope();
      button.disabled = true;
      try {
        // Refresh the canonical sequence immediately before the HUMAN command.
        const job = await fetchJson(`/api/v1/material-generations/${encodeURIComponent(button.dataset.noteJob)}`);
        if (!button.isConnected || decisionScope !== notesScope()) return;
        updateSequence(job.high_water_sequence);
        const receipt = await fetchJson(`/api/v1/material-generations/${encodeURIComponent(button.dataset.noteJob)}/decisions`, {
          method: "POST", body: JSON.stringify(commandPayload({revision_id: button.dataset.noteRevision, decision: button.dataset.noteDecision})),
        });
        updateSequence(receipt.high_water_sequence);
        await refreshMaterialJobs();
      } catch (error) { $("[data-note-error]", button.closest(".notes-job")).textContent = error.message; button.disabled = false; }
    }));
    $$('[data-note-resume]').forEach((button) => bindNoteControl(button, "click", async () => {
      button.disabled = true;
      try {
        await fetchJson(`/api/v1/material-generations/${encodeURIComponent(button.dataset.noteResume)}/resume`, {method: "POST", body: JSON.stringify(commandPayload({}))});
        await refreshMaterialJobs();
      } catch (error) { $("[data-note-error]", button.closest(".notes-job")).textContent = error.message; button.disabled = false; }
    }));
    $$('[data-source-viewer]', $("#material-jobs")).forEach((button) => bindNoteControl(button, "click", () => openSourceViewer(button.dataset.sourceViewer, button.dataset.sourceViewerMode)));
    if (jobs.some((job) => !["proposed", "stale", "failed_terminal", "retryable"].includes(job.stage))) {
      materialPoll = setTimeout(() => refreshMaterialJobs().catch(() => {}), 2500);
    }
  }

  async function pollIndexing(localStatus) {
    const token = ++state.indexingPollToken;
    for (let attempt = 0; attempt < 240 && token === state.indexingPollToken; attempt += 1) {
      const receipt = await fetchJson("/api/v1/indexing/status");
      const indexing = object(receipt.indexing);
      const status = text(indexing.status, "empty");
      const phase = text(indexing.phase, "idle");
      if (localStatus && localStatus.isConnected) {
        localStatus.textContent = status === "indexing"
          ? (phase === "structure" ? "FTS pronto. Strutturo le lezioni…" : "Indicizzo il testo per la ricerca…")
          : status === "queued" ? "Indicizzazione in coda…" : `Indicizzazione ${statusLabel(status)}.`;
      }
      setStatus(status, status === "ready"
        ? "Fonte pronta per ricerca e lezioni"
        : status === "degraded"
          ? "Fonte ricercabile; struttura delle lezioni ridotta"
          : status === "failed"
            ? "Fonte salvata, ma indicizzazione non riuscita"
            : "Indicizzazione della fonte in corso…", { alert: status === "failed" || status === "degraded" });
      if (!["queued", "indexing"].includes(status)) {
        await refreshBootstrapCounts();
        if (state.route === "oggi") await loadRoute("oggi");
        if (state.route === "fonti" && !$("[data-notes-lessons]")) await loadRoute("fonti");
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 750));
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

  /* A failed command reports into the shell's alert region, which is
     outside #view-root and therefore still in the document after the
     refresh that a failure triggers. */
  function showCommandError(error, copy = null) {
    const conflict = error && error.status === 409;
    const retryable = conflict || isTransientTutorError(error);
    const command = retryable && state.lastCommand
      ? Object.freeze({
        ...state.lastCommand,
        payload: Object.freeze({ ...state.lastCommand.payload }),
      })
      : null;
    const actions = command
      ? [{
        label: "Riprova",
        run: () => {
          const traceId = text(error?.payload?.traceId || state.diagnosticTraceId, "");
          return executeCommand(
            command.endpoint,
            command.payload,
            null,
            command.refreshRoute,
            command.requestId,
          );
        },
      }]
      : [];
    const traceId = text(error?.payload?.traceId || state.diagnosticTraceId, "");
    if (traceId) {
      actions.push({
        label: "Apri trace",
        run: () => {
          state.diagnosticTraceId = traceId;
          return loadRoute("impostazioni");
        },
      });
    }
    // A model failure is fixed in Settings, so offer the way there.
    if (!conflict && !traceId && error && MODEL_ERROR_MESSAGES[error.code]) {
      actions.push({ label: "Apri Impostazioni", run: () => loadRoute("impostazioni") });
    }
    showAlert({
      tone: conflict ? "warning" : "danger",
      title: copy?.title || (conflict ? "Lo stato è cambiato" : "Il comando non è stato registrato"),
      detail: copy?.detail || (conflict
        ? "Ho aggiornato la sezione con lo stato corrente: puoi rieseguire lo stesso comando."
        : text(error && error.message, "Il servizio locale non ha risposto.")),
      actions,
    });
  }

  function bindDynamicControls() {
    // The browser's own validation bubble is never the product's error UI.
    $$("form", root).forEach((form) => { form.noValidate = true; });
    $$('[data-entry-form]').forEach((form) => {
      if (form.dataset.bound === "true") return;
      form.dataset.bound = "true";
      form.addEventListener("submit", (event) => { event.preventDefault(); submitTurn(form, form.id === "continuation-entry").catch((error) => showCommandError(error)); });
      const textarea = $("textarea", form);
      if (textarea) {
        // An unsent draft belongs to the conversation, so it comes back with
        // it. A reply to a specific continuation does not carry it over.
        if (form.id !== "continuation-entry" && !text(textarea.value).trim() && state.continuationDraft) {
          textarea.value = state.continuationDraft;
        }
        textarea.addEventListener("input", () => {
          if (form.id !== "continuation-entry") state.continuationDraft = textarea.value;
          resizeComposer(textarea);
        });
        textarea.addEventListener("keydown", (event) => submitComposerFromKeyboard(event, form));
        resizeComposer(textarea);
      }
    });
    $$('[data-lesson-search]').forEach((form) => {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        searchLessons(form).catch((error) => showCommandError(error));
      });
    });
    $$('[data-lesson-select]').forEach((control) => {
      control.addEventListener("click", () => {
        selectLesson(control).catch((error) => showCommandError(error));
      });
    });
    $$('[data-lesson-unpin]').forEach((control) => {
      control.addEventListener("click", () => unpinLesson());
    });
    $$('[data-artifact-bulk]').forEach((form) => {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        submitArtifactBulk(form).catch((error) => showCommandError(error));
      });
    });
    $$('[data-review-retry]').forEach((control) => control.addEventListener("click", () => recoverReviews()));
    $$('[data-review-discard]').forEach((control) => control.addEventListener("click", () => recoverReviews(true)));
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
    $$('[data-generate-notes]').forEach((control) => control.addEventListener("click", async () => {
      const label = control.textContent;
      control.textContent = "Preparazione…";
      try {
        await prepareNotes(control);
        const pane = $("#material-jobs");
        if (!control.isConnected || !pane || state.route !== "fonti") return;
        if ($('[data-notes-lessons]', pane)) {
          $('textarea, input, button', pane)?.focus({ preventScroll: true });
          pane.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
        }
      } finally { control.textContent = label; }
    }));
    if (state.route === "fonti" && $("#material-jobs")) {
      const pane = $("#material-jobs");
      refreshMaterialJobs().catch(() => {
        if (pane !== $("#material-jobs")) return;
        const status = $('[data-material-jobs-status]', pane);
        if (status) status.textContent = "Non riesco a caricare le generazioni. Riapri Fonti per riprovare.";
      });
    }
    $$('[data-provenance]').forEach((control) => control.addEventListener("click", () => openProvenance(control.dataset.provenance)));
    $$('[data-source-viewer]').forEach((control) => control.addEventListener("click", () => openSourceViewer(control.dataset.sourceViewer, control.dataset.sourceViewerMode)));
    $$('[data-retry-route]').forEach((control) => control.addEventListener("click", () => loadRoute(control.dataset.retryRoute)));
    $$('[data-open-turn-trace]').forEach((control) => control.addEventListener("click", () => {
      state.diagnosticTraceId = text(control.dataset.openTurnTrace, state.diagnosticTraceId);
      loadRoute("impostazioni");
    }));
  }

  async function searchLessons(form) {
    const query = text(form.elements.namedItem("query")?.value).trim();
    if (!query) return;
    const request = requestId();
    setBusy(true);
    try {
      const receipt = await fetchJson("/api/v1/lessons/search", {
        method: "POST",
        body: JSON.stringify(commandPayload({ query }, request)),
      });
      state.lesson = { query, candidates: array(receipt.candidates), pin: null, answer: null };
      updateSequence(first(receipt, ["high_water_sequence"], state.highWaterSequence));
      renderOggi(state.viewData || state.bootstrap || {});
      setStatus(text(receipt.status, "ready"), "Ricerca lezione completata");
    } finally {
      setBusy(false);
    }
  }

  async function selectLesson(control) {
    const lesson = state.lesson || {};
    const query = text(lesson.query).trim();
    const candidateId = text(control.dataset.lessonSelect).trim();
    if (!query || !candidateId) return;
    const request = requestId();
    setBusy(true);
    try {
      const receipt = await fetchJson("/api/v1/lessons/select", {
        method: "POST",
        body: JSON.stringify(commandPayload({ query, candidate_id: candidateId }, request)),
      });
      state.lesson = { ...lesson, pin: object(receipt.pin), answer: null };
      updateSequence(first(receipt, ["high_water_sequence"], state.highWaterSequence));
      renderOggi(state.viewData || state.bootstrap || {});
      setStatus("selected", "Lezione allegata alla chat");
    } finally {
      setBusy(false);
    }
  }

  /* Detaching a source is a local choice: nothing was committed by pinning,
     so the browser only drops what it was carrying into the next question. */
  function unpinLesson() {
    if (!state.lesson) return;
    state.lesson = { ...state.lesson, pin: null, answer: null };
    // The attachment is shown both on the home screen and above the chat
    // composer, so the detach has to repaint whichever one is on screen.
    if (state.route === "sessione") renderSessione(state.viewData || {});
    else renderOggi(state.viewData || state.bootstrap || {});
    setStatus("ready", "Fonte allegata rimossa");
  }

  async function submitArtifactBulk(form) {
    const decisions = $$('[data-bulk-revision]:checked', root).map((control) => {
      const revisionId = text(control.dataset.bulkRevision);
      const picker = $$('[data-bulk-decision]', root).find((item) => item.dataset.bulkDecision === revisionId);
      return { revision_id: revisionId, decision: text(picker?.value, "accepted") };
    });
    if (!decisions.length) {
      setStatus("ready", "Seleziona almeno una flashcard da decidere");
      return;
    }
    await executeCommand("/api/v1/artifacts/decisions", { decisions }, form, "proposte");
  }

  /* The floor and the ceiling come from the stylesheet, so the box can
     never disagree with its own CSS and clip text the reader has typed. */
  function resizeComposer(textarea) {
    if (!textarea || !textarea.isConnected) return;
    const styles = window.getComputedStyle(textarea);
    const floor = parseFloat(styles.minHeight) || 0;
    const ceiling = parseFloat(styles.maxHeight) || Infinity;
    textarea.style.height = "auto";
    const content = textarea.scrollHeight;
    const height = Math.min(Math.max(content, floor), ceiling);
    textarea.style.height = `${height}px`;
    textarea.style.overflowY = content > ceiling ? "auto" : "hidden";
    const form = textarea.closest("[data-entry-form]");
    if (form) syncComposerState(form, textarea);
  }

  function syncComposerState(form, textarea) {
    const value = text(textarea.value);
    const hasValue = Boolean(value.trim());
    form.classList.toggle("has-value", hasValue);
    const send = $(".composer__send", form);
    if (send) send.disabled = Boolean(state.pendingTurn) || state.loading || !hasValue;
    const counter = $(".char-counter", form);
    if (counter) {
      const remaining = MAX_ENTRY_CHARS - value.length;
      // Silent until the limit is close enough to matter.
      const near = remaining <= 400;
      counter.hidden = !near;
      counter.textContent = near ? `${remaining} caratteri rimasti` : "";
      counter.dataset.state = remaining < 0 ? "over" : "near";
    }
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
    if (kind === "student-state-import") executeCommand("/api/v1/student-state/import", {}, control, "percorso");
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
    if (kind === "review") rateReview(control);
  }

  function openProvenance(serialized) {
    let source = {};
    try { source = object(JSON.parse(serialized)); } catch (_) { source = {}; }
    const title = first(source, ["title", "name"], "Fonte");
    const quote = first(source, ["excerpt", "quote"], "");
    const fields = [
      ["revisione", first(source, ["revision", "revision_id", "version"], "non dichiarata")],
      ["checksum", first(source, ["checksum_sha256", "checksum", "sha256"], "non dichiarato")],
      ["locatore", first(source, ["locator", "location"], "non dichiarato")],
      ["ruolo", first(source, ["source_role", "role"], "non dichiarato")],
      ["fiducia", first(source, ["trust_level", "trust"], "non dichiarato")],
    ];
    const quoteText = text(quote, "");
    const multilineOrCode = quoteText.includes("\n") || ["code", "source", "snippet"].includes(text(first(source, ["kind", "type", "role"], "")).toLowerCase());
    const excerpt = quoteText
      ? multilineOrCode ? aiCodeBlock({ title, code: quoteText, language: first(source, ["language", "lang"], "testo"), caption: "Estratto della fonte selezionata." }) : `<p class="sheet__quote">${esc(quoteText)}</p>`
      : `<p class="empty-state">Nessun estratto disponibile.</p>`;
    patch($("#drawer-content"), `<div class="sheet__body"><h3 class="state-title">${esc(title)}</h3>${excerpt}<div class="provenance-meta">${fields.map(([key, value]) => `<div class="provenance-meta__row"><span class="provenance-meta__key">${esc(key)}</span><span class="provenance-meta__value">${esc(value)}</span></div>`).join("")}</div></div>`);
    $("#provenance-drawer").showModal();
  }

  async function openSourceViewer(serialized, mode = "sheet") {
    let source = {};
    try { source = object(JSON.parse(serialized)); } catch (_) { source = {}; }
    const sourceId = text(source.source_id);
    const revisionId = text(source.revision_id);
    const viewer_kind = text(source.viewer_kind);
    if (!sourceId || !revisionId || !["pdf", "markdown", "text"].includes(viewer_kind)) return;
    const title = text(source.title, "Fonte del corso");
    const page = Number.isInteger(source.page) && source.page > 0 ? source.page : null;
    const endpoint = `/api/v1/materials/${encodeURIComponent(sourceId)}/revisions/${encodeURIComponent(revisionId)}/content`;
    const inline = mode === "page" && $("#materials-viewer");
    const dialog = inline ? null : $("#source-viewer");
    const content = inline ? $("#materials-viewer-content") : $("#source-viewer-content");
    const requestVersion = ++state.sourceViewerVersion;
    const kindLabel = viewer_kind === "pdf" ? page ? `PDF · pagina ${page}` : "PDF" : "Testo";
    $(inline ? "#materials-viewer-title" : "#source-viewer-title").textContent = title;
    $(inline ? "#materials-viewer-kind" : "#source-viewer-kind").textContent = kindLabel;
    patch(content, '<p class="source-viewer__loading" role="status">Apro la fonte…</p>');
    if (inline) {
      inline.dataset.active = "true";
      $$('.source-row', root).forEach((row) => {
        const selected = row.dataset.sourceId === sourceId && row.dataset.revisionId === revisionId;
        row.classList.toggle("is-selected", selected);
        $('[data-source-viewer]', row)?.setAttribute("aria-pressed", String(selected));
      });
      if (window.matchMedia("(max-width: 1080px)").matches) {
        $("#materials-viewer-title").focus({ preventScroll: true });
        inline.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
      }
    } else if (!dialog.open) {
      dialog.showModal();
    }
    if (viewer_kind === "pdf") {
      const target = `${endpoint}${page ? `#page=${page}` : ""}`;
      patch(content, `<iframe class="source-viewer__frame" src="${esc(target)}" title="${esc(`Documento: ${title}`)}"></iframe>`);
      return;
    }
    try {
      const response = await fetch(endpoint, {
        credentials: "same-origin",
        headers: { Accept: viewer_kind === "markdown" ? "text/markdown" : "text/plain" },
      });
      if (!response.ok) throw new Error("source viewer request failed");
      const documentText = await response.text();
      if (requestVersion !== state.sourceViewerVersion) return;
      const rendered = viewer_kind === "markdown" ? CardineAI.markdown(documentText) : `<pre>${esc(documentText)}</pre>`;
      patch(content, `<article class="source-viewer__markdown ai-answer__markdown">${rendered}</article>`);
    } catch (_) {
      if (requestVersion !== state.sourceViewerVersion) return;
      patch(content, `<div class="source-viewer__error" role="status"><p>Non riesco ad aprire questa fonte. Riprova o verifica che la revisione sia ancora disponibile.</p><button class="button button--quiet" type="button" data-source-viewer-retry>Riprova apertura</button></div>`);
      $('[data-source-viewer-retry]', content).addEventListener("click", () => openSourceViewer(serialized, mode));
    }
  }

  function bindSourceViewerResize() {
    const dialog = $("#source-viewer");
    const handle = $("[data-source-viewer-resize]", dialog);
    if (!dialog || !handle) return;
    const resize = (width, height) => {
      const inset = 24;
      const minWidth = Math.min(360, window.innerWidth - inset);
      const minHeight = Math.min(320, window.innerHeight - inset);
      dialog.style.width = `${Math.max(minWidth, Math.min(width, window.innerWidth - inset))}px`;
      dialog.style.height = `${Math.max(minHeight, Math.min(height, window.innerHeight - inset))}px`;
    };
    handle.addEventListener("pointerdown", (event) => {
      if (window.matchMedia("(max-width: 700px)").matches) return;
      event.preventDefault();
      const start = dialog.getBoundingClientRect();
      const startX = event.clientX;
      const startY = event.clientY;
      handle.setPointerCapture(event.pointerId);
      const move = (moveEvent) => resize(start.width + startX - moveEvent.clientX, start.height + moveEvent.clientY - startY);
      const stop = () => {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", stop);
        handle.removeEventListener("pointercancel", stop);
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", stop);
      handle.addEventListener("pointercancel", stop);
    });
    handle.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
      event.preventDefault();
      const current = dialog.getBoundingClientRect();
      const step = event.shiftKey ? 64 : 24;
      const width = current.width + (event.key === "ArrowLeft" ? step : event.key === "ArrowRight" ? -step : 0);
      const height = current.height + (event.key === "ArrowDown" ? step : event.key === "ArrowUp" ? -step : 0);
      resize(width, height);
    });
  }

  function commandSearchEntries() {
    const routes = Object.entries(ROUTES)
      .filter(([route, config]) => route !== "login" && (!config.private || state.auth.authenticated || (route === "impostazioni" && state.auth.mode !== "private")))
      .map(([route, config]) => ({
        group: "Vai a",
        icon: config.icon,
        label: config.label,
        description: ROUTE_DESCRIPTIONS[route] || "",
        keywords: [config.label, config.heading, route],
        route,
      }));
    const prompts = [
      {
        group: "Chiedi al tutor",
        icon: "icon--chat-circle",
        label: "Spiegami un concetto",
        description: "Dalle basi, usando solo le fonti del corso",
        keywords: ["chat", "spiega", "studio"],
        prompt: "Spiegami un concetto dalle fonti disponibili, partendo dalle basi.",
      },
      {
        group: "Chiedi al tutor",
        icon: "icon--exam",
        label: "Interrogami",
        description: "Una domanda di richiamo attivo alla volta",
        keywords: ["quiz", "verifica", "domanda"],
        prompt: "Interrogami sulle fonti disponibili, una domanda alla volta.",
      },
      {
        group: "Chiedi al tutor",
        icon: "icon--chart-line-up",
        label: "Crea un collegamento clinico",
        description: "Collega l’argomento a un caso e fatti ragionare",
        keywords: ["clinica", "caso", "applicazione"],
        prompt: "Collega questo argomento a un caso clinico e fammi ragionare.",
      },
    ];
    if (state.auth.authenticated) {
      prompts.push({
        group: "Azioni",
        icon: "icon--plus",
        label: "Crea un corso",
        description: "Apre una conferma prima di creare qualsiasi cosa",
        keywords: ["corso", "nuovo", "crea", "sessione"],
        action: "course-creation",
      });
    }
    // A source is identified by what it is, not by its hash.
    const materials = state.route === "fonti" ? array(state.viewData).slice(0, 30).map((item) => {
      const source = object(item);
      const chunks = first(source, ["chunk_count", "chunks", "fragment_count"], null);
      const type = first(source, ["type", "kind", "role"], "materiale");
      return {
        group: "Fonti del corso",
        icon: "icon--book-open",
        label: first(source, ["title", "name", "label"], "Fonte"),
        description: chunks === null ? `${type}` : `${type} · ${chunks} frammenti`,
        keywords: ["fonte", type],
        route: "fonti",
      };
    }) : [];
    return [...routes, ...prompts, ...materials];
  }

  async function selectCommandSearchEntry(entry) {
    const dialog = $("#command-search");
    closeDialog(dialog);
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
      patch(body, `<p>Sessione privata attiva. Le impostazioni non cambiano lo stato del corso.</p><div class="state-actions"><button class="button button--quiet" type="button" data-route="impostazioni">Apri impostazioni</button><button class="button" type="button" data-auth-logout>Esci</button></div>`);
    } else {
      patch(body, `<p>Accedi per aprire le impostazioni e la configurazione del modello.</p><div class="state-actions"><button class="button" type="button" data-route="login">Accedi</button></div>`);
    }
    dialog.showModal();
  }

  /* ------------------------------------------------------------------ */
  /* Validation                                                          */
  /*                                                                     */
  /* Inline, in Italian, anchored to the field it is about, and styled as */
  /* an error — instead of an OS bubble that vanishes on the next click   */
  /* and an orange ring that also means "focused".                       */
  /* ------------------------------------------------------------------ */

  function validationMessage(control) {
    const validity = control.validity;
    if (validity.valueMissing) return "Questo campo è obbligatorio.";
    if (validity.tooLong) return `Massimo ${control.maxLength} caratteri.`;
    if (validity.typeMismatch || validity.badInput) return "Il formato non è valido.";
    return "Controlla questo valore.";
  }

  function setFieldError(control, message) {
    const id = `${control.id || control.name || "field"}-error`;
    const anchor = control.closest(".password-field") || control;
    let node = anchor.parentElement?.querySelector(`[data-field-error="${id}"]`) || null;
    if (!message) {
      node?.remove();
      if (control.getAttribute("aria-describedby") === id) control.removeAttribute("aria-describedby");
      return;
    }
    if (!node) {
      node = document.createElement("p");
      node.className = "field-error";
      node.dataset.fieldError = id;
      node.id = id;
      node.setAttribute("role", "alert");
      anchor.after(node);
    }
    node.textContent = message;
    control.setAttribute("aria-describedby", id);
  }

  function validateForm(form) {
    let firstInvalid = null;
    $$("input, textarea, select", form).forEach((control) => {
      if (control.disabled || control.type === "hidden") return;
      const valid = control.checkValidity();
      control.setAttribute("aria-invalid", valid ? "false" : "true");
      setFieldError(control, valid ? "" : validationMessage(control));
      if (!valid && !firstInvalid) firstInvalid = control;
    });
    if (firstInvalid) firstInvalid.focus({ preventScroll: true });
    return !firstInvalid;
  }

  function bindStaticControls() {
    document.addEventListener("submit", (event) => {
      const form = event.target instanceof HTMLFormElement ? event.target : null;
      if (!form) return;
      // The composer validates itself through the send button's state.
      if (!form.matches("[data-entry-form]") && !validateForm(form)) {
        event.preventDefault();
        return;
      }
      if (form.matches("[data-auth-login]")) {
        event.preventDefault();
        login(form);
      }
      if (form.matches("[data-auth-setup]")) {
        event.preventDefault();
        setupOwner(form);
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
        closeDialog(routeControl.closest("dialog"));
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
        $("#rail-toggle").setAttribute("aria-label", open ? "Chiudi barra laterale" : "Apri barra laterale");
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
      const consentControl = event.target.closest("[data-provider-consent]");
      if (consentControl) {
        event.preventDefault();
        changeProviderConsent(consentControl);
      }
      if (event.target.closest("[data-account-control]")) openAccountMenu();
      if (event.target.closest("[data-auth-logout]")) {
        event.preventDefault();
        closeDialog($("#account-menu"));
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
      if (event.target.closest("[data-close-drawer]")) closeDialog(event.target.closest("dialog"));
      const secretToggle = event.target.closest("[data-toggle-secret]");
      if (secretToggle) {
        event.preventDefault();
        const field = document.getElementById(secretToggle.dataset.toggleSecret);
        if (field) {
          const revealed = field.type === "text";
          field.type = revealed ? "password" : "text";
          secretToggle.textContent = revealed ? "Mostra" : "Nascondi";
          secretToggle.setAttribute("aria-pressed", String(!revealed));
          field.focus({ preventScroll: true });
        }
      }
      if (event.target.closest("[data-study-setup-back]")) {
        event.preventDefault();
        state.studySetup = null;
        renderOggi(state.viewData || state.bootstrap || {});
      }
      if (event.target.closest("#global-alert-dismiss")) {
        event.preventDefault();
        dismissAlert();
      }
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
        closeOpenDialogs();
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
    $("#rail-toggle").setAttribute("aria-label", "Apri barra laterale");
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
    control.dataset.tooltip = label;
  }

  applyRailState();
  window.addEventListener("beforeunload", (event) => {
    if (!state.review.pending.length) return;
    event.preventDefault();
    event.returnValue = "";
  });
  bindStaticControls();
  bindDynamicControls();
  bindSourceViewerResize();
  window.addEventListener("resize", applyRailState);
  loadAuthSession().then((auth) => {
    if (auth.mode === "setup") {
      renderOwnerSetup();
      return;
    }
    if (auth.mode === "private" && !auth.authenticated) {
      renderLogin();
      return;
    }
    return loadBootstrap();
  });
}());
