"""Acquisition handlers and checksum validation for raw sources."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

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
    """Acquire one configured source without building standard datasets."""

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

        self.validate(source, path)
        expected = source.get("expected_checksum")
        if expected and checksum(path, source).lower() != expected.lower():
            path.unlink(missing_ok=True)
            raise ValueError(f"Checksum mismatch for {source['source_id']}.")

    @staticmethod
    def cds_credentials_available() -> bool:
        """Check local cdsapi configuration without contacting CDS."""

        try:
            from cdsapi.api import get_url_key_verify

            url, key, _ = get_url_key_verify(None, None, None)
            return bool(url and key)
        except Exception:
            return False

    @classmethod
    def validate(cls, source: pd.Series, path: Path) -> None:
        """Validate a downloaded file without interpreting scientific values."""

        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Raw source file is empty or missing: {path}")
        if str(source["file_format"]).lower() == "zip":
            try:
                with ZipFile(path) as archive:
                    invalid_member = archive.testzip()
            except BadZipFile as error:
                raise ValueError(f"Invalid ZIP archive: {path}") from error
            if invalid_member:
                raise ValueError(
                    f"Corrupt ZIP member {invalid_member!r} in {path}."
                )
        if source["acquisition_method"] == "atlite_cds":
            cls._validate_atlite(source, path)

    @staticmethod
    def _download_url(url: str, path: Path) -> None:
        if not url:
            raise ValueError("A direct download URL is required.")
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.name}.part")
        partial.unlink(missing_ok=True)
        request = Request(
            url,
            headers={"User-Agent": "GridX/1.0"},
        )
        try:
            with urlopen(request) as response, partial.open("wb") as file:
                shutil.copyfileobj(response, file, length=1024 * 1024)
            partial.replace(path)
        except Exception as url_error:
            partial.unlink(missing_ok=True)
            curl = shutil.which("curl")
            if curl is None:
                raise
            try:
                subprocess.run(
                    [
                        curl, "-L", "--fail", "--silent", "--show-error",
                        "-o", str(partial), url,
                    ],
                    check=True,
                )
                partial.replace(path)
            except Exception as curl_error:
                partial.unlink(missing_ok=True)
                raise RuntimeError(
                    f"Direct download failed with Python ({url_error}) and "
                    f"curl ({curl_error})."
                ) from curl_error

    @staticmethod
    def _figshare_url(source: pd.Series) -> str:
        request = Request(
            source["api_url"],
            headers={"User-Agent": "GridX/1.0"},
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

        options = SourceDownloader._atlite_options(source)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.stem}.part{path.suffix}")
        partial.unlink(missing_ok=True)
        try:
            cutout = atlite.Cutout(
                partial,
                module="era5",
                x=slice(*options["x"]),
                y=slice(*options["y"]),
                time=slice(options["start"], options["end"]),
            )
            cutout.prepare(
                features=options["features"],
                monthly_requests=options["monthly_requests"],
                concurrent_requests=options["concurrent_requests"],
                show_progress=options["show_progress"],
            )
            SourceDownloader._validate_atlite(source, partial)
            partial.replace(path)
        except Exception:
            partial.unlink(missing_ok=True)
            raise

    @staticmethod
    def _atlite_options(source: pd.Series) -> dict[str, object]:
        from atlite.datasets import era5

        try:
            options = json.loads(source["options_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("atlite_cds requires valid options_json.") from error
        required = {"start", "end", "x", "y", "features"}
        missing = required.difference(options)
        if missing:
            raise ValueError(f"atlite_cds options are missing: {sorted(missing)}")
        for coordinate in ("x", "y"):
            bounds = options[coordinate]
            if (
                not isinstance(bounds, list)
                or len(bounds) != 2
                or float(bounds[0]) >= float(bounds[1])
            ):
                raise ValueError(
                    f"atlite_cds {coordinate} must be increasing bounds."
                )
            options[coordinate] = [float(bounds[0]), float(bounds[1])]
        features = options["features"]
        if not isinstance(features, list) or not features:
            raise ValueError("atlite_cds features must be a non-empty list.")
        unknown_features = set(features).difference(era5.features)
        if unknown_features:
            raise ValueError(
                f"Unsupported ERA5 features: {sorted(unknown_features)}"
            )
        start, end = pd.Timestamp(options["start"]), pd.Timestamp(options["end"])
        if start > end:
            raise ValueError("atlite_cds start must not be after end.")
        options["start"], options["end"] = str(start), str(end)
        options["monthly_requests"] = bool(options.get("monthly_requests", True))
        options["concurrent_requests"] = bool(
            options.get("concurrent_requests", False)
        )
        options["show_progress"] = bool(options.get("show_progress", True))
        return options

    @staticmethod
    def _validate_atlite(source: pd.Series, path: Path) -> None:
        import atlite

        options = SourceDownloader._atlite_options(source)
        cutout = atlite.Cutout(path)
        try:
            prepared = set(cutout.data.attrs.get("prepared_features", []))
            missing = set(options["features"]).difference(prepared)
            if missing:
                raise ValueError(
                    f"ERA5 cutout is missing features: {sorted(missing)}"
                )
            expected_time = pd.date_range(
                options["start"], options["end"], freq="1h"
            )
            actual_time = pd.DatetimeIndex(cutout.data["time"].values)
            if not actual_time.equals(expected_time):
                raise ValueError(
                    "ERA5 cutout has an incomplete or unexpected hourly time axis."
                )
            tolerance = 0.26
            for coordinate in ("x", "y"):
                values = cutout.data[coordinate].values
                lower, upper = options[coordinate]
                if (
                    values.min() > lower + tolerance
                    or values.max() < upper - tolerance
                ):
                    raise ValueError(
                        "ERA5 cutout does not cover requested "
                        f"{coordinate} bounds."
                    )
        finally:
            cutout.data.close()
