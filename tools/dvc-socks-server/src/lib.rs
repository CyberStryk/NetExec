mod dvc;
mod proto;
mod proxy;

use std::ffi::c_void;

/// rundll32-compatible entry point:
///   rundll32 dvc_server.dll,DvcSocksMain [channel_name]
#[no_mangle]
pub extern "system" fn DvcSocksMain(
    _hwnd: *mut c_void,
    _hinst: *mut c_void,
    cmd_line: *const u8,
    _cmd_show: i32,
) {
    let channel_name = if cmd_line.is_null() {
        "dvc_socks".to_string()
    } else {
        let cstr = unsafe {
            let mut len = 0usize;
            while *cmd_line.add(len) != 0 {
                len += 1;
            }
            std::slice::from_raw_parts(cmd_line, len)
        };
        let s = String::from_utf8_lossy(cstr).trim().to_string();
        if s.is_empty() {
            "dvc_socks".to_string()
        } else {
            s
        }
    };

    let channel = match dvc::DvcChannel::open(&channel_name) {
        Some(c) => c,
        None => return,
    };

    proxy::run_relay(channel);
}
