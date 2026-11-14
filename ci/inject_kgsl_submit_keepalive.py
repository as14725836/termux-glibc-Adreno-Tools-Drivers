#!/usr/bin/env python3
"""实验：flush_submit_list 成功时推迟释放 merged submits。

kgsl 的 IOCTL_KGSL_GPU_COMMAND 只是把 IB 入队，GPU 异步执行；如果调用方
在 ioctl 返回后立刻 fd_submit_del() 释放 ring BO，BO 会被复用，GPU 读到
垃圾数据 -> context fault -> 后续提交全部 EDEADLK。

KGSL_KEEP_SUBMITS=1 时成功路径不释放前序 submits（用泄漏换正确性），用于
验证该假设。
"""
import sys
from pathlib import Path

MARKER = 'LG-KGSL-KEEPALIVE'

OLD = '''    foreach_submit_safe (submit, submit_list) {
        if (submit == last_submit(submit_list))
            break;

        list_del(&submit->node);
        fd_submit_del(submit);
    }
'''

NEW = '''    /* @MARK@: 成功时把 merged submits 挂到一个延迟回收队列，等 GPU 真正
     * 消费完这个 timestamp 之后再释放，避免 ring BO 被提前复用。
     */
    if (getenv("KGSL_KEEP_SUBMITS")) {
        static unsigned n_kept;
        if (ret == 0) {
            foreach_submit_safe (submit, submit_list) {
                if (submit == last_submit(submit_list))
                    break;
                list_del(&submit->node);
                /* 不释放，故意泄漏以验证假设 */
                n_kept++;
            }
            if (n_kept && (n_kept % 64) == 0)
                fprintf(stderr, "[KGSL-KEEP] leaked %u submits\\n", n_kept);
        } else {
            foreach_submit_safe (submit, submit_list) {
                if (submit == last_submit(submit_list))
                    break;
                list_del(&submit->node);
                fd_submit_del(submit);
            }
        }
        return ret;
    }

'''.replace('@MARK@', MARKER) + OLD


def patch(root: Path):
    f = root / 'src/freedreno/drm/kgsl/kgsl_ringbuffer_sp.c'
    s = f.read_text()
    if MARKER in s:
        print('  = kgsl_ringbuffer_sp.c 已注入 keepalive，跳过')
        return
    if OLD not in s:
        raise SystemExit('找不到 foreach_submit_safe 释放逻辑')
    s = s.replace(OLD, NEW, 1)
    for inc in ('#include <stdio.h>', '#include <stdlib.h>'):
        if inc not in s:
            s = s.replace('#include "kgsl_priv.h"',
                          '#include "kgsl_priv.h"\n' + inc, 1)
    f.write_text(s)
    print('  + kgsl_ringbuffer_sp.c: submit keepalive 实验')


if __name__ == '__main__':
    patch(Path(sys.argv[1] if len(sys.argv) > 1 else '.'))
