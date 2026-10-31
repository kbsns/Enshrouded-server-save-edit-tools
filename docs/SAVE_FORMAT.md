# Enshrouded world save format

Notes from reverse engineering a dedicated server save (game version of October 2026).
Everything is little endian. "Confirmed" means a change was written, loaded by the
server and checked in game.

## Where the world lives

On the dedicated server used for these notes: `savegame/world0.ksd`, next to a
`savegame/backup/` folder. Other installs may use another file name; the format is
recognised by its `SV2C` magic.
Characters (level, XP, inventory, position) are **not** in the world save: they are
stored on the client (Steam Cloud).

The server only writes container contents reliably when it shuts down. Stop it,
edit, start it again. Editing while it runs gets overwritten.

## Container (`SV2C`)

| offset | type | meaning |
|---|---|---|
| 0x00 | char[4] | `SV2C` |
| 0x08 | u32 | total file size |
| 0x28 | u32 | section table offset, relative to 0x28 |
| 0x2c | u32 | section count |

The header is 48 bytes. Section blocks follow, then the table (aligned to 16 bytes),
then the section names.

Table entry, 20 bytes:

| offset | type | meaning |
|---|---|---|
| 0 | u32 | name offset, relative to this entry |
| 4 | u32 | name length |
| 8 | u32 | block offset |
| 12 | u32 | block size |
| 16 | u32 | `XXH3_64(block) & 0xffffffff` |

A wrong hash makes the server reject the world, so recompute it after any change.
Rebuilding a save with `tools/ksd.py` without changes gives a byte-identical file.

### Sections

Seen: `world_metadata`, `base00..04`, `entity_chunk00`, `snapping`, `miscellaneous`,
`world_knowledge`, `custom_strings`, `weather`, `logic`, `chunk_persistent_data`,
`known_users`.

Each block starts with a 14-byte header: `S2BH`, u32 version (2), u8 section type,
u8 **compressed flag** (1 = the rest of the block is one zstd frame), then 4 bytes not
needed for editing.

## Flame level (confirmed)

u32 stored twice, in uncompressed sections. Offsets count from the start of the
block, header included:

- `miscellaneous` + 34
- `world_metadata` + 67

Set both, then fix the two section hashes. Level 11 loads fine.

## Containers (confirmed for the Small Magic Chest)

In `entity_chunk00` (zstd), a container's inventory follows its item id (u32 LE) and
a `0x03` byte:

```
u32 container item id   e.g. f1147751 Small Magic Chest -> bytes 51 77 14 f1
u8  0x03
u8  record count
records...
```

The chest has 24 slots. Records must not repeat a slot. Adding records and updating
the count works; nothing else references the length.

### Item record

| size | field |
|---|---|
| u8 | slot |
| u32 | item id |
| u16 | quantity |
| u8 | flags |
| … | optional blocks, in this order, present when their flag is set |

| flag | block | notes |
|---|---|---|
| 0x01 | u8 level | weapons, armor, rings, eternal ammo |
| 0x02 | u32 durability, u32 max durability | the game resets max durability to the item's real value on save |
| 0x10 | u8, u8 upgrades | `5 5` on max level items (5 enhancement rows unlocked and applied) |
| 0x20 | u32 gem item id, u8 gem level | socketed gem |

Other flag bits have not been seen yet; `ksd.py` refuses to parse them rather than
guessing.

Examples from the game:

```
00 57897128 0100 11 | 32 | 05 05
   Eternal Ember Arrow III, level 50, upgrades 5/5
01 2dc69cd4 0100 33 | 33 | 99010000 ee010000 | 05 05 | c2be5cda 0d
   Phoenix Bow, level 51, durability 409/494, upgrades 5/5, Gem of Burning level 13
```

### What has been tested

- Stack items (runes, arrows, scrolls): quantity up to the item's max stack.
- Unique items written from scratch: weapons with gem and gem level, armor
  (level + upgrades, no durability block; the game fills it in), eternal arrows.
- Level is clamped to the item's own maximum. Some items share a display name across
  several ids with different level ranges (for example older ring ids that cap at
  level 15–20); pick the id whose range you want.
