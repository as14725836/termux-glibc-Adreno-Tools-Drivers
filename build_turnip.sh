#!/bin/bash -e
set -o pipefail

deps="git meson ninja patchelf unzip curl pip flex bison zip glslangValidator python3"
workdir="$(pwd)/turnip_workdir"
mesasrc="https://github.com/whitebelyash/mesa-tu8.git"
srcfolder="mesa"
BUILD_VERSION="${BUILD_VERSION:-1.0}"

run_all(){
    check_deps
    prepare_workdir
    build_lib_for_linux gen8
}

check_deps(){
    for deps_chk in $deps; do
        if ! command -v "$deps_chk" >/dev/null 2>&1 ; then
            echo "Missing dependency: $deps_chk"
            exit 1
        fi
    done
    pip install mako --break-system-packages &> /dev/null || true
}

prepare_workdir(){
    mkdir -p "$workdir" && cd "$workdir"

    rm -rf "$srcfolder"
    git clone "$mesasrc" --depth=1 --no-single-branch "$srcfolder"
    cd "$srcfolder"
}

build_lib_for_linux(){
    cd "$workdir/$srcfolder"
    git checkout "origin/$1"

    sed -i 's/ (%s)//g' src/freedreno/vulkan/tu_device.cc || true
    sed -i 's/ (%s)//g' src/freedreno/vulkan/tu_device.c || true

    sed -i '/a7xx_gen1 = GPUProps(/a \        has_early_preamble = False,' src/freedreno/common/freedreno_devices.py || true
    sed -i 's/typedef const native_handle_t\* buffer_handle_t;/typedef void\* buffer_handle_t;/g' include/android_stub/cutils/native_handle.h || true
    sed -i 's/, hnd->handle/, (void \*)hnd->handle/g' src/util/u_gralloc/u_gralloc_fallback.c || true
    sed -i 's/native_buffer->handle->/((const native_handle_t \*)native_buffer->handle)->/g' src/vulkan/runtime/vk_android.c || true
    sed -i 's/anb->handle->/((const native_handle_t \*)anb->handle)->/g' src/vulkan/runtime/vk_android.c || true

    GITHASH=$(git rev-parse --short HEAD)

    echo "Generating build files for Linux ..."
    meson setup build-linux \
            --libdir=lib \
            --prefix "/tmp/turnip-$1" \
            -Dbuildtype=release \
            -Dplatforms=x11 \
            -Degl-native-platform=x11 \
            -Dglx=dri \
            -Dglx-direct=true \
            -Dopengl=true \
            -Dgles1=enabled \
            -Dgles2=enabled \
            -Dglvnd=disabled \
            -Degl=enabled \
            -Dllvm=disabled \
            -Dshared-llvm=disabled \
            -Dshader-cache=enabled \
            -Dshared-glapi=enabled \
            -Dxlib-lease=enabled \
            -Dvulkan-beta=true \
            -Dlibunwind=disabled \
            -Dvalgrind=disabled \
            -Dmicrosoft-clc=disabled \
            -Dgallium-rusticl=false \
            -Dgallium-extra-hud=true \
            -Dgallium-drivers=zink,freedreno,virgl,softpipe \
            -Dgallium-rusticl-enable-drivers=freedreno \
            -Dshader-cache-default=true \
            -Dvulkan-drivers=freedreno \
            -Dfreedreno-kmds=kgsl \
            -Dvulkan-layers=anti-lag \
            -Dvideo-codecs=all \
            -Dgbm=enabled \
            -Db_ndebug=true \
            -Dstrip=true \
		    --reconfigure

    echo "Compiling ..."
    ninja -C build-linux install

    if [ ! -f "/tmp/turnip-$1/lib/libvulkan_freedreno.so" ]; then
        echo "Build failed!"
        exit 1
    fi

    cd "/tmp/turnip-$1/lib"
    
    zip -9 "/tmp/a8xx-$1-V${BUILD_VERSION}.zip" libvulkan_freedreno.so
    cp "/tmp/a8xx-$1-V${BUILD_VERSION}.zip" "$workdir/"
    
    echo "Build complete: $workdir/a8xx-$1-V${BUILD_VERSION}.zip"
}

run_all
