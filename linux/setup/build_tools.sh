#!/bin/sh
# The repository's native tools built for Linux, into the paths where the scripts look for the macOS ones (the Docker image runs this once; linux/README.md):
#   dmsh_xlate  → dm/gfx/tools/dmsh_xlate_bin/dmsh_xlate    the shader translator (NativeAOT, as dm/tools/native_tools.sh builds it for macOS)
#   nx_extract  → dm/tools/nx_extract_bin/nx_extract         NSP extraction (LibHac)
#   spirv-cross → dm/gfx/tools/spirv_cross_bin/spirv-cross   the pinned SPIRV-Cross commit with ordered-ne.patch, as spirv_cross_bin/build.sh builds it
# The committed Mach-O files are replaced in this checkout only. Their fingerprints differ from dm/gfx/tools/dmsh_chain.lock, so linux/setup/setup.py writes the lock and the chain header
# again for this checkout (setup.py chain) once the Metal tools are installed: the Linux build's shaders are a chain version of their own, made and used only here.
# Needs the .NET SDK 10, cmake, git and a C++ compiler. Usage: build_tools.sh [dmsh_xlate] [nx_extract] [spirv-cross]   (default: all); WORK=folder for intermediate files
set -eu
REPO=$(cd "$(dirname "$0")/../.." && pwd)
WORK=${WORK:-$(mktemp -d -t nxn_tools.XXXXXX)}
mkdir -p "$WORK"
case $(uname -m) in x86_64 | amd64) RID=linux-x64 ;; aarch64 | arm64) RID=linux-arm64 ;; *) echo "[RED] unsupported machine $(uname -m)" >&2; exit 1 ;; esac

dotnet_tool() {   # <name> <csproj> <output>
    command -v dotnet > /dev/null || { echo '[RED] building the native tools needs the .NET SDK 10' >&2; exit 1; }
    dotnet publish "$2" -c Release -r "$RID" -nologo -v quiet --artifacts-path "$WORK/$1-artifacts" -o "$WORK/$1-$RID" > "$WORK/$1.log" 2>&1 \
        || { tail -30 "$WORK/$1.log" >&2; echo "[RED] $1 did not build for $RID" >&2; exit 1; }
    cp "$WORK/$1-$RID/$1" "$3"; chmod +x "$3"
    echo "$1 ($RID): source $("$3" --source-sha), sha256 $(sha256sum "$3" | cut -c1-16)"
}

spirv_cross() {
    HERE=$REPO/dm/gfx/tools/spirv_cross_bin
    TAG=$(sed -n 's/^TAG=\([^ ]*\).*/\1/p' "$HERE/build.sh"); COMMIT=$(sed -n 's/^COMMIT=\([0-9a-f]*\).*/\1/p' "$HERE/build.sh")
    rm -rf "$WORK/spirv-src" "$WORK/spirv-build"
    git clone --quiet --depth 1 --branch "$TAG" https://github.com/KhronosGroup/SPIRV-Cross.git "$WORK/spirv-src"
    got=$(git -C "$WORK/spirv-src" rev-parse HEAD); [ "$got" = "$COMMIT" ] || { echo "[RED] $TAG is commit $got, not the pinned $COMMIT" >&2; exit 1; }
    git -C "$WORK/spirv-src" apply "$HERE/ordered-ne.patch"
    SOURCE_DATE_EPOCH="$(git -C "$WORK/spirv-src" log -1 --format=%ct)" TZ=UTC cmake -S "$WORK/spirv-src" -B "$WORK/spirv-build" -DCMAKE_BUILD_TYPE=Release \
        -DSPIRV_CROSS_ENABLE_TESTS=OFF -DSPIRV_CROSS_SKIP_INSTALL=ON -DCMAKE_EXE_LINKER_FLAGS='-s' > "$WORK/spirv-configure.log"
    cmake --build "$WORK/spirv-build" --target spirv-cross -j "$(nproc)" > "$WORK/spirv-build.log"
    cp "$WORK/spirv-build/spirv-cross" "$HERE/spirv-cross"
    echo "spirv-cross $TAG ($COMMIT, $RID): $("$HERE/spirv-cross" --revision 2>&1 | head -1)"
}

for t in ${*:-dmsh_xlate nx_extract spirv-cross}; do
    case $t in
        dmsh_xlate) dotnet_tool dmsh_xlate "$REPO/dm/gfx/tools/dmsh_xlate/dmsh_xlate.csproj" "$REPO/dm/gfx/tools/dmsh_xlate_bin/dmsh_xlate" ;;
        nx_extract) dotnet_tool nx_extract "$REPO/dm/tools/nx_extract/nx_extract.csproj" "$REPO/dm/tools/nx_extract_bin/nx_extract" ;;
        spirv-cross) spirv_cross ;;
        *) echo "usage: build_tools.sh [dmsh_xlate] [nx_extract] [spirv-cross]" >&2; exit 2 ;;
    esac
done
