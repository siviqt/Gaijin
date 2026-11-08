// SPDX-License-Identifier: MIT
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/reboot.h>
#include <mntent.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#define POWER_SYSFS "/sys/bus/platform/devices/5010800c.power-controller/"
#define BACKLIGHT_POWER "/sys/class/backlight/tanmatsu/bl_power"
/* This binary is target-only. Host regressions replace all transition calls. */
enum action { GA_LAUNCHER, GA_POWEROFF, GA_HALT };

static int trace_fd = -1;
static void power_note(const char *message)
{
    char buffer[256];
    int n = snprintf(buffer, sizeof(buffer), "<6>Gaijin power: %s\n", message);
    if (trace_fd >= 0 && n > 0 && (size_t)n < sizeof(buffer))
        (void)write(trace_fd, buffer, (size_t)n);
    fprintf(stderr, "Gaijin power: %s\n", message);
}

static int parse_action(int argc, char **argv, enum action *action, int *check)
{
    const char *name = strrchr(argv[0], '/');
    name = name ? name + 1 : argv[0];
    *check = 0;
    if (!strcmp(name, "gaijin-power")) {
        if (argc < 2) return -1;
        name = argv[1]; argv++; argc--;
    }
    if (!strcmp(name, "shutdown")) *action = GA_LAUNCHER;
    else if (!strcmp(name, "poweroff")) *action = GA_POWEROFF;
    else if (!strcmp(name, "halt")) *action = GA_HALT;
    else return -1;
    if (argc == 2 && !strcmp(argv[1], "--check")) { *check = 1; return 0; }
    if (*action == GA_LAUNCHER) {
        if (argc > 1 && !strcmp(argv[1], "now")) return argc == 2 ? 0 : -1;
        if (argc > 1) {
            if (!strcmp(argv[1], "-P")) *action = GA_POWEROFF;
            else if (!strcmp(argv[1], "-H")) *action = GA_HALT;
            else return -1;
            return argc == 2 || (argc == 3 && !strcmp(argv[2], "now")) ? 0 : -1;
        }
    }
    return argc == 1 ? 0 : -1;
}

static int read_number(const char *path, long expected)
{
    char buf[32], *end;
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    ssize_t n = read(fd, buf, sizeof(buf) - 1);
    int saved = errno;
    close(fd); errno = saved;
    if (n <= 0) return -1;
    buf[n] = 0;
    errno = 0;
    long value = strtol(buf, &end, 10);
    if (errno || end == buf || (*end != '\n' && *end != '\0') || value != expected) {
        errno = ENODEV; return -1;
    }
    return 0;
}

static int preflight(enum action action)
{
    if (geteuid() != 0 || getpid() == 1) { errno = EPERM; return -1; }
    /* Refuse to signal processes on a host, another board or an old kernel. */
    if (read_number(POWER_SYSFS "ready", 1)) return -1;
    if (action == GA_POWEROFF && read_number("/sys/bus/i2c/devices/0-005f/firmware_version", 9)) return -1;
    return 0;
}

static bool set_backlight_power(bool enabled)
{
    int fd = open(BACKLIGHT_POWER, O_WRONLY | O_CLOEXEC);
    if (fd < 0) return false;
    ssize_t n = write(fd, enabled ? "0\n" : "4\n", 2);
    close(fd);
    return n == 2;
}

static int reopen_console(void)
{
    /* Killing a login shell can hang up every inherited tty file. Reopen the
     * preferred console after stopping sessions so cleanup can still log and
     * echo's status cannot falsely turn a successful unmount into failure.
     */
    int fd = open("/dev/console", O_RDWR | O_NOCTTY);
    if (fd < 0) return -1;
    for (int target = 0; target < 3; target++) {
        if (dup2(fd, target) < 0) { if (fd > 2) close(fd); return -1; }
    }
    if (fd > 2) close(fd);
    return 0;
}

static int stop_persistence(void)
{
    char *const args[] = {"/sbin/gaijin-persist", "stop", NULL};
    pid_t pid = vfork();
    int status;
    if (pid < 0) return -1;
    if (!pid) { execv(args[0], args); _exit(127); }
    while (waitpid(pid, &status, 0) < 0) {
        if (errno != EINTR) return -1;
    }
    if (!WIFEXITED(status) || WEXITSTATUS(status)) { errno = EBUSY; return -1; }
    return 0;
}

static int writable_devices(void)
{
    FILE *mounts = setmntent("/proc/mounts", "r");
    struct mntent *m;
    int writable = 0;
    if (!mounts) return -1;
    while ((m = getmntent(mounts))) {
        if (hasmntopt(m, "rw") && (!strncmp(m->mnt_fsname, "/dev/", 5) ||
                                   !strcmp(m->mnt_type, "ext2"))) {
            fprintf(stderr, "Gaijin power: writable mount remains: %s\n", m->mnt_dir);
            writable = 1;
        }
    }
    if (ferror(mounts)) writable = 1;
    endmntent(mounts);
    if (writable) { errno = EBUSY; return -1; }
    return 0;
}

static int transition(enum action action)
{
    if (action == GA_LAUNCHER)
        return syscall(SYS_reboot, LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2,
                       LINUX_REBOOT_CMD_RESTART, NULL);
    return syscall(SYS_reboot, LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2,
                   action == GA_POWEROFF ? LINUX_REBOOT_CMD_POWER_OFF : LINUX_REBOOT_CMD_HALT, NULL);
}

int main(int argc, char **argv)
{
    enum action action;
    int check, lock, saved;
    bool restore_backlight = false;
    if (parse_action(argc, argv, &action, &check)) {
        fputs("usage: shutdown|poweroff|halt [--check]\n"
              "       gaijin-power shutdown|poweroff|halt [--check]\n"
              "       shutdown [-P|-H] [now]\n"
              "shutdown returns to Launcher; no force, no-sync or scheduled modes.\n", stderr);
        return 2;
    }
    if (preflight(action)) { perror("Gaijin power: transition unavailable"); return 1; }
    if (check) { puts("Gaijin power: transition available (no action performed)"); return 0; }
    lock = open("/run/gaijin-power.lock", O_RDWR | O_CREAT | O_CLOEXEC, 0600);
    if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB)) { perror("Gaijin power: transition already active"); return 1; }
    trace_fd = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
    if (trace_fd < 0 || setenv("GAIJIN_POWER_TRACE", "1", 1)) {
        perror("Gaijin power: cannot enable shutdown trace"); close(lock); return 1;
    }
    if (chdir("/")) { perror("Gaijin power: chdir"); close(lock); return 1; }
    signal(SIGHUP, SIG_IGN); signal(SIGTERM, SIG_IGN);
    signal(SIGINT, SIG_IGN); signal(SIGQUIT, SIG_IGN); signal(SIGPIPE, SIG_IGN);
    if (kill(1, SIGTSTP)) { perror("Gaijin power: cannot pause init"); close(lock); return 1; }
    /* Keep the panel dark while shutdown messages scroll the framebuffer.
     * bl_power leaves the user's brightness setting in place.
     */
    if (action != GA_HALT && !read_number(BACKLIGHT_POWER, 0))
        restore_backlight = set_backlight_power(false);
    usleep(100000);
    power_note("paused init respawns");
    power_note("stop user processes: TERM");
    /* Linux kill(-1) excludes this process and init. No shell-side killall
     * child can accidentally kill its own controller on NOMMU.
     */
    if (kill(-1, SIGTERM) && errno != ESRCH) goto failed;
    sleep(1);
    power_note("stop user processes: KILL");
    if (kill(-1, SIGKILL) && errno != ESRCH) goto failed;
    usleep(100000);
    power_note("reopen console begin");
    if (reopen_console()) goto failed;
    power_note("persistence cleanup begin");
    if (stop_persistence()) goto failed;
    power_note("persistence cleanup complete; audit mounts");
    if (writable_devices()) goto failed;
    /* Cleanup has syncfs'ed ext2, unmounted it, verified the clean header
     * through fresh raw reads and removed the DM/loop map. A global sync
     * here needlessly revisits overlapping block-device aliases, including
     * the auto-clearing raw loop. No writable device mount is left to flush.
     */
    power_note("no writable device mounts; invoke Linux power transition");
    if (transition(action) < 0) goto failed;
    errno = EIO; /* A successful transition does not return. */
failed:
    saved = errno;
    if (restore_backlight && !set_backlight_power(true))
        power_note("could not restore backlight");
    power_note("transition cancelled; resuming init");
    kill(1, SIGCONT);
    close(lock);
    errno = saved;
    perror("Gaijin power: cancelled; init resumed, no forced unmount");
    return 1;
}
