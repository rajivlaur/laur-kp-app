# SPDX-License-Identifier: AGPL-3.0-or-later
"""Offline KP chart calculations for Python 3.11+.

Runtime: pyswisseph==2.10.3.2, plus the standard Swiss ephemeris files
sepl_18.se1 and semo_18.se1 in ephe/ beside this module (or KP_EPHE).
IANA zones use the locally installed IANA database (OS or tzdata package).

The provisional ayanamsa is Swiss SIDM_KRISHNAMURTI_VP291 (45); this
is not a claim of an authoritative match to every 'New KP' implementation.
Input uses the proleptic Gregorian calendar. UTC is used as an approximation
of UT1, without historical civil-calendar or leap-second reconstruction.
"""
from __future__ import annotations

import math
import os
import re
from bisect import bisect_right
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from fractions import Fraction
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from threading import RLock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    'calculate', 'resolve_local_time', 'horary_segment', 'vimshottari_periods',
    'KPError', 'TimeZoneError', 'EphemerisError', 'HouseError', 'HORARY_COUNT',
    'SIDEREAL_MODE_ID', 'VIMSHOTTARI_YEAR_DAYS', 'SUN_CUSP_ORB', 'PLANET_CUSP_ORB',
]

SIDEREAL_MODE_ID = 45
VIMSHOTTARI_YEAR_DAYS = 365.25
SUN_CUSP_ORB = 6 + 40 / 60      # 6°40′
PLANET_CUSP_ORB = 3 + 20 / 60   # 3°20′
SWISS_VERSION = '2.10.3.2'
MIN_YEAR, MAX_YEAR = 1801, 2398
UTC = timezone.utc
_ARCSEC_PER_DEG = 3600
_ZODIAC_AS, _SIGN_AS, _STAR_AS = 1296000, 108000, 48000
_YEAR_US = int(VIMSHOTTARI_YEAR_DAYS * 86400 * 1_000_000)
_TOL = 1e-8  # degrees; geometry verification, not stellar boundary assignment
_SWISS_LOCK = RLock()
_SIGNS = ('Aries', 'Taurus', 'Gemini', 'Cancer', 'Leo', 'Virgo', 'Libra',
          'Scorpio', 'Sagittarius', 'Capricorn', 'Aquarius', 'Pisces')
_SIGN_LORDS = ('Mars', 'Venus', 'Mercury', 'Moon', 'Sun', 'Mercury', 'Venus',
               'Mars', 'Jupiter', 'Saturn', 'Saturn', 'Jupiter')
_STARS = ('Ashwini', 'Bharani', 'Krittika', 'Rohini', 'Mrigashira', 'Ardra',
          'Punarvasu', 'Pushya', 'Ashlesha', 'Magha', 'Purva Phalguni',
          'Uttara Phalguni', 'Hasta', 'Chitra', 'Swati', 'Vishakha', 'Anuradha',
          'Jyeshtha', 'Mula', 'Purva Ashadha', 'Uttara Ashadha', 'Shravana',
          'Dhanishta', 'Shatabhisha', 'Purva Bhadrapada', 'Uttara Bhadrapada',
          'Revati')
_LORDS = ('Ketu', 'Venus', 'Sun', 'Moon', 'Mars', 'Rahu', 'Jupiter', 'Saturn', 'Mercury')
_YEARS = dict(zip(_LORDS, (7, 20, 6, 10, 7, 18, 16, 19, 17)))
_WEEKDAY_LORDS = ('Moon', 'Mars', 'Mercury', 'Jupiter', 'Venus', 'Saturn', 'Sun')
_BODIES = (('Sun', 'SUN'), ('Moon', 'MOON'), ('Mars', 'MARS'),
           ('Mercury', 'MERCURY'), ('Jupiter', 'JUPITER'), ('Venus', 'VENUS'),
           ('Saturn', 'SATURN'), ('Rahu', 'MEAN_NODE'))
_DATE_RE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
_TIME_RE = re.compile(r'[0-9]{2}:[0-9]{2}(?::[0-9]{2})?')
_OFFSET_RE = re.compile(r'([+-])([0-9]{2}):([0-9]{2})')


class KPError(ValueError):
    """Invalid chart input or a chart that cannot be calculated as requested."""


class TimeZoneError(KPError):
    """Invalid/unavailable timezone or invalid, ambiguous, or nonexistent time."""


class EphemerisError(KPError):
    """The pinned Swiss dependency or required file-backed ephemeris failed."""


class HouseError(KPError):
    """The requested Placidus geometry could not be calculated without fallback."""


def _cycle(first: str) -> tuple[str, ...]:
    start = _LORDS.index(first)
    return _LORDS[start:] + _LORDS[:start]


def _build_zodiac():
    subs = []
    subsubs = []
    for i in range(27):
        pos = i * _STAR_AS
        star_lord = _LORDS[i % 9]
        for sub in _cycle(star_lord):
            subs.append(pos)
            width = _STAR_AS * _YEARS[sub] // 120
            subsub_pos = Fraction(pos)
            for subsub in _cycle(sub):
                subsubs.append((float(subsub_pos / _ARCSEC_PER_DEG), i, sub, subsub))
                subsub_pos += Fraction(width * _YEARS[subsub], 120)
            pos += width
            assert subsub_pos == pos
        assert pos == (i + 1) * _STAR_AS
    starts = tuple(sorted(set(subs) | set(range(0, _ZODIAC_AS, _SIGN_AS))))
    assert len(subs) == 243 and len(starts) == 249
    return starts + (_ZODIAC_AS,), tuple(subsubs)


_HORARY_EDGES_AS, _SUBSUBS = _build_zodiac()
_SUBSUB_STARTS = tuple(row[0] for row in _SUBSUBS)
_SIGN_STARTS = tuple(30.0 * i for i in range(12))
_STAR_STARTS = tuple(i * _STAR_AS / _ARCSEC_PER_DEG for i in range(27))
HORARY_COUNT = len(_HORARY_EDGES_AS) - 1


def _wrap(deg: float) -> float:
    value = deg % 360.0
    return 0.0 if value == 360.0 else value


def _gap(a: float, b: float) -> float:
    return abs(math.remainder(a - b, 360.0))


def _stellar(lon: float):
    _, ordinal, sub, subsub = _SUBSUBS[bisect_right(_SUBSUB_STARTS, lon) - 1]
    return ordinal, _LORDS[ordinal % 9], sub, subsub


def horary_segment(number: int) -> dict:
    """Return the 1-based KP horary segment and its half-open bounds in degrees."""
    if type(number) is not int or not 1 <= number <= HORARY_COUNT:
        raise KPError(f'horary must be an integer from 1 through {HORARY_COUNT}')
    start = _HORARY_EDGES_AS[number - 1] / _ARCSEC_PER_DEG
    end = _HORARY_EDGES_AS[number] / _ARCSEC_PER_DEG
    ordinal, star, sub, subsub = _stellar(start)
    sign_index = bisect_right(_SIGN_STARTS, start) - 1
    return {'number': number, 'start': start, 'end': end,
            'sign': _SIGNS[sign_index], 'sign_index': sign_index,
            'sign_lord': _SIGN_LORDS[sign_index], 'nakshatra': _STARS[ordinal],
            'star': star, 'sub': sub, 'subsub': subsub}


def _offset_text(offset: timedelta) -> str:
    seconds = int(offset.total_seconds())
    hours, tail = divmod(abs(seconds), 3600)
    minutes, sec = divmod(tail, 60)
    result = f'{"-" if seconds < 0 else "+"}{hours:02d}:{minutes:02d}'
    return f'{result}:{sec:02d}' if sec else result


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def resolve_local_time(date_str: str, time_str: str, zone: str,
                       fold: int | None = None) -> tuple[datetime, str]:
    """Return (aware UTC datetime, signed UTC offset string) for a civil time."""
    if not isinstance(date_str, str) or not _DATE_RE.fullmatch(date_str):
        raise TimeZoneError('date must be YYYY-MM-DD')
    if not isinstance(time_str, str) or not _TIME_RE.fullmatch(time_str):
        raise TimeZoneError('time must be HH:MM or HH:MM:SS (24-hour clock)')
    if fold is not None and (type(fold) is not int or fold not in (0, 1)):
        raise TimeZoneError('fold must be None, 0, or 1')
    try:
        wall = datetime.combine(date.fromisoformat(date_str), time.fromisoformat(time_str))
    except ValueError as exc:
        raise TimeZoneError(f'Invalid civil date or time: {exc}') from exc
    if not isinstance(zone, str) or not zone:
        raise TimeZoneError('zone must be an IANA name or an offset like +05:30')
    match = _OFFSET_RE.fullmatch(zone)
    try:
        if match or zone == 'UTC':
            offset = timedelta(0)
            if match:
                sign, h, m = match.groups()
                hours, minutes = int(h), int(m)
                if hours > 23 or minutes > 59:
                    raise TimeZoneError('Manual offset is outside ±23:59 or has invalid minutes')
                offset = (-1 if sign == '-' else 1) * timedelta(hours=hours, minutes=minutes)
            if fold == 1:
                raise TimeZoneError('fold=1 requires an ambiguous IANA local time')
            return wall.replace(tzinfo=timezone(offset)).astimezone(UTC), _offset_text(offset)
        if zone.startswith(('+', '-')):
            raise TimeZoneError('Manual offset must have the form ±HH:MM')
        try:
            tz = ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise TimeZoneError(f'IANA zone {zone!r} was not found; check the name and '
                                'local tzdata, or supply a manual UTC offset') from exc
        choices = {}
        for f in (0, 1):
            local = wall.replace(tzinfo=tz, fold=f)
            instant = local.astimezone(UTC)
            round_trip = instant.astimezone(tz)
            if round_trip.replace(tzinfo=None) == wall and round_trip.fold == f:
                choices[f] = instant, _offset_text(local.utcoffset())
        if not choices:
            raise TimeZoneError(f'Nonexistent local time in {zone!r} (clock moved forward)')
        if len(choices) == 2 and fold is None:
            raise TimeZoneError(f'Ambiguous local time in {zone!r}; supply fold=0 or fold=1')
        f = 0 if fold is None else fold
        if f not in choices:
            raise TimeZoneError('fold=1 requires an ambiguous IANA local time')
        return choices[f]
    except OverflowError as exc:
        raise TimeZoneError('UTC conversion falls outside the Python datetime range') from exc


def _coordinate(value, label: str, limit: int) -> float:
    try:
        if isinstance(value, bool):
            raise ValueError
        value = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise KPError(f'{label} must be a finite number in degrees') from exc
    if not math.isfinite(value) or not -limit <= value <= limit:
        raise KPError(f'{label} must be between {-limit} and {limit} degrees')
    return value


def _load_swiss():
    try:
        installed = version('pyswisseph')
        swe = import_module('swisseph')
    except (PackageNotFoundError, ImportError, OSError) as exc:
        raise EphemerisError(f'Install pyswisseph=={SWISS_VERSION} for Swiss calculations') from exc
    if installed != SWISS_VERSION or getattr(swe, 'SIDM_KRISHNAMURTI_VP291', None) != SIDEREAL_MODE_ID:
        raise EphemerisError(f'Expected pyswisseph=={SWISS_VERSION} with VP291 (ID 45); found {installed}')
    return swe


def _ephe_files() -> tuple[Path, tuple[Path, Path]]:
    root = Path(os.environ.get('KP_EPHE') or Path(__file__).resolve().with_name('ephe'))
    try:
        root = root.expanduser().resolve()
        env = os.environ.get('SE_EPHE_PATH')
        if env and Path(env).expanduser().resolve() != root:
            raise EphemerisError('SE_EPHE_PATH conflicts with the KP ephemeris directory; '
                                 'unset SE_EPHE_PATH and use KP_EPHE to select the directory')
        if len(os.fsencode(root)) > 242 or os.pathsep in str(root):
            raise EphemerisError('Swiss ephemeris directory is too long or contains a path-list separator')
        files = tuple(root / name for name in ('sepl_18.se1', 'semo_18.se1'))
        missing = [p.name for p in files if not p.is_file()]
        if missing:
            raise EphemerisError(f'Missing Swiss files in {root}: {", ".join(missing)}')
        return root, files
    except (OSError, RuntimeError) as exc:
        raise EphemerisError(f'Cannot access Swiss ephemeris directory {root}: {exc}') from exc


def _planet_positions(swe, jd: float, files: tuple[Path, Path]):
    flags = swe.FLG_SWIEPH | swe.FLG_SIDEREAL | swe.FLG_SPEED
    ephe_mask = swe.FLG_SWIEPH | swe.FLG_MOSEPH | swe.FLG_JPLEPH
    positions = []
    for name, body in _BODIES:
        values, used = swe.calc_ut(jd, getattr(swe, body), flags)
        if (used & ephe_mask) != swe.FLG_SWIEPH:
            raise EphemerisError(f'{name}: required SWIEPH, but Swiss returned flags {used:#x}; '
                                 'fallback is disabled')
        if used & flags != flags or not all(math.isfinite(values[i]) for i in (0, 3)):
            raise EphemerisError(f'{name}: invalid sidereal longitude or speed from Swiss')
        positions.append((name, _wrap(values[0]), values[3] < 0.0))
    for i, expected in enumerate(files):
        data = swe.get_current_file_data(i)
        if (not data or not data[0] or Path(os.fsdecode(data[0])).resolve() != expected.resolve()
                or not data[1] <= jd <= data[2]):
            raise EphemerisError(f'Swiss did not use the required file for this date: {expected}')
    _, rahu, retro = positions[-1]
    positions.append(('Ketu', _wrap(rahu + 180.0), retro))
    return positions


def _placidus(swe, jd: float, lat: float, lon: float, target: float | None):
    f = swe.FLG_SWIEPH
    (eps_true, *_), _ = swe.calc_ut(jd, swe.ECL_NUT, f)
    if not math.isfinite(eps_true) or abs(lat) >= 90.0 - eps_true:
        raise HouseError('Placidus is undefined at or beyond the polar circle; no fallback')
    try:
        sid, sid_angles, *_ = swe.houses_ex2(jd, lat, lon, b'P', f | swe.FLG_SIDEREAL)
        if len(sid) != 12 or not all(map(math.isfinite, (*sid, *sid_angles[:3]))):
            raise HouseError('Invalid Swiss sidereal Placidus geometry')
        trop, trop_angles, *_ = swe.houses_armc_ex2(sid_angles[2], lat, eps_true, b'P')
        offset = _wrap(trop_angles[0] - sid_angles[0])
        if (_gap(sid[0], sid_angles[0]) > _TOL or _gap(trop[0], trop_angles[0]) > _TOL
                or any(_gap(t - offset, s) > _TOL for t, s in zip(trop, sid))):
            raise HouseError('Swiss house frames disagree on the sidereal cusp shift')
        actual_asc = _wrap(sid[0])
        if target is None:
            return tuple(map(_wrap, sid)), actual_asc, offset
        lam, eps, phi = map(math.radians, (target + offset, eps_true, lat))
        ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
        dec = math.asin(math.sin(eps) * math.sin(lam))
        cos_h = -math.tan(phi) * math.tan(dec)
        if not -1.0 <= cos_h <= 1.0:
            raise HouseError('Horary target has no rising horizon solution at this latitude')
        armc = _wrap(math.degrees(ra - math.acos(cos_h)))
        anchored, angles, *_ = swe.houses_armc_ex2(armc, lat, eps_true, b'P')
        if (len(anchored) != 12 or not all(map(math.isfinite, (*anchored, angles[0])))
                or _gap(angles[0] - offset, target) > _TOL
                or _gap(anchored[0] - offset, target) > _TOL
                or _gap(anchored[6] - offset, target + 180.0) > _TOL):
            raise HouseError('ARMC inversion did not reproduce the requested horary ascendant')
        cusps = [_wrap(c - offset) for c in anchored]
        cusps[0], cusps[6] = target, _wrap(target + 180.0)
        return tuple(cusps), actual_asc, offset
    except swe.Error as exc:
        raise HouseError(f'Swiss Placidus calculation failed (no fallback): {exc}') from exc


def _house_edges(cusps: tuple[float, ...]) -> tuple[float, ...]:
    edges = tuple(_wrap(c - cusps[0]) for c in cusps)
    if not all(a < b for a, b in zip(edges, edges[1:] + (360.0,))):
        raise HouseError('Placidus cusps do not form twelve ordered zodiac intervals')
    return edges


def _deposit_house(name: str, lon: float, cusps: tuple[float, ...],
                   edges: tuple[float, ...]) -> int:
    """Assign cusp deposit using Sun 6°40′ and other planets 3°20′ orb.

    Aries–Virgo (sign_index 0..5) evaluates cusps up-to-down (1 -> 12);
    Libra–Pisces (sign_index 6..11) evaluates cusps down-to-up (12 -> 1).
    """
    base_house = bisect_right(edges, _wrap(lon - cusps[0]))
    orb = SUN_CUSP_ORB if name == 'Sun' else PLANET_CUSP_ORB
    sign_index = bisect_right(_SIGN_STARTS, lon) - 1
    cusp_order = range(12) if sign_index <= 5 else range(11, -1, -1)
    for idx in cusp_order:
        if _gap(lon, cusps[idx]) <= orb + 1e-10:
            return idx + 1
    return base_house


def _row(name: str, lon: float, retro: bool, house: int) -> dict:
    si = bisect_right(_SIGN_STARTS, lon) - 1
    ordinal, star, sub, subsub = _stellar(lon)
    arcsec = min(_SIGN_AS - 1, round((lon - _SIGN_STARTS[si]) * _ARCSEC_PER_DEG))
    degrees, tail = divmod(arcsec, _ARCSEC_PER_DEG)
    minutes, seconds = divmod(tail, 60)
    return {'name': name, 'longitude': lon, 'sign': _SIGNS[si],
            'degree': f'{degrees:02d}°{minutes:02d}′{seconds:02d}″',
            'sign_lord': _SIGN_LORDS[si], 'nakshatra': _STARS[ordinal],
            'star': star, 'sub': sub, 'subsub': subsub, 'retro': retro,
            'house': house, 'sign_index': si}


def _period(origin: datetime, lord: str, lo: int, hi: int, levels: int) -> dict:
    children = []
    if levels:
        old, weight = lo, 0
        for child in _cycle(lord):
            weight += _YEARS[child]
            new = lo + round(Fraction((hi - lo) * weight, 120))
            children.append(_period(origin, child, old, new, levels - 1))
            old = new
    return {'lord': lord, 'start': _iso_utc(origin + timedelta(microseconds=lo)),
            'end': _iso_utc(origin + timedelta(microseconds=hi)), 'children': children}


def vimshottari_periods(utc: datetime, moon_longitude: float) -> list[dict]:
    """Return nine Mahadashas from the Moon, with Antardasha and Pratyantar."""
    if not isinstance(utc, datetime) or utc.utcoffset() is None:
        raise KPError('Vimshottari requires a timezone-aware reference datetime')
    origin = utc.astimezone(UTC)
    if not MIN_YEAR - 1 <= origin.year <= MAX_YEAR + 1:
        raise KPError('Vimshottari reference year is outside supported chart coverage')
    if (isinstance(moon_longitude, bool) or not isinstance(moon_longitude, (float, int))
            or not 0.0 <= moon_longitude < 360.0):
        raise KPError('Moon longitude must be finite and in [0, 360) degrees')
    ordinal = bisect_right(_STAR_STARTS, moon_longitude) - 1
    first = _LORDS[ordinal % 9]
    fraction = Fraction(0)
    if moon_longitude != _STAR_STARTS[ordinal]:
        delta = Fraction(moon_longitude) * _ARCSEC_PER_DEG - ordinal * _STAR_AS
        fraction = min(Fraction(1), max(Fraction(0), delta / _STAR_AS))
    duration = _YEAR_US * _YEARS[first]
    lo = -min(duration - 1, round(fraction * duration))
    periods = []
    for lord in _cycle(first):
        hi = lo + _YEAR_US * _YEARS[lord]
        periods.append(_period(origin, lord, lo, hi, 2))
        lo = hi
    return periods


def _significators(planets: list[dict], cusps: list[dict]) -> dict:
    by_name = {p['name']: p for p in planets}
    owned = {name: [c['house'] for c in cusps if c['sign_lord'] == name] for name in by_name}
    return {p['name']: {'A_star_lord_occupies': [by_name[p['star']]['house']],
                        'B_occupies': [p['house']],
                        'C_star_lord_owns': owned[p['star']][:],
                        'D_owns': owned[p['name']][:]} for p in planets}


def calculate(record: Mapping) -> dict:
    """Calculate an offline KP chart from the application's record mapping."""
    if not isinstance(record, Mapping):
        raise KPError('record must be a mapping')
    for key in ('date', 'time', 'zone', 'lat', 'lon'):
        if key not in record:
            raise KPError(f'Missing required record field: {key}')
    utc, offset = resolve_local_time(record['date'], record['time'], record['zone'], record.get('fold'))
    civil_date = date.fromisoformat(record['date'])
    if not MIN_YEAR <= civil_date.year <= MAX_YEAR:
        raise KPError(f'Chart year must be in {MIN_YEAR}–{MAX_YEAR} inclusive (local date)')
    lat = _coordinate(record['lat'], 'lat', 90)
    lon = _coordinate(record['lon'], 'lon', 180)
    mode = record.get('mode', 'Birth')
    if mode not in ('Birth', 'Horary'):
        raise KPError("mode must be 'Birth' or 'Horary'")
    segment = horary_segment(record.get('horary')) if mode == 'Horary' else None
    swe = _load_swiss()
    root, files = _ephe_files()
    with _SWISS_LOCK:
        try:
            swe.set_ephe_path(str(root))
            swe.set_sid_mode(SIDEREAL_MODE_ID)
            hours = (utc.hour * 3600 + utc.minute * 60 + utc.second) / 3600
            jd = swe.julday(utc.year, utc.month, utc.day, hours, swe.GREG_CAL)
            positions = _planet_positions(swe, jd, files)
            _, ayanamsa = swe.get_ayanamsa_ex_ut(jd, swe.FLG_SWIEPH | swe.FLG_NONUT)
            if not math.isfinite(ayanamsa):
                raise EphemerisError('Swiss returned a nonfinite ayanamsa')
            cusps, actual_asc, house_ayanamsa = _placidus(
                swe, jd, lat, lon, None if segment is None else segment['start'])
            outer = []
            flags = swe.FLG_SWIEPH | swe.FLG_SIDEREAL | swe.FLG_SPEED
            ephe_mask = swe.FLG_SWIEPH | swe.FLG_MOSEPH | swe.FLG_JPLEPH
            for name in ('Uranus', 'Neptune', 'Pluto'):
                vals, used = swe.calc_ut(jd, getattr(swe, name.upper()), flags)
                if (used & flags != flags or used & ephe_mask != swe.FLG_SWIEPH or
                        not all(math.isfinite(vals[i]) for i in (0, 3))):
                    raise EphemerisError(f'{name}: invalid Swiss sidereal longitude or speed')
                outer.append((name, _wrap(vals[0]), vals[3] < 0))
        except (swe.Error, OSError, UnicodeError) as exc:
            raise EphemerisError(f'Swiss calculation failed (no fallback): {exc}') from exc
        finally:
            swe.close()
    edges = _house_edges(cusps)
    planets = [_row(name, value, retro, _deposit_house(name, value, cusps, edges))
               for name, value, retro in positions]
    cusp_rows = [_row(f'Cusp {i}', value, False, i) for i, value in enumerate(cusps, 1)]
    fortune = _wrap(cusps[0] + planets[1]['longitude'] - planets[0]['longitude'])
    extras = [_row(name, value, retro, _deposit_house(name, value, cusps, edges))
              for name, value, retro in outer + [('Fortune', fortune, False)]]
    rashi, bhava = ([[] for _ in range(12)] for _ in range(2))
    for p in planets:
        rashi[p['sign_index']].append(p['name'])
        bhava[p['house'] - 1].append(p['name'])
    asc = _row('Actual ASC', actual_asc, False, 1)
    moon = planets[1]
    ruling = [('Actual-time ASC sign lord', asc['sign_lord']),
              ('Actual-time ASC star lord', asc['star']),
              ('Actual-time ASC sub lord', asc['sub']),
              ('Moon sign lord', moon['sign_lord']), ('Moon star lord', moon['star']),
              ('Local civil weekday lord', _WEEKDAY_LORDS[civil_date.weekday()])]
    return {'planets': planets, 'cusps': cusp_rows, 'extras': extras, 'ayanamsa': ayanamsa,
            'house_ayanamsa': house_ayanamsa, 'utc': _iso_utc(utc), 'offset': offset,
            'rising_sign': cusp_rows[0]['sign_index'], 'ruling': ruling,
            'dasha': vimshottari_periods(utc, moon['longitude']), 'rashi': rashi, 'bhava': bhava,
            'significators': _significators(planets, cusp_rows), 'horary_segment': segment}