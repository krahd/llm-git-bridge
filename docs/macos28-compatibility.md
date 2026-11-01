# macOS 28 / Rosetta compatibility

As of November 2026, Apple has announced that macOS 27 is the last general-purpose release supporting Rosetta for Intel-only macOS software on Apple silicon. Starting with macOS 28, Rosetta will be retained only for some older games. Apple may warn users when they open an Intel-only application **or an application that uses an Intel-only component**.

Authoritative sources:
- [Apple Support: Using Intel-based apps on a Mac with Apple silicon](https://support.apple.com/en-gb/102527)
- [Apple Developer: Upcoming changes to Rosetta support](https://developer.apple.com/news/?id=w5ngl9k2)

This warning is **not evidence that Local Executor Bridge's protocol is inherently incompatible**. Determine the exact executable named by macOS before drawing that conclusion.

## Inspect an existing installation (read-only; Terminal on the Mac)

In an **interactive Mac Terminal**, run:

```bash
APP="$HOME/.local/share/local-executor-bridge/Local Executor Approval.app/Contents/MacOS/local-executor-approval"
if [ -f "$APP" ]; then
  /usr/bin/file "$APP"
  /usr/bin/lipo -archs "$APP"
else
  printf '%s\n' "Native approval app not found at expected default path"
fi
```

On Apple silicon, the approved native app must include `arm64` in the `lipo -archs` result. `x86_64` alone indicates an Intel-only build and is a plausible reason for the macOS warning. `arm64 x86_64` (Universal) is also fine.

If the app already includes arm64, macOS may be warning about **a different executable**. Identify the name in the notification or inspect **System Settings > General > About > Intel-based Apps** on macOS 27, and use Finder > Get Info > Kind on the specific application. Apple states that apps can also depend on Intel-only components.

Check the Python actually configured for the daemon (do not infer it from your shell's `python3`):

```bash
PLIST="$HOME/Library/LaunchAgents/net.laurenzo.local-executor-bridge.plist"
if [ -f "$PLIST" ]; then
  PYTHON=$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:0' "$PLIST")
  /usr/bin/file "$PYTHON"
  "$PYTHON" -c 'import platform; print("Python machine:", platform.machine())'
fi
```

If that interpreter reports `x86_64` when invoked on Apple silicon, inspect whether a native/Universal Python distribution is available and ensure the guarded release configuration selects it. The interpreter and any external tools (notably `rclone`) need independent compatibility checks; an ARM64 approval app does **not** prove an ARM64 execution chain. You may check `rclone version` for its `os/arch` field on a known selected executable.

These commands inspect files and launch the configured interpreter for a one-line query; they **do not** restart the daemon or alter its installation. If you receive the warning while running a diagnostic, record the exact application name. Never remove Rosetta or delete the existing installation merely to dismiss the warning.

## Future installation guard

The v6 installer now selects the Swift compilation architecture using the physical Mac's `hw.optional.arm64`, not the calling shell's `uname -m`, which may be translated under Rosetta. On Apple silicon the native approval executable must contain an `arm64` Mach-O slice; the installer checks the built file using `lipo -archs` and fails rather than packaging an Intel-only approval app.

The macOS CI workflow separately cross-compiles the native approval app for ARM64 and validates the result. This is a **build-artifact guard**, not a macOS 28 end-to-end acceptance test. Compatibility also depends on the actual Python interpreter, rclone, sandboxing APIs and macOS permissions.

## Safe repair

An operator must stage a qualified new release and apply the guarded [v6 production upgrade procedure](../shell_bridge/V6_PRODUCTION_OPERATIONS.md) **from an interactive Terminal**. Never re-run the new-install bootstrap on top of a live state directory, start a second daemon, delete journals/locks, or upgrade the daemon through its own request mailbox.

A macOS alert does not require immediate action if the existing bridge remains functional on the currently installed OS. Before a future macOS 28 upgrade, establish that the native approval app, configured interpreter, rclone and core sandbox work without Rosetta, then complete the existing [third-party acceptance checklist](https://github.com/krahd/llm-git-bridge/issues/84).
