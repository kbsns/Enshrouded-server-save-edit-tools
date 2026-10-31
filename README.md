# enshrouded-save-tools

Tools and notes to edit **Enshrouded** world saves on your own dedicated server:
container contents (including unique items with level, upgrades and gems), Flame
level, and an up-to-date list of item ids extracted from the game data.

> For private servers and single player worlds. Always keep a backup: a bad edit can
> cost you world progress. There is no anti-cheat on dedicated servers, but don't use
> this to ruin other people's games.

## What's inside

| path | content |
|---|---|
| `tools/ksdtool.py` | command line editor: check a save, list / add / fill / remove chest items, set the Flame level |
| `tools/ksd.py` | the library behind it (container, hashes, inventory records) |
| `tools/extract_items.py` | rebuilds the item list from the dedicated server files |
| `docs/SAVE_FORMAT.md` | save container, sections, chest records, unique item flags |
| `docs/GAME_DATA.md` | item table, `id = FNV-1a(GUID)`, internal names |
| `data/items.csv` / `data/items.md` | ~4,250 item ids, ~4,000 with a name |
| `data/overrides.csv` | names checked in game (add yours here) |
| `data/unknown.csv` | ids still without a name |

## Setup

Python 3.9+ and two packages:

```bash
pip install -r requirements.txt
```

## Editing a chest

1. Place a **Small Magic Chest** in your world (other container types use another id,
   pass it with `--container`).
2. **Stop the server.** It writes chest contents on shutdown and would overwrite your
   changes otherwise.
3. Edit, then start the server again.

```bash
# check the save and see what is in the chest
python3 tools/ksdtool.py info  savegame/world0.ksd
python3 tools/ksdtool.py list  savegame/world0.ksd

# 500 Stun Arrows in every free slot
python3 tools/ksdtool.py fill  savegame/world0.ksd 8d55f4f2 500 --in-place

# a max level Firefeather Bow with a level 40 Gem of Burning
python3 tools/ksdtool.py add   savegame/world0.ksd 2e16cb39 --level 55 --upgrades 5 \
        --gem da5cbec2 --gem-level 40 --in-place

# a max level armor piece (the game fills in durability)
python3 tools/ksdtool.py add   savegame/world0.ksd 0ed219fb --level 50 --upgrades 5 --in-place

# Flame level 11
python3 tools/ksdtool.py flame savegame/world0.ksd 11 --in-place
```

Without `--in-place` the result is written to `<save>.new`. With it, the original is
copied to `<save>.bak-<timestamp>` first. Every write is re-read to check the hashes.

Ids are in `data/items.md`. Tips:

- To find an id the list doesn't have, put one of that item in the chest and run
  `list`.
- Unique items (max stack 1) are clamped to their own maximum level. Some names exist
  under several ids with different level ranges; if an item shows up at a low level,
  try the other ids of the same name.
- For armor, use the first id of the pair (see `docs/GAME_DATA.md`).

## Updating the item list after a patch

Download the dedicated server (SteamCMD app `2278520`) or use your server install:

```bash
python3 tools/extract_items.py <server>/enshrouded_server.kfc_resources --cache /tmp/kfc.bin
```

It reads the current `data/items.csv` as a starting point, adds new table entries and
internal names, applies `data/overrides.csv` and rewrites `items.csv`, `items.md` and
`unknown.csv`. It takes about 5 minutes.

## Not possible from the world save

Character level, XP, position and inventory are stored on the client (Steam Cloud),
not on the server. The server has no admin console or RCON.

## Contributing

Checked an item in game? Add a line to `data/overrides.csv` (`id,name,confirmed,note`)
and rerun the extractor, or open an issue with a screenshot of the item and the
`list` output.

## Credits

- Names marked `legacy` come from an older community item list (see the note in
  `data/README.md`).
- Thanks to the authors of earlier Enshrouded save tools whose work pointed the way.
