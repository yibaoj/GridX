from pathlib import Path

import pandas as pd

from src.raw import RawDataManager


def _catalog(path: Path, *, local_path: str, remote_name: str) -> Path:
    columns = [
        "source_id", "provider", "acquisition_method", "file_format",
        "local_path", "source_url", "download_url", "api_url",
        "remote_file_name", "version", "checksum_algorithm",
        "expected_checksum", "description", "download_instructions",
        "options_json",
    ]
    pd.DataFrame([{
        "source_id": "source", "provider": "test", "acquisition_method": "manual",
        "file_format": "csv", "local_path": local_path, "source_url": "",
        "download_url": "", "api_url": "", "remote_file_name": remote_name,
        "version": "1", "checksum_algorithm": "", "expected_checksum": "",
        "description": "test", "download_instructions": "place file manually",
        "options_json": "{}",
    }], columns=columns).to_csv(path, index=False)
    return path


def test_check_reports_validated_availability_without_redundant_columns(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    catalog = _catalog(
        tmp_path / "sources.csv", local_path="nested/source.csv",
        remote_name="source.csv",
    )
    (data / "nested").mkdir()
    (data / "nested/source.csv").write_text("value\n1\n", encoding="utf-8")

    report = RawDataManager(catalog, data).check("source")

    assert bool(report.loc["source", "available"])
    assert report.loc["source", "status"] == "available"
    assert not {
        "domain", "required", "exists", "validation_error", "action", "error",
    }.intersection(report.columns)


def test_prepare_normalizes_only_the_exact_remote_filename(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    catalog = _catalog(
        tmp_path / "sources.csv", local_path="nested/canonical.csv",
        remote_name="downloaded.csv",
    )
    candidate = data / "downloaded.csv"
    candidate.write_text("value\n1\n", encoding="utf-8")
    manager = RawDataManager(catalog, data)

    before = manager.check("source")
    report = manager.prepare("source")

    assert before.loc["source", "detected_path"] == str(candidate)
    assert bool(report.loc["source", "available"])
    assert (data / "nested/canonical.csv").is_file()
    assert not candidate.exists()
