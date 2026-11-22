"""The Linux builder's web interface (linux/README.md): setup (the player's Xcode .xip, Metal tools, fonts, prod.keys), game folders (uploads, RomM imports), IPA builds.

Everything lives in NXN_DATA (/data in the container):
  toolchain/   what linux/setup/setup.py installs          keys/prod.keys   the player's keys (never sent back, never logged)
  games/<name>/   one game's NSP / NSZ / XCI files (base, updates, DLC), and the build folder the NSP script keeps beside them (<title ID>_build: extraction, shader cache)
  builds/<id>/    one job: job.json, log.jsonl (the NSP script's --progress json events and every other output line)
  ipa/            finished IPAs (also copied to NXN_IPA_EXPORT when set)
  uploads/        files on their way in      settings.json   RomM address and login set here (the environment's ROMM_* take precedence)
One job runs at a time (a build uses every core); the others wait in order.
"""
import base64, hmac, json, os, re, secrets, shutil, signal, subprocess, sys, threading, time, uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
LINUX = HERE.parent; REPO = LINUX.parent
sys.path.insert(0, str(LINUX / 'lib')); sys.path.insert(0, str(LINUX / 'setup')); sys.path.insert(0, str(HERE))
import nxn, setup as nxsetup, romm as rommlib

DATA = Path(os.environ.get('NXN_DATA', '/data'))
GAMES, BUILDS, IPA, KEYS, UPLOADS = DATA / 'games', DATA / 'builds', DATA / 'ipa', DATA / 'keys', DATA / 'uploads'
SETTINGS = DATA / 'settings.json'
for d in (GAMES, BUILDS, IPA, KEYS, UPLOADS): d.mkdir(parents=True, exist_ok=True)
GAME_SUFFIXES = ('.nsp', '.nsz', '.xci', '.xcz', '.nup')
NAME = re.compile(r'^[\w][\w .,()\[\]&+\'!-]{0,120}$')
PASSWORD = os.environ.get('NXN_PASSWORD', '')

app = FastAPI(title='nxnative Linux builder', docs_url=None, redoc_url=None)


@app.middleware('http')
async def basic_auth(request: Request, call_next):
    if PASSWORD:
        h = request.headers.get('authorization', '')
        ok = False
        if h.lower().startswith('basic '):
            try: user, _, pw = base64.b64decode(h[6:]).decode().partition(':'); ok = hmac.compare_digest(pw, PASSWORD)
            except ValueError: ok = False
        if not ok: return Response(status_code=401, headers={'WWW-Authenticate': 'Basic realm="nxnative"'})
    return await call_next(request)


def safe_name(n, what='name'):
    n = (n or '').strip()
    if not NAME.match(n) or '..' in n: raise HTTPException(400, f'invalid {what}: {n!r}')
    return n


def settings():
    try: s = json.loads(SETTINGS.read_text())
    except (OSError, ValueError): s = {}
    for k, env in (('romm_url', 'ROMM_URL'), ('romm_token', 'ROMM_TOKEN'), ('romm_user', 'ROMM_USER'), ('romm_password', 'ROMM_PASSWORD'), ('ipa_export', 'NXN_IPA_EXPORT')):
        if os.environ.get(env): s[k] = os.environ[env]
    return s


# ---- jobs: one at a time, each with a log of JSON lines ----
class Job:
    def __init__(self, kind, title, run, meta=None):
        self.id = time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6]
        self.kind, self.title, self.run, self.meta = kind, title, run, meta or {}
        self.dir = BUILDS / self.id; self.dir.mkdir(parents=True)
        self.state, self.started, self.ended, self.result, self.error = 'queued', None, None, None, None
        self.proc, self.stop_asked, self.stage, self.lock = None, False, None, threading.Lock()
        self.save()

    def info(self):
        return {'id': self.id, 'kind': self.kind, 'title': self.title, 'state': self.state, 'started': self.started, 'ended': self.ended,
                'result': self.result, 'error': self.error, 'stage': self.stage, 'meta': self.meta}

    def save(self): (self.dir / 'job.json').write_text(json.dumps(self.info(), indent=1))

    def log(self, obj):
        if isinstance(obj, str): obj = {'ev': 'line', 'msg': obj}
        obj.setdefault('t', time.time())
        ev = obj.get('ev')
        if ev == 'stage': self.stage = {k: v for k, v in obj.items() if k != 't'}
        elif ev in ('progress', 'step'): self.stage = {**(self.stage or {}), **{k: v for k, v in obj.items() if k not in ('t', 'ev')}}
        with self.lock, open(self.dir / 'log.jsonl', 'a') as f: f.write(json.dumps(obj, ensure_ascii=False) + '\n')

    def command(self, cmd, env=None, cwd=None):
        """Runs cmd, every output line into the log (JSON lines as they are); raises on a non-zero exit"""
        self.log({'ev': 'cmd', 'msg': ' '.join(str(c) for c in cmd)})
        self.proc = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', bufsize=1,
                                     env=env, cwd=cwd, start_new_session=True)
        for line in self.proc.stdout:
            line = line.rstrip('\n')
            if not line: continue
            if line.startswith('{'):
                try: self.log(json.loads(line)); continue
                except ValueError: pass
            self.log(line)
        rc = self.proc.wait(); self.proc = None
        if self.stop_asked: raise RuntimeError('stopped')
        if rc: raise RuntimeError(f'{Path(str(cmd[1])).name if len(cmd) > 1 else cmd[0]} exited with code {rc}')

    def stop(self):
        self.stop_asked = True
        if self.state == 'queued': self.state = 'stopped'; self.save(); return
        p = self.proc
        if p:
            try: os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError: pass


class Queue:
    def __init__(self):
        self.jobs, self.cv = {}, threading.Condition()
        for d in sorted(BUILDS.glob('*/job.json')):   # earlier jobs, read only
            try:
                i = json.loads(d.read_text())
                j = Job.__new__(Job); j.__dict__.update(i); j.dir = d.parent; j.run = None; j.proc = None; j.lock = threading.Lock(); j.stop_asked = False
                if j.state in ('queued', 'running'): j.state, j.error = 'failed', 'the container stopped during the job'
                self.jobs[j.id] = j
            except (OSError, ValueError): pass
        threading.Thread(target=self.worker, daemon=True).start()

    def add(self, job):
        with self.cv: self.jobs[job.id] = job; self.cv.notify()
        return job

    def worker(self):
        while True:
            with self.cv:
                job = None
                while not job:
                    job = next((j for j in sorted(self.jobs.values(), key=lambda j: j.id) if j.state == 'queued' and j.run), None)
                    if not job: self.cv.wait()
            job.state, job.started = 'running', time.time(); job.save()
            try:
                job.result = job.run(job); job.state = 'done'
            except Exception as e:
                job.state, job.error = ('stopped' if job.stop_asked else 'failed'), str(e)
                job.log({'ev': 'error', 'msg': str(e)})
            job.ended = time.time(); job.save()


QUEUE = Queue()


def tool_env():
    env = dict(os.environ, PATH=f'{LINUX / "bin"}:{os.environ.get("PATH", "")}', PYTHONUNBUFFERED='1')
    for kind, var in (('std', 'STD_FONT'), ('letters', 'LETTERS_FONT')):
        p, _ = nxsetup.font(kind)
        if p: env[var] = str(p)
    return env


def setup_cmd(*args):
    return [sys.executable, str(LINUX / 'setup/setup.py'), *args]


# ---- setup ----
@app.get('/api/status')
def status():
    s = nxsetup.status()
    s['keys'] = (KEYS / 'prod.keys').is_file()
    s['romm'] = bool(settings().get('romm_url'))
    return s


@app.put('/api/setup/{kind}/{filename}')
async def setup_upload(kind: str, filename: str, request: Request):
    """The body is the file (streamed to disk): kind xcode (.xip), metal (installer or zip), font (.ttf), keys (prod.keys)"""
    if kind not in ('xcode', 'metal', 'font', 'keys'): raise HTTPException(404)
    filename = safe_name(Path(filename).name, 'file name')
    dst = (KEYS / 'prod.keys') if kind == 'keys' else UPLOADS / f'{kind}-{filename}'
    await receive(request, dst)
    if kind == 'keys':
        os.chmod(dst, 0o600)
        text = dst.read_text(errors='replace')
        if 'header_key' not in text: dst.unlink(); raise HTTPException(400, 'that is not a prod.keys file (no header_key)')
        return {'ok': True}
    if kind == 'font':
        try:
            named = UPLOADS / filename; dst.rename(named); nxsetup.install_fonts([named]); named.unlink(missing_ok=True)
        except SystemExit: raise HTTPException(400, 'give Arial Unicode.ttf or Arial Rounded Bold.ttf')
        return {'ok': True}
    def run(job):
        try: job.command(setup_cmd(kind, dst), env=tool_env())
        finally: dst.unlink(missing_ok=True)
        if kind in ('xcode', 'metal') and nxsetup.status()['xcode']['ios_sdk'] and nxsetup.status()['metal']['installed']:
            job.command(setup_cmd('chain'), env=tool_env())
    return QUEUE.add(Job('setup', f'install {kind}: {filename}', run)).info()


@app.post('/api/setup/{step}')
def setup_step(step: str):
    if step not in ('wine', 'chain'): raise HTTPException(404)
    return QUEUE.add(Job('setup', f'setup {step}', lambda job: job.command(setup_cmd(step), env=tool_env()))).info()


async def receive(request, dst):
    tmp = dst.with_name(f'.{dst.name}.{secrets.token_hex(4)}.part')
    try:
        with open(tmp, 'wb') as f:
            async for chunk in request.stream(): f.write(chunk)
        tmp.replace(dst)
    except BaseException:
        tmp.unlink(missing_ok=True); raise


# ---- games ----
def game_dir(name): return GAMES / safe_name(name, 'game folder')


@app.get('/api/games')
def games():
    out = []
    for d in sorted(p for p in GAMES.iterdir() if p.is_dir()):
        files = [{'name': f.name, 'size': f.stat().st_size} for f in sorted(d.iterdir()) if f.is_file() and f.suffix.lower() in GAME_SUFFIXES]
        ipas = sorted((f.name for f in IPA.glob(f'{d.name}*.ipa')), reverse=True)
        work = [w.name for w in d.glob('*_build') if w.is_dir()]
        out.append({'name': d.name, 'files': files, 'size': sum(f['size'] for f in files), 'ipas': ipas, 'build_folders': work})
    return out


@app.post('/api/games')
async def new_game(request: Request):
    name = safe_name((await request.json()).get('name'), 'game folder')
    (GAMES / name).mkdir(exist_ok=True)
    return {'name': name}


@app.put('/api/games/{name}/files/{filename}')
async def upload_game_file(name: str, filename: str, request: Request):
    d = game_dir(name); d.mkdir(exist_ok=True)
    filename = safe_name(Path(filename).name, 'file name')
    if not filename.lower().endswith(GAME_SUFFIXES): raise HTTPException(400, f'{filename}: game files are NSP, NSZ, XCI, XCZ or NUP')
    await receive(request, d / filename)
    return {'ok': True}


@app.delete('/api/games/{name}/files/{filename}')
def delete_game_file(name: str, filename: str):
    f = game_dir(name) / safe_name(filename, 'file name')
    if not f.is_file(): raise HTTPException(404)
    f.unlink(); return {'ok': True}


@app.delete('/api/games/{name}')
def delete_game(name: str, work_only: bool = False):
    d = game_dir(name)
    if not d.is_dir(): raise HTTPException(404)
    if work_only:
        for w in d.glob('*_build'): shutil.rmtree(w, ignore_errors=True)
    else: shutil.rmtree(d)
    return {'ok': True}


# ---- RomM ----
def romm_client():
    s = settings()
    try: return rommlib.Romm(s.get('romm_url'), s.get('romm_token', ''), s.get('romm_user', ''), s.get('romm_password', ''))
    except rommlib.RommError as e: raise HTTPException(400, str(e))


@app.get('/api/settings')
def get_settings():
    s = settings()
    return {'romm_url': s.get('romm_url', ''), 'romm_user': s.get('romm_user', ''), 'romm_token_set': bool(s.get('romm_token')),
            'romm_password_set': bool(s.get('romm_password')), 'ipa_export': s.get('ipa_export', ''),
            'from_env': [k for k in ('ROMM_URL', 'ROMM_TOKEN', 'ROMM_USER', 'ROMM_PASSWORD', 'NXN_IPA_EXPORT') if os.environ.get(k)]}


@app.post('/api/settings')
async def set_settings(request: Request):
    body = await request.json()
    try: s = json.loads(SETTINGS.read_text())
    except (OSError, ValueError): s = {}
    for k in ('romm_url', 'romm_user', 'ipa_export'):
        if k in body: s[k] = (body[k] or '').strip()
    for k in ('romm_token', 'romm_password'):   # write-only: empty keeps the stored value, '-' clears it
        if body.get(k) == '-': s.pop(k, None)
        elif body.get(k): s[k] = body[k]
    SETTINGS.write_text(json.dumps(s, indent=1)); os.chmod(SETTINGS, 0o600)
    return get_settings()


@app.get('/api/romm/search')
def romm_search(q: str = ''):
    try: return romm_client().search(q)
    except rommlib.RommError as e: raise HTTPException(502, str(e))


@app.post('/api/romm/import')
async def romm_import(request: Request):
    body = await request.json()
    ids = [int(i) for i in body.get('ids') or []]
    if not ids: raise HTTPException(400, 'no ROM chosen')
    name = safe_name(body.get('game') or '', 'game folder')
    client = romm_client()
    def run(job):
        d = GAMES / name; d.mkdir(exist_ok=True)
        last = [0.0]
        def progress(fname, got, total):
            if time.time() - last[0] > 1 or got == total:
                last[0] = time.time(); job.log({'ev': 'progress', 'name': f'RomM: {fname}', 'done': got, 'total': total, 'unit': 'B'})
        for i in ids:
            files = client.download(i, d, progress, stop=lambda: job.stop_asked)
            for f in files: job.log(f'saved {f.name} ({f.stat().st_size / 2**30:.2f} GB) in {name}/')
        return {'game': name}
    return QUEUE.add(Job('romm', f'RomM import → {name}', run, {'game': name, 'roms': ids})).info()


# ---- builds ----
@app.get('/api/profiles')
def profiles():
    r = subprocess.run([sys.executable, str(REPO / 'dm/games/game.py'), 'list'], capture_output=True, text=True, cwd=REPO)
    return [dict(zip(('id', 'build', 'name'), l.split('\t')[:3])) for l in r.stdout.splitlines() if l.strip()]


@app.post('/api/builds')
async def start_build(request: Request):
    o = await request.json()
    name = safe_name(o.get('game'), 'game folder')
    d = GAMES / name
    if not d.is_dir() or not any(f.suffix.lower() in GAME_SUFFIXES for f in d.iterdir()): raise HTTPException(400, f'{name} holds no game files')
    if not (KEYS / 'prod.keys').is_file() and not any(f.suffix.lower() == '.nup' for f in d.iterdir()): raise HTTPException(400, 'prod.keys is missing (setup)')
    st = nxsetup.status()
    if st['chain_running']: raise HTTPException(409, 'the translation chain lock is being written for these tools (a minute after the container starts): try again shortly')
    if not st['ready']: raise HTTPException(400, 'the toolchain is not ready: finish the setup first')
    stamp = time.strftime('%Y%m%d-%H%M')
    out = IPA / f'{name}-{stamp}.ipa'
    cmd = [sys.executable, str(REPO / 'scripts/build_ipa_from_nsp.py'), str(d), '-k', str(KEYS / 'prod.keys'), '--progress', 'json', '-o', str(out)]
    flag = lambda k, opt: cmd.extend([opt, str(o[k])]) if o.get(k) not in (None, '') else None
    flag('language', '-l'); flag('package', '-p'); flag('title_id', '--title-id'); flag('app_name', '--app-name'); flag('gpu', '--gpu'); flag('romfs', '--romfs')
    for k, opt in (('show_fps', '--show-fps'), ('keep_extracted', '--keep-extracted'), ('motion', '--motion'), ('shader_files', '--shader-files')):
        if o.get(k) is True: cmd.append(opt)
        elif o.get(k) is False: cmd.append('--no-' + opt[2:])
    if o.get('allow_other_metal'): cmd.append('--allow-other-metal')
    if o.get('provisional'): cmd.append('--provisional')
    def run(job):
        job.command(cmd, env=tool_env(), cwd=REPO)
        if not out.is_file(): raise RuntimeError('the build ended without an IPA')
        exp = settings().get('ipa_export')
        if exp:
            try: Path(exp).mkdir(parents=True, exist_ok=True); shutil.copy2(out, Path(exp) / out.name); job.log(f'copied to {exp}/{out.name}')
            except OSError as e: job.log(f'[RED] copy to {exp} failed: {e}')
        return {'ipa': out.name, 'size': out.stat().st_size}
    return QUEUE.add(Job('build', f'IPA: {name}', run, {'game': name, 'options': o, 'ipa': out.name})).info()


@app.get('/api/jobs')
def jobs(limit: int = 50):
    return [j.info() for j in sorted(QUEUE.jobs.values(), key=lambda j: j.id, reverse=True)[:limit]]


@app.get('/api/jobs/{jid}')
def job(jid: str):
    j = QUEUE.jobs.get(jid)
    if not j: raise HTTPException(404)
    return j.info()


@app.get('/api/jobs/{jid}/log')
def job_log(jid: str, offset: int = 0):
    """The log from byte offset on, as a list of events, and the offset to ask from next"""
    j = QUEUE.jobs.get(jid)
    if not j: raise HTTPException(404)
    p = j.dir / 'log.jsonl'
    if not p.is_file(): return {'events': [], 'offset': 0, 'state': j.state}
    with open(p, 'rb') as f:
        f.seek(offset); data = f.read(2 << 20)
    cut = data.rfind(b'\n') + 1
    ev = [json.loads(l) for l in data[:cut].splitlines() if l.strip()]
    return {'events': ev, 'offset': offset + cut, 'state': j.state, 'info': j.info()}


@app.post('/api/jobs/{jid}/stop')
def job_stop(jid: str):
    j = QUEUE.jobs.get(jid)
    if not j: raise HTTPException(404)
    j.stop(); return j.info()


@app.get('/api/ipa/{filename}')
def get_ipa(filename: str):
    f = IPA / safe_name(filename, 'file name')
    if not f.is_file() or f.suffix != '.ipa': raise HTTPException(404)
    return FileResponse(f, filename=f.name, media_type='application/octet-stream')


@app.delete('/api/ipa/{filename}')
def delete_ipa(filename: str):
    f = IPA / safe_name(filename, 'file name')
    if not f.is_file(): raise HTTPException(404)
    f.unlink(); return {'ok': True}


@app.get('/api/ipas')
def ipas():
    return [{'name': f.name, 'size': f.stat().st_size, 'time': f.stat().st_mtime} for f in sorted(IPA.glob('*.ipa'), key=lambda f: f.stat().st_mtime, reverse=True)]


app.mount('/', StaticFiles(directory=HERE / 'static', html=True), name='static')
