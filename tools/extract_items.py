#!/usr/bin/env python3
"""Rebuild the item list from the dedicated server's game data.

Usage:
  extract_items.py <server>/enshrouded_server.kfc_resources [--out data]

The dedicated server (free on SteamCMD, app 2278520) ships the same item
definitions as the client, without translations. What we rely on:

* kfc_resources is a series of zstd frames; concatenated they form one blob.
* Item id = FNV-1a 32 of the item resource's 16-byte GUID.
* Item table: 36-byte entries starting with the item id.
    +4  u8 type (0x1f regular, 0x1b armor stats twin, other values for rings...)
    +6  u8 rarity (0 common .. 4 legendary)
    +28 u16 max stack (1 = unique, upgradeable)
    +31 u8 equipment class (1 weapon, 3 armor, 4 ring)
  Armor pieces are a pair: a 0x1f entry immediately followed by a 0x1b entry
  carrying the stats, both with the same display name.
* Recipe records give internal names:
    GUID(16) id(4) count(4) [hash(4)] name\\0   (name padded to 4 bytes)
  The id right before the name is the crafted item.

Item names shown in game are not in the server files. They come from
data/items.csv (previous run / community) and data/overrides.csv
(confirmed in game).
"""
import argparse
import collections
import csv
import os
import re
import struct
import sys

import zstandard

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, '..', 'data')
ENTRY = 36
EQUIPMENT = {1: 'weapon', 3: 'armor', 4: 'ring'}  # table entry byte +31
RARITY = ['common', 'uncommon', 'rare', 'epic', 'legendary']
PREFIXES = [
    ('Ammo_Arrow', 'arrow'), ('Ammo_Spell', 'spell'), ('Ammo_T', 'spell'), ('Armor_', 'armor'),
    ('Shield', 'shield'), ('Craft_Weapon', 'weapon'), ('Weapon_', 'weapon'), ('Tool', 'tool'),
    ('Prop_Decoration', 'decoration'), ('Prop_', 'prop'), ('Food_', 'food'), ('Material_', 'material'),
    ('Consumable', 'consumable'), ('Block_', 'block'), ('Vanity', 'cosmetic'), ('VC_', 'character'),
    ('Animal_', 'animal'), ('Ability_', 'ability'),
]


def fnv1a(data, h=0x811c9dc5):
    for c in data:
        h = ((h ^ c) * 0x01000193) & 0xffffffff
    return h


def decompress(path, cache=None):
    if cache and os.path.exists(cache):
        return open(cache, 'rb').read()
    raw = open(path, 'rb').read()
    starts = [m.start() for m in re.finditer(rb'\x28\xb5\x2f\xfd', raw)]
    out = bytearray()
    for i, s in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(raw)
        try:
            out += zstandard.ZstdDecompressor().decompressobj().decompress(raw[s:end])
        except zstandard.ZstdError:
            pass  # magic bytes inside compressed data
    if cache:
        open(cache, 'wb').write(out)
    return bytes(out)


def read_csv(path):
    if not os.path.exists(path):
        return {}
    return {int(r['id'], 16): r for r in csv.DictReader(open(path, encoding='utf-8'))}


def find_table(blob, seeds):
    """Locate item table entries, starting from known ids and walking 36-byte neighbours."""
    def ok(q):
        if q < 0 or q + ENTRY > len(blob):
            return False
        st, a, b = (struct.unpack_from('<H', blob, q + o)[0] for o in (28, 24, 26))
        return blob[q + 7] == 0 and blob[q + 8:q + 12] == b'\0' * 4 and 1 <= st <= 10000 \
            and 1 <= a <= 64 and 1 <= b <= 64 and struct.unpack_from('<I', blob, q)[0] > 0xffff

    hits = collections.defaultdict(list)
    for item in seeds:
        needle = struct.pack('<I', item)
        p = blob.find(needle)
        while p >= 0:
            if ok(p):
                hits[p >> 20].append(p)
            p = blob.find(needle, p + 1)
    # the table lives in the 1 MiB windows holding most valid seed entries
    best = max(hits, key=lambda k: len(hits[k]))
    lo, hi = (best - 1) << 20, (best + 2) << 20

    pos = set()
    for k in range(best - 1, best + 2):
        pos.update(hits.get(k, []))
    for q in sorted(pos):
        for step in (ENTRY, -ENTRY):
            r = q + step
            while lo <= r < hi and ok(r) and r not in pos:
                pos.add(r)
                r += step
    table = {}
    for q in sorted(pos):
        table.setdefault(struct.unpack_from('<I', blob, q)[0], q)
    return table


def read_name(blob, p):
    m = re.compile(rb'[A-Za-z0-9_\- ]+').match(blob, p)
    if not m or not blob[p:p + 1].isalpha():
        return None
    s = m.group(0)
    if blob[m.end()] != 0:  # no terminator: the next field starts at 4-byte alignment
        s = s[:len(s) // 4 * 4]
    return s.decode() if len(s) >= 4 else None


def internal_names(blob, ids):
    """Map item id -> internal name using recipe records."""
    names = collections.defaultdict(collections.Counter)
    for m in re.finditer(rb'[A-Za-z][A-Za-z0-9_\-]{3,}', blob):
        for start in range(m.start(), min(m.start() + 4, m.end())):
            for back in (24, 28):
                g = start - back
                if g < 0:
                    continue
                item = struct.unpack_from('<I', blob, g + 16)[0]
                if item in ids and fnv1a(blob[g:g + 16]) == item:
                    n = read_name(blob, start)
                    if n:
                        names[item][n] += 1
    return {k: c.most_common(1)[0][0] for k, c in names.items() if len(c) == 1 or c.most_common(2)[0][1] > c.most_common(2)[1][1]}


def category(internal):
    for prefix, cat in PREFIXES:
        if internal.startswith(prefix):
            return cat
    return ''


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('resources', help='enshrouded_server.kfc_resources')
    ap.add_argument('--out', default=DATA)
    ap.add_argument('--cache', help='keep the decompressed blob here to speed up reruns')
    args = ap.parse_args()

    known = read_csv(os.path.join(args.out, 'items.csv'))
    overrides = read_csv(os.path.join(args.out, 'overrides.csv'))
    if not known:
        sys.exit('data/items.csv is needed as seed list')

    blob = decompress(args.resources, args.cache)
    print('decompressed %d bytes' % len(blob), file=sys.stderr)
    table = find_table(blob, list(known))
    print('item table: %d entries' % len(table), file=sys.stderr)
    # keep table entries, plus named items the table walk did not reach
    ids = set(table) | {k for k, r in known.items() if r.get('name')}
    internals = internal_names(blob, ids)
    print('internal names: %d' % len(internals), file=sys.stderr)

    rows = {}
    for item in sorted(ids):
        q = table.get(item)
        k = known.get(item, {})
        internal = internals.get(item) or k.get('internal_name', '')
        name = k.get('name', '') if k.get('source') in ('legacy', 'override', 'twin') else ''
        source = k.get('source') if name else ('internal' if internal else 'unknown')
        rows[item] = {
            'id': '%08x' % item, 'name': name, 'internal_name': internal,
            'category': category(internal) or (q is not None and EQUIPMENT.get(blob[q + 31])) or k.get('category', ''),
            'rarity': RARITY[blob[q + 6]] if q is not None and blob[q + 6] < 5 else k.get('rarity', ''),
            'max_stack': k.get('max_stack') or (struct.unpack_from('<H', blob, q + 28)[0] if q is not None else ''),
            'source': source, 'confirmed': k.get('confirmed', ''),
        }
    # armor stats twins share the name of the entry right before them
    by_pos = {q: i for i, q in table.items()}
    for item, q in table.items():
        prev = by_pos.get(q - ENTRY)
        if blob[q + 4] == 0x1b and prev is not None and blob[q - ENTRY + 4] == 0x1f:
            r, p = rows[item], rows[prev]
            if not r['name'] and not r['internal_name'] and (p['name'] or p['internal_name']):
                r['name'], r['internal_name'] = p['name'], p['internal_name']
                r['category'], r['source'] = 'armor', 'twin'
                r['note'] = 'stats twin of %s' % p['id']
    for item, o in overrides.items():
        if item in rows:
            rows[item]['name'] = o['name']
            rows[item]['source'] = 'override'
            rows[item]['confirmed'] = o.get('confirmed', '')
    for r in rows.values():
        r['unique'] = 'yes' if str(r['max_stack']) == '1' else 'no'
        r['label'] = r['name'] or r['internal_name']

    write_outputs(rows, args.out)


def write_outputs(rows, out):
    cols = ['id', 'name', 'internal_name', 'category', 'rarity', 'max_stack', 'unique', 'source', 'confirmed', 'note']
    ordered = sorted(rows.values(), key=lambda r: (r['label'] == '', r['category'], r['label'].lower()))
    with open(os.path.join(out, 'items.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, cols, extrasaction='ignore')
        w.writeheader()
        w.writerows(ordered)
    with open(os.path.join(out, 'unknown.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, ['id', 'rarity', 'max_stack', 'unique'], extrasaction='ignore')
        w.writeheader()
        w.writerows(r for r in ordered if not r['label'])
    named = [r for r in ordered if r['label']]
    groups = collections.defaultdict(list)
    for r in named:
        groups[r['category'] or 'other'].append(r)
    with open(os.path.join(out, 'items.md'), 'w', encoding='utf-8') as f:
        f.write('# Enshrouded item ids\n\n')
        f.write('Generated by `tools/extract_items.py`. %d identified items, %d without a name '
                '(see `unknown.csv`).\n\n' % (len(named), len(rows) - len(named)))
        f.write('Ids are written the way you read them (big endian); in save files they are stored '
                'little endian. ✔ = checked in game.\n\n')
        f.write('Source: `override` checked by hand, `legacy` older community list, `internal` internal '
                'name from game data, `twin` armor stats entry named after its pair.\n\n')
        for cat in sorted(groups):
            f.write('- [%s](#%s) (%d)\n' % (cat, cat, len(groups[cat])))
        for cat in sorted(groups):
            f.write('\n## %s\n\n| id | name | internal name | rarity | stack | source |\n|---|---|---|---|---|---|\n' % cat)
            for r in groups[cat]:
                f.write('| `%s` | %s%s | %s | %s | %s | %s |\n' % (
                    r['id'], r['name'].replace('|', '\\|'), ' ✔' if r['confirmed'] else '',
                    r['internal_name'] and '`%s`' % r['internal_name'], r['rarity'], r['max_stack'], r['source']))
    print('wrote items.csv (%d), items.md (%d named), unknown.csv' % (len(rows), len(named)), file=sys.stderr)


if __name__ == '__main__':
    main()
