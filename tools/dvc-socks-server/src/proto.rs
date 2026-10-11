pub const MSG_CONNECT: u8 = 0x01;
pub const MSG_CONNECT_OK: u8 = 0x02;
pub const MSG_CONNECT_ERR: u8 = 0x03;
pub const MSG_DATA: u8 = 0x04;
pub const MSG_CLOSE: u8 = 0x05;

pub const ATYP_IPV4: u8 = 0x01;
pub const ATYP_DOMAIN: u8 = 0x03;
pub const ATYP_IPV6: u8 = 0x04;

pub struct Header {
    pub msg_type: u8,
    pub stream_id: u32,
}

pub fn parse_header(data: &[u8]) -> Option<(Header, &[u8])> {
    if data.len() < 5 {
        return None;
    }
    let msg_type = data[0];
    let stream_id = u32::from_be_bytes([data[1], data[2], data[3], data[4]]);
    Some((Header { msg_type, stream_id }, &data[5..]))
}

pub fn pack_connect_ok(stream_id: u32) -> Vec<u8> {
    let mut buf = Vec::with_capacity(5);
    buf.push(MSG_CONNECT_OK);
    buf.extend_from_slice(&stream_id.to_be_bytes());
    buf
}

pub fn pack_connect_err(stream_id: u32, errno: u8) -> Vec<u8> {
    let mut buf = Vec::with_capacity(6);
    buf.push(MSG_CONNECT_ERR);
    buf.extend_from_slice(&stream_id.to_be_bytes());
    buf.push(errno);
    buf
}

pub fn pack_data(stream_id: u32, data: &[u8]) -> Vec<u8> {
    let len = data.len() as u16;
    let mut buf = Vec::with_capacity(7 + data.len());
    buf.push(MSG_DATA);
    buf.extend_from_slice(&stream_id.to_be_bytes());
    buf.extend_from_slice(&len.to_be_bytes());
    buf.extend_from_slice(data);
    buf
}

pub fn pack_close(stream_id: u32) -> Vec<u8> {
    let mut buf = Vec::with_capacity(5);
    buf.push(MSG_CLOSE);
    buf.extend_from_slice(&stream_id.to_be_bytes());
    buf
}

pub struct ConnectRequest {
    pub host: String,
    pub port: u16,
}

pub fn parse_connect_payload(payload: &[u8]) -> Option<ConnectRequest> {
    if payload.is_empty() {
        return None;
    }
    let atyp = payload[0];
    match atyp {
        ATYP_IPV4 => {
            if payload.len() < 7 {
                return None;
            }
            let host = format!("{}.{}.{}.{}", payload[1], payload[2], payload[3], payload[4]);
            let port = u16::from_be_bytes([payload[5], payload[6]]);
            Some(ConnectRequest { host, port })
        }
        ATYP_DOMAIN => {
            if payload.len() < 2 {
                return None;
            }
            let dlen = payload[1] as usize;
            if payload.len() < 2 + dlen + 2 {
                return None;
            }
            let host = String::from_utf8_lossy(&payload[2..2 + dlen]).to_string();
            let port = u16::from_be_bytes([payload[2 + dlen], payload[3 + dlen]]);
            Some(ConnectRequest { host, port })
        }
        ATYP_IPV6 => {
            if payload.len() < 19 {
                return None;
            }
            let mut octets = [0u8; 16];
            octets.copy_from_slice(&payload[1..17]);
            let addr = std::net::Ipv6Addr::from(octets);
            let host = addr.to_string();
            let port = u16::from_be_bytes([payload[17], payload[18]]);
            Some(ConnectRequest { host, port })
        }
        _ => None,
    }
}
