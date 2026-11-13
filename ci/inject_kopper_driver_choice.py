#!/usr/bin/env python3
"""让 EGL/kopper 路径下的 gallium 驱动选择可配置，并支持无 /dev/dri 的 kgsl 场景。

两个问题：

1) vl_winsys_kopper.c 把 pipe_loader_drm_probe_fd() 的第三个参数（zink）
   写死成 true，于是 pipe_loader_drm.c 里 `if (zink) driver_name = "zink"`
   直接命中，MESA_LOADER_DRIVER_OVERRIDE / GALLIUM_DRIVER 全部被绕过。
   结果：编了 freedreno/kgsl 也永远只出 zink。

2) x11_dri3_open() 拿不到 DRM 节点时返回 -1，代码立刻掉进
   pipe_loader_vk_probe_dri()（纯软件路径），freedreno 根本没机会被调用，
   FD_FORCE_KGSL 也就无从生效。而 xmobox 这种完全没有 /dev/dri 节点的
   环境恰恰属于这种情况——kgsl 只能从 /dev/kgsl-3d0 访问。

补丁内容（都由环境变量控制，默认行为与上游一致）：

  MESA_KOPPER_ZINK=0     kopper 不再强制 zink，改让 loader 自动探测
  MESA_KOPPER_KGSL=<路径> fd 为 -1 时用这个 char device 顶替
                         （例如 /dev/kgsl-3d0）。配合
                         MESA_LOADER_DRIVER_OVERRIDE=freedreno 和
                         FD_FORCE_KGSL=1 即可在无 /dev/dri 的机器上跑
                         原生 freedreno。

用法: python3 ci/inject_kopper_driver_choice.py <mesa源码目录>
幂等：重复执行不会重复插入。
"""
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else '.')

REL = 'src/gallium/auxiliary/vl/vl_winsys_kopper.c'
MARK = 'MESA_KOPPER_KGSL'

p = root / REL
s = p.read_text(encoding='utf-8')

if MARK in s:
    print('[skip] %s' % REL)
    sys.exit(0)

# 1) 需要 util/u_debug.h 才能用 debug_get_bool_option / debug_get_option
if '#include "util/u_debug.h"' not in s:
    anchor = '#include "util/u_memory.h"'
    if anchor not in s:
        sys.exit('FATAL: %s 找不到 u_memory.h' % REL)
    s = s.replace(anchor, anchor + '\n#include "util/u_debug.h"', 1)
    print('[ok]   %s  + #include "util/u_debug.h"' % REL)

# 2) 需要 open()/O_RDWR 打开 kgsl 节点
if '#include <fcntl.h>' not in s:
    anchor = '#include <X11/Xlib-xcb.h>'
    if anchor not in s:
        sys.exit('FATAL: %s 找不到 Xlib-xcb.h' % REL)
    s = s.replace(anchor, '#include <fcntl.h>\n' + anchor, 1)
    print('[ok]   %s  + #include <fcntl.h>' % REL)

# 3) 核心：fd == -1 时允许用 kgsl char device 顶替，并让 zink 可配置
anchor = '''   scrn->fd = fd;
   bool success;
   if (fd != -1)
      success = pipe_loader_drm_probe_fd(&scrn->base.dev, fd, true);
   else
      success = pipe_loader_vk_probe_dri(&scrn->base.dev);'''

addon = '''   /* 上游这里把 zink 写死成 true，会跳过 pipe_loader_drm.c 里的
    * loader_get_driver_for_fd()，导致 MESA_LOADER_DRIVER_OVERRIDE
    * 和 GALLIUM_DRIVER 都没机会生效——编了 freedreno/kgsl 也只会出 zink。
    * 加个开关，默认 true 保持原行为；设 0 就走 DRM 自动探测。 */
   const bool kopper_zink = debug_get_bool_option("MESA_KOPPER_ZINK", true);

   /* 有些环境（例如 xmobox）根本没有 /dev/dri 节点，GPU 只能从
    * /dev/kgsl-3d0 这样的 char device 访问，于是 x11_dri3_open() 返回 -1，
    * 上游会直接掉进 pipe_loader_vk_probe_dri()（纯软件路径），
    * freedreno 永远没机会被调用。这里允许显式指定一个 char device
    * 当 fd 用：freedreno_device.c 在 FD_FORCE_KGSL=1 时会忽略这个 fd
    * 的内容，自己 open("/dev/kgsl-3d0")，所以传进去只要能过
    * pipe_loader_drm_probe_fd() 的 fd>=0 检查就够了。 */
   if (fd == -1 && !kopper_zink) {
      const char *kgsl_path = os_get_option("MESA_KOPPER_KGSL");
      if (kgsl_path && kgsl_path[0]) {
         int kgsl_fd = open(kgsl_path, O_RDWR | O_CLOEXEC);
         if (kgsl_fd >= 0)
            fd = kgsl_fd;   /* vl_kopper_screen_destroy 会 close(scrn->fd) */
      }
   }

   scrn->fd = fd;
   bool success;
   if (fd != -1)
      success = pipe_loader_drm_probe_fd(&scrn->base.dev, fd, kopper_zink);
   else
      success = pipe_loader_vk_probe_dri(&scrn->base.dev);'''

if anchor not in s:
    sys.exit('FATAL: %s 找不到 probe_fd 锚点' % REL)

s = s.replace(anchor, addon, 1)
print('[ok]   %s  probe_fd(zink=true) -> probe_fd(kopper_zink) + kgsl fd' % REL)

p.write_text(s, encoding='utf-8')
