import os
import struct
import subprocess
import time
from pathlib import Path

CONFIGFS = Path("/sys/kernel/config/usb_gadget")
GADGET = CONFIGFS / "n8000"
FUNCTION = GADGET / "functions" / "ffs.loke"
CONFIG = GADGET / "configs" / "c.1"
FFS_ROOT = Path("/dev/ffs")
FFS = FFS_ROOT / "loke"
UDC = "dummy_udc.0"
VID = 0x04E8
PID = 0x685D


def write_text(path, value):
    Path(path).write_text(str(value))


def descriptor_block():
    fs = (
        struct.pack("<BBBBBBBBB", 9, 4, 0, 0, 2, 0x0A, 0, 0, 0)
        + struct.pack("<BBBBHB", 7, 5, 0x01, 2, 64, 0)
        + struct.pack("<BBBBHB", 7, 5, 0x81, 2, 64, 0)
    )

    hs = (
        struct.pack("<BBBBBBBBB", 9, 4, 0, 0, 2, 0x0A, 0, 0, 0)
        + struct.pack("<BBBBHB", 7, 5, 0x01, 2, 512, 0)
        + struct.pack("<BBBBHB", 7, 5, 0x81, 2, 512, 0)
    )

    return struct.pack(
        "<5I",
        3,
        20 + len(fs) + len(hs),
        1 | 2,
        3,
        3,
    ) + fs + hs


def strings_block():
    string = b"GT-N8000\0"
    length = 16 + 2 + len(string)

    return (
        struct.pack(
            "<4I",
            2,
            length,
            1,
            1,
        )
        + struct.pack("<H", 0x0409)
        + string
    )


def setup_configfs():
    subprocess.run(["modprobe", "libcomposite"], check=True)
    subprocess.run(["modprobe", "usb_f_fs"], check=True)

    GADGET.mkdir(parents=True, exist_ok=True)

    write_text(GADGET / "idVendor", f"0x{VID:04x}")
    write_text(GADGET / "idProduct", f"0x{PID:04x}")
    write_text(GADGET / "bcdUSB", "0x0200")
    write_text(GADGET / "bcdDevice", "0x0001")

    strings = GADGET / "strings" / "0x409"
    strings.mkdir(parents=True, exist_ok=True)
    write_text(strings / "serialnumber", "GT-N8000")
    write_text(strings / "manufacturer", "SAMSUNG")
    write_text(strings / "product", "GT-N8000")

    config_strings = CONFIG / "strings" / "0x409"
    config_strings.mkdir(parents=True, exist_ok=True)
    write_text(config_strings / "configuration", "Loke")
    write_text(CONFIG / "MaxPower", "500")
    write_text(CONFIG / "bmAttributes", "0x80")

    FUNCTION.mkdir(parents=True, exist_ok=True)

    link = CONFIG / "ffs.loke"

    if link.exists() or link.is_symlink():
        link.unlink()

    link.symlink_to(FUNCTION)


def setup_functionfs():
    FFS_ROOT.mkdir(parents=True, exist_ok=True)

    if not FFS.exists():
        FFS.mkdir(parents=True)

    mounted = subprocess.run(
        ["mountpoint", "-q", str(FFS)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0

    if not mounted:
        subprocess.run(
            ["mount", "-t", "functionfs", "loke", str(FFS)],
            check=True,
        )

    ep0 = os.open(FFS / "ep0", os.O_RDWR)

    os.write(ep0, descriptor_block())
    os.write(ep0, strings_block())

    return ep0


def wait_ready():
    ready = FUNCTION / "ready"

    for _ in range(200):
        try:
            if ready.read_text().strip() == "1":
                return
        except FileNotFoundError:
            pass

        time.sleep(0.01)

    raise RuntimeError("FunctionFS function did not become ready")


def bind():
    write_text(GADGET / "UDC", UDC)


class USBDevice:
    def __init__(self):
        self.ep0 = None
        self.out = None
        self.in_ep = None
        self.started = False

    def start(self):
        setup_configfs()
        self.ep0 = setup_functionfs()
        wait_ready()
        bind()

        self.out = os.open(FFS / "ep1", os.O_RDWR)
        self.in_ep = os.open(FFS / "ep2", os.O_RDWR)
        self.started = True

        print(f"USB gadget ready: {VID:04x}:{PID:04x}")

        return self

    def close(self):
        for fd_name in ("out", "in_ep", "ep0"):
            fd = getattr(self, fd_name, None)

            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

                setattr(self, fd_name, None)

        try:
            write_text(GADGET / "UDC", "")
        except Exception:
            pass

        try:
            subprocess.run(
                ["umount", str(FFS)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

        self.started = False


def main():
    device = USBDevice()

    try:
        device.start()

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        pass

    finally:
        device.close()


if __name__ == "__main__":
    main()