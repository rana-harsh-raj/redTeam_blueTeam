"""archkit -- RedGrid architecture knowledge layer (M8).

Immutable, content-addressed architecture snapshots compiled from committed inputs (no Docker, no wall clock),
an in-process index + query library, a CLI (`python3 -m archkit`) and a read-only loopback HTTP service.

Static architecture (ArchitectureSnapshot) is kept apart from RuntimeInstance (what a boot ran),
EvidenceBundle (what journeys observed), AcceptanceRecord (what a milestone gate decided) and the
ProductionUnknown registry. See archkit/SCHEMA.md.
"""
__version__ = "1.0.0"
SCHEMA_VERSION = "m8.1"
