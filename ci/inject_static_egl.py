#!/usr/bin/env python3
"""给 mesa 源码注入 -Dstatic-libgl 时把 libEGL 一并静态内联进 libGL.so.1。

必须在 inject_static_libgl.py 之后执行：本脚本复用后者创建的
libgallium_dri_static，避免 gallium 被链接两份。

产物：libGL.so.1 同时导出 glX* 与 egl* 两套入口，运行时不再需要
独立的 libEGL.so.1 / libgallium-<ver>.so。

用法: python3 ci/inject_static_egl.py <mesa源码目录>
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
        sys.exit(f'FATAL: {rel} 找不到锚点: {anchor[:70]!r}')
    p.write_text(s.replace(anchor, addon, 1), encoding='utf-8')
    print(f'[ok]   {rel}')


# ---------------------------------------------------------------------
# 1) src/egl/meson.build：把 files_egl 编成静态库 libegl_static。
#    链接 libgallium_dri_static（static_library）而不是 libgallium_dri
#    (shared_library)：后者会给 libGL.so.1 添一条 libgallium 的
#    DT_NEEDED，单文件目标就破了。
# ---------------------------------------------------------------------
patch('src/egl/meson.build',
"""libegl = shared_library(
  egl_lib_name,
  files_egl,""",
"""# static-libgl 时把 EGL 的实现静态收进一个 archive，
# 供 src/glx/meson.build 里的 libGL.so.1 link_whole 拉入。
if get_option('static-libgl')
  libegl_static = static_library(
    'egl_static',
    files_egl,
    c_args : c_args_for_egl,
    cpp_args : cpp_args_for_egl,
    gnu_symbol_visibility : 'hidden',
    include_directories : incs_for_egl,
    link_with : [libgallium_dri_static],
    dependencies : [deps_for_egl, dep_dl, dep_libdrm, dep_clock, dep_thread, idep_mesautil],
  )
endif

libegl = shared_library(
  egl_lib_name,
  files_egl,""",
'libegl_static = static_library(')

# ---------------------------------------------------------------------
# 2) src/glx/meson.build：把 libegl_static 一起 link_whole 进 libGL.so.1。
#
#    必须 link_whole：EGL 的入口符号（eglGetDisplay 等）只被 dlopen 的
#    使用方引用，链接期看不到任何引用，link_with 会让整个 archive 被丢弃。
# ---------------------------------------------------------------------
patch('src/glx/meson.build',
"""  if get_option('static-libgl')
    # libgallium_dri_static 里承载驱动注册表的是 dri_target.c.o，""",
"""  if get_option('static-libgl')
    # EGL 实现一并静态内联，使 libGL.so.1 成为同时提供 glX* 与 egl*
    # 的单文件库，运行时不再需要独立的 libEGL.so.1。
    # 同样必须 link_whole：EGL 入口只被 dlopen 方引用，link_with 会整个丢弃。
    #
    # libegl_static 里 link_with : [libgallium_dri_static]，而后者
    # link_with [libmesa, libgallium, libglapi ...]。这些是 static_library，
    # 链接器按需拉取 .o，同名符号只保留一份，不会 multiple definition。
    # src/glx 自己的 -Ddri_bind_context=glx_dri_bind_context 等重命名只作用于
    # src/glx 下的源码；EGL 侧不含 dri_common.h，因此不会误伤。
    glx_gallium_whole += libegl_static
    # libgallium_dri_static 里承载驱动注册表的是 dri_target.c.o，""",
'glx_gallium_whole += libegl_static')

# ---------------------------------------------------------------------
# 3) src/meson.build：把 subdir('egl') 提到 subdir('glx') 之前。
#
#    meson 是顺序求值：subdir('glx') 期间 libGL.so.1 的参数就要展开，
#    那时若 libegl_static 还没定义就会直接报 undefined variable。
#    egl 需要 dep_gbm，所以 subdir('gbm') 也得一起提前。
#    gbm 不反向依赖 glx/egl，不会形成循环。
# ---------------------------------------------------------------------
patch('src/meson.build',
"""if with_glx == 'dri'
  subdir('glx')
endif

if with_gbm
  if get_option('libgbm-external')
    dep_gbm = dependency('gbm')
  else
    subdir('gbm')
  endif
else
  dep_gbm = null_dep
endif

if with_egl
  subdir('egl')
endif""",
"""# static-libgl 需要把 libegl_static link_whole 进 libGL.so.1，
# 因此 gbm 与 egl 必须先于 glx 处理（meson 顺序求值）。
# gbm 不依赖 glx/egl，提前不会形成循环。
if with_gbm
  if get_option('libgbm-external')
    dep_gbm = dependency('gbm')
  else
    subdir('gbm')
  endif
else
  dep_gbm = null_dep
endif

if with_egl
  subdir('egl')
endif

if with_glx == 'dri'
  subdir('glx')
endif""",
"static-libgl 需要把 libegl_static")

print('static-egl 注入完成')
