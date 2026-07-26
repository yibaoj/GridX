# Modular Architecture

The next version of the project uses five layers. Each layer has one public
package or module and exchanges explicit objects instead of notebook globals.

## 1. Raw data layer: `src/raw/`

Responsibilities:

- Read source metadata and acquisition instructions from
  `config/raw_data_sources.csv`.
- Preserve source URLs, versions, domains, providers, and stable source IDs.
- Download files through direct URLs, the Figshare API, or CDS/atlite.
- Report an exact manual procedure when a licence, form, or publication
  prevents unattended download.
- Validate file existence, size, and an optional configured checksum.
- Return a tabular report without interpreting source-specific columns.

Input: a source catalog and a project `data/` directory.

Output: immutable raw files plus an inventory `DataFrame`.

`source_id` is the stable cross-layer key for a dataset. It is used in calls
such as `get_file("osm_china_pbf")` and may be used as a dictionary key by the
next layer. It does not create a dynamic Python variable or notebook global.

The public interface is intentionally small:

- `check(source_ids=None)` checks one, several, or all sources.
- `prepare(source_ids=None)` prepares one, several, or all sources.
- `get_file(source_id)` returns one verified local path.

The raw layer must not rename source columns, classify technologies, construct
network topology, resample time series, or modify downloaded files.

`catalog.py` validates and selects source metadata, `download.py` implements
acquisition methods and checksums, and `manager.py` exposes the public API.

## 2. Standard data layer: `src/standard/`

This layer combines source adaptation and canonicalization.

Responsibilities:

- Read each source through a source-specific adapter.
- Normalize identifiers, status values, technology names, units, timestamps,
  coordinate reference systems, and missing-value conventions.
- Preserve source identifiers and provenance alongside canonical identifiers.
- Produce canonical entity tables and multidimensional time-series arrays.
- Validate uniqueness, units, geometry, ranges, and referential integrity.

Each dataset has a focused processor module (`network.py`, `generation.py`,
`storage.py`, `parameter.py`, `load.py`, `population.py`, and `resource.py`).
`manager.py` is the public orchestration interface, while `schema.py` owns the
shared contract.

The raw OSM PBF is the source. `china-power-network.gpkg` is a reproducible
derived cache built by the version-controlled script referenced in
`config/standard_data.toml`. Voltage thresholds belong to system-case
selection, not canonical standardization.

Asset classification rules are stored in one auditable table,
`config/asset_type_mapping.csv`. Rules are evaluated by ascending `priority`
and first match wins. Every standardized asset keeps its matched `rule_id`.

Stable dataset IDs:

- `spatial`: province, city, regular-grid, or custom-zone geometries.
- `network`: a bundle containing node and branch GeoDataFrames.
- `generation` and `storage`: physical asset GeoDataFrames.
- `parameter`: long-form technical and economic assumptions.
- `load`, `population`, and `resource`: labeled xarray datasets.

Entity tables use `DataFrame` or `GeoDataFrame`. Dense time-series data use
`xarray`, because forcing `time x asset x scenario` data into one wide
`DataFrame` would weaken labels and increase memory use.

Entity voltage is a nullable Arrow `list[float64]` in kV. Partial ISO time
strings preserve source precision, such as `2024`, `2024-03`, or an exact
timestamp. `load` and `resource` reference entity or `spatial` IDs instead of
duplicating geometry at every time step.

## 3. Spatiotemporal mapping layer: `spatiotemporal_mapping.py`

Responsibilities:

- Map points, polygons, and raster cells to stable `spatial_unit_id` values.
- Build explicit generator-to-node, storage-to-node, and load-cell-to-node
  mapping tables.
- Harmonize time zones, interval conventions, calendars, and model snapshots.
- Aggregate or disaggregate data without changing canonical source tables.
- Record mapping method, distance, confidence, and review flags.

Spatial units are an indexing and aggregation interface, not an electrical
connectivity rule. Sharing one grid cell does not prove that two assets are
electrically connected. Network connectivity remains defined by branch
endpoints; grid membership only narrows candidate searches.

## 4. System case layer: `system_case.py`

Responsibilities:

- Select year, scenario, geographic scope, network resolution, technology
  aggregation, and time horizon.
- Assemble one validated `PowerSystemCase` from canonical tables and mappings.
- Keep static assets separate from time-varying demand and availability.
- Check node references, power and energy units, temporal coverage, network
  connectivity, and energy conservation.

Expected `PowerSystemCase` contents:

- Nodes, branches, generators, storage units, and loads.
- Generator availability, nodal demand, inflows, and outage profiles.
- Scenario settings, units, spatial resolution, time resolution, and source
  provenance.

This object is the only data interface consumed by optimization applications.

## 5. Application layer: `applications.py`

Responsibilities:

- Translate a `PowerSystemCase` into UC, economic dispatch, OPF, or capacity
  expansion models.
- Hold formulation-specific variables, constraints, objectives, and solver
  options.
- Return standardized result tables without mutating the input case.
- Keep plotting and reporting separate from model construction and solving.

Applications must not read OSM, GEM, population, weather, or load source files
directly. A new application should reuse the same validated system case.

## Independent resolutions

The architecture treats the following choices as independent:

- Resource spatial resolution: raster, regular grid, city, or province.
- Electrical resolution: physical substations, aggregated cities, or provinces.
- Generator resolution: unit, project, node-technology cluster, or fleet.
- Temporal resolution: hourly, representative days, or representative weeks.
- Scenario resolution: weather year, policy case, outage case, or planning year.

Changing one resolution therefore requires rebuilding mappings or a system
case, not rewriting source adapters or application models.
