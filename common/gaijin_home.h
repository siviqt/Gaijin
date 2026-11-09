// SPDX-License-Identifier: MIT
/* Shared wire/format contract. No pointers or native structs go on disk. */
#ifndef GAIJIN_HOME_H
#define GAIJIN_HOME_H
#define GH_FACTORY_BYTES (32U * 1024U * 1024U)
#define GH_RESERVE_BYTES (16U * 1024U * 1024U)
#define GH_IMAGE "userdata.ext2"
#define GH_TEMP "userdata.new"
#define GH_TRANSACTION "provision-v1"
#define GH_RESET_PREFIX "ghr1:"
#define GH_RESET_LENGTH 111
#define GH_APPFS_MAGIC 0x2af7de4a994f7236ULL
#define GH_APPFS_ARG_OFFSET 13
#define GH_APPFS_ARG_BYTES 128

static inline unsigned int gh_le16(const unsigned char *p)
{ return p[0] | ((unsigned int)p[1] << 8); }
static inline unsigned int gh_le32(const unsigned char *p)
{ return gh_le16(p) | (gh_le16(p + 2) << 16); }
static inline int gh_format(const unsigned char *s, unsigned long long size)
{
    static const char label[] = "GAIJIN-STATE";
    unsigned int i;
    if (size < 16U*1024U*1024U || size > 256U*1024U*1024U ||
        gh_le16(s+56) != 0xef53 || gh_le32(s+24) != 0 ||
        gh_le32(s+76) != 1 || gh_le16(s+88) != 128 ||
        gh_le32(s+92) != 0 || gh_le32(s+96) != 2 || gh_le32(s+100) != 3 ||
        (unsigned long long)gh_le32(s+4)*1024 != size) return 0;
    for (i = 0; i < 12; i++) if (s[120+i] != (unsigned char)label[i]) return 0;
    return 1;
}
static inline int gh_hex(char c)
{ return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); }
/* ghr1:<CID,32 hex>:<file length,8 hex>:<fresh superblock SHA256,64 hex> */
static inline int gh_reset_valid(const char *p)
{
    unsigned int i;
    if (!p) return 0;
    for (i = 0; i < 5; i++) if (p[i] != GH_RESET_PREFIX[i]) return 0;
    for (i = 5; i < GH_RESET_LENGTH; i++) {
        if (!p[i]) return 0;
        if (i == 37 || i == 46) { if (p[i] != ':') return 0; }
        else if (!gh_hex(p[i])) return 0;
    }
    return p[GH_RESET_LENGTH] == 0;
}
#endif
