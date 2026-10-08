#!/usr/bin/env python3
"""Grottle(TM) skin submission checker (stdlib only).

Runs the automatic checks from the skin-to-site plan on a skin zip, on a copy, without
ever importing or running anything from the zip.

Ported from the released Grottle(TM) v0.13.1 code (pinned on the grottle.app site build):
  core/engine/skin_dl.py       sha256 1d57faf69e944e8cbbcf778640cb26c693177afc6da0c442df73aba4af0d543c
      share_id(), _unzip() limits and unsafe-path rule, template/no-code rule, golden sha check
  core/checker/check_skin.py   sha256 68d515e52cb4f284e69828e1e6f897992eca0513b83f18b384a0d4b29862697a
      safety_scan(), package size cap, golden/ file types, bundled font + licence checks
  core/engine/grottle_engine.py sha256 2d30fef48e4070abe82cbdfebf362b73085bc4f53bf28894a751ce3feadd4547
      load_manifest() api_version rule, golden_status()
  skin_api/slots.json          sha256 37dc0e3f519a2dacc8f9beec0819b58773344ad21f0a72ffac41a2b5b2ddd245
      manifest_required, bands, rules (banned elements, size_cap_bytes)

NOT done here (they need the Grottle(TM) engine, Pillow, fontTools and rsvg): rendering every
layout through the full checker and re-rendering the locked goldens within tolerance. Those run
at review time with the released v0.13.1 code before anything is listed.

Usage:
  check_skin.py SKIN.zip [--expect-share-id g1-xxxxxxxxxxxx] [--json out.json] [--markdown out.md]
  check_skin.py --issue-body body.md [--json ...] [--markdown ...]
      (reads a GitHub issue-form body, downloads the attached zip from GitHub's attachment
       host only, and uses the share ID typed in the form)
Exit code: 0 = PASS, 1 = FAIL.
"""
import hashlib, io, json, os, re, shutil, sys, tempfile, urllib.parse, urllib.request, zipfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

# ---- limits and rules, copied from v0.13.1 (skin_dl.py + skin_api/slots.json) ----
MAX_DOWNLOAD = 8000000        # skin_dl.MAX_DOWNLOAD (zip size)
MAX_UNZIPPED = 12000000       # skin_dl.MAX_UNZIPPED
MAX_FILES = 200               # skin_dl.MAX_FILES
SIZE_CAP = 307200             # slots.json rules.size_cap_bytes (golden/ not counted)
SUPPORTED_API_VERSIONS = [1]  # slots.json supported_api_versions
MANIFEST_REQUIRED = ['id', 'name', 'creator', 'version', 'api_version', 'canvas', 'fonts',
                     'license', 'tier', 'layouts', 'palette']
BANDS = {'pace_band': ['green', 'yellow', 'red', 'none'], 'reserve.band': ['normal', 'red', 'unset']}
BANNED = {'script', 'foreignObject', 'iframe', 'object', 'embed', 'audio', 'video'}
EXT = re.compile(r'(?i)(https?:|javascript:|file:|data:text/html|^//)')
# code suffixes: union of skin_dl.verify() and check_skin.py template-tier rule
CODE_SUFFIXES = {'.py', '.pyc', '.so', '.sh', '.js', '.exe'}
GOLDEN_SUFFIXES = {'.png', '.svg', '.json', '.sha256', '.md'}   # check_skin.py golden/ rule
# extra (submission-site) allowlist of file types anywhere in the package
ALLOWED_SUFFIXES = {'.svg', '.json', '.ttf', '.otf', '.txt', '.md', '.png', '.sha256'}
ALLOWED_BARE_NAMES = {'LICENSE', 'LICENCE', 'README', 'COPYING'}
SHARE_ID_RE = re.compile(r'^g1-[0-9a-f]{12}$')
ATTACHMENT_HOSTS = {'github.com', 'objects.githubusercontent.com',
                    'user-images.githubusercontent.com', 'private-user-images.githubusercontent.com'}


class Report:
    def __init__(self):
        self.rows = []
        self.info = {}

    def add(self, name, ok, detail=''):
        self.rows.append(dict(check=name, result='PASS' if ok else 'FAIL', detail=detail))
        return ok

    @property
    def ok(self):
        return bool(self.rows) and all(r['result'] == 'PASS' for r in self.rows)


# ---- share ID: verbatim port of v0.13.1 skin_dl.share_id() ----
def share_id(d):
    d = Path(d); h = hashlib.sha256()
    for f in sorted(p for p in d.rglob('*') if p.is_file() and '__pycache__' not in p.parts
                    and p.relative_to(d).parts[0] != 'golden'):
        rel = f.relative_to(d).as_posix(); data = f.read_bytes()
        if rel == 'skin.json':
            m = json.loads(data); m['share_id'] = ''
            data = json.dumps(m, sort_keys=True, ensure_ascii=False).encode()
        h.update(rel.encode() + b'\x00' + hashlib.sha256(data).digest())
    return 'g1-' + h.hexdigest()[:12]


# ---- SVG safety: verbatim port of v0.13.1 check_skin.safety_scan() ----
def _local(tag):
    return tag.split('}')[-1]


def safety_scan(text, where):
    probs = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        return [f"{where}: not well-formed SVG/XML ({e})"], None
    for el in root.iter():
        tag = _local(el.tag)
        if tag in BANNED: probs.append(f"{where}: banned element <{tag}>")
        for k, v in el.attrib.items():
            a = _local(k).lower()
            if a.startswith('on'): probs.append(f"{where}: event handler attribute {a}=")
            if a in ('href', 'src') and not v.startswith('#'): probs.append(f'{where}: external reference {a}="{v[:60]}"')
            for u in re.findall(r'url\(([^)]*)\)', v):
                if not u.strip(' \'"').startswith('#'): probs.append(f"{where}: external url({u[:60]})")
            if EXT.search(v): probs.append(f'{where}: external URL in {a}="{v[:60]}"')
        if tag == 'style' and el.text and ('@import' in el.text or EXT.search(el.text)):
            probs.append(f"{where}: external import in <style>")
    return sorted(set(probs)), root


# ---- safe unzip (v0.13.1 skin_dl._unzip rules, but extracting member by member) ----
def safe_unzip(data, dest, rep):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        rep.add('zip opens', False, 'not a valid zip file'); return None
    infos = z.infolist()
    total = sum(i.file_size for i in infos)
    nfiles = sum(1 for i in infos if not i.is_dir())
    rep.add('safe unzip: file count', len(infos) <= MAX_FILES, f"{nfiles} files, {len(infos)} entries (limit {MAX_FILES})")
    rep.add('safe unzip: unpacked size', total <= MAX_UNZIPPED, f"{total:,} B unpacked (limit {MAX_UNZIPPED:,} B)")
    bad = []
    for i in infos:
        n = i.filename
        if (n.startswith('/') or '\\' in n or re.match(r'^[A-Za-z]:', n) or '..' in PurePosixPath(n).parts
                or (i.external_attr >> 16) & 0o170000 == 0o120000):
            bad.append(n)
    rep.add('safe unzip: no absolute or ../ paths, no links', not bad, ', '.join(bad[:5]))
    if bad or len(infos) > MAX_FILES or total > MAX_UNZIPPED:
        return None
    dest = Path(dest).resolve()
    for i in infos:
        out = (dest / i.filename).resolve()
        if dest not in out.parents and out != dest:
            rep.add('safe unzip: paths stay inside the folder', False, i.filename); return None
        if i.is_dir():
            out.mkdir(parents=True, exist_ok=True); continue
        out.parent.mkdir(parents=True, exist_ok=True)
        with z.open(i) as src, open(out, 'wb') as dst:
            shutil.copyfileobj(src, dst)
    kids = list(dest.iterdir())
    return kids[0] if len(kids) == 1 and kids[0].is_dir() and (kids[0] / 'skin.json').exists() else dest


def check_zip(zip_path, expect_share_id=None):
    rep = Report()
    data = Path(zip_path).read_bytes()
    rep.info['zip_sha256'] = hashlib.sha256(data).hexdigest()
    rep.info['zip_size'] = len(data)
    rep.add('zip size', len(data) <= MAX_DOWNLOAD, f"{len(data):,} B (limit {MAX_DOWNLOAD:,} B)")
    tmp = Path(tempfile.mkdtemp(prefix='grottle_submission_'))
    try:
        skin = safe_unzip(data, tmp, rep)
        if skin is None:
            return rep
        _check_dir(skin, rep, expect_share_id)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return rep


def _check_dir(B, rep, expect_share_id):
    A = rep.add
    files = [p for p in B.rglob('*') if p.is_file()]
    links = [p for p in B.rglob('*') if p.is_symlink()]
    A('no links in package', not links, ', '.join(p.name for p in links))
    # --- manifest (grottle_engine.load_manifest + check_skin required fields) ---
    mp = B / 'skin.json'
    if not mp.is_file():
        A('skin.json present', False, 'no skin.json at the top of the skin folder (zip one folder containing skin.json)')
        return
    try:
        man = json.loads(mp.read_text(encoding='utf-8'))
        if not isinstance(man, dict): raise ValueError('not a JSON object')
    except Exception as e:
        A('skin.json readable', False, f"unreadable manifest: {e}"); return
    av = man.get('api_version')
    A('manifest + api_version', isinstance(av, int) and av in SUPPORTED_API_VERSIONS,
      f"api v{av}" if av in SUPPORTED_API_VERSIONS else f"api_version {av!r} not supported (supported: {SUPPORTED_API_VERSIONS})")
    miss = [k for k in MANIFEST_REQUIRED if k not in man or man[k] in ('', None, [])]
    A('manifest required fields', not miss, 'missing: ' + ', '.join(miss) if miss else 'all present')
    rep.info.update({k: man.get(k) for k in ('id', 'name', 'version', 'creator', 'license', 'share_id')})
    pal = man.get('palette') if isinstance(man.get('palette'), dict) else {}
    pm = [f"{b}:{c}" for b, cs in BANDS.items() for c in cs if c not in (pal.get(b) or {})]
    A('palette has a colour for every band', not pm, 'missing: ' + ', '.join(pm) if pm else '')
    # --- tier + no code (skin_dl.verify + check_skin template rule) ---
    A('tier is template', man.get('tier') == 'template', f"tier {man.get('tier')!r}" + ('' if man.get('tier') == 'template' else ' (user skins must be template tier, no code)'))
    code = [p.relative_to(B).as_posix() for p in files if p.suffix.lower() in CODE_SUFFIXES or p.name == '__pycache__']
    A('no code files (.py/.pyc/.js/.sh/.so/.exe)', not code, ', '.join(code[:8]))
    odd = [p.relative_to(B).as_posix() for p in files
           if p.suffix.lower() not in ALLOWED_SUFFIXES and p.name not in ALLOWED_BARE_NAMES]
    A('only allowed file types (svg, json, ttf/otf, png, txt/md, sha256)', not odd, ', '.join(odd[:8]))
    # --- size cap (check_skin: golden/ not counted) ---
    sz = sum(p.stat().st_size for p in files if '__pycache__' not in p.parts and p.relative_to(B).parts[0] != 'golden')
    A('package size cap (golden/ not counted)', sz <= SIZE_CAP, f"{sz:,} B (cap {SIZE_CAP:,} B)")
    # --- required art files ---
    tmpl = man.get('templates') if isinstance(man.get('templates'), dict) else {}
    lays = man.get('layouts') if isinstance(man.get('layouts'), list) else []
    tm = []
    for lay in lays:
        f = tmpl.get(lay)
        if not f: tm.append(f"{lay}: no template named")
        elif PurePosixPath(f).is_absolute() or '..' in PurePosixPath(f).parts or not (B / f).is_file(): tm.append(f"{lay}: {f} missing")
        elif not str(f).lower().endswith('.svg'): tm.append(f"{lay}: {f} is not .svg")
    A('art template present for every layout', not tm and bool(lays), '; '.join(tm) if tm else ', '.join(f"{k}={v}" for k, v in tmpl.items()))
    # --- art safety (check_skin: every .svg, placeholders neutralised) ---
    probs = []
    for p in sorted(B.rglob('*.svg')):
        D = p.read_text(encoding='utf-8', errors='replace')
        if not D.lstrip().startswith('<svg') and not D.lstrip().startswith('<?xml'):
            D = f'<svg xmlns="http://www.w3.org/2000/svg">{D}</svg>'
        D = re.sub(r'\{\{[^{}]*\}\}', '0', D)
        probs += safety_scan(D, p.relative_to(B).as_posix())[0]
    A('art safety (no scripts / handlers / external URLs)', not probs, '; '.join(probs[:6]))
    # --- fonts carry their licence (check_skin bundled-font checks) ---
    fonts = [f for f in (man.get('fonts') or []) if isinstance(f, dict) and f.get('file')]
    if fonts:
        bad = [f['file'] for f in fonts if not (B / f['file']).is_file() or Path(f['file']).suffix.lower() not in ('.ttf', '.otf')]
        A('bundled font files present', not bad, 'missing/not a font: ' + ', '.join(bad) if bad else ', '.join(Path(f['file']).name for f in fonts))
        lic = man.get('font_license')
        A('bundled font licence shipped', bool(lic) and (B / lic).is_file() and all(f.get('license') for f in fonts),
          (f"{lic}; " + ', '.join(sorted({str(f.get('license')) for f in fonts}))) if lic else 'manifest needs font_license + a licence per font')
    else:
        A('bundled fonts', True, 'none bundled')
    # --- share ID recomputed (skin_dl.share_id) and must match ---
    sid = share_id(B); rep.info['share_id_recomputed'] = sid
    A('share ID recomputed matches skin.json', man.get('share_id') == sid, f"skin.json {man.get('share_id')!r}, recomputed {sid}")
    if expect_share_id is not None:
        e = expect_share_id.strip().lower()
        A('share ID matches the one in the submission', e == sid, f"submitted {e!r}, recomputed {sid}")
    # --- golden/ files + locked goldens intact (check_skin + grottle_engine.golden_status + skin_dl._golden_check) ---
    gd = B / 'golden'
    if gd.is_dir():
        gb = [p.relative_to(B).as_posix() for p in gd.rglob('*') if p.is_file() and p.suffix not in GOLDEN_SUFFIXES]
        A('golden/ holds only golden files', not gb, ', '.join(gb))
    meta = {}
    try:
        if (gd / 'GOLDEN.json').is_file(): meta = json.loads((gd / 'GOLDEN.json').read_text(encoding='utf-8'))
    except Exception:
        meta = {}
    sha = {}
    if (gd / 'GOLDEN.sha256').is_file():
        for line in (gd / 'GOLDEN.sha256').read_text().splitlines():
            if line.strip():
                parts = line.split(None, 1)
                if len(parts) == 2: sha[parts[1].strip()] = parts[0]
    locked = (gd / 'GOLDEN.sha256').is_file() and meta.get('status') == 'locked' and bool(meta.get('approved_by'))
    A('goldens locked and approved', locked, f"approved by {meta.get('approved_by')} ({meta.get('approval_ref')})" if locked
      else 'site skins must carry locked, approved goldens (golden/GOLDEN.json status "locked" + approved_by, and golden/GOLDEN.sha256)')
    if locked:
        sets = meta.get('sets') or []
        ms = [s for s in sets if f"GOLDEN_{s}.png" not in sha]
        alt = []
        for name, h in sha.items():
            f = gd / name
            if '/' in name or '\\' in name or not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != h:
                alt.append(name)
        A('golden files present and unaltered', bool(sets) and not ms and not alt,
          ('no golden for: ' + ', '.join(ms) + '; ' if ms else '') + ('missing or altered: ' + ', '.join(alt) if alt else
                                                                      f"{len(sha)} files match GOLDEN.sha256; sets {', '.join(sets)}"))


# ---- issue-form body handling ----
def parse_issue_body(body):
    secs, cur = {}, None
    for line in body.splitlines():
        m = re.match(r'^###\s+(.*?)\s*$', line)
        if m: cur = m.group(1); secs[cur] = []; continue
        if cur is not None: secs[cur].append(line)
    return {k: '\n'.join(v).strip() for k, v in secs.items()}


def zip_urls(text):
    urls = re.findall(r'https://[^\s)\]>"\']+', text or '')
    return [u for u in dict.fromkeys(urls) if urllib.parse.urlparse(u).hostname in ATTACHMENT_HOSTS
            and ('/user-attachments/' in u or '/files/' in u)]


def download(url, out):
    host = urllib.parse.urlparse(url).hostname
    if urllib.parse.urlparse(url).scheme != 'https' or host not in ATTACHMENT_HOSTS:
        raise ValueError(f"not a GitHub attachment URL: {url}")
    req = urllib.request.Request(url, headers={'User-Agent': 'grottle-skins-checker'})
    with urllib.request.urlopen(req, timeout=60) as r:
        if urllib.parse.urlparse(r.geturl()).scheme != 'https':
            raise ValueError('redirected to a non-https URL')
        data = r.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise ValueError(f"download larger than the {MAX_DOWNLOAD:,}-byte limit")
    Path(out).write_bytes(data)


def to_markdown(rep, note=''):
    i = rep.info
    head = '### ✅ Automatic checks passed' if rep.ok else '### ❌ Automatic checks failed'
    lines = [head, '']
    if note: lines += [note, '']
    if i.get('name'):
        lines += [f"**{i.get('name')}** {i.get('version')} by {i.get('creator')} · share ID `{i.get('share_id_recomputed', '?')}` · "
                  f"licence: {i.get('license')}", '']
    if i.get('zip_sha256'):
        lines += [f"zip sha256 `{i['zip_sha256']}` ({i.get('zip_size', 0):,} B)", '']
    lines += ['| Result | Check | Detail |', '|---|---|---|']
    for r in rep.rows:
        d = str(r['detail']).replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| {'✅' if r['result'] == 'PASS' else '❌'} {r['result']} | {r['check']} | {d} |")
    lines += ['', 'Still to do at review (released Grottle™ v0.13.1 code): full skin checker render pass, golden re-render within tolerance, '
              'and a person checking the name, the art, the Grottle™ sign and the licence.',
              '', '**Passing these checks does not list the skin. BCORN approves every listing.**']
    if not rep.ok:
        lines += ['', 'Fix the skin, then edit this issue and attach the new zip; the checks will run again.']
    return '\n'.join(lines) + '\n'


def main(argv):
    def opt(name):
        if name in argv:
            k = argv.index(name); v = argv[k + 1]; del argv[k:k + 2]; return v
        return None
    jout, mout, expect, body = opt('--json'), opt('--markdown'), opt('--expect-share-id'), opt('--issue-body')
    note = ''
    tmpdir = None
    if body is not None:
        secs = parse_issue_body(Path(body).read_text(encoding='utf-8'))
        expect = (secs.get('Share ID') or '').strip().strip('`').lower() or None
        urls = zip_urls(secs.get('Skin zip', ''))
        rep = Report()
        if expect is not None and not SHARE_ID_RE.match(expect):
            rep.add('share ID in the form looks like g1- + 12 hex', False, repr(expect))
        if len(urls) != 1:
            rep.add('exactly one zip attached', False,
                    f"found {len(urls)} GitHub attachment links in the Skin zip box" + ('' if urls else ' (drag the .zip into the box and wait for the link)'))
        if rep.rows:
            return finish(rep, jout, mout, note)
        tmpdir = tempfile.mkdtemp(prefix='grottle_dl_'); zp = Path(tmpdir) / 'submission.zip'
        try:
            download(urls[0], zp)
        except Exception as e:
            rep.add('zip downloads', False, str(e)[:200]); shutil.rmtree(tmpdir, ignore_errors=True)
            return finish(rep, jout, mout, note)
        note = f"Checked attachment: {urls[0]}"
    else:
        if not argv:
            print(__doc__); return 2
        zp = Path(argv[0])
    try:
        rep = check_zip(zp, expect)
    finally:
        if tmpdir: shutil.rmtree(tmpdir, ignore_errors=True)
    return finish(rep, jout, mout, note)


def finish(rep, jout, mout, note):
    for r in rep.rows:
        print(f"{r['result']}  {r['check']}" + (f"  — {r['detail']}" if r['detail'] else ''))
    print('OVERALL', 'PASS' if rep.ok else 'FAIL')
    if jout:
        Path(jout).write_text(json.dumps(dict(overall='PASS' if rep.ok else 'FAIL', info=rep.info, checks=rep.rows),
                                         indent=1, ensure_ascii=False), encoding='utf-8')
    if mout:
        Path(mout).write_text(to_markdown(rep, note), encoding='utf-8')
    return 0 if rep.ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
