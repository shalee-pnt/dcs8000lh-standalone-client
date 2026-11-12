import sys
from elftools.elf.elffile import ELFFile
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

path = sys.argv[1]
addr = int(sys.argv[2], 16)

with open(path, 'rb') as f:
    elf = ELFFile(f)
    text = elf.get_section_by_name('.text')
    base_vaddr = text['sh_addr']
    base_offset = text['sh_offset']
    size = text['sh_size']
    f.seek(base_offset)
    code = f.read(size)

md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
all_insns = list(md.disasm(code, base_vaddr))
addr_to_idx = {insn.address: i for i, insn in enumerate(all_insns)}

idx = None
for i, insn in enumerate(all_insns):
    if insn.address <= addr < insn.address + insn.size:
        idx = i
        break
    if insn.address > addr:
        idx = i - 1
        break

# scan backward for a 'stp x29, x30, [sp, #-N]!' prologue
for j in range(idx, max(0, idx - 2000), -1):
    insn = all_insns[j]
    if insn.mnemonic == 'stp' and 'x29, x30, [sp' in insn.op_str:
        print(f"Function start: 0x{insn.address:x}")
        # print from there to ~400 bytes after target addr
        end_addr = addr + 300
        for k in range(j, len(all_insns)):
            if all_insns[k].address > end_addr:
                break
            print(f"0x{all_insns[k].address:x}:\t{all_insns[k].mnemonic}\t{all_insns[k].op_str}")
        break
else:
    print("No prologue found in range")
