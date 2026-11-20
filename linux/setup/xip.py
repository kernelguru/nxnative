#!/usr/bin/env python3
"""Reads an Xcode .xip on any system, streaming, and writes out only the files a filter keeps (setup.py keeps the SDKs and the toolchain's Swift and clang libraries).

An .xip is a xar archive (header, zlib-compressed XML table of contents, heap) whose file "Content" is a pbzx stream: chunks of XZ-compressed (or stored) data that together are
a cpio archive (the old portable ASCII format 070707, or the new ASCII 070701 / 070702). Nothing is decompressed to disk but what is kept, so the 3 GB of an iOS + macOS SDK subset
come out of a 3 GB .xip without the 40 GB of a whole Xcode. Apple's signature over the xar table of contents is not checked here: the player's own download is trusted as such.

Usage: xip.py <Xcode.xip> <output folder> [--list]   (output: the Xcode.app's Contents/... tree, the kept parts only)
"""
import lzma, os, stat, struct, sys, xml.etree.ElementTree as ET, zlib
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


def cpio(stream):
    """(name, mode, data reader) of each entry; the reader must be called once (it returns the entry's bytes, or None to skip them)"""
    while True:
        magic = stream.read(6)
        if len(magic) < 6: return
        if magic == b'070707':
            h = stream.read(70)
            mode, nlen, fsize = int(h[12:18], 8), int(h[53:59], 8), int(h[59:70], 8)
            name = stream.read(nlen)[:-1].decode('utf-8', 'replace'); pad_name = pad_data = 0
        elif magic in (b'070701', b'070702'):
            h = stream.read(104)
            mode, fsize, nlen = int(h[8:16], 16), int(h[48:56], 16), int(h[88:96], 16)
            name = stream.read(nlen)[:-1].decode('utf-8', 'replace')
            pad_name, pad_data = (4 - (110 + nlen) % 4) % 4, (4 - fsize % 4) % 4
            stream.read(pad_name)
        else:
            raise ValueError(f'unknown cpio header {magic!r} at byte {stream.done}')
        if name == 'TRAILER!!!': return
        yield name, mode, fsize
        stream.read(pad_data) if pad_data else None


def app_relative(name):   # './Xcode.app/Contents/...' or 'Xcode-beta.app/Contents/...' → 'Contents/...'
    parts = name.lstrip('./').split('/')
    for i, p in enumerate(parts):
        if p.endswith('.app'): return '/'.join(parts[i + 1:])
    return None


def extract(xip, out, want=keep, progress=None):
    x = Xar(xip)
    off, length, enc = x.file('Content')
    if 'octet-stream' not in enc: raise ValueError(f'the Content of the .xip is encoded as {enc}, which this reader does not know')
    s = Pbzx(x.f, off, length)
    out = Path(out); n = kept = 0; links = []
    for name, mode, size in cpio(s):
        n += 1
        rel = app_relative(name)
        if rel is None or not want(rel):
            s.skip(size); continue
        dst = out / rel
        kind = stat.S_IFMT(mode)
        if kind == stat.S_IFDIR:
            dst.mkdir(parents=True, exist_ok=True); s.skip(size); continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if kind == stat.S_IFLNK:
            links.append((dst, s.read(size).decode('utf-8', 'replace'))); continue
        with open(dst, 'wb') as f:
            left = size
            while left:
                b = s.read(min(left, 8 << 20))
                if not b: raise ValueError(f'the archive ends inside {name}')
                f.write(b); left -= len(b)
        os.chmod(dst, (mode & 0o777) | 0o600)
        kept += 1
        if progress and kept % 2000 == 0: progress(kept, n, s.done)
    for dst, target in links:   # last: a link may point at a file that came after it
        if dst.is_symlink() or dst.exists(): dst.unlink()
        os.symlink(target, dst)
    x.f.close()
    return kept, n


if __name__ == '__main__':
    if len(sys.argv) < 3: sys.exit(__doc__)
    if '--list' in sys.argv:
        x = Xar(sys.argv[1]); off, length, _ = x.file('Content'); s = Pbzx(x.f, off, length)
        for name, mode, size in cpio(s):
            r = app_relative(name)
            if r and keep(r): print(name, size)
            s.skip(size)
    else:
        k, n = extract(sys.argv[1], sys.argv[2], progress=lambda k, n, b: print(f'{k} kept of {n} entries, {b / 2**30:.1f} GB read', flush=True))
        print(f'done: {k} files kept of {n} entries')
