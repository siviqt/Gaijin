# Keep the vendor halt hook from disabling the hardware watchdogs.
esp32p4 configure -event halted {}
# Separate the effect of halting/resuming from OpenOCD's automatic fences.
riscv autofence off
echo "Automatic fences disabled for this capture"
source [file join [file dirname [info script]] capture-p4-before-halt.tcl]
esp32p4 configure -defer-examine
init
set resume_capture_halt 0
if {[esp32p4 was_examined]} {
    echo "Before-first-halt sample unavailable: target was already examined"
} else {
    if {[catch {p4_sample_before_examine [esp32p4 cget -chain-position]} result]} {
        echo "Before-examination capture incomplete: $result"
    }
    esp32p4 arp_examine
}
set initial_state [esp32p4 curstate]
echo "Target state before halt: $initial_state"
set was_halted [expr {$initial_state eq "halted" && !$resume_capture_halt}]

proc read_trace {label} {
    if {[catch {read_memory 0x50110058 32 5 phys} result]} {
        echo "$label unavailable: $result"
    } else {
        echo "$label: $result"
    }
}

if {$initial_state eq "running"} {
    riscv set_mem_access sysbus
    read_trace "LP trace after attach, before explicit halt"
    sleep 150
    read_trace "LP trace 150 ms later"
}

if {[catch {halt 1000} result]} {
    echo "CPU halt failed: $result"
    shutdown error
}

echo "=== CPU registers ==="
foreach name {pc sp ra s1 mstatus mcause mepc mtval dcsr} {
    if {[catch {dict get [get_reg -force [list $name]] $name} result]} {
        echo "Register $name unavailable: $result"
    } else {
        set regs($name) $result
        echo "$name = $result"
    }
}
# x8 is the frame pointer. Use its register number to avoid OpenOCD aliases.
if {[catch {capture {reg 8 force}} result] ||
    ![regexp {:[[:space:]]*(0x[[:xdigit:]]+)} $result -> frame_pointer]} {
    echo "Frame pointer unavailable: $result"
} else {
    set regs(fp) $frame_pointer
    echo "fp = $frame_pointer"
}

echo "=== Watchdog and interrupt state ==="
riscv set_mem_access sysbus progbuf abstract
foreach {label address words} {
    {LP store 11 through 15} 0x50110058 5
    {LP clock/reset cause} 0x50111010 1
    {LP watchdog CONFIG0} 0x50116000 1
    {SYSTIMER CONF and UNIT0_OP} 0x500e2000 2
    {SYSTIMER target0 high and low} 0x500e201c 2
    {SYSTIMER latched unit0 high and low} 0x500e2040 2
    {SYSTIMER interrupt enable} 0x500e2064 1
    {SYSTIMER raw interrupt status} 0x500e2068 1
    {SYSTIMER masked interrupt status} 0x500e2070 1
    {SYSTIMER interrupt matrix route} 0x500d60d4 1
    {Display DMA channel enable} 0x50081018 1
    {Display DMA channel 0 status} 0x50081188 1
    {DSI bridge DPI configuration} 0x500a0840 1
    {DSI bridge underrun raw status} 0x500a0858 1
} {
    if {[catch {read_memory $address 32 $words phys} result]} {
        echo "$label at $address unavailable: $result"
    } else {
        echo "$label at $address: $result"
    }
}

# Use the hart for CLIC registers and stack contents. System-bus CLIC reads
# returned zero even on a working boot.
riscv set_mem_access progbuf
foreach {slot address} {17 0x20801044 18 0x20801048} {
    if {[catch {read_memory $address 32 1 phys} result]} {
        echo "CLIC slot $slot unavailable: $result"
    } else {
        echo "CLIC slot $slot at $address: $result"
    }
}

# The supported RV32 kernel uses frame pointers and 8 KiB stacks in PSRAM. Walk only the
# current stack, with bounds checked before every read.
if {[info exists regs(pc)] && [info exists regs(sp)] && [info exists regs(fp)] &&
    $regs(pc) >= 0x48001000 && $regs(pc) < 0x48800000 &&
    $regs(sp) >= 0x48000000 && $regs(sp) < 0x4a000000} {
    echo "=== Kernel stack return addresses ==="
    set sp $regs(sp)
    set fp $regs(fp)
    set stack_end [expr {($sp | 0x1fff) + 1}]
    for {set i 0} {$i < 12 && $fp >= $sp + 8 && $fp <= $stack_end && ($fp & 7) == 0} {incr i} {
        if {[catch {read_memory [expr {$fp - 8}] 32 2 phys} frame]} {
            echo "Stack frame at $fp unavailable: $frame"
            break
        }
        lassign $frame next_fp return_pc
        echo "Frame $i return PC = $return_pc"
        set sp $fp
        set fp $next_fp
    }
}

if {!$was_halted} {
    if {[catch {resume} result]} {
        echo "CPU resume failed: $result"
        shutdown error
    }
    echo "Target state after resume: [esp32p4 curstate]"
    riscv set_mem_access sysbus
    sleep 150
    read_trace "LP trace after resume"
}
shutdown
