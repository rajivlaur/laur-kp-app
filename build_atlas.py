# SPDX-License-Identifier: AGPL-3.0-or-later
"""Rebuild the offline atlas from GeoNames public data files (CC BY 4.0)."""
import io
import sqlite3
import sys
import unicodedata
import zipfile
from pathlib import Path


def normalize(value):
    value = unicodedata.normalize('NFKD', value.casefold())
    return ' '.join(''.join(c for c in value if not unicodedata.combining(c)).split())


def build(cities, countries, regions, target):
    names = {}
    with open(countries, encoding='utf-8') as f:
        for line in f:
            a = line.rstrip('\n').split('\t')
            if not line.startswith('#') and len(a) > 4:
                names[a[0]] = a[4]
    areas = {}
    with open(regions, encoding='utf-8') as f:
        for line in f:
            a = line.rstrip('\n').split('\t')
            if len(a) > 2:
                areas[a[0]] = a[1]
    if Path(target).exists():
        raise FileExistsError(target)
    count = 0
    with sqlite3.connect(target) as db, zipfile.ZipFile(cities) as z:
        db.executescript("""
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            CREATE TABLE place (id INTEGER PRIMARY KEY, name TEXT, region TEXT, country TEXT,
                code TEXT, lat REAL, lon REAL, zone TEXT, population INTEGER);
            CREATE TABLE alias (key TEXT, id INTEGER, PRIMARY KEY (key,id)) WITHOUT ROWID;
        """)
        with io.TextIOWrapper(z.open('cities500.txt'), encoding='utf-8') as f:
            rows, aliases = [], []
            for line in f:
                a=line.rstrip('\n').split('\t')
                if len(a)!=19 or not a[17] or not a[1]:
                    continue
                key = int(a[0]); count += 1
                rows.append((key, a[1], areas.get(a[8]+'.'+a[10],''), names.get(a[8],a[8]), a[8],
                    float(a[4]),float(a[5]),a[17],int(a[14] or 0)))
                clean = {normalize(x) for x in [a[1],a[2],*a[3].split(',')] if 1 <= len(x) <= 90}
                aliases.extend((n,key) for n in clean if n)
                if len(rows)==5000:
                    db.executemany('INSERT INTO place VALUES (?,?,?,?,?,?,?,?,?)',rows)
                    db.executemany('INSERT INTO alias VALUES (?,?)',aliases)
                    rows.clear(); aliases.clear()
            db.executemany('INSERT INTO place VALUES (?,?,?,?,?,?,?,?,?)',rows)
            db.executemany('INSERT INTO alias VALUES (?,?)',aliases)
    return count


if __name__ == '__main__':
    if len(sys.argv) != 5:
        raise SystemExit('Usage: python build_atlas.py cities500.zip countryInfo.txt admin1CodesASCII.txt atlas.sqlite3')
    print(build(*sys.argv[1:]), 'places')
