"""Versioned browser data contract. This module uses only the Python standard library."""
from __future__ import annotations

from pathlib import PurePath
import math

SCHEMA_VERSION = 1


def validate_payload(relative: str, payload: dict) -> None:
    """Accept legacy v1 packages and additive fields, reject incompatible data."""
    if not isinstance(payload, dict):
        raise ValueError(f'{relative}: expected a JSON object')
    if payload.get('schema_version', 1) != SCHEMA_VERSION:
        raise ValueError(f'{relative}: unsupported schema_version')
    parts = PurePath(relative).parts
    name = parts[-1]
    required = {}
    if name == 'manifest.json.gz':
        required = {'year': int, 'stages': list, 'layers': list, 'resource_classes': list}
    elif 'boundary' in parts:
        required = {'type': str, 'features': list}
    elif 'layers' in parts:
        required = {'summary': dict, 'geojson': dict}
    elif name == 'uc.json.gz':
        required = {'time': list, 'series': list, 'categories': list, 'summary': dict}
        # Early exports omitted categories; the frontend accepts an empty list.
        payload.setdefault('categories', [])
    for field, kind in required.items():
        if not isinstance(payload.get(field), kind):
            raise ValueError(f'{relative}: {field} must be {kind.__name__}')
    geojson = payload.get('geojson', payload if 'boundary' in parts else None)
    if geojson is not None:
        if geojson.get('type') != 'FeatureCollection' or not isinstance(geojson.get('features'), list):
            raise ValueError(f'{relative}: expected GeoJSON FeatureCollection')
        for feature in geojson['features']:
            if not isinstance(feature, dict) or feature.get('type') != 'Feature' or not isinstance(feature.get('properties'), dict):
                raise ValueError(f'{relative}: invalid GeoJSON feature')
    if 'layers' in parts and payload.get('schema_version') == 1:
        layer = parts[parts.index('layers') + 2].removesuffix('.json.gz')
        units = {'generator': 'MW', 'storage': 'MW', 'load': 'TWh',
                 'population': 'log10(人口 + 1)', 'resource': 'p.u.'}
        if layer in units and payload['summary'].get('unit') != units[layer]:
            raise ValueError(f'{relative}: unit must remain {units[layer]} in v1')
        fields = {
            'generator': ('location_uid', 'total_mw', 'breakdown'),
            'storage': ('location_uid', 'total_mw', 'breakdown'),
            'network': ('uid', 'feature_kind', 'voltage_kv', 'current_type'),
            'load': ('location_uid', 'value'),
            'resource': ('location_uid', 'value'),
            'population': ('value', 'raw_value'),
        }
        for feature in geojson['features']:
            for field in fields.get(layer, ()):
                if field not in feature['properties']:
                    raise ValueError(f'{relative}: feature is missing {field}')
    if name == 'uc.json.gz':
        for series in payload['series']:
            if not isinstance(series, dict) or not isinstance(series.get('id'), str) or not isinstance(series.get('kind'), str):
                raise ValueError(f'{relative}: series requires id and kind')
            values = series.get('values')
            if not isinstance(values, list) or len(values) != len(payload['time']):
                raise ValueError(f'{relative}: series length must match time')
            if any(not isinstance(value, (float, int)) or not math.isfinite(value) for value in values):
                raise ValueError(f'{relative}: series values must be finite numbers')
