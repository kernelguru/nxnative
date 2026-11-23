# Building IPAs on Linux

This folder holds a builder that runs nxnative's IPA build (`scripts/build_ipa_from_nsp.py`) on Linux, in Docker, behind a web interface. The output is the same unsigned IPA a Mac makes. The game still runs on the iPhone or iPad. The only change is where the IPA is built.

**Status: experimental, untested with real game files.** The stand-ins below are checked by `linux/tests/test_linux_tools.py`, and a small iOS app built with them links correctly (Mach-O arm64, platform iOS, the `__DM*W` segments rw). No game has been built with them end to end yet. Logs of a first real build are welcome in an issue.

## What it needs

- An x86_64 Linux host with Docker (the Metal tools are Windows x64 programs, which Wine runs on x86_64). Plan for about 20 GB for the image and toolchain, and as much disk per game as on a Mac.
- **Your own downloads from Apple** (developer.apple.com, free account). They are installed into the data volume and never into the image:
  - the **Xcode `.xip`** (the version `dm/gfx/tools/own_compilers.py` names). Only the iOS and macOS SDKs and the toolchain's Swift and clang libraries are unpacked from it, about 3 GB.
  - the **Metal Developer Tools for Windows**: the installer, or a `.zip` of `C:\Program Files\Metal Developer Tools` from a Windows PC. `metal.exe` and `metallib.exe` run under Wine.
  - optionally, **Arial Unicode.ttf** and **Arial Rounded Bold.ttf** from a Mac (`/System/Library/Fonts/Supplemental`), the shared-font sources. Without them free stand-ins are used, and the shared fonts look different.
- Your `prod.keys` and game files, as for the Mac build.

Apple's Xcode licence covers use on Apple hardware. Using its SDK on another system is your own decision.

## Start

```sh
docker compose -f linux/docker/compose.yml up -d            # the image from ghcr.io (.github/workflows/linux-image.yml); the web interface is on port 8080
docker compose -f linux/docker/compose.yml up -d --build    # or built from this checkout, from the repository root
```

Or without compose: `docker run -d -p 8080:8080 -v /mnt/user/appdata/nxnative:/data ghcr.io/kernelguru/nxnative-linux:latest`.

Then, in the web interface:

1. **Setup**: upload the Xcode `.xip`, then the Metal tools. Then upload `prod.keys` and, if you have them, the fonts. The chain lock is written automatically once the SDK and the Metal tools are both in place.
2. **Games**: create a folder and upload NSP / NSZ / XCI files (base game, updates and DLC together), or search your **RomM** library and import from it.
3. **Builds**: choose the folder and build. The log follows the NSP script's stages, and the IPA can be downloaded when it is done. It can also be copied to `NXN_IPA_EXPORT`, for example a folder that a Feather or AltStore source serves.

Settings are passed through the environment in `compose.yml`:

| Variable | Purpose |
|---|---|
| `NXN_PASSWORD` | HTTP Basic login for the web interface (user `nxnative`). Leave it empty only behind an authenticating reverse proxy |
| `ROMM_URL`, `ROMM_TOKEN` or `ROMM_USER` / `ROMM_PASSWORD` | RomM: the site address, and a client API token (`rmm_…`) or a login. They can also be set in the web interface |
| `NXN_IPA_EXPORT` | a folder in the container where finished IPAs are also copied |

Without the web interface, the same steps run in the container's shell:

```sh
python3 linux/setup/setup.py xcode /data/uploads/Xcode_27.2.xip
python3 linux/setup/setup.py metal /data/uploads/MetalDeveloperTools.zip
python3 linux/setup/setup.py chain
python3 linux/setup/setup.py status
python3 scripts/build_ipa_from_nsp.py /data/games/<folder> -k /data/keys/prod.keys
```

### Memory

A build uses as much memory as it can get. The NSP script rewrites a game's code modules side by side, each with several workers. A large game such as Mario Kart World needs 12 GB or more, or swap to make up the difference. `compose.yml` therefore sets two things:

- a memory limit for the container (`NXN_MEM_LIMIT`, `NXN_MEMSWAP_LIMIT`). When memory runs out, the kernel stops the build instead of the whole machine freezing.
- fewer workers (`DM_REWRITE_J=2`, `J=4`). Raise both or remove the lines on a machine with 16 GB or more.

## How it works

The NSP script and `dm/ios/tools/build_ipa.sh` run unchanged. `linux/bin`, first on `PATH`, answers the Apple tools they call:

| Mac tool | Linux stand-in |
|---|---|
| `xcrun` | `linux/bin/xcrun`: SDK paths and versions from the installed SDK. It runs the Metal tools under Wine (`linux/libexec/wine-tool`: the platform from `-isysroot` or `-sdk` as `-target air64-apple-<os><version>`, paths as `Z:`, long command lines through a response file), clang for the SDK (`darwin-cc`), swiftc for the SDK (`darwin-swiftc`) and LLVM's tools |
| `xcodegen` | reads `dm/ios/project.yml` and writes `Info.plist` and the project description that the `xcodebuild` stand-in reads |
| `xcodebuild` | builds the app: the build settings as Xcode layers them, a clang compile for every source, one whole-module Swift compile with the bridging header, the link with lld (`ld64.lld`), and the bundle. The app icon goes in as PNG files listed in `CFBundleIcons`, because there is no `actool` |
| Swift (the SwiftUI launcher) | compiled on a macOS runner by GitHub Actions (`linux/swift/prebuilt.py`, job `swift` of `.github/workflows/linux-image.yml`): the iOS 27 SDK's SwiftUI declares `@State` as a macro, whose plugin only macOS can run. The image carries the object file, and `xcodebuild` links it when it was built for the checkout's Swift sources. An image built locally (`--build`) has no such object, so use the one from ghcr.io |
| `PlistBuddy`, `sips`, `codesign`, `xcode-select` | Python `plistlib`, Pillow, `ldid` (ad-hoc signature with entitlements, as `build_ipa.sh` makes it) |

The Xcode `.xip` is unpacked by `linux/setup/extract_xip.py`, taken from [NXNative for Windows](https://github.com/zenvyzenshiro/NXNativeWindows) (`win/sdk/extract_xip.py`, MIT licence, `linux/setup/LICENSE-extract_xip`). Its reading of the archive is unchanged, including the two passes that fill the hard links of Xcode 27's deduplicated archive (`NULLcanary` placeholders). The Linux copy also keeps the macOS SDK and the toolchain's Swift and clang libraries, and writes symlinks as symlinks. `setup.py` then checks the result as NXNative for Windows' Easy Builder does: Foundation and UIKit headers, iOS SDK 26 or later, no placeholder left. It also checks the macOS SDK and the Swift libraries, and installs nothing if a check fails.

The shader translator, NSP extraction and SPIRV-Cross are built for Linux in the image (`linux/setup/build_tools.sh`), from the same sources and pinned commit. Their fingerprints differ from `dm/gfx/tools/dmsh_chain.lock`, so `setup.py chain` writes the lock and `dm/gfx/nvn/dmsh_chain.h` again in the container's checkout. The Linux build's shaders are therefore a chain version of their own, made and used only there. The host built into the IPA is compiled with the same header, so the two match.

## What it cannot do

- **Games whose build boots the game in the Mac host** need macOS with Metal. That covers run-time shader captures (GLSLC: Fire Emblem: Three Houses, Hyrule Warriors: Definitive Edition, the Koei Tecmo and Source-engine games; Vulkan and GL captures: Super Mario Galaxy 1 and 2, Super Mario 3D All-Stars, DOOM 3, Balatro, Well Dweller), nn::jit captures (Super Mario 64), and shaders packed with Oodle that only the game's own decoder reads: Unreal Engine shader archives in Oodle, Game Freak's Trinity packs (Pokémon Legends: Z-A), Bayonetta 3. The Linux build stops on these with a message, at the start where it can tell and otherwise when it reaches the step. Unreal games whose shaders use LZ4 build normally.
- **The offline pipeline archive** (`--gpu`, `metal-tt`) is only made if the Windows Metal tools include `metal-tt`. Without it the device builds its pipeline bases on its first start, which is slower.
- **The provisional heap and region checks** for a game without a profile also boot the game. On Linux they are skipped and the profile keeps its guesses.
- `--platform mac` and `mac-app` (Mac builds) and `--prepare-xcode` need a Mac.

## Troubleshooting

- `setup.py status` lists what is missing.
- The Swift compile fails reading the SDK's Swift interfaces: the image's Swift is older than the Xcode's. Rebuild with a matching `SWIFT_IMAGE` (`docker compose build --build-arg SWIFT_IMAGE=swift:<version>-noble`).
- `metal.exe` does not run: create the Wine prefix again (delete `/data/toolchain/wine`, use **Create Wine prefix**, then install the Metal tools again). The tools are installed with the Visual C++ runtime (winetricks) only when they do not start without it.
- The chain lock is written in the background a minute after the container starts, and only when no lock kept in the volume fits the tools. Its log is `/data/toolchain/chain.log`.
- A compile error in Apple's headers: `NXN_CLANG_MODULES=0` turns off clang modules, and `NXN_VERBOSE=1` shows every compiler warning.
- Tests: `python3 linux/tests/test_linux_tools.py`.
