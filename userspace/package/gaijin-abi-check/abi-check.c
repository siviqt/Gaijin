// SPDX-License-Identifier: MIT
#define _DEFAULT_SOURCE
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#if !defined(__riscv) || __riscv_xlen != 32
#error "Gaijin requires RV32"
#endif
#if !defined(__riscv_float_abi_soft) || defined(__riscv_flen)
#error "Gaijin requires an integer ISA and the ilp32 soft-float ABI"
#endif
#if !defined(__UCLIBC__)
#error "Gaijin requires the pinned uClibc-ng userspace toolchain"
#endif
_Static_assert(sizeof(void *) == 4 && sizeof(long) == 4 && sizeof(int) == 4,
               "ilp32 widths required");
_Static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__, "little endian required");

/* This pointer and the writable global exercise bFLT data relocation. */
static const char *volatile identity = "gaijin-ilp32-v1";
static volatile uint32_t counter = 7;

int gaijin_uaccess_probe(void);

static int arithmetic(void)
{
    volatile float fa = 1.5f, fb = 2.25f;
    volatile double da = 9.0, db = 4.0;
    volatile uint64_t large = UINT64_C(0x1234567800000000);
    unsigned char *memory = malloc(256 * 1024);
    if (!memory)
        return 1;
    memset(memory, 0xa5, 256 * 1024);
    int failed = memory[0] != 0xa5 || memory[256 * 1024 - 1] != 0xa5;
    free(memory);
    counter += 5;
    return failed || counter != 12 || strcmp(identity, "gaijin-ilp32-v1") ||
           fa + fb != 3.75f || da / db != 2.25 ||
           large / UINT64_C(0x100000000) != UINT64_C(0x12345678);
}

int main(int argc, char **argv)
{
	if (argc == 2 && !strcmp(argv[1], "--uaccess"))
		return gaijin_uaccess_probe();
    if (arithmetic()) {
        fputs("ABI arithmetic/relocation/allocation FAIL\n", stderr);
        return 1;
    }
    if (argc == 2 && !strcmp(argv[1], "--child"))
        return 0;
    /* Nothing except execv/_exit in the vfork child: shared address space. */
    char *child_argv[] = { argv[0], "--child", NULL };
    pid_t child = vfork();
    if (!child) {
        execv("/usr/bin/gaijin-abi-check", child_argv);
        _exit(127);
    }
    if (child < 0) {
        perror("vfork");
        return 1;
    }
    int status;
    pid_t result;
    do {
        result = waitpid(child, &status, 0);
    } while (result < 0 && errno == EINTR);
    if (result != child || !WIFEXITED(status) || WEXITSTATUS(status)) {
        fputs("ABI vfork/exec/wait FAIL\n", stderr);
        return 1;
    }
    puts("ABI PASS: rv32imac ilp32 LE uClibc bFLT; software float/double, relocation, 256KiB malloc, vfork/exec/wait");
    return 0;
}
