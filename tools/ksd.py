"""Read and write Enshrouded world saves (KSD / SV2C container).

Container layout (little endian):
  0x00  'SV2C'
  0x08  u32 total file size
  0x28  u32 section table offset, relative to 0x28
  0x2c  u32 section count
Each table entry is 20 bytes: name_rel (relative to the entry), name_len,
offset, size, hash. hash = XXH3_64(section bytes) & 0xffffffff.
Each section starts with a 14-byte 'S2BH' header; byte 9 is 1 when the body
(bytes 14..) is a zstd frame.
"""
import struct

import xxhash
import zstandard

HEADER_SIZE = 48
SECTION_HEADER = 14


def h32(data):
    return xxhash.xxh3_64_intdigest(bytes(data)) & 0xffffffff


class Save:
    def __init__(self, path):
        self.path = path
        raw = open(path, 'rb').read()
        if raw[:4] != b'SV2C' or struct.unpack_from('<I', raw, 8)[0] != len(raw):
            raise ValueError('not an SV2C save or truncated file')
        self.header = raw[:HEADER_SIZE]
        tab = 0x28 + struct.unpack_from('<I', raw, 0x28)[0]
        count = struct.unpack_from('<I', raw, 0x2c)[0]
        self.sections = []
        for i in range(count):
            at = tab + 20 * i
            name_at = at + struct.unpack_from('<I', raw, at)[0]
            name_len, off, size, h = struct.unpack_from('<4I', raw, at + 4)
            block = raw[off:off + size]
            if h32(block) != h:
                raise ValueError('hash mismatch in section %d' % i)
            self.sections.append([raw[name_at:name_at + name_len].decode(), block])

    def names(self):
        return [n for n, _ in self.sections]

    def _find(self, name):
        for s in self.sections:
            if s[0] == name:
                return s
        raise KeyError(name)

    def compressed(self, name):
        return self._find(name)[1][9] == 1

    def body(self, name):
        """Section payload, decompressed when needed."""
        block = self._find(name)[1]
        payload = block[SECTION_HEADER:]
        if block[9] == 1:
            payload = zstandard.ZstdDecompressor().decompressobj().decompress(payload)
        return bytearray(payload)

    def set_body(self, name, payload):
        s = self._find(name)
        head = s[1][:SECTION_HEADER]
        if head[9] == 1:
            payload = zstandard.ZstdCompressor(level=3).compress(bytes(payload))
        s[1] = bytes(head) + bytes(payload)

    def serialize(self):
        pos, offsets = HEADER_SIZE, []
        for _, block in self.sections:
            offsets.append(pos)
            pos += len(block)
        tab = (pos + 15) & ~15
        names_at = tab + 20 * len(self.sections)
        total = names_at + sum(len(n.encode()) for n, _ in self.sections)
        out = bytearray(total)
        out[:HEADER_SIZE] = self.header
        struct.pack_into('<I', out, 8, total)
        struct.pack_into('<I', out, 0x28, tab - 0x28)
        struct.pack_into('<I', out, 0x2c, len(self.sections))
        na = names_at
        for i, (name, block) in enumerate(self.sections):
            n = name.encode()
            out[offsets[i]:offsets[i] + len(block)] = block
            at = tab + 20 * i
            struct.pack_into('<5I', out, at, na - at, len(n), offsets[i], len(block), h32(block))
            out[na:na + len(n)] = n
            na += len(n)
        return bytes(out)

    def write(self, path):
        data = self.serialize()
        open(path, 'wb').write(data)
        Save(path)  # re-read to verify hashes
        return len(data)


# --- flame level --------------------------------------------------------------
# Stored twice as u32, in uncompressed sections (offsets include the 14-byte header).
FLAME_FIELDS = {'miscellaneous': 34, 'world_metadata': 67}


def get_flame(save):
    return {n: struct.unpack_from('<I', save._find(n)[1], off)[0] for n, off in FLAME_FIELDS.items()}


def set_flame(save, level):
    for n, off in FLAME_FIELDS.items():
        s = save._find(n)
        if s[1][9] != 0:
            raise ValueError('%s is unexpectedly compressed' % n)
        block = bytearray(s[1])
        struct.pack_into('<I', block, off, level)
        s[1] = bytes(block)


# --- container inventories ----------------------------------------------------
# An inventory follows the container's item id (u32 LE) and a 0x03 byte, then
# u8 record count and the records. Record:
#   u8 slot, u32 item id, u16 quantity, u8 flags, then optional blocks by flag:
#   0x01 u8 level
#   0x02 u32 durability, u32 max durability
#   0x10 u8 upgrades unlocked, u8 upgrades applied (seen 5,5 at max level)
#   0x20 u32 socketed gem item id, u8 gem level
FLAG_LEVEL, FLAG_DURABILITY, FLAG_UPGRADES, FLAG_GEM = 0x01, 0x02, 0x10, 0x20
KNOWN_FLAGS = FLAG_LEVEL | FLAG_DURABILITY | FLAG_UPGRADES | FLAG_GEM
SMALL_MAGIC_CHEST = 0xf1147751
ENTITY_SECTION = 'entity_chunk00'


class Record:
    def __init__(self, slot, item, qty=1, level=None, durability=None, upgrades=None, gem=None):
        self.slot, self.item, self.qty = slot, item, qty
        self.level, self.durability, self.upgrades, self.gem = level, durability, upgrades, gem

    @classmethod
    def parse(cls, d, p):
        slot = d[p]
        item, qty, flags = struct.unpack_from('<IHB', d, p + 1)
        if flags & ~KNOWN_FLAGS:
            raise ValueError('unknown record flags 0x%02x at %d' % (flags, p))
        p += 8
        r = cls(slot, item, qty)
        if flags & FLAG_LEVEL:
            r.level = d[p]; p += 1
        if flags & FLAG_DURABILITY:
            r.durability = struct.unpack_from('<II', d, p); p += 8
        if flags & FLAG_UPGRADES:
            r.upgrades = (d[p], d[p + 1]); p += 2
        if flags & FLAG_GEM:
            r.gem = struct.unpack_from('<IB', d, p); p += 5
        return r, p

    def pack(self):
        flags = ((self.level is not None and FLAG_LEVEL) | (self.durability is not None and FLAG_DURABILITY)
                 | (self.upgrades is not None and FLAG_UPGRADES) | (self.gem is not None and FLAG_GEM))
        out = struct.pack('<BIHB', self.slot, self.item, self.qty, flags)
        if self.level is not None:
            out += bytes([self.level])
        if self.durability is not None:
            out += struct.pack('<II', *self.durability)
        if self.upgrades is not None:
            out += bytes(self.upgrades)
        if self.gem is not None:
            out += struct.pack('<IB', *self.gem)
        return out


class Inventory:
    """Inventory of one container inside the entity section."""

    def __init__(self, save, container=SMALL_MAGIC_CHEST, index=0):
        self.save = save
        self.data = save.body(ENTITY_SECTION)
        marker = struct.pack('<I', container) + b'\x03'
        hits, i = [], self.data.find(marker)
        while i >= 0:
            hits.append(i)
            i = self.data.find(marker, i + 1)
        if not hits:
            raise LookupError('container %08x not found (is the server stopped?)' % container)
        self.count_at = hits[index] + 5
        p = self.count_at + 1
        self.records = []
        for _ in range(self.data[self.count_at]):
            r, p = Record.parse(self.data, p)
            self.records.append(r)
        self.end = p
        self.containers_found = len(hits)

    def free_slots(self, size=24):
        used = {r.slot for r in self.records}
        return [s for s in range(size) if s not in used]

    def commit(self):
        self.records.sort(key=lambda r: r.slot)
        blob = bytes([len(self.records)]) + b''.join(r.pack() for r in self.records)
        self.data[self.count_at:self.end] = blob
        self.end = self.count_at + len(blob)
        self.save.set_body(ENTITY_SECTION, self.data)
