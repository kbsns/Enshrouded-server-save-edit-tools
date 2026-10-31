# Item ids from the game data

The dedicated server is free to download (SteamCMD app `2278520`) and contains the
item definitions, but no translations. `tools/extract_items.py` rebuilds
`data/items.csv` from it after each game patch.

## `enshrouded_server.kfc_resources`

A sequence of zstd frames. Decompress each frame (split on the magic `28 b5 2f fd`;
the magic can also occur inside compressed data, so skip frames that fail) and
concatenate them: about 350 MB.

## Item id = FNV-1a(GUID)

Every resource has a 16-byte GUID. An item id is the 32-bit FNV-1a hash of its GUID:

```python
def fnv1a(data, h=0x811c9dc5):
    for c in data:
        h = ((h ^ c) * 0x01000193) & 0xffffffff
    return h
```

That is why references often look like `GUID(16) id(4)`: the id is a cached hash of
the GUID right before it.

## Item table

36-byte entries starting with the item id:

| offset | type | meaning |
|---|---|---|
| 0 | u32 | item id |
| 4 | u8 | entry type: `0x1f` regular, `0x1b` armor stats twin, other values for rings |
| 6 | u8 | rarity: 0 common, 1 uncommon, 2 rare, 3 epic, 4 legendary (reliable for ammo, spells, tools, consumables; named weapons and armor stats twins show 0 even when legendary) |
| 16 | u32 | offset of a detailed record (equipment only) |
| 24 | u16, u16 | small counters, never 0 (used to tell real entries from noise) |
| 28 | u16 | max stack; 1 = unique, upgradeable item |
| 31 | u8 | equipment class: 1 weapon, 3 armor, 4 ring |
| 33 | u8 | weapon type when class is 1: 1 one-handed, 2 axe/hammer, 3 greatsword, 4 bow, 5 dagger, 6 staff, 7 wand (guessed from known items) |

Items of the same family are stored next to each other: all arrows and spells by tier,
tools, food, and weapons in pairs of the same model (Tribal Hammer / Chieftain's
Hammer, Phoenix Bow / Firefeather Bow…). That is how unnamed items can often be
identified.

**Armor pieces have two ids**: a `0x1f` entry immediately followed by a `0x1b` entry
with the same display name, which carries the stats. The `0x1f` id (the crafted
item) is the one to put in a chest. Legendary set pieces such as Vorgoth's have a
single entry.

## Internal names

Crafting recipes contain the crafted item followed by its internal name:

```
GUID(16) item id(4) count(4) [u32 hash] name
```

The name is NUL terminated or padded to a 4-byte boundary, so when the next field
starts right after it, cut at the last multiple of 4. Examples:
`Ammo_Arrow_T7_Explosive` (Explosive Arrow IV), `Armor_T7_Ranger_Helmet`
(Ashwind Strider Hood), `Ammo_Arrow_T7_Magic_Fire_Eternal` (Eternal Ember Arrow III).

Items without a recipe (loot-only legendaries, most rings) have no internal name in
the server files; they need a name from the community list or a check in game.
