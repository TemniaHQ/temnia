# Validate route capacity at worker boot — 2026-09-09

The final startup audit found that a valid route snapshot can contain a required-seat candidate
whose output ceiling is below `HARNESS_MAX_OUTPUT_TOKENS`, or whose context cannot hold that output
plus the fixed protocol allowance and any request content. The worker currently starts, accepts a
chapter run, and fails later during preparation. A smaller failover candidate can hide the problem
until an earlier route fails. No paid dispatch occurs, but this is a configuration error that should
prevent the worker from polling.

For every distinct route referenced by `summary`, `propose` or `verify`, require the configured
output ceiling to fit its route maximum, and require context headroom beyond that ceiling plus the
shared protocol allowance. Reuse the current estimator or its shared constants rather than copying
a second cost/context convention. A refusal must name the route and `HARNESS_MAX_OUTPUT_TOKENS`.
Apply this to recorded and gateway mode; leave disabled workers untouched. Never clamp the cap,
silently remove a candidate or change the frozen snapshot. Actual prompt/schema sizes still receive
their existing per-request checks; passing boot is not proof that arbitrary source content fits.

This is a local read-only startup validation: no external calls, model credentials printed, database
mutation, new retry behavior, or changed run configuration. Current in-flight runs and immutable route
identities retain their meaning. Operators correct the setting or deliberately qualify a new snapshot
before restarting. Verify the first and a later failover candidate, missing headroom including equality,
an exact valid boundary, the existing valid recorded fixture and disabled-mode behavior. Run focused
settings/route tests and static checks, then the final full release gate. Work only in the integration
checkout; the live speech benchmark keeps its original qualified worker fingerprint. Record this later
startup-only change separately from the benchmark's frozen builds.
