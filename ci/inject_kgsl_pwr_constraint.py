#!/usr/bin/env python3
"""给 freedreno 的 kgsl 后端补上 PWR_CONSTRAINT 设置。

现象：MESA_LOADER_DRIVER_OVERRIDE=kgsl FD_FORCE_KGSL=1 时，凡是真正走渲染
的提交（glxgears / glmark2）都持续刷

    MESA: error: flush_submit_list:128: submit failed -1 (Resource deadlock avoided)

而纯查询（glxinfo -B）正常。

对比同机可用、同源的 Turnip（src/freedreno/vulkan/tu_knl_kgsl.cc），它在三
个地方做了 PWR 约束，freedreno 的 kgsl 后端一处都没做：

 1. DRAWCTXT_CREATE 的 flags 含 KGSL_CONTEXT_PWR_CONSTRAINT
      turnip :77-79   freedreno: 只有 SAVE_GMEM|NO_GMEM_ALLOC|PREAMBLE
 2. drawctxt 创建后 ioctl(SETPROPERTY, KGSL_PROP_PWR_CONSTRAINT) 设
    PWR_MAX 上限
      turnip :88-89   freedreno: 无
 3. GPU_COMMAND 的 flags 含 KGSL_CMDBATCH_PWR_CONSTRAINT
      turnip :1797    freedreno: 只有 KGSL_CMDBATCH_SUBMIT_IB_LIST

缺少功率约束时，这台机器（Adreno740v2 / kernel 5.15.167）的 kgsl 会在真正
调度渲染工作时让提交失败，errno 报 EDEADLK —— 这正是 Turnip 把 EDEADLK 当
"context faulted / detached" 处理的原因（tu_knl_kgsl.cc:983 及其注释）。

本脚本把上面三处对齐 Turnip。只动 kgsl 后端，msm 路径不受影响。

用法: python3 ci/inject_kgsl_pwr_constraint.py <mesa源码目录>
幂等：以 'LORIE-KGSL-PWR' 标记。
"""
import sys
from pathlib import Path

MARKER = 'LORIE-KGSL-PWR'

HELPER = '''/* @MARK@: 与 turnip tu_knl_kgsl.cc:53 的 dd_set_pwr_max_constraint 对齐。
 * 不给 kgsl context 设 PWR_MAX 上限时，本机内核会让真正的渲染提交返回
 * EDEADLK（turnip 把这个错误码当 context fault 处理）。 */
int
kgsl_pipe_set_pwr_max(int fd, uint32_t context_id)
{
   struct kgsl_device_constraint_pwrlevel pwrlevel = {
      .level = KGSL_CONSTRAINT_PWR_MAX,
   };
   struct kgsl_device_constraint constraint = {
      .type = KGSL_CONSTRAINT_PWRLEVEL,
      .context_id = context_id,
      .data = (void *) &pwrlevel,
      .size = sizeof(pwrlevel),
   };
   struct kgsl_device_getproperty prop = {
      .type = KGSL_PROP_PWR_CONSTRAINT,
      .value = (void *) &constraint,
      .sizebytes = sizeof(constraint),
   };

   return kgsl_pipe_safe_ioctl(fd, IOCTL_KGSL_SETPROPERTY, &prop);
}
'''.replace('@MARK@', MARKER)


def patch(root: Path):
    # ---- kgsl_pipe.c: drawctxt flags + 创建后设 PWR_MAX ----
    f = root / 'src/freedreno/drm/kgsl/kgsl_pipe.c'
    s = f.read_text()

    if MARKER in s:
        print('  = kgsl_pipe.c 已注入，跳过')
    else:
        old_flags = '''        .flags = KGSL_CONTEXT_SAVE_GMEM |
                 KGSL_CONTEXT_NO_GMEM_ALLOC |
                 KGSL_CONTEXT_PREAMBLE,'''
        if old_flags not in s:
            raise SystemExit('kgsl_pipe.c 找不到 DRAWCTXT_CREATE flags')
        s = s.replace(old_flags, '''        .flags = KGSL_CONTEXT_SAVE_GMEM |
                 KGSL_CONTEXT_NO_GMEM_ALLOC |
                 KGSL_CONTEXT_PREAMBLE |
                 KGSL_CONTEXT_PWR_CONSTRAINT, /* @MARK@ */'''.replace('@MARK@', MARKER), 1)

        old_after = '''    kgsl_pipe->queue_id = req.drawctxt_id;

    fd_pipe_sp_ringpool_init(pipe);'''
        if old_after not in s:
            raise SystemExit('kgsl_pipe.c 找不到 queue_id 赋值处')
        s = s.replace(old_after, '''    kgsl_pipe->queue_id = req.drawctxt_id;

    /* @MARK@: turnip 在 drawctxt 建好后立刻设 PWR_MAX，缺了它渲染提交会
     * 报 EDEADLK。失败不致命（可能有设备不支持），只提示。 */
    if (kgsl_pipe_set_pwr_max(dev->fd, req.drawctxt_id))
        ERROR_MSG("PWR_MAX constraint failed (%s)", strerror(errno));

    fd_pipe_sp_ringpool_init(pipe);'''.replace('@MARK@', MARKER), 1)

        # 插入 helper：放在 kgsl_pipe_new 之前
        anchor = 'struct fd_pipe *\nkgsl_pipe_new('
        if anchor not in s:
            anchor = 'kgsl_pipe_new(struct fd_device *dev'
            idx = s.index(anchor)
            # 回退到函数头
            start = s.rindex('\n', 0, s.rindex('\n', 0, idx)) + 1
            s = s[:start] + HELPER + '\n' + s[start:]
        else:
            s = s.replace(anchor, HELPER + '\n' + anchor, 1)

        f.write_text(s)
        print('  + kgsl_pipe.c: DRAWCTXT flags + PWR_MAX 设置')

    # ---- kgsl_ringbuffer_sp.c: 提交 flags ----
    f2 = root / 'src/freedreno/drm/kgsl/kgsl_ringbuffer_sp.c'
    s2 = f2.read_text()
    if MARKER + '-SUBMIT' in s2:
        print('  = kgsl_ringbuffer_sp.c 已注入，跳过')
    else:
        old = '        .flags = KGSL_CMDBATCH_SUBMIT_IB_LIST,'
        if old not in s2:
            raise SystemExit('kgsl_ringbuffer_sp.c 找不到提交 flags')
        s2 = s2.replace(old,
                        '        .flags = KGSL_CMDBATCH_SUBMIT_IB_LIST |\n'
                        '                 KGSL_CMDBATCH_PWR_CONSTRAINT, /* %s-SUBMIT */' % MARKER, 1)
        f2.write_text(s2)
        print('  + kgsl_ringbuffer_sp.c: 提交加 PWR_CONSTRAINT')


if __name__ == '__main__':
    patch(Path(sys.argv[1] if len(sys.argv) > 1 else '.'))
