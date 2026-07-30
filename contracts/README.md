# Convoy execution contracts

These schemas are the only interface the application should depend on.

- `RunSpec` describes an immutable execution request.
- `WorldSpec` describes a resettable company environment.
- `RunEvent` is the append-only observation stream emitted by every executor.

The current version is `convoy.ai/v1alpha1`. Providers may add internal fields
to their own resolved records, but provider-specific fields must not leak into
these contracts. Breaking changes require a new `apiVersion`.
