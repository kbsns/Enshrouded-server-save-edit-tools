#!/usr/bin/env python3
"""Command line editor for Enshrouded world saves.

Always stop the dedicated server first: it only writes container contents
on shutdown and overwrites the file while running.
By default the result goes to <save>.new; --in-place replaces the save after
copying it to <save>.bak-<timestamp>.
"""
import argparse
import csv
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ksd import Inventory, Record, Save, SMALL_MAGIC_CHEST, get_flame, set_flame  # noqa: E402

DEFAULT_ITEMS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'items.csv')


def load_names(path):
    names = {}
    if path and os.path.exists(path):
        for row in csv.DictReader(open(path, encoding='utf-8')):
            names[int(row['id'], 16)] = row['name'] or row.get('internal_name', '')
    return names


def hexid(text):
    return int(text, 16)


def output(save, args):
    if args.in_place:
        backup = '%s.bak-%s' % (args.save, time.strftime('%Y%m%d-%H%M%S'))
        shutil.copy2(args.save, backup)
        save.write(args.save)
        print('backup:', backup)
        print('written:', args.save)
    else:
        save.write(args.save + '.new')
        print('written:', args.save + '.new')


def describe(r, names):
    s = 'slot %2d  %08x  %-40s x%d' % (r.slot, r.item, names.get(r.item, '?')[:40], r.qty)
    if r.level is not None:
        s += '  lvl %d' % r.level
    if r.upgrades is not None:
        s += '  upgrades %d/%d' % r.upgrades
    if r.durability is not None:
        s += '  durability %d/%d' % r.durability
    if r.gem is not None:
        s += '  gem %08x %s lvl %d' % (r.gem[0], names.get(r.gem[0], '?'), r.gem[1])
    return s


def cmd_info(args):
    save = Save(args.save)
    print('sections (hashes OK):')
    for n in save.names():
        print('  %-24s %s' % (n, 'zstd' if save.compressed(n) else 'raw'))
    print('flame level:', get_flame(save))


def cmd_flame(args):
    save = Save(args.save)
    print('before:', get_flame(save))
    set_flame(save, args.level)
    print('after: ', get_flame(save))
    output(save, args)


def cmd_list(args):
    names = load_names(args.items)
    inv = Inventory(Save(args.save), args.container, args.index)
    print('%d record(s), %d container(s) of this type found' % (len(inv.records), inv.containers_found))
    for r in sorted(inv.records, key=lambda r: r.slot):
        print(describe(r, names))


def cmd_add(args):
    names = load_names(args.items)
    save = Save(args.save)
    inv = Inventory(save, args.container, args.index)
    free = inv.free_slots(args.size)
    if len(free) < args.count:
        sys.exit('only %d free slot(s)' % len(free))
    for slot in free[:args.count]:
        r = Record(slot, args.item, args.qty, args.level,
                   tuple(args.durability) if args.durability else None,
                   (args.upgrades, args.upgrades) if args.upgrades is not None else None,
                   (args.gem, args.gem_level) if args.gem is not None else None)
        inv.records.append(r)
        print('add', describe(r, names))
    inv.commit()
    output(save, args)


def cmd_fill(args):
    """Set every stack of ITEM to QTY and put ITEM x QTY in every free slot."""
    save = Save(args.save)
    inv = Inventory(save, args.container, args.index)
    for r in inv.records:
        if r.item == args.item:
            r.qty = args.qty
    for slot in inv.free_slots(args.size):
        inv.records.append(Record(slot, args.item, args.qty))
    inv.commit()
    print('%d record(s)' % len(inv.records))
    output(save, args)


def cmd_remove(args):
    save = Save(args.save)
    inv = Inventory(save, args.container, args.index)
    before = len(inv.records)
    inv.records = [r for r in inv.records if r.slot not in args.slots]
    inv.commit()
    print('removed %d record(s)' % (before - len(inv.records)))
    output(save, args)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    def common(p, writes=False, container=False):
        p.add_argument('save', help='path to world save, e.g. savegame/<world>')
        if container:
            p.add_argument('--container', type=hexid, default=SMALL_MAGIC_CHEST,
                           help='container item id in hex (default: Small Magic Chest f1147751)')
            p.add_argument('--index', type=int, default=0, help='which container of that type (default 0)')
            p.add_argument('--size', type=int, default=24, help='container slot count (default 24)')
            p.add_argument('--items', default=DEFAULT_ITEMS, help='items.csv used for display names')
        if writes:
            p.add_argument('--in-place', action='store_true', help='overwrite the save (a backup is made)')
        return p

    common(sub.add_parser('info', help='check hashes, list sections, show flame level')).set_defaults(f=cmd_info)
    p = common(sub.add_parser('flame', help='set the Flame (altar) level'), writes=True)
    p.add_argument('level', type=int)
    p.set_defaults(f=cmd_flame)
    common(sub.add_parser('list', help='list a container'), container=True).set_defaults(f=cmd_list)

    p = common(sub.add_parser('add', help='add an item to free slots'), writes=True, container=True)
    p.add_argument('item', type=hexid, help='item id in hex')
    p.add_argument('--qty', type=int, default=1)
    p.add_argument('--count', type=int, default=1, help='number of slots to fill')
    p.add_argument('--level', type=int, help='item level (unique items)')
    p.add_argument('--upgrades', type=int, help='upgrades unlocked/applied, usually 5 at max level')
    p.add_argument('--durability', type=int, nargs=2, metavar=('CUR', 'MAX'))
    p.add_argument('--gem', type=hexid, help='socketed gem item id in hex')
    p.add_argument('--gem-level', type=int, default=1)
    p.set_defaults(f=cmd_add)

    p = common(sub.add_parser('fill', help='fill all free slots with a stack'), writes=True, container=True)
    p.add_argument('item', type=hexid)
    p.add_argument('qty', type=int)
    p.set_defaults(f=cmd_fill)

    p = common(sub.add_parser('remove', help='remove records by slot'), writes=True, container=True)
    p.add_argument('slots', type=int, nargs='+')
    p.set_defaults(f=cmd_remove)

    args = ap.parse_args()
    args.f(args)


if __name__ == '__main__':
    main()
