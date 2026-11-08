// SPDX-License-Identifier: MIT
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

static long long milliseconds(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) {
        perror("clock_gettime");
        exit(1);
    }
    return (long long)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static int show_state(int fd)
{
    unsigned char keys[(KEY_MAX + 8) / 8] = {0};
    unsigned char switches[(SW_MAX + 8) / 8] = {0};
    unsigned int i;
    if (ioctl(fd, EVIOCGKEY(sizeof(keys)), keys) < 0 ||
        ioctl(fd, EVIOCGSW(sizeof(switches)), switches) < 0) {
        perror("evdev state");
        return -1;
    }
    printf("STATE keys:");
    for (i = 0; i <= KEY_MAX; i++)
        if (keys[i / 8] & (1U << (i % 8)))
            printf(" %u", i);
    printf("; switches:");
    for (i = 0; i <= SW_MAX; i++)
        if (switches[i / 8] & (1U << (i % 8)))
            printf(" %u", i);
    putchar('\n');
    return 0;
}

int main(int argc, char **argv)
{
    char path[64], name[128], *end;
    long seconds = 30;
    int fd = -1, dropped = 0, repeat[2] = {0};
    unsigned int events = 0, overflows = 0;
    long long deadline;
    if (argc > 3) {
        fprintf(stderr, "Usage: m2-input-test [/dev/input/eventN] [seconds:1..3600]\n");
        return 2;
    }
    if (argc == 3) {
        errno = 0;
        seconds = strtol(argv[2], &end, 10);
        if (errno || *end || end == argv[2] || seconds < 1 || seconds > 3600)
            return 2;
    }
    for (unsigned int i = 0; i < 32; i++) {
        if (argc >= 2)
            snprintf(path, sizeof(path), "%s", argv[1]);
        else
            snprintf(path, sizeof(path), "/dev/input/event%u", i);
        fd = open(path, O_RDONLY | O_NONBLOCK);
        memset(name, 0, sizeof(name));
        if (fd >= 0 && ioctl(fd, EVIOCGNAME(sizeof(name) - 1), name) >= 0 &&
            !strcmp(name, "Tanmatsu MCU keyboard"))
            break;
        if (fd >= 0)
            close(fd);
        fd = -1;
        if (argc >= 2)
            break;
    }
    if (fd < 0) {
        fprintf(stderr, "Tanmatsu MCU keyboard not found; inspect dmesg and /proc/bus/input/devices\n");
        return 1;
    }
    setvbuf(stdout, NULL, _IOLBF, 0);
    printf("M2 INPUT %s: %s; %ld seconds\n", path, name, seconds);
    if (ioctl(fd, EVIOCGREP, repeat) < 0) {
        perror("EVIOCGREP");
        close(fd);
        return 1;
    }
    printf("REPEAT delay=%d period=%d ms; EV_KEY value 0=up 1=down 2=repeat\n",
           repeat[0], repeat[1]);
    if (show_state(fd)) {
        close(fd);
        return 1;
    }
    deadline = milliseconds() + seconds * 1000;
    while (milliseconds() < deadline) {
        struct pollfd pfd = {.fd = fd, .events = POLLIN};
        struct input_event batch[16];
        int timeout = (int)(deadline - milliseconds());
        int ready = poll(&pfd, 1, timeout > 0 ? timeout : 0);
        ssize_t bytes;
        if (ready < 0 && errno == EINTR)
            continue;
        if (ready < 0 || pfd.revents & (POLLERR | POLLHUP | POLLNVAL)) {
            fprintf(stderr, "evdev poll failed/disconnected\n");
            close(fd);
            return 1;
        }
        if (!ready)
            continue;
        bytes = read(fd, batch, sizeof(batch));
        if (bytes < 0 && (errno == EINTR || errno == EAGAIN))
            continue;
        if (bytes <= 0 || bytes % sizeof(batch[0])) {
            fprintf(stderr, "invalid evdev read\n");
            close(fd);
            return 1;
        }
        for (size_t i = 0; i < (size_t)bytes / sizeof(batch[0]); i++) {
            struct input_event *event = &batch[i];
            if (event->type == EV_SYN && event->code == SYN_DROPPED) {
                dropped = 1;
                overflows++;
                puts("SYN_DROPPED: discarding through SYN_REPORT, then querying state");
            }
            if (dropped) {
                if (event->type == EV_SYN && event->code == SYN_REPORT) {
                    if (show_state(fd)) {
                        close(fd);
                        return 1;
                    }
                    dropped = 0;
                }
                continue;
            }
            printf("EVENT type=%u code=%u value=%d\n", event->type, event->code, event->value);
            events++;
        }
    }
    if (show_state(fd)) {
        close(fd);
        return 1;
    }
    printf("M2 INPUT capture complete: events=%u overflows=%u (manual key comparison required)\n",
           events, overflows);
    close(fd);
    return 0;
}
