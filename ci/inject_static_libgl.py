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
"get_option('static-libgl')")

print('static-libgl 注入完成')
