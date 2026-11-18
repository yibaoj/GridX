"""Regression checks for the exported-data boundary, without scientific modules."""

import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from .ui_data import atomic_json, prepare_dashboard, read_payload
from . import app
from .data import WebDataService


def feature(identifier, geometry, **properties):
    return {"type": "Feature", "geometry": geometry,
            "properties": {"uid": identifier, **properties}}


def payload(features, **summary):
    return {"geojson": {"type": "FeatureCollection", "features": features},
            "summary": {"count": len(features), **summary}}


class DashboardDataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "outputs/web"
        polygon = {"type": "Polygon", "coordinates": [
            [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
            [[3, 3], [4, 3], [4, 4], [3, 4], [3, 3]],
        ]}
        generation = [
            feature("g1", {"type": "Point", "coordinates": [1, 1]}, total_mw=100,
                    breakdown={"solar": 50, "wind": 10}),
            feature("g2", {"type": "Point", "coordinates": [3.5, 3.5]}, total_mw=20,
                    breakdown={"solar": 10}),
            feature("g3", {"type": "Point", "coordinates": [15, 15]}, total_mw=30,
                    breakdown={"coal": 30}),
        ]
        branch = feature("line1", {"type": "LineString", "coordinates": [[2, 2], [8, 2]]},
                         feature_kind="branch", voltage_kv=1000, current_type="AC")
        self.inputs = {
            "manifest.json.gz": {"year": 2025, "stages": ["standard", "case"],
                                 "layers": ["generator", "load", "network"], "resource_classes": []},
            "boundary/common.json.gz": {"type": "FeatureCollection", "features": [
                feature("admin-A", polygon, spatial_level="province", name="测试省")]},
            "layers/standard/load.json.gz": payload([
                feature("load-A", {"type": "GeometryCollection", "geometries": [
                    polygon, {"type": "LineString", "coordinates": [[0, 0], [-1, 0]]}]},
                    location_uid="load-A", value=12.5)], unit="TWh"),
            "layers/standard/generator.json.gz": payload(generation, value=150, unit="MW",
                categories=[{"id": "solar", "label": "光伏", "color": "#ff0"},
                            {"id": "wind", "label": "风电", "color": "#0ff"}]),
            "layers/standard/network.json.gz": payload([branch, copy.deepcopy(branch)]),
            "layers/case/generator.json.gz": payload([], value=80),
            "application/uc.json.gz": {"time": ["2025-01-01T00:00:00", "2025-01-01T01:00:00"],
                "series": [{"id": "load", "kind": "load", "values": [100, 120]},
                           {"id": "imports", "kind": "generation", "values": [10, 20]}],
                "summary": {"snapshots": 2, "solve_mode": "single", "case_minimum_voltage_kv": 500}},
        }
        for relative, data in self.inputs.items():
            atomic_json(self.root / relative, data)

    def test_local_data_survives_without_backend_imports(self):
        unavailable = {name: None for name in ("src.standard", "src.mapping", "src.case", "src.app")}
        with patch.dict(sys.modules, unavailable):
            paths = prepare_dashboard(self.root)
        overview, regional = (read_payload(paths[name]) for name in ("overview", "regional-overview"))
        self.assertEqual(overview["year"], 2025)
        self.assertEqual(overview["provinces"][0]["name"], "测试省")
        self.assertEqual(overview["annual_energy_twh"], 12.5)
        self.assertEqual(regional["national"]["installed_capacity_mw"], 150)
        self.assertEqual(regional["provinces"]["load-A"]["installed_capacity_mw"], 100)
        self.assertEqual(regional["coverage"]["unassigned_capacity_mw"], 50)
        self.assertEqual(regional["national"]["uhv_branch_count"], 1)
        self.assertEqual(regional["provinces"]["load-A"]["uhv_branch_count"], 1)
        self.assertEqual(regional["simulation"]["maximum_load_mw"], 120)
        self.assertIsNone(regional["national"]["peak_load_mw"])

    def test_changed_export_creates_a_new_snapshot_and_preserves_the_previous(self):
        old = prepare_dashboard(self.root)
        updated = self.inputs["layers/standard/load.json.gz"]
        updated["geojson"]["features"][0]["properties"]["value"] = 20
        atomic_json(self.root / "layers/standard/load.json.gz", updated)
        new = prepare_dashboard(self.root)
        self.assertNotEqual(old, new)
        self.assertEqual(read_payload(old["overview"])["annual_energy_twh"], 12.5)
        self.assertEqual(read_payload(new["overview"])["annual_energy_twh"], 20)
        self.assertEqual(prepare_dashboard(self.root), new)

    def test_a_corrupted_cache_is_rebuilt(self):
        paths = prepare_dashboard(self.root)
        paths["overview"].write_bytes(b"interrupted cache")
        rebuilt = prepare_dashboard(self.root)
        self.assertEqual(read_payload(rebuilt["overview"])["annual_energy_twh"], 12.5)

    def test_missing_exports_return_no_dashboard(self):
        (self.root / "manifest.json.gz").unlink()
        self.assertEqual(prepare_dashboard(self.root), {})

    def test_unknown_load_units_are_rejected(self):
        self.inputs["layers/standard/load.json.gz"]["summary"]["unit"] = "MW"
        atomic_json(self.root / "layers/standard/load.json.gz", self.inputs["layers/standard/load.json.gz"])
        with self.assertRaisesRegex(ValueError, "TWh"):
            prepare_dashboard(self.root)

    def test_missing_package_has_a_readable_bootstrap_status(self):
        (self.root / "manifest.json.gz").unlink()
        config = Path(self.temporary.name) / "config/web.toml"
        config.parent.mkdir()
        config.write_text('stages=[]\nlayers=[]\n[site]\ntitle="GridX"\n[map]\nzoom=4\n'
                          '[data]\noutput_root="outputs/web"\nplot_year=2025\n', encoding="utf-8")
        status = WebDataService(config).bootstrap()
        self.assertFalse(status["data_status"]["available"])
        self.assertEqual(status["manifest"]["year"], 2025)
        self.assertIn("src.web.build", status["data_status"]["message"])

    def test_new_and_legacy_assets_keep_their_routes_and_cannot_escape(self):
        ui = Path(self.temporary.name).resolve() / "ui"
        legacy = Path(self.temporary.name).resolve() / "legacy"
        for folder in (ui, legacy):
            folder.mkdir()
            (folder / "index.html").write_text("page", encoding="utf-8")
        (legacy / "app.js").write_text("legacy", encoding="utf-8")
        with patch.object(app, "UI_ROOT", ui), patch.object(app, "STATIC_ROOT", legacy):
            self.assertEqual(app.static_file("/"), ui / "index.html")
            self.assertEqual(app.static_file("/legacy/"), legacy / "index.html")
            self.assertEqual(app.static_file("/app.js"), legacy / "app.js")
            self.assertIsNone(app.static_file("/../../config/web.toml"))

    def test_contract_rejects_changed_versions_and_misaligned_uc_series(self):
        from .contract import validate_payload
        validate_payload("manifest.json.gz", self.inputs["manifest.json.gz"])
        incompatible = {**self.inputs["manifest.json.gz"], "schema_version": 2}
        with self.assertRaisesRegex(ValueError, "schema_version"):
            validate_payload("manifest.json.gz", incompatible)
        uc = copy.deepcopy(self.inputs["application/uc.json.gz"])
        uc["series"][0]["values"].pop()
        with self.assertRaisesRegex(ValueError, "length"):
            validate_payload("application/uc.json.gz", uc)
        layer = copy.deepcopy(self.inputs["layers/standard/generator.json.gz"])
        layer["schema_version"] = 1
        layer["summary"]["unit"] = "GW"
        with self.assertRaisesRegex(ValueError, "unit"):
            validate_payload("layers/standard/generator.json.gz", layer)

    def test_failed_export_preserves_previous_package(self):
        from .build import WebDataBuilder
        builder = WebDataBuilder.__new__(WebDataBuilder)
        builder.output_root = self.root / "replacement"
        builder.output_root.mkdir()
        previous = builder.output_root / "keep.txt"
        previous.write_text("old")
        with patch.object(builder, "_build_package", side_effect=ValueError("incompatible")):
            with self.assertRaisesRegex(ValueError, "incompatible"):
                builder.build()
        self.assertEqual(previous.read_text(), "old")

        def package():
            manifest = copy.deepcopy(self.inputs["manifest.json.gz"])
            builder._write("manifest.json.gz", manifest)
            return manifest

        with patch.object(builder, "_build_package", side_effect=package):
            builder.build()
        exported = read_payload(builder.output_root / "manifest.json.gz")
        self.assertEqual(exported["schema_version"], 1)
        self.assertFalse(previous.exists())


if __name__ == "__main__":
    unittest.main()
