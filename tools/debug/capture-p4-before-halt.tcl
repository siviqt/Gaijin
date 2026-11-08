# Read ESP32-P4 peripheral registers through the RISC-V debug system bus
# before OpenOCD's target examination can halt and resume the CPU.
proc p4_dmi {link operation address {value 0}} {
    lassign $link tap address_bits idle_cycles
    set sent [drscan $tap 2 $operation 32 [format 0x%08x $value] $address_bits $address]
    runtest $idle_cycles
    # The result of the preceding request comes back with the NOP scan.
    set received [drscan $tap 2 0 32 0 $address_bits 0]
    runtest $idle_cycles
    foreach fields [list $sent $received] {
        if {[llength $fields] != 3 || ![regexp {^0+$} [lindex $fields 0]]} {
            # Clear sticky transport errors so normal OpenOCD examine can
            # still proceed. This only resets the DMI error state.
            irscan $tap 0x10
            drscan $tap 32 0x10000
            irscan $tap 0x11
            error "DMI request $operation at $address failed: $fields"
        }
    }
    return "0x[lindex $received 1]"
}

proc p4_sba_idle {link} {
    for {set i 0} {$i < 8} {incr i} {
        set status [p4_dmi $link 1 0x38]
        if {!($status & 0x00200000)} {
            return $status
        }
    }
    error "System bus still busy: SBCS=$status"
}

proc p4_read_mmio {link address} {
    # SBADDRESS0 triggers a read. SBDATA0 is only read, never written.
    p4_dmi $link 2 0x39 $address
    set status [p4_sba_idle $link]
    if {$status & 0x00407000} {
        error "System bus read at $address failed: SBCS=$status"
    }
    return [p4_dmi $link 1 0x3c]
}

proc p4_sample_running_state {link sample} {
    set trace {}
    foreach address {0x50110058 0x5011005c 0x50110060 0x50110064 0x50110068} {
        lappend trace [p4_read_mmio $link $address]
    }
    echo "$sample (magic phase ticks frames flushes): $trace"
    foreach {label address} {
        {SYSTIMER CONF} 0x500e2000
        {SYSTIMER target0 low} 0x500e2020
        {SYSTIMER latched counter low} 0x500e2044
        {SYSTIMER raw IRQ} 0x500e2068
        {SYSTIMER masked IRQ} 0x500e2070
        {Core 0 SYSTIMER route} 0x500d60d4
        {Core 0 interrupt sources 32 to 63} 0x500d6204
        {HP clock force-on control} 0x500e60b4
        {DSI DPI configuration} 0x500a0840
        {LP clock/reset cause} 0x50111010
    } {
        echo "$sample $label: [p4_read_mmio $link $address]"
    }
}

proc p4_wait_debug_status {link address mask expected} {
    for {set i 0} {$i < 30} {incr i} {
        set status [p4_dmi $link 1 $address]
        if {($status & $mask) == $expected} {
            return $status
        }
        sleep 10
    }
    error "DMI register $address did not reach $expected under mask $mask (last=$status)"
}

proc p4_read_hart_register {link regno} {
    # Access Register, RV32, transfer to DATA0, no program-buffer execution.
    p4_dmi $link 2 0x17 [expr {0x00220000 | $regno}]
    set abstractcs [p4_wait_debug_status $link 0x16 0x1000 0]
    if {$abstractcs & 0x700} {
        p4_dmi $link 2 0x16 0x700
        error "abstract read failed: ABSTRACTCS=$abstractcs"
    }
    return [p4_dmi $link 1 0x04]
}

proc p4_capture_first_halt {link} {
    global resume_capture_halt
    p4_dmi $link 2 0x10 1 ;# Select hart 0, with no halt or reset request.
    if {!([p4_dmi $link 1 0x11] & 0x800)} {
        echo "First halt skipped: hart 0 was not running"
        return 0
    }
    p4_wait_debug_status $link 0x16 0x1000 0
    set resume_capture_halt 1
    set failed [catch {
        p4_dmi $link 2 0x10 0x80000001 ;# HALTREQ, hart 0, DMACTIVE.
        p4_wait_debug_status $link 0x11 0x200 0x200
        p4_dmi $link 2 0x10 1
        echo "=== Registers at first halt, before OpenOCD examination ==="
        foreach {name regno} {
            dpc 0x7b1 dcsr 0x7b0 mstatus 0x300 mcause 0x342 mepc 0x341
            mintstatus_rev1 0x346 mtval 0x343 sp 0x1002 ra 0x1001
        } {
            if {[catch {p4_read_hart_register $link $regno} result]} {
                echo "First halt $name unavailable: $result"
            } else {
                echo "First halt $name = $result"
            }
        }
    } message]

    # Resume even if the first halt or a register read failed. If the raw
    # resume fails, let the normal capture resume a hart left halted by us.
    p4_wait_debug_status $link 0x16 0x1000 0
    p4_dmi $link 2 0x10 0x40000001 ;# RESUMEREQ, hart 0, DMACTIVE.
    p4_wait_debug_status $link 0x11 0x20800 0x20800
    set resume_capture_halt 0
    p4_dmi $link 2 0x10 1
    echo "Hart 0 resumed after first halt"
    if {$failed} {
        echo "First halt register capture incomplete: $message"
    }
    return 1
}

proc p4_sample_before_examine {tap} {
    irscan $tap 0x10
    set dtmcs "0x[lindex [drscan $tap 32 0] 0]"
    set address_bits [expr {($dtmcs >> 4) & 0x3f}]
    if {($dtmcs & 0xf) != 1 || $address_bits < 7 || $address_bits > 32} {
        error "Unexpected RISC-V debug transport configuration: DTMCS=$dtmcs"
    }
    if {$dtmcs & 0xc00} {
        drscan $tap 32 0x10000
    }
    set link [list $tap $address_bits [expr {8 + (($dtmcs >> 12) & 7)}]]
    irscan $tap 0x11

    # Enable only the debug module if needed. Neither HALTREQ nor any reset
    # bit is written here. OpenOCD will close the module after its capture.
    if {!([p4_dmi $link 1 0x10] & 1)} {
        p4_dmi $link 2 0x10 1
    }
    set dmstatus [p4_dmi $link 1 0x11]
    set version [expr {$dmstatus & 0xf}]
    if {!($dmstatus & 0x80) || ($version != 2 && $version != 3)} {
        error "Unexpected RISC-V debug module status: DMSTATUS=$dmstatus"
    }
    echo "Before first halt DMSTATUS: $dmstatus"

    set sbcs [p4_sba_idle $link]
    if {($sbcs >> 29) != 1 || (($sbcs >> 5) & 0x7f) != 32 ||
        !($sbcs & 4) || ($sbcs & 0x00407000)} {
        error "32-bit system-bus reads unavailable: SBCS=$sbcs"
    }
    # sbreadonaddr=1, sbaccess=32 bits, no increment or read-on-data.
    p4_dmi $link 2 0x38 0x00140000
    set failed [catch {
        p4_sample_running_state $link "Before first halt sample 1"
        sleep 150
        p4_sample_running_state $link "Before first halt sample 2"
        if {[p4_capture_first_halt $link]} {
            sleep 150
            p4_sample_running_state $link "After first halt/resume"
        }
    } message]
    if {[catch {
        p4_sba_idle $link
        p4_dmi $link 2 0x38 [expr {($sbcs & 0x001f8000) | 0x00407000}]
    } cleanup_message]} {
        echo "System bus cleanup unavailable: $cleanup_message"
    }
    if {$failed} {
        error $message
    }
}
