/* Compile the real early console against a fake endpoint, with no time API. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;
#define __iomem
#define BIT(n) (1u << (n))
struct uart_port { void *membase; };
struct console { void *data; };
struct earlycon_device { struct uart_port port; };
static unsigned char mmio[16], sent[1024];
static unsigned int reads, writes, relaxes, count, capacity;
static bool ready;
static uint32_t readl(const void *address)
{
    (void)address;
    /* Fail promptly if a regression reintroduces per-character polling. */
    assert(++reads <= 1024);
    return ready && count < capacity ? BIT(1) : 0;
}
static void writel(uint32_t value, void *address)
{
    writes++;
    if (address == mmio) {
        assert(count < sizeof(sent));
        sent[count++] = value;
    }
}
#define cpu_relax() ((void)relaxes++)

/* ACTUAL_CONSOLE_CODE */

int main(int argc, char **argv)
{
    assert(argc == 2);
    struct earlycon_device dev = { .port = { .membase = mmio } };
    struct console con = { .data = &dev };
    capacity = sizeof(sent);
    if (!strcmp(argv[1], "full")) {
        const char text[] = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
        esp32p4_usb_jtag_earlycon_write(&con, text, strlen(text));
        assert(reads == 64 && writes == 0 && count == 0);
    } else if (!strcmp(argv[1], "reading")) {
        ready = true;
        esp32p4_usb_jtag_earlycon_write(&con, "A\n", 2);
        assert(count == 3 && !memcmp(sent, "A\r\n", 3));
    } else if (!strcmp(argv[1], "reconnect")) {
        esp32p4_usb_jtag_earlycon_write(&con, "lost", 4);
        assert(count == 0);
        ready = true;
        esp32p4_usb_jtag_earlycon_write(&con, "A\n", 2);
        assert(count == 3 && !memcmp(sent, "A\r\n", 3));
    } else if (!strcmp(argv[1], "fills")) {
        ready = true;
        capacity = 2;
        esp32p4_usb_jtag_earlycon_write(&con, "ABCD\n", 5);
        assert(count == 2 && !memcmp(sent, "AB", 2));
        assert(reads == 6);
    } else {
        abort();
    }
    assert(relaxes == 0);
    return 0;
}
