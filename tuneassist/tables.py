"""
tables.py -- map a recommended change to the EXACT vendor table/parameter to edit.

Tuners want "raise the Main VE table at these cells", not "raise VE". These are the
canonical table names tuners recognize (HP Tuners VCM Editor for GM; the Holley EFI
software for Holley). We name the table, not a version-specific menu path, since
those differ across OS/firmware -- the table name is stable and searchable.
"""

from __future__ import annotations

_GM = {
    "ve":          "Main VE table (Engine > Airflow > Volumetric Efficiency)",
    "maf":         "MAF Calibration table (Airflow vs Frequency / Hz)",
    "spark":       "High Octane spark table (Engine > Spark)",
    "iat_spark":   "IAT/charge-temp spark-retard table",
    "pe":          "Power Enrichment commanded-AFR/EQ table (Engine > Fuel > PE)",
    "startup_air": "Cranking Airflow + Startup Airflow Decay tables",
    "idle_air":    "Base Running Airflow / idle airflow tables",
    "idle_rpm":    "Desired Idle Speed (target idle RPM) table",
    "idle_spark":  "idle spark-vs-RPM-error correction table",
    "injector":    "injector flow-rate / injector data (Engine > Fuel > Injectors)",
    "warmup_enr":  "warmup (coolant-temp) enrichment table",
    "ase":         "afterstart / post-start enrichment table",
}

_HOLLEY = {
    "ve":          "Base Fuel table",
    "maf":         "Base Fuel table",
    "spark":       "Base Timing (ignition) table",
    "iat_spark":   "Timing-vs-Air-Temp compensation",
    "pe":          "Target AFR table (WOT cells)",
    "startup_air": "Cranking / Afterstart and idle airflow settings",
    "idle_air":    "Idle Speed / IAC settings",
    "idle_rpm":    "Target Idle Speed",
    "idle_spark":  "idle spark settings",
    "injector":    "injector data (size / flow)",
    "warmup_enr":  "Coolant Enrichment table",
    "ase":         "Afterstart Enrichment table",
}


def table(platform: str, key: str) -> str:
    """Descriptive name of the vendor table for a change `key` on `platform`."""
    m = _HOLLEY if platform == "holley" else _GM
    return m.get(key, key)
