"""
garage.py -- on-disk memory for multiple vehicles.

Each vehicle is its own tune in progress: hardware (cam, block, compression),
fuel, airflow strategy, and where it sits on the journey (with the convergence
history across passes). None of that is in a datalog, and you tune more than one
car, so we persist it keyed by a vehicle name and reload it next launch.

This module is pure storage -- load / save / list / get / upsert on a JSON file.
The wizard owns the (de)serialization of its own SessionOpts into a record dict,
so garage.py has no dependency on the rest of the package (no import cycles).

Default location: ~/.tuneassist/garage.json  (override for tests).
"""

from __future__ import annotations
import json
import os
from pathlib import Path

SCHEMA_VERSION = 1


def default_path() -> str:
    return str(Path.home() / ".tuneassist" / "garage.json")


def load(path: str | None = None) -> dict:
    """Return the garage dict; a fresh empty one if the file is missing/corrupt."""
    path = path or default_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "vehicles" in data:
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"version": SCHEMA_VERSION, "vehicles": {}}


def save(data: dict, path: str | None = None) -> None:
    path = path or default_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)            # atomic-ish: never leave a half-written file


def list_vehicles(data: dict) -> list[str]:
    """Vehicle names, most-recently-updated first."""
    vs = data.get("vehicles", {})
    return sorted(vs, key=lambda n: vs[n].get("updated", ""), reverse=True)


def get(data: dict, name: str) -> dict | None:
    return data.get("vehicles", {}).get(name)


def upsert(data: dict, name: str, record: dict) -> None:
    data.setdefault("vehicles", {})[name] = record


def delete(data: dict, name: str) -> bool:
    return data.get("vehicles", {}).pop(name, None) is not None
