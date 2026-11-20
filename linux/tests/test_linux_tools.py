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
        (fake / 'wine').write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n'); (fake / 'wine').chmod(0o755)
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
    def entry(name, mode, data=b''):
        nb = name.encode() + b'\0'
        cp.write(b'070707' + b'%06o%06o%06o%06o%06o%06o%06o%011o%06o%011o' % (0, 0, mode, 0, 0, 1, 0, 0, len(nb), len(data)) + nb + data)
    for name, (mode, data) in files.items(): entry(name, mode, data)
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
    def test_extract(self):
        import xip
        tmp = Path(tempfile.mkdtemp(prefix='nxn_xip_'))
        try:
            sdk = './Xcode.app/Contents/Developer/Platforms/iPhoneOS.platform/Developer/SDKs'
            make_xip(tmp / 'x.xip', {
                './Xcode.app/Contents/version.plist': (0o100644, b'<plist/>'),
                f'{sdk}/iPhoneOS.sdk': (0o040755, b''),
                f'{sdk}/iPhoneOS.sdk/SDKSettings.json': (0o100644, b'{"Version": "26.2"}'),
                f'{sdk}/iPhoneOS26.2.sdk': (0o120755, b'iPhoneOS.sdk'),
                './Xcode.app/Contents/Developer/Platforms/WatchOS.platform/Developer/SDKs/WatchOS.sdk/x': (0o100644, b'no'),
                './Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/swift/iphoneos/libswiftCompatibility56.a': (0o100644, b'lib' * 1000),
                './Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/swift/watchos/x.a': (0o100644, b'no'),
                './Xcode.app/Contents/Applications/Simulator.app/x': (0o100644, b'no')})
            kept, n = xip.extract(tmp / 'x.xip', tmp / 'out')
            o = tmp / 'out/Contents'
            self.assertEqual((o / 'Developer/Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS.sdk/SDKSettings.json').read_text(), '{"Version": "26.2"}')
            self.assertTrue((o / 'Developer/Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS26.2.sdk').is_symlink())
            self.assertEqual((o / 'Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/swift/iphoneos/libswiftCompatibility56.a').read_bytes(), b'lib' * 1000)
            self.assertFalse((o / 'Developer/Platforms/WatchOS.platform').exists())
            self.assertFalse((o / 'Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/swift/watchos').exists())
            self.assertFalse((o / 'Applications').exists())
            self.assertEqual(n, 8)
        finally: shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
