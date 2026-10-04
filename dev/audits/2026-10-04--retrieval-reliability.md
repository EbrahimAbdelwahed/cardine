# Audit del retrieval — Biochimica unificato

## Esito

Il caso segnalato è riprodotto. La fonte contiene una spiegazione di base del
meccanismo della triade catalitica a pagina 177; le due query registrate
recuperano la definizione ma perdono i due chunk successivi con il meccanismo.
Il recupero lessicale non è una misura sufficiente della copertura richiesta
dalla domanda. Il benchmark è diagnostico: non è una stima dell'accuratezza
complessiva dell'agente e non dimostra una correzione già attiva nel prodotto.

Richiesta del proprietario del 2026-10-04: audit, needle retrieval e keyword
climbing. Questa richiesta supera l'esclusione dei benchmark dalla precedente
consegna Jev/PageIndex. Il lavoro implementa lo strumento di audit e misura le
strategie; non cambia il runtime, non riavvia il server e non usa provider.

## Ambiente verificato

- Checkout di questa chat: worktree `b2ca/cardine`, branch
  `codex/retrieval-reliability-audit`, base main `1ebf70a`.
- Server locale: porta 8765, checkout `merge-open-prs-8901/cardine`, stesso
  commit `1ebf70a`, configurazione GPT-6 Luna e document index ON.
- Store effettivo: `cardine-wave-a-live`, corso `course-wave-a`; snapshot
  canonico alla sequenza 391. Policy corrente: trust minimo 0, nessun filtro di
  ruolo. Il catalogo include anche tre fonti di appunti ammesse.
- Biochimica unificato: 559 pagine, 1.401.278 caratteri nel testo normalizzato,
  3.676 chunk. Catalogo FTS totale: 3.914 chunk; audit di integrità superato.
- Strategia: `sqlite_fts5_bm25` 1.1.0, tokenizer `unicode61`.
- Fingerprint indice:
  `bc5406b948e9c32b658e4474ef8db9a28fc1527f4e5599dd476d1681b1022c43`.
- Manifest finale:
  `5e141f5a9ebf9ec22f1a1a1662dd1e665a34faf2792b1fa22ff4d01fd9a99356`.

## Riproduzione della triade

Le tracce esistenti contengono le query `triade catalitica chimotripsina` e
`chimotripsina triade catalitica associata`. Una risposta registrata dichiara
che le evidenze identificano i componenti ma non descrivono il meccanismo.

Tutti questi passaggi sono nella stessa sezione e pagina 177. Gli ordinali
sono zero-based; gli offset si riferiscono al testo normalizzato ammesso.

| Passaggio | Ordinale | Intervallo esatto |
| --- | ---: | --- |
| Definizione dei tre residui | 1193 | 448676–449812 |
| Trasferimento dei protoni, estratto come tabella | 1194 | 449814–450212 |
| Attivazione della serina e attacco nucleofilo | 1195 | 450214–450757 |

La prima query restituisce solo 1193, status `sufficient`. La seconda restituisce
1193, 1197 e 1126: anche in questo caso mancano 1194 e 1195. `triade catalitica`
restituisce 1197 e 1193. `istidina protone` recupera 1194;
`serina nucleofilo` recupera 1195. Un'espansione di due ordinali dalla prima
query recupera i tre passaggi senza cambiare query o interrogare un modello.

La fonte spiega il meccanismo di base e rinvia al libro per ulteriori dettagli.
Questo audit non sostiene che il documento contenga ogni stadio del ciclo
catalitico dettagliato. La frammentazione tabellare della conversione PDF
complica il recupero, ma i byte necessari sono ammessi e citabili: non è una
semplice assenza del contenuto dal corpus.

## Benchmark eseguito

21 domande positive su 16 argomenti: sei varianti della triade e 15 casi su
effetto Bohr, HbF, biotina, inibizione competitiva, malonil-CoA, oligomicina,
termogenina, enzima tandem, propionil-CoA, urea, GLUT4, piruvato carbossilasi,
sticky ends, cDNA e CRISPR. I passaggi scelti coprono pagine 42–535. Due
controlli negativi verificano un termine assente e un residuo sintetico assente
in una domanda con keyword realmente presenti. Non è un campionamento casuale
di tutte le 559 pagine; i sei casi della triade pesano sul totale.

Il gold richiede la copertura esatta di tutti i passaggi designati, verificati
contro il catalogo canonico con identità, offset e checksum. Le query reali
registrate sono distinte dalle altre domande naturali, che vengono date
direttamente all'adapter e non attraversano la distillazione del tutor.

| Strategia sperimentale | Positivi completi | Triade | Altri | Negativi corretti |
| --- | ---: | ---: | ---: | ---: |
| Attuale, massimo 8 chunk | 10/21 | 0/6 | 10/15 | 1/2 |
| Query originale + vicini, massimo 8 | 13/21 | 3/6 | 10/15 | 1/2 |
| Keyword climbing, massimo 8 | 14/21 | 4/6 | 10/15 | 1/2 |
| Climbing + vicini, massimo 8 | 14/21 | 4/6 | 10/15 | 1/2 |
| Climbing + vicini, massimo 24 | 21/21 | 6/6 | 15/15 | 1/2 |

Le alternative sono curate manualmente vedendo la fonte: il 21/21 dimostra
recuperabilità, non affidabilità di una policy autonoma. La fusione mantiene
prima i risultati della query originale; se saturano il budget di 8 chunk,
possono impedire l'inclusione di alternative o vicini. Non è stato provato un
reranker e non sono stati calibrati punteggi o soglie semantiche.

Nel baseline dieci domande positive incomplete e un controllo negativo
ricevono comunque `sufficient`; un'ulteriore domanda positiva riceve
`insufficient`. Il controllo negativo con keyword sovrapposte resta errato
anche con 24 chunk: più contesto non risolve l'astensione. Questo non dimostra
che il modello inventi una risposta; misura il limite del segnale lessicale.

Costo totale sui 23 casi: baseline 119 chunk / 60.583 caratteri / 23 query;
climbing con 8 chunk 164 / 91.300 / 86; climbing + vicini con 24 chunk
472 / 219.378 / 86. Il miglior risultato usa circa 3,6 volte i caratteri del
baseline. Questi conteggi descrivono il contesto finale, non token o costi del
provider. In una prova separata, sullo snapshot locale, due query naturali
richiedono circa 5–6 secondi ciascuna, contro 0,25 secondi della query esatta
della triade. Non sono percentili di latenza live: il tempo è dominato dal
fallback per singoli token nella prova osservata.

## Cause verificate nel codice

1. `SQLiteFtsRetrieval.search` restituisce `sufficient` appena esiste un candidato.
   Questo rispetta il contratto lessicale, ma non verifica il target esplicativo.
2. Il ramo AND richiede tutte le keyword informative nel medesimo chunk.
   La definizione ripete il nome completo; i paragrafi successivi usano riferimenti
   contestuali. Il meccanismo resta fuori dal read set.
3. `_recover_empty_retrieval_query` si interrompe se la ricerca iniziale non è
   `insufficient`; tra le alternative usa la prima che trova qualcosa. Non può
   correggere il caso reale di evidenze presenti ma incomplete.
4. PageIndex ON fornisce struttura ai propri consumer. L'ordinario
   `explain_concept` continua a chiamare `source.search`/FTS; non espande
   automaticamente la sezione del risultato.
5. La ricerca per titolo esatto prende i primi chunk in ordine e BM25 può
   privilegiare intestazioni brevi. Sono limiti del codice da includere in
   futuri casi; questo campione non ne misura una frequenza di errore.

## Priorità della correzione successiva

Prima integrare recupero del contesto locale e ricerca aggiuntiva per evidenze
parziali, conservando la domanda e il soggetto dei follow-up. Per la triade
il vicinato canonico risolve già la riproduzione; nel campione generale 8 chunk
restano insufficienti. Servono una selezione migliore del contesto e un budget
anche in caratteri, non un aumento indiscriminato del numero di risultati.

Usare PageIndex come navigazione verso sezioni candidate, poi risolvere sempre
chunk canonici e citazioni esatte. Separare disponibilità lessicale e copertura
della richiesta; una nota di insufficienza della bozza dovrebbe poter innescare
un recupero aggiuntivo limitato. L'autorità su scope, azioni e stato resta host-owned.

Prima di attivare una policy, riservare un insieme di domande non visto durante
la scelta delle keyword, stratificato per pagine/argomenti. Misurare copertura
delle componenti, precisione del contesto, parafrasi/refusi, negativi, scope e
follow-up. Validare separatamente le risposte dell'agente; questo audit non ha
eseguito nuove chiamate al modello né misurato qualità delle spiegazioni.

## Ripetibilità, verifica e consegna

Strumento: `scripts/benchmark_retrieval.py`; contratto e uso in
`docs/evaluation/retrieval-needles.md`. Manifest, snapshot e risultati privati
restano in `.cardine-ui-preview/retrieval-audit/` e non sono versionati.
Il report definitivo è `results-final.json`, il manifest è `needles-final.json`.
100 misure condivise tra due esecuzioni indipendenti coincidono esattamente.

La riproduzione `red.json`, eseguita con `--fail-on-miss`, termina con exit 1:
0/1 completo, un chunk, `sufficient`, entrambi i facet del meccanismo mancanti.
La variante `green.json` usa soltanto vicini della query originale e termina
con exit 0. È un esperimento offline, non una patch del retrieval operativo.

Suite completa fuori dalla sandbox: 2.904 passed, quattro smoke opzionali
skipped. Le limitazioni di socket/worker della sandbox iniziale sono risolte
con l'esecuzione offline autorizzata fuori sandbox. Otto regressioni mirate
passano anche dopo i controlli finali sui tipi. Ruff, mypy (673 file), audit di
ownership (322 righe), build offline e verifica wheel/sdist passano. CI e
review automatica dell'esatto commit pubblicato restano gate distinti; nessun
merge o rollout è autorizzato da questo audit.
