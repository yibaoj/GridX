# GridX Instructions

- Run, test, and execute all Python scripts and notebooks in conda environment
  `env-py313`.
- Keep the number of variables small. Use stable semantic names for public
  objects; prefix short-lived internal variables with `_`.
- Do not create aliases for the same object unless they improve auditability.
- Implement repeated behavior once, using a function, a parameterized script,
  or the existing authoritative notebook.
- Treat `notebooks/osm_exp.ipynb` and its `grid_topology` as the exploratory source of truth
  for OSM grid topology. Do not rebuild station nodes or topology in GEM code.
- Preserve domain names: `lines`, `substations`, `segments`, and `terminals`
  describe GIS/OSM objects; `nodes` are geographic intermediates, while
  `bus`, `branch`, `transformer`, and `converter` describe the electrical network.
- Keep downloaded inputs in `data/`, generated artifacts in `outputs/`, and
  reproducible options in `config/`. Do not modify raw downloaded files.
- After notebook changes, execute from the first cell in `env-py313` and check
  errors, summary consistency, generated files, and map readability.
- Keep the modular architecture easy to read: one package per substantial
  layer, focused `.py` files by dataset or responsibility, explicit inputs and
  outputs, short methods, and no hidden notebook globals or cross-notebook
  `%run` interfaces.
- Prefer small composition-based classes over inheritance hierarchies. Add an
  abstraction only when it removes repeated behavior or stabilizes a real data
  contract.
- Keep layer boundaries strict. Raw-data code acquires and validates immutable
  source files; standardization interprets source fields; mapping creates
  explicit crosswalks; system-case assembly selects a scenario; applications
  formulate and solve models.
- Keep notebooks as concise examples, diagnostics, and research narratives.
  Reusable processing logic belongs in the corresponding Python module.
- Give every public table and array stable identifiers, documented units, and
  provenance fields. Do not overwrite canonical data when creating a mapping,
  aggregation, scenario, or solver-specific object.
- After processing changes, rebuild the affected datasets in `env-py313` and
  verify schemas, conservation, and topology integrity.
- Keep raw-data responsibilities limited to acquisition, inventory, and file
  validation. Do not classify assets, rename source columns, construct topology,
  or resample time series in the raw layer. Report exact manual acquisition
  steps when licences or access restrictions prevent unattended downloads.
- Keep `source_id` stable and preserve source URLs, versions, and providers.
  Use the catalog's canonical `local_path`; do not create dynamic variables
  from source IDs. Declare dataset dependencies in the standard configuration.
- Store shared asset classification in `config/class_mapping.csv`; evaluate
  rules by ascending priority, keep the first match, and retain `mapping_rule_id`.
  Use configured source adapters and explicit source-specific mapping files
  rather than hard-coded source classification or processing-order assumptions.
- Preserve and validate `standard_dataset_id` metadata on load rather than
  inferring dataset identity from filenames. Keep UID uniqueness, canonical
  column order, dtypes, CRS, units, ranges, and references valid.
- Store dense labeled time series in xarray and entity tables in DataFrames or
  GeoDataFrames. Preserve partial ISO timestamp precision and store geometry
  once per UID, not once per timestamp. Follow each component's voltage schema;
  electrical network buses and branches have scalar voltage and AC/DC type.
- Generate OSM derived caches reproducibly using the configured preparation
  script. Apply scenario voltage thresholds in the case layer, not in canonical
  network standardization. Preserve inferred-topology provenance.
- Treat spatial cells as indexing and aggregation units, not evidence of
  electrical connectivity; use branch, transformer, and converter endpoints.
- Keep mapped parameters unchanged. Construct cases only from mapped data and
  make applications consume the case without reading raw sources or mutating it.
- Keep backend adapters under `src/case/backends/`. Share one unit-checked
  parameter manifest between backend conversion and case validation; declare
  required parameters and fallbacks there instead of scattering defaults.
- Preserve asset-to-aggregate membership and parameter-selection provenance.
  Rebuild a coherent case bundle when any required output is missing; do not
  combine components from different builds. Rebuild explicitly after changing
  scenario configuration.
- Reuse shared visualization configuration and data preparation. Plot APIs
  return figures without displaying or saving them; close displayed figures
  and release unused NetCDF handles. Build web payloads offline and keep web
  requests read-only instead of repeating spatial or time-series aggregation.
- Preserve extensive quantities when allocating load and population; use
  weighted averages for intensive resource profiles. Verify source-to-target
  totals and flag unresolved spatial or temporal coverage.
- For load-weighting extensions, normalize proxies within the source region and
  conserve its demand at every timestamp. Do not treat missing night-light
  observations as zero; account for observation coverage. Calibrate proxy
  coefficients with reference data or report sensitivity across weighting schemes.
- Label spatially downscaled demand as synthetic nodal load, not measured
  substation load. Distinguish implemented baselines from proposed sector,
  city-level, or time-varying enhancements, and validate against available
  regional statistics or known peaks. Preserve limitations of proxy datasets.
- Do not duplicate parameters already supplied by technology-data in supplemental
  model assumptions. Keep unresolved equal-rank conflicts explicit rather than
  silently selecting a source.
- Keep plotting/reporting separate from optimization construction and validate
  dispatch before replacing saved results. Describe continuous aggregated ED
  accurately; do not present it as unit-level binary commitment.
