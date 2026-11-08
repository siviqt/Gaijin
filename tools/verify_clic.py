#!/usr/bin/env python3
"""Execute the entry/return shims with modeled P4 CSRs and active IRQ level.

QEMU user emulation has no P4 CLIC. CSR access and mret below implement the
observed P4 contract: only an interrupt mret pops MIL, and its type must match
the active hardware trap. All other shim instructions run as actual RV32 code.
This checks the Linux scheduling boundary, not just the saved return frame.
"""
import argparse
from pathlib import Path
import re
import os
import subprocess


root = Path(__file__).resolve().parents[1]
variant = root / "build/clic-check"
variant.mkdir(parents=True, exist_ok=True)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, default=root / "build/linux-6.18.35")
parser.add_argument("--negative-control", action="store_true", help="verify that leaving the hardware level raised blocks the timer")
parser.add_argument("--cross-compile", default=os.environ.get("CROSS_COMPILE", "riscv32-esp-elf-"))
args = parser.parse_args()
source = (args.source / "arch/riscv/kernel/entry.S").read_text()
offsets = (root / "build/kernel/include/generated/asm-offsets.h").read_text()

entry = "\tREG_S s4, PT_CLIC_CAUSE(sp)\n" + source.split(
    "\tREG_S s4, PT_CLIC_CAUSE(sp)\n", 1)[1].split("#endif", 1)[0]
if ".Lclic_irq_ready:" in source:
    pop = source.split("\tcsrw CSR_SCRATCH, x0\n", 1)[1].split(
        "\t/* Load the global pointer */", 1)[0]
    pop = pop.replace("#ifdef CONFIG_SOC_ESP32P4\n", "").replace("#endif\n", "")
else:
    pop = ""  # Negative control: the failed candidate never lowers MIL here.
if args.negative_control:
    pop = ""
restore = "\tREG_L a3, PT_CLIC_CAUSE(sp)\n" + source.split(
    "\tREG_L a3, PT_CLIC_CAUSE(sp)\n", 1)[1].split("\tcsrw CSR_EPC, a2", 1)[0]
restore += "\tcsrw CSR_EPC, a2\n"
restore = restore.replace("#endif\n", "")


def instrument(code, suffix):
    code = code.replace(".Lclic_irq_ready", f".Lclic_irq_ready_{suffix}")
    code = code.replace(".Lclic_kernel_return", f".Lclic_kernel_return_{suffix}")
    for csr, macro in (("CAUSE", "cause"), ("STATUS", "status"), ("EPC", "epc")):
        code = re.sub(rf"\bcsrw\s+CSR_{csr},\s*(\w+)", rf"model_{macro} \1", code)
    code = re.sub(r"\bmret\b", f"""lw t2, LIVE_EPC(s6)
    la t3, .Lclic_irq_ready_{suffix}
    bne t2, t3, .Lfail
    model_mret""", code)
    assert "csrw" not in code
    return code


assembly = [r"""
#define REG_S sw
#define REG_L lw
#define SR_MPP 0x1800
#define SR_MPIE 0x80
#define LIVE_CAUSE 0
#define LIVE_STATUS 4
#define LIVE_EPC 8
#define LIVE_MIL 12
#define TRAP_KIND 16
#define LIVE_PRIV 20
#define IRQ_POPS 24

/* CSR aliasing is bidirectional on P4. Scratch t2-t4 are not shim operands. */
.macro model_cause value
    sw \value, LIVE_CAUSE(s6)
    lw t2, LIVE_STATUS(s6)
    li t3, ~0x1880
    and t2, t2, t3
    srli t3, \value, 17
    li t4, 0x1800
    and t3, t3, t4
    or t2, t2, t3
    srli t3, \value, 20
    andi t3, t3, 0x80
    or t2, t2, t3
    sw t2, LIVE_STATUS(s6)
.endm

.macro model_status value
    sw \value, LIVE_STATUS(s6)
    lw t2, LIVE_CAUSE(s6)
    li t3, ~0x38000000
    and t2, t2, t3
    li t3, 0x1800
    and t3, \value, t3
    slli t3, t3, 17
    or t2, t2, t3
    andi t3, \value, 0x80
    slli t3, t3, 20
    or t2, t2, t3
    sw t2, LIVE_CAUSE(s6)
.endm

.macro model_epc value
    sw \value, LIVE_EPC(s6)
.endm

.macro model_mret
    lw t2, LIVE_CAUSE(s6)
    lw t3, TRAP_KIND(s6)
    li t4, 2
    bltz t2, .Lirq_return\@
    /* An exception/synthetic return cannot complete a hardware IRQ. */
    beq t3, t4, .Lfail
    j .Lupdate_status\@
.Lirq_return\@:
    bne t3, t4, .Lfail
    srli t3, t2, 16
    andi t3, t3, 255
    sw t3, LIVE_MIL(s6)
    lw t3, IRQ_POPS(s6)
    addi t3, t3, 1
    sw t3, IRQ_POPS(s6)
.Lupdate_status\@:
    srli t3, t2, 28
    andi t3, t3, 3
    sw t3, LIVE_PRIV(s6)
    srli t3, t2, 24
    andi t3, t3, 8
    lw t4, LIVE_STATUS(s6)
    li t2, ~0x1888
    and t4, t4, t2
    ori t4, t4, 0x80
    or t4, t4, t3
    model_status t4
    sw zero, TRAP_KIND(s6)
.endm

/* Saving a frame must not enable IRQs or leave M-mode before Linux C. */
.macro check_c_boundary
    lw t2, LIVE_STATUS(s6)
    andi t2, t2, 8
    bnez t2, .Lfail
    lw t2, LIVE_PRIV(s6)
    li t3, 3
    bne t2, t3, .Lfail
.endm

/* The idle task enables IRQs by setting MIE alone. A level-255 tick must win. */
.macro check_idle_delivery
    lw t2, LIVE_STATUS(s6)
    ori t2, t2, 8
    sw t2, LIVE_STATUS(s6)
    lw t2, LIVE_MIL(s6)
    li t3, 255
    bgeu t2, t3, .Lfail
    lw t2, LIVE_STATUS(s6)
    andi t2, t2, 8
    beqz t2, .Lfail
.endm

.option norelax
.text
.global _start
_start:
    addi sp, sp, -512
    la s6, csr_state
"""
]
for name in ("PT_CLIC_CAUSE", "PT_CAUSE", "PT_STATUS", "PT_EPC"):
    match = re.search(rf"^#define {name} (\d+)", offsets, re.M)
    assert match, name
    assembly.insert(0, f"#define {name} {match[1]}\n")

origins = (0x88000011, 0xb8000011, 0xb8000012, 0x08000008, 0x38000002)
destinations = (
    (0x08000008, 0x80),       # Sleeping syscall task, returning to U-mode.
    (0, 0x80),                # Fresh exec frame.
    (0, 0x1880),              # Fresh kernel thread.
    (0xb8ff0011, 0x1880),    # A different interrupted kernel task.
    (0x88000012, 0x80),      # A different interrupted user task.
    (0x30000002, 0x1800),    # Kernel return with IRQs intentionally disabled.
)
cases = [(origin, dest, sentinel) for origin in origins
         for dest in destinations for sentinel in (8, 0xffffffff)]

for index, (raw, (return_raw, return_status), sentinel) in enumerate(cases, 1):
    irq = bool(raw & (1 << 31))
    saved_status = 0x10000 | ((raw >> 17) & 0x1800) | ((raw >> 20) & 0x80)
    assembly.append(f"""
    li s11, {index}
    sw zero, IRQ_POPS(s6)
    li t0, {255 if irq else 0}
    sw t0, LIVE_MIL(s6)
    li t0, {2 if irq else 1}
    sw t0, TRAP_KIND(s6)
    li t0, 3
    sw t0, LIVE_PRIV(s6)
    li t0, 0x10000
    sw t0, LIVE_STATUS(s6)
    li s4, {raw:#x}
    model_cause s4
    lw s1, LIVE_STATUS(s6)
""")
    assembly.append(entry)
    assembly.append(instrument(pop, f"outer_{index}"))
    assembly.append(f"""
    check_c_boundary
    li t2, {saved_status:#x}
    bne s1, t2, .Lfail
    lw t2, PT_CLIC_CAUSE(sp)
    li t3, {raw:#x}
    bne t2, t3, .Lfail
    /* Linux may switch to idle before this trap's final assembly return. */
    check_idle_delivery

    /* A second IRQ can interrupt kernel C while the first frame is parked. */
    addi sp, sp, -160
    li t0, 255
    sw t0, LIVE_MIL(s6)
    li t0, 2
    sw t0, TRAP_KIND(s6)
    li t0, 0x10000
    sw t0, LIVE_STATUS(s6)
    li s4, 0xb8000011
    model_cause s4
    lw s1, LIVE_STATUS(s6)
""")
    assembly.append(entry)
    assembly.append(instrument(pop, f"nested_{index}"))
    assembly.append("""
    check_c_boundary
    mv a0, s1
    li a2, 0x48001234
""")
    assembly.append(instrument(restore, f"nested_exit_{index}"))
    assembly.append("""
    model_mret
    lw t2, LIVE_PRIV(s6)
    li t3, 3
    bne t2, t3, .Lfail
    lw t2, LIVE_MIL(s6)
    bnez t2, .Lfail
    addi sp, sp, 160
""")
    assembly.append(f"""
    /* Scheduler picks a frame saved from a different trap type/task. */
    li t0, {return_raw:#x}
    sw t0, PT_CLIC_CAUSE(sp)
    li t0, {sentinel:#x}
    sw t0, PT_CAUSE(sp)
    li a0, {return_status:#x}
    sw a0, PT_STATUS(sp)
    li a2, 0x49a17bee
    sw a2, PT_EPC(sp)
""")
    assembly.append(instrument(restore, f"outer_exit_{index}"))
    assembly.append(f"""
    model_mret
    lw t2, LIVE_PRIV(s6)
    li t3, {(return_status >> 11) & 3}
    bne t2, t3, .Lfail
    lw t2, LIVE_STATUS(s6)
    andi t2, t2, 8
    li t3, {(return_status >> 4) & 8}
    bne t2, t3, .Lfail
    lw t2, LIVE_MIL(s6)
    bnez t2, .Lfail
    lw t2, IRQ_POPS(s6)
    li t3, {int(irq) + 1}
    bne t2, t3, .Lfail
    lw t2, LIVE_CAUSE(s6)
    li t3, 0xc0ff0000
    and t2, t2, t3
    bnez t2, .Lfail
    lw t2, LIVE_EPC(s6)
    li t3, 0x49a17bee
    bne t2, t3, .Lfail
    lw t2, PT_CAUSE(sp)
    li t3, {sentinel:#x}
    bne t2, t3, .Lfail
""")

assembly.append("""
    li a0, 0
    li a7, 93
    ecall
.Lfail:
    mv a0, s11
    li a7, 93
    ecall
.bss
.balign 4
csr_state:
    .zero 32
""")
test_source = variant / "clic-lifecycle-check.S"
test_binary = variant / "clic-lifecycle-check.elf"
test_source.write_text("".join(assembly))
compiler = args.cross_compile + "gcc"
subprocess.run([compiler, "-nostdlib", "-static", "-march=rv32imac", "-mabi=ilp32",
                "-Wl,-Ttext=0x10000", "-o", str(test_binary), str(test_source)], check=True)
result = subprocess.run(["qemu-riscv32", str(test_binary)])
if args.negative_control:
    if result.returncode != 1:
        raise SystemExit(f"Negative control did not reproduce the first timer stall: {result.returncode}")
    print("PASS: negative control blocks the pending timer when idle enables IRQs with MIL still raised.")
    raise SystemExit(0)
if result.returncode:
    raise SystemExit(f"FAIL: lifecycle scenario {result.returncode}: {cases[result.returncode - 1]}")
print(f"PASS: {len(cases)} RV32 scenarios, modeled P4 CSR aliases, MIL and interrupt mret.")
print("Checked C/idle scheduling boundary, nested IRQ, cross-task return and signal sentinel.")
print("This model does not replace ESP32-P4 silicon validation.")
