"""Raw source catalog validation and selection."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pandas as pd


class SourceCatalog:
    """Validated metadata for immutable raw sources."""

    REQUIRED_COLUMNS = {
        "source_id",
        "domain",
        "provider",
        "acquisition_method",
        "file_format",
        "local_path",
        "source_url",
        "required",
        "description",
        "download_instructions",
    }

    def __init__(self, catalog_path: str | Path) -> None:
        table = pd.read_csv(catalog_path, keep_default_na=False)
        missing = self.REQUIRED_COLUMNS.difference(table.columns)
        if missing:
            raise ValueError(f"Raw data catalog is missing: {sorted(missing)}")
        if table["source_id"].duplicated().any():
            raise ValueError("Raw data catalog contains duplicate source_id values.")
        for source_id, value in table[["source_id", "local_path"]].itertuples(
            index=False
        ):
            path = Path(str(value).strip())
            if not str(value).strip() or path.is_absolute() or ".." in path.parts:
                raise ValueError(
                    f"{source_id} local_path must be a relative path inside data/."
                )
        if "remote_file_name" in table:
            for source_id, value in table[
                ["source_id", "remote_file_name"]
            ].itertuples(index=False):
                name = str(value).strip()
                if name and Path(name).name != name:
                    raise ValueError(
                        f"{source_id} remote_file_name must contain only a filename."
                    )
        table["required"] = pd.Series(
            [
                True
                if str(value).strip().lower() in {"1", "true", "yes"}
                else False
                if str(value).strip().lower() in {"0", "false", "no"}
                else pd.NA
                for value in table["required"]
            ],
            dtype="boolean",
        )
        self.table = table.set_index("source_id", drop=False)

    def select(
        self,
        source_ids: str | Iterable[str] | None = None,
    ) -> pd.DataFrame:
        if source_ids is None:
            return self.table
        selected = [source_ids] if isinstance(source_ids, str) else list(source_ids)
        unknown = set(selected).difference(self.table.index)
        if unknown:
            raise KeyError(f"Unknown source_id values: {sorted(unknown)}")
        return self.table.loc[selected]
