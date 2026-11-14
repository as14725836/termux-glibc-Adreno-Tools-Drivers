#!/usr/bin/env python3
"""实验开关：用环境变量覆盖 DRAWCTXT_CREATE 的 flags。

LG_CTX_NO_PREAMBLE=1  去掉 KGSL_CONTEXT_PREAMBLE
LG_CTX_NO_SAVEGMEM=1  去掉 KGSL_CONTEXT_SAVE_GMEM
LG_CTX_NO_NOGMEM=1    去掉 KGSL_CONTEXT_NO_GMEM_ALLOC

用来定位 context 创建 flags 是否是 GPU fault 的诱因。
"""
import sys
from pathlib import Path

MARKER = 'LG-KGSL-CTXFLAGS'

PATCH = '''    /* @MARK@: 用环境变量裁剪 DRAWCTXT_CREATE flags（实验用） */
    {
        if (getenv("LG_CTX_NO_PREAMBLE"))
            req.flags &= ~KGSL_CONTEXT_PREAMBLE;
        if (getenv("LG_CTX_NO_SAVEGMEM"))
            req.flags &= ~KGSL_CONTEXT_SAVE_GMEM;
        if (getenv("LG_CTX_NO_NOGMEM"))
            req.flags &= ~KGSL_CONTEXT_NO_GMEM_ALLOC;
        fprintf(stderr, "[KGSL-CTX] flags=0x%x\\n", req.flags);
    }
'''.replace('@MARK@', MARKER)


def patch(root: Path):
    f = root / 'src/freedreno/drm/kgsl/kgsl_pipe.c'
    s = f.read_text()
    if MARKER in s:
        print('  = kgsl_pipe.c 已注入 ctx flags，跳过')
        return
    anchor = '    int ret = kgsl_pipe_safe_ioctl(dev->fd, IOCTL_KGSL_DRAWCTXT_CREATE, &req);\n'
    if anchor not in s:
        raise SystemExit('找不到 DRAWCTXT_CREATE 调用')
    s = s.replace(anchor, PATCH + '\n' + anchor, 1)
    for inc in ('#include <stdio.h>', '#include <stdlib.h>'):
        if inc not in s:
            s = s.replace('#include "kgsl_priv.h"',
                          '#include "kgsl_priv.h"\n' + inc, 1)
    f.write_text(s)
    print('  + kgsl_pipe.c: DRAWCTXT flags 实验开关')


if __name__ == '__main__':
    patch(Path(sys.argv[1] if len(sys.argv) > 1 else '.'))
