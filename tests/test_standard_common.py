"""Regression tests for shared standardization metadata and filters."""

from types import SimpleNamespace

import pandas as pd
import pytest

from src.standard.asset_mapping import _asset_in_scope
from src.standard.base import _Standardizer
from src.standard.parameter import ParameterData


def test_asset_scope_uses_common_country_and_canonical_status_filters() -> None:
    options = {
        "country_areas": ["China", "China mainland"],
        "include_statuses": ["in construction"],
    }
    assert _asset_in_scope(
        "China mainland", "In construction", **options
    )
    assert not _asset_in_scope("China", "Operating", **options)
    assert not _asset_in_scope("Japan", "In construction", **options)


def test_source_observed_at_is_derived_from_catalog_version() -> None:
    catalog = pd.DataFrame(
        {"version": ["March 2026", "2022"]},
        index=["gem", "doe"],
    )
    manager = SimpleNamespace(
        datasets={"test": {}},
        raw_data=SimpleNamespace(catalog=catalog),
    )
    standardizer = _Standardizer(manager, "test")
    assert standardizer.source_observed_at("gem") == "2026-03"
    assert standardizer.source_observed_at("doe") == "2022"


def test_parameter_api_counts_and_resolves_one_or_many_assets() -> None:
    parameters = ParameterData(pd.DataFrame([{
        "uid": "source:coal:ramp",
        "name": "ramp_up_pu_per_hour",
        "group": "technical",
        "value": 0.5,
        "unit": "p.u./h",
        "applies_to_dataset": "generator",
        "applies_to_uid": pd.NA,
        "class": "coal",
        "subclass": pd.NA,
        "status": pd.NA,
        "observed_at": "2025",
        "valid_from": pd.NA,
        "valid_to": pd.NA,
        "source_id": "source",
        "source_uid": "coal:ramp",
        "source_version": "2025",
        "selector_json": "{}",
        "quality": "source",
    }]))
    assets = pd.DataFrame([
        {"uid": "gem:1", "class": "coal"},
        {"uid": "gem:2", "class": "gas"},
    ])

    assert parameters.count().loc[0, "records"] == 1
    resolved, candidates = parameters.resolve(
        assets,
        "ramp_up_pu_per_hour",
        dataset_id="generator",
        include_candidates=True,
    )
    assert resolved["target_uid"].tolist() == ["gem:1", "gem:2"]
    assert resolved["match_rank"].tolist() == [0, -1]
    assert resolved["match_result"].tolist() == ["eligible", "ineligible"]
    assert resolved.loc[0, "match_info"] == "parameter_uids=source:coal:ramp"
    assert "mismatch:class" in resolved.loc[1, "match_info"]
    assert set(candidates["target_uid"]) == {"gem:1", "gem:2"}
    assert candidates.columns[:4].tolist() == [
        "selection_status", "match_rank", "rank_m1_ineligible",
        "rank_0_eligible",
    ]
    assert not hasattr(parameters, "report")
    assert not hasattr(parameters, "explain")
    assert not hasattr(parameters, "check_requirements")

    assets.attrs["standard_dataset_id"] = "storage"
    with pytest.raises(ValueError, match="conflicts with dataset_id"):
        parameters.resolve(assets, "ramp_up_pu_per_hour", dataset_id="generator")


def test_parameter_match_result_identifies_decisive_rank() -> None:
    common = {
        "name": "efficiency",
        "group": "technical",
        "unit": "p.u.",
        "applies_to_dataset": "generator",
        "applies_to_uid": pd.NA,
        "class": "coal",
        "subclass": pd.NA,
        "status": pd.NA,
        "observed_at": "2025",
        "valid_from": pd.NA,
        "valid_to": pd.NA,
        "source_version": "2025",
        "selector_json": "{}",
    }
    parameters = ParameterData(pd.DataFrame([
        {
            **common, "uid": "official:efficiency", "value": 0.42,
            "source_id": "official", "source_uid": "efficiency",
            "quality": "source",
        },
        {
            **common, "uid": "generic:efficiency", "value": 0.38,
            "source_id": "generic", "source_uid": "efficiency",
            "quality": "generic",
        },
    ]))
    asset = pd.Series({"uid": "gem:coal", "class": "coal"})

    exact_parameters = ParameterData(pd.DataFrame([{
        **common, "uid": "exact:efficiency", "value": 0.43,
        "applies_to_uid": "gem:coal", "source_id": "exact",
        "source_uid": "efficiency", "quality": "source",
    }]))
    exact = exact_parameters.resolve(
        asset, "efficiency", dataset_id="generator"
    )
    assert exact.loc[0, "match_rank"] == 1
    assert exact.loc[0, "match_result"] == "exact uid"

    quality = parameters.resolve(asset, "efficiency", dataset_id="generator")
    assert quality.loc[0, "match_rank"] == 4
    assert quality.loc[0, "match_result"] == "quality"
    preferred = parameters.resolve(
        asset,
        "efficiency",
        dataset_id="generator",
        source_priority={"generic": -1},
    )
    assert preferred.loc[0, "match_rank"] == 3
    assert preferred.loc[0, "match_result"] == "priority"
    assert preferred.loc[0, "selected_parameter_uid"] == "generic:efficiency"

    parameters.loc[1, ["value", "quality"]] = [0.42, "source"]
    equivalent = parameters.resolve(asset, "efficiency", dataset_id="generator")
    assert equivalent.loc[0, "match_rank"] == 6
    assert equivalent.loc[0, "match_result"] == "equal values"
    parameters.loc[1, "value"] = 0.4
    ambiguous = parameters.resolve(asset, "efficiency", dataset_id="generator")
    assert ambiguous.loc[0, "match_rank"] == 7
    assert ambiguous.loc[0, "match_result"] == "ambiguous"


def test_parameter_selector_context_is_explicit_and_conflict_safe() -> None:
    parameters = ParameterData(pd.DataFrame([{
        "uid": "source:ac:r",
        "name": "r_ohm_per_km",
        "group": "technical",
        "value": 0.03,
        "unit": "ohm/km",
        "applies_to_dataset": "network",
        "applies_to_uid": pd.NA,
        "class": "branch",
        "subclass": pd.NA,
        "status": pd.NA,
        "observed_at": "2025",
        "valid_from": pd.NA,
        "valid_to": pd.NA,
        "source_id": "source",
        "source_uid": "ac:r",
        "source_version": "2025",
        "selector_json": '{"current_type":"AC"}',
        "quality": "source",
    }]))
    asset = {"uid": "osm:1", "class": "branch"}
    resolved = parameters.resolve(
        asset,
        "r_ohm_per_km",
        dataset_id="network",
        selector_context={"current_type": "AC"},
    )
    assert resolved.loc[0, "value"] == 0.03
    with pytest.raises(ValueError, match="conflicts with asset value"):
        parameters.resolve(
            {**asset, "current_type": "DC"},
            "r_ohm_per_km",
            dataset_id="network",
            selector_context={"current_type": "AC"},
        )
