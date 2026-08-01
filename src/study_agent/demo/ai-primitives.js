/*
 * Cardine AI-native primitives.
 *
 * These renderers are intentionally dependency-free and provider-neutral. They
 * turn bounded DTOs into safe HTML strings so the existing shell can compose
 * them without giving the browser ownership of canonical state. Interactions
 * are local presentation helpers; callers may provide explicit callbacks when
 * a command belongs to a canonical service.
 */
(function cardineAiPrimitives(global) {
  "use strict";

  var MAX_TEXT = 12000;

  function value(input, fallback) {
    if (input === null || input === undefined || input === "") return fallback || "";
    if (typeof input === "string" || typeof input === "number" || typeof input === "boolean") {
      return String(input);
    }
    return fallback || "";
  }

  function bounded(input, fallback) {
    var result = value(input, fallback);
    return result.length > MAX_TEXT ? result.slice(0, MAX_TEXT) + "…" : result;
  }

  function escapeText(input) {
    return bounded(input, "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function escapeAttribute(input) {
    return escapeText(input).replace(/`/g, "&#96;");
  }

  function read(item, keys, fallback) {
    if (!item || typeof item !== "object") return fallback || "";
    for (var index = 0; index < keys.length; index += 1) {
      var candidate = item[keys[index]];
      if (candidate !== null && candidate !== undefined && candidate !== "") return candidate;
    }
    return fallback || "";
  }

  function list(input) {
    return Array.isArray(input) ? input : [];
  }

  function arrayText(input, fallback) {
    return list(input).map(function (item) {
      return bounded(read(item, ["label", "title", "name", "value", "text"], item), fallback || "");
    });
  }

  function classNames(base, extra) {
    var suffix = value(extra, "").trim();
    return suffix ? base + " " + escapeAttribute(suffix) : base;
  }

  function statusPill(status, label) {
    var normalized = value(status, "neutral").toLowerCase().replace(/[^a-z0-9_-]/g, "-");
    return '<span class="ai-status" data-tone="' + escapeAttribute(normalized) + '">' +
      escapeText(value(label, normalized.replace(/_/g, " "))) + "</span>";
  }

  function renderLoading(options) {
    var config = options || {};
    var label = bounded(config.label, "Preparazione del tutor");
    var detail = bounded(config.detail, "Raccolgo contesto e fonti consentite.");
    var elapsed = config.elapsed ? '<span class="ai-loading__elapsed">' + escapeText(config.elapsed) + "</span>" : "";
    return '<section class="ai-loading" aria-live="polite" aria-busy="true">' +
      '<div class="ai-loading__mark" aria-hidden="true"><span></span><span></span><span></span></div>' +
      '<div class="ai-loading__copy"><p class="ai-eyebrow">in lavorazione</p><h2>' + escapeText(label) +
      '</h2><p>' + escapeText(detail) + "</p>" + elapsed + "</div></section>";
  }

  function renderThinking(options) {
    var config = options || {};
    var steps = list(config.steps);
    var summary = bounded(config.summary, "Come sto arrivando alla risposta");
    var rows = steps.map(function (step, index) {
      var item = typeof step === "object" && step !== null ? step : { label: step };
      var state = value(read(item, ["status", "state"], index === steps.length - 1 ? "working" : "done"), "neutral");
      return '<li class="ai-thinking__step" data-state="' + escapeAttribute(state) + '">' +
        '<span class="ai-thinking__index" aria-hidden="true">' + escapeText(index + 1) + '</span>' +
        '<span class="ai-thinking__step-copy"><strong>' + escapeText(read(item, ["label", "title", "text"], "Attività")) +
        '</strong>' + (read(item, ["detail", "description"], "") ? '<small>' + escapeText(read(item, ["detail", "description"], "")) + "</small>" : "") +
        '</span>' + statusPill(state, read(item, ["status_label", "status"], state)) + "</li>";
    }).join("");
    return '<details class="ai-thinking" data-ai-disclosure open><summary data-ai-disclosure-trigger><span class="ai-disclosure-caret" aria-hidden="true"></span>' +
      '<span><strong>' + escapeText(summary) + '</strong><small>' + escapeText(config.hint || "Dettaglio dichiarato dal servizio, non una trascrizione interna del modello.") +
      '</small></span></summary><ol class="ai-thinking__steps">' + (rows || '<li class="ai-empty">Nessun passaggio dichiarato.</li>') + "</ol></details>";
  }

  function renderAnswer(options) {
    var config = options || {};
    var answer = bounded(read(config, ["answer", "text", "content"], ""), "");
    var citations = list(config.citations || config.sources);
    var followUps = list(config.followUps || config.follow_ups);
    var citationHtml = citations.map(function (citation, index) {
      var item = typeof citation === "object" && citation !== null ? citation : { label: citation };
      return '<span class="ai-citation" data-citation-index="' + escapeAttribute(index) + '">' +
        '<span class="ai-citation__index" aria-hidden="true">' + escapeText(index + 1) + '</span>' +
        escapeText(read(item, ["label", "title", "locator", "path"], "Fonte")) + "</span>";
    }).join("");
    var followHtml = followUps.map(function (followUp) {
      var label = bounded(read(followUp, ["label", "title", "text"], followUp), "Continua");
      var prompt = bounded(read(followUp, ["prompt", "value", "text"], label), label);
      return '<button class="ai-follow-up" type="button" data-ai-follow-up="' + escapeAttribute(prompt) + '">' + escapeText(label) + "</button>";
    }).join("");
    var reveal = config.reveal === true ? ' data-ai-reveal="true"' : "";
    return '<article class="ai-answer"><div class="ai-answer__header"><p class="ai-eyebrow">risposta</p>' + statusPill(config.status, config.statusLabel) +
      '</div><div class="ai-answer__body">' + (answer ? '<p' + reveal + '>' + escapeText(answer) + "</p>" : '<p class="ai-empty">Nessuna risposta disponibile.</p>') +
      '</div>' + (citationHtml ? '<footer class="ai-answer__sources" aria-label="Fonti">' + citationHtml + "</footer>" : "") +
      (followHtml ? '<div class="ai-answer__follow-ups" aria-label="Continua lo studio">' + followHtml + "</div>" : "") + "</article>";
  }

  function renderApproval(options) {
    var config = options || {};
    var choices = list(config.choices || config.actions);
    var buttons = choices.map(function (choice) {
      var item = typeof choice === "object" && choice !== null ? choice : { label: choice };
      var label = read(item, ["label", "title"], "Continua");
      var action = read(item, ["action", "value", "id"], label);
      var style = value(read(item, ["tone", "variant"], "quiet"), "quiet");
      return '<button type="button" class="ai-button ai-button--' + escapeAttribute(style) + '" data-ai-follow-up="' + escapeAttribute(read(item, ["prompt", "followUp"], action)) + '" data-ai-approval="' + escapeAttribute(action) + '">' + escapeText(label) + "</button>";
    }).join("");
    return '<section class="ai-approval" aria-labelledby="ai-approval-title"><div class="ai-approval__icon" aria-hidden="true">?</div><div class="ai-approval__body"><p class="ai-eyebrow">serve una scelta</p><h2 id="ai-approval-title">' + escapeText(config.title || "Confermi il prossimo passo?") +
      '</h2><p>' + escapeText(config.detail || "La decisione resta tua e verrà inviata al servizio canonico solo quando scegli.") + "</p>" +
      (buttons ? '<div class="ai-approval__actions">' + buttons + "</div>" : "") + "</div></section>";
  }

  function renderToolStack(options) {
    var config = options || {};
    var tools = list(config.tools || config.items);
    var chips = tools.map(function (tool) {
      var item = typeof tool === "object" && tool !== null ? tool : { label: tool };
      var state = value(read(item, ["status", "state"], "ready"), "ready");
      return '<li class="ai-tool-stack__item" data-state="' + escapeAttribute(state) + '"><span class="ai-tool-stack__dot" aria-hidden="true"></span><span><strong>' +
        escapeText(read(item, ["label", "name", "title"], "Capability")) + '</strong><small>' + escapeText(read(item, ["detail", "description"], state)) + "</small></span>" + statusPill(state, read(item, ["status_label", "status"], state)) + "</li>";
    }).join("");
    return '<section class="ai-tool-stack" aria-labelledby="ai-tool-stack-title"><header><div><p class="ai-eyebrow">attività</p><h2 id="ai-tool-stack-title">' + escapeText(config.title || "Strumenti usati") +
      '</h2></div>' + statusPill(config.status, config.statusLabel) + '</header><ul>' + (chips || '<li class="ai-empty">Nessuna attività dichiarata.</li>') + "</ul></section>";
  }

  function renderTaskList(options) {
    var config = options || {};
    var tasks = list(config.tasks || config.items);
    var rows = tasks.map(function (task) {
      var item = typeof task === "object" && task !== null ? task : { label: task };
      var state = value(read(item, ["status", "state"], "open"), "open");
      /* A round status marker, never a square that reads as an empty
         checkbox: none of these rows can be ticked. */
      return '<li class="ai-task-list__row" data-state="' + escapeAttribute(state) + '"><span class="ai-task-list__check" aria-hidden="true"></span><span class="ai-task-list__copy"><strong>' + escapeText(read(item, ["label", "title", "text"], "Attività")) +
        '</strong><small>' + escapeText(read(item, ["detail", "description"], "")) + '</small></span>' + statusPill(state, read(item, ["status_label", "status"], state)) + "</li>";
    }).join("");
    return '<section class="ai-task-list" aria-labelledby="ai-task-list-title"><header><div><p class="ai-eyebrow">lavoro aperto</p><h2 id="ai-task-list-title">' + escapeText(config.title || "Per oggi") + '</h2></div></header><ul>' + (rows || '<li class="ai-empty">Nessun compito aperto.</li>') + "</ul></section>";
  }

  function renderChatPanel(options) {
    var config = options || {};
    var messages = list(config.messages || config.turns);
    var content = messages.map(function (message) {
      var item = typeof message === "object" && message !== null ? message : { content: message };
      var role = value(read(item, ["role", "speaker"], "assistant"), "assistant").toLowerCase();
      var learner = role === "learner" || role === "user" || role === "student";
      return '<article class="ai-chat-panel__message ai-chat-panel__message--' + (learner ? "learner" : "assistant") + '"><p class="ai-chat-panel__role">' + escapeText(learner ? "tu" : config.assistantLabel || "tutor") + '</p><p>' + escapeText(read(item, ["content", "text", "message"], "")) + "</p></article>";
    }).join("");
    var composer = config.composer === false ? "" : '<p class="ai-chat-panel__hint">' + escapeText(config.hint || "Invio invia · Maiusc + Invio va a capo") + "</p>";
    return '<section class="ai-chat-panel" aria-labelledby="ai-chat-panel-title"><header class="ai-chat-panel__header"><div><p class="ai-eyebrow">sessione</p><h2 id="ai-chat-panel-title">' + escapeText(config.title || "Conversazione") +
      '</h2></div>' + statusPill(config.status, config.statusLabel) + '</header><div class="ai-chat-panel__scroll">' + (content || '<p class="ai-empty">La conversazione inizierà qui.</p>') + "</div>" + composer + "</section>";
  }

  function renderRecommendation(options) {
    var config = options || {};
    var prompt = bounded(read(config, ["prompt", "followUp", "value"], config.title || "Inizia"), "Inizia");
    return '<article class="ai-recommendation"><div class="ai-recommendation__accent" aria-hidden="true"></div><div class="ai-recommendation__body"><p class="ai-eyebrow">prossimo passo</p><h2>' + escapeText(config.title || "Una scelta utile per continuare") + '</h2><p>' + escapeText(config.detail || "Basato solo sulle evidenze disponibili nel corso.") +
      '</p><button class="ai-button ai-button--accent" type="button" data-ai-follow-up="' + escapeAttribute(prompt) + '">' + escapeText(config.actionLabel || "Portami lì") + "</button></div></article>";
  }

  function renderContextGrid(options) {
    var config = options || {};
    var cards = list(config.cards || config.items);
    var content = cards.map(function (card, index) {
      var item = typeof card === "object" && card !== null ? card : { title: card };
      var id = "ai-context-" + index;
      return '<article class="ai-context-grid__card"><div class="ai-context-grid__card-header"><span class="ai-context-grid__index" aria-hidden="true">' + escapeText(index + 1) + '</span><h3>' + escapeText(read(item, ["title", "label", "name"], "Contesto")) + '</h3></div><p>' + escapeText(read(item, ["detail", "description", "excerpt", "text"], "Nessun dettaglio disponibile.")) + '</p>' + (read(item, ["source", "provenance", "locator"], "") ? '<details data-ai-disclosure><summary data-ai-disclosure-trigger>Provenienza</summary><p id="' + escapeAttribute(id) + '">' + escapeText(read(item, ["source", "provenance", "locator"], "")) + "</p></details>" : "") + "</article>";
    }).join("");
    return '<section class="ai-context-grid" aria-labelledby="ai-context-grid-title"><header><p class="ai-eyebrow">contesto in uso</p><h2 id="ai-context-grid-title">' + escapeText(config.title || "Quello che sto usando") + '</h2></header><div class="ai-context-grid__items">' + (content || '<p class="ai-empty">Nessun contesto selezionato.</p>') + "</div></section>";
  }

  function renderDiffTable(options) {
    var config = options || {};
    var rows = list(config.rows || config.items);
    var body = rows.map(function (row) {
      var item = typeof row === "object" && row !== null ? row : { label: row };
      return '<tr><th scope="row">' + escapeText(read(item, ["label", "field", "name"], "Campo")) + '</th><td class="ai-diff-table__before">' + escapeText(read(item, ["before", "old", "current"], "—")) + '</td><td class="ai-diff-table__after">' + escapeText(read(item, ["after", "new", "proposed"], "—")) + "</td></tr>";
    }).join("");
    return '<section class="ai-diff-table" aria-labelledby="ai-diff-table-title"><header><div><p class="ai-eyebrow">confronto</p><h2 id="ai-diff-table-title">' + escapeText(config.title || "Cosa cambia") + '</h2></div>' + statusPill(config.status, config.statusLabel) + '</header><div class="ai-table-scroll"><table><thead><tr><th scope="col">Campo</th><th scope="col">Prima</th><th scope="col">Proposta</th></tr></thead><tbody>' + (body || '<tr><td colspan="3" class="ai-empty">Nessuna differenza dichiarata.</td></tr>') + "</tbody></table></div></section>";
  }

  function renderRecordsTable(options) {
    var config = options || {};
    var columns = list(config.columns);
    var records = list(config.records || config.rows);
    var headers = columns.length ? columns : ["label", "status", "detail"];
    var head = headers.map(function (column) { return '<th scope="col">' + escapeText(typeof column === "object" ? read(column, ["label", "name"], "Campo") : column) + "</th>"; }).join("");
    var body = records.map(function (record) {
      var item = typeof record === "object" && record !== null ? record : { label: record };
      return '<tr>' + headers.map(function (column) {
        var key = typeof column === "object" ? read(column, ["key", "name"], "label") : column;
        var cell = read(item, [key], "—");
        return '<td>' + escapeText(typeof cell === "object" ? read(cell, ["label", "title", "value"], "—") : cell) + "</td>";
      }).join("") + "</tr>";
    }).join("");
    return '<section class="ai-records-table" aria-label="' + escapeAttribute(config.title || "Record del corso") + '"><header><div><p class="ai-eyebrow">registro</p><h2>' + escapeText(config.title || "Record del corso") + '</h2></div>' + statusPill(config.status, config.statusLabel) + '</header><div class="ai-table-scroll"><table><thead><tr>' + head + '</tr></thead><tbody>' + (body || '<tr><td colspan="' + escapeAttribute(headers.length) + '" class="ai-empty">Nessun record disponibile.</td></tr>') + "</tbody></table></div></section>";
  }

  /* One container, one heading. The records table is embedded without its
     own surface or title so the filter never renders a card inside an
     identical card carrying the same <h2> twice. */
  function renderFilterTable(options) {
    var config = options || {};
    var table = renderRecordsTable(config)
      .replace(/^<section class="ai-records-table"[^>]*>/, '<div class="ai-filter-table__table ai-records-table">')
      .replace(/<header>[\s\S]*?<\/header>/, "")
      .replace(/<\/section>$/, "</div>");
    var toolbar = '<div class="ai-filter-table__toolbar"><label><span>Cerca nel registro</span><input type="search" data-ai-filter placeholder="Filtra…" autocomplete="off"></label><span class="ai-filter-table__status" data-ai-filter-status role="status"></span></div>';
    return '<section class="ai-filter-table" aria-labelledby="ai-filter-table-title"><header><div><p class="ai-eyebrow">filtro locale</p><h2 id="ai-filter-table-title">' + escapeText(config.title || "Filtra i record") + '</h2></div></header>' + toolbar + table + '</section>';
  }

  function renderSidebarSearch(options) {
    var config = options || {};
    var label = config.placeholder || "Cerca in Cardine";
    return '<button class="ai-sidebar-search" type="button" data-open-command-search aria-label="' + escapeAttribute(label) + '"><span class="icon icon--magnifying-glass" aria-hidden="true"></span><span>' + escapeText(label) + '</span><kbd aria-hidden="true">' + escapeText(config.shortcut || "/") + "</kbd></button>";
  }

  function renderCommandSearch(options) {
    var config = options || {};
    return '<dialog class="modal ai-command-search" aria-label="' + escapeAttribute(config.title || "Cerca in Cardine") + '"><div class="ai-command-search__header"><span class="icon icon--magnifying-glass ai-command-search__icon" aria-hidden="true"></span><input type="search" data-ai-command-input placeholder="' + escapeAttribute(config.placeholder || "Cerca sezioni, fonti o suggerimenti…") + '" autocomplete="off"><kbd>esc</kbd></div><div class="ai-command-search__results" data-ai-command-results role="listbox" aria-label="Risultati"></div><p class="ai-command-search__empty" data-ai-command-empty hidden>Nessun risultato.</p></dialog>';
  }

  function renderInsightDeck(options) {
    var config = options || {};
    var insights = list(config.insights || config.cards);
    var single = insights.length < 2;
    var cards = insights.map(function (insight, index) {
      var item = typeof insight === "object" && insight !== null ? insight : { title: insight };
      var counter = single ? "" : '<p class="ai-eyebrow">' + escapeText(index + 1) + ' di ' + escapeText(insights.length) + "</p>";
      return '<article class="ai-insight-deck__card" data-ai-insight="' + escapeAttribute(index) + '"' + (index ? ' hidden' : '') + '>' + counter + '<h3>' + escapeText(read(item, ["title", "label"], "Osservazione")) + '</h3><p>' + escapeText(read(item, ["detail", "description", "text"], "Nessun dettaglio disponibile.")) + '</p>' + (read(item, ["source", "provenance"], "") ? '<small>' + escapeText(read(item, ["source", "provenance"], "")) + "</small>" : "") + "</article>";
    }).join("");
    /* A single card is not a carousel: the controls are removed, not just
       left enabled at both ends of a list of one. */
    return '<section class="ai-insight-deck" data-single="' + (single ? "true" : "false") + '" aria-labelledby="ai-insight-deck-title"><header><div><p class="ai-eyebrow">segnali utili</p><h2 id="ai-insight-deck-title">' + escapeText(config.title || "Osservazioni del corso") + '</h2></div><div class="ai-insight-deck__controls">' + (single ? "" : '<button type="button" class="ai-icon-button" data-ai-insight-prev aria-label="Evidenza precedente" disabled>Indietro</button><button type="button" class="ai-icon-button" data-ai-insight-next aria-label="Evidenza successiva">Avanti</button>') + '</div></header><div class="ai-insight-deck__viewport" aria-live="polite">' + (cards || '<p class="ai-empty">Nessuna evidenza disponibile.</p>') + '</div></section>';
  }

  function renderCodeBlock(options) {
    var config = options || {};
    var code = bounded(read(config, ["code", "text", "excerpt"], ""), "");
    return '<figure class="ai-code-block"><figcaption><span><p class="ai-eyebrow">estratto</p><strong>' + escapeText(config.title || config.language || "Fonte") + '</strong></span><button class="ai-button ai-button--quiet" type="button" data-ai-copy="' + escapeAttribute(code) + '">Copia</button></figcaption><pre><code>' + escapeText(code) + "</code></pre>" + (config.caption ? '<small>' + escapeText(config.caption) + "</small>" : "") + "</figure>";
  }

  function renderFineTune(options) {
    var config = options || {};
    var styles = list(config.styles || ["Più breve", "Più esempi", "Interrogami"]);
    var controls = styles.map(function (style, index) {
      var item = typeof style === "object" && style !== null ? style : { label: style };
      var label = read(item, ["label", "title", "value"], "Stile");
      var prompt = read(item, ["prompt", "followUp", "value"], label);
      return '<button type="button" class="ai-fine-tune__option' + (index === 0 ? ' is-selected' : '') + '" data-ai-style="' + escapeAttribute(prompt) + '" aria-pressed="' + (index === 0 ? "true" : "false") + '">' + escapeText(label) + "</button>";
    }).join("");
    return '<section class="ai-fine-tune" data-ai-fine-tune aria-labelledby="ai-fine-tune-title"><div class="ai-fine-tune__copy"><p class="ai-eyebrow">risposta su misura</p><h2 id="ai-fine-tune-title">' + escapeText(config.title || "Come vuoi continuare?") + '</h2><p>' + escapeText(config.detail || "Prepara un follow-up locale senza cambiare le impostazioni del modello.") + '</p></div><div class="ai-fine-tune__options" role="group" aria-label="Stile di risposta">' + (controls || '<span class="ai-empty">Nessuna opzione.</span>') + "</div></section>";
  }

  function render(name, options) {
    var renderers = {
      loading: renderLoading,
      thinking: renderThinking,
      answer: renderAnswer,
      approval: renderApproval,
      toolStack: renderToolStack,
      taskList: renderTaskList,
      chatPanel: renderChatPanel,
      recommendation: renderRecommendation,
      contextGrid: renderContextGrid,
      diffTable: renderDiffTable,
      recordsTable: renderRecordsTable,
      filterTable: renderFilterTable,
      sidebarSearch: renderSidebarSearch,
      commandSearch: renderCommandSearch,
      insightDeck: renderInsightDeck,
      codeBlock: renderCodeBlock,
      fineTune: renderFineTune
    };
    if (!renderers[name]) throw new Error("Unknown Cardine AI primitive: " + name);
    return renderers[name](options || {});
  }

  function findComposer(root) {
    return root.querySelector("[data-entry-form] textarea, .composer textarea, textarea");
  }

  function followUp(root, control, callbacks) {
    var prompt = control.getAttribute("data-ai-follow-up") || control.getAttribute("data-ai-prompt") || control.getAttribute("data-ai-style") || control.textContent || "";
    if (callbacks && typeof callbacks.onFollowUp === "function") callbacks.onFollowUp(prompt, control);
    if (callbacks && callbacks.populateComposer === false) return;
    var composer = findComposer(root);
    if (!composer || !prompt.trim()) return;
    composer.value = prompt.trim();
    composer.dispatchEvent(new Event("input", { bubbles: true }));
    composer.focus({ preventScroll: true });
  }

  function copyText(root, control, callbacks) {
    var text = control.getAttribute("data-ai-copy") || "";
    var targetSelector = control.getAttribute("data-ai-copy-target");
    if (targetSelector) {
      var target = root.querySelector(targetSelector);
      if (target) text = target.textContent || "";
    }
    text = text.trim();
    var done = function (success) {
      control.setAttribute("data-copy-state", success ? "copied" : "unavailable");
      control.setAttribute("aria-live", "polite");
      if (callbacks && typeof callbacks.onCopy === "function") callbacks.onCopy(text, success, control);
      setTimeout(function () { control.removeAttribute("data-copy-state"); }, 1600);
    };
    if (!text) return done(false);
    if (global.navigator && global.navigator.clipboard && typeof global.navigator.clipboard.writeText === "function") {
      global.navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
    } else {
      done(false);
    }
  }

  function bindDisclosures(root) {
    var cleanups = [];
    Array.prototype.forEach.call(root.querySelectorAll("[data-ai-disclosure-trigger]"), function (trigger) {
      var details = trigger.closest("details");
      if (!details || trigger.dataset.aiEnhanced === "true") return;
      trigger.dataset.aiEnhanced = "true";
      var onKey = function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          details.open = !details.open;
        }
      };
      trigger.addEventListener("keydown", onKey);
      cleanups.push(function () { trigger.removeEventListener("keydown", onKey); delete trigger.dataset.aiEnhanced; });
    });
    return cleanups;
  }

  function bindFilters(root) {
    var cleanups = [];
    Array.prototype.forEach.call(root.querySelectorAll(".ai-filter-table"), function (table) {
      var input = table.querySelector("[data-ai-filter]");
      if (!input || input.dataset.aiEnhanced === "true") return;
      input.dataset.aiEnhanced = "true";
      var rows = Array.prototype.slice.call(table.querySelectorAll("tbody tr"));
      var status = table.querySelector("[data-ai-filter-status]");
      var apply = function () {
        var query = value(input.value, "").trim().toLowerCase();
        var visible = 0;
        rows.forEach(function (row) {
          var haystack = value(row.getAttribute("data-filter-text"), row.textContent || "").toLowerCase();
          var matches = !query || haystack.indexOf(query) !== -1;
          row.hidden = !matches;
          if (matches) visible += 1;
        });
        if (status) status.textContent = query ? visible + " risultati" : "";
      };
      var onInput = function () { apply(); };
      var onKey = function (event) { if (event.key === "Escape") { input.value = ""; apply(); } };
      input.addEventListener("input", onInput);
      input.addEventListener("keydown", onKey);
      apply();
      cleanups.push(function () { input.removeEventListener("input", onInput); input.removeEventListener("keydown", onKey); delete input.dataset.aiEnhanced; });
    });
    return cleanups;
  }

  function bindInsights(root) {
    var cleanups = [];
    Array.prototype.forEach.call(root.querySelectorAll(".ai-insight-deck"), function (deck) {
      if (deck.dataset.aiEnhanced === "true") return;
      deck.dataset.aiEnhanced = "true";
      var cards = Array.prototype.slice.call(deck.querySelectorAll("[data-ai-insight]"));
      var index = 0;
      var prev = deck.querySelector("[data-ai-insight-prev]");
      var next = deck.querySelector("[data-ai-insight-next]");
      /* The deck does not wrap around, so the controls report the real
         boundaries instead of staying enabled at both ends. */
      var show = function (target) {
        if (!cards.length) return;
        index = Math.min(Math.max(target, 0), cards.length - 1);
        cards.forEach(function (card, cardIndex) {
          card.hidden = cardIndex !== index;
          card.setAttribute("aria-current", cardIndex === index ? "true" : "false");
        });
        if (prev) prev.disabled = index === 0;
        if (next) next.disabled = index === cards.length - 1;
      };
      var onPrev = function () { show(index - 1); };
      var onNext = function () { show(index + 1); };
      if (prev) prev.addEventListener("click", onPrev);
      if (next) next.addEventListener("click", onNext);
      show(0);
      cleanups.push(function () { if (prev) prev.removeEventListener("click", onPrev); if (next) next.removeEventListener("click", onNext); delete deck.dataset.aiEnhanced; });
    });
    return cleanups;
  }

  function bindFineTune(root, callbacks) {
    var cleanups = [];
    Array.prototype.forEach.call(root.querySelectorAll("[data-ai-fine-tune]"), function (card) {
      if (card.dataset.aiEnhanced === "true") return;
      card.dataset.aiEnhanced = "true";
      var controls = Array.prototype.slice.call(card.querySelectorAll("[data-ai-style]"));
      controls.forEach(function (control) {
        var onClick = function () {
          controls.forEach(function (other) { other.classList.toggle("is-selected", other === control); other.setAttribute("aria-pressed", other === control ? "true" : "false"); });
          var style = control.getAttribute("data-ai-style") || "";
          if (callbacks && typeof callbacks.onFineTune === "function") callbacks.onFineTune(style, control);
          if (callbacks && callbacks.populateComposer !== false) followUp(root, control, callbacks);
        };
        control.addEventListener("click", onClick);
        cleanups.push(function () { control.removeEventListener("click", onClick); });
      });
      cleanups.push(function () { delete card.dataset.aiEnhanced; });
    });
    return cleanups;
  }

  function bindAnswerReveals(root) {
    var cleanups = [];
    var reduceMotion = global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches;
    Array.prototype.forEach.call(root.querySelectorAll(".ai-answer__body p[data-ai-reveal]"), function (copy) {
      if (copy.dataset.aiEnhanced === "true" || reduceMotion) return;
      if ((copy.textContent || "").trim().length < 2) return;
      var announcement = copy.ownerDocument.createElement("span");
      var fullAnswer = copy.textContent || "";
      announcement.className = "ai-visually-hidden";
      announcement.setAttribute("role", "status");
      announcement.setAttribute("aria-live", "polite");
      announcement.setAttribute("aria-atomic", "true");
      copy.parentNode.appendChild(announcement);
      copy.dataset.aiEnhanced = "true";
      copy.setAttribute("aria-hidden", "true");
      copy.classList.add("is-revealing");
      var timer = setTimeout(function () {
        copy.classList.remove("is-revealing");
        copy.removeAttribute("aria-hidden");
        announcement.textContent = fullAnswer;
      }, 280);
      cleanups.push(function () {
        clearTimeout(timer);
        copy.classList.remove("is-revealing");
        copy.removeAttribute("aria-hidden");
        if (announcement.parentNode) announcement.parentNode.removeChild(announcement);
        delete copy.dataset.aiEnhanced;
      });
    });
    return cleanups;
  }

  function enhance(root, callbacks) {
    if (!root || typeof root.querySelectorAll !== "function") return function () {};
    var config = callbacks || {};
    var cleanups = [];
    bindDisclosures(root).forEach(function (cleanup) { cleanups.push(cleanup); });
    bindFilters(root).forEach(function (cleanup) { cleanups.push(cleanup); });
    bindInsights(root).forEach(function (cleanup) { cleanups.push(cleanup); });
    bindFineTune(root, config).forEach(function (cleanup) { cleanups.push(cleanup); });
    bindAnswerReveals(root).forEach(function (cleanup) { cleanups.push(cleanup); });
    Array.prototype.forEach.call(root.querySelectorAll("[data-ai-follow-up]"), function (control) {
      if (control.dataset.aiEnhanced === "true") return;
      control.dataset.aiEnhanced = "true";
      var onClick = function () { followUp(root, control, config); };
      control.addEventListener("click", onClick);
      cleanups.push(function () { control.removeEventListener("click", onClick); delete control.dataset.aiEnhanced; });
    });
    Array.prototype.forEach.call(root.querySelectorAll("[data-ai-copy], [data-ai-copy-target]"), function (control) {
      if (control.dataset.aiEnhanced === "true") return;
      control.dataset.aiEnhanced = "true";
      var onClick = function () { copyText(root, control, config); };
      control.addEventListener("click", onClick);
      cleanups.push(function () { control.removeEventListener("click", onClick); delete control.dataset.aiEnhanced; });
    });
    return function destroy() { cleanups.forEach(function (cleanup) { cleanup(); }); };
  }

  function commandSearch(dialog, entries, onSelect) {
    if (!dialog || typeof dialog.querySelector !== "function") return function () {};
    var documentRef = dialog.ownerDocument;
    var input = dialog.querySelector("[data-ai-command-input], #command-search-input, input[type=search]");
    var results = dialog.querySelector("[data-ai-command-results], #command-search-results, [role=listbox]");
    var empty = dialog.querySelector("[data-ai-command-empty], #command-search-empty");
    if (!results && documentRef) { results = documentRef.createElement("div"); results.className = "ai-command-search__results"; results.setAttribute("role", "listbox"); dialog.appendChild(results); }
    var source = list(entries).slice(0, 100);
    var active = 0;
    var filtered = source.slice();
    function normalized(item) {
      return [read(item, ["label", "title", "name"], ""), read(item, ["description", "detail"], ""), arrayText(item && item.keywords).join(" ")].join(" ").toLowerCase();
    }
    function choose(item) {
      if (typeof onSelect === "function") onSelect(item);
    }
    /* A conformant listbox: exactly one tab stop (the input), a roving
       aria-activedescendant, and section headings so fourteen rows do not
       arrive as one undifferentiated list. */
    function paint() {
      if (!results || !documentRef) return;
      results.replaceChildren();
      var lastGroup = null;
      filtered.forEach(function (item, index) {
        var group = value(read(item, ["group", "section"], ""), "");
        if (group && group !== lastGroup) {
          lastGroup = group;
          var heading = documentRef.createElement("p");
          heading.className = "ai-command-search__group";
          heading.id = "ai-command-group-" + index;
          heading.setAttribute("role", "presentation");
          heading.textContent = group;
          results.appendChild(heading);
        }
        var button = documentRef.createElement("button");
        button.type = "button";
        button.className = "ai-command-search__result";
        button.id = "ai-command-option-" + index;
        button.setAttribute("role", "option");
        button.setAttribute("aria-selected", index === active ? "true" : "false");
        button.tabIndex = -1;
        button.dataset.commandIndex = String(index);
        var glyph = documentRef.createElement("span");
        glyph.className = "icon " + value(read(item, ["icon"], "icon--chat-circle"), "icon--chat-circle");
        glyph.setAttribute("aria-hidden", "true");
        button.appendChild(glyph);
        var copy = documentRef.createElement("span");
        copy.className = "ai-command-search__result-copy";
        var label = documentRef.createElement("strong");
        label.textContent = bounded(read(item, ["label", "title", "name"], "Risultato"), "Risultato");
        copy.appendChild(label);
        var detail = read(item, ["description", "detail"], "");
        if (detail) { var note = documentRef.createElement("small"); note.textContent = bounded(detail, ""); copy.appendChild(note); }
        button.appendChild(copy);
        button.addEventListener("click", function () { choose(item); });
        results.appendChild(button);
      });
      var current = results.querySelector('[aria-selected="true"]');
      if (input) {
        if (current) input.setAttribute("aria-activedescendant", current.id);
        else input.removeAttribute("aria-activedescendant");
      }
      if (current && typeof current.scrollIntoView === "function") current.scrollIntoView({ block: "nearest" });
      if (empty) empty.hidden = filtered.length !== 0;
    }
    function refresh(query) {
      var needle = value(query, "").trim().toLowerCase();
      filtered = needle ? source.filter(function (item) { return normalized(item).indexOf(needle) !== -1; }) : source.slice();
      active = Math.min(active, Math.max(filtered.length - 1, 0));
      paint();
    }
    function onInput() { refresh(input ? input.value : ""); }
    function onKey(event) {
      if (event.isComposing || event.keyCode === 229) return;
      if (event.key === "ArrowDown") { event.preventDefault(); active = Math.min(active + 1, Math.max(filtered.length - 1, 0)); paint(); }
      if (event.key === "ArrowUp") { event.preventDefault(); active = Math.max(active - 1, 0); paint(); }
      if (event.key === "Enter" && filtered[active]) { event.preventDefault(); choose(filtered[active]); }
      if (event.key === "Escape" && typeof dialog.close === "function") dialog.close();
    }
    if (input) { input.addEventListener("input", onInput); input.addEventListener("keydown", onKey); }
    paint();
    return function destroy() { if (input) { input.removeEventListener("input", onInput); input.removeEventListener("keydown", onKey); } if (results) results.replaceChildren(); };
  }

  global.CardineAI = Object.freeze({
    escape: escapeText,
    escapeAttribute: escapeAttribute,
    render: render,
    loading: renderLoading,
    thinking: renderThinking,
    answer: renderAnswer,
    approval: renderApproval,
    toolStack: renderToolStack,
    taskList: renderTaskList,
    chatPanel: renderChatPanel,
    recommendation: renderRecommendation,
    contextGrid: renderContextGrid,
    diffTable: renderDiffTable,
    recordsTable: renderRecordsTable,
    filterTable: renderFilterTable,
    sidebarSearch: renderSidebarSearch,
    /* The palette renderer is available through render("commandSearch") and
       commandSearchMarkup; commandSearch itself is the scoped binder. */
    commandSearchMarkup: renderCommandSearch,
    insightDeck: renderInsightDeck,
    codeBlock: renderCodeBlock,
    fineTune: renderFineTune,
    enhance: enhance,
    bind: enhance,
    commandSearch: commandSearch,
    commandSearchBinder: commandSearch
  });
})(typeof window !== "undefined" ? window : globalThis);
