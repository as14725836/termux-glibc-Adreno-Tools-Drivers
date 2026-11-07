#!/usr/bin/env python3
"""检查 Mesa 产物里的 gallium 驱动注册表是否完整。

DRM_DRIVER_DESCRIPTOR(driver, ...) 展开出 .driver_name = #driver，
在 .rodata 里是独立的 NUL 结尾 C 字符串（\\0driver\\0）。
用字节级匹配，避免 strings 最小长度 / 相邻字符串粘连导致的误判。

用法:
  check_driver_registry.py <文件> <名字> [名字...]      # 校验
  check_driver_registry.py --dump <文件>               # 列出所有候选
退出码 0 = 全部存在, 1 = 有缺失
"""
import sys

CANDS = ['zink', 'swrast', 'kgsl', 'freedreno', 'asahi', 'llvmpipe',
         'kms_swrast', 'virpipe', 'msm', 'd3d12', 'softpipe']


def iso(data, name):
    return data.count(b'\x00' + name.encode() + b'\x00')


def raw(data, name):
    return data.count(name.encode())


def dump(path):
    data = open(path, 'rb').read()
    print(f'  文件: {path} ({len(data)} B)')
    for n in CANDS:
        print(f'    {n:12s} 独立 {iso(data, n):3d}  裸 {raw(data, n):3d}')


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    if sys.argv[1] == '--dump':
        dump(sys.argv[2])
        return 0
    path, names = sys.argv[1], sys.argv[2:]
    data = open(path, 'rb').read()
    missing = 0
    for name in names:
        n_iso, n_raw = iso(data, name), raw(data, name)
        if n_iso:
            print(f'  OK  {name}: 独立字符串 {n_iso} 处 (裸字节 {n_raw})')
        else:
            print(f'  NG  {name}: 无独立字符串 (裸字节 {n_raw} 处)')
            missing += 1
    return 1 if missing else 0


if __name__ == '__main__':
    sys.exit(main())
