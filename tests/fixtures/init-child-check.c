/* Execute init's real child path against a PTY with a stopped reader.
 * vfork's parent half and exec are test boundaries; terminal setup and the
 * order of logging/redirection come from the actual patched BusyBox source.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/types.h>
#include <termios.h>
#include <unistd.h>
#define BB_MMU 0
#define ALIGN1 __attribute__((aligned(1)))
#define ENABLE_FEATURE_INIT_COREDUMPS 0
#define ASKFIRST 1
#define L_LOG 1
#define L_CONSOLE 2
#define STRERROR_FMT "%s"
#define STRERROR_ERRNO , strerror(errno)
#define vfork() 0
#define _exit_FAILURE() _exit(1)
#define dbg_message(...) ((void)0)
#define tcsetattr_stdin_TCSANOW(t) tcsetattr(STDIN_FILENO, TCSANOW, (t))
#define safe_read read
struct init_action {
    uint8_t action_type;
    const char *terminal;
    const char *command;
};
static ssize_t full_write(int fd, const void *data, size_t bytes)
{
    const char *cursor = data;
    size_t total = 0;
    while (total < bytes) {
        ssize_t done = write(fd, cursor + total, bytes - total);
        if (done <= 0)
            return -1;
        total += done;
    }
    return total;
}
static void message(int where, const char *format, ...)
{
    (void)where;
    char text[256];
    va_list arguments;
    va_start(arguments, format);
    int bytes = vsnprintf(text, sizeof(text), format, arguments);
    va_end(arguments);
    if (bytes > 0)
        full_write(STDERR_FILENO, text, (size_t)bytes < sizeof(text) ? (size_t)bytes : sizeof(text) - 1);
}
static int device_open(const char *name, int flags)
{
    return open(name, flags | O_NOCTTY);
}
static void reset_sighandlers_and_unblock_sigs(void) { }
static void init_exec(const char *marker)
{
    int fd = open(marker, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (fd < 0 || full_write(fd, "exec\n", 5) != 5)
        _exit(1);
    _exit(0);
}

/* ACTUAL_INIT_CODE */

int main(int argc, char **argv)
{
    if (argc != 3)
        return 1;
    const struct init_action action = { .terminal = argv[1], .command = argv[2] };
    run(&action);
    return 1;
}
