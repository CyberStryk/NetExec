const WTS_CURRENT_SESSION: u32 = 0xFFFFFFFF;
const WTS_CHANNEL_OPTION_DYNAMIC: u32 = 0x00000001;

type HANDLE = *mut std::ffi::c_void;
type BOOL = i32;

#[link(name = "kernel32")]
extern "system" {
    fn GetLastError() -> u32;
}

#[link(name = "wtsapi32")]
extern "system" {
    fn WTSVirtualChannelOpenEx(
        session_id: u32,
        virtual_name: *const i8,
        flags: u32,
    ) -> HANDLE;

    fn WTSVirtualChannelRead(
        handle: HANDLE,
        timeout: u32,
        buffer: *mut u8,
        buffer_size: u32,
        bytes_read: *mut u32,
    ) -> BOOL;

    fn WTSVirtualChannelWrite(
        handle: HANDLE,
        buffer: *const u8,
        length: u32,
        bytes_written: *mut u32,
    ) -> BOOL;

    fn WTSVirtualChannelClose(handle: HANDLE) -> BOOL;
}

pub struct DvcChannel {
    handle: HANDLE,
}

unsafe impl Send for DvcChannel {}

impl DvcChannel {
    pub fn open(channel_name: &str) -> Option<Self> {
        let cstr = std::ffi::CString::new(channel_name).ok()?;
        let handle = unsafe {
            WTSVirtualChannelOpenEx(
                WTS_CURRENT_SESSION,
                cstr.as_ptr(),
                WTS_CHANNEL_OPTION_DYNAMIC,
            )
        };
        if handle.is_null() {
            return None;
        }
        Some(DvcChannel { handle })
    }

    pub fn read(&self, buf: &mut [u8]) -> Option<usize> {
        let mut bytes_read: u32 = 0;
        let ok = unsafe {
            WTSVirtualChannelRead(
                self.handle,
                200,
                buf.as_mut_ptr(),
                buf.len() as u32,
                &mut bytes_read,
            )
        };
        if ok != 0 {
            Some(bytes_read as usize)
        } else {
            None
        }
    }

    pub fn last_error() -> u32 {
        unsafe { GetLastError() }
    }

    pub fn write(&self, data: &[u8]) -> bool {
        let mut bytes_written: u32 = 0;
        let ok = unsafe {
            WTSVirtualChannelWrite(
                self.handle,
                data.as_ptr(),
                data.len() as u32,
                &mut bytes_written,
            )
        };
        ok != 0
    }
}

impl Drop for DvcChannel {
    fn drop(&mut self) {
        if !self.handle.is_null() {
            unsafe {
                WTSVirtualChannelClose(self.handle);
            }
        }
    }
}
