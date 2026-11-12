import sys
from elftools.elf.elffile import ELFFile

path = sys.argv[1]
addr = int(sys.argv[2], 16)

with open(path, 'rb') as f:
    elf = ELFFile(f)
    best = None
    for secname in ['.symtab', '.dynsym']:
        sec = elf.get_section_by_name(secname)
        if sec is None:
            continue
        for sym in sec.iter_symbols():
            v = sym['st_value']
            sz = sym['st_size']
            if v <= addr < v + max(sz, 1) and sym.name:
                if best is None or sz < best[2]:
                    best = (sym.name, v, sz)
    if best:
        print(f"Containing symbol: {best[0]} @ 0x{best[1]:x} size={best[2]}")
    else:
        print("No containing symbol found (static/local, unnamed)")
