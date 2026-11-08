"""Exercise the pinned Linux console= parser against the loader's bootargs."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ConsoleTests(unittest.TestCase):
    def test_boot_console_name_and_index(self):
        source = ROOT / "build/linux-6.18.35/kernel/printk/printk.c"
        if not source.exists():
            self.skipTest("pinned Linux source is not prepared")
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
int main(void)
{
    const char *option = strstr(M0_BOOTARGS, " console=");
    char buf[128];
    if (!option) return 1;
    option += strlen(" console=");
    snprintf(buf, sizeof(buf), "%s", option);
    char *space = strchr(buf, ' ');
    if (space) *space = 0;
    if (console_setup(buf) != 1) return 1;
    option = strstr(option, " console=");
    if (!option) return 1;
    snprintf(buf, sizeof(buf), "%s", option + strlen(" console="));
    space = strchr(buf, ' ');
    if (space) *space = 0;
    return console_setup(buf) == 1 ? 0 : 1;
}
'''
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            cfile = base / "console.c"
            cfile.write_text(prefix + parser + suffix)
            binary = base / "console"
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "main"),
                            str(cfile), "-o", str(binary)], check=True)
            self.assertEqual(subprocess.check_output([str(binary)], text=True).strip(), "tty|0|\nttyUJ|0|115200n8")


if __name__ == "__main__":
    unittest.main()
