"""twinfactory -- M9 Isolated Twin Factory.

Takes an immutable M8 ArchitectureSnapshot (+ its normalized service recipes), a runtime profile, an instance id, a
synthetic-data seed and an execution backend, and produces an independently runnable, isolated twin instance with a
durable RuntimeInstance record. Every instance owns its execution directory, generated configuration, fixtures,
secrets, Compose project/namespace, networks, volumes, published ports, runtime evidence and journey output.

Backends are execution boundaries (a separate Docker daemon / VM per instance); the factory interface is
backend-independent. stdlib + PyYAML; docker/colima are shelled out to only from the backend and lifecycle layers.
"""
__version__ = "1.0.0"
SCHEMA_VERSION = "m9.1"
