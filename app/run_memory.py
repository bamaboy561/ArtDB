"""Frees sales tables held by finished script runs of a Streamlit session.

Widget callbacks keep the namespace of the run that created them alive, and with
it every table of that run. On a small server those copies decide whether the
app fits in memory, so each new run clears the tables of the session's previous run.
"""
from __future__ import annotations

from collections import OrderedDict
from threading import Lock
from typing import Any

import pandas as pd

MAX_TRACKED_SESSIONS = 16

_lock = Lock()
_previous_namespaces: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def _clear_tables(namespace: dict[str, Any]) -> None:
    for name, value in list(namespace.items()):
        if isinstance(value, (pd.DataFrame, pd.Series)):
            namespace[name] = None


def release_previous_run(session_id: str, namespace: dict[str, Any]) -> None:
    evicted: list[dict[str, Any]] = []
    with _lock:
        previous = _previous_namespaces.pop(session_id, None)
        _previous_namespaces[session_id] = namespace
        while len(_previous_namespaces) > MAX_TRACKED_SESSIONS:
            evicted.append(_previous_namespaces.popitem(last=False)[1])
    if previous is not None and previous is not namespace:
        evicted.append(previous)
    for stale_namespace in evicted:
        _clear_tables(stale_namespace)
