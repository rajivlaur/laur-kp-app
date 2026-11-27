# SPDX-License-Identifier: AGPL-3.0-or-later
"""KP Studio offline desktop interface. No network requests."""
import json
import math
import os
import re
import sqlite3
import sys
import traceback
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HERE = Path(__file__).resolve().parent
# The bundled interpreter does not depend on the machine's Python or Tk installation.
sys.path.insert(0, str(HERE))
if os.name == 'nt' and (HERE.parent/'runtime'/'tcl').is_dir():
    # Use the packaged tzdata everywhere, rather than a machine-specific override.
    from zoneinfo import reset_tzpath
    reset_tzpath(())
    os.environ['TCL_LIBRARY'] = str(HERE.parent/'runtime'/'tcl'/'tcl8.6')
    os.environ['TK_LIBRARY'] = str(HERE.parent/'runtime'/'tcl'/'tk8.6')
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from build_atlas import normalize
from kp_analysis import planet_analysis
from kp_core import calculate
from kp_views import ChartTabs

BG='#eeeeee'; WHITE='#ffffff'; TEXT='#26394a'; MUTED='#556574'; LINE='#d6dde3'
TEAL='#007f91'; CYAN='#00b5cd'; PURPLE='#921797'; CREAM='#f2e5bf'; PALE='#faf7ef'
DARK='#203440'; ALT='#f6f8fa'; FONT='Segoe UI'
OVERLAP = ['Automatic (ask if ambiguous)', 'First occurrence (earlier UTC)', 'Second occurrence (later UTC)']
SHORT={'Sun':'Su','Moon':'Mo','Mars':'Ma','Mercury':'Me','Jupiter':'Ju','Venus':'Ve','Saturn':'Sa','Rahu':'Ra','Ketu':'Ke'}
COLORS={'Sun':'#a44800','Moon':'#42587b','Mars':'#b92b27','Mercury':'#12674b','Jupiter':'#855900','Venus':'#98245e','Saturn':'#4b4494','Rahu':'#424c58','Ketu':'#5e4c41'}

def local_folder():
    if os.environ.get('KP_STUDIO_DATA'):
        return Path(os.environ['KP_STUDIO_DATA'])
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA') or (Path.home()/'AppData'/'Local'))/'KP Studio'
    return Path(os.environ.get('XDG_DATA_HOME') or (Path.home()/'.local'/'share'))/'kp-studio'


def get_zone(s):
    if re.fullmatch(r'[+-]\d{2}:\d{2}', s):
        h,m=map(int,s[1:].split(':'))
        if h>23 or m>59:
            raise ValueError('Enter the UTC offset as +HH:MM or −HH:MM, for example +05:30.')
        return timezone(timedelta(minutes=(60*h+m)*(-1 if s[0]=='-' else 1)))
    try:
        return ZoneInfo(s)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('Enter a time zone such as Asia/Kolkata, or a UTC offset such as +05:30.') from None


def valid_place(name, lat, lon, zone):
    name,zone=name.strip(),zone.strip().replace('−','-')
    if not name:
        raise ValueError('Enter a place name.')
    try:
        a,b=float(lat),float(lon)
        if not (-90 <= a <= 90 and -180 <= b <= 180):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('Enter latitude from −90 to 90 and longitude from −180 to 180. Use negative values for south or west.') from None
    get_zone(zone)
    return {'place':name,'lat':a,'lon':b,'zone':zone}


class Library:
    def __init__(self, home):
        home.mkdir(parents=True, exist_ok=True)
        self.path=home/'charts.sqlite3'
        self.db=sqlite3.connect(self.path)
        self.db.row_factory=sqlite3.Row
        with self.db:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS chart (id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    record TEXT NOT NULL, stamp TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS custom (key TEXT PRIMARY KEY, name TEXT NOT NULL,
                    lat REAL NOT NULL, lon REAL NOT NULL, zone TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS prefs (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    def save(self, rec, chart_id=None):
        chart_id=chart_id or uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO chart VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,record=excluded.record,stamp=excluded.stamp',
                (chart_id,rec['title'],json.dumps(rec,ensure_ascii=False),datetime.now(timezone.utc).isoformat(timespec='seconds')))
            p={k:rec[k] for k in ('place','lat','lon','zone')}
            self.db.execute('INSERT INTO prefs VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('recent_place',json.dumps(p,ensure_ascii=False)))
        return chart_id

    def rows(self):
        return [(r['id'],json.loads(r['record'])) for r in self.db.execute('SELECT id,record FROM chart ORDER BY stamp DESC,title COLLATE NOCASE,id')]

    def read(self, chart_id):
        row=self.db.execute('SELECT record FROM chart WHERE id=?',(chart_id,)).fetchone()
        if row is None:
            raise ValueError('This chart was not found.')
        return json.loads(row[0])

    def place(self):
        r=self.db.execute('SELECT value FROM prefs WHERE key=?',('recent_place',)).fetchone()
        return json.loads(r[0]) if r else None

    def add_place(self,p):
        key=json.dumps([normalize(p['place']),p['lat'],p['lon'],p['zone']],ensure_ascii=False)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO custom VALUES (?,?,?,?,?)',(key,p['place'],p['lat'],p['lon'],p['zone']))

    def find_places(self, term):
        found=[]
        q=normalize(term)
        for r in self.db.execute('SELECT name,lat,lon,zone FROM custom ORDER BY name COLLATE NOCASE'):
            if q in normalize(r['name']):
                found.append((r['name'],'Custom place','',r['lat'],r['lon'],r['zone']))
                if len(found)>=20:
                    break
        return found

    def backup(self, target):
        p=Path(target)
        if p.resolve()==self.path.resolve():
            raise ValueError('Choose a different file from the live chart library.')
        tmp=p.with_name('.kp-'+uuid.uuid4().hex+'.tmp')
        try:
            with closing(sqlite3.connect(tmp)) as dest:
                self.db.backup(dest)
            os.replace(tmp,p)
        finally:
            tmp.unlink(missing_ok=True)


class Atlas:
    def __init__(self, path):
        self.path=path
        self.db=None
        if path.is_file():
            self.db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
            self.db.create_function('place_norm',1,normalize,deterministic=True)

    def search(self, term, limit=45):
        if self.db is None:
            return []
        parts=[normalize(t) for t in term.split(',') if t.strip()]
        if not parts or len(parts[0])<2:
            return []
        q,*areas=parts[:3]
        whr=''
        args=[q,q,q+'\U0010ffff']
        for area in areas:
            whr+=' AND (instr(place_norm(p.region),?)>0 OR instr(place_norm(p.country),?)>0 OR lower(p.code)=?)'
            args.extend([area,area,area])
        args.extend([q,limit])
        query="""
            WITH hits AS (
                SELECT id, MIN(CASE WHEN key=? THEN 0 ELSE 1 END) AS rank
                FROM alias WHERE key>=? AND key<? GROUP BY id
            )
            SELECT p.name,p.region,p.country,p.lat,p.lon,p.zone
            FROM hits JOIN place p ON p.id=hits.id WHERE 1=1 """ + whr + """
            ORDER BY hits.rank, CASE WHEN place_norm(p.name)=? THEN 0 ELSE 1 END,
                p.population DESC, p.id LIMIT ?
        """
        return self.db.execute(query,args).fetchall()


class App:
    def __init__(self, root):
        self.root=root
        root.title('KP Studio  |  Offline astrology'); root.configure(bg=BG)
        root.minsize(1010,690)
        w=min(1460,max(1010,root.winfo_screenwidth()-72)); h=min(900,max(690,root.winfo_screenheight()-105))
        root.geometry(f'{w}x{h}')
        root.option_add('*Font',(FONT,10))
        self.library=Library(local_folder())
        self.atlas=Atlas(HERE/'data'/'atlas.sqlite3')
        self.box=None; self.view=''; self.chart_id=None; self.rec=None; self.result=None
        self.status=tk.StringVar(value='Ready')
        self.styles()
        self.top=tk.Frame(root,bg=WHITE,height=61,highlightbackground=LINE,highlightthickness=1)
        self.top.pack(fill='x'); self.top.pack_propagate(False)
        logo=tk.Label(self.top,text='✦',font=(FONT,19,'bold'),bg=TEAL,fg=WHITE,padx=8)
        logo.pack(side='left',padx=(22,10),pady=10)
        tk.Label(self.top,text='KP Studio',font=(FONT,16,'bold'),bg=WHITE,fg=TEXT).pack(side='left')
        self.crumb=tk.Label(self.top,text='',bg=WHITE,fg=MUTED,font=(FONT,11)); self.crumb.pack(side='left',padx=22)
        self.button(self.top,'About',self.about,'ghost').pack(side='right',padx=(4,18),pady=9)
        self.button(self.top,'My Charts',self.show_library,'ghost').pack(side='right',padx=(4,0),pady=9)
        tk.Label(self.top,text='●  OFFLINE',font=(FONT,9,'bold'),bg=WHITE,fg=TEAL).pack(side='right',padx=14)
        foot=tk.Frame(root,bg=DARK,height=31); foot.pack(side='bottom',fill='x'); foot.pack_propagate(False)
        tk.Label(foot,textvariable=self.status,bg=DARK,fg='#f1f6f7',font=(FONT,9),anchor='w').pack(side='left',fill='x',expand=True,padx=18)
        tk.Label(foot,text='KP NEW  •  PLACIDUS  •  MEAN NODE',bg=DARK,fg='#d6e9ed',font=(FONT,9,'bold')).pack(side='right',padx=18)
        self.show_form()

    def styles(self):
        s=ttk.Style(self.root); s.theme_use('clam')
        s.configure('TEntry',padding=(7,5),fieldbackground=WHITE,foreground=TEXT,bordercolor=LINE,lightcolor=LINE,darkcolor=LINE)
        s.map('TEntry',bordercolor=[('focus',TEAL)],lightcolor=[('focus',TEAL)])
        s.configure('TCombobox',padding=(6,4),fieldbackground=WHITE,foreground=TEXT,background=ALT,bordercolor=LINE)
        s.map('TCombobox',fieldbackground=[('readonly',WHITE)],foreground=[('readonly',TEXT)],selectbackground=[('readonly',WHITE)],selectforeground=[('readonly',TEXT)])
        s.configure('Treeview',background=WHITE,fieldbackground=WHITE,foreground=TEXT,borderwidth=0,rowheight=24,font=(FONT,9))
        s.configure('Treeview.Heading',background=CREAM,foreground=TEXT,font=(FONT,9,'bold'),padding=(6,6),relief='flat')
        s.map('Treeview',background=[('selected','#cbe7eb')],foreground=[('selected','#14333f')])
        s.map('Treeview.Heading',background=[('active',CREAM)])
        s.configure('TPanedwindow',background=BG,sashwidth=6)

    def button(self, parent, text, command, theme='main'):
        pal={'main':(TEAL,WHITE,'#006a77'),'purple':(PURPLE,WHITE,'#75117a'),'ghost':(WHITE,TEAL,'#e5f3f4'),'quiet':(ALT,TEXT,'#e2e9ee'),'on':(TEAL,WHITE,'#006a77')}
        bg,fg,hover=pal[theme]
        b=tk.Button(parent,text=text,command=command,bg=bg,fg=fg,activebackground=hover,activeforeground=fg,relief='flat',bd=0,padx=12,pady=7,cursor='hand2',font=(FONT,10,'bold'),highlightbackground=bg,highlightcolor=TEAL,highlightthickness=1)
        b.bind('<Enter>',lambda _e: b.configure(bg=hover) if b['state']=='normal' else None)
        b.bind('<Leave>',lambda _e: b.configure(bg=bg))
        return b

    def replace_view(self, title):
        if self.box is not None:
            self.box.destroy()
        self.crumb.configure(text='›   '+title)
        self.box=tk.Frame(self.root,bg=BG); self.box.pack(fill='both',expand=True)
        return self.box

    def label(self,parent,text,*,fg=TEXT,bg=WHITE,size=10,bold=False,**kw):
        kw.setdefault('anchor', 'w')
        return tk.Label(parent,text=text,fg=fg,bg=bg,font=(FONT,size,'bold' if bold else 'normal'),**kw)

    def new_card(self, parent):
        return tk.Frame(parent,bg=WHITE,highlightbackground=LINE,highlightthickness=1)

    def field(self, parent, text, var=None, width=20):
        frame=tk.Frame(parent,bg=WHITE)
        self.label(frame,text,fg=MUTED,size=9,bold=True).pack(fill='x',pady=(0,4))
        e=ttk.Entry(frame,textvariable=var,width=width); e.pack(fill='x')
        return frame,e

    def show_form(self, chart_id=None, copy=False):
        seed=self.library.read(chart_id) if chart_id else {}
        if copy and seed:
            seed=dict(seed,title=seed['title']+' (copy)')
        editing=bool(chart_id and not copy)
        self.view='form'; self.edit_id=chart_id if editing else None
        self.form_seed=seed
        page=self.replace_view('Edit Birth Details' if editing else ('Save a Copy' if copy else 'Create New Chart'))
        self.status.set('Enter the local date and time for the selected place.')
        card=self.new_card(page); card.pack(fill='both',expand=True,padx=34,pady=(24,22))
        bar=tk.Frame(card,bg=WHITE); bar.pack(fill='x',padx=24,pady=(12,3))
        self.label(bar,'Create Chart' if not editing else 'Edit Chart Details',size=17,bold=True).pack(side='left')
        self.button(bar,'↶  Back to Charts',self.show_library,'ghost').pack(side='right')
        self.mode=tk.StringVar(value=seed.get('mode','Birth'))
        tabs=tk.Frame(card,bg=ALT); tabs.pack(pady=(5,10))
        self.mode_buttons={k:self.button(tabs,k,lambda v=k:self.switch_mode(v),'quiet') for k in ('Birth','Horary')}
        for b in self.mode_buttons.values(): b.pack(side='left',padx=2,pady=2)
        grid=tk.Frame(card,bg=WHITE); grid.pack(fill='both',expand=True,padx=24,pady=(0,4))
        for c,w in enumerate((4,2,2)): grid.columnconfigure(c,weight=w,uniform='fields')
        today=datetime.now()
        values={**seed,'date':seed.get('date',today.strftime('%Y-%m-%d')),'time':seed.get('time',today.strftime('%H:%M:%S'))}
        self.vars={k:tk.StringVar(value=str(values.get(k,''))) for k in ('title','date','time','place','lat','lon','zone','horary')}
        self.fold=tk.StringVar(value=OVERLAP[0 if seed.get('fold') is None else int(seed['fold'])+1])
        title,self.title_entry=self.field(grid,'Chart Title',self.vars['title']); title.grid(row=0,column=0,columnspan=2,sticky='ew',pady=(0,8),padx=(0,12))
        self.horary_col,_=self.field(grid,'Horary Number (1–249)',self.vars['horary']); self.horary_col.grid(row=0,column=2,sticky='ew',pady=(0,8))
        date,_=self.field(grid,'Local Date  ·  YYYY-MM-DD',self.vars['date']); date.grid(row=1,column=0,sticky='ew',pady=(0,8),padx=(0,12))
        time,_=self.field(grid,'Local Time  ·  HH:MM:SS',self.vars['time']); time.grid(row=1,column=1,sticky='ew',pady=(0,8),padx=(0,12))
        self.here=self.button(grid,'◎  Here & Now',self.here_and_now,'purple'); self.here.grid(row=1,column=2,sticky='ews',pady=(0,8))
        pcol=tk.Frame(grid,bg=WHITE); pcol.grid(row=2,column=0,sticky='ew',pady=(0,8),padx=(0,12)); pcol.columnconfigure(0,weight=1)
        self.label(pcol,'Place  ·  use the offline atlas or enter coordinates',fg=MUTED,size=9,bold=True).grid(row=0,column=0,columnspan=3,sticky='ew',pady=(0,4))
        p=ttk.Entry(pcol,textvariable=self.vars['place'],width=12); p.grid(row=1,column=0,sticky='ew',padx=(0,5))
        self.button(pcol,'Find Place',self.place_dialog,'quiet').grid(row=1,column=1,padx=(0,4))
        self.button(pcol,'Custom Place',self.custom_dialog,'ghost').grid(row=1,column=2)
        p.bind('<Return>',lambda _e:self.place_dialog())
        for c,k,title2 in ((1,'lat','Latitude  ·  north +'),(2,'lon','Longitude  ·  east +')):
            f,_=self.field(grid,title2,self.vars[k]); f.grid(row=2,column=c,sticky='ews',pady=(0,8),padx=(0,12 if c==1 else 0))
        zone,_=self.field(grid,'Time Zone  ·  IANA name or UTC offset',self.vars['zone']); zone.grid(row=3,column=0,sticky='ew',pady=(0,8),padx=(0,12))
        amb=tk.Frame(grid,bg=WHITE); amb.grid(row=3,column=1,columnspan=2,sticky='ew',pady=(0,8))
        self.label(amb,'If the local clock time occurs twice',fg=MUTED,size=9,bold=True).pack(fill='x',pady=(0,4))
        ttk.Combobox(amb,textvariable=self.fold,values=OVERLAP,state='readonly').pack(fill='x')
        note=tk.Frame(grid,bg=WHITE); note.grid(row=4,column=0,columnspan=3,sticky='nsew',pady=(0,4)); grid.rowconfigure(4,weight=1)
        self.label(note,'Notes / Matter / Question',fg=MUTED,size=9,bold=True).pack(fill='x',pady=(0,4))
        self.notes=tk.Text(note,height=3,wrap='word',font=(FONT,10),fg=TEXT,bg=WHITE,borderwidth=0,highlightbackground=LINE,highlightcolor=TEAL,highlightthickness=1,padx=8,pady=5)
        self.notes.pack(fill='both',expand=True); self.notes.insert('1.0',seed.get('notes',''))
        f=tk.Frame(card,bg=WHITE); f.pack(fill='x',padx=24,pady=(4,12))
        self.label(f,'Ayanamsha   KP NEW  ·  Swiss mode 45',fg=TEAL,size=10,bold=True).pack(side='left')
        self.button(f,'Save & Recalculate' if editing else ('Save Copy' if copy else 'Save & Create'),self.commit_form).pack(side='right')
        self.form_error=self.label(f,'',fg='#9b2424',size=9,justify='left',wraplength=415)
        self.form_error.pack(side='right',padx=12)
        self.switch_mode(self.mode.get()); self.title_entry.focus_set()

    def switch_mode(self, v):
        self.mode.set(v)
        for k,b in self.mode_buttons.items():
            bg,fg=(TEAL,WHITE) if v==k else (ALT,TEXT)
            b.configure(bg=bg,fg=fg,activebackground=bg,activeforeground=fg,highlightbackground=bg)
            b.bind('<Enter>',lambda _e,bb=b,cc='#006a77' if v==k else '#e2e9ee':bb.configure(bg=cc))
            b.bind('<Leave>',lambda _e,bb=b,cc=bg:bb.configure(bg=cc))
        if v=='Horary':
            self.horary_col.grid(); self.here.grid()
        else:
            self.horary_col.grid_remove(); self.here.grid_remove()

    def set_place(self, place):
        for k,v in place.items():
            if k in self.vars:
                self.vars[k].set(f'{v:.7f}'.rstrip('0').rstrip('.') if k in ('lat','lon') else v)
        self.fold.set(OVERLAP[0]); self.form_error.configure(text='')

    def here_and_now(self):
        try:
            if not self.vars['zone'].get().strip():
                p=self.library.place()
                if not p:
                    raise ValueError('Select a place first. Here & Now uses your selected or last saved location.')
                self.set_place(p)
            z=get_zone(self.vars['zone'].get().strip().replace('−','-'))
            d=datetime.now(timezone.utc).astimezone(z)
            self.vars['date'].set(d.strftime('%Y-%m-%d')); self.vars['time'].set(d.strftime('%H:%M:%S'))
            offsets={d.replace(fold=f).utcoffset() for f in (0,1)}
            self.fold.set(OVERLAP[d.fold+1 if len(offsets)>1 else 0]); self.form_error.configure(text='')
            self.status.set('Current local time filled for the selected place.')
        except ValueError as e:
            self.form_error.configure(text=str(e))

    def commit_form(self):
        try:
            r={k:v.get().strip() for k,v in self.vars.items()}
            if not r['title']:
                raise ValueError('Enter a chart title.')
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',r['date']) or not re.fullmatch(r'\d{2}:\d{2}(?::\d{2})?',r['time']):
                raise ValueError('Use YYYY-MM-DD for date and HH:MM:SS for time (24-hour clock).')
            if len(r['time'])==5:
                r['time']+=':00'
            r.update(valid_place(r['place'],r['lat'],r['lon'],r['zone']))
            r['notes']=self.notes.get('1.0','end-1c'); r['mode']=self.mode.get()
            f=OVERLAP.index(self.fold.get()); r['fold']=None if f==0 else f-1
            if r['mode']=='Horary':
                if not (r['horary'].isdecimal() and 1<=int(r['horary'])<=249):
                    raise ValueError('Enter a Horary number from 1 to 249.')
                r['horary']=int(r['horary'])
            else:
                r['horary']=None
            out=calculate(r)
            cid=self.library.save(r,self.edit_id)
        except (ValueError,RuntimeError,sqlite3.Error,OSError) as e:
            self.form_error.configure(text=str(e)); return
        self.show_chart(cid,r,out,True)

    def dialog(self, title, width, height, parent=None):
        parent=parent or self.root
        d=tk.Toplevel(parent); d.title(title); d.configure(bg=WHITE)
        d.transient(parent); d.grab_set(); d.minsize(min(480,width),min(290,height))
        self.root.update_idletasks()
        x=self.root.winfo_rootx()+max(10,(self.root.winfo_width()-width)//2)
        y=self.root.winfo_rooty()+max(10,(self.root.winfo_height()-height)//2)
        d.geometry(f'{width}x{height}+{x}+{y}')
        d.bind('<Escape>',lambda _e:d.destroy())
        return d

    def place_dialog(self):
        d=self.dialog('Find a place · Offline Atlas',780,455)
        box=tk.Frame(d,bg=WHITE); box.pack(fill='both',expand=True,padx=16,pady=14)
        self.label(box,'Find a birthplace',size=17,bold=True).pack(fill='x')
        self.label(box,'Search city or alternate name. Use “City, Region” or “City, Country” to narrow results.',fg=MUTED,size=9).pack(fill='x',pady=(2,6))
        q=tk.StringVar(value=self.vars['place'].get().split(' · ')[0]); bar=tk.Frame(box,bg=WHITE); bar.pack(fill='x',pady=(0,7))
        e=ttk.Entry(bar,textvariable=q); e.pack(side='left',fill='x',expand=True); e.select_range(0,'end'); e.focus_set()
        columns=[('name','Place',165),('area','Region / Country',218),('zone','Time Zone',155),('coords','Coordinates',164)]
        t=self.table(box,columns,9); t.master.pack(fill='both',expand=True)
        msg=self.label(box,'',fg=MUTED,size=9); msg.pack(fill='x',pady=(4,0))
        results={}
        def find(*_):
            for item in t.get_children(): t.delete(item)
            results.clear()
            text=q.get().strip()
            if not text:
                msg.configure(text='Enter a place name. Custom places can be added at any time.'); return
            try:
                rows=self.library.find_places(text)+self.atlas.search(text)
            except (ValueError,sqlite3.Error) as exc:
                msg.configure(text=f'Atlas unavailable: {exc}'); return
            for i,(name,region,country,a,b,z) in enumerate(rows):
                label=' · '.join(filter(None,(name,region if region!='Custom place' else '',country)))
                item=t.insert('','end',values=(name,' / '.join(filter(None,(region,country))),z,f'{a:.5f}, {b:.5f}'),tags=('alt',) if i%2 else ())
                results[item]={'place':label,'lat':a,'lon':b,'zone':z}
            msg.configure(text=f'{len(rows)} match(es) shown.' if rows else 'No match. Try another spelling or choose Add Custom Place.')
            if rows:
                first=t.get_children()[0]; t.selection_set(first); t.focus(first)
        def use(*_):
            ids=t.selection()
            if ids and ids[0] in results:
                self.set_place(results[ids[0]]); d.destroy()
        self.button(bar,'Search',find).pack(side='left',padx=(7,0)); e.bind('<Return>',find)
        t.bind('<Double-1>',use); t.bind('<Return>',use)
        bottom=tk.Frame(box,bg=WHITE); bottom.pack(fill='x',pady=(4,0))
        self.button(bottom,'Add Custom Place',lambda:(d.destroy(),self.custom_dialog()),'ghost').pack(side='left')
        self.button(bottom,'Use Selected Place',use).pack(side='right')
        find()

    def custom_dialog(self):
        d=self.dialog('Add Custom Place',535,442)
        box=tk.Frame(d,bg=WHITE); box.pack(fill='both',expand=True,padx=20,pady=14)
        self.label(box,'Add Custom Place',size=17,bold=True).pack(fill='x')
        self.label(box,'Enter any village, hospital or location, even if it is not in the atlas.',fg=MUTED,size=9).pack(fill='x',pady=(3,8))
        vs={k:tk.StringVar(value=self.vars[k].get()) for k in ('place','lat','lon','zone')}
        for k,label in (('place','Place Name'),('lat','Latitude  ·  south is negative'),('lon','Longitude  ·  west is negative'),('zone','Time Zone  ·  e.g. Asia/Kolkata or +05:30')):
            f,e=self.field(box,label,vs[k]); f.pack(fill='x',pady=(0,6))
            if k=='place': e.focus_set()
        err=self.label(box,'',fg='#9b2424',size=9,wraplength=445,justify='left'); err.pack(fill='x')
        def add(*_):
            try:
                p=valid_place(*(vs[k].get() for k in ('place','lat','lon','zone')))
                self.library.add_place(p); self.set_place(p); d.destroy()
                self.status.set('Custom place saved to this computer.')
            except (ValueError,sqlite3.Error) as exc: err.configure(text=str(exc))
        self.button(box,'Save & Use Place',add).pack(side='right',pady=(5,0))
        d.bind('<Return>',add)

    def table(self,parent, columns, height=8, stretch=True):
        f=tk.Frame(parent,bg=WHITE); f.rowconfigure(0,weight=1); f.columnconfigure(0,weight=1)
        ids=[c[0] for c in columns]; t=ttk.Treeview(f,columns=ids,show='headings',height=height,selectmode='browse')
        for k,label,w in columns:
            t.heading(k,text=label,anchor='w'); t.column(k,width=w,minwidth=35,stretch=stretch,anchor='w')
        y=ttk.Scrollbar(f,orient='vertical',command=t.yview); t.configure(yscrollcommand=y.set)
        t.grid(row=0,column=0,sticky='nsew'); y.grid(row=0,column=1,sticky='ns')
        if not stretch:
            x=ttk.Scrollbar(f,orient='horizontal',command=t.xview); t.configure(xscrollcommand=x.set); x.grid(row=1,column=0,sticky='ew')
        t.tag_configure('alt',background=PALE)
        return t

    def show_library(self):
        self.view='library'; box=self.new_card(self.replace_view('My Charts')); box.pack(fill='both',expand=True,padx=34,pady=24)
        bar=tk.Frame(box,bg=WHITE); bar.pack(fill='x',padx=20,pady=(15,8))
        self.label(bar,'My Charts',size=18,bold=True).pack(side='left')
        self.button(bar,'＋  New Chart',self.show_form).pack(side='right')
        self.button(bar,'Back Up Library',self.backup,'ghost').pack(side='right',padx=5)
        self.label(box,'Saved on this computer. Select a chart to open it or change its details.',fg=MUTED,size=10).pack(fill='x',padx=20)
        q=tk.StringVar(); search=tk.Frame(box,bg=WHITE); search.pack(fill='x',padx=20,pady=8)
        self.label(search,'Filter',fg=MUTED,bold=True).pack(side='left',padx=(0,6))
        ttk.Entry(search,textvariable=q).pack(side='left',fill='x',expand=True)
        t=self.table(box,[('t','Title',220),('m','Type',90),('d','Local Date & Time',165),('p','Place',290)],12)
        t.master.pack(fill='both',expand=True,padx=20,pady=(0,10))
        rows=self.library.rows(); msg=self.label(box,'',fg=MUTED,size=9); msg.pack(fill='x',padx=20)
        def render(*_):
            for child in t.get_children(): t.delete(child)
            n=0; needle=normalize(q.get())
            for cid,r in rows:
                if needle not in normalize(r['title']+' '+r['place']): continue
                t.insert('','end',iid=cid,values=(r['title'],r['mode'],r['date']+'  '+r['time'],r['place']),tags=('alt',) if n%2 else ()); n+=1
            msg.configure(text=f'{n} chart(s) shown.' if rows else 'No charts saved yet. Choose New Chart to get started.')
            selection=t.get_children()
            if selection: t.selection_set(selection[0]); t.focus(selection[0])
        def selected(action):
            ids=t.selection()
            if ids: action(ids[0])
        q.trace_add('write',render); render(); t.bind('<Double-1>',lambda _e:selected(self.open_chart)); t.bind('<Return>',lambda _e:selected(self.open_chart))
        f=tk.Frame(box,bg=WHITE); f.pack(fill='x',padx=20,pady=(4,13))
        self.button(f,'Open Chart',lambda:selected(self.open_chart)).pack(side='right')
        self.button(f,'Edit Details',lambda:selected(self.show_form),'ghost').pack(side='right',padx=6)
        self.status.set(f'{len(rows)} local chart(s) • No account or network connection required.')

    def backup(self):
        filename=filedialog.asksaveasfilename(parent=self.root,title='Back up chart library',defaultextension='.sqlite3',initialfile='KP Studio backup.sqlite3',filetypes=[('SQLite database','*.sqlite3')])
        if not filename: return
        try:
            self.library.backup(filename); self.status.set('Library backup saved to '+filename)
        except (OSError,sqlite3.Error,ValueError) as e:
            messagebox.showerror('Backup failed',str(e),parent=self.root)

    def open_chart(self,cid):
        try:
            record=self.library.read(cid); result=calculate(record)
            self.show_chart(cid,record,result)
        except (ValueError,RuntimeError,sqlite3.Error,OSError) as e:
            messagebox.showerror('Could not open chart',str(e),parent=self.root)

    def panel(self, parent, title):
        p=self.new_card(parent)
        self.label(p,title,fg=TEXT,bg=CREAM,size=10,bold=True,padx=8,pady=6).pack(fill='x')
        return p

    def show_chart(self,cid,rec,calc,saved=False):
        self.rec,self.result,self.chart_id=rec,calc,cid; self.view='result'
        page=self.replace_view('Chart Workspace')
        header=self.new_card(page); header.pack(fill='x',padx=10,pady=(8,7))
        title=tk.Frame(header,bg=WHITE); title.pack(side='left',fill='both',expand=True,padx=10,pady=6)
        chart_title=rec['title'] if len(rec['title'])<50 else rec['title'][:47]+'…'
        self.label(title,chart_title,size=15,bold=True).pack(fill='x')
        place=rec['place'] if len(rec['place'])<62 else rec['place'][:59]+'…'
        mode=rec['mode']+(' #'+str(rec['horary']) if rec['mode']=='Horary' else '')
        self.label(title,f"{mode}  ·  {rec['date']}   {rec['time']}  ·  {place}",size=9,fg=MUTED).pack(fill='x')
        for txt,cmd,theme in [('Edit Birth Details' if rec['mode']=='Birth' else 'Edit Horary Details',lambda:self.show_form(cid),'main'),('Save As',lambda:self.show_form(cid,True),'ghost'),('＋ New Chart',self.show_form,'ghost')]:
            self.button(header,txt,cmd,theme).pack(side='right',padx=(1,7),pady=6)
        workspace=ttk.Notebook(page)
        workspace.pack(fill='both',expand=True,padx=10,pady=(0,8))
        ChartTabs(self,workspace,rec,calc)
        work=tk.Frame(workspace,bg=BG)
        workspace.add(work,text='North Indian & Notes',padding=5)
        for col,weight,minsize in ((0,44,385),(1,30,275),(2,26,254)): work.columnconfigure(col,weight=weight,minsize=minsize)
        work.rowconfigure(0,weight=1)
        left,center,right=[tk.Frame(work,bg=BG) for i in range(3)]
        left.grid(row=0,column=0,sticky='nsew',padx=(0,6)); center.grid(row=0,column=1,sticky='nsew',padx=(0,6)); right.grid(row=0,column=2,sticky='nsew')
        tabs=ttk.Notebook(left); tabs.pack(side='bottom',fill='x',pady=(4,0))
        sig=tk.Frame(tabs,bg=WHITE); tabs.add(sig,text='KP Significators')
        _, _, _, _, conj_opp_sigs = planet_analysis(calc)
        sig_values=calc.get('significators') or []
        st=self.table(sig,[('p','Planet',72),('h','Signified Houses (A / B / C / D)',155),('co','Conjoined / Opposing Sig.',155)],3,stretch=False); st.master.pack(fill='x')
        if isinstance(sig_values,dict): sig_values=list(sig_values.items())
        for i,item in enumerate(sig_values):
            if isinstance(item,dict):
                name=item.get('planet',item.get('name','')); hs=item.get('houses',item.get('signifies',[]))
            else: name,hs=item
            if isinstance(hs,dict):
                levels=('A_star_lord_occupies','B_occupies','C_star_lord_owns','D_owns')
                hs='  /  '.join(', '.join(map(str,hs.get(k,[]))) or '–' for k in levels)
            elif not isinstance(hs,str): hs=', '.join(map(str,hs))
            co_info = conj_opp_sigs.get(name, {})
            c_txt = ', '.join(map(str, co_info.get('conj_all', []))) or '–'
            o_txt = ', '.join(map(str, co_info.get('opp_all', []))) or '–'
            st.insert('','end',values=(name,hs,f'Conj: {c_txt} | Opp: {o_txt}'),tags=('alt',) if i%2 else ())
        self.label(sig,'Order: star occupancy, occupancy, star ownership, ownership + conj/opp houses.',fg=MUTED,size=8).pack(fill='x',padx=3)
        cols=[('obj','Body',62),('deg','Degree',69),('sign','Sign',79),('s','Sign Lord',68),('nak','Nakshatra',112),('star','Star',61),('sub','Sub',61),('ss','Sub-Sub',65)]
        c=self.panel(left,'Planetary Positions'); c.pack(fill='both',expand=True,pady=(0,4))
        p=self.table(c,cols,5,stretch=False); p.master.pack(fill='both',expand=True)
        c2=self.panel(left,'Stellar Cusps  ·  Placidus'); c2.pack(fill='both',expand=True)
        q=self.table(c2,[('obj','Cusp',62)]+cols[1:],6,stretch=False); q.master.pack(fill='both',expand=True)
        for t,key in [(p,'planets'),(q,'cusps')]:
            for i,r in enumerate(calc[key]):
                n=(r['name']+(' ℞' if r.get('retro') else '')) if key=='planets' else f'{i+1:02d}'
                t.insert('','end',values=(n,r['degree'],r['sign'],r['sign_lord'],r['nakshatra'],r['star'],r['sub'],r['subsub']),tags=('alt',) if i%2 else ())
        for idx,label in enumerate(('Rashi Chart','Bhava Chart')):
            center.rowconfigure(idx,weight=1,uniform='chart'); center.columnconfigure(0,weight=1)
            x=self.panel(center,label); x.grid(row=idx,column=0,sticky='nsew',pady=(0,4)); cv=tk.Canvas(x,bg='#fffdf7',highlightthickness=0,width=200,height=116); cv.pack(fill='both',expand=True)
            cv.bind('<Configure>',lambda _e,a=cv,b=bool(idx):self.draw_chart(a,calc,b))
        notes=self.panel(center,'Chart Notes'); notes.grid(row=2,column=0,sticky='ew')
        view=tk.Text(notes,wrap='word',height=4,bg=WHITE,fg=TEXT,relief='flat',padx=7,pady=4,font=(FONT,9))
        view.insert('1.0',rec['notes'] or 'No notes. Use Edit Details to add a note.'); view.configure(state='disabled'); view.pack(fill='x')
        rpanel=self.panel(right,'Ruling Planets  ·  Chart Time'); rpanel.pack(fill='x',pady=(0,4))
        for i,(rule,planet) in enumerate(calc['ruling']):
            b=tk.Frame(rpanel,bg=WHITE if i%2 else ALT); b.pack(fill='x',padx=3)
            color=b['bg']; self.label(b,f'{i+1}   {rule}',bg=color,size=8,padx=3,pady=3).pack(side='left',fill='x',expand=True)
            self.label(b,planet,bg=color,fg=TEAL,size=8,bold=True,padx=3).pack(side='right')
        dp=self.panel(right,'Vimshottari Dasha'); dp.pack(fill='both',expand=True)
        self.label(dp,'Expand a period to see its subdivisions.',fg=MUTED,size=8,padx=6,pady=3).pack(fill='x')
        df=tk.Frame(dp,bg=WHITE); df.pack(fill='both',expand=True); df.columnconfigure(0,weight=1); df.rowconfigure(0,weight=1)
        tree=ttk.Treeview(df,show='tree',height=5,selectmode='browse'); tree.column('#0',width=286,minwidth=230,stretch=True)
        sy=ttk.Scrollbar(df,orient='vertical',command=tree.yview); sx=ttk.Scrollbar(df,orient='horizontal',command=tree.xview); tree.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
        tree.grid(row=0,column=0,sticky='nsew'); sy.grid(row=0,column=1,sticky='ns'); sx.grid(row=1,column=0,sticky='ew')
        def periods(rows,parent='',lev=0):
            for item in rows:
                start=str(item['start'])[:10]; end=str(item['end'])[:10]
                tag='d'+str(lev); sub=tree.insert(parent,'end',text=f"{item['lord']}  ·  {start} → {end}",tags=(tag,))
                periods(item.get('children',[]),sub,lev+1)
        tree.tag_configure('d0',foreground=TEAL,font=(FONT,9,'bold')); tree.tag_configure('d1',foreground=TEXT); tree.tag_configure('d2',foreground=MUTED)
        periods(calc['dasha'])
        self.label(dp,'Date range shown in UTC  ·  365.25-day year',fg=MUTED,size=8,padx=6,pady=4).pack(fill='x')
        self.status.set(('Saved locally  •  ' if saved else '')+f"{rec['zone']} ({calc['offset']})  •  UTC {calc['utc']}  •  Ayanamsha {calc['ayanamsa']:.6f}°")

    def draw_chart(self,canvas,calc,bhava):
        w=canvas.winfo_width(); h=canvas.winfo_height(); canvas.delete('all')
        if w<60 or h<45: return
        m=10; x0,y0=m,m; x1,y1=w-m,h-m; dx=x1-x0; dy=y1-y0
        def pt(x,y): return x0+x*dx,y0+y*dy
        for a,b in [((0,0),(1,1)),((0,1),(1,0)),((.5,0),(1,.5)),((1,.5),(.5,1)),((.5,1),(0,.5)),((0,.5),(.5,0))]:
            canvas.create_line(*pt(*a),*pt(*b),fill='#baa981',width=1.35)
        canvas.create_rectangle(x0,y0,x1,y1,outline='#baa981',width=1.35)
        centers=[(.50,.22),(.25,.104),(.105,.255),(.215,.50),(.105,.745),(.25,.892),(.50,.78),(.75,.892),(.895,.745),(.785,.50),(.895,.255),(.75,.104)]
        planets=[[] for _ in range(12)]
        for r in calc['planets']:
            k=(r['house']-1) if bhava else (r['sign_index']-calc['rising_sign'])%12
            if 0<=k<12: planets[k].append(r)
        for i,(x,y) in enumerate(centers):
            xx,yy=pt(x,y); fs=9 if dy>140 else 8
            number=((calc['cusps'][i]['sign_index'] if bhava else (calc['rising_sign']+i)%12)+1)
            short=planets[i]; shift=7 if dy>120 else 5
            canvas.create_text(xx,yy-(shift if short else 0),text=str(number),font=(FONT,fs),fill='#695536')
            if short:
                for off in range(0,len(short),3):
                    chunk=short[off:off+3]; sep=min(23,dx/13)
                    for j,planet in enumerate(chunk):
                        n=planet['name']; token=SHORT.get(n,n[:2])+('℞' if planet.get('retro') and n not in ('Rahu','Ketu') else '')
                        canvas.create_text(xx+(j-(len(chunk)-1)/2)*sep,yy+shift+off//3*(fs+3),text=token,fill=COLORS.get(n,TEXT),font=(FONT,fs,'bold'))

    def about(self):
        d=self.dialog('About KP Studio',575,390)
        b=tk.Frame(d,bg=WHITE); b.pack(fill='both',expand=True,padx=18,pady=18)
        self.label(b,'KP Studio  ·  Personal preview',size=18,bold=True).pack(fill='x')
        txt=('An offline KP workbench for Birth and 1–249 Horary charts. This build labels the Swiss Ephemeris Krishnamurti–Senthilathiban / VP291 definition (mode 45) as KP NEW; compare a known chart before relying on agreement with another program.\n\n'
             'Charts use Placidus cusps and the mean lunar node. Placidus cannot be calculated at some polar locations; use a chart with a known reference for validation.\n\n'
             'Offline atlas: GeoNames, CC BY 4.0. Not every village or address is indexed. See START HERE and Third-Party Notices for details.\n\n'
             f'Local chart library: {self.library.path}')
        self.label(b,txt,wraplength=520,justify='left',fg=MUTED,size=10).pack(fill='x',pady=(8,8))
        self.button(b,'Close',d.destroy,'ghost').pack(side='right')


def main():
    root=tk.Tk()
    if os.name=='nt':
        try: root.iconbitmap(default=str(HERE/'kp-studio.ico'))
        except tk.TclError: pass
    def report(_typ,e,tb):
        logging_error=''.join(traceback.format_exception(_typ,e,tb))
        try: (local_folder()/'last-error.txt').write_text(logging_error,encoding='utf-8')
        except OSError: pass
        messagebox.showerror('KP Studio',str(e) or 'Unexpected error. See last-error.txt in your library folder.',parent=root)
    root.report_callback_exception=report
    try:
        App(root)
    except Exception as e:
        report(type(e),e,e.__traceback__); root.destroy(); return
    root.mainloop()


if __name__=='__main__':
    main()