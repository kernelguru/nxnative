#!/usr/bin/env python3
"""Installs the Apple parts the Linux build needs into the toolchain folder (NXN_TOOLCHAIN, default /data/toolchain; linux/lib/nxn.py), from files the player downloaded:

  setup.py xcode <Xcode.xip>          the iOS and macOS SDKs and the toolchain's Swift and clang libraries, out of the player's Xcode download (developer.apple.com)
  setup.py metal <installer|zip|dir>  Apple's Metal Developer Tools for Windows, run under Wine: a folder holding metal.exe, a .zip of one (C:\\Program Files\\Metal Developer Tools
                                      zipped on a Windows PC), or the installer (.exe / .msi), unpacked with 7-Zip or, failing that, run silently in Wine
  setup.py fonts <file.ttf>...        the shared-font sources: Arial Unicode.ttf and Arial Rounded Bold.ttf from a Mac (/System/Library/Fonts/Supplemental)
  setup.py wine                       creates the Wine prefix (setup.py metal adds the Visual C++ runtime with winetricks when the tools do not start without it)
  setup.py chain [--if-needed]        the lock file of the translation chain and its header again for this checkout's Linux-built tools and the installed Metal compiler;
                                      --if-needed: the ones kept in the volume when they fit these tools (the container's start), written only otherwise
  setup.py status [--json]            what is installed, what is missing
Each step can be repeated; a later one replaces what an earlier one installed. The web interface (linux/web) runs the same steps.
"""
import json, os, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib')); import nxn
sys.path.insert(0, str(Path(__file__).resolve().parent)); import xip

FONT_NAMES = {'std': ('Arial Unicode.ttf', 'Arial Unicode MS.ttf', 'ArialUnicode.ttf', 'ARIALUNI.TTF'),   # macOS's names, then Microsoft Office's (C:\\Windows\\Fonts)
              'letters': ('Arial Rounded Bold.ttf', 'ArialRoundedBold.ttf', 'Arial Rounded MT Bold.ttf', 'ARLRDBD.TTF')}
FALLBACK_FONTS = {'std': ('/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
                  'letters': ('/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')}


def say(msg): print(msg, flush=True)


wine_env = nxn.wine_env


def install_xcode(src):
    src = Path(src)
    if not src.is_file(): nxn.die(f'no file {src}')
    tmp = nxn.TOOLCHAIN / 'xcode.partial'
    if tmp.exists(): shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    say(f'reading {src} ({src.stat().st_size / 2**30:.1f} GB): only the SDKs and the toolchain libraries are written (this takes 10-30 minutes)')
    kept, n = xip.extract(src, tmp, progress=lambda k, n, b: say(f'  {k} files kept, {b / 2**30:.1f} GB unpacked'))
    say(f'{kept} files kept of {n}')
    for need in ('Contents/Developer/Platforms/iPhoneOS.platform/Developer/SDKs', 'Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/swift'):
        if not (tmp / need).is_dir():
            shutil.rmtree(tmp); nxn.die(f'{src} has no {need}: is it a full Xcode .xip (not the Command Line Tools)?')
    if nxn.XCODE.exists(): shutil.rmtree(nxn.XCODE)
    tmp.rename(nxn.XCODE)
    v, b = nxn.xcode_version()
    say(f'Xcode {v} ({b}) installed: iOS SDK {nxn.sdk_settings("iphoneos")["version"]}' + (f', macOS SDK {nxn.sdk_settings("macosx")["version"]}' if has_sdk('macosx') else ''))


def has_sdk(s):
    try: nxn.sdk_path(s); return True
    except nxn.Missing: return False


def find_metal_root(d):
    """The top folder of an installed Metal Developer Tools under d: above the platform folders (ios\\bin\\metal.exe, macos\\bin\\metal.exe) when it has them"""
    for p in sorted(Path(d).rglob('metal.exe')):
        q = p.parent.parent if p.parent.name.lower() == 'bin' else p.parent
        return q.parent if q.name.lower() in ('ios', 'macos', 'tvos', 'xros') else q
    return None


def install_metal(src):
    src = Path(src)
    tmp = Path(tempfile.mkdtemp(prefix='nxn_metal_', dir=nxn.TOOLCHAIN))
    try:
        if src.is_dir(): shutil.copytree(src, tmp / 'tools')
        elif zipfile.is_zipfile(src):
            with zipfile.ZipFile(src) as z: z.extractall(tmp / 'tools')
        elif src.suffix.lower() in ('.exe', '.msi'):
            seven = nxn.which('7z', '7zz', '7za')
            if seven: subprocess.run([seven, 'x', '-y', f'-o{tmp / "tools"}', str(src)], stdout=subprocess.DEVNULL)
            if not find_metal_root(tmp):   # an installer 7-Zip cannot open: run it in Wine, silently, into a known folder
                wine_init(quiet=True)
                target = nxn.WINEPREFIX / 'drive_c/Program Files/Metal Developer Tools'
                cmd = (['wine', 'msiexec', '/i', str(src), '/qn'] if src.suffix.lower() == '.msi' else ['wine', str(src), '/S', '/quiet', '/VERYSILENT'])
                say('running the installer in Wine (silent)')
                subprocess.run(['xvfb-run', '-a', *cmd] if nxn.which('xvfb-run') else cmd, env=wine_env(), timeout=1800)
                root = find_metal_root(nxn.WINEPREFIX / 'drive_c')
                if root: shutil.copytree(root, tmp / 'tools')
        else: nxn.die(f'{src}: give the Metal Developer Tools as a folder, a .zip of one, or the installer (.exe / .msi)')
        root = find_metal_root(tmp)
        if not root:
            nxn.die(f'no metal.exe in {src}. Install the Metal Developer Tools for Windows on a Windows PC, zip the folder C:\\Program Files\\Metal Developer Tools and give that .zip')
        if nxn.METAL.exists(): shutil.rmtree(nxn.METAL)
        shutil.move(str(root), str(nxn.METAL))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    (nxn.METAL / '.nxn_tools.json').unlink(missing_ok=True)
    tools = sorted(p.stem for p in nxn.METAL.rglob('*.exe'))
    say(f'Metal tools installed in {nxn.METAL}: {", ".join(t for t in tools if t.startswith("metal") or t.startswith("air"))}')
    wine_init(quiet=True)
    bad = metal_check()
    if bad and nxn.which('winetricks'):   # the Visual C++ runtime, only when the tools need it (its 32-bit half does not install in this prefix, which they do not need)
        say(f'metal.exe does not start ({bad[:200]}): installing the Visual C++ runtime (winetricks vcrun2022, downloads it from Microsoft)')
        subprocess.run(['winetricks', '-q', '--unattended', 'vcrun2022'], env=wine_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800)
        bad = metal_check()
    if bad: nxn.die(f'metal.exe does not run in Wine: {bad[:500]}')
    for t in ('metal-tt', 'metal-pack'):
        if t not in tools: say(f'note: {t}.exe is not in these tools: the IPA gets no offline pipeline archive, and the device builds its pipelines on its first start')


def metal_check():
    """None when metal.exe answers --version for both SDKs (and says so), else the error"""
    for sdk in ('iphoneos', 'macosx'):
        try: r = subprocess.run([str(nxn.LINUX / 'bin/xcrun'), '-sdk', sdk, 'metal', '--version'], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired: return 'metal --version did not answer in 5 minutes'
        if r.returncode or not r.stdout.strip(): return (r.stderr or r.stdout or f'exit code {r.returncode}').strip()
        say(f'metal under Wine for {sdk}: {r.stdout.splitlines()[0]} ({nxn.metal_tool("metal", sdk).relative_to(nxn.METAL)})')
    return None


def wine_init(quiet=False):
    if not nxn.which('wine', 'wine64'): nxn.die('wine is not installed')
    marker = nxn.WINEPREFIX / '.nxn_ready'
    if marker.exists(): return
    nxn.WINEPREFIX.mkdir(parents=True, exist_ok=True)
    say(f'creating the Wine prefix {nxn.WINEPREFIX}')
    subprocess.run(['wineboot', '-i'] if nxn.which('wineboot') else ['wine', 'wineboot', '-i'], env=wine_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=600, start_new_session=True)
    marker.write_text('ok\n')


def install_fonts(files):
    nxn.FONTS.mkdir(parents=True, exist_ok=True)
    for f in map(Path, files):
        kind = next((k for k, names in FONT_NAMES.items() if f.name.lower() in (n.lower() for n in names)), None)
        if not kind:
            low = f.name.lower()
            kind = 'letters' if 'rounded' in low else 'std' if 'unicode' in low else None
        if not kind: nxn.die(f'{f.name}: give Arial Unicode.ttf / ARIALUNI.TTF or Arial Rounded Bold.ttf / ARLRDBD.TTF')
        shutil.copy2(f, nxn.FONTS / FONT_NAMES[kind][0])
        say(f'{kind} font: {f.name}')


def font(kind):
    p = nxn.FONTS / FONT_NAMES[kind][0]
    if p.is_file(): return p, True
    for q in FALLBACK_FONTS[kind]:
        if Path(q).is_file(): return Path(q), False
    return None, False


CHAIN_DIR = nxn.TOOLCHAIN / 'chain'   # the lock and header setup.py chain wrote, kept in the volume with the key of what they were written for
CHAIN_RUNNING = nxn.TOOLCHAIN / 'chain.running'
CHAIN_FILES = ('dm/gfx/tools/dmsh_chain.lock', 'dm/gfx/nvn/dmsh_chain.h')


def chain_key():
    """What the chain lock and header of this checkout follow from: the Linux-built translator and SPIRV-Cross, the installed Metal tools, and the chain's own sources
    (dmsh_chain.FILES but the lock): equal for a recreated container of the same image, different after a new image or new Metal tools"""
    import hashlib
    h = hashlib.sha256()
    for rel in ('dm/gfx/tools/dmsh_xlate_bin/dmsh_xlate', 'dm/gfx/tools/spirv_cross_bin/spirv-cross'):
        try: h.update(hashlib.sha256((nxn.REPO / rel).read_bytes()).digest())
        except OSError: h.update(b'missing ' + rel.encode())
    if nxn.METAL.is_dir():
        for p in sorted(nxn.METAL.rglob('*.exe')): h.update(f'{p.relative_to(nxn.METAL)}\t{p.stat().st_size}\n'.encode())
    sys.path.insert(0, str(nxn.REPO / 'dm/gfx/tools'))
    try:
        import dmsh_chain
        files = [f for f in dmsh_chain.FILES if not f.endswith('dmsh_chain.lock')]
    except Exception: files = []
    for f in files:
        try: h.update(f.encode() + b'\0' + (nxn.REPO / 'dm/gfx' / f).read_bytes())
        except OSError: h.update(b'missing ' + f.encode())
    return h.hexdigest()[:24]


def chain():
    """The lock and the chain header again for the tools of this checkout: the Linux-built translator and SPIRV-Cross (linux/setup/build_tools.sh) and the Metal compiler under Wine.
    Both are also kept in the volume (CHAIN_DIR), so a recreated container of the same image takes them back (chain_restore) instead of writing them again."""
    tools = nxn.REPO / 'dm/gfx/tools'
    env = dict(os.environ, PATH=f'{nxn.LINUX / "bin"}:{os.environ.get("PATH", "")}')
    nxn.wine_up()
    ver = ''
    for cmd in (['tool_lock.py', '--update-lock'], ['dmsh_chain.py', '--header']):
        try: r = subprocess.run([sys.executable, str(tools / cmd[0]), *cmd[1:]], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900)
        except subprocess.TimeoutExpired: nxn.die(f'{" ".join(cmd)} did not finish in 15 minutes (Wine: is metal.exe hanging?)')
        if r.returncode: nxn.die(f'{" ".join(cmd)}: {(r.stderr or r.stdout).strip()[-800:]}')
        ver = r.stdout.strip()
    CHAIN_DIR.mkdir(parents=True, exist_ok=True)
    for rel in CHAIN_FILES: shutil.copy2(nxn.REPO / rel, CHAIN_DIR / Path(rel).name)
    (CHAIN_DIR / 'key.json').write_text(json.dumps({'key': chain_key(), 'version': ver, 'metal': metal_version()}))
    say(f'translation chain for this checkout: b-{ver} ({nxn.REPO / "dm/gfx/nvn/dmsh_chain.h"})')


def chain_restore():
    """The kept lock and header back into this checkout when they were written for exactly these tools and sources; whether it did"""
    try: saved = json.loads((CHAIN_DIR / 'key.json').read_text())
    except (OSError, ValueError): return False
    if saved.get('key') != chain_key() or not all((CHAIN_DIR / Path(r).name).is_file() for r in CHAIN_FILES): return False
    for rel in CHAIN_FILES: shutil.copy2(CHAIN_DIR / Path(rel).name, nxn.REPO / rel)
    say(f'translation chain b-{saved.get("version", "?")} taken from {CHAIN_DIR}')
    return True


def chain_if_needed():
    """At the container's start (entrypoint.sh, in the background): nothing without the SDK and the Metal tools, the kept lock when it fits, else chain()"""
    if not (has_sdk('iphoneos') and (nxn.METAL / '.').is_dir() and any(nxn.METAL.rglob('metal.exe'))): return
    if chain_current() or chain_restore(): return
    CHAIN_RUNNING.write_text(str(os.getpid()))
    try: chain()
    finally: CHAIN_RUNNING.unlink(missing_ok=True)


def chain_running():
    try: pid = int(CHAIN_RUNNING.read_text())
    except (OSError, ValueError): return False
    return Path(f'/proc/{pid}').exists()


def metal_version():
    try:
        r = subprocess.run([str(nxn.LINUX / 'bin/xcrun'), '-sdk', 'iphoneos', 'metal', '--version'], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        return r.stdout.splitlines()[0] if r.returncode == 0 and r.stdout else None
    except (OSError, subprocess.SubprocessError): return None


def chain_current():   # whether this checkout holds the lock and header kept for these tools (setup.py chain), so a new image or new Metal tools ask for them again
    try: saved = json.loads((CHAIN_DIR / 'key.json').read_text())
    except (OSError, ValueError): return False
    try: same = all((nxn.REPO / rel).read_bytes() == (CHAIN_DIR / Path(rel).name).read_bytes() for rel in CHAIN_FILES)
    except OSError: return False
    return same and saved.get('key') == chain_key()


def status():
    v, b = nxn.xcode_version()
    s = {'toolchain': str(nxn.TOOLCHAIN), 'xcode': {'version': v, 'build': b, 'ios_sdk': None, 'macos_sdk': None, 'swift_libs': (nxn.XCTC / 'usr/lib/swift').is_dir()}}
    for k, n in (('ios_sdk', 'iphoneos'), ('macos_sdk', 'macosx')):
        try: s['xcode'][k] = nxn.sdk_settings(n)['version']
        except nxn.Missing: pass
    tools = sorted(p.stem for p in nxn.METAL.rglob('*.exe')) if nxn.METAL.is_dir() else []
    s['metal'] = {'installed': 'metal' in tools, 'tools': [t for t in tools if t.startswith(('metal', 'air'))], 'wine': bool(nxn.which('wine', 'wine64')),
                  'prefix': (nxn.WINEPREFIX / '.nxn_ready').exists()}
    for k in ('std', 'letters'):
        p, own = font(k)
        s.setdefault('fonts', {})[k] = {'path': str(p) if p else None, 'own': own}
    s['tools'] = {}
    for name, rel in (('dmsh_xlate', 'dm/gfx/tools/dmsh_xlate_bin/dmsh_xlate'), ('nx_extract', 'dm/tools/nx_extract_bin/nx_extract'), ('spirv-cross', 'dm/gfx/tools/spirv_cross_bin/spirv-cross')):
        p = nxn.REPO / rel
        try: s['tools'][name] = p.is_file() and open(p, 'rb').read(4) == b'\x7fELF'
        except OSError: s['tools'][name] = False
    for name in ('clang', 'ld64.lld', 'swiftc', 'ldid', 'zstd', 'glslangValidator', 'make'):
        try: s['tools'][name] = bool(nxn.llvm(name)) if name in ('clang', 'ld64.lld') else bool(nxn.which(name))
        except nxn.Missing: s['tools'][name] = False
    s['chain'] = chain_current()
    s['chain_running'] = chain_running()
    s['ready'] = bool(s['xcode']['ios_sdk'] and s['xcode']['swift_libs'] and s['metal']['installed'] and all(s['tools'][t] for t in ('dmsh_xlate', 'nx_extract', 'spirv-cross', 'clang', 'ld64.lld', 'swiftc')) and s['chain'])
    return s


def main(a):
    if not a or a[0] in ('-h', '--help'): print(__doc__); return 0
    nxn.TOOLCHAIN.mkdir(parents=True, exist_ok=True)
    cmd = a[0]
    if cmd == 'xcode' and len(a) == 2: install_xcode(a[1])
    elif cmd == 'metal' and len(a) == 2: install_metal(a[1])
    elif cmd == 'fonts' and len(a) >= 2: install_fonts(a[1:])
    elif cmd == 'wine': wine_init()
    elif cmd == 'chain' and a[1:] == ['--if-needed']: chain_if_needed()
    elif cmd == 'chain': chain()
    elif cmd == 'status':
        s = status()
        if '--json' in a: print(json.dumps(s, indent=1))
        else:
            x = s['xcode']
            say(f'Xcode: {x["version"] or "missing"} (iOS SDK {x["ios_sdk"] or "missing"}, macOS SDK {x["macos_sdk"] or "missing"}, Swift libraries {"yes" if x["swift_libs"] else "missing"})')
            say(f'Metal tools: {", ".join(s["metal"]["tools"]) or "missing"}; Wine prefix: {"ready" if s["metal"]["prefix"] else "not created"}')
            for k, f in s['fonts'].items(): say(f'font {k}: {f["path"] or "missing"}{"" if f["own"] else " (a stand-in: shared fonts look different from Apple\'s Arial)"}')
            say('tools: ' + ', '.join(f'{k} {"ok" if v else "MISSING"}' for k, v in s['tools'].items()))
            say(f'translation chain lock for this checkout: {"current" if s["chain"] else "being written" if s["chain_running"] else "not written (setup.py chain)"}')
            say('ready to build' if s['ready'] else 'not ready')
        return 0 if s['ready'] else 1
    else: print(__doc__); return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
