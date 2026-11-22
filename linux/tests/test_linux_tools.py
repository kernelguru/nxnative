#!/usr/bin/env python3
"""Tests of the Linux stand-ins (linux/bin, linux/libexec, linux/setup/xip.py) that need neither Apple's files nor Wine: a fake toolchain folder, a fake wine that prints its
command line, a synthetic .xip. Run: python3 linux/tests/test_linux_tools.py"""
import io, json, lzma, os, plistlib, shutil, struct, subprocess, sys, tempfile, unittest, zlib
from pathlib import Path

LINUX = Path(__file__).resolve().parent.parent
REPO = LINUX.parent
sys.path.insert(0, str(LINUX / 'lib')); sys.path.insert(0, str(LINUX / 'setup'))


def run(cmd, env=None, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, env=env, **kw)


class Toolchain(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='nxn_test_'))
        tc = self.tmp / 'tc'
        for plat, pre, ver in (('iPhoneOS.platform', 'iPhoneOS', '26.2'), ('MacOSX.platform', 'MacOSX', '26.2')):
            sdk = tc / f'xcode/Contents/Developer/Platforms/{plat}/Developer/SDKs/{pre}.sdk'
            (sdk / 'System/Library/CoreServices').mkdir(parents=True)
            (sdk / 'System/Library/Frameworks/UIKit.framework').mkdir(parents=True)
            (sdk / 'SDKSettings.json').write_text(json.dumps({'Version': ver}))
            with open(sdk / 'System/Library/CoreServices/SystemVersion.plist', 'wb') as f: plistlib.dump({'ProductBuildVersion': '23C54'}, f)
        with open(tc / 'xcode/Contents/version.plist', 'wb') as f: plistlib.dump({'CFBundleShortVersionString': '27.2', 'ProductBuildVersion': '27C100'}, f)
        for plat in ('ios', 'macos'):   # Apple's layout: one toolset per platform
            (tc / f'metal/{plat}/bin').mkdir(parents=True)
            for t in ('metal', 'metallib'): (tc / f'metal/{plat}/bin/{t}.exe').write_text('')
        (tc / 'metal/bin').mkdir(); (tc / 'metal/bin/metal-tt.exe').write_text('')
        fake = self.tmp / 'fakebin'; fake.mkdir()
        for w in ('wine', 'wine64'):   # both names: the image has a real Wine, which the tests must not run
            (fake / w).write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n'); (fake / w).chmod(0o755)
        (fake / 'wineboot').write_text('#!/bin/sh\n'); (fake / 'wineboot').chmod(0o755)
        self.env = dict(os.environ, NXN_TOOLCHAIN=str(tc), PATH=f'{LINUX / "bin"}:{fake}:{os.environ["PATH"]}', WINEPREFIX=str(self.tmp / 'wine'))

    def tearDown(self): shutil.rmtree(self.tmp, ignore_errors=True)

    def test_xcrun_sdk(self):
        r = run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-version'], self.env)
        self.assertEqual(r.stdout.strip(), '26.2', r.stderr)
        r = run(['xcrun', '-sdk', 'iphoneos', '--show-sdk-path'], self.env)
        self.assertTrue(r.stdout.strip().endswith('iPhoneOS.sdk'))
        r = run(['xcrun', '-sdk', 'macosx', '--show-sdk-build-version'], self.env)
        self.assertEqual(r.stdout.strip(), '23C54')
        r = run(['xcrun', '-sdk', 'macosx', '-f', 'metal'], self.env)
        self.assertTrue(r.stdout.strip().endswith('libexec/metal/metal'), r.stderr)

    def test_metal_under_wine(self):
        r = run(['xcrun', '-sdk', 'iphoneos', 'metal', '-c', '-O1', '-mios-version-min=18.0', '-fmodules-cache-path=/tmp/mc', '-x', 'metal', '-', '-o', '/w/a.air'], self.env, input='')
        a = r.stdout.splitlines()
        self.assertTrue(a[0].endswith('ios/bin/metal.exe'), r.stdout + r.stderr)
        self.assertEqual(a[1:3], ['-target', 'air64-apple-ios18.0'])
        self.assertIn('-fmodules-cache-path=Z:\\tmp\\mc', a)
        self.assertIn('Z:\\w\\a.air', a)
        self.assertNotIn('-mios-version-min=18.0', a)
        sdk = run(['xcrun', '-sdk', 'macosx', '--show-sdk-path'], self.env).stdout.strip()
        metal = run(['xcrun', '-sdk', 'macosx', '-f', 'metal'], self.env).stdout.strip()
        r = run([metal, '-isysroot', sdk, '-c', 'x.metal'], self.env)
        self.assertTrue(r.stdout.splitlines()[0].endswith('macos/bin/metal.exe'), r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[1:3], ['-target', 'air64-apple-macos26.2'], r.stdout + r.stderr)
        r = run(['xcrun', '-sdk', 'iphoneos', 'metal-tt', '--version'], self.env)
        self.assertTrue(r.stdout.splitlines()[0].endswith('metal/bin/metal-tt.exe'), r.stdout + r.stderr)
        r = run(['xcrun', '-sdk', 'iphoneos', 'metallib', '/a/b.air', '-o', '/a/b.metallib'], self.env)
        self.assertEqual(r.stdout.splitlines()[1:], ['Z:\\a\\b.air', '-o', 'Z:\\a\\b.metallib'])
        r = run(['xcrun', '-sdk', 'iphoneos', 'metal', '--version'], self.env)
        self.assertEqual(r.stdout.splitlines()[1:], ['--version'])

    def test_wine_services_do_not_hold_the_pipe(self):
        """Wine's first program starts services.exe, which outlives it with that program's output: wine-tool starts it first, detached (nxn.wine_up), so a captured call ends"""
        fake = self.tmp / 'fakebin'
        svc = fake / 'services.exe'
        svc.write_text('#!/bin/sh\nsleep 20\n'); svc.chmod(0o755)
        up = self.tmp / 'services.up'   # as wineserver knows its services run: then no Wine program starts them again
        for w in ('wine', 'wine64'):   # starts the service as Wine does, inheriting its output, unless it runs
            (fake / w).write_text(f'#!/bin/sh\n[ -e "{up}" ] || {{ touch "{up}"; "{svc}" & }}\nprintf "%s\\n" "$@"\n'); (fake / w).chmod(0o755)
        (fake / 'wineboot').write_text(f'#!/bin/sh\ntouch "{up}"; "{svc}" < /dev/null > /dev/null 2>&1 &\n'); (fake / 'wineboot').chmod(0o755)
        try:
            r = run(['xcrun', '-sdk', 'iphoneos', 'metal', '--version'], self.env, timeout=10)
            self.assertEqual(r.stdout.splitlines()[1:], ['--version'], r.stderr)
        finally: subprocess.run(['pkill', '-f', str(svc)])

    def test_response_file(self):
        many = [f'/very/long/path/number/{i}/shader.air' for i in range(2000)]
        r = run(['xcrun', '-sdk', 'iphoneos', 'metallib', *many, '-o', '/o.metallib'], self.env)
        a = r.stdout.splitlines()
        self.assertEqual(len(a), 2, r.stderr); self.assertTrue(a[1].startswith('@Z:'))

    def test_xcodegen_plistbuddy_settings(self):
        proj = self.tmp / 'ios'; (proj / 'Sources').mkdir(parents=True); (proj / 'Gen/dm').mkdir(parents=True)
        shutil.copy(REPO / 'dm/ios/project.yml', proj)
        (proj / 'Gen/dm.xcconfig').write_text('HEADER_SEARCH_PATHS = $(inherited) "/a b/inc" /c\nOTHER_CFLAGS = $(inherited) -DX=1\nOTHER_LDFLAGS = $(inherited) $(DM_FRAMEWORKS) -Wl,-segprot,__DM0W,rw,rw\nDM_FRAMEWORKS = -framework Metal\n')
        env = dict(self.env, DM_BUNDLE_ID='org.nxnative.test', DM_DISPLAY_NAME='Test Game', DM_APP_VERSION='1.2.3', DM_APP_CATEGORY='public.app-category.games',
                   DM_SHOW_FPS='NO', DM_LANG='de', DM_TEAM_ID='0000000000', DM_SCHEME_NAME='DmIOS')
        r = run(['xcodegen', 'generate', '-q'], env, cwd=proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(proj / 'Gen/Info.plist', 'rb') as f: info = plistlib.load(f)
        self.assertEqual(info['CFBundleDisplayName'], 'Test Game'); self.assertEqual(info['DMLanguage'], 'de')
        r = run(['PlistBuddy', '-c', 'Add :DMCommit string abc123', proj / 'Gen/Info.plist'], env)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run(['PlistBuddy', '-c', 'Print :DMCommit', proj / 'Gen/Info.plist'], env)
        self.assertEqual(r.stdout.strip(), 'abc123')
        desc = json.loads((proj / 'DmIOS.xcodeproj/nxn_project.json').read_text())
        import xcsettings
        sys.path.insert(0, str(LINUX / 'bin'))
        st = xcsettings.Settings([{'PRODUCT_NAME': 'DmIOS', 'MARKETING_VERSION': '0'}, xcsettings.read_xcconfig(desc['configFiles']['Release']), desc['settings'],
                                  {'DM_XCODE_OUTPUT_ROOT': '/dd', 'CONFIGURATION': 'Release', 'EFFECTIVE_PLATFORM_NAME': '-iphoneos'}])
        self.assertEqual(st.list('HEADER_SEARCH_PATHS'), ['/a b/inc', '/c'])
        self.assertEqual(st.list('OTHER_CFLAGS'), ['-DX=1', '-mcpu=apple-m1'])
        self.assertEqual(st.get('CONFIGURATION_BUILD_DIR'), '/dd/Build/Products/Release-iphoneos')
        self.assertEqual(st.list('OTHER_LDFLAGS'), ['-framework', 'Metal', '-Wl,-segprot,__DM0W,rw,rw'])
        self.assertEqual(st.expand('$(MARKETING_VERSION)'), '1.2.3')

    def test_dtxcode(self):
        import importlib.machinery, importlib.util
        spec = importlib.util.spec_from_loader('xcb', importlib.machinery.SourceFileLoader('xcb', str(LINUX / 'bin/xcodebuild')))
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        self.assertEqual(m.dtxcode('27.2'), '2720'); self.assertEqual(m.dtxcode('16.4.1'), '1641')


def make_xip(path, files):
    """A minimal .xip: a xar with one file Content, a pbzx stream (one XZ chunk, one stored chunk) of an odc cpio archive"""
    cp = io.BytesIO()
    def entry(name, mode, data=b'', ino=0, nlink=1):
        nb = name.encode() + b'\0'
        cp.write(b'070707' + b'%06o%06o%06o%06o%06o%06o%06o%011o%06o%011o' % (1, ino, mode, 0, 0, nlink, 0, 0, len(nb), len(data)) + nb + data)
    for name, f in files.items(): entry(name, *f)
    entry('TRAILER!!!', 0)
    raw = cp.getvalue(); half = len(raw) // 2
    px = b'pbzx' + struct.pack('>Q', 1 << 24)
    a = lzma.compress(raw[:half], format=lzma.FORMAT_XZ)
    px += struct.pack('>QQ', half, len(a)) + a + struct.pack('>QQ', len(raw) - half, len(raw) - half) + raw[half:]
    toc = (f'<?xml version="1.0"?><xar><toc><file id="1"><name>Content</name><data><offset>0</offset><length>{len(px)}</length>'
           f'<encoding style="application/octet-stream"/></data></file></toc></xar>').encode()
    tz = zlib.compress(toc)
    with open(path, 'wb') as f: f.write(b'xar!' + struct.pack('>HHQQI', 28, 1, len(tz), len(toc), 0) + tz + px)


class Xip(unittest.TestCase):
    """linux/setup/extract_xip.py (NXNative for Windows' extractor) and setup.py's checks of what it wrote"""
    def setUp(self): self.tmp = Path(tempfile.mkdtemp(prefix='nxn_xip_'))
    def tearDown(self): shutil.rmtree(self.tmp, ignore_errors=True)

    def test_extract(self):
        import extract_xip
        sdk = './Xcode.app/Contents/Developer/Platforms/iPhoneOS.platform/Developer/SDKs'
        make_xip(self.tmp / 'x.xip', {
            './Xcode.app/Contents/version.plist': (0o100644, b'<plist/>'),
            f'{sdk}/iPhoneOS.sdk': (0o040755, b''),
            f'{sdk}/iPhoneOS.sdk/SDKSettings.json': (0o100644, b'{"Version": "26.2"}'),
            f'{sdk}/iPhoneOS26.2.sdk': (0o120755, b'iPhoneOS.sdk'),
            './Xcode.app/Contents/Developer/Platforms/WatchOS.platform/Developer/SDKs/WatchOS.sdk/x': (0o100644, b'no'),
            './Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/swift/iphoneos/libswiftCompatibility56.a': (0o100644, b'lib' * 1000),
            './Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/swift/watchos/x.a': (0o100644, b'no'),
            './Xcode.app/Contents/Applications/Simulator.app/x': (0o100644, b'no')})
        r = extract_xip.extract(self.tmp / 'x.xip', self.tmp / 'out', say=lambda m: None)
        o = self.tmp / 'out/Contents'
        self.assertEqual((o / 'Developer/Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS.sdk/SDKSettings.json').read_text(), '{"Version": "26.2"}')
        self.assertTrue((o / 'Developer/Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS26.2.sdk').is_symlink())
        self.assertEqual((o / 'Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/swift/iphoneos/libswiftCompatibility56.a').read_bytes(), b'lib' * 1000)
        self.assertFalse((o / 'Developer/Platforms/WatchOS.platform').exists())
        self.assertFalse((o / 'Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/swift/watchos').exists())
        self.assertFalse((o / 'Applications').exists())
        self.assertEqual((r['lost'], r['placeholders']), (0, []))

    def hard_link_xip(self, carrier=True):
        """Xcode 27's cpio: a hard-linked file's bytes with one entry, NULLcanary placeholders with the others (the iOS SDK's headers linking to the macOS SDK's, which come
        first, or to a platform the filter leaves out, or to an entry that comes after them)"""
        import extract_xip
        plat = './Xcode.app/Contents/Developer/Platforms'
        ph = lambda n: extract_xip.CANARY + b'\0' * (n - len(extract_xip.CANARY))
        ios, mac = f'{plat}/iPhoneOS.platform/Developer/SDKs/iPhoneOS.sdk', f'{plat}/MacOSX.platform/Developer/SDKs/MacOSX.sdk'
        self.found, self.uikit, tv = b'/* Foundation.h */\n' * 20, b'/* UIKit.h */\n' * 7, b'/* shared with tvOS */\n' * 9
        tc = './Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/swift'
        files = {
            './Xcode.app/Contents/version.plist': (0o100644, b'<plist/>'),
            f'{plat}/AppleTVOS.platform/Developer/SDKs/AppleTVOS.sdk/a.h': (0o100644, tv, 7, 2),                     # left out, but the iOS SDK links to it
            f'{mac}/SDKSettings.json': (0o100644, b'{"Version": "26.2"}'),
            f'{mac}/System/Library/Frameworks/Foundation.framework/Headers/Foundation.h': (0o100644, self.found, 5, 2),
            f'{ios}/SDKSettings.json': (0o100644, b'{"Version": "26.2"}'),
            f'{ios}/System/Library/Frameworks/Foundation.framework/Headers/Foundation.h': (0o100644, ph(len(self.found)), 5, 2),
            f'{ios}/System/Library/Frameworks/UIKit.framework/Headers/UIKit.h': (0o100644, ph(len(self.uikit)), 9, 2),   # its bytes come later
            f'{ios}/a.h': (0o100644, ph(len(tv)), 7, 2),
            f'{ios}/zz/UIKit-copy.h': (0o100644, self.uikit, 9, 2),
            f'{tc}/iphoneos/libswiftCore.tbd': (0o100644, b'--- tbd'),
        }
        if not carrier: files[f'{ios}/d.h'] = (0o100644, ph(40), 11, 2)   # no entry has its bytes
        make_xip(self.tmp / 'x.xip', files)
        return extract_xip.extract(self.tmp / 'x.xip', self.tmp / 'out', say=lambda m: None)

    def test_hard_links(self):
        import setup
        r = self.hard_link_xip()
        ios = self.tmp / 'out/Contents/Developer/Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS.sdk'
        self.assertEqual((ios / 'System/Library/Frameworks/Foundation.framework/Headers/Foundation.h').read_bytes(), self.found)
        self.assertEqual((ios / 'System/Library/Frameworks/UIKit.framework/Headers/UIKit.h').read_bytes(), self.uikit)
        self.assertEqual((ios / 'a.h').read_bytes(), b'/* shared with tvOS */\n' * 9)
        self.assertEqual((r['lost'], r['placeholders']), (0, []))
        self.assertFalse((self.tmp / 'out/Contents/Developer/Platforms/AppleTVOS.platform').exists())
        self.assertEqual(setup.validate_xcode(self.tmp / 'out', r), [])

    def test_validation_refuses(self):
        import setup
        r = self.hard_link_xip(carrier=False)
        self.assertEqual(r['lost'], 1)
        problems = setup.validate_xcode(self.tmp / 'out', r)
        self.assertTrue(any('no entry that carries' in p for p in problems), problems)
        (self.tmp / 'out/Contents/Developer/Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS.sdk/System/Library/Frameworks/UIKit.framework/Headers/UIKit.h').unlink()
        self.assertTrue(any('UIKit.h' in p for p in setup.validate_xcode(self.tmp / 'out')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
