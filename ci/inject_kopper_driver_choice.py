#!/usr/bin/env python3
"""让 EGL/kopper 路径在没有 /dev/dri 的环境下也能跑原生 freedreno(kgsl)。

问题：切到 kgsl 后 OpenGL 仍然显示 zink。

走 EGL 的程序最终进 src/gallium/frontends/dri/kopper.c 的
kopper_init_screen()：

    if (screen->fd != -1)
       success = pipe_loader_drm_probe_fd(&screen->dev, screen->fd, false);
    else
       success = pipe_loader_vk_probe_dri(&screen->dev);

zink 参数这里已经是 false，所以 MESA_LOADER_DRIVER_OVERRIDE 本来该生效。
但 screen->fd 是 EGL platform 从 /dev/dri/renderD* 拿的；xmobox 这类环境
/dev/dri 是空的、GPU 只能从 /dev/kgsl-3d0 访问，于是 fd == -1，
直接掉进 pipe_loader_vk_probe_dri()（纯软件路径），
freedreno 永远没机会被调用，FD_FORCE_KGSL 也就无从生效。

改法：fd == -1 且显式指定了 MESA_KOPPER_KGSL 时，用该 char device 当 fd。
默认不设这个变量，行为与上游完全一致。

fd 传进 freedreno 后，freedreno_device.c 的 FD_FORCE_KGSL 分支在函数最开头
就会自己 open("/dev/kgsl-3d0") 并 goto out，跳过 drmGetVersion，
所以这个 fd 只需要通过 pipe_loader_drm_probe_fd() 的 fd >= 0 检查。

用法:
  MESA_KOPPER_KGSL=/dev/kgsl-3d0 \\
  MESA_LOADER_DRIVER_OVERRIDE=freedreno FD_FORCE_KGSL=1 <程序>

顺带给 vl_winsys_kopper.c 也加上同样的兜底并让它的 zink 参数可配置
（上游在那里写死了 true），供非 EGL 的多媒体路径使用。

用法: python3 ci/inject_kopper_driver_choice.py <mesa源码目录>
幂等：重复执行不会重复插入。
"""
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else '.')


def read(rel):
    p = root / rel
    if not p.exists():
        print('[miss] %s' % rel)
        return None, None
    return p, p.read_text(encoding='utf-8')


def ensure_include(s, rel, want, anchor=None):
    if want in s:
        return s
    if anchor is None or anchor not in s:
        print('[warn] %s 找不到 include 锚点，跳过 %s' % (rel, want))
        return s
    print('[ok]   %s  + %s' % (rel, want))
    return s.replace(anchor, want + '\n' + anchor, 1)


# ---------------------------------------------------------------- kopper.c
REL = 'src/gallium/frontends/dri/kopper.c'
MARK = 'MESA_KOPPER_KGSL'
p, s = read(REL)

if p is not None and MARK not in s:
    s = ensure_include(s, REL, '#include <fcntl.h>', '#include "dri_screen.h"')
    s = ensure_include(s, REL, '#include "util/u_debug.h"',
                       '#include "util/u_memory.h"')

    anchor = '''   if (screen->fd != -1)
      success = pipe_loader_drm_probe_fd(&screen->dev, screen->fd, false);
   else
      success = pipe_loader_vk_probe_dri(&screen->dev);'''

    addon = '''   /* 上游这里在 fd == -1 时直接掉进 pipe_loader_vk_probe_dri()，
    * 也就是纯软件路径。但有些环境（例如 xmobox）根本没有 /dev/dri
    * 节点，GPU 只能通过 /dev/kgsl-3d0 这样的 char device 访问，
    * EGL platform 拿不到 renderD* 就传来 -1，于是原生 freedreno
    * 永远没有机会被调用——编了 kgsl 也只会出 zink。
    * 允许用 MESA_KOPPER_KGSL 显式指定一个 char device 顶替：
    * freedreno_device.c 在 FD_FORCE_KGSL=1 时会忽略这个 fd 的内容，
    * 自己 open("/dev/kgsl-3d0")，所以它只要 fd >= 0 就够。
    * 不设这个变量时行为与上游完全一致。 */
   int probe_fd = screen->fd;
   if (probe_fd == -1) {
      const char *kgsl_path = os_get_option("MESA_KOPPER_KGSL");
      if (kgsl_path && kgsl_path[0]) {
         int kgsl_fd = open(kgsl_path, O_RDWR | O_CLOEXEC);
         if (kgsl_fd >= 0)
            probe_fd = kgsl_fd;
      }
   }

   if (probe_fd != -1)
      success = pipe_loader_drm_probe_fd(&screen->dev, probe_fd, false);
   else
      success = pipe_loader_vk_probe_dri(&screen->dev);'''

    if anchor not in s:
        sys.exit('FATAL: %s 找不到 probe_fd 锚点' % REL)
    s = s.replace(anchor, addon, 1)
    print('[ok]   %s  fd==-1 时支持 MESA_KOPPER_KGSL' % REL)
    p.write_text(s, encoding='utf-8')
elif p is not None:
    print('[skip] %s' % REL)

# ------------------------------------------------- vl_winsys_kopper.c
REL2 = 'src/gallium/auxiliary/vl/vl_winsys_kopper.c'
p2, s2 = read(REL2)

if p2 is not None and MARK not in s2:
    s2 = ensure_include(s2, REL2, '#include <fcntl.h>', '#include <X11/Xlib-xcb.h>')
    s2 = ensure_include(s2, REL2, '#include "util/u_debug.h"',
                        '#include "util/u_memory.h"')

    anchor2 = '''   if (fd != -1)
      success = pipe_loader_drm_probe_fd(&scrn->base.dev, fd, true);
   else
      success = pipe_loader_vk_probe_dri(&scrn->base.dev);'''

    addon2 = '''   /* 上游这里把 zink 写死成 true，会跳过 pipe_loader_drm.c 里的
    * loader_get_driver_for_fd()，MESA_LOADER_DRIVER_OVERRIDE 就没机会生效。
    * 加个开关，默认 true 保持原行为。 */
   const bool kopper_zink = debug_get_bool_option("MESA_KOPPER_ZINK", true);

   /* 同 kopper.c：没有 /dev/dri 时允许用 char device 顶替 fd。 */
   if (fd == -1 && !kopper_zink) {
      const char *kgsl_path = os_get_option("MESA_KOPPER_KGSL");
      if (kgsl_path && kgsl_path[0]) {
         int kgsl_fd = open(kgsl_path, O_RDWR | O_CLOEXEC);
         if (kgsl_fd >= 0)
            fd = kgsl_fd;   /* vl_kopper_screen_destroy 会 close(scrn->fd) */
      }
   }

   if (fd != -1)
      success = pipe_loader_drm_probe_fd(&scrn->base.dev, fd, kopper_zink);
   else
      success = pipe_loader_vk_probe_dri(&scrn->base.dev);'''

    if anchor2 not in s2:
        sys.exit('FATAL: %s 找不到 probe_fd 锚点' % REL2)
    s2 = s2.replace(anchor2, addon2, 1)
    print('[ok]   %s  probe_fd(zink) 可配置 + kgsl fd' % REL2)
    p2.write_text(s2, encoding='utf-8')
elif p2 is not None:
    print('[skip] %s' % REL2)
