#![windows_subsystem = "windows"]

mod dvc;
mod proto;
mod proxy;

fn main() {
    let channel_name = std::env::args().nth(1).unwrap_or_else(|| "dvc_socks".to_string());

    let log_path = std::env::temp_dir().join("dvc_debug.log");
    let mut log = std::fs::File::create(&log_path).ok();

    if let Some(ref mut f) = log {
        use std::io::Write;
        let _ = writeln!(f, "Starting DVC server, channel={}", channel_name);
    }

    let channel = match dvc::DvcChannel::open(&channel_name) {
        Some(c) => {
            if let Some(ref mut f) = log {
                use std::io::Write;
                let _ = writeln!(f, "Channel opened successfully");
            }
            c
        }
        None => {
            if let Some(ref mut f) = log {
                use std::io::Write;
                let err = unsafe { GetLastError() };
                let _ = writeln!(f, "Failed to open channel, GetLastError={}", err);
            }
            std::process::exit(1);
        }
    };

    if let Some(ref mut f) = log {
        use std::io::Write;
        let _ = writeln!(f, "Starting relay loop");
    }

    proxy::run_relay_debug(channel, log);
}

#[link(name = "kernel32")]
extern "system" {
    fn GetLastError() -> u32;
}
