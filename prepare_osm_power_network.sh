#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="${CONDA_ENV_NAME:-env-py313}"
INPUT_PBF="${1:-${SCRIPT_DIR}/data/osm/china-latest.osm.pbf}"
OUTPUT_PREFIX="${2:-${SCRIPT_DIR}/data/osm/china-power-network}"

FILTERED_PBF="${OUTPUT_PREFIX}.osm.pbf"
GEOJSONSEQ="${OUTPUT_PREFIX}.geojsonseq"
GPKG="${OUTPUT_PREFIX}.gpkg"

if [[ ! -f "${INPUT_PBF}" ]]; then
    printf 'Input PBF not found: %s\n' "${INPUT_PBF}" >&2
    exit 1
fi

if ! command -v conda >/dev/null 2>&1; then
    printf 'conda is not available on PATH.\n' >&2
    exit 1
fi

mkdir -p "$(dirname "${OUTPUT_PREFIX}")"

# Active assets, lifecycle-prefixed assets, and legacy power=<status> forms.
# --remove-tags strips tags only from referenced geometry nodes that were not
# matched themselves, preventing towers/poles from becoming millions of extra
# point features while retaining coordinates needed to build line geometry.
FILTERS=(
    nwr/power=line
    nwr/power=cable
    nwr/power=substation
    nwr/power=converter
    nwr/power=construction
    nwr/power=proposed
    nwr/power=planned
    nwr/power=disused
    nwr/power=abandoned
    nwr/power=demolished
    nwr/power=removed
    nwr/power=razed
    nwr/power=destroyed
    nwr/construction:power=line
    nwr/construction:power=cable
    nwr/construction:power=substation
    nwr/construction:power=converter
    nwr/proposed:power=line
    nwr/proposed:power=cable
    nwr/proposed:power=substation
    nwr/proposed:power=converter
    nwr/planned:power=line
    nwr/planned:power=cable
    nwr/planned:power=substation
    nwr/planned:power=converter
    nwr/disused:power=line
    nwr/disused:power=cable
    nwr/disused:power=substation
    nwr/disused:power=converter
    nwr/abandoned:power=line
    nwr/abandoned:power=cable
    nwr/abandoned:power=substation
    nwr/abandoned:power=converter
    nwr/demolished:power=line
    nwr/demolished:power=cable
    nwr/demolished:power=substation
    nwr/demolished:power=converter
    nwr/removed:power=line
    nwr/removed:power=cable
    nwr/removed:power=substation
    nwr/removed:power=converter
    nwr/razed:power=line
    nwr/razed:power=cable
    nwr/razed:power=substation
    nwr/razed:power=converter
    nwr/destroyed:power=line
    nwr/destroyed:power=cable
    nwr/destroyed:power=substation
    nwr/destroyed:power=converter
    nwr/was:power=line
    nwr/was:power=cable
    nwr/was:power=substation
    nwr/was:power=converter
    r/power=circuit
    r/power=line_section
)

printf 'Extracting network candidates from %s\n' "${INPUT_PBF}"
conda run -n "${ENV_NAME}" osmium tags-filter \
    --remove-tags \
    "${INPUT_PBF}" \
    "${FILTERS[@]}" \
    -o "${FILTERED_PBF}" \
    --overwrite \
    --progress

printf 'Exporting points, lines, and polygons with OSM IDs\n'
conda run -n "${ENV_NAME}" osmium export \
    "${FILTERED_PBF}" \
    --geometry-types=point,linestring,polygon \
    --attributes=type,id \
    --output-format=geojsonseq \
    -o "${GEOJSONSEQ}" \
    --overwrite \
    --show-errors

printf 'Building GeoPackage spatial index\n'
conda run -n "${ENV_NAME}" ogr2ogr \
    -f GPKG \
    "${GPKG}" \
    "${GEOJSONSEQ}" \
    -nln power_features \
    -nlt GEOMETRY \
    -lco SPATIAL_INDEX=YES \
    -overwrite

printf '\nCreated:\n'
printf '  %s\n' "${FILTERED_PBF}"
printf '  %s\n' "${GEOJSONSEQ}"
printf '  %s\n' "${GPKG}"
printf '\nThe PBF retains power=circuit and power=line_section relations for later topology work.\n'
