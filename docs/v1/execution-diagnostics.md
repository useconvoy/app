# Explain where a mission stopped

Failed, cancelled and uncertain executions retain a small `summary.failure`
record in the same durable terminal report as their outcome. The console shows
the component, execution stage and failure category in plain language; the full
coordinator summary remains available under Technical details. Older episodes
without this record retain their original detail.

The record has schema version 1, a known component and phase, a bounded category,
and the robot's observation of whether the original authorization had elapsed.
An HTTP rejection adds its status; a particular inference request adds its
original request ID and sequence when available. It contains no response body,
endpoint, credentials, camera image or model output. A failed terminal upload
retries this exact journaled report without issuing another command.

HTTP status is evidence of what the endpoint reported, not a root-cause diagnosis.
A 401 means authorization was rejected; it does not by itself mean expiry. The
separate `authorization_elapsed` flag compares the robot's wall clock with the
original expiry at failure time. Transport timeouts and local admission deadlines
are labelled as deadline failures. An unavailable connection/service is distinct
from a malformed response or a service-reported runtime failure. The record
does not claim which network device or internal model operation caused a failure.

The coordinator records stages across management admission, planner session and
proposal, policy session and inference, action validation, and adapter execution.
A possibly applied robot command still reports `unknown`. If adapter cleanup
also fails, that uncertainty takes precedence and the original failure remains
in `preceding_failure`. No diagnostic changes execution authority, extends a
deadline or retries an uncertain command. Readiness failures before a mission
and asynchronous remote-session cleanup remain separate operational concerns.

Focused coordinator cases cover rejected, late, malformed and declined plans,
policy-session failure, expired versus unexpired authorization and exact report
retry. A small real HTTP check verifies that error payloads do not escape, that
malformed/oversized/truncated responses are rejected, and that connection failure
is classified. Browser acceptance observes the persisted cancellation diagnosis
through the ordinary API and console after a real simulated mission.
