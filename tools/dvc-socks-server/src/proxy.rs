use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::sync::mpsc::{self, Receiver, Sender};
use std::thread;
use std::time::Duration;

use crate::dvc::DvcChannel;
use crate::proto;

enum StreamEvent {
    Data(u32, Vec<u8>),
    Closed(u32),
}

struct StreamState {
    tcp: TcpStream,
}

pub fn run_relay(channel: DvcChannel) {
    run_relay_debug(channel, None);
}

pub fn run_relay_debug(channel: DvcChannel, mut log: Option<std::fs::File>) {
    let mut streams: HashMap<u32, StreamState> = HashMap::new();
    let (event_tx, event_rx): (Sender<StreamEvent>, Receiver<StreamEvent>) = mpsc::channel();
    let mut read_buf = vec![0u8; 65536];
    let mut loop_count: u64 = 0;

    loop {
        loop_count += 1;
        let read_result = channel.read(&mut read_buf);
        if let Some(n) = read_result {
            if let Some(ref mut f) = log {
                use std::io::Write;
                let _ = writeln!(f, "loop={} read {} bytes: {:02x?}", loop_count, n, &read_buf[..n.min(32)]);
                let _ = f.flush();
            }
            if n >= 13 {
                // WTSVirtualChannelRead prepends 8-byte CHANNEL_PDU_HEADER on DVC
                let data = &read_buf[8..n];
                if let Some((hdr, payload)) = proto::parse_header(data) {
                    if let Some(ref mut f) = log {
                        use std::io::Write;
                        let _ = writeln!(f, "  msg_type={} stream_id={} payload_len={}", hdr.msg_type, hdr.stream_id, payload.len());
                        let _ = f.flush();
                    }
                    match hdr.msg_type {
                        proto::MSG_CONNECT => {
                            handle_connect(&channel, &mut streams, &event_tx, hdr.stream_id, payload);
                            if let Some(ref mut f) = log {
                                use std::io::Write;
                                let _ = writeln!(f, "  CONNECT handled, streams={}", streams.len());
                                let _ = f.flush();
                            }
                        }
                        proto::MSG_DATA => {
                            handle_data(&mut streams, &channel, hdr.stream_id, payload);
                        }
                        proto::MSG_CLOSE => {
                            streams.remove(&hdr.stream_id);
                        }
                        _ => {}
                    }
                }
            }
        } else if loop_count <= 10 || loop_count % 5000 == 0 {
            let err = DvcChannel::last_error();
            if let Some(ref mut f) = log {
                use std::io::Write;
                let _ = writeln!(f, "loop={} read=None GetLastError={}", loop_count, err);
                let _ = f.flush();
            }
        }

        while let Ok(event) = event_rx.try_recv() {
            match event {
                StreamEvent::Data(sid, data) => {
                    let msg = proto::pack_data(sid, &data);
                    channel.write(&msg);
                }
                StreamEvent::Closed(sid) => {
                    streams.remove(&sid);
                    let msg = proto::pack_close(sid);
                    channel.write(&msg);
                }
            }
        }

        if streams.is_empty() {
            thread::sleep(Duration::from_millis(1));
        }
    }
}

fn handle_connect(
    channel: &DvcChannel,
    streams: &mut HashMap<u32, StreamState>,
    event_tx: &Sender<StreamEvent>,
    stream_id: u32,
    payload: &[u8],
) {
    let req = match proto::parse_connect_payload(payload) {
        Some(r) => r,
        None => {
            channel.write(&proto::pack_connect_err(stream_id, 1));
            return;
        }
    };

    let addr_str = format!("{}:{}", req.host, req.port);
    let sock_addr = match addr_str.to_socket_addrs() {
        Ok(mut addrs) => match addrs.next() {
            Some(a) => a,
            None => {
                channel.write(&proto::pack_connect_err(stream_id, 4));
                return;
            }
        },
        Err(_) => {
            channel.write(&proto::pack_connect_err(stream_id, 4));
            return;
        }
    };

    match TcpStream::connect_timeout(&sock_addr, Duration::from_secs(10)) {
        Ok(tcp) => {
            let _ = tcp.set_nonblocking(false);
            let read_tcp = match tcp.try_clone() {
                Ok(t) => t,
                Err(_) => {
                    channel.write(&proto::pack_connect_err(stream_id, 1));
                    return;
                }
            };
            let tx = event_tx.clone();
            let sid = stream_id;
            thread::spawn(move || {
                tcp_reader(sid, read_tcp, tx);
            });
            streams.insert(stream_id, StreamState { tcp });
            channel.write(&proto::pack_connect_ok(stream_id));
        }
        Err(_) => {
            channel.write(&proto::pack_connect_err(stream_id, 5));
        }
    }
}

fn handle_data(
    streams: &mut HashMap<u32, StreamState>,
    channel: &DvcChannel,
    stream_id: u32,
    payload: &[u8],
) {
    if payload.len() < 2 {
        return;
    }
    let len = u16::from_be_bytes([payload[0], payload[1]]) as usize;
    if payload.len() < 2 + len {
        return;
    }
    let data = &payload[2..2 + len];

    if let Some(state) = streams.get_mut(&stream_id) {
        if state.tcp.write_all(data).is_err() {
            streams.remove(&stream_id);
            channel.write(&proto::pack_close(stream_id));
        }
    }
}

fn tcp_reader(stream_id: u32, mut tcp: TcpStream, tx: Sender<StreamEvent>) {
    let mut buf = [0u8; 4096];
    loop {
        match tcp.read(&mut buf) {
            Ok(0) | Err(_) => {
                let _ = tx.send(StreamEvent::Closed(stream_id));
                return;
            }
            Ok(n) => {
                let _ = tx.send(StreamEvent::Data(stream_id, buf[..n].to_vec()));
            }
        }
    }
}
