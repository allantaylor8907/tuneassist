# Test fixtures

Drop your real CSV exports here to use as regression fixtures. Expected behavior
for known logs is documented in DESIGN.md ("Known fixtures and expected behavior").

Suggested set:
- silverado_cruise.csv   -> flat ~-4.7% rich, global-offset shape
- ride42.csv             -> median ~-0.8%, well-sorted (has AEM wideband)
- jr42.csv               -> REJECTED (cold, never warmed up)
- protuner12.csv         -> REJECTED (spark/diag log, no fuel channels)
- holley_drive.csv       -> Holley Learn-based correction

NOTE: real HPTuners exports embed the VIN in the preamble; scrub if sharing.
