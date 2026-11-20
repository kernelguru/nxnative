"""A small client of RomM's REST API (https://docs.romm.app): the Switch games of a RomM library, and their files downloaded into a game folder.

Authentication: a client API token (rmm_..., sent as a bearer token) or a username and password (HTTP Basic), as RomM accepts both on its API.
Endpoints used: GET /api/platforms, GET /api/roms (search_term, platform_ids, limit, offset; a page {items, total} or, on older servers, a list), GET /api/roms/{id},
GET /api/roms/{id}/content/{file_name} (with file_ids= one file of a multi-file ROM; a ROM of several files without it comes as a zip).
"""
import os, re, shutil, zipfile
from pathlib import Path
from urllib.parse import quote
import httpx

SWITCH_SLUGS = ('switch', 'nintendo-switch', 'switch2', 'nintendo-switch-2')
GAME_SUFFIXES = ('.nsp', '.nsz', '.xci', '.xcz', '.nup')


class RommError(Exception): pass


class Romm:
    def __init__(self, url, token='', user='', password='', timeout=60):
        if not url: raise RommError('no RomM address set (ROMM_URL, or the settings in the web interface)')
        self.base = url.rstrip('/')
        if self.base.endswith('/api'): self.base = self.base[:-4]
        headers = {'Accept': 'application/json'}
        auth = None
        if token: headers['Authorization'] = f'Bearer {token}'
        elif user: auth = (user, password)
        self.http = httpx.Client(base_url=self.base, headers=headers, auth=auth, timeout=timeout, follow_redirects=True)

    def _get(self, path, **params):
        try: r = self.http.get(path, params={k: v for k, v in params.items() if v not in (None, '')})
        except httpx.HTTPError as e: raise RommError(f'RomM at {self.base} does not answer: {e}') from e
        if r.status_code in (401, 403): raise RommError(f'RomM refused the login ({r.status_code}): check the token or the user and password')
        if r.status_code >= 400: raise RommError(f'RomM {path}: HTTP {r.status_code} {r.text[:200]}')
        return r.json()

    def platforms(self):
        return self._get('/api/platforms')

    def switch_platforms(self):
        return [p for p in self.platforms() if (p.get('slug') or '').lower() in SWITCH_SLUGS or 'switch' in (p.get('name') or '').lower()]

    def search(self, term='', limit=60):
        ids = [p['id'] for p in self.switch_platforms()]
        if not ids: raise RommError('RomM has no Nintendo Switch platform')
        out = []
        for pid in ids:
            data = self._get('/api/roms', search_term=term, platform_ids=pid, platform_id=pid, limit=limit, offset=0, order_by='name', order_dir='asc')
            items = data.get('items', []) if isinstance(data, dict) else data
            out += [r for r in items if r.get('platform_id') in (None, pid)]
        return [self.summary(r) for r in out]

    @staticmethod
    def summary(r):
        files = r.get('files') or []
        return {'id': r.get('id'), 'name': r.get('name') or r.get('fs_name_no_ext') or r.get('fs_name'), 'fs_name': r.get('fs_name'),
                'platform': r.get('platform_slug') or r.get('platform_display_name'), 'size': r.get('fs_size_bytes') or sum(f.get('file_size_bytes') or 0 for f in files),
                'files': [{'id': f.get('id'), 'name': f.get('file_name'), 'size': f.get('file_size_bytes')} for f in files],
                'cover': r.get('path_cover_small') or r.get('url_cover')}

    def rom(self, rom_id):
        return self._get(f'/api/roms/{int(rom_id)}')

    def download(self, rom_id, dest, progress=None, stop=lambda: False):
        """The ROM's game files (NSP / NSZ / XCI / XCZ / NUP) into dest; returns the files written"""
        r = self.rom(rom_id)
        dest = Path(dest); dest.mkdir(parents=True, exist_ok=True)
        files = [f for f in (r.get('files') or []) if (f.get('file_name') or '').lower().endswith(GAME_SUFFIXES)]
        jobs = [(f['file_name'], f.get('file_size_bytes'), {'file_ids': f['id']}) for f in files] if files else [(r.get('fs_name'), r.get('fs_size_bytes'), {})]
        written = []
        for name, size, params in jobs:
            if not name: raise RommError(f'ROM {rom_id} names no file')
            safe = re.sub(r'[/\\\0]', '_', name)
            part = dest / f'.{safe}.part'
            url = f'/api/roms/{int(rom_id)}/content/{quote(name)}'
            try:
                with self.http.stream('GET', url, params=params, timeout=httpx.Timeout(60, read=600)) as resp:
                    if resp.status_code >= 400: raise RommError(f'download of {name}: HTTP {resp.status_code}')
                    total = int(resp.headers.get('content-length') or size or 0); got = 0
                    zipped = 'zip' in (resp.headers.get('content-type') or '') and not name.lower().endswith('.zip')
                    with open(part, 'wb') as f:
                        for chunk in resp.iter_bytes(8 << 20):
                            if stop(): raise RommError('stopped')
                            f.write(chunk); got += len(chunk)
                            if progress: progress(name, got, total)
            except httpx.HTTPError as e:
                part.unlink(missing_ok=True); raise RommError(f'download of {name} failed: {e}') from e
            except BaseException:
                part.unlink(missing_ok=True); raise
            if zipped or zipfile.is_zipfile(part) and not safe.lower().endswith(GAME_SUFFIXES):
                with zipfile.ZipFile(part) as z:
                    for m in z.infolist():
                        base = os.path.basename(m.filename)
                        if m.is_dir() or not base.lower().endswith(GAME_SUFFIXES): continue
                        with z.open(m) as src, open(dest / base, 'wb') as out: shutil.copyfileobj(src, out, 8 << 20)
                        written.append(dest / base)
                part.unlink()
            else:
                part.rename(dest / safe); written.append(dest / safe)
        if not written: raise RommError(f'ROM {rom_id} has no NSP, NSZ, XCI, XCZ or NUP file')
        return written
