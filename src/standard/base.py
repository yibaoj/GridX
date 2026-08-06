"""Base class shared by dataset-specific standardizers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

import pandas as pd


class _Standardizer:
    def __init__(self, manager: StandardDataManager, dataset_id: str) -> None:
        self.manager = manager
        self.dataset_id = dataset_id
        self.config = manager.datasets[dataset_id]
        self.options = self.config.get("options", {})

    def source(self, source_id: str) -> Path:
        return self.manager.raw_data.get_file(source_id)

    def source_observed_at(self, source_id: str) -> object:
        """Return a partial ISO observation time from raw-source metadata."""

        value = str(
            self.manager.raw_data.catalog.loc[source_id].get("version", "")
        ).strip()
        if not value:
            return pd.NA
        if re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
            return value
        for format_string in ("%B %Y", "%b %Y"):
            try:
                return datetime.strptime(value, format_string).strftime("%Y-%m")
            except ValueError:
                pass
        return pd.NA

    def output(self, name: str | None = None) -> Path:
        filename = (
            self.config["output"] if name is None else self.config["outputs"][name]
        )
        return self.manager.output_root / filename
