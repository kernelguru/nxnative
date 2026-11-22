#!/usr/bin/env python3
# Extract the parts of Xcode_<version>.xip the Linux build needs (linux/README.md), on any system (no Mac, no xar / pbzx / cpio tools needed).
# A .xip is a xar archive whose "Content" file is a pbzx stream (a series of xz-compressed chunks) of a cpio archive of Xcode.app. This streams it, decompressing the
# chunks in parallel, and writes only the entries wanted() keeps; everything else is skipped while reading.
#
# Taken from NXNative for Windows, win/sdk/extract_xip.py (https://github.com/zenvyzenshiro/NXNativeWindows, commit 269d61ff974b2e8e073d7357466ae130c3f6812f), MIT licence:
# Copyright (c) 2026 seedds, Copyright (c) 2026 ZenvyZenshiro; the licence text is linux/setup/LICENSE-extract_xip. Its reading of the archive is kept as it is: the xar and pbzx
# readers, the cpio entries, and the two passes that fill the hard links of Xcode 27's deduplicated cpio (an entry whose data is the NULLcanary placeholder gets the bytes of the
# entry of the same inode that carries them, wherever that entry lies). Changed for the Linux build:
#   - what is kept: the iOS and macOS SDKs and the toolchain's Swift and clang libraries (wanted(), from the Xcode.app-relative path), not the iOS SDK alone; the output is that
#     Xcode.app tree (Contents/Developer/...), which linux/lib/nxn.py reads
#   - symlinks are written as symlinks (Linux needs no rights for them), so the SDKs' framework links (Versions/Current) stay links
#   - extract() for setup.py: returns what it wrote, the hard links without a carrier and the files left holding a placeholder, which setup.py refuses
# Usage: extract_xip.py <Xcode.xip> <output folder>
# Get Xcode from https://developer.apple.com/download/all/ (a free Apple ID); the Xcode and Apple SDKs Agreement applies to what you extract.
import concurrent.futures as cf, lzma, os, shutil, struct, sys, time, zlib
import xml.etree.ElementTree as ET
from pathlib import Path

KEEP = ('Contents/version.plist', 'Contents/Info.plist',
        'Contents/Developer/Platforms/iPhoneOS.platform/Developer/SDKs/',
        'Contents/Developer/Platforms/iPhoneOS.platform/Info.plist',
        'Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/',
        'Contents/Developer/Platforms/MacOSX.platform/Info.plist',
        'Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/swift/',
        'Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/clang/',
        'Contents/Developer/Toolchains/XcodeDefault.xctoolchain/ToolchainInfo.plist')
DROP = ('simulator', 'watchos', 'appletv', 'xros', 'driverkit', 'maccatalyst', '/host/', '.swiftsourceinfo', '.swiftdoc', '/linux/', '/wasi/', '/android/', '/windows/')


def wanted(name):
    """The Xcode.app-relative path ('Contents/...') of a cpio entry the Linux build keeps, else None: the SDKs, and of the toolchain's libraries only the iOS and macOS parts"""
    parts = name.lstrip('./').split('/')
    i = next((k for k, p in enumerate(parts) if p.endswith('.app')), None)
    if i is None: return None
    rel = '/'.join(parts[i + 1:])
    if not any(rel == k or rel.startswith(k) for k in KEEP): return None
    low = rel.lower()
    if '/usr/lib/swift/' in rel or '/usr/lib/clang/' in rel:
        if any(d in low for d in DROP): return None
        if '/lib/darwin/' in rel and rel.endswith(('.a', '.dylib')) and not any(x in rel for x in ('_ios.', '.ios.', '_osx.', '.osx.')): return None
    if '/SDKs/' in rel and ('/usr/share/man/' in rel or '/Documentation/' in rel): return None
    return rel


def xar_content(f):
    """(offset, length, encoding) of the xar archive's "Content" file"""
    hdr = f.read(28)
    if hdr[:4] != b'xar!': sys.exit('not a xar / xip archive')
    hsize, _ver, toc_c, _toc_u = struct.unpack('>HHQQ', hdr[4:24])
    f.seek(hsize); toc = zlib.decompress(f.read(toc_c))
    heap = hsize + toc_c
    for fe in ET.fromstring(toc).iter('file'):
        if (fe.findtext('name') or '') == 'Content':
            d = fe.find('data')
            enc = d.find('encoding').get('style') if d.find('encoding') is not None else ''
            return heap + int(d.findtext('offset')), int(d.findtext('length')), enc
    sys.exit('no Content file in the xip')


def pbzx_chunks(f, start, length):
    """the raw chunks of the pbzx stream, in order"""
    f.seek(start); end = start + length
    if f.read(4) != b'pbzx': sys.exit('the Content file is not a pbzx stream')
    f.read(8)   # chunk size
    while f.tell() < end:
        h = f.read(16)
        if len(h) < 16: break
        _flags, size = struct.unpack('>QQ', h)
        yield f.read(size)


def unpack(chunk):
    return lzma.decompress(chunk, format=lzma.FORMAT_XZ) if chunk[:6] == b'\xfd7zXZ\x00' else chunk


def stream(f, start, length, workers):
    """the cpio bytes, decompressed in parallel (chunks are independent xz streams) and yielded in order"""
    with cf.ThreadPoolExecutor(workers) as ex:
        pend = []
        for ch in pbzx_chunks(f, start, length):
            pend.append(ex.submit(unpack, ch))
            if len(pend) >= workers * 2: yield pend.pop(0).result()
        for p in pend: yield p.result()


class Reader:
    def __init__(self, it): self.it, self.buf, self.pos, self.total = it, bytearray(), 0, 0
    def read(self, n):
        while len(self.buf) - self.pos < n:
            try: c = next(self.it)
            except StopIteration: break
            if self.pos: del self.buf[:self.pos]; self.pos = 0
            self.buf += c
        out = bytes(self.buf[self.pos:self.pos + n]); self.pos += len(out); self.total += len(out)
        return out
    def skip(self, n):
        while n > 0:
            k = len(self.buf) - self.pos
            if k == 0:
                try: c = next(self.it)
                except StopIteration: return
                self.buf = bytearray(c); self.pos = 0; continue
            s = min(k, n); self.pos += s; self.total += s; n -= s


def entries(r):
    """(name, mode, ino, data_size) of each cpio entry; the caller reads or skips the data"""
    while True:
        magic = r.read(6)
        if not magic: return
        if magic == b'070707':   # odc
            h = r.read(70)
            ino, mode = int(h[6:12], 8), int(h[12:18], 8)
            namesize, size = int(h[53:59], 8), int(h[59:70], 8)
            name = r.read(namesize)[:-1].decode('utf-8', 'surrogateescape'); pad = 0
        elif magic in (b'070701', b'070702'):   # newc
            h = r.read(104)
            fld = [int(h[i:i + 8], 16) for i in range(0, 104, 8)]
            ino, mode, size, namesize = fld[0], fld[1], fld[6], fld[11]
            name = r.read(namesize)[:-1].decode('utf-8', 'surrogateescape')
            r.skip((-(110 + namesize)) % 4); pad = (-size) % 4
        else: sys.exit(f'unknown cpio header {magic!r} at byte {r.total}')
        if name == 'TRAILER!!!': return
        yield name, mode, ino, size, pad


CANARY = b'NULLcanary'   # the data of a hard link whose bytes another entry of the same inode carries (Xcode 27's sorted, deduplicated cpio: Metadata xbs_sortcpio)


def is_canary(head, size): return size >= len(CANARY) and head.startswith(CANARY) and not head[len(CANARY):].strip(b'\0')


def scan(f, start, length):
    """Pass 1: for each SDK file whose data is a canary, the inode key (ino, size) it needs, and which keys some entry carries real bytes for"""
    need, have, r = {}, set(), Reader(stream(f, start, length, os.cpu_count() or 4))
    for name, mode, ino, size, pad in entries(r):
        if mode & 0o170000 == 0o100000 and size:
            head = r.read(min(size, 64)); r.skip(size - len(head))
            rel = wanted(name)
            if is_canary(head, size):
                if rel: need.setdefault((ino, size), []).append(rel)
            else: have.add((ino, size))
        else: r.skip(size)
        r.skip(pad)
    return need, have


def materialize(out, links):
    """each symlink as a symlink, after every file: its target is kept as the archive gives it (relative, as Xcode's are)"""
    for rel, target in links:
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_symlink() or dst.is_file(): dst.unlink()
        elif dst.exists(): continue   # a folder the archive also holds
        os.symlink(target, dst)


def placeholders(out):
    """the files under out that still hold a NULLcanary placeholder"""
    return [p for p in Path(out).rglob('*') if p.is_file() and not p.is_symlink() and p.stat().st_size >= len(CANARY) and is_canary(p.open('rb').read(64), p.stat().st_size)]


def extract(xip, out, say=print):
    """Writes what wanted() keeps under out: {'files': written, 'links': symlinks, 'hardlinks': placeholders filled, 'lost': hard-linked files no entry carries the bytes of,
    'placeholders': files still holding one}"""
    xip, out = Path(xip), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    t0, n, links = time.time(), 0, []
    with open(xip, 'rb') as f:
        start, length, enc = xar_content(f)
        say(f'xip: Content {length / 2**30:.1f} GB ({enc or "raw"}); writing the SDKs and the toolchain libraries to {out}')
        need, have = scan(f, start, length)
        lost = sum(len(v) for k, v in need.items() if k not in have)
        say(f'  pass 1: {sum(map(len, need.values()))} files are hard links whose bytes another entry carries' + (f' ({lost} with no carrier)' if lost else ''))
        r = Reader(stream(f, start, length, os.cpu_count() or 4))
        last, done = time.time(), set()
        for name, mode, ino, size, pad in entries(r):
            rel = wanted(name)
            kind, key = mode & 0o170000, (ino, size)
            if rel and kind == 0o120000:   # symlink: its target is the data
                links.append((rel, r.read(size).decode('utf-8', 'surrogateescape')))
            elif kind == 0o100000:
                head = r.read(min(size, 64))
                canary = is_canary(head, size)
                dsts = [out / rel] if rel and not canary else []
                if not canary and key in need and key not in done: dsts += [out / p for p in need[key]]; done.add(key)   # the carrier: also every canary link of this inode
                if dsts:
                    for d in dsts: d.parent.mkdir(parents=True, exist_ok=True)
                    for d in dsts: d.write_bytes(head)
                    if size > len(head):
                        fs = [open(d, 'ab') for d in dsts]
                        try:
                            left = size - len(head)
                            while left:
                                b = r.read(min(left, 1 << 22))
                                if not b: sys.exit('the cpio stream ended inside a file')
                                for o in fs: o.write(b)
                                left -= len(b)
                        finally:
                            for o in fs: o.close()
                    for d in dsts: os.chmod(d, (mode & 0o777) | 0o600)
                    n += len(dsts)
                else: r.skip(size - len(head))
            else:
                if rel and kind == 0o040000: (out / rel).mkdir(parents=True, exist_ok=True)
                r.skip(size)
            r.skip(pad)
            if time.time() - last > 10: last = time.time(); say(f'  {r.total / 2**30:.1f} GB read, {n} files')
    materialize(out, links)
    bad = placeholders(out)
    if bad: say(f'  {len(bad)} files still hold a placeholder, e.g. {bad[0]}')
    say(f'done in {time.time() - t0:.0f} s: {n} files, {len(links)} symlinks')
    return {'files': n, 'links': len(links), 'hardlinks': len(done), 'lost': lost, 'placeholders': [str(p) for p in bad]}


def main():
    if len(sys.argv) < 3: sys.exit(__doc__ or 'usage: extract_xip.py <Xcode.xip> <out>')
    r = extract(sys.argv[1], sys.argv[2], say=lambda m: print(m, flush=True))
    sys.exit(1 if r['lost'] or r['placeholders'] else 0)


if __name__ == '__main__':
    main()
