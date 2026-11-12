import sys
from elftools.elf.elffile import ELFFile
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

path = sys.argv[1]
target_addr = int(sys.argv[2], 16)

with open(path, 'rb') as f:
    elf = ELFFile(f)
    text = elf.get_section_by_name('.text')
    base_vaddr = text['sh_addr']
    base_offset = text['sh_offset']
    size = text['sh_size']
    f.seek(base_offset)
    code = f.read(size)

md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
callers = []
for insn in md.disasm(code, base_vaddr):
    if insn.mnemonic == 'bl':
        try:
            dest = int(insn.op_str.replace('#', ''), 16)
        except ValueError:
            continue
        if dest == target_addr:
            callers.append(insn.address)

print(f"Found {len(callers)} call sites to 0x{target_addr:x}:")
for c in callers:
    print(f"  0x{c:x}")
