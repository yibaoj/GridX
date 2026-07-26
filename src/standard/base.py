"""Base class shared by dataset-specific standardizers."""

from __future__ import annotations

from pathlib import Path


class _Standardizer:
    def __init__(self, manager: StandardDataManager, dataset_id: str) -> None:
        self.manager = manager
        self.dataset_id = dataset_id
        self.config = manager.datasets[dataset_id]
        self.options = self.config.get("options", {})

    def source(self, source_id: str) -> Path:
        return self.manager.raw_data.get_file(source_id)

    def output(self, name: str | None = None) -> Path:
        filename = (
            self.config["output"] if name is None else self.config["outputs"][name]
        )
        return self.manager.output_root / filename
