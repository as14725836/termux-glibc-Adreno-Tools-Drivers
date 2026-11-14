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
    if (diag < 0) {
        const char *d = getenv("LG_KGSL_DIAG");
        diag = d ? atoi(d) : 0;
    }
    if (diag) {
        static unsigned kgsl_submit_seq;
        kgsl_submit_seq++;
        fprintf(stderr, "[KGSL-SEQ] %u", kgsl_submit_seq);
        fprintf(stderr, "[KGSL-DIAG] ctx=%u numcmds=%u numsyncs=%u infence=%d\\n",
                kgsl_pipe->queue_id, cmd_idx, req.numsyncs, fd_submit->in_fence_fd);
        for (unsigned d = 0; d < cmd_idx; d++) {
            fprintf(stderr, "[KGSL-DIAG]   ib[%u] gpuaddr=0x%llx offset=%llu size=%llu flags=0x%x id=%u\\n",
                    d, (unsigned long long) cmds[d].gpuaddr,
                    (unsigned long long) cmds[d].offset,
                    (unsigned long long) cmds[d].size,
                    cmds[d].flags, cmds[d].id);
            /* LG_KGSL_DIAG=2 时把 IB 的 dword 流打出来，用于人工解 pm4 包 */
            if (diag >= 2 && (unsigned) atoi(getenv("IB_KGSL_DUMP_SKIP") ? getenv("IB_KGSL_DUMP_SKIP") : "0") <= kgsl_submit_seq) {
                struct fd_ringbuffer_sp *dp = to_fd_ringbuffer_sp(fd_submit->base.primary);
                struct fd_bo *rbo = dp->u.cmds[d].ring_bo;
                const uint32_t *w = fd_bo_map(rbo);
                if (w) {
                    unsigned words = dp->u.cmds[d].size / 4;
                    for (unsigned k = 0; k < words && k < 320; k++)
                        fprintf(stderr, "[KGSL-IB] %03u: %08x\\n", k, w[k]);
                }
            }
        }
    }
    /* @MARK@-trunc: IB_KGSL_TRUNC=N 只提交前 N 个 dword，用于二分定位
     * 让 GPU 挂死的那条 pm4 命令。0/不设 = 不截断。 */
    {
        const char *t_ = getenv("IB_KGSL_TRUNC");
        unsigned tr_ = t_ ? (unsigned) atoi(t_) : 0;
        if (getenv("IB_KGSL_ZERO")) {
            for (unsigned d_ = 0; d_ < cmd_idx; d_++)
                cmds[d_].size = 0;
            fprintf(stderr, "[KGSL-ZERO] all IB size=0 !");
        } else if (tr_ > 0) {
            for (unsigned d_ = 0; d_ < cmd_idx; d_++) {
                unsigned bytes_ = tr_ * 4;
                if (cmds[d_].size > bytes_)
                    cmds[d_].size = bytes_;
            }
            fprintf(stderr, "[KGSL-TRUNC] trunc to %u dwords\\n", tr_);
        }
    }
'''.replace('@MARK@', MARKER)

AFTER = '''    /* @MARK@-after */
    if (diag)
        fprintf(stderr, "[KGSL-DIAG]   -> ret=%d errno=%d (%s) ts=%llu\\n",
                ret, ret ? errno : 0, ret ? strerror(errno) : "ok",
                ret ? 0ULL : (unsigned long long) req.timestamp);
    /* LG-KGSL-DIAG-serial: IB_KGSL_SERIAL=1 每笔提交后立刻等 GPU 完成 */
    /* LG-KGSL-DIAG-nofence: IB_KGSL_NOFENCE=1 强制走 kfence 而不是 dma-fence fd，
     * 用来验证 IOCTL_KGSL_TIMESTAMP_EVENT 是否是失败点。 */
    if (getenv("IB_KGSL_NOFENCE"))
        fd_submit->out_fence->use_fence_fd = false;
    if (getenv("IB_KGSL_SERIAL") && ret == 0) {
        struct kgsl_device_waittimestamp_ctxtid w_ = {
            .context_id = kgsl_pipe->queue_id,
            .timestamp = req.timestamp,
            .timeout = 2000,
        };
        int wr_ = kgsl_pipe_safe_ioctl(pipe->dev->fd,
                                       IOCTL_KGSL_DEVICE_WAITTIMESTAMP_CTXTID, &w_);
        fprintf(stderr, "[KGSL-WAIT] ts=%llu ret=%d errno=%d",
                (unsigned long long) req.timestamp, wr_, wr_ ? errno : 0);
    }
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
