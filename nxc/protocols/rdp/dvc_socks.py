"""Client-side DVC SOCKS5 proxy.

Registers a custom RDP Dynamic Virtual Channel that multiplexes SOCKS5
connections through the RDP session.  A server-side component (PowerShell,
EXE, or DLL) running inside the RDP session opens the same named DVC and
relays TCP traffic to the requested destinations.
"""

import asyncio
import contextlib
import struct
import logging

from aardwolf.extensions.RDPEDYC.vchannels import VirtualChannelBase

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Multiplexing protocol
# ---------------------------------------------------------------------------

MSG_CONNECT = 0x01
MSG_CONNECT_OK = 0x02
MSG_CONNECT_ERR = 0x03
MSG_DATA = 0x04
MSG_CLOSE = 0x05

ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04


def pack_connect(stream_id: int, atyp: int, addr: bytes, port: int) -> bytes:
    return struct.pack("!BI", MSG_CONNECT, stream_id) + bytes([atyp]) + addr + struct.pack("!H", port)


def pack_data(stream_id: int, data: bytes) -> bytes:
    return struct.pack("!BIH", MSG_DATA, stream_id, len(data)) + data


def pack_close(stream_id: int) -> bytes:
    return struct.pack("!BI", MSG_CLOSE, stream_id)


def unpack_header(data: bytes):
    if len(data) < 5:
        return None, None, data
    msg_type = data[0]
    stream_id = struct.unpack("!I", data[1:5])[0]
    return msg_type, stream_id, data[5:]


# ---------------------------------------------------------------------------
# DVC virtual channel handler
# ---------------------------------------------------------------------------


class SocksDVChannel(VirtualChannelBase):
    def __init__(self, channel_name="dvc_socks"):
        VirtualChannelBase.__init__(self, channel_name)
        self.channel_ready = asyncio.Event()
        self._streams: dict[int, asyncio.Queue] = {}
        self._connect_futures: dict[int, asyncio.Future] = {}

    async def channel_init(self):
        self.channel_ready.set()
        logger.debug("DVC SOCKS channel initialized")
        return True, None

    async def channel_data_in(self, data: bytes):
        msg_type, stream_id, payload = unpack_header(data)
        if msg_type is None:
            return

        if msg_type == MSG_CONNECT_OK:
            fut = self._connect_futures.pop(stream_id, None)
            if fut and not fut.done():
                fut.set_result(True)

        elif msg_type == MSG_CONNECT_ERR:
            fut = self._connect_futures.pop(stream_id, None)
            if fut and not fut.done():
                fut.set_result(False)

        elif msg_type == MSG_DATA:
            if len(payload) < 2:
                return
            length = struct.unpack("!H", payload[:2])[0]
            body = payload[2:2 + length]
            queue = self._streams.get(stream_id)
            if queue is not None:
                await queue.put(body)

        elif msg_type == MSG_CLOSE:
            queue = self._streams.pop(stream_id, None)
            if queue is not None:
                await queue.put(None)
            self._connect_futures.pop(stream_id, None)

    async def channel_closed(self):
        for queue in self._streams.values():
            await queue.put(None)
        self._streams.clear()
        for fut in self._connect_futures.values():
            if not fut.done():
                fut.set_result(False)
        self._connect_futures.clear()
        self.channel_closed_evt.set()

    async def request_connect(self, stream_id: int, atyp: int, addr: bytes, port: int) -> bool:
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self._connect_futures[stream_id] = fut
        self._streams[stream_id] = asyncio.Queue()
        await self.channel_data_out(pack_connect(stream_id, atyp, addr, port))
        try:
            return await asyncio.wait_for(fut, timeout=30.0)
        except TimeoutError:
            self._connect_futures.pop(stream_id, None)
            self._streams.pop(stream_id, None)
            return False

    async def send_data(self, stream_id: int, data: bytes):
        await self.channel_data_out(pack_data(stream_id, data))

    async def send_close(self, stream_id: int):
        self._streams.pop(stream_id, None)
        await self.channel_data_out(pack_close(stream_id))

    def get_stream_queue(self, stream_id: int) -> asyncio.Queue | None:
        return self._streams.get(stream_id)

    def __deepcopy__(self, memo):
        return SocksDVChannel(self.channel_name)


# ---------------------------------------------------------------------------
# SOCKS5 server
# ---------------------------------------------------------------------------

SOCKS5_VER = 0x05
SOCKS5_AUTH_NONE = 0x00
SOCKS5_CMD_CONNECT = 0x01
SOCKS5_REP_SUCCESS = 0x00
SOCKS5_REP_GENERAL_FAILURE = 0x01
SOCKS5_REP_CONN_REFUSED = 0x05


class Socks5Server:
    def __init__(self, channel: SocksDVChannel, bind_host: str, bind_port: int):
        self.channel = channel
        self.bind_host = bind_host
        self.bind_port = bind_port
        self._next_stream_id = 1
        self._server = None

    def _alloc_stream_id(self) -> int:
        sid = self._next_stream_id
        self._next_stream_id += 1
        return sid

    async def start(self):
        self._server = await asyncio.start_server(self._handle_client, self.bind_host, self.bind_port)
        logger.info("SOCKS5 proxy listening on %s:%d", self.bind_host, self.bind_port)

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        stream_id = self._alloc_stream_id()
        try:
            if not await self._socks5_handshake(reader, writer):
                return
            atyp, addr, port = await self._socks5_request(reader, writer)
            if atyp is None:
                return

            ok = await self.channel.request_connect(stream_id, atyp, addr, port)
            if not ok:
                writer.write(self._socks5_reply(SOCKS5_REP_CONN_REFUSED))
                await writer.drain()
                return

            writer.write(self._socks5_reply(SOCKS5_REP_SUCCESS))
            await writer.drain()

            await self._relay(stream_id, reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError, OSError):
            pass
        finally:
            with contextlib.suppress(Exception):
                await self.channel.send_close(stream_id)
            writer.close()

    async def _socks5_handshake(self, reader, writer) -> bool:
        header = await reader.readexactly(2)
        if header[0] != SOCKS5_VER:
            return False
        nmethods = header[1]
        methods = await reader.readexactly(nmethods)
        if SOCKS5_AUTH_NONE not in methods:
            writer.write(bytes([SOCKS5_VER, 0xFF]))
            await writer.drain()
            return False
        writer.write(bytes([SOCKS5_VER, SOCKS5_AUTH_NONE]))
        await writer.drain()
        return True

    async def _socks5_request(self, reader, writer):
        header = await reader.readexactly(4)
        if header[1] != SOCKS5_CMD_CONNECT:
            writer.write(self._socks5_reply(0x07))
            await writer.drain()
            return None, None, None

        atyp = header[3]
        if atyp == ATYP_IPV4:
            addr = await reader.readexactly(4)
        elif atyp == ATYP_DOMAIN:
            length = (await reader.readexactly(1))[0]
            domain = await reader.readexactly(length)
            addr = bytes([length]) + domain
        elif atyp == ATYP_IPV6:
            addr = await reader.readexactly(16)
        else:
            writer.write(self._socks5_reply(0x08))
            await writer.drain()
            return None, None, None

        port_bytes = await reader.readexactly(2)
        port = struct.unpack("!H", port_bytes)[0]
        return atyp, addr, port

    @staticmethod
    def _socks5_reply(rep: int) -> bytes:
        return bytes([SOCKS5_VER, rep, 0x00, ATYP_IPV4, 0, 0, 0, 0, 0, 0])

    async def _relay(self, stream_id: int, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        queue = self.channel.get_stream_queue(stream_id)
        if queue is None:
            return

        async def dvc_to_client():
            while True:
                data = await queue.get()
                if data is None:
                    break
                writer.write(data)
                await writer.drain()

        async def client_to_dvc():
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                await self.channel.send_data(stream_id, data)

        tasks = [
            asyncio.create_task(dvc_to_client()),
            asyncio.create_task(client_to_dvc()),
        ]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            for t in done:
                t.result()
        except Exception:
            for t in tasks:
                t.cancel()


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------


async def run_socks_proxy(channel: SocksDVChannel, bind_host: str, bind_port: int, disconnected_evt: asyncio.Event):
    server = Socks5Server(channel, bind_host, bind_port)
    await server.start()
    try:
        await disconnected_evt.wait()
    finally:
        await server.stop()
