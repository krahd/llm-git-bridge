# Bridge benchmarks

## Measurement convention

End-to-end acceptance probes separate bridge/transport overhead from optional repository commands such as tests.

The implementation that removed runtime `rclone mkdir` calls from registry and snapshot publication passed 27 tests before this probe.
