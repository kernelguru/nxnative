# nxnative's Linux builder: builds iOS IPAs from a player's own game files on Linux (linux/README.md). x86_64 only: Apple's Metal tools are Windows x64 programs, which Wine runs
# on x86_64. Build from the repository root:
#   docker build -f linux/docker/Dockerfile -t nxnative-linux .        (or: docker compose -f linux/docker/compose.yml up -d --build)
# The image holds no Apple file: the player's Xcode .xip and Metal Developer Tools go into the /data volume through the web interface (or linux/setup/setup.py).
# SWIFT_IMAGE: the Swift toolchain that compiles the launcher's SwiftUI sources against the SDK. It must be at least the Swift version of the player's Xcode (the SDK's Swift
# interfaces are read by that compiler): swift:noble is the newest release; pin one (swift:6.3-noble) when a newer one misbehaves.
ARG SWIFT_IMAGE=swift:noble

# ---- the repository's native tools for Linux: shader translator, NSP extraction, SPIRV-Cross ----
FROM mcr.microsoft.com/dotnet/sdk:10.0-noble AS tools
RUN apt-get update && apt-get install -y --no-install-recommends clang zlib1g-dev cmake git make ca-certificates && rm -rf /var/lib/apt/lists/*
ENV CC=clang CXX=clang++
WORKDIR /src
COPY dm/gfx/tools/dmsh_xlate dm/gfx/tools/dmsh_xlate
COPY dm/gfx/tools/spirv_cross_bin dm/gfx/tools/spirv_cross_bin
COPY dm/third_party/ryujinx dm/third_party/ryujinx
COPY dm/tools/nx_extract dm/tools/nx_extract
COPY linux/setup/build_tools.sh linux/setup/build_tools.sh
RUN mkdir -p dm/gfx/tools/dmsh_xlate_bin dm/tools/nx_extract_bin && WORK=/tmp/nxn_tools sh linux/setup/build_tools.sh && rm -rf /tmp/nxn_tools

# ---- the Mach-O linker: lld of an LLVM release (Ubuntu's LLVM 18 cannot read the iOS 27 SDK's .tbd files; NXNativeWindows' Linux build tested 23.1.4) ----
FROM ubuntu:24.04 AS lld
ARG LLVM_VERSION=23.1.4
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates xz-utils && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /opt/llvm && curl -fsSL "https://github.com/llvm/llvm-project/releases/download/llvmorg-${LLVM_VERSION}/LLVM-${LLVM_VERSION}-Linux-X64.tar.xz" \
      | tar -xJ -C /opt/llvm --strip-components=1 --wildcards '*/bin/lld' \
    && ln -s lld /opt/llvm/bin/ld64.lld && /opt/llvm/bin/ld64.lld --version

# ---- the builder ----
FROM ${SWIFT_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 PYTHONUNBUFFERED=1
RUN dpkg --add-architecture i386 && apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv procps make zstd glslang-tools git ca-certificates unzip p7zip-full cabextract xvfb xauth \
        lld llvm wine wine64 wine32:i386 winetricks libicu74 fonts-noto-core fonts-dejavu-core tini \
        g++ pkg-config libplist-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*
# ldid (ProcursusTeam): the ad-hoc signature that carries the IPA's entitlements
RUN git clone --depth 1 https://github.com/ProcursusTeam/ldid.git /tmp/ldid && make -C /tmp/ldid && install -m755 /tmp/ldid/ldid /usr/local/bin/ldid && rm -rf /tmp/ldid
# lld answers as the Mach-O linker under the name clang's Darwin driver asks for
COPY --from=lld /opt/llvm /opt/llvm
ENV NXN_LD64=/opt/llvm/bin/ld64.lld
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir lz4 "capstone>=5,<6" fonttools zbic backports.zstd nsz pyyaml pillow cryptography \
        fastapi "uvicorn[standard]" python-multipart httpx
COPY . /opt/nxnative
COPY --from=tools /src/dm/gfx/tools/dmsh_xlate_bin/dmsh_xlate /opt/nxnative/dm/gfx/tools/dmsh_xlate_bin/dmsh_xlate
COPY --from=tools /src/dm/tools/nx_extract_bin/nx_extract /opt/nxnative/dm/tools/nx_extract_bin/nx_extract
COPY --from=tools /src/dm/gfx/tools/spirv_cross_bin/spirv-cross /opt/nxnative/dm/gfx/tools/spirv_cross_bin/spirv-cross
# The checkout's own git state stays (the app records its commit, +dirty: the Linux tools replace the committed macOS ones in this copy only)
# (STD_FONT / LETTERS_FONT, the shared-font sources, are set per build by the web interface: the player's Arial fonts when given, else stand-ins)
ENV PATH=/opt/nxnative/linux/bin:/opt/venv/bin:$PATH NXN_TOOLCHAIN=/data/toolchain WINEPREFIX=/data/toolchain/wine NXN_DATA=/data
RUN chmod +x /opt/nxnative/linux/bin/* /opt/nxnative/linux/libexec/wine-tool /opt/nxnative/linux/libexec/darwin-* /opt/nxnative/linux/docker/entrypoint.sh \
    && python3 /opt/nxnative/linux/tests/test_linux_tools.py
VOLUME /data
EXPOSE 8080
WORKDIR /opt/nxnative
ENTRYPOINT ["tini", "--", "/opt/nxnative/linux/docker/entrypoint.sh"]
