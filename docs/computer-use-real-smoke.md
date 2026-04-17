# Computer Use Real Smoke

This feature already has CI coverage through the scripted desktop runtime. Use this runbook when you need to verify that the built-in computer tools are operating a real desktop session on Linux or Windows.

## Prerequisites

### Windows

- Windows desktop session with an interactive user session
- PowerShell available on `PATH`
- Calculator available through `calc.exe`

### Linux

- Linux desktop session with `DISPLAY` or `WAYLAND_DISPLAY` available
- Window discovery/activation:
  - `wmctrl`, or
  - `xdotool` with `xprop`
- Input control:
  - `xdotool`
- One screenshot tool:
  - `gnome-screenshot`, or
  - `grim`, or
  - `scrot`, or
  - ImageMagick `import`, or
- `flameshot`
- A calculator app available through one of these launch paths:
  - `gtk-launch org.gnome.Calculator`
  - `gnome-calculator`
  - `kcalc`
  - `mate-calc`
  - `galculator`
  - `xcalc`

The Windows backend uses PowerShell for screenshot and window discovery, plus Win32 input injection for mouse and keyboard control. It expects a normal interactive desktop session.

The Linux backend is intended for X11 or XWayland-style desktop control. Scripted CI validation remains the default because many headless or pure Wayland environments do not expose window/input automation consistently.

If your session only exposes `xdotool` fallback discovery, some native Wayland apps can launch successfully but still remain invisible to X11 window enumeration. In that case either install `wmctrl` for better discovery coverage or validate with an app that is visible to the current automation stack.

When Agent Teams launches apps through the Linux backend, it now prefers X11-compatible GUI backends by default through `GDK_BACKEND=x11` and `QT_QPA_PLATFORM=xcb`, so GTK and Qt apps are more likely to land on the automatable path when the desktop session supports it.

## Start The Backend

Agent Teams now auto-detects the host OS backend. On Linux and Windows, the real desktop runtime is selected automatically unless you explicitly force scripted validation.

```bash
uv run --extra dev python -m uvicorn relay_teams.interfaces.server.app:app --host 127.0.0.1 --port 8000
```

If you need the scripted desktop runtime instead of the real OS backend, set:

```bash
export AGENT_TEAMS_COMPUTER_RUNTIME=fake
```

Keep the existing fake model server setup if you want a deterministic tool sequence for validation.

## Browser Validation

1. Open the app in Chrome.
2. Open the role settings for the validating role.
3. Ensure the role includes at least `launch_app`, `wait_for_window`, and `capture_screen`.
4. Set `Execution Surface` to `desktop`.
5. Start a run with a prompt such as:

```text
[computer-real-validation] 打开计算器，等待窗口出现，然后截图确认。
```

6. Approve the `launch_app` action when the approval card appears.
7. Confirm that a real calculator window appears on the desktop.
8. Confirm that the completed run includes:
   - a successful `launch_app`
   - a successful `wait_for_window`
   - a screenshot showing the calculator window

## Expected Result

- The OS really launches Calculator instead of only appending a fake window entry.
- The run timeline contains a real screenshot from the desktop session.
- Windows screenshot observations include the virtual-screen origin so multi-monitor coordinate picks stay aligned with pointer actions.
- The `launch_app` tool result includes `data.launched_command`, which shows the resolved executable used by the OS backend, such as `calc.exe` on Windows or `gnome-calculator` on Linux.
