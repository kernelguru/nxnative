"""Finished IPAs to a WebDAV folder, ipa-source's /dav/ (kernelguru/ipa-source: a file PUT into its top folder is imported into the source on its own).

Authentication: an API token of ipa-source (scope "upload" is enough), sent as a bearer token; or a user and password (HTTP Basic). The file is streamed, with its length
given (no chunked encoding, which some proxies refuse), so an IPA of many GB never sits in memory on either side.
"""
import os
from pathlib import Path
from urllib.parse import quote
import httpx


class DavError(Exception): pass


def _client(url, token='', user='', password=''):
    if not url: raise DavError('no WebDAV address set (settings, or IPA_SOURCE_DAV_URL)')
    if not url.startswith(('http://', 'https://')): raise DavError(f'{url}: the WebDAV address starts with http:// or https://')
    headers, auth = {}, None
    if token: headers['Authorization'] = f'Bearer {token}'
    elif user: auth = (user, password)
    else: raise DavError('no WebDAV login set: an ipa-source API token (scope "upload"), or a user and password')
    return httpx.Client(headers=headers, auth=auth, follow_redirects=False, timeout=httpx.Timeout(60, read=3600, write=600))


def folder(url):
    return url if url.endswith('/') else url + '/'


def check(url, token='', user='', password=''):
    """Whether the folder answers and takes the login: a PROPFIND of depth 0; returns a short description"""
    with _client(url, token, user, password) as c:
        try: r = c.request('PROPFIND', folder(url), headers={'Depth': '0'})
        except httpx.HTTPError as e: raise DavError(f'{url} does not answer: {e}') from e
    if r.status_code in (401, 403): raise DavError(f'{url} refused the login ({r.status_code}): check the token (scope "upload" or more)')
    if r.status_code in (301, 302, 307, 308): raise DavError(f'{url} redirects to {r.headers.get("location")}: give that address (with the trailing /dav/)')
    if r.status_code != 207: raise DavError(f'{url}: PROPFIND answered {r.status_code}, not 207 (is it the WebDAV folder, …/dav/?)')
    return f'{folder(url)} answers (WebDAV {r.headers.get("dav", "?")})'


def upload(url, path, token='', user='', password='', progress=None, stop=lambda: False):
    """PUT path into the folder under its own name; progress(sent, total)"""
    path = Path(path)
    size = path.stat().st_size
    target = folder(url) + quote(path.name)
    def body():
        sent = 0
        with open(path, 'rb') as f:
            while True:
                if stop(): raise DavError('stopped')
                b = f.read(8 << 20)
                if not b: return
                sent += len(b)
                if progress: progress(sent, size)
                yield b
    with _client(url, token, user, password) as c:
        try: r = c.put(target, content=body(), headers={'Content-Length': str(size), 'Content-Type': 'application/octet-stream'})
        except httpx.HTTPError as e: raise DavError(f'upload to {target} failed: {e}') from e
    if r.status_code not in (200, 201, 204):
        raise DavError(f'upload to {target}: HTTP {r.status_code} {r.text[:200]}' + (' (a proxy limit? the internal address avoids it)' if r.status_code in (413, 502, 504) else ''))
    return target
