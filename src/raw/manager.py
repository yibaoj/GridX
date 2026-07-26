"""Public interface for checking and preparing raw source files."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from .catalog import SourceCatalog
from .download import SourceDownloader, checksum


class RawDataManager:
    """Prepare raw source files without interpreting their contents."""

    def __init__(
        self,
        catalog_path: str | Path = "config/raw_data_sources.csv",
        data_root: str | Path = "data",
    ) -> None:
        self.data_root = Path(data_root).expanduser().resolve()
        self._catalog = SourceCatalog(catalog_path)
        self._downloader = SourceDownloader()

    @property
    def catalog(self) -> pd.DataFrame:
        return self._catalog.table

    def check(
        self,
        source_ids: str | Iterable[str] | None = None,
    ) -> pd.DataFrame:
        """Check one, several, or all configured raw files."""

        rows = []
        for source_id, source in self._catalog.select(source_ids).iterrows():
            path = self.data_root / source["local_path"]
            exists = path.is_file()
            file_checksum = checksum(path, source) if exists else ""
            checksum_ok = (
                not source.get("expected_checksum")
                or file_checksum.lower() == source["expected_checksum"].lower()
            )
            if exists and checksum_ok:
                status = "available"
            elif exists:
                status = "checksum_mismatch"
            elif source["acquisition_method"] == "manual":
                status = "manual_action_required"
            elif (
                source["acquisition_method"] == "atlite_cds"
                and not (Path.home() / ".cdsapirc").exists()
            ):
                status = "credentials_required"
            else:
                status = "ready_to_download"
            rows.append(
                {
                    "source_id": source_id,
                    "domain": source["domain"],
                    "provider": source["provider"],
                    "acquisition_method": source["acquisition_method"],
                    "file_format": source["file_format"],
                    "local_path": str(path),
                    "required": source["required"],
                    "exists": exists,
                    "size_bytes": path.stat().st_size if exists else pd.NA,
                    "checksum": file_checksum,
                    "status": status,
                    "download_instructions": source["download_instructions"],
                }
            )
        return pd.DataFrame(rows).set_index("source_id")

    def prepare(
        self,
        source_ids: str | Iterable[str] | None = None,
        *,
        overwrite: bool = False,
    ) -> pd.DataFrame:
        """Download available sources and report manual or failed acquisitions."""

        selected = self._catalog.select(source_ids)
        actions: dict[str, str] = {}
        errors: dict[str, str] = {}
        for source_id, source in selected.iterrows():
            path = self.data_root / source["local_path"]
            current_status = self.check(source_id).loc[source_id, "status"]
            if current_status == "available" and not overwrite:
                actions[source_id] = "already_available"
                continue
            method = source["acquisition_method"]
            if method == "manual":
                actions[source_id] = "manual_action_required"
                continue
            if method == "atlite_cds" and not (
                Path.home() / ".cdsapirc"
            ).exists():
                actions[source_id] = "credentials_required"
                continue
            try:
                self._downloader.download(source, path)
                actions[source_id] = "downloaded"
            except Exception as error:
                actions[source_id] = "download_failed"
                errors[source_id] = str(error)

        report = self.check(selected.index)
        report["action"] = pd.Series(actions)
        report["error"] = pd.Series(errors, dtype="string")
        return report

    def get_file(self, source_id: str, *, must_exist: bool = True) -> Path:
        """Return one configured path for use by the standard data layer."""

        source = self._catalog.select(source_id).iloc[0]
        path = self.data_root / source["local_path"]
        if must_exist and not path.is_file():
            raise FileNotFoundError(
                f"{source_id} is unavailable. Run prepare({source_id!r}) first."
            )
        return path
