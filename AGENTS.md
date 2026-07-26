# Paper Codes Instructions

- Run, test, and execute all Python scripts and notebooks in conda environment
  `env-py313`.
- Keep the number of variables small. Use stable semantic names for public
  objects; prefix short-lived internal variables with `_`.
- Do not create aliases for the same object unless they improve auditability.
- Implement repeated behavior once, using a function, a parameterized script,
  or the existing authoritative notebook.
- Treat `osm_exp.ipynb` and its `grid_topology` as the single source of truth
  for OSM grid topology. Do not rebuild station nodes or topology in GEM code.
- Preserve domain names: `lines`, `substations`, `segments`, and `terminals`
  describe GIS/OSM objects; `nodes` and `branches` describe the final graph.
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
