# Log: Tool Chips vuoti nei turni dialogici

Date: 2026-08-13 18:05 CEST
Area: Cardine / tutor diagnostics / chat UI

## Summary

Gli asset e il polling live erano attivi, ma gli ultimi turni reali erano
`assistant_message` o `ask_learner`. Il filtro delle attività registrava solo
capability, retrieval, tool e verifica, quindi la ricevuta conteneva zero record
e il renderer ometteva correttamente il componente.

La correzione registra ora una singola attività `model` sicura per le due
decisioni che producono direttamente una risposta o una domanda. Le etichette
sono statiche (`Elaboro la risposta`, `Preparo una domanda`) e non includono
testo del modello, prompt, ragionamento, query o identificativi canonici.

## Files Changed

- `src/cardine/adapters/model/tutor_decision.py`: osserva le decisioni dialogiche reali.
- `src/cardine/diagnostics/turn_activity.py`: aggiunge due riferimenti al vocabolario chiuso.
- `tests/integration/demo/TUT08/test_tool_chips_activity_contract.py`: copre i due turni pubblici.

## Verification

- Test rosso `assistant_message`: ricevuta con zero record prima del fix.
- Test rosso `ask_learner`: ricevuta con zero record prima del fix.
- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/unit/demo tests/unit/cli tests/unit/diagnostics tests/integration/demo/TUT08/test_tool_chips_activity_contract.py tests/integration/demo/TUT08/test_flashcard_proposals.py`: 195 passed fuori dal sandbox.
- Ruff, sintassi Node e `git diff --check`: passed.

## Notes

- Non sono stati inventati tool call. Il chip `model` descrive solo una decisione realmente restituita da Luna.
- I turni con fonti continuano a mostrare anche lettura, ricerca, capability e verifica.
