"""Authenticated, lock-independent transport contract for live activity."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from typing import cast

import pytest

from cardine.demo.browser import BrowserSurface, create_server
from cardine.demo.private_access import PrivateAccessController, hash_password
from cardine.demo.ui_application import _turn_activity_status
from cardine.diagnostics.turn_activity import TurnActivityStore
from cardine.hosts import TutorHostRunStatus
from study_agent.domain._validation import JsonObject


class _ActivityApplication:
    mode = "local_repository"

    def __init__(self) -> None:
        self.turn_activity = TurnActivityStore()
        self.lock = threading.Lock()

    def get(self, path: str) -> JsonObject:
        # The application owns this route and checks it before its normal
        # repository-read lock. BrowserSurface only supplies authentication.
        if path.startswith("/api/v1/turns/") and path.endswith("/activity"):
            request_id = path.removeprefix("/api/v1/turns/").removesuffix("/activity")
            return self.turn_activity.snapshot(request_id)
        with self.lock:
            raise AssertionError(f"unexpected locked route: {path}")

    def post(self, path: str, command: object) -> JsonObject:
        del path, command
        raise AssertionError("not used")


def _get(connection: HTTPConnection, path: str) -> tuple[int, object]:
    connection.request("GET", path, headers={"Accept": "application/json"})
    response = connection.getresponse()
    payload = json.loads(response.read().decode("utf-8"))
    return response.status, payload


def test_activity_route_returns_unknown_without_touching_repository_lock() -> None:
    application = _ActivityApplication()
    server = create_server("127.0.0.1", 0, ui_application=application)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = cast(tuple[str, int], server.server_address)
    try:
        application.lock.acquire()
        connection = HTTPConnection(host, port, timeout=1)
        status, payload = _get(connection, "/api/v1/turns/unknown-request/activity")
        connection.close()
        assert status == 200
        assert payload == {
            "schema_version": 2,
            "state": "unknown",
            "records": [],
            "omitted": 0,
        }
    finally:
        if application.lock.locked():
            application.lock.release()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_activity_route_remains_behind_existing_api_authentication() -> None:
    application = _ActivityApplication()
    access = PrivateAccessController(
        hash_password("correct horse battery staple"),
        canonical_origin="http://127.0.0.1:8765",
    )
    surface = BrowserSurface(application, private_access=access)

    with pytest.raises(Exception) as error:
        surface.api_get("/api/v1/turns/request/activity")
    assert getattr(error.value, "status_code", None) == 401

    session = access.login("correct horse battery staple", client_id="test")
    payload = surface.api_get("/api/v1/turns/request/activity", session_token=session.session_token)
    assert payload["state"] == "unknown"
    assert payload["records"] == ()


def test_repository_activity_get_is_independent_of_mutation_lock(tmp_path: Path) -> None:
    """The real application route is checked before normal locked reads."""
    from cardine.demo.ui_application import RepositoryUiApplication
    from tests.integration.demo.TUT08.test_repository_backed_chat import _repository

    root, adapters, _model = _repository(tmp_path)
    application = RepositoryUiApplication(
        root, "cardine-course", "cardine-session", model_adapters=adapters
    )
    application._lock.acquire()
    result: list[object] = []

    def read_activity() -> None:
        try:
            result.append(application.get("/api/v1/turns/lock-free/activity"))
        except Exception as error:  # pragma: no cover - assertion below reports it
            result.append(error)

    worker = Thread(target=read_activity, daemon=True)
    worker.start()
    worker.join(timeout=0.5)
    application._lock.release()
    assert not worker.is_alive(), "activity polling must not wait on the repository lock"
    assert result and not isinstance(result[0], Exception)
    assert result[0] == {
        "schema_version": 2,
        "state": "unknown",
        "records": (),
        "omitted": 0,
    }


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (TutorHostRunStatus.COMPLETED, "done"),
        (TutorHostRunStatus.SUSPENDED, "done"),
        (TutorHostRunStatus.NEEDS_LEARNER_INPUT, "done"),
        (TutorHostRunStatus.ASSISTANT_MESSAGE, "done"),
        (TutorHostRunStatus.FAILED, "failed"),
        (TutorHostRunStatus.TERMINATED, "failed"),
        (TutorHostRunStatus.CANCELLED, "failed"),
        (TutorHostRunStatus.BUDGET_EXHAUSTED, "failed"),
    ],
)
def test_turn_activity_settlement_follows_receipt_status(
    status: TutorHostRunStatus, expected: str
) -> None:
    assert _turn_activity_status(status) == expected


def test_repository_session_read_is_independent_of_long_tutor_mutation_lock(
    tmp_path: Path,
) -> None:
    from cardine.demo.ui_application import RepositoryUiApplication
    from tests.integration.demo.TUT08.test_repository_backed_chat import _repository

    root, adapters, _model = _repository(tmp_path)
    application = RepositoryUiApplication(
        root, "cardine-course", "cardine-session", model_adapters=adapters
    )
    application._lock.acquire()
    result: list[object] = []

    def read_session() -> None:
        try:
            result.append(application.get("/api/v1/session"))
        except Exception as error:  # pragma: no cover - assertion below reports it
            result.append(error)

    worker = Thread(target=read_session, daemon=True)
    worker.start()
    worker.join(timeout=0.5)
    application._lock.release()

    assert not worker.is_alive(), "session reads must not wait for the model call lock"
    assert result and not isinstance(result[0], Exception)
    assert result[0]["session_id"] == "cardine-session"  # type: ignore[index]


def test_progress_message_is_patched_into_only_the_optimistic_bubble_as_escaped_text() -> None:
    javascript = (Path(__file__).parents[3] / "src" / "cardine" / "demo" / "browser.js").read_text(
        encoding="utf-8"
    )

    assert "progress_message" in javascript
    assert "data-optimistic-turn" in javascript
    assert "data-turn-activity" in javascript
    # Raw model text must never be interpolated into HTML. Assigning textContent
    # keeps markup inert inside the optimistic bubble.
    assert "progressNode.textContent = progressMessage" in javascript
    assert "${progressMessage}" not in javascript


def test_retry_poll_survives_previous_terminal_activity() -> None:
    import subprocess

    browser = Path(__file__).parents[3] / "src/cardine/demo/browser.js"
    script = r"""
const fs = require('node:fs'); const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const sync = source.slice(source.indexOf('  function syncAttributes'),
 source.indexOf('\n  function morphElement'));
const poll = source.slice(source.indexOf('  async function pollTurnActivity'),
 source.indexOf('  function restoreFailedTurnDraft'));
const snapshots = [
 {state:'failed',records:[{sequence:1}]},
 {state:'running',records:[{sequence:2}]},
 {state:'running',records:[{sequence:2}]},
 {state:'failed',records:[{sequence:2}]}];
let reads=0; const rendered=[];
let openAfterFresh = null; let openAfterReaderCollapse = null;
const state = {activityPollToken:0,navigationVersion:0,pendingTurn:{
 requestId:'retry',awaitingRetryActivity:true}};
const disclosure = {
 tagName:'DETAILS', dataset:{state:'running'}, open:true,
 classList:{contains:(name)=>name==='ai-tool-chips'}, attrs:{'data-state':'running',open:''},
 get attributes(){return Object.entries(this.attrs).map(([name,value])=>({name,value}));},
 hasAttribute(name){return name in this.attrs;}, getAttribute(name){return this.attrs[name]??null;},
 removeAttribute(name){delete this.attrs[name];},
 setAttribute(name,value){this.attrs[name]=String(value);},
};
const context = {state, root:{}, text:(value)=>value||'', $:()=>({}),
 captureScroll:()=>({}), restoreScroll:()=>{},
 fetchJson:async()=>snapshots[reads++], aiToolChips:(payload)=>({state:payload.state}),
 patch:(_node,value)=>{
   const next={tagName:'DETAILS',dataset:{state:value.state},attrs:{'data-state':value.state},
     get attributes(){return Object.entries(this.attrs).map(([name,value])=>({name,value}));},
     hasAttribute(name){return name in this.attrs;},
     getAttribute(name){return this.attrs[name]??null;}};
   if(value.state==='running')next.attrs.open='';
   context.syncAttributes(disclosure,next); disclosure.dataset.state=value.state;
   rendered.push(value.state);
   if(value.state==='running'&&openAfterFresh===null){openAfterFresh=disclosure.open;disclosure.open=false;disclosure.removeAttribute('open');}
   else if(value.state==='running')openAfterReaderCollapse=disclosure.open;
 },
 window:{setTimeout:(resolve)=>{if(reads===4)state.pendingTurn=null; resolve();}}};
vm.createContext(context);
vm.runInContext(`${sync}; this.syncAttributes=syncAttributes; ${poll}`,context);
context.pollTurnActivity('retry').then(()=>console.log(JSON.stringify({
 reads,rendered,openAfterFresh,openAfterReaderCollapse})));
"""
    result = subprocess.run(
        ["node", "-e", script, str(browser)], check=True, capture_output=True, text=True
    )
    assert json.loads(result.stdout) == {
        "reads": 4,
        "rendered": ["running", "running", "failed"],
        "openAfterFresh": True,
        "openAfterReaderCollapse": False,
    }
