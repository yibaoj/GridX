"""Date precision, lifecycle precedence, and persistent network additions."""

from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from src.case.manager import _filter_assets
from src.case.manager import PowerSystemCaseManager
from src.case.network import filter_network
from src.mapping.manager import SpatiotemporalMappingManager
from src.mapping.model import MappedNetwork
from src.standard.model import StandardNetwork
from src.standard.network_overlay import append_network_sources
from src.standard.parameter import _active_at
from src.standard.schema import REQUIRED_COLUMNS, _finalize_frame
from src.standard.time import active_at, network_validity, valid_time
from src.standard.generator import _GeneratorStandardizer
from src.standard.storage import _StorageStandardizer, _doe_time
from pathlib import Path


@pytest.mark.parametrize("at,expected", [("2024-12-31", True), ("2025", False),
                                        ("2020", True), ("2019", False)])
def test_shared_date_precision(at, expected):
    row = pd.Series(dict(valid_from="2020-06", valid_to="2024"))
    assert active_at(row, at) == expected
    assert _active_at(row, at) == expected


def test_network_tag_precedence_and_missing_dates():
    row = dict(start_date="2022", opening_date="2023", construction_date="2020",
               end_date="2030", closing_date="2029")
    assert network_validity(row) == ("2022", "2030")
    del row["start_date"]
    assert network_validity(row)[0] == "2023"
    del row["opening_date"]
    assert network_validity(row)[0] == "2020"
    assert all(pd.isna(v) for v in network_validity({}))
    assert network_validity(dict(start_date="unknown", opening_date="2030"))[0] == "2030"


def test_doe_dates_use_us_order_without_inventing_missing_years():
    assert _doe_time("11-22-2012") == "2012-11-22"
    assert _doe_time("01-05-2002") == "2002-01-05"
    assert _doe_time("2030") == "2030"
    assert pd.isna(_doe_time(None))


def test_gem_not_found_placeholder_falls_back_to_status():
    assert pd.isna(valid_time("not found"))
    frame = pd.DataFrame(dict(valid_from=["not found"], valid_to=[None],
                             status=["retired"], capacity_mw=[10]))
    options = dict(statuses=["operating"], minimum_capacity_mw=0, maximum_capacity_mw=1000)
    assert _filter_assets(frame, options, "capacity_mw", at="2030").empty


def test_dates_override_status_and_missing_doe_status_is_allowed():
    frame = pd.DataFrame(dict(
        uid=["historical", "future", "retired", "planned", "doe"],
        valid_from=["2010", "2030", None, None, None],
        valid_to=["2024", None, None, None, None],
        status=["retired", "planned", "retired", "planned", None],
        capacity_mw=[100] * 5,
    ))
    options = dict(statuses=["operating"], minimum_capacity_mw=0, maximum_capacity_mw=1000)
    assert _filter_assets(frame, options, "capacity_mw", at="2024-12-31").uid.tolist() == ["historical", "doe"]
    assert _filter_assets(frame, options, "capacity_mw", at="2030").uid.tolist() == ["future", "doe"]


def _table(name, rows):
    frame = pd.DataFrame(rows, columns=list(dict.fromkeys([
        *REQUIRED_COLUMNS[f"network.{name}"], *(k for r in rows for k in r)
    ])))
    return _finalize_frame(frame, schema_id=f"network.{name}")


def _network():
    bus = _table("bus", [dict(uid=u, **{"class": "bus"}, status="operating",
        voltage_kv=500, current_type="AC", geometry=Point(i, 0))
        for i, u in enumerate(["a", "b", "c", "d"])])
    branch = _table("branch", [dict(uid=u, **{"class": "branch"}, status="operating",
        voltage_kv=500, current_type="AC", from_bus_uid=a, to_bus_uid=b,
        geometry=LineString([bus.loc[bus.uid.eq(a)].geometry.iloc[0],
                             bus.loc[bus.uid.eq(b)].geometry.iloc[0]]),
        valid_from="2030" if u == "bridge" else None)
        for u, a, b in [("ab", "a", "b"), ("cd", "c", "d"), ("bridge", "b", "c")]])
    return StandardNetwork(bus, branch, _table("transformer", []), _table("converter", []))


def test_mapping_keeps_disconnected_versions_then_case_filters_dates():
    network = _network()
    manager = object.__new__(SpatiotemporalMappingManager)
    manager.standard_data = SimpleNamespace(load=lambda name: network)
    mapped = manager.build_network()
    assert len(mapped.bus) == 4
    for table in (mapped.bus, mapped.branch, mapped.transformer, mapped.converter):
        assert "in_largest_connected_graph" not in table.columns
    mapped = MappedNetwork(mapped.bus, mapped.branch, mapped.transformer, mapped.converter,
                           pd.DataFrame({"branch_uid": network.branch.uid}))
    options = dict(minimum_voltage_kv=0, maximum_voltage_kv=2000,
                   **{f"{n}_statuses": ["operating"] for n in ("bus", "branch", "transformer", "converter")})
    past, _ = filter_network(mapped, options, at="2024")
    future, _ = filter_network(mapped, options, at="2030")
    assert len(past.bus) == 2 and len(future.bus) == 4


@pytest.mark.parametrize("dataset", ["spatial", "population"])
def test_spatial_versions_are_selected_before_mapping_without_mutation(dataset):
    data = pd.DataFrame(dict(uid=["old", "new"], valid_from=["2020", "2025"],
                             valid_to=["2024", None]))
    manager = object.__new__(SpatiotemporalMappingManager)
    manager.config = {dataset: {"at": "2024-12-31"}}
    manager.standard_data = SimpleNamespace(load=lambda name: data)
    assert manager._spatial_snapshot(dataset).uid.tolist() == ["old"]
    assert len(data) == 2


def test_mapping_spatial_and_population_dates_are_independent():
    data = pd.DataFrame(dict(uid=["old", "new"], valid_from=["2020", "2030"],
                             valid_to=["2024", None]))
    manager = object.__new__(SpatiotemporalMappingManager)
    manager.config = {"spatial": {"at": "2024"}, "population": {"at": "2030"}}
    manager.standard_data = SimpleNamespace(load=lambda name: data)
    assert manager._spatial_snapshot("spatial").uid.tolist() == ["old"]
    assert manager._spatial_snapshot("population").uid.tolist() == ["new"]
    manager.config["population"]["at"] = ""
    assert len(manager._spatial_snapshot("population")) == 2


@pytest.mark.parametrize("snapshot,expected", [
    ({}, "2024-05-01"), ({"at": ""}, "2024-05-01"), ({"at": "2030"}, "2030"),
])
def test_case_snapshot_date_is_routed_to_network_filter(snapshot, expected, monkeypatch):
    manager = object.__new__(PowerSystemCaseManager)
    manager.config = {"snapshot": snapshot, "time": {"start": "2024-05-01"}, "network": {}}
    manager.mapped_data = SimpleNamespace(network=object())
    seen = []

    class StopAfterSelection(Exception):
        pass

    def select(*args, **kwargs):
        seen.append(kwargs["at"])
        raise StopAfterSelection

    monkeypatch.setattr("src.case.manager.filter_network", select)
    with pytest.raises(StopAfterSelection):
        manager._build_case()
    assert seen == [expected]


def test_old_time_config_names_raise_migration_errors(tmp_path):
    path = tmp_path / "old.toml"
    path.write_text('[general]\nat = "2024"\n')
    with pytest.raises(ValueError, match=r"\[spatial\].at"):
        SpatiotemporalMappingManager(path, standard_data=SimpleNamespace())
    path.write_text('[time]\nat = "2024"\n')
    with pytest.raises(ValueError, match=r"\[snapshot\].at"):
        PowerSystemCaseManager(path)


def test_geojson_additions_survive_repeated_builds_and_validate_endpoints(tmp_path):
    network = _network()
    path = tmp_path / "planned.geojson"
    extra = gpd.GeoDataFrame([dict(uid="plan:new", **{"class": "branch"},
        from_bus_uid="a", to_bus_uid="d", voltage_kv=500, current_type="AC",
        opening_date="2030", geometry=LineString([(0, 0), (3, 0)]))], crs=4326)
    extra.to_file(path, driver="GeoJSON")
    original = path.read_bytes()
    standardizer = SimpleNamespace(config={"source_ids": ["osm", "plan"]}, source=lambda source: path)
    for _ in range(2):
        result = append_network_sources(standardizer, network)
        assert result.branch.loc[result.branch.uid.eq("plan:new"), "valid_from"].iloc[0] == "2030"
        assert len(result.branch) == 4
    assert path.read_bytes() == original
    extra["to_bus_uid"] = "missing"
    extra.to_file(path, driver="GeoJSON")
    with pytest.raises(ValueError, match="missing"):
        append_network_sources(standardizer, network)


@pytest.mark.parametrize("dataset,processor,kind,technology", [
    ("generator", _GeneratorStandardizer, "Coal", "Subcritical"),
    ("storage", _StorageStandardizer, "Hydropower", "Pumped storage"),
])
def test_multiple_gem_csv_sources_preserve_dates_and_provenance(tmp_path, dataset, processor, kind, technology):
    paths = {}
    for i, source in enumerate(["base", "addition"]):
        paths[source] = tmp_path / f"{source}.csv"
        pd.DataFrame([{
            "Type": kind, "Technology": technology, "Country/area": "China",
            "Plant / Project name": source, "Capacity (MW)": 100,
            "Status": "Operating" if i == 0 else "Planned",
            "Latitude": 35, "Longitude": 115, "GEM location ID": source,
            "GEM unit/phase ID": source, "Fuel (combustion only)": "coal",
            "Start year": 2020 if i == 0 else 2030,
            "Retired year": 2024 if i == 0 else None,
        }]).to_csv(paths[source], index=False)
    manager = SimpleNamespace(
        datasets={dataset: dict(source_ids=list(paths), output=f"{dataset}.parquet", options=dict(
            sheet="Power facilities", country_areas=["China"], include_statuses=[],
            class_mapping_file="config/class_mapping.csv"))},
        project_root=Path(__file__).resolve().parents[1], output_root=tmp_path,
        raw_data=SimpleNamespace(get_file=lambda source: paths[source], catalog=pd.DataFrame(
            {"version": ["2024", "2030"]}, index=list(paths))),
    )
    result = processor(manager, dataset).build()
    assert result.source_id.tolist() == ["base", "addition"]
    assert result.valid_from.tolist() == ["2020", "2030"]
    assert result.valid_to.iloc[0] == "2024"
