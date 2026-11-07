#!/usr/bin/env python3
"""检查 Mesa 产物里 gallium 驱动注册表是否完整。

判定方法（唯一可靠）：定位 <driver>_driver_descriptor 符号，读出它的
.driver_name 指针，再解析出实际字符串。

为什么不能只看文件里有没有 "zink" 字样：
  - zink 驱动代码里到处是 *_zink 结尾的内部字符串；
  - 链接器会做字符串尾部合并，"zink" 可能被并入 "mesa zink" 的尾部，
    此时独立的 \\0zink\\0 不存在，但运行期 strcmp 是正常工作的。

用法:
  check_driver_registry.py <ELF> <名字> [名字...]   # 校验，退出码 0/1
  check_driver_registry.py --dump <ELF>            # 列出已知驱动状态
strip 过的文件没有 .symtab 时，退化为“完整独立字符串”检测。
"""
import re
import struct
import sys

KNOWN = ['zink', 'swrast', 'kgsl', 'asahi', 'msm', 'i915', 'iris',
         'freedreno', 'llvmpipe', 'kmsro', 'lima', 'tegra', 'virpipe']


def parse_elf(path):
    data = open(path, 'rb').read()
    if data[:4] != b'\x7fELF' or data[4] != 2:
        sys.exit(f'{path}: 不是 ELF64')
    le = '<' if data[5] == 1 else '>'
    shoff, = struct.unpack_from(le + 'Q', data, 0x28)
    shentsize, shnum = struct.unpack_from(le + 'HH', data, 0x3A)
    secs = []
    for i in range(shnum):
        o = shoff + i * shentsize
        name, typ = struct.unpack_from(le + 'II', data, o)
        addr, off, size = struct.unpack_from(le + 'QQQ', data, o + 16)
        link, = struct.unpack_from(le + 'I', data, o + 40)
        entsize, = struct.unpack_from(le + 'Q', data, o + 56)
        secs.append(dict(name=name, type=typ, addr=addr, off=off,
                         size=size, link=link, entsize=entsize))
    return data, le, secs


def symbols(data, le, secs):
    """返回 {符号名: 虚拟地址}，兼容 strip 后无 .symtab 的情况。"""
    table = {}
    for s in secs:
        if s['type'] not in (2, 11) or not s['entsize']:
            continue
        strtab = secs[s['link']]
        stroff = strtab['off']
        for k in range(s['size'] // s['entsize']):
            o = s['off'] + k * s['entsize']
            st_name, = struct.unpack_from(le + 'I', data, o)
            st_value, = struct.unpack_from(le + 'Q', data, o + 8)
            if not st_value:
                continue
            end = data.find(b'\0', stroff + st_name)
            table.setdefault(data[stroff + st_name:end].decode('latin1'),
                             st_value)
    return table


def va2off(secs, va):
    for s in secs:
        if s['type'] != 8 and s['addr'] and s['addr'] <= va < s['addr'] + s['size']:
            return s['off'] + (va - s['addr'])
    return None


def cstr(data, off):
    if off is None or off >= len(data):
        return None
    end = data.find(b'\0', off)
    return data[off:end].decode('latin1')


def has_standalone(data, name):
    """是否存在完整的独立 C 字符串 name（长度严格等于 len(name)）。"""
    nb = name.encode()
    for m in re.finditer(re.escape(nb) + rb'\x00', data):
        begin = m.start()
        prev = data[begin - 1] if begin else 0
        if not (0x20 <= prev < 0x7f):
            return True
    return False


def report(path, names):
    data, le, secs = parse_elf(path)
    syms = symbols(data, le, secs)
    print(f'  文件: {path} ({len(data)} B, 符号 {len(syms)} 个)')
    bad = 0
    for name in names:
        sym = f'{name}_driver_descriptor'
        va = syms.get(sym)
        if va is not None:
            off = va2off(secs, va)
            ptr, = struct.unpack_from(le + 'Q', data, off)
            s = cstr(data, va2off(secs, ptr))
            if s == name:
                print(f'  OK  {name}: {sym} @0x{va:x} -> driver_name="{s}"')
            else:
                print(f'  NG  {name}: {sym} @0x{va:x} -> driver_name={s!r}')
                bad += 1
        elif has_standalone(data, name):
            print(f'  OK  {name}: 无符号表，但存在独立字符串 "{name}"')
        else:
            print(f'  NG  {name}: 无 {sym} 符号，也没有独立字符串')
            bad += 1
    return 1 if bad else 0


def dump(path):
    data, le, secs = parse_elf(path)
    syms = symbols(data, le, secs)
    print(f'  文件: {path} ({len(data)} B, 符号 {len(syms)} 个)')
    found = 0
    for name in KNOWN:
        sym = f'{name}_driver_descriptor'
        va = syms.get(sym)
        if va is None:
            continue
        found += 1
        off = va2off(secs, va)
        ptr, = struct.unpack_from(le + 'Q', data, off)
        s = cstr(data, va2off(secs, ptr))
        print(f'    [{"OK " if s == name else "BAD"}] {sym:30s} '
              f'@0x{va:x}  driver_name={s!r}')
    if not found:
        print('  无 *_driver_descriptor 符号（可能已 strip）')
    return 0


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    if sys.argv[1] == '--dump':
        return dump(sys.argv[2])
    return report(sys.argv[1], sys.argv[2:])


if __name__ == '__main__':
    sys.exit(main())
