// SPDX-License-Identifier: MIT
#define _DEFAULT_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <unistd.h>

static int expect_fault(long result, const char *operation, unsigned long address)
{
    if (result == -1 && errno == EFAULT)
        return 0;
    fprintf(stderr, "Uaccess FAIL: %s at %#lx returned %ld errno=%d\n",
            operation, address, result, errno);
    return 1;
}

int gaijin_uaccess_probe(void)
{
    /* Invoke syscalls directly: libc's nonnull annotations are inappropriate
     * for a deliberate invalid-pointer test. Only a temporary RAM file is used.
     */
    static const unsigned long bad[] = {
        0, 1, 0x47ffffffUL, 0x4a000000UL, 0x500d2000UL, ~0UL,
    };
    char path[] = "/tmp/m5-uaccess-XXXXXX";
    char byte;
    int fd = mkstemp(path);
    if (fd < 0) {
        perror("uaccess temporary file");
        return 1;
    }
    unlink(path);
    if (write(fd, "x", 1) != 1)
        goto io_error;
    for (unsigned int i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        errno = 0;
        long result = syscall(SYS_openat, AT_FDCWD, bad[i], O_RDONLY, 0);
        if (expect_fault(result, "openat", bad[i])) {
            if (result >= 0)
                close((int)result);
            close(fd);
            return 1;
        }
        if (lseek(fd, 0, SEEK_SET) != 0)
            goto io_error;
        errno = 0;
        result = syscall(SYS_read, fd, bad[i], 1);
        if (expect_fault(result, "read", bad[i])) {
            close(fd);
            return 1;
        }
        if (lseek(fd, 0, SEEK_SET) != 0)
            goto io_error;
        errno = 0;
        result = syscall(SYS_write, fd, bad[i], 1);
        if (expect_fault(result, "write", bad[i])) {
            close(fd);
            return 1;
        }
    }
    if (lseek(fd, 0, SEEK_SET) != 0 || read(fd, &byte, 1) != 1 || byte != 'x' ||
        lseek(fd, 0, SEEK_SET) != 0 || write(fd, "v", 1) != 1 ||
        lseek(fd, 0, SEEK_SET) != 0 || read(fd, &byte, 1) != 1 || byte != 'v')
        goto io_error;
    close(fd);
    puts("Uaccess PASS: invalid pathname/read/write pointers return EFAULT; valid RAM I/O survives");
    return 0;
io_error:
    perror("uaccess RAM file I/O");
    close(fd);
    return 1;
}
