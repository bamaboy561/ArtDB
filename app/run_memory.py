"""Frees sales tables held by finished script runs of a Streamlit session.

Widget callbacks keep the namespace of the run that created them alive, and with
it every table of that run. On a small server those copies decide whether the
app fits in memory, so each new run clears the tables of the session's previous run.
A quick rerun can start while the previous run is still executing on its own
thread: that run keeps its tables until its thread has ended.
"""
from __future__ import annotations

from collections import OrderedDict
from threading import Lock, Thread, current_thread
from typing import Any

import pandas as pd

MAX_TRACKED_SESSIONS = 16

_lock = Lock()
_previous_runs: "OrderedDict[str, tuple[dict[str, Any], Thread]]" = OrderedDict()
_still_running: list[tuple[dict[str, Any], Thread]] = []


def _clear_tables(namespace: dict[str, Any]) -> None:
    for name, value in list(namespace.items()):
        if isinstance(value, (pd.DataFrame, pd.Series)):
            namespace[name] = None


def release_previous_run(session_id: str, namespace: dict[str, Any]) -> None:
    this_thread = current_thread()
    finished: list[dict[str, Any]] = []
    with _lock:
        candidates = _still_running[:]
        _still_running.clear()
        previous = _previous_runs.pop(session_id, None)
        _previous_runs[session_id] = (namespace, this_thread)
        while len(_previous_runs) > MAX_TRACKED_SESSIONS:
            candidates.append(_previous_runs.popitem(last=False)[1])
        if previous is not None and previous[0] is not namespace:
            candidates.append(previous)
        for run_namespace, run_thread in candidates:
            # Runs on one thread follow each other, so an earlier run there is over.
            if run_thread is this_thread or not run_thread.is_alive():
                finished.append(run_namespace)
            else:
                _still_running.append((run_namespace, run_thread))
    for stale_namespace in finished:
        _clear_tables(stale_namespace)
