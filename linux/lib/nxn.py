"""Shared configuration of the Linux build environment (linux/README.md).

The Apple parts a build needs live under one toolchain folder, NXN_TOOLCHAIN (default /data/toolchain), which linux/setup/setup.py fills:
  xcode/      the parts of the player's own Xcode .xip the build reads: the iOS and macOS SDKs, the Swift libraries and the clang runtime of its toolchain, its version.plist
  metal/      Apple's Metal Developer Tools for Windows (metal.exe, metallib.exe, ...), run under Wine
  wine/       the Wine prefix the Metal tools run in
  fonts/      the shared-font sources (Arial Unicode.ttf, Arial Rounded Bold.ttf from a Mac), optional
Nothing here is Apple's: the folder is the player's, filled from files they downloaded themselves.
"""
import json, os, plistlib, re, shutil, sys
from pathlib import Path

TOOLCHAIN = Path(os.environ.get('NXN_TOOLCHAIN', '/data/toolchain'))
XCODE = TOOLCHAIN / 'xcode'
DEVELOPER = XCODE / 'Contents/Developer'
METAL = TOOLCHAIN / 'metal'
WINEPREFIX = Path(os.environ.get('WINEPREFIX', TOOLCHAIN / 'wine'))
FONTS = TOOLCHAIN / 'fonts'
XCTC = DEVELOPER / 'Toolchains/XcodeDefault.xctoolchain'
LINUX = Path(__file__).resolve().parent.parent          # linux/ in the repository
REPO = LINUX.parent

SDKS = {   # xcrun's SDK names → (platform folder, SDK folder name prefix, clang OS name, AIR OS name)
    'iphoneos': ('iPhoneOS.platform', 'iPhoneOS', 'ios', 'ios'),
    'macosx': ('MacOSX.platform', 'MacOSX', 'macos', 'macos'),
}
ALIASES = {'macos': 'macosx', 'osx': 'macosx', 'ios': 'iphoneos'}


class Missing(Exception):
    """A part of the toolchain that setup.py has not installed yet"""


def sdk_name(name):
    """xcrun's -sdk argument (iphoneos, iphoneos26.2, macosx, a path to an SDK) → iphoneos / macosx"""
    if not name: return os.environ.get('NXN_DEFAULT_SDK', 'macosx')
    n = name.lower()
    if '/' in n: n = Path(n).name
    if n.startswith('iphoneos'): return 'iphoneos'
    if n.startswith('macosx') or n.startswith('macos'): return 'macosx'
    n = ALIASES.get(n, n)
    if n in SDKS: return n
    raise Missing(f'unsupported SDK {name!r}: this toolchain has iphoneos and macosx')


def sdk_path(sdk):
    plat, prefix, _, _ = SDKS[sdk_name(sdk)]
    d = DEVELOPER / 'Platforms' / plat / 'Developer/SDKs'
    exact = d / f'{prefix}.sdk'
    if exact.is_dir(): return exact
    found = sorted(p for p in d.glob(f'{prefix}*.sdk') if p.is_dir()) if d.is_dir() else []
    if found: return found[-1]
    raise Missing(f'no {prefix} SDK in {d}: install your Xcode .xip with linux/setup/setup.py xcode <Xcode.xip>')


def sdk_of_path(path):
    """The SDK name an -isysroot path names (None when it is neither)"""
    n = Path(path).name.lower()
    if n.startswith('iphoneos'): return 'iphoneos'
    if n.startswith('macosx'): return 'macosx'
    return None


def sdk_settings(sdk):
    p = sdk_path(sdk)
    try: s = json.loads((p / 'SDKSettings.json').read_text())
    except (OSError, ValueError): s = {}
    ver = s.get('Version') or re.sub(r'^[A-Za-z]+', '', p.name[:-4]) or ''
    build = ''
    try:
        with open(p / 'System/Library/CoreServices/SystemVersion.plist', 'rb') as f: build = plistlib.load(f).get('ProductBuildVersion', '')
    except (OSError, ValueError): pass
    return {'path': str(p), 'version': ver, 'build': build, 'platform_path': str(p.parent.parent.parent)}


def xcode_version():
    """(short version, build) of the installed Xcode, from its version.plist"""
    try:
        with open(XCODE / 'Contents/version.plist', 'rb') as f: v = plistlib.load(f)
        return v.get('CFBundleShortVersionString', ''), v.get('ProductBuildVersion', '')
    except (OSError, ValueError): return '', ''


def swift_resource_dir():
    d = XCTC / 'usr/lib/swift'
    if d.is_dir(): return d
    raise Missing(f'no Swift libraries in {d}: install your Xcode .xip with linux/setup/setup.py xcode <Xcode.xip>')


def clang_rt(os_name='ios'):
    """Xcode's compiler runtime for the OS (libclang_rt.ios.a): the builtins clang's driver links for @available and the like; None when absent"""
    for p in sorted((XCTC / 'usr/lib/clang').glob(f'*/lib/darwin/libclang_rt.{os_name}.a')): return p
    return None


def metal_tool(name, sdk=None):
    """The Windows executable of a Metal tool (metal, metallib, metal-pack, metal-tt, ...) in the installed Metal Developer Tools. The Windows tools come as one toolset per
    platform (ios\\bin\\metal.exe, macos\\bin\\metal.exe, which must not be mixed): sdk picks the one for iphoneos / macosx; a tool in no platform folder serves both"""
    index = METAL / '.nxn_tools.json'
    try: tools = json.loads(index.read_text())
    except (OSError, ValueError):
        tools = {}
        if METAL.is_dir():
            for p in sorted(METAL.rglob('*.exe')): tools.setdefault(p.stem.lower(), []).append(str(p.relative_to(METAL)))
            try: index.write_text(json.dumps(tools, indent=1))
            except OSError: pass
    cands = [METAL / p for p in tools.get(name.lower(), []) if (METAL / p).is_file()]
    if cands:
        want = {'iphoneos': 'ios', 'macosx': 'macos'}.get(sdk_name(sdk) if sdk else '', '')
        def plat(p): return next((x.lower() for x in p.relative_to(METAL).parts[:-1] if x.lower() in ('ios', 'macos', 'tvos', 'xros', 'visionos')), '')
        for c in cands:
            if want and plat(c) == want: return c
        for c in cands:
            if not plat(c): return c
        if not want: return cands[0]
        raise Missing(f'{name}.exe for {want} is not in the Metal Developer Tools in {METAL} (found: {", ".join(str(c.relative_to(METAL)) for c in cands)})')
    raise Missing(f'{name}.exe is not in the Metal Developer Tools in {METAL}' + ('' if tools else ': install them with linux/setup/setup.py metal <installer or zip>'))


def which(*names):
    for n in names:
        p = shutil.which(n)
        if p: return p
    return None


def llvm(tool):
    """An LLVM tool (clang, ld64.lld, llvm-ar, ...), the versioned name the distribution installs when there is no plain one"""
    p = which(tool)
    if p: return p
    for v in range(30, 14, -1):
        p = which(f'{tool}-{v}')
        if p: return p
    raise Missing(f'{tool} is not installed (the Docker image has it: linux/docker/Dockerfile)')


def die(msg, code=1):
    print(f'[RED] {msg}', file=sys.stderr)
    sys.exit(code)
