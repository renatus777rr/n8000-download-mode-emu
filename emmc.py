import argparse
import os
import sys
from pathlib import Path

from pit import parse_pit

SECTOR_SIZE = 512
DEFAULT_SIZE = 16 * 1024 * 1024 * 1024
BOOTLOADER_PARTITIONS = {"BOOTLOADER", "BOTA0", "BOTA1"}
ZERO_PARTITIONS = {"EFS", "OTA"}
CHUNK_SIZE = 1024 * 1024


def load_pit(path):
    _, _, _, _, entries = parse_pit(path)
    return {entry["partition_name"]: entry for entry in entries}


def partition_range(entry, total_sectors):
    start = entry["block_offset"]
    count = entry["block_count"]
    end = total_sectors if count == 0 else start + count

    if start > total_sectors or end > total_sectors or end < start:
        raise ValueError(
            f"partition {entry['partition_name']} exceeds eMMC capacity"
        )

    return start, end


class EMMC:
    def __init__(self, image, pit, size=DEFAULT_SIZE):
        self.image = Path(image)
        self.pit_path = Path(pit)
        self.partitions = load_pit(self.pit_path)
        self.size = size

        if size % SECTOR_SIZE:
            raise ValueError("eMMC size must be a multiple of 512 bytes")

        self.total_sectors = size // SECTOR_SIZE

        for entry in self.partitions.values():
            partition_range(entry, self.total_sectors)

    def create(self, force=False):
        self.image.parent.mkdir(parents=True, exist_ok=True)

        if self.image.exists() and not force:
            raise FileExistsError(f"{self.image} already exists")

        with open(self.image, "wb") as f:
            f.truncate(self.size)

        self.write_partition_file("PIT", self.pit_path)

        for name in ZERO_PARTITIONS:
            if name in self.partitions:
                self.zero_partition(name)

    def _check_range(self, lba, count):
        if lba < 0 or count < 0:
            raise ValueError("invalid LBA range")

        if lba + count > self.total_sectors:
            raise ValueError("LBA range outside eMMC")

    def read_sectors(self, lba, count):
        self._check_range(lba, count)

        with open(self.image, "rb") as f:
            f.seek(lba * SECTOR_SIZE)
            return f.read(count * SECTOR_SIZE)

    def write_sectors(self, lba, data):
        if len(data) % SECTOR_SIZE:
            raise ValueError("write data must be sector-aligned")

        count = len(data) // SECTOR_SIZE
        self._check_range(lba, count)

        with open(self.image, "r+b") as f:
            f.seek(lba * SECTOR_SIZE)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

    def get_partition(self, name):
        try:
            return self.partitions[name]
        except KeyError:
            names = ", ".join(self.partitions)
            raise KeyError(
                f"unknown partition {name}; available: {names}"
            )

    def get_partition_by_identifier(self, identifier):
        for name, entry in self.partitions.items():
            if entry["identifier"] == identifier:
                return name, entry

        raise KeyError(
            f"unknown partition identifier: {identifier}"
        )

    def partition_info(self, name):
        entry = self.get_partition(name)
        start, end = partition_range(
            entry,
            self.total_sectors,
        )
        return entry, start, end

    def write_partition_file(self, name, path):
        entry, start, end = self.partition_info(name)
        capacity = (end - start) * SECTOR_SIZE

        file_size = os.path.getsize(path)

        if file_size > capacity:
            raise ValueError(
                f"{name}: payload is {file_size} bytes "
                f"but partition capacity is {capacity} bytes"
            )

        with open(path, "rb") as src, open(self.image, "r+b") as dst:
            dst.seek(start * SECTOR_SIZE)

            remaining = file_size

            while remaining:
                data = src.read(min(CHUNK_SIZE, remaining))

                if not data:
                    raise IOError("unexpected end of input file")

                dst.write(data)
                remaining -= len(data)

            padding = (-file_size) % SECTOR_SIZE

            if padding:
                dst.write(b"\0" * padding)

            dst.flush()
            os.fsync(dst.fileno())

        return file_size

    def flash_file(self, name, path):
        if name == "BOOTLOADER":
            total = 0

            for target in (
                "BOOTLOADER",
                "BOTA0",
                "BOTA1",
            ):
                if target in self.partitions:
                    total += self.write_partition_file(
                        target,
                        path,
                    )

            return total

        return self.write_partition_file(
            name,
            path,
        )

    def zero_partition(self, name):
        _, start, end = self.partition_info(name)
        total = end - start

        chunk_sectors = 2048
        chunk = b"\0" * (
            chunk_sectors * SECTOR_SIZE
        )

        with open(self.image, "r+b") as f:
            f.seek(start * SECTOR_SIZE)

            remaining = total

            while remaining:
                n = min(
                    remaining,
                    chunk_sectors,
                )

                f.write(
                    chunk[:n * SECTOR_SIZE]
                )

                remaining -= n

            f.flush()
            os.fsync(f.fileno())

    def info(self):
        print(f"Image: {self.image}")
        print(f"Capacity: {self.size} bytes")
        print(
            f"Capacity: "
            f"{self.size / 1024 / 1024 / 1024:.2f} GiB"
        )
        print(f"Sectors: {self.total_sectors}")
        print()

        for name, entry in self.partitions.items():
            start, end = partition_range(
                entry,
                self.total_sectors,
            )

            size = (
                (end - start) *
                SECTOR_SIZE
            )

            if entry["block_count"] == 0:
                size_text = "remaining"
            else:
                size_text = (
                    f"{size / 1024 / 1024:.2f} MiB"
                )

            print(
                f"{name:12} "
                f"start={start:8} "
                f"sectors={end - start:8} "
                f"size={size_text:>12} "
                f"file={entry['flash_filename'] or '-'}"
            )


def parse_size(value):
    value = value.strip().upper()

    units = {
        "K": 1024,
        "M": 1024**2,
        "G": 1024**3,
        "KI": 1024,
        "MI": 1024**2,
        "GI": 1024**3,
    }

    for suffix in sorted(
        units,
        key=len,
        reverse=True,
    ):
        if value.endswith(suffix):
            return int(
                float(value[:-len(suffix)])
                * units[suffix]
            )

    return int(value)


def build_parser():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--image",
        default="n8000-emmc.bin",
    )

    parser.add_argument(
        "--pit",
        default=str(
            Path.home() /
            "Downloads" /
            "note10.pit"
        ),
    )

    parser.add_argument(
        "--size",
        type=parse_size,
        default=DEFAULT_SIZE,
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    sub.add_parser("create")
    sub.add_parser("info")

    flash = sub.add_parser("flash")
    flash.add_argument("partition")
    flash.add_argument("file")

    read = sub.add_parser("read")
    read.add_argument("partition")
    read.add_argument("output")

    zero = sub.add_parser("zero")
    zero.add_argument("partition")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    emmc = EMMC(
        args.image,
        args.pit,
        args.size,
    )

    if args.command == "create":
        emmc.create()
        print(
            f"created {emmc.image}"
        )
        return

    if not emmc.image.exists():
        raise FileNotFoundError(
            f"{emmc.image} does not exist; "
            f"run create first"
        )

    if args.command == "info":
        emmc.info()
        return

    if args.command == "flash":
        written = emmc.flash_file(
            args.partition,
            args.file,
        )

        print(
            f"flashed {args.file} to "
            f"{args.partition}: "
            f"{written} bytes written"
        )

        return

    if args.command == "read":
        _, start, end = emmc.partition_info(
            args.partition
        )

        if end - start > 8 * 1024 * 1024:
            raise ValueError(
                "partition is too large for direct read"
            )

        data = emmc.read_sectors(
            start,
            end - start,
        )

        with open(args.output, "wb") as f:
            f.write(data)

        print(
            f"read {args.partition}: "
            f"{len(data)} bytes"
        )

        return

    if args.command == "zero":
        emmc.zero_partition(
            args.partition
        )

        print(
            f"zeroed {args.partition}"
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(
            f"error: {e}",
            file=sys.stderr,
        )
        sys.exit(1)