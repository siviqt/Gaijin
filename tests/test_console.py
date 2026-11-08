"""Exercise the pinned Linux console= parser against the loader's bootargs."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = ROOT / "build/linux-6.18.35/kernel/printk/printk.c"
        if not source.exists():
            raise unittest.SkipTest("pinned Linux source is not prepared")
        kernel = source.read_text()
        start = kernel.index("static int __init console_setup(char *str)")
        end = kernel.index('__setup("console=", console_setup);', start)
        parser = kernel[start:end]
        # Compile the actual kernel parser; only its storage/helpers are
        # stubbed. This catches digits inside a device-name prefix.
        prefix = r'''
#include <stdbool.h>
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "boot_contract.h"
#define __init
#define static_assert _Static_assert
#define scnprintf snprintf
#define strscpy(dst, src) snprintf(dst, sizeof(dst), "%s", src)
#define simple_strtoul strtoul
static struct { char devname[32]; char name[16]; } console_cmdline[1];
static int _braille_console_setup(char **s, char **b) { (void)s; (void)b; return 0; }
static int __add_preferred_console(const char *name, short idx,
    const char *dev, char *opts, char *brl, bool user)
{
    (void)dev; (void)brl; (void)user;
    printf("%s|%d|%s\n", name, idx, opts ? opts : "");
    return 0;
}
'''
        suffix = r'''
int main(int argc, char **argv)
{
    char buf[512];
    const char *args = argc > 1 && !strcmp(argv[1], "usb") ? M0_USB_BOOTARGS : M0_BOOTARGS;
    snprintf(buf, sizeof(buf), "%s", args);
    for (char *token = strtok(buf, " "); token; token = strtok(NULL, " "))
        if (!strncmp(token, "console=", 8) && console_setup(token + 8) != 1)
            return 1;
    return 0;
}
'''
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        base = Path(temp.name)
        cfile = base / "console.c"
        cfile.write_text(prefix + parser + suffix)
        cls.binary = base / "console"
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "main"),
                        str(cfile), "-o", str(cls.binary)], check=True)

    def test_lcd_is_last_preferred_console(self):
        self.assertEqual(subprocess.check_output([str(self.binary)], text=True).strip(),
                         "ttyUJ|0|115200n8\ntty|0|")

    def test_display_failure_selects_only_usb(self):
        self.assertEqual(subprocess.check_output([str(self.binary), "usb"], text=True).strip(),
                         "ttyUJ|0|115200n8")


if __name__ == "__main__":
    unittest.main()
