#!/usr/bin/env python3
"""The iOS launcher's Swift sources compiled on a Mac, for the Linux build (linux/README.md).

Launcher.swift uses SwiftUI's @State, which the iOS 27 SDK declares as a macro. A macro is expanded at compile time by a plugin of the compiler (SwiftUIMacros), and Apple ships
that plugin only as a macOS program: the Linux Swift compiler cannot load it. So the Swift sources are compiled on a macOS runner (.github/workflows/linux-image.yml, job swift)
with Apple's own compiler, into one object file that the Docker image carries (linux/prebuilt/), and linux/bin/xcodebuild links that object instead of compiling Swift.

The object depends on the Swift sources and the headers their bridging header reads, none of which depend on the game (CloudSaves.h, DmGameMenu.h, save_sync.h have no game
switches). key() is the digest of exactly those files and of the compile flags: xcodebuild takes the object only when its key matches the checkout's, else it stops and says so.

  prebuilt.py key                    the checkout's key
  prebuilt.py build <out folder>     on a Mac: compiles the sources (xcrun swiftc for iphoneos), writes DmIOS-swift.o and DmIOS-swift.json
"""
import hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCES = 'dm/ios/Sources'
BRIDGING = 'dm/ios/Sources/DmIOS-Bridging-Header.h'
INCLUDES = ('dm/ios/Sources', 'dm/host')   # where the bridging header's includes are (save_sync.h is in dm/host)
DEPLOY = '26.0'                             # dm/ios/project.yml's deploymentTarget
MODULE = 'DmIOS'
FLAGS = ['-O', '-swift-version', '5', '-parse-as-library', '-emit-object', '-whole-module-optimization']   # xcodebuild's Release Swift compile (linux/bin/xcodebuild swift())
OBJECT, INFO = 'DmIOS-swift.o', 'DmIOS-swift.json'


def swift_files(repo=REPO):
    return sorted(p for p in (repo / SOURCES).glob('*.swift'))


def inputs(repo=REPO):
    """Every file the object follows from: the Swift sources, the headers of the sources folder (the bridging header and what it imports), save_sync.h"""
    files = swift_files(repo) + sorted((repo / SOURCES).glob('*.h')) + [repo / 'dm/host/save_sync.h']
    return [p for p in files if p.is_file()]


def key(repo=REPO):
    h = hashlib.sha256()
    h.update(json.dumps({'deploy': DEPLOY, 'module': MODULE, 'flags': FLAGS, 'includes': INCLUDES}).encode())
    for p in inputs(repo):
        h.update(str(p.relative_to(repo)).encode() + b'\0' + p.read_bytes().replace(b'\r\n', b'\n') + b'\0')
    return h.hexdigest()[:24]


def command(repo, out, cache):
    cmd = ['xcrun', '-sdk', 'iphoneos', 'swiftc', '-target', f'arm64-apple-ios{DEPLOY}', '-module-name', MODULE, *FLAGS, '-module-cache-path', str(cache),
           '-import-objc-header', str(repo / BRIDGING)]
    for d in INCLUDES: cmd += ['-Xcc', f'-I{repo / d}']
    return cmd + ['-o', str(out), *map(str, swift_files(repo))]


def build(out_dir):
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform != 'darwin': sys.exit('prebuilt.py build runs on a Mac (Apple\'s swiftc expands SwiftUI\'s macros)')
    with tempfile.TemporaryDirectory() as cache:
        cmd = command(REPO, out_dir / OBJECT, cache)
        print(' '.join(cmd), flush=True)
        subprocess.run(cmd, check=True)
    ver = lambda *c: subprocess.run(c, capture_output=True, text=True).stdout.strip()
    info = {'key': key(), 'files': [p.name for p in swift_files()], 'target': f'arm64-apple-ios{DEPLOY}',
            'xcode': ver('xcodebuild', '-version').replace('\n', ' '), 'swiftc': ver('xcrun', 'swiftc', '--version').splitlines()[0] if ver('xcrun', 'swiftc', '--version') else '',
            'sdk': ver('xcrun', '--sdk', 'iphoneos', '--show-sdk-version'), 'commit': os.environ.get('GITHUB_SHA', '')}
    (out_dir / INFO).write_text(json.dumps(info, indent=1))
    print(f'{out_dir / OBJECT}: {(out_dir / OBJECT).stat().st_size} bytes, key {info["key"]} ({info["xcode"]})')


def load(folder):
    """(object path, info) of a prebuilt object in folder, or (None, None)"""
    folder = Path(folder)
    try: info = json.loads((folder / INFO).read_text())
    except (OSError, ValueError): return None, None
    return (folder / OBJECT if (folder / OBJECT).is_file() else None), info


if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['key']: print(key())
    elif a[:1] == ['build'] and len(a) == 2: build(a[1])
    else: sys.exit(__doc__)
