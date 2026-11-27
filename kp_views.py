# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only chart workspace tabs for KP Studio."""
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timezone

from kp_analysis import (
    ASPECTS,
    ASPECT_EFFECTS,
    EFFECT_CATEGORIES,
    GROUPS,
    SOUTH,
    abcd_rows,
    l1_l4_strength,
    pada,
    planet_analysis,
)

BG, WHITE, TEXT, MUTED = '#eeeeee', '#ffffff', '#26394a', '#556574'
PURPLE, TEAL = '#8c268b', '#007f91'
FONT = 'Segoe UI'
SIGNS = ('Aries', 'Taurus', 'Gemini', 'Cancer', 'Leo', 'Virgo', 'Libra', 'Scorpio',
         'Sagittarius', 'Capricorn', 'Aquarius', 'Pisces')
SHORT = dict(zip(('Sun', 'Moon', 'Mars', 'Mercury', 'Jupiter', 'Venus', 'Saturn', 'Rahu', 'Ketu',
                  'Uranus', 'Neptune', 'Pluto', 'Fortune'),
                 ('Su', 'Mo', 'Ma', 'Me', 'Ju', 'Ve', 'Sa', 'Ra', 'Ke', 'Ur', 'Ne', 'Pl', 'Fo')))
ROMAN = ('I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI', 'XII')


def joined(seq):
    return ', '.join(map(str, seq)) or '—'


def fill(tree, rows, highlight=None):
    tree.delete(*tree.get_children())
    for i, row in enumerate(rows):
        tags = (('match',) if highlight and highlight(row) else ('alt',) if i % 2 else ())
        tree.insert('', 'end', iid=str(i), values=row, tags=tags)


def selector(parent, choices, width):
    box = ttk.Combobox(parent, values=choices, state='readonly', width=width)
    box.current(0)
    return box


class ChartTabs:
    def __init__(self, app, tabs, rec, chart):
        self.app, self.chart, self.rec = app, chart, rec
        (
            self.own,
            self.aspects,
            self.groups,
            self.aspect_effects,
            self.conj_opp_sigs,
        ) = planet_analysis(chart)
        self.l1_l4 = l1_l4_strength(chart)
        self.by_name = {p['name']: p for p in chart['planets']}
        self.houses = chart['cusps']
        self.extras = chart.get('extras', [])
        self.objs = chart['planets'] + self.extras
        for title, method in (
            ('KP Overview', self.overview),
            ('Houses, Planets & Aspects', self.positions),
            ('Planet Significations', self.significations),
            ('L1–L4 Strength Table', self.l1_l4_tab),
        ):
            pane = tk.Frame(tabs, bg=BG)
            tabs.add(pane, text=title, padding=5)
            method(pane)

    def note(self, parent, text, **kwargs):
        label = self.app.label(parent, text, fg=MUTED, size=9, padx=8, pady=3, **kwargs)
        label.pack(fill='x')
        return label

    def overview(self, pane):
        pane.rowconfigure(0, weight=1)
        for col, wt, minimum in ((0, 5, 330), (1, 4, 295), (2, 3, 245)):
            pane.columnconfigure(col, weight=wt, minsize=minimum)

        # Left column: South Indian Chart + Planet Description below it
        left_col = tk.Frame(pane, bg=BG)
        left_col.grid(row=0, column=0, sticky='nsew', padx=(0, 6))
        left_col.rowconfigure(0, weight=3)
        left_col.rowconfigure(1, weight=2)
        left_col.columnconfigure(0, weight=1)

        kp = self.app.panel(left_col, 'KP Chart  ·  South Indian')
        kp.grid(row=0, column=0, sticky='nsew', pady=(0, 5))
        canvas = tk.Canvas(kp, bg=WHITE, width=320, height=250, highlightthickness=0)
        canvas.pack(fill='both', expand=True)
        canvas.bind('<Configure>', lambda _e: self.draw_south(canvas))
        canvas.bind('<Button-1>', lambda e: self.open_sign(canvas, e.x, e.y))
        self.note(kp, 'Click a sign to see all of its entries.')

        pdesc = self.app.panel(left_col, 'Planet Description')
        pdesc.grid(row=1, column=0, sticky='nsew')
        pcols = [
            ('p', 'Planet', 68),
            ('sign', 'Sign · Deg', 108),
            ('nak', 'Star (Pada)', 106),
            ('lords', 'Sgl / Stl / Sbl / Ssl', 120),
            ('occ', 'Occ', 38),
            ('own', 'Owns', 55),
        ]
        ptable = self.app.table(pdesc, pcols, 6, stretch=False)
        ptable.master.pack(fill='both', expand=True)
        fill(
            ptable,
            [
                (
                    r['name'] + (' ℞' if r.get('retro') and r['name'] not in ('Rahu', 'Ketu') else ''),
                    f"{r['sign'][:3]} {r['degree']}",
                    f"{r['nakshatra']} ({pada(r)})",
                    f"{SHORT[r['sign_lord']]}/{SHORT[r['star']]}/{SHORT[r['sub']]}/{SHORT[r['subsub']]}",
                    r['house'],
                    joined(self.own.get(r['name'], ())),
                )
                for r in self.chart['planets']
            ],
        )

        # Center column: ABCD Significators
        panel = self.app.panel(pane, 'ABCD Significators')
        panel.grid(row=0, column=1, sticky='nsew', padx=(0, 6))
        mode = selector(panel, ('Planets', 'Houses'), 13)
        mode.pack(anchor='e', padx=8, pady=5)
        table = self.app.table(panel, [('what', 'Planet', 63)] + [(k, k, 48) for k in 'ABCD'], 9)
        table.master.pack(fill='both', expand=True)
        self.note(
            panel,
            'A  Star lord occupies\nB  Planet occupies\nC  Star lord owns\nD  Planet owns',
            justify='left',
            anchor='w',
        )

        def refresh(_event=None):
            house = mode.current() == 1
            table.heading('what', text='House' if house else 'Planet')
            fill(
                table,
                [
                    (n, *(joined(SHORT.get(x, x) for x in values) for values in cells))
                    for n, *cells in abcd_rows(self.chart, house)
                ],
            )

        mode.bind('<<ComboboxSelected>>', refresh)
        refresh()

        # Right column: Vimshottari Dasha with Full-View / Minimize toggle icon
        dp = self.app.new_card(pane)
        dp.grid(row=0, column=2, sticky='nsew')
        dheader = tk.Frame(dp, bg='#f2e5bf')
        dheader.pack(fill='x')
        self.app.label(dheader, 'Vimshottari Dasha', fg=TEXT, bg='#f2e5bf', size=10, bold=True, padx=8, pady=6).pack(side='left')

        dasha_expanded = {'state': False}

        def toggle_dasha_full():
            if not dasha_expanded['state']:
                left_col.grid_remove()
                panel.grid_remove()
                dp.grid(row=0, column=0, columnspan=3, sticky='nsew')
                toggle_btn.configure(text='🗗 Minimize')
                dasha_expanded['state'] = True
            else:
                dp.grid(row=0, column=2, columnspan=1, sticky='nsew')
                left_col.grid()
                panel.grid()
                toggle_btn.configure(text='⛶ Full View')
                dasha_expanded['state'] = False

        toggle_btn = tk.Button(
            dheader,
            text='⛶ Full View',
            command=toggle_dasha_full,
            bg='#e6d4a3',
            fg=TEXT,
            activebackground=TEAL,
            activeforeground=WHITE,
            relief='flat',
            bd=0,
            padx=8,
            pady=2,
            cursor='hand2',
            font=(FONT, 9, 'bold'),
        )
        toggle_btn.pack(side='right', padx=6, pady=4)

        self.note(dp, 'Expand each period for subperiods. Click ⛶ Full View to maximize.')
        df = tk.Frame(dp, bg=WHITE)
        df.pack(fill='both', expand=True)
        df.columnconfigure(0, weight=1)
        df.rowconfigure(0, weight=1)
        tree = ttk.Treeview(df, show='tree', selectmode='browse')
        tree.column('#0', width=257, minwidth=235, stretch=True)
        sy = ttk.Scrollbar(df, orient='vertical', command=tree.yview)
        sx = ttk.Scrollbar(df, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        tree.grid(row=0, column=0, sticky='nsew')
        sy.grid(row=0, column=1, sticky='ns')
        sx.grid(row=1, column=0, sticky='ew')
        tree.tag_configure('current', background='#e1f3e7', foreground='#235943')
        now = datetime.now(timezone.utc)

        def periods(rows, parent=''):
            for p in rows:
                start = datetime.fromisoformat(p['start'].replace('Z', '+00:00'))
                end = datetime.fromisoformat(p['end'].replace('Z', '+00:00'))
                item = tree.insert(
                    parent,
                    'end',
                    text=f"{p['lord']}  ·  {start.date()} → {end.date()}",
                    tags=('current',) if start <= now < end else (),
                )
                periods(p['children'], item)

        periods(self.chart['dasha'])
        self.note(dp, 'UTC dates · Green = current period')

    def sign_entries(self, index):
        return sorted(
            [r for r in self.houses + self.objs if r['sign_index'] == index],
            key=lambda r: r['longitude'],
        )

    def draw_south(self, c):
        c.delete('all')
        size = min(c.winfo_width() - 14, c.winfo_height() - 14)
        if size < 100:
            return
        x0, y0 = (c.winfo_width() - size) / 2, (c.winfo_height() - size) / 2
        w = size / 4
        c._south_box = (x0, y0, w)
        fs = 8 if w < 115 else 9
        step = 15 if w < 115 else 18
        for si, (r, col) in enumerate(SOUTH):
            x, y = x0 + col * w, y0 + r * w
            c.create_rectangle(x, y, x + w, y + w, outline='#425668')
            c.create_text(x + 4, y + 4, text=SIGNS[si], anchor='nw', fill=MUTED, font=(FONT, fs, 'bold'))
            rows = self.sign_entries(si)
            capacity = max(2, int((w - 25) // step) + 1)
            visible = rows if len(rows) <= capacity else rows[: capacity - 1]
            for i, p in enumerate(visible):
                yy = y + 20 + i * step
                cusp = p['name'].startswith('Cusp ')
                token = ROMAN[p['house'] - 1] if cusp else SHORT[p['name']]
                retro = '℞' if p.get('retro') and p['name'] not in ('Rahu', 'Ketu') else ''
                c.create_text(
                    x + 4,
                    yy,
                    text=f"{token}{retro}  {p['degree']}",
                    anchor='nw',
                    fill=TEXT if cusp else PURPLE,
                    font=(FONT, fs),
                )
                if w >= 117:
                    c.create_text(
                        x + w - 4,
                        yy,
                        text=f"{SHORT[p['star']]}·{SHORT[p['sub']]}",
                        anchor='ne',
                        fill=MUTED,
                        font=(FONT, fs - 1),
                    )
            if len(rows) > len(visible):
                c.create_text(
                    x + 4,
                    y + 20 + len(visible) * step,
                    text=f'+{len(rows) - len(visible)} more',
                    anchor='nw',
                    fill=TEAL,
                    font=(FONT, fs),
                )
        center = ((x0 + 2 * w), (y0 + 2 * w))
        rec = self.rec
        mode = rec['mode'] + (f" #{rec['horary']}" if rec['mode'] == 'Horary' else '')
        lines = (rec['title'], f'{mode}  ·  KP NEW', f"{rec['date']}  {rec['time']}", rec['place'])
        for i, line in enumerate(lines):
            limit = max(16, int((2 * w - 18) / 6.4))
            if len(line) > limit:
                line = line[: limit - 1] + '…'
            c.create_text(
                center[0],
                center[1] + (i - 1.5) * 17,
                text=line,
                font=(FONT, fs, 'bold' if i == 0 else 'normal'),
                fill=TEAL if i == 1 else TEXT,
            )

    def open_sign(self, canvas, px, py):
        if not hasattr(canvas, '_south_box'):
            return
        x, y, w = canvas._south_box
        cell = (int((py - y) // w), int((px - x) // w))
        if cell not in SOUTH:
            return
        si = SOUTH.index(cell)
        dialog = self.app.dialog(f'{SIGNS[si]} · KP Chart', 695, 350)
        cols = [
            ('n', 'Body or cusp', 100),
            ('d', 'Position', 85),
            ('s', 'Sign lord', 90),
            ('t', 'Star lord', 90),
            ('u', 'Sub lord', 90),
            ('b', 'Sub-sub lord', 105),
        ]
        table = self.app.table(dialog, cols, 9)
        table.master.pack(fill='both', expand=True, padx=7, pady=7)
        fill(
            table,
            [
                (r['name'], r['degree'], r['sign_lord'], r['star'], r['sub'], r['subsub'])
                for r in self.sign_entries(si)
            ],
        )

    def positions(self, pane):
        panel = self.app.panel(pane, 'Houses & Planets')
        panel.pack(fill='both', expand=True, pady=(0, 5))
        bar = tk.Frame(panel, bg=WHITE)
        bar.pack(fill='x', padx=6, pady=4)
        self.app.label(
            bar,
            'Cusp Orb Rule: Sun 6°40′ · Other Planets 3°20′ (Aries–Virgo: up→down · Libra–Pisces: down→up)',
            fg=MUTED,
            size=9,
        ).pack(side='left', padx=3)
        mode = selector(bar, ('Planets', 'Houses'), 13)
        mode.pack(side='right')
        cols = [
            ('name', 'Planet', 88),
            ('sign', 'Sign', 92),
            ('position', 'Position', 85),
            ('star', 'Nakshatra · Pada', 136),
            ('sl', 'Sign Lord', 92),
            ('st', 'Star Lord', 92),
            ('sub', 'Sub Lord', 92),
            ('ss', 'Sub-Sub Lord', 96),
            ('occ', 'Occupies', 70),
            ('own', 'Owns', 85),
        ]
        t = self.app.table(panel, cols, 5)
        t.master.pack(fill='both', expand=True)

        def refresh(_event=None):
            is_house = mode.current() == 1
            t.heading('name', text='Cusp' if is_house else 'Planet / Point')
            t.heading('occ', text='Occupants' if is_house else 'Occupies')
            t.heading('own', text='Cusp Ruler' if is_house else 'Owns')
            fill(
                t,
                [
                    (
                        r['name'] + (' ℞' if r['retro'] and r['name'] not in ('Rahu', 'Ketu') else ''),
                        r['sign'],
                        r['degree'],
                        f"{r['nakshatra']} · {pada(r)}",
                        r['sign_lord'],
                        r['star'],
                        r['sub'],
                        r['subsub'],
                        joined(SHORT[p['name']] for p in self.chart['planets'] if p['house'] == r['house'])
                        if is_house
                        else r['house'],
                        r['sign_lord'] if is_house else joined(self.own.get(r['name'], ())),
                    )
                    for r in (self.houses if is_house else self.objs)
                ],
            )

        mode.bind('<<ComboboxSelected>>', refresh)
        refresh()

        ap = self.app.panel(pane, 'Planetary Aspects & Effect Classification')
        ap.pack(fill='both', expand=True)
        bar = tk.Frame(ap, bg=WHITE)
        bar.pack(fill='x', padx=6, pady=4)
        self.app.label(
            bar,
            'Orb: Sun 6°40′ · Others 3°20′   |   Benefic: 30°, 60°, 120°  ·  '
            'Most Malefic: 0°, 90°, 180°  ·  Very Less Effect: 45°, 135°, 145°',
            fg=MUTED,
            size=9,
        ).pack(side='left', padx=3)

        short_tag = {'Benefic': 'Ben', 'Most Malefic': 'Mal', 'Very Less Effect': 'Less'}
        names = ('Conj.', 'Semisext.', 'Semisq.', 'Sextile', 'Square', 'Trine', 'Sesquisq.', '145°', 'Opp.')
        aspect_cols = [('n', 'Planet', 76)] + [
            (str(a), f'{name} {a}° [{short_tag[ASPECT_EFFECTS[a]]}]', 102)
            for (a, _), name in zip(ASPECTS, names)
        ]
        self.at = self.app.table(ap, aspect_cols, 5, stretch=False)
        self.at.master.pack(fill='both', expand=True)

        effect_cols = [
            ('p', 'Planet', 84),
            ('ben', 'Benefic Aspects (30°, 60°, 120°)', 270),
            ('mal', 'Most Malefic Aspects (0°, 90°, 180°)', 270),
            ('less', 'Very Less Effect Aspects (45°, 135°, 145°)', 270),
        ]
        self.et = self.app.table(ap, effect_cols, 4, stretch=True)
        self.et.master.pack(fill='both', expand=True, pady=(4, 0))
        self.refresh_aspects()

    def refresh_aspects(self):
        fill(
            self.at,
            [
                (p, *(joined(f'{SHORT[n]} ({miss:.2f}°)' for n, miss in row[a]) for a, _ in ASPECTS))
                for p, row in self.aspects.items()
            ],
        )
        fill(
            self.et,
            [
                (
                    p,
                    *(
                        joined(f'{SHORT[n]} {ang}° ({miss:.2f}°)' for n, ang, miss in self.aspect_effects[p][cat])
                        for cat in EFFECT_CATEGORIES
                    ),
                )
                for p in self.aspects
            ],
        )

    def significations(self, pane):
        panel = self.app.panel(pane, 'Planet Signification and Strength Table')
        panel.pack(fill='both', expand=True)
        bar = tk.Frame(panel, bg=WHITE)
        bar.pack(fill='x', padx=6, pady=5)
        self.app.label(bar, 'House filter:', size=9).pack(side='left', padx=5)
        self.house = selector(bar, ('All houses', *(str(i) for i in range(1, 13))), 13)
        self.house.pack(side='left')
        self.summary = self.app.label(bar, '', fg=MUTED, size=9)
        self.summary.pack(side='right', padx=7)
        self.note(
            panel,
            'Dep = occupied house · Own = owned houses · Conj. All / Opp. All = full KP houses (A–D) of conjoined/opposing planets.',
            anchor='w',
        )
        cols = (
            [('p', 'Planet', 82)]
            + [
                (str(i) + k, f'{label} {k}', 68)
                for i, label in enumerate(('Star', 'Sub', 'Conj.', 'Opp.', 'Self'))
                for k in ('Dep', 'Own')
            ]
            + [('call', 'Conj. All (A–D)', 112), ('oall', 'Opp. All (A–D)', 112)]
        )
        self.sg = self.app.table(panel, cols, 8, stretch=False)
        self.sg.master.pack(fill='both', expand=True)

        detail = self.app.panel(pane, 'Source Planets, Conjoined & Opposing Planet Significations (Selected Planet)')
        detail.pack(fill='x', pady=(5, 0))
        self.sources = self.app.table(
            detail,
            [
                ('g', 'Relation / Group', 180),
                ('n', 'Planet(s)', 160),
                ('d', 'Dep · Occupies', 130),
                ('o', 'Own · Rules', 130),
                ('full', 'Full KP Signification (A–D)', 280),
            ],
            7,
        )
        self.sources.master.pack(fill='x')
        self.house.bind('<<ComboboxSelected>>', self.refresh_significations)
        self.sg.bind('<<TreeviewSelect>>', self.show_sources)
        self.refresh_significations()

    def refresh_significations(self, _event=None):
        h = self.house.current()
        prev = self.sg.selection()
        old = self.sg.item(prev[0], 'values')[0] if prev else None
        rows = []
        for name, blocks in self.groups.items():
            co = self.conj_opp_sigs[name]
            matches = (
                not h
                or any(h in dep or h in own for _, dep, own in blocks)
                or h in co['conj_all']
                or h in co['opp_all']
            )
            if matches:
                rows.append(
                    (
                        name,
                        *(joined(hs) for _, dep, own in blocks for hs in (dep, own)),
                        joined(co['conj_all']),
                        joined(co['opp_all']),
                    )
                )
        fill(self.sg, rows)
        match = next((str(i) for i, r in enumerate(rows) if r[0] == old), '0')
        if rows:
            self.sg.selection_set(match)
            self.sg.focus(match)
        n = len(rows)
        self.summary.configure(text=f'{n} of {len(self.groups)} planets  ·  Orb: Sun 6°40′ / Others 3°20′')
        self.show_sources()

    def show_sources(self, _event=None):
        selected = self.sg.selection()
        rows = []
        if selected:
            name = self.sg.item(selected[0], 'values')[0]
            co = self.conj_opp_sigs[name]
            for label, (names, dep, own) in zip(GROUPS, self.groups[name]):
                if label == 'Conjunction':
                    full_txt = joined(co['conj_all'])
                elif label == 'Opposition':
                    full_txt = joined(co['opp_all'])
                else:
                    full_txt = joined(sorted(set(dep) | set(own)))
                rows.append((label, joined(names), joined(dep), joined(own), full_txt))

            for cp, det in co['conj_planets'].items():
                abcd_str = (
                    f"All: {joined(det['all'])}  "
                    f"(A:{joined(det['A'])} | B:{joined(det['B'])} | C:{joined(det['C'])} | D:{joined(det['D'])})"
                )
                rows.append((f'Conjoined · {cp}', cp, joined(det['B']), joined(det['D']), abcd_str))

            for op, det in co['opp_planets'].items():
                abcd_str = (
                    f"All: {joined(det['all'])}  "
                    f"(A:{joined(det['A'])} | B:{joined(det['B'])} | C:{joined(det['C'])} | D:{joined(det['D'])})"
                )
                rows.append((f'Opposing · {op}', op, joined(det['B']), joined(det['D']), abcd_str))

        fill(self.sources, rows)

    def l1_l4_tab(self, pane):
        panel = self.app.panel(pane, 'L1, L2, L3, L4 Strength Table')
        panel.pack(fill='both', expand=True)
        bar = tk.Frame(panel, bg=WHITE)
        bar.pack(fill='x', padx=6, pady=5)
        self.app.label(bar, 'Filter by House:', size=9, bold=True).pack(side='left', padx=(5, 4))
        self.l_house = selector(
            bar,
            ('All Houses (All-in-One)', *(f'House {i}' for i in range(1, 13))),
            24,
        )
        self.l_house.pack(side='left')
        self.l_summary = self.app.label(bar, '', fg=MUTED, size=9)
        self.l_summary.pack(side='right', padx=7)

        self.note(
            panel,
            'L1 = Placement of Star, Sub, Conjoined & Opposed Lords  ·  L2 = Planet Placement  ·  '
            'L3 = Ownership of Star, Sub, Conjoined & Opposed Lords  ·  L4 = Planet Ownership',
            anchor='w',
        )

        cols = [
            ('planet', 'Planet', 84),
            ('lords', 'Star / Sub / Conj / Opp', 190),
            ('l1', 'L1 (St/Sb/Cj/Op Occ)', 140),
            ('l2', 'L2 (Planet Occ)', 105),
            ('l3', 'L3 (St/Sb/Cj/Op Own)', 140),
            ('l4', 'L4 (Planet Own)', 110),
            ('active', 'Selected House Levels / All Houses', 240),
        ]
        self.lt = self.app.table(panel, cols, 9, stretch=True)
        self.lt.master.pack(fill='both', expand=True)

        detail = self.app.panel(pane, 'Contributing Source Lords Breakdown (Selected Planet)')
        detail.pack(fill='x', pady=(5, 0))
        self.l_detail = self.app.table(
            detail,
            [
                ('lvl', 'Strength Level', 165),
                ('rule', 'Rule Source', 240),
                ('houses', 'Signified Houses', 150),
                ('lords', 'Contributing Planet(s)', 360),
            ],
            4,
        )
        self.l_detail.master.pack(fill='x')

        self.l_house.bind('<<ComboboxSelected>>', self.refresh_l1_l4)
        self.lt.bind('<<TreeviewSelect>>', self.show_l1_l4_detail)
        self.refresh_l1_l4()

    def refresh_l1_l4(self, _event=None):
        h = self.l_house.current()  # 0 = All Houses, 1..12 = specific house
        prev = self.lt.selection()
        old = self.lt.item(prev[0], 'values')[0] if prev else None
        rows = []
        for name, data in self.l1_l4.items():
            cj_txt = ','.join(SHORT[x] for x in data['conj']) or '–'
            op_txt = ','.join(SHORT[x] for x in data['opp']) or '–'
            lords_col = f"St:{SHORT[data['star']]} Sb:{SHORT[data['sub']]} Cj:{cj_txt} Op:{op_txt}"
            if h == 0:
                rows.append(
                    (
                        name,
                        lords_col,
                        joined(data['L1']),
                        joined(data['L2']),
                        joined(data['L3']),
                        joined(data['L4']),
                        joined(data['all']),
                    )
                )
            else:
                if h in data['all']:
                    matched_levels = [lvl for lvl in ('L1', 'L2', 'L3', 'L4') if h in data[lvl]]
                    reasons = data['house_reasons'][h]
                    via_parts = [f"{lvl}: {', '.join(reasons[lvl])}" for lvl in matched_levels]
                    rows.append(
                        (
                            name,
                            lords_col,
                            joined(data['L1']),
                            joined(data['L2']),
                            joined(data['L3']),
                            joined(data['L4']),
                            f"{' + '.join(matched_levels)}  ({'; '.join(via_parts)})",
                        )
                    )

        if h == 0:
            self.lt.heading('active', text='All Houses (L1–L4)')
            self.l_summary.configure(text=f'All {len(rows)} planets displayed (All-in-One)')
        else:
            self.lt.heading('active', text=f'House {h} Active Levels & Source')
            if rows:
                self.l_summary.configure(text=f'House {h}: {len(rows)} planet(s) signify this house')
            else:
                self.l_summary.configure(text=f'House {h}: No planet signifies this house')

        fill(self.lt, rows)
        if rows:
            match = next((str(i) for i, r in enumerate(rows) if r[0] == old), '0')
            self.lt.selection_set(match)
            self.lt.focus(match)
        self.show_l1_l4_detail()

    def show_l1_l4_detail(self, _event=None):
        selected = self.lt.selection()
        if not selected:
            fill(self.l_detail, [])
            return
        name = self.lt.item(selected[0], 'values')[0]
        data = self.l1_l4[name]
        h = self.l_house.current()

        def all_sources_for_level(lvl):
            seen = []
            for house_num in data[lvl]:
                for item in data['house_reasons'][house_num][lvl]:
                    entry = f'{item} → H{house_num}'
                    if entry not in seen:
                        seen.append(entry)
            return ', '.join(seen) or '—'

        if h == 0:
            rows = [
                ('L1 (Strongest)', 'Star, Sub, Conj & Opp Placement', joined(data['L1']), all_sources_for_level('L1')),
                ('L2', 'Planet Self Placement', joined(data['L2']), all_sources_for_level('L2')),
                ('L3', 'Star, Sub, Conj & Opp Ownership', joined(data['L3']), all_sources_for_level('L3')),
                ('L4', 'Planet Self Ownership', joined(data['L4']), all_sources_for_level('L4')),
            ]
        else:
            reasons = data['house_reasons'][h]
            rows = [
                ('L1 (Strongest)', 'Star, Sub, Conj & Opp Placement', str(h) if h in data['L1'] else '—', ', '.join(reasons['L1']) or '—'),
                ('L2', 'Planet Self Placement', str(h) if h in data['L2'] else '—', ', '.join(reasons['L2']) or '—'),
                ('L3', 'Star, Sub, Conj & Opp Ownership', str(h) if h in data['L3'] else '—', ', '.join(reasons['L3']) or '—'),
                ('L4', 'Planet Self Ownership', str(h) if h in data['L4'] else '—', ', '.join(reasons['L4']) or '—'),
            ]
        fill(self.l_detail, rows)