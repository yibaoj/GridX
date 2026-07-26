"""Acquisition handlers and checksum validation for raw sources."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from urllib.request import Request, urlopen

import pandas as pd


def checksum(path: Path, source: pd.Series) -> str:
    algorithm = source.get("checksum_algorithm") or ""
    if not algorithm or not source.get("expected_checksum"):
        return ""
    digest = hashlib.new(algorithm)
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class SourceDownloader:
    """Download one configured source without interpreting its contents."""

    def download(self, source: pd.Series, path: Path) -> None:
        method = source["acquisition_method"]
        if method == "direct":
            self._download_url(source["download_url"], path)
        elif method == "figshare_api":
            self._download_url(self._figshare_url(source), path)
        elif method == "atlite_cds":
            self._download_atlite(source, path)
        else:
            raise ValueError(f"Unsupported acquisition_method: {method}")

        expected = source.get("expected_checksum")
        if expected and checksum(path, source).lower() != expected.lower():
            path.unlink(missing_ok=True)
            raise ValueError(f"Checksum mismatch for {source['source_id']}.")

    @staticmethod
    def _download_url(url: str, path: Path) -> None:
        if not url:
            raise ValueError("A direct download URL is required.")
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.name}.part")
        partial.unlink(missing_ok=True)
        request = Request(
            url,
            headers={"User-Agent": "Power-System-Operations/1.0"},
        )
        try:
            with urlopen(request) as response, partial.open("wb") as file:
                shutil.copyfileobj(response, file, length=1024 * 1024)
            partial.replace(path)
        except Exception:
            partial.unlink(missing_ok=True)
            raise

    @staticmethod
    def _figshare_url(source: pd.Series) -> str:
        request = Request(
            source["api_url"],
            headers={"User-Agent": "Power-System-Operations/1.0"},
        )
        with urlopen(request) as response:
            metadata = json.load(response)
        matches = [
            file
            for file in metadata["files"]
            if file["name"] == source["remote_file_name"]
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Figshare file not uniquely found: {source['remote_file_name']}"
            )
        return matches[0]["download_url"]

    @staticmethod
    def _download_atlite(source: pd.Series, path: Path) -> None:
        import atlite

        options = json.loads(source["options_json"])
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.stem}.part{path.suffix}")
        partial.unlink(missing_ok=True)
        try:
            cutout = atlite.Cutout(
                partial,
                module="era5",
                x=slice(*options["x"]),
                y=slice(*options["y"]),
                time=slice(
                    f"{options['year']}-01-01",
                    f"{options['year']}-12-31 23:00",
                ),
            )
            cutout.prepare(
                features=options["features"],
                monthly_requests=True,
                show_progress=True,
            )
            partial.replace(path)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
