import os
import struct
import sys
import tempfile
import threading
from pathlib import Path

from emmc import EMMC
from usb import USBDevice

EVENT_SIZE = 12

FUNCTIONFS_BIND = 0
FUNCTIONFS_UNBIND = 1
FUNCTIONFS_ENABLE = 2
FUNCTIONFS_DISABLE = 3
FUNCTIONFS_SETUP = 4
FUNCTIONFS_SUSPEND = 5
FUNCTIONFS_RESUME = 6

PIT_PATH = Path("note10.pit")
EMMC_IMAGE = Path("n8000-emmc.bin")

FILE_PACKET_SIZE = 131072


class Loke:
    def __init__(self):
        self.usb = None
        self.running = True
        self.enabled = False
        self.lock = threading.Lock()

        self.control_thread = None
        self.data_thread = None

        self.pit_data = PIT_PATH.read_bytes()
        self.pit_size = len(self.pit_data)

        self.emmc = EMMC(
            EMMC_IMAGE,
            PIT_PATH,
        )

        self.total_transfer_bytes = 0

        self.pit_upload_active = False
        self.pit_upload_phase = "idle"
        self.pit_upload_expected = 0
        self.pit_upload_received = 0
        self.pit_upload_file = None
        self.pit_upload_path = None

        self.transfer_active = False
        self.sequence_active = False

        self.sequence_total = 0
        self.sequence_received = 0
        self.sequence_file_offset = 0
        self.file_part_index = 0

        self.file_identifier = None
        self.file_device_type = None
        self.file_destination = None

        self.file_bytes_written = 0

        self.temp_file = None
        self.temp_path = None

    def handle_event(self, event):
        event_type = event[8]

        names = {
            FUNCTIONFS_BIND: "BIND",
            FUNCTIONFS_UNBIND: "UNBIND",
            FUNCTIONFS_ENABLE: "ENABLE",
            FUNCTIONFS_DISABLE: "DISABLE",
            FUNCTIONFS_SETUP: "SETUP",
            FUNCTIONFS_SUSPEND: "SUSPEND",
            FUNCTIONFS_RESUME: "RESUME",
        }

        print(
            f"FunctionFS event: "
            f"{names.get(event_type, str(event_type))}",
            flush=True,
        )

        with self.lock:
            if event_type == FUNCTIONFS_ENABLE:
                self.enabled = True

            elif event_type in (
                FUNCTIONFS_DISABLE,
                FUNCTIONFS_UNBIND,
                FUNCTIONFS_SUSPEND,
            ):
                self.enabled = False

            elif event_type == FUNCTIONFS_RESUME:
                self.enabled = True

    def control_loop(self):
        while self.running:
            try:
                data = os.read(
                    self.usb.ep0,
                    EVENT_SIZE * 8,
                )
            except OSError as e:
                if not self.running:
                    return

                print(
                    f"EP0 error: {e}",
                    file=sys.stderr,
                    flush=True,
                )

                with self.lock:
                    self.enabled = False

                return

            if not data:
                continue

            for offset in range(
                0,
                len(data),
                EVENT_SIZE,
            ):
                event = data[
                    offset:
                    offset + EVENT_SIZE
                ]

                if len(event) == EVENT_SIZE:
                    self.handle_event(event)

    def send(self, data):
        with self.lock:
            if not self.enabled:
                return False

        try:
            written = os.write(
                self.usb.in_ep,
                data,
            )

            if written != len(data):
                print(
                    f"USB IN short write: "
                    f"{written}/{len(data)}",
                    file=sys.stderr,
                    flush=True,
                )
                return False

            return True

        except OSError as e:
            print(
                f"EP2 error: {e}",
                file=sys.stderr,
                flush=True,
            )

            with self.lock:
                self.enabled = False

            return False

    def send_response(
        self,
        response_type,
        value=0,
    ):
        packet = struct.pack(
            "<II",
            response_type,
            value,
        )

        print(
            f"TX response: "
            f"type=0x{response_type:02x} "
            f"value={value}",
            flush=True,
        )

        return self.send(packet)

    def reset_pit_upload(self):
        self.pit_upload_active = False
        self.pit_upload_phase = "idle"
        self.pit_upload_expected = 0
        self.pit_upload_received = 0

        if self.pit_upload_file is not None:
            try:
                self.pit_upload_file.close()
            except Exception:
                pass

        self.pit_upload_file = None

        if self.pit_upload_path is not None:
            try:
                os.unlink(
                    self.pit_upload_path
                )
            except FileNotFoundError:
                pass

        self.pit_upload_path = None

    def start_pit_upload(self):
        self.reset_pit_upload()

        temp = tempfile.NamedTemporaryFile(
            prefix="n8000-pit-",
            suffix=".pit",
            dir="/tmp",
            delete=False,
        )

        self.pit_upload_file = temp
        self.pit_upload_path = temp.name
        self.pit_upload_active = True
        self.pit_upload_phase = "await_size"
        self.pit_upload_expected = 0
        self.pit_upload_received = 0

        print(
            f"PIT upload started: "
            f"{self.pit_upload_path}",
            flush=True,
        )

        self.send_response(
            0x65,
            0,
        )

    def begin_pit_upload(self, part_size):
        if not self.pit_upload_active:
            print(
                "PIT part request without active upload",
                flush=True,
            )

            self.send_response(
                0x65,
                1,
            )
            return

        if part_size <= 0:
            print(
                f"Invalid PIT part size: {part_size}",
                flush=True,
            )

            self.reset_pit_upload()

            self.send_response(
                0x65,
                1,
            )
            return

        self.pit_upload_expected = part_size
        self.pit_upload_received = 0
        self.pit_upload_phase = "raw"

        self.send_response(
            0x65,
            0,
        )

        print(
            f"Waiting for {part_size} raw PIT bytes",
            flush=True,
        )

    def receive_pit_data(self, data):
        if (
            not self.pit_upload_active
            or self.pit_upload_phase != "raw"
        ):
            print(
                "Unexpected raw PIT data",
                flush=True,
            )
            return

        remaining = (
            self.pit_upload_expected
            - self.pit_upload_received
        )

        if len(data) > remaining:
            data = data[:remaining]

        if not data:
            return

        self.pit_upload_file.write(data)
        self.pit_upload_file.flush()

        self.pit_upload_received += len(data)

        print(
            f"PIT raw chunk: "
            f"{len(data)} bytes "
            f"({self.pit_upload_received}/"
            f"{self.pit_upload_expected})",
            flush=True,
        )

        if (
            self.pit_upload_received
            == self.pit_upload_expected
        ):
            self.pit_upload_phase = "await_end"

            print(
                "PIT raw data complete",
                flush=True,
            )

            self.send_response(
                0x65,
                0,
            )

    def finish_pit_upload(self, file_size):
        if (
            not self.pit_upload_active
            or self.pit_upload_phase != "await_end"
        ):
            print(
                "Unexpected PIT end request",
                flush=True,
            )

            self.send_response(
                0x65,
                1,
            )
            return

        if file_size != self.pit_upload_received:
            print(
                f"PIT size mismatch: "
                f"received={self.pit_upload_received} "
                f"reported={file_size}",
                flush=True,
            )

            self.reset_pit_upload()

            self.send_response(
                0x65,
                1,
            )
            return

        self.pit_upload_file.flush()
        self.pit_upload_file.close()
        self.pit_upload_file = None

        try:
            uploaded = Path(
                self.pit_upload_path
            ).read_bytes()

            self.pit_data = uploaded
            self.pit_size = len(uploaded)

            Path(PIT_PATH).write_bytes(
                uploaded
            )

            self.emmc = EMMC(
                EMMC_IMAGE,
                PIT_PATH,
            )

            print(
                f"PIT loaded successfully: "
                f"{self.pit_size} bytes",
                flush=True,
            )

            self.send_response(
                0x65,
                0,
            )

        except Exception as e:
            print(
                f"PIT load failed: {e}",
                file=sys.stderr,
                flush=True,
            )

            self.send_response(
                0x65,
                1,
            )

        finally:
            self.reset_pit_upload()

    def reset_file_transfer(self):
        self.transfer_active = False
        self.sequence_active = False

        self.sequence_total = 0
        self.sequence_received = 0
        self.sequence_file_offset = 0
        self.file_part_index = 0

        self.file_identifier = None
        self.file_device_type = None
        self.file_destination = None

        self.file_bytes_written = 0

        if self.temp_file is not None:
            try:
                self.temp_file.close()
            except Exception:
                pass

        self.temp_file = None

        if self.temp_path is not None:
            try:
                os.unlink(
                    self.temp_path
                )
            except FileNotFoundError:
                pass

        self.temp_path = None

    def start_file_transfer(self):
        self.reset_file_transfer()

        temp = tempfile.NamedTemporaryFile(
            prefix="n8000-flash-",
            suffix=".bin",
            dir="/tmp",
            delete=False,
        )

        self.temp_file = temp
        self.temp_path = temp.name
        self.transfer_active = True

        print(
            f"File transfer started: "
            f"{self.temp_path}",
            flush=True,
        )

        self.send_response(
            0x66,
            0,
        )

    def begin_sequence(self, byte_count):
        if not self.transfer_active:
            print(
                "Sequence received without "
                "active file transfer",
                flush=True,
            )
            return

        self.sequence_active = True
        self.sequence_total = byte_count
        self.sequence_received = 0

        self.sequence_file_offset = (
            self.file_bytes_written
        )

        self.file_part_index = 0

        print(
            f"Sequence started: "
            f"{byte_count} bytes "
            f"offset={self.sequence_file_offset}",
            flush=True,
        )

        self.send_response(
            0x66,
            0,
        )

    def receive_file_data(self, data):
        if not self.sequence_active:
            print(
                "RAW data received without active sequence",
                flush=True,
            )
            return

        remaining = (
            self.sequence_total
            - self.sequence_received
        )

        if len(data) > remaining:
            data = data[:remaining]

        if not data:
            return

        self.temp_file.write(data)
        self.temp_file.flush()

        self.sequence_received += len(data)
        self.file_bytes_written += len(data)

        part_index = self.file_part_index
        self.file_part_index += 1

        print(
            f"RAW chunk: "
            f"{len(data)} bytes "
            f"({self.sequence_received}/"
            f"{self.sequence_total}) "
            f"part={part_index}",
            flush=True,
        )

        self.send(
            struct.pack(
                "<II",
                0,
                part_index,
            )
        )

        if (
            self.sequence_received
            == self.sequence_total
        ):
            self.sequence_active = False

            print(
                f"Sequence complete: "
                f"{self.sequence_total} bytes",
                flush=True,
            )

    def finish_sequence(
        self,
        sequence_byte_count,
        destination,
        unknown1,
        device_type,
        file_identifier,
        end_of_file,
    ):
        print(
            f"EndPhone: "
            f"destination={destination} "
            f"sequence={sequence_byte_count} "
            f"unknown1={unknown1} "
            f"device={device_type} "
            f"identifier={file_identifier} "
            f"eof={end_of_file}",
            flush=True,
        )

        expected_sequence_bytes = (
            self.sequence_received
        )

        if (
            expected_sequence_bytes
            != sequence_byte_count
        ):
            print(
                f"Sequence byte mismatch: "
                f"received={expected_sequence_bytes} "
                f"reported={sequence_byte_count}",
                flush=True,
            )

        self.file_identifier = file_identifier
        self.file_device_type = device_type
        self.file_destination = destination

        if self.temp_file is not None:
            self.temp_file.flush()

        if end_of_file:
            try:
                actual_file_size = (
                    self.sequence_file_offset
                    + sequence_byte_count
                )

                if self.temp_file is not None:
                    self.temp_file.flush()
                    self.temp_file.close()
                    self.temp_file = None

                with open(
                    self.temp_path,
                    "r+b",
                ) as f:
                    f.truncate(
                        actual_file_size
                    )

                partition_name, _ = (
                    self.emmc.get_partition_by_identifier(
                        file_identifier
                    )
                )

                print(
                    f"Committing "
                    f"{actual_file_size} bytes "
                    f"to {partition_name}",
                    flush=True,
                )

                self.emmc.flash_file(
                    partition_name,
                    self.temp_path,
                )

                print(
                    f"Flash successful: "
                    f"{partition_name}",
                    flush=True,
                )

                try:
                    os.unlink(
                        self.temp_path
                    )
                except FileNotFoundError:
                    pass

                self.reset_file_transfer()

            except Exception as e:
                print(
                    f"Flash failed: {e}",
                    file=sys.stderr,
                    flush=True,
                )

            self.send_response(
                0x66,
                0,
            )

            return

        self.sequence_active = False
        self.sequence_total = 0
        self.sequence_received = 0
        self.sequence_file_offset = (
            self.file_bytes_written
        )
        self.file_part_index = 0

        print(
            f"More sequences expected; "
            f"file bytes accumulated="
            f"{self.file_bytes_written}",
            flush=True,
        )

        self.send_response(
            0x66,
            0,
        )

    def handle_session_packet(
        self,
        request,
        data,
    ):
        if request == 0:
            self.send_response(
                0x64,
                0,
            )

            return

        if request == 2:
            low = struct.unpack_from(
                "<I",
                data,
                8,
            )[0]

            high = struct.unpack_from(
                "<I",
                data,
                12,
            )[0]

            self.total_transfer_bytes = (
                low |
                (high << 32)
            )

            print(
                f"Total transfer bytes: "
                f"{self.total_transfer_bytes}",
                flush=True,
            )

            self.send_response(
                0x64,
                0,
            )

            return

        print(
            f"Unhandled session request "
            f"{request}",
            flush=True,
        )

        self.send_response(
            0x64,
            0,
        )

    def handle_pit_packet(
        self,
        request,
        data,
    ):
        if request == 0:
            self.start_pit_upload()
            return

        if request == 1:
            self.send_response(
                0x65,
                self.pit_size,
            )

            return

        if request == 2:
            value = struct.unpack_from(
                "<I",
                data,
                8,
            )[0]

            if self.pit_upload_active:
                self.begin_pit_upload(
                    value
                )
            else:
                part_index = value
                offset = part_index * 500

                chunk = self.pit_data[
                    offset:
                    offset + 500
                ]

                print(
                    f"PIT part {part_index}: "
                    f"offset={offset} "
                    f"size={len(chunk)}",
                    flush=True,
                )

                self.send(chunk)

            return

        if request == 3:
            file_size = struct.unpack_from(
                "<I",
                data,
                8,
            )[0]

            if self.pit_upload_active:
                self.finish_pit_upload(
                    file_size
                )
            else:
                self.send_response(
                    0x65,
                    0,
                )

            return

        print(
            f"Unhandled PIT request "
            f"{request}",
            flush=True,
        )

        self.send_response(
            0x65,
            1,
        )

    def handle_file_control(
        self,
        request,
        data,
    ):
        if request == 0:
            self.start_file_transfer()
            return

        if request == 2:
            if len(data) < 12:
                return

            byte_count = struct.unpack_from(
                "<I",
                data,
                8,
            )[0]

            self.begin_sequence(
                byte_count
            )

            return

        if request == 3:
            if len(data) < 32:
                print(
                    f"Invalid EndPhone packet: "
                    f"{len(data)}",
                    flush=True,
                )
                return

            destination = struct.unpack_from(
                "<I",
                data,
                8,
            )[0]

            sequence_byte_count = struct.unpack_from(
                "<I",
                data,
                12,
            )[0]

            unknown1 = struct.unpack_from(
                "<I",
                data,
                16,
            )[0]

            device_type = struct.unpack_from(
                "<I",
                data,
                20,
            )[0]

            file_identifier = struct.unpack_from(
                "<I",
                data,
                24,
            )[0]

            end_of_file = struct.unpack_from(
                "<I",
                data,
                28,
            )[0]

            self.finish_sequence(
                sequence_byte_count,
                destination,
                unknown1,
                device_type,
                file_identifier,
                end_of_file == 1,
            )

            return

        print(
            f"Unhandled file request "
            f"{request}",
            flush=True,
        )

        self.send_response(
            0x66,
            1,
        )

    def handle_end_session(
        self,
        request,
    ):
        print(
            f"EndSession request "
            f"{request}",
            flush=True,
        )

        self.send_response(
            0x67,
            0,
        )

    def handle_control_packet(
        self,
        data,
    ):
        if len(data) < 8:
            print(
                f"Short control packet: "
                f"{len(data)}",
                flush=True,
            )
            return

        control_type, request = struct.unpack_from(
            "<II",
            data,
            0,
        )

        print(
            f"Control type=0x{control_type:02x} "
            f"request=0x{request:02x}",
            flush=True,
        )

        if control_type == 0x64:
            self.handle_session_packet(
                request,
                data,
            )
            return

        if control_type == 0x65:
            self.handle_pit_packet(
                request,
                data,
            )
            return

        if control_type == 0x66:
            self.handle_file_control(
                request,
                data,
            )
            return

        if control_type == 0x67:
            self.handle_end_session(
                request,
            )
            return

        print(
            f"Unknown control type "
            f"0x{control_type:02x}",
            flush=True,
        )

    def data_loop(self):
        while self.running:
            if (
                self.pit_upload_active
                and self.pit_upload_phase == "raw"
            ):
                read_size = (
                    self.pit_upload_expected
                    - self.pit_upload_received
                )

                if read_size <= 0:
                    read_size = 1

            elif self.sequence_active:
                read_size = min(
                    FILE_PACKET_SIZE,
                    self.sequence_total
                    - self.sequence_received,
                )

                if read_size <= 0:
                    continue

            else:
                read_size = 1024 * 1024

            try:
                data = os.read(
                    self.usb.out,
                    read_size,
                )

            except OSError as e:
                if not self.running:
                    return

                print(
                    f"EP1 error: {e}",
                    file=sys.stderr,
                    flush=True,
                )

                with self.lock:
                    self.enabled = False

                continue

            if not data:
                continue

            if (
                self.pit_upload_active
                and self.pit_upload_phase == "raw"
            ):
                self.receive_pit_data(
                    data
                )
                continue

            if self.sequence_active:
                self.receive_file_data(
                    data
                )
                continue

            if data == b"ODIN":
                if self.send(b"LOKE"):
                    print(
                        "TX: LOKE",
                        flush=True,
                    )
                continue

            self.handle_control_packet(
                data
            )

    def start(self):
        if not PIT_PATH.exists():
            raise FileNotFoundError(
                f"PIT not found: {PIT_PATH}"
            )

        if not EMMC_IMAGE.exists():
            raise FileNotFoundError(
                f"eMMC image not found: {EMMC_IMAGE}"
            )

        print(
            f"Loaded PIT: {PIT_PATH}",
            flush=True,
        )

        print(
            f"PIT size: {self.pit_size} bytes",
            flush=True,
        )

        print(
            f"eMMC image: {EMMC_IMAGE}",
            flush=True,
        )

        self.usb = USBDevice().start()

        print(
            "Loke transport ready",
            flush=True,
        )

        self.control_thread = threading.Thread(
            target=self.control_loop,
            daemon=True,
        )

        self.data_thread = threading.Thread(
            target=self.data_loop,
            daemon=True,
        )

        self.control_thread.start()
        self.data_thread.start()

        self.control_thread.join()

        self.running = False

    def close(self):
        self.running = False

        self.reset_pit_upload()
        self.reset_file_transfer()

        if self.usb is not None:
            for fd in (
                self.usb.ep0,
                self.usb.out,
                self.usb.in_ep,
            ):
                try:
                    os.close(fd)
                except OSError:
                    pass

            self.usb.close()


def main():
    loke = Loke()

    try:
        loke.start()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(
            f"error: {e}",
            file=sys.stderr,
            flush=True,
        )
        raise
    finally:
        loke.close()


if __name__ == "__main__":
    main()