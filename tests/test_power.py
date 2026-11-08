"""Fault-inject the real power controller; no host power/signal calls execute."""
import ctypes as C
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from kernel_source import added_file

ROOT = Path(__file__).resolve().parents[1]


class PowerCommands(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        harness = r'''
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/reboot.h>
#include <mntent.h>
#include <signal.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
static int firmware_fd, kmsg_fd = -1, backlight_read_fd = -1, backlight_write_fd = -1;
static int failure(const char *s) {return getenv("POWER_FAIL") && !strcmp(getenv("POWER_FAIL"),s);}
static int mock_open(const char *path,int flags,...) {
    if (failure("preflight") && strstr(path,"ready")) {errno=ENOENT; return -1;}
    if (failure("trace") && !strcmp(path,"/dev/kmsg")) {errno=EIO;return -1;}
    if (failure("console") && !strcmp(path,"/dev/console")) {errno=EIO;return -1;}
    if (strstr(path,"/bl_power") && failure("backlight-missing")) {errno=ENOENT;return -1;}
    int fd=open("/dev/null",O_RDWR);
    firmware_fd=strstr(path,"firmware_version") ? fd : -1;
    if (!strcmp(path,"/dev/kmsg")) kmsg_fd=fd;
    if (strstr(path,"/bl_power")) {
        if ((flags & O_ACCMODE)==O_WRONLY) backlight_write_fd=fd;
        else backlight_read_fd=fd;
    }
    return fd;
}
static ssize_t mock_read(int fd,void *buffer,size_t len) {
    (void)len;
    const char *value=fd==firmware_fd ? "9\n" : "1\n";
    if (failure("firmware") && fd==firmware_fd) value="8\n";
    if (fd==backlight_read_fd) value=failure("backlight-off") ? "4\n" : "0\n";
    memcpy(buffer,value,2); return 2;
}
static ssize_t mock_write(int fd,const void *buffer,size_t len) {
    if (fd==kmsg_fd) puts("trace");
    if (fd==backlight_write_fd) {
        const char *value=buffer;
        if (len!=2 || value[1]!='\n' || (value[0]!='0' && value[0]!='4')) abort();
        if ((value[0]=='4' && failure("backlight-blank")) ||
            (value[0]=='0' && failure("backlight-restore"))) {errno=EIO;return -1;}
        printf("backlight:%c\n",value[0]);
        return len;
    }
    return write(fd,buffer,len);
}
static int mock_dup2(int old,int target) {(void)old;printf("console-fd:%d\n",target);return target;}
static int mock_flock(int fd,int op) {(void)fd;(void)op; if(failure("lock")){errno=EWOULDBLOCK;return -1;}return 0;}
static int mock_chdir(const char *p) {printf("chdir:%s\n",p);return 0;}
static uid_t mock_uid(void) {return failure("uid") ? 1000 : 0;}
static pid_t mock_pid(void) {return 100;}
static int mock_kill(pid_t pid,int sig) {
    printf("signal:%d:%d\n",pid,sig);
    if(failure("pause") && pid==1 && sig==SIGTSTP){errno=EPERM;return -1;}
    return 0;
}
static unsigned mock_sleep(unsigned n) {(void)n;return 0;}
static int mock_usleep(useconds_t n) {(void)n;return 0;}
static pid_t mock_vfork(void) {puts("cleanup-spawn");if(failure("spawn")){errno=ENOMEM;return -1;}return 200;}
static pid_t mock_wait(pid_t pid,int *status,int flags) {(void)flags;*status=failure("cleanup") ? 256 : 0;return pid;}
static FILE *mock_mounts(const char *path,const char *mode) {
    (void)path;(void)mode;
    const char *data=failure("mount") ? "/dev/dm-0 /persist ext2 rw 0 0\n" : "/dev/loop0 / squashfs ro 0 0\ntmpfs /run tmpfs rw 0 0\n";
    return fmemopen((void *)data,strlen(data),"r");
}
static void __attribute__((unused)) mock_sync(void) {fputs("unexpected global sync",stderr);abort();}
static long mock_syscall(long nr,...) {
    va_list ap;va_start(ap,nr);
    int magic1=va_arg(ap,int),magic2=va_arg(ap,int),cmd=va_arg(ap,int);
    const char *arg=va_arg(ap,const char *);va_end(ap);
    if(nr!=SYS_reboot || (unsigned)magic1!=LINUX_REBOOT_MAGIC1 || magic2!=LINUX_REBOOT_MAGIC2) abort();
    printf("transition:%x:%s\n",cmd,arg ? arg : "");errno=EIO;return -1;
}
#define open mock_open
#define read mock_read
#define write mock_write
#define flock mock_flock
#define dup2 mock_dup2
#define chdir mock_chdir
#define geteuid mock_uid
#define getpid mock_pid
#define kill mock_kill
#define sleep mock_sleep
#define usleep mock_usleep
#define vfork mock_vfork
#define waitpid mock_wait
#define setmntent mock_mounts
#define sync mock_sync
#define syscall mock_syscall
'''
        source = ROOT / 'userspace/package/gaijin-state/power.c'
        (work / 'power.c').write_text(harness + '\n' + source.read_text())
        cls.binary = work / 'gaijin-power'
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O1',
                        '-fsanitize=undefined', '-fno-sanitize-recover=all',
                        '-o', str(cls.binary), str(work / 'power.c')], check=True)

    def run_power(self, *args, fail=''):
        return subprocess.run([str(self.binary), *args], env=dict(os.environ, POWER_FAIL=fail),
                              capture_output=True, text=True, timeout=5)

    def test_all_modes_clean_up_before_the_correct_linux_transition(self):
        for args, expected in ((('shutdown',), '1234567:'),
                               (('shutdown', 'now'), '1234567:'),
                               (('poweroff',), '4321fedc:'),
                               (('halt',), 'cdef0123:'),
                               (('shutdown', '-H', 'now'), 'cdef0123:'),
                               (('shutdown', '-P', 'now'), '4321fedc:')):
            with self.subTest(args=args):
                result = self.run_power(*args)
                self.assertIn('transition:' + expected, result.stdout)
                self.assertIn('persistence cleanup begin', result.stderr)
                self.assertIn('no writable device mounts; invoke Linux power transition', result.stderr)
                events = result.stdout
                self.assertLess(events.index('signal:1:'), events.index('signal:-1:'))
                self.assertLess(events.index('signal:-1:'), events.index('console-fd:0'))
                self.assertLess(events.index('console-fd:0'), events.index('cleanup-spawn'))
                self.assertLess(events.index('cleanup-spawn'), events.index('transition:'))
                self.assertNotIn('sync\n', events)
                self.assertIn('signal:1:18', events)  # failed syscall resumes init

    def test_preflight_check_and_invalid_arguments_have_no_side_effects(self):
        result = self.run_power('shutdown', '--check')
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('signal:', result.stdout)
        self.assertNotIn('backlight:', result.stdout)
        for args in (('reboot',), ('reboot', '--check'), ('poweroff', '-n'),
                     ('shutdown', '+5'), ('shutdown', '-r', 'now')):
            result = self.run_power(*args)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn('signal:', result.stdout)
        for failure in ('preflight', 'uid', 'lock', 'pause', 'trace'):
            result = self.run_power('shutdown', fail=failure)
            self.assertNotIn('signal:-1:', result.stdout)
            self.assertNotIn('transition:', result.stdout)
            self.assertNotIn('backlight:', result.stdout)

        result = self.run_power('poweroff', fail='firmware')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('signal:', result.stdout)
        self.assertNotIn('transition:', result.stdout)

    def test_cleanup_failures_and_rw_mounts_block_transition_and_resume_init(self):
        for failure in ('spawn', 'cleanup', 'mount', 'console'):
            result = self.run_power('shutdown', fail=failure)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('transition:', result.stdout)
            self.assertIn('signal:1:18', result.stdout)
            self.assertIn('cancelled', result.stderr)
            self.assertNotIn('no writable device mounts; invoke Linux power transition', result.stderr)
            self.assertLess(result.stdout.index('backlight:0'), result.stdout.index('signal:1:18'))

    def test_shutdown_and_poweroff_blank_before_output_and_restore_on_failure(self):
        for action in ('shutdown', 'poweroff'):
            result = self.run_power(action)
            events = result.stdout
            self.assertLess(events.index('backlight:4'), events.index('trace'))
            self.assertLess(events.index('transition:'), events.index('backlight:0'))
            self.assertLess(events.index('backlight:0'), events.index('signal:1:18'))
        for failure in ('backlight-off', 'backlight-missing', 'backlight-blank'):
            result = self.run_power('shutdown', fail=failure)
            self.assertIn('transition:', result.stdout)
            self.assertNotIn('backlight:', result.stdout)
        self.assertNotIn('backlight:', self.run_power('halt').stdout)
        result = self.run_power('poweroff', fail='backlight-restore')
        self.assertIn('could not restore backlight', result.stderr)
        self.assertIn('signal:1:18', result.stdout)


class LauncherReset(unittest.TestCase):
    def test_reset_clears_appfs_magic_even_for_legacy_gaijin_argument(self):
        source = added_file('drivers/power/reset/tanmatsu-power.c')
        source = source[source.index('struct tanmatsu_power'):source.index('static ssize_t ready_show')]
        source = source.replace('((void (*)(void))0x4fc00094UL)();', 'mock_rom_reset();')
        harness = r'''
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#define __iomem
#define NOTIFY_DONE 0
#define pr_emerg(...) ((void)0)
static unsigned char retained[256];
static int order;
struct sys_off_data {void *cb_data; const char *cmd;};
static void writel(uint32_t value, void *p) {memcpy(p, &value, 4);}
static void wmb(void) {if (memcmp(retained, "\0\0\0\0\0\0\0\0", 8)) abort(); order = 1;}
static void local_irq_disable(void) {if (order != 1) abort(); order = 2;}
static void mock_rom_reset(void) {if (order != 2) abort(); order = 3;}
''' + source + r'''
int main(void) {
    struct tanmatsu_power power = {.retained = retained};
    const char *commands[] = {NULL, "launcher", "gaijin"};
    for (unsigned i = 0; i < sizeof(commands) / sizeof(commands[0]); ++i) {
        memset(retained, 0xa5, sizeof(retained));
        order = 0;
        struct sys_off_data data = {.cb_data = &power, .cmd = commands[i]};
        if (tanmatsu_restart(&data) != NOTIFY_DONE || order != 3) abort();
        for (unsigned j = 8; j < sizeof(retained); ++j)
            if (retained[j] != 0xa5) abort();
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            (work / 'reset.c').write_text(harness)
            subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-o', str(work / 'reset'),
                            str(work / 'reset.c')], check=True)
            subprocess.run([str(work / 'reset')], check=True)


class MCUPowerOff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        source = added_file('drivers/input/keyboard/tanmatsu-mcu.c')
        source = source[source.index('static int tanmatsu_poweroff_prepare'):source.index('static ssize_t firmware_version_show')]
        harness = r'''
#include <stdint.h>
#include <errno.h>
typedef uint8_t u8;
#define TANMATSU_FW 9
#define NOTIFY_DONE 0
#define dev_emerg(...) ((void)0)
struct i2c_client {int unused;};
struct sys_off_data {void *cb_data;};
static unsigned fw, writes, wait_ms, address, value;
static int read_result, write_result;
static int tanmatsu_read(struct i2c_client *c,int reg,u8 *out,int size) {
    (void)c;(void)reg;(void)size;out[0]=fw;out[1]=fw>>8;return read_result;
}
static int i2c_master_send(struct i2c_client *c,u8 *command,unsigned size) {
    (void)c;(void)size;writes++;address=command[0];value=command[1];return write_result;
}
static void msleep(unsigned n) {wait_ms=n;}
'''
        harness += source + r'''
void reset(unsigned version,int rd,int wr) {fw=version;read_result=rd;write_result=wr;writes=wait_ms=0;}
void prepare(void) {struct i2c_client c;struct sys_off_data d={.cb_data=&c};tanmatsu_poweroff_prepare(&d);}
void final(void) {struct sys_off_data d={0};tanmatsu_poweroff_final(&d);}
unsigned stat(unsigned n) {unsigned a[]={writes,wait_ms,address,value};return a[n];}
'''
        (work / 'mcu.c').write_text(harness)
        subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Werror', '-o', str(work / 'mcu.so'), str(work / 'mcu.c')], check=True)
        cls.lib = C.CDLL(str(work / 'mcu.so'))

    def test_poweroff_checks_firmware_and_sends_exact_non_alarm_command(self):
        self.lib.reset(9, 0, 2); self.lib.prepare()
        self.assertEqual([self.lib.stat(n) for n in range(4)], [1, 500, 126, 1])
        self.lib.reset(9, 0, 2); self.lib.final()
        self.assertEqual(self.lib.stat(0), 0)  # atomic/final and halt never use I2C
        for version, read, write in ((8, 0, 2), (265, 0, 2), (9, -5, 2), (9, -110, 2)):
            self.lib.reset(version, read, write); self.lib.prepare()
            self.assertEqual(self.lib.stat(0), 0)
        for write in (0, 1, -5, -110):
            self.lib.reset(9, 0, write); self.lib.prepare()
            self.assertEqual(self.lib.stat(0), 1)
            self.assertEqual(self.lib.stat(1), 0)
