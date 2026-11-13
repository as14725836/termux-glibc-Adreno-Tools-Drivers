#!/usr/bin/env python3
"""给 kgsl 的 GPU_COMMAND 提交加详细诊断，用来定位 EDEADLK。

只在 LG_KGSL_DIAG=1 时输出，默认零开销（除了一个 getenv 缓存）。

打印内容：context_id / cmd_idx / 每条 IB 的 gpuaddr,size,flags,id /
BO 的 handle,iova,size / numsyncs / 返回的 errno 与 timestamp。
另外在 ioctl 前后各打印一次，方便确认是不是"每次都失败"还是"偶发"。

用法: python3 ci/inject_kgsl_submit_diag.py <mesa源码目录>
幂等：'LG-KGSL-DIAG'
"""
import sys
from pathlib import Path

MARKER = 'LG-KGSL-DIAG'

INSERT = '''    /* @MARK@ */
    static int diag = -1;
    if (diag < 0)
        diag = getenv("LG_KGSL_DIAG") != NULL;
    if (diag) {
        fprintf(stderr, "[KGSL-DIAG] ctx=%u numcmds=%u numsyncs=%u infence=%d\\n",
                kgsl_pipe->queue_id, cmd_idx, req.numsyncs, fd_submit->in_fence_fd);
        for (unsigned d = 0; d < cmd_idx; d++)
            fprintf(stderr, "[KGSL-DIAG]   ib[%u] gpuaddr=0x%llx offset=%u size=%u flags=0x%x id=%u\\n",
                    d, (unsigned long long) cmds[d].gpuaddr, cmds[d].offset,
                    cmds[d].size, cmds[d].flags, cmds[d].id);
    }
'''.replace('@MARK@', MARKER)

AFTER = '''    /* @MARK@-after */
    if (diag)
        fprintf(stderr, "[KGSL-DIAG]   -> ret=%d errno=%d (%s) ts=%llu\\n",
                ret, ret ? errno : 0, ret ? strerror(errno) : "ok",
                ret ? 0ULL : (unsigned long long) req.timestamp);
'''.replace('@MARK@', MARKER)


def patch(root: Path):
    f = root / 'src/freedreno/drm/kgsl/kgsl_ringbuffer_sp.c'
    s = f.read_text()
    if MARKER in s:
        print('  = kgsl_ringbuffer_sp.c 已注入 diag，跳过')
    else:
        anchor = ('    int ret = kgsl_pipe_safe_ioctl(pipe->dev->fd, IOCTL_KGSL_GPU_COMMAND, &req);\n')
        if anchor not in s:
            raise SystemExit('找不到 GPU_COMMAND ioctl 调用')
        s = s.replace(anchor, INSERT + '\n' + anchor + '\n' + AFTER, 1)

        for inc in ('#include <stdio.h>',):
            if inc not in s:
                s = s.replace('#include "kgsl_priv.h"', '#include "kgsl_priv.h"\n' + inc, 1)
        f.write_text(s)
        print('  + kgsl_ringbuffer_sp.c: GPU_COMMAND 诊断')


if __name__ == '__main__':
    patch(Path(sys.argv[1] if len(sys.argv) > 1 else '.'))
