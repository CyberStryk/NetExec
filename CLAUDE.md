# NetExec RDP Enhancements — Development Guide

## Repository layout

This is a fork of [Pennyw0rth/NetExec](https://github.com/Pennyw0rth/NetExec) at
[CyberStryk/NetExec](https://github.com/CyberStryk/NetExec). Active development
happens on the `rdp-enhancements` branch, rebased on upstream `main`.

```
origin  = https://github.com/Pennyw0rth/NetExec.git   (upstream, read-only)
fork    = github-nxc:CyberStryk/NetExec.git            (our fork, SSH via ~/.ssh/github_nxc_id_rsa)
```

SSH config entry `github-nxc` is in `~/.ssh/config`.

## What we've built

Six new RDP features, all in one commit on `rdp-enhancements`:

| Feature | Flag(s) | Files | Status |
|---------|---------|-------|--------|
| Elevated exec (UAC) | `--elevated` | `rdp.py` | Working |
| Splash screen login | `--rdp-splash` | `rdp.py` | Working |
| Sticky Keys / Utilman | `--stickykeys`, `--utilman` | `rdp.py` | Working |
| Clipboard file transfer | `--put-file`, `--get-file` | `rdp.py`, `clipboard_file.py` | Working |
| TSClient drive redirection | `--tsclient` | `rdp.py` | **Needs work** |
| DVC SOCKS5 proxy | `--rdp-dvc-socks5` | `rdp.py`, `dvc_socks.py`, `dvc_server.py` | Working |

## TSClient — current state and known gaps

The tsclient feature mounts a local directory as `\\tsclient\<name>` via aardwolf's
`FilesystemDriveProvider` (RDPEFS virtual channel). What exists today:

**Done:**
- CLI args: `--tsclient LOCAL_DIR`, `--tsclient-name NAME`, `--tsclient-ro`
- `make_tsclient_drive()` creates a `FilesystemDriveProvider` and attaches it to `iosettings.drives`
- Drive is attached in both normal and splash/backdoor RDP connection paths
- Debug logging of active channels after connection

**Needs work:**
- No standalone `--tsclient` action — it only works as a modifier to `-x`/`-X` commands
  - Should it support a standalone mode (connect + mount + hold session open)?
- No verification that the RDPEFS channel actually joined (only debug-logged, not checked)
- No e2e test commands in `tests/e2e_commands.txt`
- Not tested with `--put-file`/`--get-file` in combination
- Not tested with `--rdp-splash` or `--stickykeys`/`--utilman` (splash_ios path sets drives but untested)
- aardwolf's `FilesystemDriveProvider` is mature (read/write/create/delete/rename/directory listing)
  but its channel negotiation may silently fail — need error handling for that case
- No `--tsclient` support in DVC SOCKS5 sessions

## Test environment

QEMU/KVM Windows Server 2022 eval VM at `~/win-rdp-test/`:
- Host access: `127.0.0.1:3389` → guest RDP
- Credentials: `Administrator` / `Passw0rd!2024`
- NLA is off (splash mode works), stickykeys/utilman backdoors are planted
- Start: `~/win-rdp-test/run-vm.sh run`
- RDP-ready check: `~/win-rdp-test/probe_rdp.py`

## Development workflow

1. **Branch:** Work on `rdp-enhancements`, keep it rebased on upstream `main`
2. **Sync upstream:** `git fetch origin && git rebase origin/main`
3. **Lint:** `uv tool run ruff check .` (no pip/ruff in system — use `uv tool run`)
4. **Test:** Run actual nxc commands against the VM, not pytest
5. **e2e commands:** Add test commands to `tests/e2e_commands.txt`
6. **Commit style:** `feat(rdp): ...` or `fix(rdp): ...`
7. **Push:** `git push fork rdp-enhancements` (force-push after rebase is expected)

## Upstream PR requirements (AI_POLICY.md)

- AI usage **must** be disclosed (tool + model + extent)
- Must test against a live system with screenshots
- PR template must be filled out **by hand** (not AI-generated)
- Must reference an accepted issue or open a discussion first
- Binary artifacts (exe/dll) may need to be excluded or built in CI
- Run `ruff check .` before submitting

## Key files

```
nxc/protocols/rdp.py                    # Main RDP protocol handler
nxc/protocols/rdp/proto_args.py         # CLI argument definitions
nxc/protocols/rdp/clipboard_file.py     # Clipboard file push/pull
nxc/protocols/rdp/dvc_socks.py          # DVC SOCKS5 client channel + proxy
nxc/protocols/rdp/dvc_server.py         # DVC server deploy helpers
nxc/protocols/rdp/dvc_server.ps1        # Standalone PS1 DVC server
nxc/protocols/rdp/dvc_server.{exe,dll}  # Pre-built Windows binaries
tools/dvc-socks-server/                 # Rust source for exe/dll
tests/e2e_commands.txt                  # End-to-end test command catalog
FORK_README.md                          # Fork documentation
AGENTS.md                               # Coding conventions (from upstream)
AI_POLICY.md                            # Upstream AI contribution policy
```

## aardwolf RDPEFS internals (for tsclient work)

The drive redirection stack:
- `FilesystemDriveProvider` (`.venv/.../RDPEFS/providers/filesystem.py`) — maps local paths to RDP file operations
- `RDPEFSChannel` (`.venv/.../RDPEFS/channel.py`) — handles RDPDR protocol negotiation and I/O dispatch
- Drives are registered via `iosettings.drives = [provider]` before `RDPConnection` is created
- The channel handles device announce, capability exchange, and IRP dispatch automatically
- `FilesystemDriveProvider` supports: create, read, write, close, rename, delete, directory listing, volume info
- Path traversal protection is built into `_resolve()` — symlinks are blocked, `..` is rejected
