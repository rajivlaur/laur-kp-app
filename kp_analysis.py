# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pure KP display analysis. No ephemeris, storage or UI dependencies."""
from math import floor, isfinite

LEVELS = ('A_star_lord_occupies', 'B_occupies', 'C_star_lord_owns', 'D_owns')
GROUPS = ('Star Lord', 'Sub Lord', 'Conjunction', 'Opposition', 'Self')

ASPECTS = (
    (0, 'Conjunction'),
    (30, 'Semi-sextile'),
    (45, 'Semi-square'),
    (60, 'Sextile'),
    (90, 'Square'),
    (120, 'Trine'),
    (135, 'Sesquisquare'),
    (145, '145° Aspect'),
    (180, 'Opposition'),
)

ASPECT_EFFECTS = {
    0: 'Most Malefic',
    30: 'Benefic',
    45: 'Very Less Effect',
    60: 'Benefic',
    90: 'Most Malefic',
    120: 'Benefic',
    135: 'Very Less Effect',
    145: 'Very Less Effect',
    180: 'Most Malefic',
}

EFFECT_CATEGORIES = ('Benefic', 'Most Malefic', 'Very Less Effect')

PLANET_ORB = 3 + 20 / 60   # 3°20′
SUN_ORB = 6 + 40 / 60      # 6°40′
DEFAULT_ORB = PLANET_ORB

SOUTH = ((0, 1), (0, 2), (0, 3), (1, 3), (2, 3), (3, 3),
         (3, 2), (3, 1), (3, 0), (2, 0), (1, 0), (0, 0))


def angular_distance(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def pada(position):
    # Nakshatra quarters are 12,000 arcseconds (108 equal parts per zodiac).
    return floor(position['longitude'] * 3600 / 12000) % 4 + 1


def full_planet_houses(name, by_name, owns):
    """Return full 4-level KP house significations (A, B, C, D) and combined houses."""
    p = by_name[name]
    star = p['star']
    a = [by_name[star]['house']]
    b = [p['house']]
    c = sorted(owns[star])
    d = sorted(owns[name])
    combined = sorted(set(a + b + c + d))
    return {'A': a, 'B': b, 'C': c, 'D': d, 'all': combined}


def planet_analysis(chart, orb=None):
    """Analyze sources, aspects, aspect effects, and conj/opp significations.

    Uses Sun 6°40′ and other planets 3°20′. For a pair involving the Sun,
    the Sun's orb governs that pair unless an explicit override is passed.
    """
    if orb is not None and (not isfinite(orb) or not 0 <= orb <= 10):
        raise ValueError('Enter an aspect orb from 0° to 10°.')
    planets = chart['planets']
    by_name = {p['name']: p for p in planets}
    owns = {n: [] for n in by_name}
    for c in chart['cusps']:
        owns[c['sign_lord']].append(c['house'])

    aspects = {n: {angle: [] for angle, _ in ASPECTS} for n in by_name}
    aspect_effects = {n: {cat: [] for cat in EFFECT_CATEGORIES} for n in by_name}

    for i, p in enumerate(planets):
        for q in planets[i + 1:]:
            gap = angular_distance(p['longitude'], q['longitude'])
            angle = min(aspects[p['name']], key=lambda a: abs(gap - a))
            miss = abs(gap - angle)
            pair_orb = orb if orb is not None else max(
                SUN_ORB if p['name'] == 'Sun' else PLANET_ORB,
                SUN_ORB if q['name'] == 'Sun' else PLANET_ORB,
            )
            if miss <= pair_orb + 1e-10:
                aspects[p['name']][angle].append((q['name'], miss))
                aspects[q['name']][angle].append((p['name'], miss))
                effect = ASPECT_EFFECTS[angle]
                aspect_effects[p['name']][effect].append((q['name'], angle, miss))
                aspect_effects[q['name']][effect].append((p['name'], angle, miss))

    groups = {}
    conj_opp_sigs = {}
    for name, p in by_name.items():
        conj_names = tuple(n for n, _ in aspects[name][0])
        opp_names = tuple(n for n, _ in aspects[name][180])
        sources = ((p['star'],), (p['sub'],), conj_names, opp_names, (name,))
        groups[name] = tuple(
            (
                names,
                sorted({by_name[n]['house'] for n in names}),
                sorted({h for n in names for h in owns[n]}),
            )
            for names in sources
        )
        conj_details = {n: full_planet_houses(n, by_name, owns) for n in conj_names}
        opp_details = {n: full_planet_houses(n, by_name, owns) for n in opp_names}
        conj_opp_sigs[name] = {
            'conj_planets': conj_details,
            'conj_all': sorted({h for d in conj_details.values() for h in d['all']}),
            'opp_planets': opp_details,
            'opp_all': sorted({h for d in opp_details.values() for h in d['all']}),
        }

    return owns, aspects, groups, aspect_effects, conj_opp_sigs


def l1_l4_strength(chart, orb=None):
    """Compute L1, L2, L3, L4 strength table for every planet.

    Rules:
      L1: Placement (occupied house) of Star Lord, Sub Lord, Conjoined Lord(s), Opposed Lord(s)
      L2: Placement (occupied house) of the Planet itself
      L3: Ownership (cusp signs owned) of Star Lord, Sub Lord, Conjoined Lord(s), Opposed Lord(s)
      L4: Ownership (cusp signs owned) of the Planet itself
    """
    owns, aspects, _, _, _ = planet_analysis(chart, orb=orb)
    planets = chart['planets']
    by_name = {p['name']: p for p in planets}
    table = {}
    for p in planets:
        name = p['name']
        star = p['star']
        sub = p['sub']
        conjs = [n for n, _ in aspects[name][0]]
        opps = [n for n, _ in aspects[name][180]]

        # Ordered unique external lords: Star, Sub, Conjoined, Opposed
        ext_lords = []
        ext_roles = {}
        for role, lords in (('Star', [star]), ('Sub', [sub]), ('Conj', conjs), ('Opp', opps)):
            for lord in lords:
                if lord not in ext_roles:
                    ext_lords.append(lord)
                    ext_roles[lord] = []
                ext_roles[lord].append(role)

        l1_houses = sorted({by_name[lord]['house'] for lord in ext_lords})
        l2_houses = [p['house']]
        l3_houses = sorted({h for lord in ext_lords for h in owns[lord]})
        l4_houses = sorted(owns[name])
        all_houses = sorted(set(l1_houses + l2_houses + l3_houses + l4_houses))

        # Track contributing sources per house per level for clear house-filter explanation
        house_reasons = {h: {'L1': [], 'L2': [], 'L3': [], 'L4': []} for h in range(1, 13)}
        for lord in ext_lords:
            role_tag = '/'.join(ext_roles[lord])
            occ_h = by_name[lord]['house']
            house_reasons[occ_h]['L1'].append(f'{lord} ({role_tag})')
            for own_h in owns[lord]:
                house_reasons[own_h]['L3'].append(f'{lord} ({role_tag})')
        house_reasons[p['house']]['L2'].append(f'{name} (Self)')
        for own_h in l4_houses:
            house_reasons[own_h]['L4'].append(f'{name} (Self)')

        table[name] = {
            'L1': l1_houses,
            'L2': l2_houses,
            'L3': l3_houses,
            'L4': l4_houses,
            'all': all_houses,
            'star': star,
            'sub': sub,
            'conj': conjs,
            'opp': opps,
            'house_reasons': house_reasons,
        }
    return table


def abcd_rows(chart, by_house=False):
    sig = chart['significators']
    if not by_house:
        return [(name, *(row[key] for key in LEVELS)) for name, row in sig.items()]
    return [(house, *([name for name, row in sig.items() if house in row[key]] for key in LEVELS))
            for house in range(1, 13)]