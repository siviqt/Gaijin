// SPDX-License-Identifier: MIT
#define _GNU_SOURCE
#include <signal.h>
#include <stdio.h>
#include <ucontext.h>

static volatile sig_atomic_t illegal_instructions;

static void handle_illegal_instruction(int signal, siginfo_t *info, void *context)
{
    ucontext_t *registers = context;

    (void)signal;
    (void)info;
    illegal_instructions++;
    /* Both CSR reads below are 32-bit RISC-V instructions. */
    registers->uc_mcontext.__gregs[REG_PC] += 4;
}

int main(void)
{
    struct sigaction action = {
        .sa_sigaction = handle_illegal_instruction,
        .sa_flags = SA_SIGINFO,
    };

    sigemptyset(&action.sa_mask);
    if (sigaction(SIGILL, &action, NULL)) {
        perror("sigaction");
        return 1;
    }

    __asm__ volatile("csrr zero, mstatus" ::: "memory");
    __asm__ volatile("csrr zero, mhartid" ::: "memory");

    if (illegal_instructions != 2) {
        printf("U-mode FAIL: %d of 2 privileged CSR reads trapped\n",
               (int)illegal_instructions);
        return 1;
    }
    puts("U-mode PASS: both privileged CSR reads trapped and signal returns completed");
    return 0;
}
