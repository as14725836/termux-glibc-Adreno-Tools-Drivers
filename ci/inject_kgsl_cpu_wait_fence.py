#!/usr/bin/env python3
"""修 kgsl 后端 submit 报 EDEADLK (Resource deadlock avoided) 的问题。

现象：MESA_LOADER_DRIVER_OVERRIDE=kgsl FD_FORCE_KGSL=1 跑 glxgears 时
持续刷

    MESA: error: flush_submit_list:89: submit failed -1 (Resource deadlock avoided)

原因：freedreno 的 flush_submit_list() 把 fd_submit->in_fence_fd 直接塞进
KGSL 的 cmd syncpoint：

    struct kgsl_cmd_syncpoint sync = {
        .type = KGSL_CMD_SYNCPOINT_TYPE_FENCE, ... .fd = in_fence_fd };

而 GL 这条路上 in_fence_fd 几乎总是交换链自己发出来的 fence，也就是同一个
kgsl context/queue 的 timestamp。内核等到"自己已完成才允许自己提交"，
直接返回 EDEADLK。

Turnip 那边不踩这个坑是因为它自己管理 syncobj，只回传已 signal 的 fd
(src/freedreno/vulkan/tu_knl_kgsl.cc:1774 一带)。

改法：kgsl 后端不使用内核 syncpoint 传递 in-fence，改为在提交前于 CPU 侧
poll 该 fence 直到 signal 再下发命令。语义等价（提交前依赖已满足），
但不会让内核看到自我等待。等待有超时（默认 2s，KGSL_FENCE_WAIT_MS 可调），
超时后照常提交，避免永久挂死。

只在 KGSL 后端生效，msm/其它后端完全不受影响。

用法: python3 ci/inject_kgsl_cpu_wait_fence.py <mesa源码目录>
幂等：以 'LORIE-KGSL-CPU-WAIT' 标记，重复执行不会重复插入。
"""
import sys
from pathlib import Path

MARKER = 'LORIE-KGSL-CPU-WAIT'

HELPER = '''
/* @MARK@: kgsl 的 cmd syncpoint 不能用来传 in-fence，见注入脚本注释。 */
static void
kgsl_cpu_wait_in_fence(int fd)
{
   if (fd < 0)
      return;

   const char *env = getenv("KGSL_FENCE_WAIT_MS");
   int timeout_ms = env ? atoi(env) : 2000;
   if (timeout_ms <= 0)
      timeout_ms = 2000;

   struct pollfd pfd = { .fd = fd, .events = POLLIN };
   int r;
   do {
      r = poll(&pfd, 1, timeout_ms);
   } while (r < 0 && errno == EINTR);

   if (r <= 0) {
      ERROR_MSG("in-fence wait %s (%s)", r == 0 ? "timed out" : "failed",
                r == 0 ? "submitting anyway" : strerror(errno));
      return;
   }

   /* 消费掉 SIGSYNCFILE 通知，让 fd 回到可复用状态。读失败无所谓，
    * poll 已经确认 signal。 */
   uint64_t buf;
   ssize_t nread = read(fd, &buf, sizeof(buf));
   (void) nread;
}
'''.replace('@MARK@', MARKER)

def patch(root: Path):
    f = root / 'src/freedreno/drm/kgsl/kgsl_ringbuffer_sp.c'
    s = f.read_text()

    if MARKER in s:
        print('  = kgsl_ringbuffer_sp.c 已注入，跳过')
        return

    # 1) 插入 helper（放在 flush_submit_list 之前）
    anchor = 'static int\nflush_submit_list(struct list_head *submit_list)'
    if anchor not in s:
        raise SystemExit('找不到 flush_submit_list 定义')
    s = s.replace(anchor, HELPER.strip() + '\n\n' + anchor, 1)

    # 2) 提交前 CPU 等待 in-fence
    submit_anchor = '''    struct kgsl_gpu_command req = {
        .flags = KGSL_CMDBATCH_SUBMIT_IB_LIST,'''
    if submit_anchor not in s:
        raise SystemExit('找不到 kgsl_gpu_command 构造处')
    s = s.replace(submit_anchor,
'''    /* %s: 同 context 的 in-fence 会让内核判定自我等待 -> EDEADLK，
     * 所以先在 CPU 侧等它 signal，且不再把它当内核 syncpoint 传下去。 */
    kgsl_cpu_wait_in_fence(fd_submit->in_fence_fd);
    sync_fence.fd = -1;

    struct kgsl_gpu_command req = {
        .flags = KGSL_CMDBATCH_SUBMIT_IB_LIST,''' % MARKER, 1)

    # 3) 需要 poll / read(2) / getenv / atoi 的头
    for inc in ('#include <poll.h>', '#include <stdlib.h>', '#include <unistd.h>'):
        if inc not in s:
            s = s.replace('#include "kgsl_priv.h"', '#include "kgsl_priv.h"\n' + inc, 1)

    f.write_text(s)
    print('  + kgsl_ringbuffer_sp.c 已注入 CPU 等待 in-fence')

if __name__ == '__main__':
    patch(Path(sys.argv[1] if len(sys.argv) > 1 else '.'))
