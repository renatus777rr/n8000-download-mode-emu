import struct
import sys

ENTRY_SIZE = 132
HEADER_SIZE = 28

def read_string(data):
    return data.split(b"\0", 1)[0].decode("ascii", errors="replace")

def parse_entry(data):
    if len(data) != ENTRY_SIZE:
        raise ValueError("invalid PIT entry size")

    fields = struct.unpack("<9I32s32s32s", data)

    return {
        "binary_type": fields[0],
        "device_type": fields[1],
        "identifier": fields[2],
        "attributes": fields[3],
        "update_attributes": fields[4],
        "block_offset": fields[5],
        "block_count": fields[6],
        "file_offset": fields[7],
        "file_size": fields[8],
        "partition_name": read_string(fields[9]),
        "flash_filename": read_string(fields[10]),
        "fota_filename": read_string(fields[11])
    }

def parse_pit(path):
    with open(path, "rb") as f:
        data = f.read()

    if len(data) < HEADER_SIZE:
        raise ValueError("PIT is too small")

    magic = data[:4]
    entry_count, unknown1, unknown2 = struct.unpack_from("<3I", data, 4)

    entries = []
    offset = HEADER_SIZE

    for _ in range(entry_count):
        entry_data = data[offset:offset + ENTRY_SIZE]

        if len(entry_data) != ENTRY_SIZE:
            raise ValueError("truncated PIT")

        entries.append(parse_entry(entry_data))
        offset += ENTRY_SIZE

    return magic, entry_count, unknown1, unknown2, entries

def main():
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} PIT")
        sys.exit(1)

    magic, entry_count, unknown1, unknown2, entries = parse_pit(sys.argv[1])

    print(f"Magic: {magic.hex()}")
    print(f"Entries: {entry_count}")
    print(f"Unknown1: {unknown1}")
    print(f"Unknown2: {unknown2}")
    print()

    for i, entry in enumerate(entries):
        start = entry["block_offset"]
        count = entry["block_count"]
        size = count * 512

        print(f"--- Entry #{i} ---")
        print(f"Partition: {entry['partition_name']}")
        print(f"Flash filename: {entry['flash_filename']}")
        print(f"FOTA filename: {entry['fota_filename']}")
        print(f"Binary type: {entry['binary_type']}")
        print(f"Device type: {entry['device_type']}")
        print(f"Identifier: {entry['identifier']}")
        print(f"Attributes: {entry['attributes']}")
        print(f"Update attributes: {entry['update_attributes']}")
        print(f"Start block: {start}")
        print(f"Block count: {count}")
        print(f"File offset: {entry['file_offset']}")
        print(f"File size: {entry['file_size']}")
        print(f"Partition size: {size} bytes ({size / 1024 / 1024:.2f} MiB)")
        print()

if __name__ == "__main__":
    main()