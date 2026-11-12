import sys
from elftools.elf.elffile import ELFFile
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

path = sys.argv[1]
addr = int(sys.argv[2], 16)
size = int(sys.argv[3])

with open(path, 'rb') as f:
    elf = ELFFile(f)
    # find the segment/section containing this virtual address to compute file offset
    text = elf.get_section_by_name('.text')
    base_vaddr = text['sh_addr']
    base_offset = text['sh_offset']
    file_off = base_offset + (addr - base_vaddr)
    f.seek(file_off)
    code = f.read(size)

md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
md.detail = True
for insn in md.disasm(code, addr):
    print(f"0x{insn.address:x}:\t{insn.mnemonic}\t{insn.op_str}")
