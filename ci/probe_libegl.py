#!/usr/bin/env python3
"""验证产物里的 libEGL.so.1 能满足 Wine 的 EGL 加载要求。

Wine 的 win32u/opengl.c:egl_init() 是这么拿 EGL 的：
    funcs->egl_handle = dlopen( SONAME_LIBEGL /* libEGL.so.1 */,
                                RTLD_NOW | RTLD_GLOBAL );
    dlsym( handle, "eglGetProcAddress" );
    dlsym( handle, "eglQueryString" );
    /* 其余 ALL_EGL_FUNCS 全部走 eglGetProcAddress() 取 */

并且要求客户端扩展串同时含：
    EGL_KHR_client_get_all_proc_addresses
    EGL_EXT_platform_base

本脚本用 ctypes 复刻这条链路做离线校验，不需要 X11 或 GPU。

用法: probe_libegl.py <libEGL.so.1>
退出码 0 = 可用，1 = 不可用。"""
import ctypes
import sys

EGL_NO_DISPLAY = 0
EGL_EXTENSIONS = 0x3055
REQUIRED_EXT = ("EGL_KHR_client_get_all_proc_addresses", "EGL_EXT_platform_base")
# egl_init() 里通过 eglGetProcAddress 取的部分关键入口
REQUIRED_PROCS = ("eglGetPlatformDisplay", "eglCreateContext", "eglMakeCurrent",
                  "eglSwapBuffers", "eglInitialize", "eglGetDisplay")


def main(path):
    try:
        lib = ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
    except OSError as e:
        print(f"  NG  dlopen 失败: {e}")
        return 1
    print(f"  OK  dlopen: {path}")

    for sym in ("eglGetProcAddress", "eglQueryString"):
        if not hasattr(lib, sym):
            print(f"  NG  缺少 dlsym 目标: {sym}")
            return 1
    print("  OK  dlsym: eglGetProcAddress, eglQueryString")

    lib.eglQueryString.restype = ctypes.c_char_p
    lib.eglQueryString.argtypes = [ctypes.c_void_p, ctypes.c_int]
    ext = lib.eglQueryString(EGL_NO_DISPLAY, EGL_EXTENSIONS)
    if not ext:
        print("  NG  eglQueryString(EGL_NO_DISPLAY, EGL_EXTENSIONS) 返回 NULL")
        return 1
    ext = ext.decode("ascii", "replace")
    print(f"  OK  客户端扩展串 {len(ext)} 字节")

    bad = 0
    for want in REQUIRED_EXT:
        if want in ext:
            print(f"  OK  必需扩展: {want}")
        else:
            print(f"  NG  缺少必需扩展: {want}")
            bad += 1

    lib.eglGetProcAddress.restype = ctypes.c_void_p
    lib.eglGetProcAddress.argtypes = [ctypes.c_char_p]
    for name in REQUIRED_PROCS:
        if lib.eglGetProcAddress(name.encode()):
            print(f"  OK  eglGetProcAddress({name})")
        else:
            print(f"  NG  eglGetProcAddress({name}) 为空")
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("用法: probe_libegl.py <libEGL.so.1>")
    sys.exit(main(sys.argv[1]))
