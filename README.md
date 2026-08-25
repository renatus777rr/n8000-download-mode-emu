# GT-N8000 Download Mode & eMMC Emulator
## READ! Tip: You can make this for other devices, but need other PIT file, and much modifications.
A Linux-based software emulator of the Samsung GT-N8000 Download Mode / Odin-Loke flashing interface.

The emulator exposes a virtual Samsung USB device using Linux USB Gadget + FunctionFS. Unmodified Heimdall can detect it, communicate with it, download and upload PIT files, repartition it, and flash real GT-N8000 firmware into a software-emulated eMMC image.

**No QEMU is used.**

## Status

### Working

* ODIN / LOKE USB handshake
* Heimdall device detection
* Download Mode session handling
* PIT download
* `heimdall download-pit`
* PIT upload
* `--repartition`
* Single-partition flashing
* Multi-partition flashing
* Large file transfers
* Multi-sequence transfers
* Per-file-part acknowledgements
* PIT-based partition mapping
* Software eMMC storage
* Firmware readback and byte-for-byte verification

A complete multi-partition GT-N8000 firmware flash has been successfully tested, including the large `system.img`.

### Not implemented

* Booting the flashed firmware
* Exynos 4412 hardware emulation
* Samsung S-Boot execution
* Display/PMIC/SoC emulation
* Real eMMC controller emulation
* Samsung hardware-backed rollback/SW REV checks

The current project emulates the **Download Mode and firmware-storage side** of the GT-N8000.

## Architecture

```
Heimdall
   |
   | USB
   v
Linux dummy_hcd
   |
   v
FunctionFS Gadget
   |
   v
  usb.py
   |
   v
  loke.py
  /     \
 /       \
v         v
```

pit.py    emmc.py
|
v
n8000-emmc.bin

## Requirements

Linux is required.

You need:

* Python 3
* Heimdall 2.x
* `usbutils`
* `file`
* `tar`
* `kmod`
* Linux USB Gadget support
* `dummy_hcd`
* `libcomposite`
* FunctionFS

The kernel should provide:

```
CONFIG_USB_GADGET
CONFIG_USB_LIBCOMPOSITE
CONFIG_USB_FUNCTIONFS
CONFIG_USB_DUMMY_HCD
```

## Ubuntu / Debian

Install:

```
sudo apt update
sudo apt install python3 usbutils file kmod git tar heimdall-flash
```

Load the USB modules:

```
sudo modprobe dummy_hcd
sudo modprobe libcomposite
sudo modprobe usb_f_fs
```

Check:

```
ls /sys/class/udc
```

You should see something similar to:

```
dummy_udc.0
```

## Fedora

Install the basic tools:

```
sudo dnf install python3 usbutils file kmod git tar
```

Install a compatible Heimdall 2.x package for your Fedora system.

Then:

```
sudo modprobe dummy_hcd
sudo modprobe libcomposite
sudo modprobe usb_f_fs
```

Check:

```
ls /sys/class/udc
```

## Arch Linux / Manjaro

Install:

```
sudo pacman -S python usbutils file git tar kmod
```

Install a compatible Heimdall 2.x package.

Then:

```
sudo modprobe dummy_hcd
sudo modprobe libcomposite
sudo modprobe usb_f_fs
```

## openSUSE

Install:

```
sudo zypper install python3 usbutils file git tar kmod
```

Install a compatible Heimdall 2.x package.

Then:

```
sudo modprobe dummy_hcd
sudo modprobe libcomposite
sudo modprobe usb_f_fs
```

## Verify the kernel

Check the running kernel:

```
grep -E 'CONFIG_USB_GADGET|CONFIG_USB_LIBCOMPOSITE|CONFIG_USB_FUNCTIONFS|CONFIG_USB_DUMMY_HCD' /boot/config-$(uname -r)
```

If `CONFIG_USB_DUMMY_HCD=m` is present, load it with:

```
sudo modprobe dummy_hcd
```

## Firmware

Use firmware specifically for:

```
Samsung GT-N8000
```

Typical firmware packages include:

```
BOOTLOADER_*.tar.md5
CODE_*.tar.md5
MODEM_*.tar.md5
CSC_*.tar.md5
```

Do not use firmware intended for another model.

Firmware files are **not included** in this repository.

## Preparing the PIT

Inspect the PIT:

```
python3 pit.py /path/to/note10.pit
```

The N8000 PIT contains partitions such as:

```
BOOTLOADER
TZSW
PIT
MD5HDR
BOTA0
BOTA1
EFS
PARAM
BOOT
RECOVERY
RADIO
CACHE
SYSTEM
HIDDEN
OTA
USERDATA
```

## Creating the virtual eMMC

Create the backing storage image:

```
python3 emmc.py create
```

This creates:

```
n8000-emmc.bin
```

Inspect its PIT-derived layout:

```
python3 emmc.py info
```

The eMMC is a normal file. It is not a physical storage device.

## Starting the emulator

Load the required modules:

```
sudo modprobe dummy_hcd
sudo modprobe libcomposite
sudo modprobe usb_f_fs
```

Start the Download Mode emulator:

```
sudo python3 loke.py
```

You should eventually see:

```
USB gadget ready: 04e8:685d
Loke transport ready
FunctionFS event: BIND
FunctionFS event: ENABLE
```

Check USB detection:

```
lsusb | grep 04e8
```

Then:

```
sudo heimdall detect
```

Expected:

```
Device detected
```

## PIT download

Print the PIT:

```
sudo heimdall print-pit
```

Download it:

```
sudo heimdall download-pit \
    --output downloaded.pit \
    --no-reboot
```

Compare it with the original:

```
cmp downloaded.pit /path/to/note10.pit
```

A silent `cmp` means the files are identical.

## PIT upload / repartition

The emulator supports PIT upload through:

```
sudo heimdall flash \
    --repartition \
    --pit /path/to/note10.pit \
    --BOOT test-boot.bin \
    --no-reboot
```

The PIT is uploaded first, then the requested partition is flashed using the uploaded layout.

## Flashing one partition

Example:

```
sudo heimdall flash \
    --BOOT boot.img \
    --no-reboot
```

Another example:

```
sudo heimdall flash \
    --RECOVERY recovery.img \
    --no-reboot
```

Partition placement is determined by the PIT.

For the tested N8000 PIT:

```
BOOT      -> identifier 5
RECOVERY  -> identifier 6
SYSTEM    -> identifier 9
```

## Flashing multiple partitions

The emulator supports normal multi-partition Heimdall flashing:

```
sudo heimdall flash \
    --BOOT boot.img \
    --RECOVERY recovery.img \
    --PARAM param.bin \
    --RADIO modem.bin \
    --SYSTEM system.img \
    --CACHE cache.img \
    --HIDDEN hidden.img \
    --no-reboot
```

This was tested successfully with real N8000 firmware.

## Large files

Large images such as `system.img` are transferred in multiple sequences.

The implementation handles:

* 128 KiB file parts
* Per-part acknowledgements
* Multiple transfer sequences
* Final EOF handling
* Streaming rather than loading the complete image into RAM

A real N8000 `system.img` was successfully transferred and flashed.

## Readback verification

Read a partition:

```
python3 emmc.py read BOOT boot-readback.bin
```

Compare it with the original:

```
python3 - <<'PY'
from pathlib import Path

original = Path("boot.img").read_bytes()
readback = Path("boot-readback.bin").read_bytes()

print("original:", len(original))
print("readback:", len(readback))
print("MATCH:", original == readback[:len(original)])
PY
```

Expected:

```
MATCH: True
```

The real N8000 `boot.img` has been verified this way.

## Project files

### `usb.py`

Creates the Linux USB Gadget / FunctionFS device.

Responsible for:

* USB descriptors
* Samsung VID/PID
* configfs gadget setup
* FunctionFS endpoints

### `loke.py`

Main Download Mode implementation.

Handles:

* ODIN / LOKE handshake
* session protocol
* PIT protocol
* PIT upload/download
* repartition
* firmware file transfer
* large-file sequences
* file-part acknowledgements
* partition selection
* eMMC writes

### `pit.py`

Parses Samsung PIT files and provides partition metadata.

### `emmc.py`

Provides the software eMMC backend.

Handles:

* eMMC image creation
* partition mapping
* sector reads
* sector writes
* partition flashing
* partition readback

## Protocol coverage

The emulator currently implements the major Heimdall flashing protocol operations:

```
ODIN / LOKE handshake
Session control
PIT download
PIT upload
File transfer
Large-file sequences
Per-part acknowledgements
End-of-file handling
Session end
```

Main control types:

```
0x64  Session
0x65  PIT
0x66  File transfer
0x67  End session
```

## Why flashing is relatively slow

This project deliberately performs the actual transfer through:

```
Heimdall
  -> USB Gadget
  -> FunctionFS
  -> Python
  -> temporary file
  -> eMMC image
```

Large firmware files therefore take noticeable time to transfer.

This is expected and is useful for testing because the emulator is actually receiving and writing the firmware data rather than simply reporting success.

## Safety

The default backing storage is:

```
n8000-emmc.bin
```

Do not point the storage backend at a real block device unless you fully understand the consequences.

Do not commit proprietary Samsung firmware to the repository.

A suggested `.gitignore` is:

```
*.img
*.bin
*.tar
*.tar.md5
*.pit
n8000-emmc.bin
__pycache__/
*.pyc
```

## Limitations

The emulator does not currently implement the GT-N8000 hardware itself.

A correctly flashed `n8000-emmc.bin` therefore does **not** currently boot Android or Linux.

The missing layer includes the Exynos 4412 platform and the hardware expected by the Samsung boot chain.

The current project should be considered a:

**GT-N8000 Download Mode + firmware/eMMC emulator**

rather than a complete virtual GT-N8000.

## Current development status

| Feature                            | Status          |
| ---------------------------------- | --------------- |
| USB Download Mode                  | Working         |
| Heimdall detection                 | Working         |
| ODIN / LOKE handshake              | Working         |
| Sessions                           | Working         |
| PIT download                       | Working         |
| `download-pit`                     | Working         |
| PIT upload                         | Working         |
| `--repartition`                    | Working         |
| Single-partition flashing          | Working         |
| Multi-partition flashing           | Working         |
| Large file transfers               | Working         |
| Multi-sequence transfers           | Working         |
| Software eMMC                      | Working         |
| Readback verification              | Working         |
| Samsung bootloader rollback checks | Not implemented |
| Exynos 4412 emulation              | Not implemented |
| Android/Linux boot                 | Not implemented |

## Disclaimer

Samsung, Galaxy Note, GT-N8000 and Odin are trademarks of their respective owners.

This is an independent reverse-engineering and emulation project.

Samsung firmware is not included in this repository.
