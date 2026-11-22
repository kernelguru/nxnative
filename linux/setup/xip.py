#!/usr/bin/env python3
"""Reads an Xcode .xip on any system, streaming, and writes out only the files a filter keeps (setup.py keeps the SDKs and the toolchain's Swift and clang libraries).

An .xip is a xar archive (header, zlib-compressed XML table of contents, heap) whose file "Content" is a pbzx stream: chunks of XZ-compressed (or stored) data that together are
a cpio archive (the old portable ASCII format 070707, or the new ASCII 070701 / 070702). Nothing is decompressed to disk but what is kept, so the 3 GB of an iOS + macOS SDK subset
come out of a 3 GB .xip without the 40 GB of a whole Xcode. Apple's signature over the xar table of contents is not checked here: the player's own download is trusted as such.

Usage: xip.py <Xcode.xip> <output folder> [--list]   (output: the Xcode.app's Contents/... tree, the kept parts only)
"""
import lzma, os, shutil, stat, struct, sys, xml.etree.ElementTree as ET, zlib
from pathlib import Path

KEEP = ('Contents/version.plist', 'Contents/Info.plist',
        'Contents/Developer/Platforms/iPhoneOS.platform/Developer/SDKs/',
        'Contents/Developer/Platforms/iPhoneOS.platform/Info.plist',
        'Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/',
        'Contents/Developer/Platforms/MacOSX.platform/Info.plist',
        'Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/swift/',
        'Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/clang/',
        'Contents/Developer/Toolchains/XcodeDefault.xctoolchain/ToolchainInfo.plist')
DROP = ('simulator', 'watchos', 'appletv', 'xros', 'xrsimulator', 'driverkit', 'maccatalyst', '/host/', '.swiftsourceinfo', '.swiftdoc', '/linux/', '/wasi/',
        '/android/', '/windows/')


def keep(rel):
    """rel: a path inside the Xcode.app ('Contents/...'); whether setup.py needs it"""
    if not any(rel == k or rel.startswith(k) for k in KEEP): return False
    low = rel.lower()
    if '/usr/lib/swift/' in rel or '/usr/lib/clang/' in rel:   # the toolchain's libraries: only iOS and macOS parts
        if any(d in low for d in DROP): return False
        if '/lib/darwin/' in rel and not any(x in rel for x in ('_ios.', '.ios.', '_osx.', '.osx.', 'ios.a', 'osx.a')) and rel.endswith(('.a', '.dylib')): return False
    if '/SDKs/' in rel and ('/usr/share/man/' in rel or '/Documentation/' in rel): return False
    return True


class Xar:
    def __init__(self, path):
        self.f = open(path, 'rb')
        h = self.f.read(28)
        if h[:4] != b'xar!': raise ValueError(f'{path} is not a xar archive (.xip)')
        hsize, _ver, toc_c, toc_u, _ck = struct.unpack('>HHQQI', h[4:28])
        self.f.seek(hsize)
        self.toc = ET.fromstring(zlib.decompress(self.f.read(toc_c)))
        self.heap = hsize + toc_c

    def file(self, name):
        for e in self.toc.iter('file'):
            if e.findtext('name') == name:
                d = e.find('data')
                return self.heap + int(d.findtext('offset')), int(d.findtext('length')), d.find('encoding').get('style')
        raise ValueError(f'no {name} in the archive')


class Pbzx:
    """The decompressed bytes of a pbzx stream, read in order"""
    def __init__(self, f, offset, length):
        self.f, self.end = f, offset + length
        f.seek(offset)
        if f.read(4) != b'pbzx': raise ValueError('the Content of the .xip is not a pbzx stream')
        f.read(8)
        self.buf, self.pos, self.done = b'', 0, 0

    def _chunk(self):
        if self.f.tell() >= self.end: return False
        hdr = self.f.read(16)
        if len(hdr) < 16: return False
        _flags, size = struct.unpack('>QQ', hdr)
        data = self.f.read(size)
        self.buf = self.buf[self.pos:] + (lzma.decompress(data) if data[:6] == b'\xfd7zXZ\x00' else data)
        self.pos = 0
        return True

    def read(self, n):
        while len(self.buf) - self.pos < n:
            if not self._chunk(): break
        out = self.buf[self.pos:self.pos + n]; self.pos += len(out); self.done += len(out)
        return out

    def skip(self, n):
        while n > 0:
            have = len(self.buf) - self.pos
            if have == 0 and not self._chunk(): return
            k = min(n, len(self.buf) - self.pos); self.pos += k; n -= k; self.done += k


class Entry(tuple):
    """One cpio entry: name, mode, size, and the identity of its file (dev, ino, nlink): entries of one hard-linked file share dev and ino"""
    __slots__ = ()
    def __new__(cls, *a): return tuple.__new__(cls, a)
    name, mode, size, dev, ino, nlink = (property(lambda self, i=i: self[i]) for i in range(6))


def cpio(stream):
    """The entries in order; the consumer reads or skips each one's size bytes before asking for the next"""
    while True:
        magic = stream.read(6)
        if len(magic) < 6: return
        if magic == b'070707':
            h = stream.read(70)
            dev, ino, mode, nlink = int(h[0:6], 8), int(h[6:12], 8), int(h[12:18], 8), int(h[30:36], 8)
            nlen, fsize = int(h[53:59], 8), int(h[59:70], 8)
            name = stream.read(nlen)[:-1].decode('utf-8', 'replace'); pad_data = 0
        elif magic in (b'070701', b'070702'):
            h = stream.read(104)
            ino, mode, nlink, fsize, nlen = int(h[0:8], 16), int(h[8:16], 16), int(h[32:40], 16), int(h[48:56], 16), int(h[88:96], 16)
            dev = (int(h[56:64], 16) << 32) | int(h[64:72], 16)
            name = stream.read(nlen)[:-1].decode('utf-8', 'replace')
            pad_name, pad_data = (4 - (110 + nlen) % 4) % 4, (4 - fsize % 4) % 4
            stream.read(pad_name)
        else:
            raise ValueError(f'unknown cpio header {magic!r} at byte {stream.done}')
        if name == 'TRAILER!!!': return
        yield Entry(name, mode, fsize, dev, ino, nlink)
        stream.read(pad_data) if pad_data else None


def app_relative(name):   # './Xcode.app/Contents/...' or 'Xcode-beta.app/Contents/...' → 'Contents/...'
    parts = name.lstrip('./').split('/')
    for i, p in enumerate(parts):
        if p.endswith('.app'): return '/'.join(parts[i + 1:])
    return None


PLACEHOLDER = b'NULLcanary'   # what Xcode's cpio holds for every link of a hard-linked file but the first: NULLcanary, then zeros, as long as the file


def placeholder(head, size):
    return size == 0 or (head.startswith(PLACEHOLDER) and not head[len(PLACEHOLDER):].strip(b'\0'))


def extract(xip, out, want=keep, progress=None):
    """Writes the entries want keeps under out; returns (files kept, entries, links filled, links left unfilled).
    Hard links: the archive holds a hard-linked file's bytes once, with its first entry, and NULLcanary placeholders of the same size with the others (Xcode 27: a fifth of the iOS SDK's
    headers link to the macOS SDK's, which comes first). A placeholder is filled from the entry of the same file (dev, ino, size) that has the bytes: a hard link to it when it is kept,
    else to its copy in a stash folder, kept for the extraction only, as it may be in a part the filter leaves out (another platform's SDK)."""
    x = Xar(xip)
    off, length, enc = x.file('Content')
    if 'octet-stream' not in enc: raise ValueError(f'the Content of the .xip is encoded as {enc}, which this reader does not know')
    s = Pbzx(x.f, off, length)
    out = Path(out); n = kept = filled = 0; links = []
    stash = out / '.nxn_hardlinks'
    sources, pending = {}, {}   # (dev, ino, size) → the path that holds the bytes; → the placeholders written before them
    def place(src, dst):
        if dst.exists() or dst.is_symlink(): dst.unlink()
        try: os.link(src, dst)
        except OSError: shutil.copyfile(src, dst)
    for e in cpio(s):
        n += 1
        rel = app_relative(e.name)
        kind = stat.S_IFMT(e.mode)
        key = (e.dev, e.ino, e.size) if kind == stat.S_IFREG and e.nlink > 1 else None
        wanted = rel is not None and want(rel)
        if not wanted and not key:
            s.skip(e.size); continue
        if wanted:
            dst = out / rel
            if kind == stat.S_IFDIR:
                dst.mkdir(parents=True, exist_ok=True); s.skip(e.size); continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if kind == stat.S_IFLNK:
                links.append((dst, s.read(e.size).decode('utf-8', 'replace'))); continue
        else:
            stash.mkdir(parents=True, exist_ok=True)
            dst = stash / f'{e.dev}_{e.ino}_{e.size}'
        head = s.read(min(e.size, 8 << 20))
        if key and placeholder(head, e.size):
            s.skip(e.size - len(head))
            if not wanted: continue
            if key in sources: place(sources[key], dst); filled += 1
            else:
                with open(dst, 'wb') as f: f.write(head)
                pending.setdefault(key, []).append(dst)
        else:
            with open(dst, 'wb') as f:
                f.write(head); left = e.size - len(head)
                while left:
                    b = s.read(min(left, 8 << 20))
                    if not b: raise ValueError(f'the archive ends inside {e.name}')
                    f.write(b); left -= len(b)
            if key:
                sources[key] = dst
                for p in pending.pop(key, []): place(dst, p); filled += 1
        if wanted:
            os.chmod(dst, (e.mode & 0o777) | 0o600)
            kept += 1
            if progress and kept % 2000 == 0: progress(kept, n, s.done)
    for dst, target in links:   # last: a link may point at a file that came after it
        if dst.is_symlink() or dst.exists(): dst.unlink()
        os.symlink(target, dst)
    shutil.rmtree(stash, ignore_errors=True)
    x.f.close()
    return kept, n, filled, sum(len(v) for v in pending.values())


def placeholders(root):
    """The files under root that still hold a hard-link placeholder: an extraction from before links were filled"""
    out = []
    for d, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(d, f)
            try:
                if os.path.islink(p): continue
                with open(p, 'rb') as fh: head = fh.read(4096)
            except OSError: continue
            if head.startswith(PLACEHOLDER) and placeholder(head, len(head)): out.append(p)
    return out


if __name__ == '__main__':
    if len(sys.argv) < 3: sys.exit(__doc__)
    if '--list' in sys.argv:
        x = Xar(sys.argv[1]); off, length, _ = x.file('Content'); s = Pbzx(x.f, off, length)
        for e in cpio(s):
            r = app_relative(e.name)
            if r and keep(r): print(e.name, e.size, f'links {e.nlink} dev {e.dev} ino {e.ino}' if e.nlink > 1 else '')
            s.skip(e.size)
    else:
        k, n, f, u = extract(sys.argv[1], sys.argv[2], progress=lambda k, n, b: print(f'{k} kept of {n} entries, {b / 2**30:.1f} GB read', flush=True))
        print(f'done: {k} files kept of {n} entries; {f} hard links filled, {u} left as placeholders')
