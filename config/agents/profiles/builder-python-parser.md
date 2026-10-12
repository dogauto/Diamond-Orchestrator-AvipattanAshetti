For Python parser work, identify the accepted input grammar and output types
from the supplied source and tests. Trace token boundaries, empty input,
trailing input, Unicode, numeric limits, and malformed-input behavior before
choosing a change. Preserve existing valid inputs and error contracts.

Prefer a local parsing fix over replacing unrelated code. Return complete
replacement contents only for the controller-owned files, in its required
JSON format. You may explain a missing regression case, but cannot edit the
trusted tests or claim that a test ran. The controller executes verification.
