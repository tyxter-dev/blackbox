# realtime

`realtime` owns bidirectional, low-latency model sessions. It is separate from
normal model turns because realtime sessions have transport, mutable session
configuration, client commands, audio/media streams, and long-lived provider
session references.

## Belongs Here

- Realtime provider protocol and session/config contracts.
- Realtime runtime and managed session helpers.
- Realtime event conversion helpers.
- Fake realtime provider for deterministic tests.
- Realtime provider adapters.

## Does Not Belong Here

- Normal single-turn model adapters.
- Agent session providers.
- General local tool execution outside realtime session flow.

## Boundary Note

Realtime providers may use the same provider registry and tool definitions as
normal model providers, but they should keep transport/session behavior explicit
instead of hiding it behind the regular turn API.
