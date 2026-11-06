#!/usr/bin/env python3
"""给 mesa 源码注入 -Dstatic-libgl 支持（把 gallium 静态内联进 libGL.so.1）。
用法: python3 ci/inject_static_libgl.py <mesa源码目录>
幂等：重复执行不会重复插入。"""
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else '.')

def patch(rel, anchor, addon, mark):
    p = root / rel
    s = p.read_text(encoding='utf-8')
    if mark in s:
        print(f'[skip] {rel}')
        return
    if anchor not in s:
        sys.exit(f'FATAL: {rel} 找不到锚点')
    p.write_text(s.replace(anchor, addon, 1), encoding='utf-8')
    print(f'[ok]   {rel}')

patch('meson.options',
"""option(
  'glvnd',
  type : 'feature',
  deprecated: {'true': 'enabled', 'false': 'disabled'},
  description : 'Enable GLVND support.'
)
""",
"""option(
  'glvnd',
  type : 'feature',
  deprecated: {'true': 'enabled', 'false': 'disabled'},
  description : 'Enable GLVND support.'
)

option(
  'static-libgl',
  type : 'boolean',
  value : false,
  description : 'Statically link libgallium into libGL.so.1 (single-file zink).'
)
""",
"'static-libgl',")

patch('src/gallium/targets/dri/meson.build',
"""  install : true,
  name_suffix : libname_suffix,
)
""",
"""  install : true,
  name_suffix : libname_suffix,
)

# 静态版 libgallium：供 -Dstatic-libgl=true 时 libGL 内联，产出单文件 zink 库。
# link_whole libdri：静态合并后 libglx 需要通过 driBindContext / driDestroyContext
# 等符号调用 gallium 侧实现，必须全量拉入。与 libglx 的同名冲突
# (dri_bind_context / dri_unbind_context / dri_destroy_context) 已在
# src/glx/meson.build 里用 -D 宏把 glx 那份重命名成 glx_dri_*，两者彻底隔离。
libgallium_dri_static = static_library(
  'gallium_dri_static',
  files('dri_target.c'),
  include_directories : [
    inc_include, inc_src, inc_mesa, inc_gallium, inc_gallium_aux, inc_util, inc_gallium_drivers,
    inc_gallium_winsys, include_directories('../../frontends/dri'),
  ],
  gnu_symbol_visibility : 'hidden',
  link_with : [
    libmesa, libgalliumvl,
    libgallium, libglapi, libpipe_loader_static, libws_null, libwsw, libswdri,
    libswkmsdri, gallium_dri_link_with
  ],
  link_whole : [libdri, gallium_dri_link_whole],
  dependencies : [
    dep_libdrm, dep_llvm, dep_thread, idep_xmlconfig, idep_mesautil,
    driver_swrast, driver_r300, driver_r600, driver_radeonsi, driver_nouveau,
    driver_kmsro, driver_v3d, driver_vc4, driver_freedreno, driver_etnaviv,
    driver_tegra, driver_i915, driver_svga, driver_virgl,
    driver_panfrost, driver_iris, driver_lima, driver_zink, driver_d3d12,
    driver_asahi, driver_crocus, driver_rocket, driver_ethosu
  ],
)
""",
'libgallium_dri_static = static_library(')

patch('src/glx/meson.build',
"""extra_libs_libglx = []
extra_deps_libgl = []
extra_ld_args_libgl = []
c_args_libglx = []
""",
"""extra_libs_libglx = []
extra_deps_libgl = []
extra_ld_args_libgl = []
c_args_libglx = []

# static-libgl 时，libglx 与 gallium 的 libdri 会被链进同一个 libGL.so.1。
# 两边各有一份同名但签名不同的 dri_bind_context / dri_unbind_context /
# dri_destroy_context（glx 侧用 struct glx_context*，gallium 侧用
# struct dri_context*），动态库模式下符号表互相独立所以没事，静态合并后
# 就是 multiple definition。这里把 glx 那份重命名，两者彻底隔离。
# gallium 侧不 include src/glx/dri_common.h，所以 -D 不会误伤。
if get_option('static-libgl')
  c_args_libglx += [
    '-Ddri_bind_context=glx_dri_bind_context',
    '-Ddri_unbind_context=glx_dri_unbind_context',
    '-Ddri_destroy_context=glx_dri_destroy_context',
  ]
endif
""",
"glx_dri_bind_context")

patch('src/glx/meson.build',
"""glx_gallium_link = []
if with_dri
  glx_gallium_link += libgallium_dri
endif""",
"""glx_gallium_link = []
if with_dri
  if get_option('static-libgl')
    glx_gallium_link += libgallium_dri_static
  else
    glx_gallium_link += libgallium_dri
  endif
endif""",
"glx_gallium_link += libgallium_dri_static")

print('static-libgl 注入完成')
