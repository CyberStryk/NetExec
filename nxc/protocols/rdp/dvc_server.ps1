# DVC SOCKS5 Server — PowerShell implementation
# Runs inside the RDP session, opens a named DVC channel via WTS API,
# and relays multiplexed TCP streams per the shared protocol.

param(
    [string]$ChannelName = "dvc_socks"
)

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public class WtsApi {
    public const int WTS_CHANNEL_OPTION_DYNAMIC = 0x00000001;
    public const int WTS_CURRENT_SESSION = -1;

    [DllImport("wtsapi32.dll", SetLastError = true, CharSet = CharSet.Auto)]
    public static extern IntPtr WTSVirtualChannelOpenEx(
        int SessionId,
        string pVirtualName,
        int flags);

    [DllImport("wtsapi32.dll", SetLastError = true)]
    public static extern bool WTSVirtualChannelRead(
        IntPtr hChannel,
        int TimeOut,
        byte[] Buffer,
        int BufferSize,
        out int pBytesRead);

    [DllImport("wtsapi32.dll", SetLastError = true)]
    public static extern bool WTSVirtualChannelWrite(
        IntPtr hChannel,
        byte[] Buffer,
        int Length,
        out int pBytesWritten);

    [DllImport("wtsapi32.dll", SetLastError = true)]
    public static extern bool WTSVirtualChannelClose(IntPtr hChannel);
}
"@

$MSG_CONNECT     = 0x01
$MSG_CONNECT_OK  = 0x02
$MSG_CONNECT_ERR = 0x03
$MSG_DATA        = 0x04
$MSG_CLOSE       = 0x05

$ATYP_IPV4   = 0x01
$ATYP_DOMAIN = 0x03
$ATYP_IPV6   = 0x04

$streams = @{}
$hChannel = [IntPtr]::Zero

function Write-DvcMsg([byte[]]$msg) {
    $written = 0
    [WtsApi]::WTSVirtualChannelWrite($hChannel, $msg, $msg.Length, [ref]$written) | Out-Null
}

function Send-ConnectOk([uint32]$sid) {
    $buf = New-Object byte[] 5
    $buf[0] = $MSG_CONNECT_OK
    [Array]::Copy([BitConverter]::GetBytes([System.Net.IPAddress]::HostToNetworkOrder([int]$sid)), 0, $buf, 1, 4)
    Write-DvcMsg $buf
}

function Send-ConnectErr([uint32]$sid, [byte]$errno = 1) {
    $buf = New-Object byte[] 6
    $buf[0] = $MSG_CONNECT_ERR
    [Array]::Copy([BitConverter]::GetBytes([System.Net.IPAddress]::HostToNetworkOrder([int]$sid)), 0, $buf, 1, 4)
    $buf[5] = $errno
    Write-DvcMsg $buf
}

function Send-Data([uint32]$sid, [byte[]]$data) {
    $buf = New-Object byte[] (7 + $data.Length)
    $buf[0] = $MSG_DATA
    [Array]::Copy([BitConverter]::GetBytes([System.Net.IPAddress]::HostToNetworkOrder([int]$sid)), 0, $buf, 1, 4)
    [Array]::Copy([BitConverter]::GetBytes([System.Net.IPAddress]::HostToNetworkOrder([int16]$data.Length)), 0, $buf, 5, 2)
    [Array]::Copy($data, 0, $buf, 7, $data.Length)
    Write-DvcMsg $buf
}

function Send-Close([uint32]$sid) {
    $buf = New-Object byte[] 5
    $buf[0] = $MSG_CLOSE
    [Array]::Copy([BitConverter]::GetBytes([System.Net.IPAddress]::HostToNetworkOrder([int]$sid)), 0, $buf, 1, 4)
    Write-DvcMsg $buf
}

function Close-Stream([uint32]$sid) {
    if ($streams.ContainsKey($sid)) {
        $s = $streams[$sid]
        try { $s.Client.Close() } catch {}
        try { $s.ReadTask.Dispose() } catch {}
        $streams.Remove($sid)
    }
}

function Handle-Connect([uint32]$sid, [byte[]]$payload) {
    if ($payload.Length -lt 3) {
        Send-ConnectErr $sid
        return
    }
    $atyp = $payload[0]
    $host = ""
    $portOffset = 0

    if ($atyp -eq $ATYP_IPV4) {
        $host = "$($payload[1]).$($payload[2]).$($payload[3]).$($payload[4])"
        $portOffset = 5
    } elseif ($atyp -eq $ATYP_DOMAIN) {
        $dlen = $payload[1]
        $host = [System.Text.Encoding]::ASCII.GetString($payload, 2, $dlen)
        $portOffset = 2 + $dlen
    } elseif ($atyp -eq $ATYP_IPV6) {
        $ipBytes = New-Object byte[] 16
        [Array]::Copy($payload, 1, $ipBytes, 0, 16)
        $host = ([System.Net.IPAddress]::new($ipBytes)).ToString()
        $portOffset = 17
    } else {
        Send-ConnectErr $sid
        return
    }

    $port = ([int]$payload[$portOffset] -shl 8) -bor [int]$payload[$portOffset + 1]

    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect($host, $port)
        $stream = $tcp.GetStream()

        $readBuf = New-Object byte[] 4096
        $readTask = $stream.ReadAsync($readBuf, 0, $readBuf.Length)

        $streams[$sid] = @{
            Client = $tcp
            Stream = $stream
            ReadBuf = $readBuf
            ReadTask = $readTask
        }
        Send-ConnectOk $sid
    } catch {
        Send-ConnectErr $sid
    }
}

function Handle-Data([uint32]$sid, [byte[]]$payload) {
    if ($payload.Length -lt 2) { return }
    $len = ([int]$payload[0] -shl 8) -bor [int]$payload[1]
    if ($payload.Length -lt (2 + $len)) { return }

    if (-not $streams.ContainsKey($sid)) { return }
    $s = $streams[$sid]
    try {
        $s.Stream.Write($payload, 2, $len)
    } catch {
        Close-Stream $sid
        Send-Close $sid
    }
}

function Poll-Streams {
    $toRemove = @()
    foreach ($kv in $streams.GetEnumerator()) {
        $sid = $kv.Key
        $s = $kv.Value
        if ($s.ReadTask.IsCompleted) {
            try {
                $n = $s.ReadTask.Result
                if ($n -gt 0) {
                    $chunk = New-Object byte[] $n
                    [Array]::Copy($s.ReadBuf, $chunk, $n)
                    Send-Data $sid $chunk
                    $s.ReadTask = $s.Stream.ReadAsync($s.ReadBuf, 0, $s.ReadBuf.Length)
                    $streams[$sid] = $s
                } else {
                    $toRemove += $sid
                }
            } catch {
                $toRemove += $sid
            }
        }
    }
    foreach ($sid in $toRemove) {
        Close-Stream $sid
        Send-Close $sid
    }
}

# --- Main ---

$hChannel = [WtsApi]::WTSVirtualChannelOpenEx(
    [WtsApi]::WTS_CURRENT_SESSION,
    $ChannelName,
    [WtsApi]::WTS_CHANNEL_OPTION_DYNAMIC
)

if ($hChannel -eq [IntPtr]::Zero) {
    exit 1
}

$readBuf = New-Object byte[] 65536

try {
    while ($true) {
        $bytesRead = 0
        $ok = [WtsApi]::WTSVirtualChannelRead($hChannel, 50, $readBuf, $readBuf.Length, [ref]$bytesRead)

        if ($ok -and $bytesRead -ge 13) {
            $msgType = $readBuf[8]
            $sidBytes = New-Object byte[] 4
            [Array]::Copy($readBuf, 9, $sidBytes, 0, 4)
            [Array]::Reverse($sidBytes)
            $sid = [BitConverter]::ToUInt32($sidBytes, 0)
            $payloadLen = $bytesRead - 13
            $payload = New-Object byte[] $payloadLen
            if ($payloadLen -gt 0) {
                [Array]::Copy($readBuf, 13, $payload, 0, $payloadLen)
            }

            switch ($msgType) {
                $MSG_CONNECT { Handle-Connect $sid $payload }
                $MSG_DATA    { Handle-Data $sid $payload }
                $MSG_CLOSE   { Close-Stream $sid }
            }
        }

        Poll-Streams

        if ($streams.Count -eq 0 -and -not $ok) {
            Start-Sleep -Milliseconds 10
        }
    }
} finally {
    foreach ($sid in @($streams.Keys)) {
        Close-Stream $sid
    }
    [WtsApi]::WTSVirtualChannelClose($hChannel) | Out-Null
}
