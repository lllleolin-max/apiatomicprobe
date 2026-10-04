# Official prior art checked 2026-10-05

[Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests)
defines reuse, parameter matching, cached outcomes including errors, execution
conflict/validation exceptions and eventual pruning. This project's limited
session-window order-lab policy does not implement all of those semantics.

[Porcupine official repository](https://github.com/anishathalye/porcupine)
provides executable sequential models, timed operation/event histories, bounded
checking and visualizations, using known linearizability algorithms and
P-compositionality. This finite Python search credits that established work and
does not claim first invention or equivalent performance. Its useful boundary
is explicit HTTP adapter capture tied to a native business-effect witness and
reviewable replayable histories in a small disposable order deployment.

Only official sources support these comparisons. A documentation omission is
not treated as evidence that another tool cannot do something. Current lab
comparisons measure serial versus concurrent history testing, not a fabricated
weak Porcupine/Stripe competitor. Actual independent factorial oracle execution
is recorded; an unexecuted upstream tool is not described as a measured baseline.
