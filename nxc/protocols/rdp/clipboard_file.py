"""Native RDP clipboard file transfer via the CLIPRDR virtual channel.

Push (local -> remote):
    Announce local files on the RDP clipboard (FileGroupDescriptorW),
    open Explorer on the remote desktop, and paste (Ctrl+V).

Pull (remote -> local):
    Run PowerShell on the remote to SetFileDropList, wait for the
    CLIPRDR channel to deliver the file list, then download each file.
"""

import asyncio
from pathlib import Path

from aardwolf.connection import RDPConnection
from aardwolf.commons.iosettings import RDPIOSettings
from aardwolf.commons.queuedata.constants import VIDEO_FORMAT
from aardwolf.commons.queuedata.clipboard import RDP_CLIPBOARD_DATA_FILELIST
from aardwolf.protocol.x224.constants import SUPP_PROTOCOLS
from asyauth.common.credentials.ntlm import NTLMCredential
from asyauth.common.constants import asyauthSecret
from aardwolf.extensions.RDPECLIP.protocol.formatdataresponse import (
    CLIPRDR_FILEDESCRIPTOR,
    CLIPRDR_FILELIST,
    FD_FLAGS,
    FILE_ATTRIBUTE,
)


def make_descriptor(path):
    fd = CLIPRDR_FILEDESCRIPTOR()
    fd.flags = FD_FLAGS.ATTRIBUTES | FD_FLAGS.FILESIZE
    if path.is_dir():
        fd.fileAttributes = FILE_ATTRIBUTE.DIRECTORY
        fd.fileSize = 0
    else:
        fd.fileAttributes = FILE_ATTRIBUTE.NORMAL | FILE_ATTRIBUTE.ARCHIVE
        fd.fileSize = path.stat().st_size
    fd.fileSizeHigh = fd.fileSize >> 32
    fd.fileSizeLow = fd.fileSize & 0xFFFFFFFF
    fd.fileName = path.name
    return fd


async def announce_files(conn, paths):
    clipboard = conn.iosettings.clipboard
    clipboard._file_paths = []

    file_list = CLIPRDR_FILELIST()
    for p in paths:
        clipboard._file_paths.append(p)
        file_list.fileDescriptorArray.append(make_descriptor(p))

    data = RDP_CLIPBOARD_DATA_FILELIST(data=file_list, datatype=clipboard.file_copy_id)
    await clipboard.set_data(data)


async def wait_push_done(conn, transfer_timeout, quiet=3.0):
    clipboard = conn.iosettings.clipboard
    activity = [asyncio.get_event_loop().time()]
    original = clipboard._file_provider.get_file_data

    def tracking_get_file_data(name, start, count):
        activity[0] = asyncio.get_event_loop().time()
        return original(name, start, count)

    clipboard._file_provider.get_file_data = tracking_get_file_data
    try:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + transfer_timeout
        saw_activity = False
        while loop.time() < deadline:
            await asyncio.sleep(0.5)
            now = loop.time()
            if activity[0] > (now - quiet - 0.5):
                saw_activity = True
            if saw_activity and (now - activity[0]) > quiet:
                return True
        return saw_activity
    finally:
        clipboard._file_provider.get_file_data = original


async def wait_clipboard_ready(rdp_obj):
    await asyncio.sleep(rdp_obj.args.cmd_delay)
    timeout_counter = 0
    while timeout_counter < (rdp_obj.args.clipboard_delay * 10):
        try:
            data = await asyncio.wait_for(rdp_obj.conn.ext_out_queue.get(), timeout=0.1)
            if hasattr(data, "type") and data.type.name == "CLIPBOARD_READY":
                return True
        except TimeoutError:
            timeout_counter += 1
            continue
        except (ConnectionResetError, ConnectionError, OSError) as e:
            rdp_obj.logger.fail(f"Connection lost waiting for clipboard: {e}")
            return False
    return False


def resolve_local_paths(rdp_obj, local_paths):
    resolved = []
    for p in local_paths:
        rp = Path(p).resolve()
        if not rp.exists():
            rdp_obj.logger.fail(f"Local file not found: {rp}")
            return None
        resolved.append(rp)
    return resolved


def resolve_dest_dir(local_dest):
    dest = Path(local_dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def is_splash_mode(rdp_obj):
    return rdp_obj.args.rdp_splash or rdp_obj.args.stickykeys or rdp_obj.args.utilman


def create_file_iosettings(rdp_obj):
    ios = RDPIOSettings()
    ios.video_width = rdp_obj.iosettings.video_width
    ios.video_height = rdp_obj.iosettings.video_height
    ios.video_bpp_min = 15
    ios.video_bpp_max = 32
    ios.video_out_format = VIDEO_FORMAT.PNG
    ios.clipboard_use_pyperclip = False
    return ios


async def connect_session(rdp_obj):
    ios = create_file_iosettings(rdp_obj)
    if is_splash_mode(rdp_obj):
        ios.supported_protocols = SUPP_PROTOCOLS.SSL
        auth = NTLMCredential(secret="", username="", domain="", stype=asyauthSecret.PASS)
    else:
        auth = rdp_obj.auth
    rdp_obj.conn = RDPConnection(iosettings=ios, target=rdp_obj.target, credentials=auth)
    await rdp_obj.connect_rdp()


async def splash_login(rdp_obj):
    if rdp_obj.args.stickykeys:
        await rdp_obj.fire_stickykeys()
        await asyncio.sleep(rdp_obj.args.settle_after_trigger)
    elif rdp_obj.args.utilman:
        await rdp_obj.fire_utilman()
        await asyncio.sleep(rdp_obj.args.settle_after_trigger)
    elif rdp_obj.args.rdp_splash:
        await rdp_obj.login_at_splash()


async def push_session(rdp_obj, resolved, remote_dest):
    try:
        await connect_session(rdp_obj)
    except Exception as e:
        rdp_obj.logger.fail(f"RDP connection failed: {e}")
        return

    try:
        rdp_obj.logger.display("Waiting for clipboard channel...")
        if not await wait_clipboard_ready(rdp_obj):
            rdp_obj.logger.fail("Clipboard channel did not initialize")
            return

        if is_splash_mode(rdp_obj):
            await splash_login(rdp_obj)

        rdp_obj.logger.display(f"Announcing {len(resolved)} file(s) on clipboard")
        await announce_files(rdp_obj.conn, resolved)
        await asyncio.sleep(0.5)

        rdp_obj.logger.debug(f"Opening Explorer at {remote_dest}")
        if not await rdp_obj._send_win_r():
            rdp_obj.logger.fail("Failed to open Run dialog")
            return

        await rdp_obj._send_keystrokes(f'explorer "{remote_dest}"', delay=0.01)
        await rdp_obj._send_enter()
        await asyncio.sleep(rdp_obj.args.cmd_delay)

        rdp_obj.logger.debug("Pasting (Ctrl+V)")
        await rdp_obj.send_scancode_combo("VK_LCONTROL", "VK_V")

        await asyncio.sleep(1.5)
        await rdp_obj._send_enter()

        rdp_obj.logger.display("Waiting for file transfer to complete...")
        done = await wait_push_done(rdp_obj.conn, transfer_timeout=120.0)
        if done:
            for rp in resolved:
                rdp_obj.logger.success(f"Pushed {rp.name} -> {remote_dest}")
        else:
            rdp_obj.logger.fail("Transfer timed out or no data was requested")

        await rdp_obj.send_scancode_combo("VK_LMENU", "VK_F4")
        await asyncio.sleep(0.5)

    except (ConnectionResetError, ConnectionError, OSError) as e:
        rdp_obj.logger.fail(f"Connection error during push: {e}")
    except Exception as e:
        rdp_obj.logger.fail(f"Push failed: {e}")
    finally:
        await rdp_obj.terminate_conn()


async def pull_session(rdp_obj, remote_paths, dest):
    try:
        await connect_session(rdp_obj)
    except Exception as e:
        rdp_obj.logger.fail(f"RDP connection failed: {e}")
        return

    try:
        rdp_obj.logger.display("Waiting for clipboard channel...")
        if not await wait_clipboard_ready(rdp_obj):
            rdp_obj.logger.fail("Clipboard channel did not initialize")
            return

        if is_splash_mode(rdp_obj):
            await splash_login(rdp_obj)

        rdp_obj.conn.set_download_directory(str(dest))
        rdp_obj.conn.iosettings.clipboard._remote_file_list = []

        add_lines = "; ".join(f"$c.Add('{p}')" for p in remote_paths)
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$c = New-Object System.Collections.Specialized.StringCollection; "
            f"{add_lines}; "
            "[System.Windows.Forms.Clipboard]::SetFileDropList($c)"
        )

        rdp_obj.logger.debug("Triggering remote clipboard copy via PowerShell")
        await asyncio.sleep(rdp_obj.args.cmd_delay)

        if not await rdp_obj._send_win_r():
            rdp_obj.logger.fail("Failed to open Run dialog")
            return

        full_cmd = f'powershell -NoProfile -c "{ps}; Start-Sleep 3"'
        await rdp_obj._send_keystrokes(full_cmd, delay=0.01)
        await rdp_obj._send_enter()
        await asyncio.sleep(5.0)

        rdp_obj.logger.display("Waiting for file list from server...")
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 15.0
        file_list = None
        while loop.time() < deadline:
            file_list = rdp_obj.conn.get_remote_file_list()
            if file_list:
                break
            await asyncio.sleep(0.3)

        if not file_list:
            rdp_obj.logger.fail("Server did not announce clipboard files — check that remote paths exist and clipboard redirection is enabled")
            return

        rdp_obj.logger.display(f"Received {len(file_list)} file descriptor(s), downloading...")

        for i, descriptor in enumerate(file_list):
            name = descriptor.fileName
            rdp_obj.logger.debug(f"Downloading [{i + 1}/{len(file_list)}] {name}")
            try:
                nbytes = await asyncio.wait_for(rdp_obj.conn.download_file(i), timeout=120.0)
                rdp_obj.logger.success(f"Pulled {name} ({nbytes} bytes) -> {dest / name}")
            except TimeoutError:
                rdp_obj.logger.fail(f"Download timed out for {name}")
            except Exception as e:
                rdp_obj.logger.fail(f"Failed to download {name}: {e}")

    except (ConnectionResetError, ConnectionError, OSError) as e:
        rdp_obj.logger.fail(f"Connection error during pull: {e}")
    except Exception as e:
        rdp_obj.logger.fail(f"Pull failed: {e}")
    finally:
        await rdp_obj.terminate_conn()
