from nxc.helpers.args import DisplayDefaultsNotNone


def proto_args(parser, parents):
    rdp_parser = parser.add_parser("rdp", help="own stuff using RDP", parents=parents, formatter_class=DisplayDefaultsNotNone)
    rdp_parser.add_argument("-H", "--hash", metavar="HASH", dest="hash", nargs="+", default=[], help="NTLM hash(es) or file(s) containing NTLM hashes")
    rdp_parser.add_argument("--port", type=int, default=3389, help="RDP port")
    rdp_parser.add_argument("--rdp-timeout", type=int, default=5, help="RDP timeout on socket connection")
    rdp_parser.add_argument("--nla-screenshot", action="store_true", help="Screenshot RDP login prompt if NLA is disabled")

    dgroup = rdp_parser.add_mutually_exclusive_group()
    dgroup.add_argument("-d", metavar="DOMAIN", dest="domain", type=str, default=None, help="domain to authenticate to")
    dgroup.add_argument("--local-auth", action="store_true", help="authenticate locally to each target")

    egroup = rdp_parser.add_argument_group("Screenshot", "Remote Desktop Screenshot")
    egroup.add_argument("--screenshot", action="store_true", help="Screenshot RDP if connection success")
    egroup.add_argument("--screentime", type=int, default=10, help="Time to wait for desktop image")
    egroup.add_argument("--res", default="1024x768", help="Resolution in WIDTHxHEIGHT format")

    cgroup = rdp_parser.add_argument_group("Command Execution")
    cgroup.add_argument("-x", metavar="COMMAND", dest="execute", help="execute the specified command")
    cgroup.add_argument("-X", metavar="PS_COMMAND", dest="ps_execute", help="execute the specified PowerShell command")
    cgroup.add_argument("--cmd-delay", type=int, default=5, help="Sleep time before executing command")
    cgroup.add_argument("--clipboard-delay", type=int, default=30, help="Maximum time to wait for clipboard initialization (seconds)")
    cgroup.add_argument("--no-output", action="store_true", help="do not retrieve command output")

    ugroup = rdp_parser.add_argument_group("Elevated Execution (UAC)")
    ugroup.add_argument("--elevated", action="store_true", help="open a high-integrity (elevated) shell via Ctrl+Shift+Enter and handle the UAC prompt")
    ugroup.add_argument("--uac-username", metavar="USER", help="username for the UAC credential prompt (default: login username)")
    ugroup.add_argument("--uac-password", metavar="PASS", help="password for the UAC credential prompt (default: login password)")
    ugroup.add_argument("--uac-settle", type=float, default=3.0, help="seconds to wait for the UAC dialog to appear")

    sgroup = rdp_parser.add_argument_group("Splash Screen / Accessibility Backdoors")
    sgroup.add_argument("--rdp-splash", action="store_true", help="connect via TLS (no NLA), type credentials at the Windows logon screen, then execute")
    sgroup.add_argument("--stickykeys", action="store_true", help="fire SHIFTx5 at the splash logon screen to trigger the sethc.exe backdoor, type command into resulting SYSTEM shell")
    sgroup.add_argument("--utilman", action="store_true", help="fire Win+U at the splash logon screen to trigger the utilman.exe backdoor, type command into resulting SYSTEM shell")
    sgroup.add_argument("--field-order", choices=["user-tab-pass", "pass-only"], default="user-tab-pass", help="how to fill the logon screen fields in splash mode (default: user-tab-pass)")
    sgroup.add_argument("--settle-after-trigger", type=float, default=3.0, help="seconds to wait after firing the accessibility trigger (default: 3)")

    fgroup = rdp_parser.add_argument_group("Clipboard File Transfer")
    fgroup.add_argument("--put-file", nargs=2, metavar=("LOCAL_FILE", "REMOTE_DIR"), default=None, help="push a local file to the remote machine via RDP clipboard paste")
    fgroup.add_argument("--get-file", nargs=2, metavar=("REMOTE_FILE", "LOCAL_DIR"), default=None, help="pull a remote file to the local machine via RDP clipboard download")

    tgroup = rdp_parser.add_argument_group("TSClient Drive Redirection", "Mount a local directory as \\\\tsclient\\<name> on the remote machine")
    tgroup.add_argument("--tsclient", metavar="LOCAL_DIR", default=None, help="local directory to mount as a tsclient share (appears as \\\\tsclient\\<name> on remote)")
    tgroup.add_argument("--tsclient-name", metavar="NAME", default="nxc", help="share name for the tsclient mount (default: nxc)")
    tgroup.add_argument("--tsclient-ro", action="store_true", help="mount the share as read-only")

    dvc_group = rdp_parser.add_argument_group("DVC SOCKS5 Proxy")
    dvc_group.add_argument("--rdp-dvc-socks5", nargs="?", const="127.0.0.1:1080", default=None, metavar="BIND", help="set up a SOCKS5 proxy via RDP DVC tunnel (default: 127.0.0.1:1080)")
    dvc_group.add_argument("--dvc-method", choices=["powershell", "exe", "dll"], default="powershell", help="server-side deployment method (default: powershell)")
    dvc_group.add_argument("--dvc-channel", default="dvc_socks", metavar="NAME", help="DVC channel name (default: dvc_socks)")

    return parser
