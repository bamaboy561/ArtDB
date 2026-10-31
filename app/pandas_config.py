"""Pandas settings shared by the app, the bot and the scripts. Import before creating frames."""
from __future__ import annotations

import pandas as pd

if int(pd.__version__.split(".")[0]) < 3:
    # Pandas 3 defaults. Arrow-backed strings take about a third of the memory of
    # Python string objects, which decides whether the archive fits the server.
    pd.set_option("future.infer_string", True)
    pd.set_option("mode.copy_on_write", True)
