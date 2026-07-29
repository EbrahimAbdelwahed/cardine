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
    piano: { label: "Piano", heading: "Piano", endpoint: null },
    conflitti: { label: "Conflitti", heading: "Conflitti di contesto", endpoint: "/api/v1/context/conflicts" },
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
    revealedReviews: Object.create(null),
    lastCommand: null,
    loading: false,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const root = $("#view-root");

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

  function emptyState(title, copy, kind = "empty") {
    const className = kind === "error" ? "error-state" : kind === "unavailable" ? "unavailable-state" : kind === "loading" ? "loading-state" : "empty-state";
    return `<div class="${className}"><h3 class="state-title">${escapeAttribute(title)}</h3><p>${escapeAttribute(copy)}</p></div>`;
  }

  function button(label, route, className = "button button--quiet") {
    return `<button class="${className}" type="button" data-route="${escapeAttribute(route)}">${escapeAttribute(label)}</button>`;
  }

  function requestId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
    return `ui-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  async function fetchJson(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    if (options.body) headers.set("Content-Type", "application/json");
    const response = await fetch(path, { ...options, headers, cache: "no-store" });
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = null; }
    if (!response.ok) {
      const error = new Error(text(first(object(payload), ["error", "message"]), `Richiesta non riuscita (${response.status}).`));
      error.status = response.status;
      error.payload = payload;
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

  function setBusy(busy) {
    state.loading = busy;
    $$('[data-command]').forEach((control) => { control.disabled = busy; });
    const form = $("#entry-form");
    if (form) $$('button, textarea', form).forEach((control) => { control.disabled = busy; });
  }

  function renderCourse(bootstrap) {
    const course = object(bootstrap.course);
    const session = object(bootstrap.session);
    $("#rail-course").textContent = text(course.title, "corso locale");
    const recent = array(first(bootstrap, ["recent_sessions", "sessions"], []));
    const list = $("#recent-session-list");
    if (recent.length) {
      $("#recent-sessions").hidden = false;
      list.innerHTML = recent.slice(0, 4).map((item) => `<button class="recent-session" type="button" data-session-id="${escapeAttribute(first(item, ["id", "session_id"]))}"><span>${escapeAttribute(first(item, ["title", "label"], "Sessione"))}</span><span class="recent-session__date">${escapeAttribute(first(item, ["updated_at", "when"], ""))}</span></button>`).join("");
    } else {
      $("#recent-sessions").hidden = true;
    }
    document.title = `${text(course.title, "Cardine")} · Cardine`;
    const sessionId = text(session.id, "sessione non selezionata");
    const mode = text(first(bootstrap, ["mode"], "local_repository"), "local_repository");
    $("#runtime-label").textContent = mode === "public_demo"
      ? "modalità dimostrativa · nessun dato personale"
      : "ambiente locale · dati del corso";
    $("#trust-copy").innerHTML = `<p>Corso <strong>${escapeAttribute(text(course.title, "non dichiarato"))}</strong>, sessione <code>${escapeAttribute(sessionId)}</code>. Modalità: <strong>${escapeAttribute(mode)}</strong>. Il browser riceve DTO JSON bounded dal servizio locale e non apre SQLite, file di corso, runtime del provider o credenziali.</p><ul><li>Le mutazioni usano request ID e sequenza osservata.</li><li>Il piano d'esame resta esplicitamente non disponibile finché non esiste un owner canonico.</li><li>Un conflitto di fonte non viene trasformato in conflitto di contesto.</li></ul>`;
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
    $$("[data-route]").forEach((control) => control.classList.toggle("is-active", control.dataset.route === route));
  }

  function setView(route, html) {
    state.route = route;
    navActive(route);
    root.innerHTML = html;
    $("#main-content").focus({ preventScroll: true });
    bindDynamicControls();
  }

  function renderLoading(route) {
    setView(route, `<section class="section-grid"><div class="section-grid__main"><p class="section-kicker">${escapeAttribute(ROUTES[route]?.heading || "Cardine")}</p>${emptyState("Caricamento", "Sto leggendo lo stato canonico dal servizio locale.", "loading")}</div><aside class="section-grid__side"><div class="skeleton-card"></div></aside></section>`);
  }

  function renderError(route, error) {
    const message = error && error.message ? error.message : "Il servizio locale non ha risposto.";
    setStatus("error", "La sezione non è disponibile");
    setView(route, `<section class="section-grid"><div class="section-grid__main"><p class="section-kicker">${escapeAttribute(ROUTES[route]?.heading || "Cardine")}</p>${emptyState("Stato non disponibile", message, "error")}<div class="state-actions"><button class="button" type="button" data-retry-route="${escapeAttribute(route)}">Riprova</button>${button("Torna a Oggi", "oggi")}</div></div><aside class="section-grid__side">${emptyState("Nessuna cancellazione locale", "L'ultimo stato canonico non viene sostituito da dati inventati.")}</aside></section>`);
  }

  async function loadBootstrap() {
    setStatus("working", "Caricamento del corso…");
    try {
      const payload = await fetchJson("/api/v1/bootstrap");
      state.bootstrap = object(payload);
      updateSequence(first(payload, ["high_water_sequence", "sequence"], 0));
      renderCourse(payload);
      updateCounts(payload);
      setStatus(text(payload.shell_status, "ready"), `Corso pronto · ${text(object(payload.course).title, "corso locale")}`);
      await loadRoute("oggi", payload);
    } catch (error) {
      renderError("oggi", error);
      $("#rail-course").textContent = "corso non disponibile · selezione esplicita richiesta";
    }
  }

  async function loadRoute(route, suppliedData = null) {
    if (!ROUTES[route]) route = "oggi";
    if (route === "piano") {
      setStatus("degraded", "Piano non disponibile");
      renderPiano();
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
      state.viewData = payload;
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
      if (route === "conflitti") renderConflitti(payload);
    } catch (error) {
      renderError(route, error);
    }
  }

  function renderUnavailable(route) {
    setView(route, `<section class="section-grid"><section class="section-grid__main"><p class="section-kicker">${escapeAttribute(ROUTES[route]?.heading || "Cardine")}</p>${emptyState("Funzionalità non disponibile", "Questa composizione non espone un owner per la sezione selezionata. Lo stato del resto del corso resta intatto.", "unavailable")}<div class="state-actions">${button("Torna a Oggi", "oggi", "button")}</div></section><aside class="section-grid__side">${emptyState("Nessuna degradazione globale", "La capacità opzionale è isolata dalla sessione principale.")}</aside></section>`);
  }

  function renderOggi(payload) {
    const course = object(first(payload, ["course"], state.bootstrap?.course));
    const session = object(first(payload, ["session"], state.bootstrap?.session));
    const status = text(first(payload, ["shell_status", "status"], state.bootstrap?.shell_status), "ready");
    const counts = object(first(payload, ["counts"], state.bootstrap?.counts));
    const feature = object(first(payload, ["features"], state.bootstrap?.features));
    const focus = [
      { n: count(counts, ["due_reviews", "due_review_count"]), label: "ripasso dovuto", detail: feature.recall === false ? "si attiva con una raccolta di ripasso" : "pronto per il ripasso", route: "ripasso", available: feature.recall !== false },
      { n: count(counts, ["pending_proposals", "proposal_count"]), label: "proposte da decidere", detail: feature.artifacts === false ? "si attiva quando crei materiale" : "attende una tua decisione", route: "proposte", available: feature.artifacts !== false },
      { n: count(counts, ["context_conflicts", "conflict_count"]), label: "preferenze da chiarire", detail: feature.context_resolution === false ? "nessun chiarimento richiesto" : "una scelta resta sempre esplicita", route: "conflitti", available: feature.context_resolution !== false },
    ];
    const suspended = status === "suspended" || status === "needs_learner_input";
    setView("oggi", `<section class="hero"><p class="eyebrow">${escapeAttribute(text(course.title, "corso locale"))}</p><h1>Riprendi il filo dello studio.</h1><p class="hero__lede">Parti da una domanda: Cardine ti accompagna nella sessione senza nascondere ciò che è ancora da configurare.</p>${entryForm("hero-entry", "Da dove vuoi iniziare?", "Cosa vuoi capire?", "button--light")}</section><div class="section-grid"><section class="section-grid__main" aria-labelledby="oggi-heading"><p class="section-kicker">oggi · il tuo spazio</p><h2 class="section-title" id="oggi-heading">Il prossimo passo</h2><ul class="focus-list">${focus.map((item) => `<li class="focus-item ${item.available ? "" : "is-unavailable"}"><span class="focus-item__number">${escapeAttribute(item.n)}</span><div><div class="focus-item__label">${escapeAttribute(item.label)}</div><div class="focus-item__detail">${escapeAttribute(item.detail)}</div></div><span class="focus-item__meta">${item.available ? escapeAttribute(item.route) : "non attivo"}</span><span class="focus-item__button">${item.available ? button("Apri", item.route) : ""}</span></li>`).join("")}</ul>${suspended ? `<div class="side-card" style="margin-top:27px"><p class="section-kicker">sessione in pausa</p><h3 class="side-card__title">Il tutor attende il tuo prossimo dettaglio.</h3><p class="side-card__copy">Puoi riprendere esattamente da dove avevi lasciato.</p><div class="side-card__actions">${button("Riprendi sessione", "sessione", "button")}</div></div>` : ""}</section><aside class="section-grid__side" aria-labelledby="oggi-status-heading"><div class="side-card"><p class="section-kicker">stato del corso</p><h2 id="oggi-status-heading" class="side-card__title">Spazio pronto</h2><p class="side-card__copy">Il tuo spazio di studio è pronto. In questa anteprima le azioni non configurate restano chiaramente disattivate.</p><div class="side-card__actions">${button("Apri sessione", "sessione", "button button--quiet")}</div></div><div class="side-card"><p class="section-kicker">piano</p><h3 class="side-card__title">Collega una data d'esame.</h3><p class="side-card__copy">Quando il piano sarà attivo, Cardine organizzerà qui tappe e priorità.</p></div></aside></div>`);
  }

  function entryForm(id, label, placeholder, buttonClass = "", attributes = "") {
    const textareaId = id === "hero-entry" ? "entry" : `${id}-text`;
    const modeClass = id === "hero-entry" ? "composer--hero" : "composer--session";
    return `<form id="${escapeAttribute(id)}" class="composer ${modeClass}" data-entry-form ${attributes}><label for="${escapeAttribute(textareaId)}">${escapeAttribute(label)}</label><div class="composer__row"><textarea id="${escapeAttribute(textareaId)}" name="learner_entry" maxlength="${MAX_ENTRY_CHARS}" rows="1" required placeholder="${escapeAttribute(placeholder)}"></textarea><button class="button ${buttonClass}" type="submit">Invia</button></div><p class="field-note">Scrivi liberamente: puoi cambiare direzione in ogni momento.</p></form>`;
  }

  function renderSessione(payload) {
    const snapshot = object(first(payload, ["snapshot", "session", "view"], payload));
    const session = object(first(snapshot, ["session"], state.bootstrap?.session));
    const messages = array(
      first(snapshot, ["timeline", "turns", "messages", "conversation", "status_trace"], [])
    );
    const learnerEntry = text(first(snapshot, ["learner_entry"], ""));
    const displayMessages = learnerEntry
      ? [{ role: "learner", content: learnerEntry }, ...messages]
      : messages;
    const status = text(first(snapshot, ["shell_status", "status"], state.bootstrap?.shell_status), "ready");
    const continuation = object(first(snapshot, ["continuation", "pending_continuation"], null));
    const thread = displayMessages.length
      ? displayMessages.map(renderMessage).join("")
      : emptyState(
          "Nessun turno registrato",
          "La sessione non contiene ancora una conversazione canonica."
        );
    const continuationFingerprint = first(continuation, ["fingerprint", "continuation_fingerprint"], "");
    const continuationHtml = continuation && Object.keys(continuation).length ? `<div class="continuation"><p class="section-kicker">richiesta del tutor</p><p class="continuation__prompt">${escapeAttribute(first(continuation, ["prompt", "question", "message"], "Il tutor attende una risposta."))}</p>${continuationFingerprint ? entryForm("continuation-entry", "Risposta", "Scrivi la risposta…", "", `data-fingerprint="${escapeAttribute(continuationFingerprint)}"`) : emptyState("Continuazione non disponibile", "Il servizio non ha restituito il riferimento opaco necessario per riprendere.")}</div>` : "";
    setView("sessione", `<section class="hero hero--session"><p class="eyebrow">sessione · ${escapeAttribute(text(session.id, "id non dichiarato"))}</p><h1>${escapeAttribute(text(first(snapshot, ["title", "topic"], object(state.bootstrap?.course).title), "Sessione di studio"))}</h1><p class="hero__lede">${pill(status)} <span class="meta">sequenza ${escapeAttribute(state.highWaterSequence || "—")}</span></p></section><div class="section-grid"><section class="section-grid__main" aria-labelledby="conversation-heading"><p class="section-kicker">registro canonico</p><h2 class="section-title" id="conversation-heading">Conversazione</h2><div class="session-thread">${thread}</div>${continuationHtml}${entryForm("session-entry", "Scrivi al tutor", "Chiedi un chiarimento…", "")}</section><aside class="section-grid__side" aria-labelledby="material-heading"><div class="side-card"><p class="section-kicker">stato</p><h2 class="side-card__title">${escapeAttribute(statusLabel(status))}</h2><p class="side-card__copy">Un reload rilegge la stessa snapshot dal servizio. Il browser non conserva lo stato canonico.</p></div><div class="side-card" aria-labelledby="material-heading"><p class="section-kicker">fonti nel contesto</p><h3 class="side-card__title" id="material-heading">Apri i materiali</h3><p class="side-card__copy">Citazioni e revisioni provengono dal catalogo del corso selezionato.</p><div class="side-card__actions">${button("Vai a Fonti", "fonti", "button button--quiet")}</div></div></aside></div>`);
  }

  function renderMessage(message) {
    const item = object(message);
    const role = text(first(item, ["role", "speaker", "who"], "assistant"), "assistant").toLowerCase();
    const learner = role === "learner" || role === "user" || role === "student";
    const content = first(item, ["text", "content", "detail", "message"], "");
    const citation = object(first(item, ["citation", "provenance", "source"], null));
    return `<article class="thread-message ${learner ? "thread-message--learner" : "thread-message--assistant"}><p class="thread-message__role">${escapeAttribute(learner ? "tu" : role === "system" ? "sistema" : "tutor")}</p><p class="thread-message__text">${escapeAttribute(text(content, "Messaggio senza testo visualizzabile."))}</p>${Object.keys(citation).length ? `<button class="provenance-chip" type="button" data-provenance='${escapeAttribute(JSON.stringify(citation))}'>fonte · ${escapeAttribute(first(citation, ["locator", "title", "revision"], "metadati disponibili"))}</button>` : ""}</article>`;
  }

  function renderFonti(payload) {
    const materials = array(payload);
    const rows = materials.length ? materials.map((item) => renderSource(item)).join("") : emptyState("Nessuna fonte collegata", "Il catalogo del corso non ha restituito materiali disponibili.");
    setView("fonti", `<section class="section-grid"><section class="section-grid__main" aria-labelledby="material-heading"><p class="section-kicker">libreria · revisioni immutabili</p><h1 class="section-title" id="material-heading">Fonti del corso</h1><p class="section-copy">Solo titolo, revisione, checksum e frammenti bounded vengono mostrati qui. Il browser non apre il corpo grezzo della fonte.</p><ul class="source-list">${rows}</ul></section><aside class="section-grid__side"><div class="side-card"><p class="section-kicker">aggiunta fonte</p><h2 class="side-card__title">Upload non disponibile</h2><p class="side-card__copy">L'ammissione di nuovi file richiede un connettore KB pubblico e verificato; questa superficie non inventa un percorso locale.</p></div></aside></section>`);
  }

  function renderSource(item) {
    const source = object(item);
    const title = first(source, ["title", "name", "label"], "Fonte senza titolo");
    const revision = first(source, ["revision", "revision_id", "version"], "revisione non dichiarata");
    const checksum = first(source, ["checksum_sha256", "checksum", "sha256"], "checksum non dichiarato");
    const type = first(source, ["type", "kind", "role"], "materiale");
    const chunks = first(source, ["chunk_count", "chunks", "fragment_count"], "—");
    return `<li class="source-row"><div><h2 class="source-row__title">${escapeAttribute(title)}</h2><p class="source-row__meta">${escapeAttribute(revision)} · ${escapeAttribute(checksum)}</p></div><div class="source-row__value source-row__type">${escapeAttribute(type)}</div><div class="source-row__value">${escapeAttribute(chunks)}</div><div class="source-row__button"><button class="button button--quiet" type="button" data-provenance='${escapeAttribute(JSON.stringify({ title, revision, checksum, type, excerpt: first(source, ["excerpt", "quote"], "") }))}'>Apri margine</button></div></li>`;
  }

  function renderProposte(payload) {
    const proposals = array(payload);
    const rows = proposals.length ? proposals.map(renderProposal).join("") : emptyState("Nessuna proposta da decidere", "Le proposte generate non vengono considerate accettate finché non esiste una decisione esplicita.");
    setView("proposte", `<section class="section-grid"><section class="section-grid__main" aria-labelledby="proposal-heading"><p class="section-kicker">artifact lifecycle · decisione umana</p><h1 class="section-title" id="proposal-heading">Proposte</h1><p class="section-copy">Generato non significa approvato. Ogni decisione è legata a revisione, sequenza e request ID.</p><div class="card-list">${rows}</div></section><aside class="section-grid__side"><div class="side-card"><p class="section-kicker">regola di stato</p><h2 class="side-card__title">Nessun “accetta tutto”</h2><p class="side-card__copy">Le decisioni restano individuali per mantenere provenance e idempotenza verificabili.</p></div></aside></section>`);
  }

  function renderProposal(item) {
    const proposal = object(item);
    const revisionId = first(proposal, ["revision_id", "id"], "");
    const status = text(first(proposal, ["status", "state"], "pending"), "pending");
    const title = first(proposal, ["front", "title", "question", "label"], "Proposta senza titolo");
    const answer = first(proposal, ["back", "answer", "content", "summary"], "");
    const citation = first(proposal, ["citation", "provenance", "source_ref"], "");
    const pending = !["accepted", "rejected"].includes(status);
    const actions = pending && revisionId ? `<div class="card__actions"><button class="decision-button" type="button" data-command="artifact" data-decision="accepted" data-revision-id="${escapeAttribute(revisionId)}">Accetta</button><button class="decision-button decision-button--reject" type="button" data-command="artifact" data-decision="rejected" data-revision-id="${escapeAttribute(revisionId)}">Rifiuta</button></div>` : pending ? `<p class="card__meta">Decisione non disponibile: manca l'identificativo opaco della revisione.</p>` : "";
    return `<article class="card card--strong"><div class="card__header"><h2 class="card__title">${escapeAttribute(title)}</h2>${pill(status)}</div><p class="card__body">${escapeAttribute(answer || "Contenuto bounded non disponibile.")}</p>${citation ? `<p class="card__meta">provenienza · ${escapeAttribute(typeof citation === "string" ? citation : first(object(citation), ["locator", "title"], "metadati disponibili"))}</p>` : ""}${actions}</article>`;
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
    const options = array(first(assessment, ["options", "choices"], []));
    const selected = state.selectedAnswers[presentationId];
    const status = text(first(assessment, ["status", "state"], "ready"), "ready");
    const choices = options.length ? options.map((option, index) => {
      const choice = object(option);
      const value = first(choice, ["value", "id", "letter"], String(index));
      const label = first(choice, ["label", "text", "value"], value);
      return `<li><button class="choice-button ${selected === value ? "is-selected" : ""}" type="button" data-choice="${escapeAttribute(value)}" data-presentation-id="${escapeAttribute(presentationId)}"><span class="choice-button__letter">${escapeAttribute(first(choice, ["letter"], String.fromCharCode(65 + index)))}</span><span class="choice-button__label">${escapeAttribute(label)}</span></button></li>`;
    }).join("") : emptyState("Opzioni non disponibili", "Questa presentazione non espone scelte bounded.");
    const grade = first(assessment, ["grade", "result", "feedback"], null);
    const attemptAction = presentationId && (attemptId || selected) ? `<div class="card__actions"><button class="button" type="button" data-command="assessment-attempt" data-presentation-id="${escapeAttribute(presentationId)}">Registra tentativo</button>${attemptId ? `<button class="button button--quiet" type="button" data-command="assessment-grade" data-attempt-id="${escapeAttribute(attemptId)}">Richiedi valutazione</button>` : ""}</div>` : !presentationId ? `<p class="card__meta">Azioni non disponibili: manca l'identificativo della presentazione.</p>` : "";
    return `<article class="assessment-card"><div class="card__header"><p class="section-kicker">${escapeAttribute(first(assessment, ["kind", "type"], "presentazione"))}</p>${pill(status)}</div><h2 class="assessment-card__prompt">${escapeAttribute(question)}</h2><ol class="choice-list">${choices}</ol>${grade ? `<p class="assessment-feedback">${escapeAttribute(typeof grade === "string" ? grade : first(object(grade), ["message", "summary", "label"], "Esito disponibile."))}</p>` : ""}${attemptAction}</article>`;
  }

  function renderEvidenze(payload) {
    const evidence = array(payload);
    const rows = evidence.length ? evidence.map((item) => {
      const row = object(item);
      const estimate = first(row, ["estimate", "value", "score"], "—");
      const criterion = first(row, ["criterion", "concept", "label", "name"], "Criterio senza nome");
      const detail = first(row, ["support", "confidence", "status", "detail"], "stima non accompagnata da dettaglio");
      const refs = array(first(row, ["references", "citations", "evidence"], []));
      return `<article class="evidence-row"><div class="evidence-row__estimate">${escapeAttribute(estimate)}</div><div><h2 class="evidence-row__concept">${escapeAttribute(criterion)}</h2><p class="evidence-row__detail">${escapeAttribute(detail)}</p>${refs.length ? `<div class="reference-list">${refs.map((ref) => `<button class="provenance-chip" type="button" data-provenance='${escapeAttribute(JSON.stringify(ref))}'>${escapeAttribute(first(object(ref), ["locator", "title", "revision"], typeof ref === "string" ? ref : "riferimento"))}</button>`).join("")}</div>` : ""}</div></article>`;
    }).join("") : emptyState("Nessuna evidenza proiettata", "Le proiezioni vengono ricostruite dal ledger assessment e dalle fonti disponibili.");
    setView("evidenze", `<section class="section-grid"><section class="section-grid__main" aria-labelledby="evidence-heading"><p class="section-kicker">proiezione replayabile</p><h1 class="section-title" id="evidence-heading">Evidenze per criterio</h1><p class="evidence-note">Queste sono stime di evidenza e riferimenti, non una percentuale generica di padronanza.</p>${rows}</section><aside class="section-grid__side"><div class="side-card"><p class="section-kicker">integrità</p><h2 class="side-card__title">Sequenza ${escapeAttribute(state.highWaterSequence || "—")}</h2><p class="side-card__copy">Le evidenze sono lette al high-water mark restituito dal servizio.</p></div></aside></section>`);
  }

  function renderRipasso(payload) {
    const due = array(payload);
    if (!due.length) {
      setView("ripasso", `<section class="section-grid"><section class="section-grid__main" aria-labelledby="review-heading"><p class="section-kicker">ripasso · coda del giorno</p><h1 class="section-title" id="review-heading">Ripasso</h1>${emptyState("Nessun ripasso dovuto", "La coda recall non ha restituito revisioni accettate da svolgere.")}</section><aside class="section-grid__side"><div class="side-card"><p class="section-kicker">recall</p><h2 class="side-card__title">Stato vuoto</h2><p class="side-card__copy">Un'assenza di card non viene sostituita da una coda inventata.</p></div></aside></section>`);
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
    const reviewActions = revealed && revisionId ? `<div class="rating-list" style="margin-top:22px">${[["again", "Ancora"], ["hard", "Difficile"], ["good", "Bene"], ["easy", "Facile"]].map(([value, label]) => `<button class="rating-button" type="button" data-command="review" data-revision-id="${escapeAttribute(revisionId)}" data-rating="${value}">${label}<span class="rating-button__next">registra decisione</span></button>`).join("")}</div>` : revealed ? emptyState("Decisione non disponibile", "Manca l'identificativo opaco della revisione.", "unavailable") : "";
    setView("ripasso", `<section class="section-grid"><section class="section-grid__main" aria-labelledby="review-heading"><p class="section-kicker">ripasso · coda del giorno</p><h1 class="section-title" id="review-heading">Ripasso</h1><div class="review-card"><div class="review-progress"><span>${escapeAttribute(position)}</span><span class="review-progress__bar">${ticks}</span></div><div class="review-card__front">${escapeAttribute(front)}</div>${revealed ? `<div class="review-card__back">${escapeAttribute(back)}</div>` : revisionId ? `<button class="button" type="button" data-reveal-review="${escapeAttribute(revisionId)}">Mostra risposta</button>` : emptyState("Card senza identificativo", "La rivelazione è sospesa finché il servizio non restituisce la revisione.", "unavailable")}${citation ? `<button class="provenance-chip" type="button" data-provenance='${escapeAttribute(JSON.stringify(citation))}'>fonte · ${escapeAttribute(first(object(citation), ["locator", "title"], typeof citation === "string" ? citation : "metadati"))}</button>` : ""}${reviewActions}</div></section><aside class="section-grid__side"><div class="side-card"><p class="section-kicker">stato</p><h2 class="side-card__title">${escapeAttribute(text(firstCard.status, "due"))}</h2><p class="side-card__copy">La stessa coda viene usata su desktop e mobile. Il browser non calcola la prossima data.</p></div></aside></section>`);
  }

  function renderPiano() {
    setView("piano", `<section class="section-grid"><section class="section-grid__main" aria-labelledby="plan-heading"><p class="section-kicker">progresso · piano</p><h1 class="section-title" id="plan-heading">Piano verso l'esame</h1>${emptyState("Piano non disponibile", "Non esiste un owner canonico per agenda, giorni all'esame, copertura o ritenzione. Nessun dato viene fabbricato.", "unavailable")}</section><aside class="section-grid__side"><div class="side-card"><p class="section-kicker">continua a studiare</p><h2 class="side-card__title">Usa Oggi</h2><p class="side-card__copy">Le azioni reali di sessione, fonti, proposte e ripasso restano disponibili.</p><div class="side-card__actions">${button("Torna a Oggi", "oggi", "button")}</div></div></aside></section>`);
  }

  function renderConflitti(payload) {
    const conflicts = array(payload);
    const rows = conflicts.length ? conflicts.map(renderConflict).join("") : emptyState("Nessun conflitto di contesto", "La proiezione learner-context non segnala divergenze da risolvere.");
    setView("conflitti", `<section class="section-grid"><section class="section-grid__main" aria-labelledby="conflict-heading"><p class="section-kicker">learner context · risoluzione esplicita</p><h1 class="section-title" id="conflict-heading">Conflitti di contesto</h1><p class="section-copy">Qui compaiono solo divergenze del contesto dello studente. Un disaccordo tra fonti resta nella provenienza e non viene risolto da questa schermata.</p><div>${rows}</div></section><aside class="section-grid__side"><div class="side-card"><p class="section-kicker">regola</p><h2 class="side-card__title">Nessuna sovrascrittura per recenza.</h2><p class="side-card__copy">La scelta viene inviata al servizio StudyContext con sequenza attesa.</p></div></aside></section>`);
  }

  function renderConflict(item) {
    const conflict = object(item);
    const kind = text(first(conflict, ["kind", "type"], "context"), "context").toLowerCase();
    const title = first(conflict, ["title", "field", "label"], "Divergenza di contesto");
    const status = text(first(conflict, ["status", "state"], "conflicted"), "conflicted");
    const options = array(first(conflict, ["options", "choices", "values"], []));
    const sourceConflict = kind.includes("source") || kind.includes("evidence");
    return `<article class="conflict-card" data-kind="${sourceConflict ? "source" : "context"}"><div class="conflict-card__heading"><h2 class="conflict-card__title">${escapeAttribute(title)}</h2>${pill(status)}</div>${sourceConflict ? `<p class="conflict-readonly">Disaccordo tra fonti: sola lettura. Serve un contratto di risoluzione della fonte separato.</p>` : options.length ? `<div class="conflict-card__values">${options.map((option) => { const value = object(option); const choice = first(value, ["value", "label", "id"], typeof option === "string" ? option : "scelta"); return `<button class="conflict-option" type="button" data-command="context" data-conflict-kind="${escapeAttribute(first(conflict, ["kind", "type"], "context"))}" data-choice="${escapeAttribute(choice)}"><span class="conflict-option__value">${escapeAttribute(choice)}</span><span class="conflict-option__event">risolvi questo valore</span></button>`; }).join("")}</div>` : `<p class="conflict-readonly">Nessuna scelta di risoluzione bounded disponibile.</p>`}</article>`;
  }

  async function submitTurn(form, continuation = false) {
    const textarea = $("textarea", form);
    const value = text(textarea?.value).trim();
    if (!value || value.length > MAX_ENTRY_CHARS) return;
    const endpoint = continuation ? `/api/v1/session/continuations/${encodeURIComponent(form.dataset.fingerprint || "opaque")}/responses` : "/api/v1/session/turns";
    const payload = continuation ? { response: value } : { content: value };
    await executeCommand(endpoint, payload, form, continuation ? "sessione" : "sessione");
  }

  async function executeCommand(endpoint, payload, form, refreshRoute) {
    const request = state.lastCommand && state.lastCommand.endpoint === endpoint && JSON.stringify(state.lastCommand.payload) === JSON.stringify(payload) ? state.lastCommand.requestId : requestId();
    state.lastCommand = { endpoint, payload, requestId: request, refreshRoute };
    setBusy(true);
    const publicDemo = text(first(state.bootstrap, ["mode"], "")) === "public_demo";
    setStatus(
      "working",
      publicDemo ? "Esecuzione della fixture pubblica…" : "Salvataggio nel registro canonico…"
    );
    try {
      const receipt = await fetchJson(endpoint, { method: "POST", body: JSON.stringify(commandPayload(payload, request)) });
      updateSequence(first(receipt, ["high_water_sequence", "sequence"], state.highWaterSequence));
      const status = text(first(receipt, ["status", "shell_status"], "committed"), "committed");
      setStatus(status, `Comando ${statusLabel(status)}`);
      state.lastCommand = null;
      if (status === "demo_completed" && refreshRoute === "sessione") {
        state.route = "sessione";
        state.viewData = object(receipt.result);
        renderSessione(state.viewData);
        setStatus("recovered", "Anteprima completata · nessun dato personale salvato");
      } else {
        await loadRoute(refreshRoute);
      }
    } catch (error) {
      setBusy(false);
      setStatus(error.status === 409 ? "stale" : "error", error.status === 409 ? "Stato aggiornato: ricarica prima di riprovare" : "Comando non registrato");
      showCommandError(error, form);
    }
    setBusy(false);
  }

  function showCommandError(error, form) {
    if (!form) return;
    const previous = $(".command-error", form);
    if (previous) previous.remove();
    const message = document.createElement("p");
    message.className = "field-note command-error";
    message.setAttribute("role", "alert");
    message.textContent = error.status === 409 ? "La sequenza è cambiata. Aggiorna la sezione e riprova con lo stesso comando." : error.message;
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
      form.addEventListener("submit", (event) => { event.preventDefault(); submitTurn(form, form.id === "continuation-entry").catch((error) => showCommandError(error, form)); });
    });
    $$('[data-reveal-review]').forEach((control) => control.addEventListener("click", () => { state.revealedReviews[control.dataset.revealReview] = true; renderRipasso(state.viewData || {}); }));
    $$('[data-choice]').forEach((control) => control.addEventListener("click", () => { state.selectedAnswers[control.dataset.presentationId] = control.dataset.choice; const assessment = state.viewData; if (assessment) renderVerifiche(assessment); }));
    $$('[data-command]').forEach((control) => control.addEventListener("click", () => commandFromControl(control)));
    $$('[data-provenance]').forEach((control) => control.addEventListener("click", () => openProvenance(control.dataset.provenance)));
    $$('[data-retry-route]').forEach((control) => control.addEventListener("click", () => loadRoute(control.dataset.retryRoute)));
    $$('[data-retry-command]').forEach((control) => control.addEventListener("click", () => { if (state.lastCommand) executeCommand(state.lastCommand.endpoint, state.lastCommand.payload, control.parentElement, state.lastCommand.refreshRoute); }));
  }

  function commandFromControl(control) {
    const kind = control.dataset.command;
    if (kind === "artifact") executeCommand(`/api/v1/artifacts/${encodeURIComponent(control.dataset.revisionId || "")}/decisions`, { decision: control.dataset.decision }, control.closest(".card"), "proposte");
    if (kind === "assessment-attempt") executeCommand(`/api/v1/assessments/${encodeURIComponent(control.dataset.presentationId || "")}/attempts`, { answers: state.selectedAnswers[control.dataset.presentationId] ?? null }, control.closest(".assessment-card"), "verifiche");
    if (kind === "assessment-grade") executeCommand(`/api/v1/assessments/${encodeURIComponent(control.dataset.attemptId || "")}/grade`, {}, control.closest(".assessment-card"), "verifiche");
    if (kind === "review") executeCommand(`/api/v1/recall/${encodeURIComponent(control.dataset.revisionId || "")}/reviews`, { rating: control.dataset.rating }, control.closest(".review-card"), "ripasso");
    if (kind === "context") executeCommand(`/api/v1/context/conflicts/${encodeURIComponent(control.dataset.conflictKind || "context")}/resolve`, { choice: control.dataset.choice }, control.closest(".conflict-card"), "conflitti");
  }

  function openProvenance(serialized) {
    let source = {};
    try { source = object(JSON.parse(serialized)); } catch (_) { source = {}; }
    const title = first(source, ["title", "name"], "Fonte");
    const quote = first(source, ["excerpt", "quote"], "");
    const fields = [["revisione", first(source, ["revision", "revision_id", "version"], "non dichiarata")], ["checksum", first(source, ["checksum_sha256", "checksum", "sha256"], "non dichiarato")], ["locatore", first(source, ["locator", "location"], "non dichiarato")], ["ruolo", first(source, ["role", "trust"], "non dichiarato")]];
    $("#drawer-content").innerHTML = `<div class="drawer__body"><h3 class="state-title">${escapeAttribute(title)}</h3>${quote ? `<p class="drawer__quote">${escapeAttribute(quote)}</p>` : `<p class="empty-state">Nessun estratto bounded disponibile.</p>`}<div class="provenance-meta">${fields.map(([key, value]) => `<div class="provenance-meta__row"><span class="provenance-meta__key">${escapeAttribute(key)}</span><span class="provenance-meta__value">${escapeAttribute(value)}</span></div>`).join("")}</div></div>`;
    $("#provenance-drawer").showModal();
  }

  function bindStaticControls() {
    document.addEventListener("click", (event) => {
      const routeControl = event.target.closest("[data-route]");
      if (routeControl) {
        event.preventDefault();
        const route = routeControl.dataset.route;
        $("#rail").classList.remove("is-open");
        $("#rail-toggle").setAttribute("aria-expanded", "false");
        loadRoute(route);
        return;
      }
      if (event.target.closest("#rail-toggle")) {
        const rail = $("#rail");
        const open = rail.classList.toggle("is-open");
        $("#rail-toggle").setAttribute("aria-expanded", String(open));
      }
      if (event.target.closest("#trust-details")) $("#trust-drawer").showModal();
      if (event.target.closest("[data-close-drawer]")) event.target.closest("dialog").close();
    });
    window.addEventListener("keydown", (event) => {
      if (event.key === "Escape") $$('dialog[open]').forEach((dialog) => dialog.close());
    });
  }

  bindStaticControls();
  loadBootstrap();
}());
