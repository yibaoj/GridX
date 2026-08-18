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

Each dataset has a focused processor module (`network.py` plus its compact
`network_model.py` helper, `generator.py`,
`storage.py`, `parameter.py`, `load.py`, `population.py`, and `resource.py`).
`manager.py` is the public orchestration interface, while `schema.py` owns the
shared contract.

Every standardizer writes its stable dataset ID as `standard_dataset_id` in
the output metadata. Loaders restore and validate this value rather than
inferring or injecting it from the requested filename.

The raw OSM PBF is the source. `china-power-network.gpkg` is a reproducible
derived cache built by the version-controlled script referenced in
`config/standard_data.toml`. Voltage thresholds belong to system-case
selection, not canonical standardization.

Asset classification rules are stored in one auditable table,
`config/class_mapping.csv`. Rules are evaluated by ascending `priority`
and first match wins. Every standardized asset keeps its matched
`mapping_rule_id`.

Stable dataset IDs:

- `spatial`: administrative province, city, or other boundary geometries.
- `network`: a bundle containing bus, branch, transformer, and converter
  GeoDataFrames.
- `generator` and `storage`: physical asset GeoDataFrames.
- `parameter`: long-form technical and economic assumptions.
- `population`: a gridded population GeoDataFrame.
- `load` and `resource`: labeled xarray datasets with `time`, `uid`, and
  `class` dimensions.

Entity tables use `DataFrame` or `GeoDataFrame`. Dense time-series data use
`xarray`, because forcing `time x asset x scenario` data into one wide
`DataFrame` would weaken labels and increase memory use.

Entity voltage is a nullable Arrow `list[float64]` in kV. Partial ISO time
strings preserve source precision, such as `2024`, `2024-03`, or an exact
timestamp. `load` and `resource` store one WKT geometry coordinate per `uid`,
so geometry is not duplicated along the time dimension.

## 3. Spatiotemporal mapping layer: `src/mapping/`

Responsibilities:

- Map points, polygons, and raster cells to stable `spatial_unit_id` values.
- Build explicit generator-to-bus, storage-to-bus, and load-cell-to-bus
  mapping tables.
- Harmonize time zones, interval conventions, calendars, and model snapshots.
- Aggregate or disaggregate data without changing canonical source tables.
- Record mapping method, distance, confidence, and review flags.
- Carry the standardized parameter table forward unchanged, so a mapped-data
  snapshot is the case layer's only upstream dependency.

Spatial units are an indexing and aggregation interface, not an electrical
connectivity rule. Sharing one cell does not prove electrical connectivity;
branch, transformer, and converter endpoints define it.

## 4. System case layer: `src/case/`

Responsibilities:

- Select year, scenario, geographic scope, network resolution, technology
  aggregation, and time horizon.
- Assemble one validated `PowerSystemCase` from mapped data and standard
  parameters.
- Keep static assets separate from time-varying demand and availability.
- Check node references, power and energy units, temporal coverage, network
  connectivity, and energy conservation.

`PowerSystemCase` contains:

- Bus, branch, transformer, converter, generator, storage, and load data.
- Resolved long-form parameters and aggregation membership tables.
- Generator availability, nodal demand, inflows, and outage profiles.
- Scenario settings, units, spatial resolution, time resolution, and source
  provenance.

Backend adapters are isolated under `src/case/backends/`. PyPSA is the first
adapter; its declarative parameter manifest is shared by case validation and
backend conversion. Future adapters must consume the same case rather than
source data and provide an equivalent unit-checked manifest.

This object is the only data interface consumed by optimization applications.

## 5. Application layer: `src/app/`

Responsibilities:

- Translate a `PowerSystemCase` into UC, economic dispatch, OPF, or capacity
  expansion models.
- Hold formulation-specific variables, constraints, objectives, and solver
  options.
- Return standardized result tables without mutating the input case.
- Keep plotting and reporting separate from model construction and solving.

Applications must not read OSM, GEM, population, weather, or load source files
directly. A new application should reuse the same validated system case.

The first implementation is `src/app/uc/`. It converts a
`PowerSystemCase` through the PyPSA backend, solves continuous clustered
UC/ED, and returns a result object with solver status and a configurable
time/spatial-scope production plot. Binary unit commitment remains a future
formulation because the current case aggregates units by bus and technology.

## Shared visualization

`src/visualization/` is a cross-layer presentation utility, not a data layer.
It owns the bilingual text catalog, font selection, display CRS, South China
Sea inset default, and common `plot(dataset_id, **kwargs)` dispatch. Standard,
mapping, and case retain their own data preparation and delegate reusable
rendering downward: mapping reuses standard renderers, while case adapts its
components and reuses mapping renderers. Defaults live in `config/plot.toml`.

## Independent resolutions

The architecture treats the following choices as independent:

- Resource spatial resolution: raster, regular grid, city, or province.
- Electrical resolution: physical substations, aggregated cities, or provinces.
- Generator resolution: unit, project, node-technology cluster, or fleet.
- Temporal resolution: hourly, representative days, or representative weeks.
- Scenario resolution: weather year, policy case, outage case, or planning year.

Changing one resolution therefore requires rebuilding mappings or a system
case, not rewriting source adapters or application models.
