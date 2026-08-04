"""Public interface for checking and preparing raw source files."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import warnings

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
            path = self._local_path(source)
            exists = path.is_file()
            downloaded_paths = (
                [] if exists else self._downloaded_paths(source, path)
            )
            checked_path = (
                path
                if exists
                else downloaded_paths[0]
                if len(downloaded_paths) == 1
                else None
            )
            file_checksum = (
                checksum(checked_path, source) if checked_path is not None else ""
            )
            validation_error = ""
            if checked_path is not None:
                try:
                    self._downloader.validate(source, checked_path)
                except Exception as error:
                    validation_error = str(error)
            checksum_ok = (
                not source.get("expected_checksum")
                or file_checksum.lower() == source["expected_checksum"].lower()
            )
            if checked_path is not None and validation_error:
                status = "invalid_source_file"
            elif exists and checksum_ok:
                status = "available"
            elif exists:
                status = "checksum_mismatch"
            elif len(downloaded_paths) > 1:
                status = "multiple_local_candidates"
            elif downloaded_paths and checksum_ok:
                status = "local_path_mismatch"
            elif downloaded_paths:
                status = "candidate_checksum_mismatch"
            elif source["acquisition_method"] == "manual":
                status = "manual_action_required"
            elif (
                source["acquisition_method"] == "atlite_cds"
                and not self._downloader.cds_credentials_available()
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
                    "detected_path": (
                        "; ".join(str(candidate) for candidate in downloaded_paths)
                        or pd.NA
                    ),
                    "required": source["required"],
                    "exists": exists,
                    "size_bytes": (
                        checked_path.stat().st_size
                        if checked_path is not None
                        else pd.NA
                    ),
                    "checksum": file_checksum,
                    "validation_error": validation_error or pd.NA,
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
            path = self._local_path(source)
            downloaded_paths = (
                [] if path.is_file() else self._downloaded_paths(source, path)
            )
            if not path.is_file() and downloaded_paths:
                if len(downloaded_paths) > 1:
                    actions[source_id] = "multiple_local_candidates"
                    errors[source_id] = (
                        "More than one file matches remote_file_name: "
                        + ", ".join(str(candidate) for candidate in downloaded_paths)
                    )
                    continue
                candidate = downloaded_paths[0]
                expected = source.get("expected_checksum")
                if (
                    expected
                    and checksum(candidate, source).lower() != expected.lower()
                ):
                    actions[source_id] = "candidate_checksum_mismatch"
                    errors[source_id] = f"Checksum mismatch for {candidate}."
                    continue
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    candidate.replace(path)
                    actions[source_id] = "normalized_local_path"
                except Exception as error:
                    actions[source_id] = "path_normalization_failed"
                    errors[source_id] = str(error)
                continue
            current_status = self.check(source_id).loc[source_id, "status"]
            if current_status == "available" and not overwrite:
                actions[source_id] = "already_available"
                continue
            method = source["acquisition_method"]
            if method == "manual":
                actions[source_id] = "manual_action_required"
                continue
            if (
                method == "atlite_cds"
                and not self._downloader.cds_credentials_available()
            ):
                actions[source_id] = "credentials_required"
                continue
            try:
                self._downloader.download(source, path)
                actions[source_id] = "downloaded"
            except Exception as error:
                actions[source_id] = "download_failed"
                errors[source_id] = str(error)
                warnings.warn(
                    f"{source_id} download failed: {error}",
                    RuntimeWarning,
                    stacklevel=2,
                )

        report = self.check(selected.index)
        report["action"] = pd.Series(actions)
        report["error"] = pd.Series(errors, dtype="string")
        return report

    def get_file(self, source_id: str, *, must_exist: bool = True) -> Path:
        """Return one configured path for use by the standard data layer."""

        source = self._catalog.select(source_id).iloc[0]
        path = self._local_path(source)
        if must_exist and not path.is_file():
            downloaded_paths = self._downloaded_paths(source, path)
            hint = (
                f" Found file at {downloaded_paths[0]}; run prepare({source_id!r}) "
                "to normalize its path."
                if len(downloaded_paths) == 1
                else ""
            )
            raise FileNotFoundError(
                f"{source_id} is unavailable at {path}.{hint}"
            )
        return path

    def _local_path(self, source: pd.Series) -> Path:
        return self.data_root / str(source["local_path"])

    def _downloaded_paths(self, source: pd.Series, local_path: Path) -> list[Path]:
        remote_name = str(source.get("remote_file_name") or "").strip()
        if not remote_name:
            return []
        candidates = (local_path.parent / remote_name, self.data_root / remote_name)
        return list(dict.fromkeys(
            candidate
            for candidate in candidates
            if candidate != local_path and candidate.is_file()
        ))
