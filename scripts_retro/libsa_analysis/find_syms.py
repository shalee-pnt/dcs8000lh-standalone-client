import sys
from elftools.elf.elffile import ELFFile

path = sys.argv[1]
targets = set(sys.argv[2:])

with open(path, 'rb') as f:
    elf = ELFFile(f)
    dynsym = elf.get_section_by_name('.dynsym')
    symtab = elf.get_section_by_name('.symtab') or dynsym
    found = {}
    for sec in [dynsym, symtab]:
        if sec is None:
            continue
        for sym in sec.iter_symbols():
            if sym.name in targets:
                found[sym.name] = (sym['st_value'], sym['st_size'])
    for name in targets:
        if name in found:
            addr, size = found[name]
            print(f"{name}: addr=0x{addr:x} size={size}")
        else:
            print(f"{name}: NOT FOUND")
