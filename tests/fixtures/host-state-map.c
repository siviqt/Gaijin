/* Model the complete privileged mapper without mounting or writing host devices. */
#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/dm-ioctl.h>
#include <linux/fs.h>
#include <linux/loop.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/statvfs.h>
#include <sys/sysmacros.h>
#include <unistd.h>
#include "gaijin_home.h"
#include "sha256.h"
static int bad, fragment, calls, gated, loaded, resumed, removed, configured, nmap;
static unsigned char disk_super[1024];
static char gate_text[4096], registered[4096];
static unsigned raw_reads;
static char *fake_realpath(const char *p, char *r)
{ strcpy(r, bad == 1 ? "/sd/other" : p); return r; }
static int fake_open(const char *p, int flags, ...)
{
    if (!strcmp(p, "/sd/gaijin/userdata.ext2")) { assert(flags & O_NOFOLLOW); return 10; }
    if (!strcmp(p, "/persist")) { assert(flags & O_DIRECTORY); assert(flags & O_NOFOLLOW); return bad == 16 ? -1 : 16; }
    if (!strcmp(p, "/dev/mmcblk0p1")) return 11;
    if (!strcmp(p, "/dev/mapper/control")) return 12;
    if (!strcmp(p, "/sys/class/mmc_host/mmc0/device/write_ranges")) return bad == 10 ? -1 : 13;
    if (!strcmp(p, "/dev/loop-control")) return 14;
    if (!strcmp(p, "/dev/loop7")) return 15;
    assert(0); return -1;
}
static int fake_close(int fd) { assert(fd >= 10 && fd <= 16); return 0; }
static int fake_fstat(int fd, struct stat *st)
{ assert(fd == 10); memset(st,0,sizeof(*st)); st->st_mode = S_IFREG | 0644; st->st_dev = makedev(179,1); st->st_size = 16*1024*1024; return 0; }
static int fake_stat(const char *p, struct stat *st)
{
    memset(st,0,sizeof(*st)); st->st_mode = S_IFBLK;
    if (!strcmp(p,"/dev/mmcblk0")) st->st_rdev = makedev(179,0);
    else if (!strcmp(p,"/dev/mmcblk0p1")) st->st_rdev = makedev(179,1);
    else if (!strcmp(p,"/dev/dm-3")) st->st_rdev = makedev(253,3);
    else { assert(0); return -1; }
    return 0;
}
static int fake_fstatfs(int fd, struct statfs *s)
{ assert(fd==10 || fd==16); memset(s,0,sizeof(*s));
  s->f_type = fd==16 ? (bad==17 ? 0x73717368 : 0xef53) : (bad==2 ? 0 : 0x4d44); return 0; }
static int fake_syncfs(int fd)
{ assert(fd==16); if (bad==18) {errno=EIO;return -1;} return 0; }
static int fake_fstatvfs(int fd, struct statvfs *s)
{ assert(fd==10); memset(s,0,sizeof(*s)); s->f_flag = bad==3 ? 0 : ST_RDONLY; return 0; }
static ssize_t fake_pread(int fd, void *p, size_t n, off_t off)
{
    assert(fd==11 && n==512 && ((uintptr_t)p % 4096)==0);
    unsigned index = off==(4096+2)*512 ? 0 : 1;
    unsigned expected = 4096+2+index+(fragment==2 && index ? 32768 : 0);
    assert(off==(off_t)expected*512); raw_reads++;
    if (bad==15) return 511;
    memcpy(p,disk_super+index*512,n); return n;
}
static FILE *fake_fopen(const char *p, const char *mode)
{
    if (!strcmp(p,"/sys/firmware/devicetree/base/chosen/gaijin,home-status")) {errno=ENOENT;return NULL;}
    if (!strcmp(p,"/sys/block/mmcblk0/device/cid")) {
        FILE *f=tmpfile();assert(f);fputs("0123456789abcdef0123456789abcdef\n",f);rewind(f);return f;
    }
    assert(!strcmp(p,"/sys/dev/block/179:1/start") && !strcmp(mode,"r"));
    if (bad==4) return NULL;
    FILE *f=tmpfile(); assert(f); fputs("63\n",f); rewind(f); return f;
}
static ssize_t fake_write(int fd, const void *p, size_t n)
{
    assert(fd==13 && loaded && !resumed); memcpy(gate_text,p,n); gate_text[n]=0; gated++;
    if (bad==11) return (ssize_t)n-1;
    if (registered[0] && strcmp(registered,gate_text)) { errno=EBUSY; return -1; }
    strcpy(registered,gate_text);
    return n;
}
static int fake_mknod(const char *p, mode_t mode, dev_t dev)
{ assert(!strcmp(p,"/dev/dm-3") && (mode & S_IFBLK) && dev==makedev(253,3)); return 0; }
static int fake_ioctl(int fd, unsigned long op, ...);
#define main state_main
#define realpath fake_realpath
#define open fake_open
#define close fake_close
#define syncfs fake_syncfs
#define fstat fake_fstat
#define stat fake_stat
#define fstatfs fake_fstatfs
#define fstatvfs fake_fstatvfs
#define pread fake_pread
#define fopen fake_fopen
#define write fake_write
#define mknod fake_mknod
#define ioctl fake_ioctl
/* STATE_SOURCE */
#undef main
#undef stat
#undef fstat
#undef fstatfs
#undef fstatvfs
#undef ioctl
#include <stdarg.h>
static int fake_ioctl(int fd, unsigned long op, ...)
{
    calls++;
    if (op==LOOP_CTL_GET_FREE) { assert(fd==14); return 7; }
    va_list ap; va_start(ap,op); void *arg=va_arg(ap,void *); va_end(ap);
    if (op==FIGETBSZ) { assert(fd==10); *(int *)arg=512; return 0; }
    if (op==BLKGETSIZE64) { assert(fd==11); *(uint64_t *)arg=1024ULL*1024*1024; return 0; }
    if (op==FIBMAP) {
        assert(fd==10); int n=*(int *)arg; nmap++;
        *(int *)arg = 4096+n+(fragment && n>=(fragment==2 ? 3 : 16384) ? 32768 : 0);
        if (bad==5 && n==100) *(int *)arg=0;
        if (bad==6 && n==100) *(int *)arg=4096;
        if (bad==7) *(int *)arg=4096+n*2; /* too fragmented */
        if (bad==8) *(int *)arg=3000000+n; /* outside partition */
        return 0;
    }
    if (op==LOOP_CONFIGURE) {
        assert(fd==15); struct loop_config *c=arg;
        assert(c->fd==11 && (c->info.lo_flags & ~LO_FLAGS_READ_ONLY)==(LO_FLAGS_AUTOCLEAR|LO_FLAGS_DIRECT_IO)); configured++;
        return bad==14 ? -1 : 0;
    }
    assert(fd==12);
    struct dm_ioctl *d=arg;
    assert(d->version[0]==4 && !strcmp(d->name,"gaijin-state"));
    if (op==DM_DEV_CREATE) return bad==9 ? -1 : 0;
    if (op==DM_TABLE_LOAD) {
        assert(!gated && configured);
        if (bad==13) return -1;
        assert(d->target_count==(fragment ? 2U : 1U));
        unsigned position=d->data_start;
        uint64_t logical=0;
        for (unsigned i=0;i<d->target_count;i++) {
            struct dm_target_spec *t=(void *)(table+position);
            assert(!strcmp(t->target_type,"linear") && t->sector_start==logical);
            unsigned boundary=fragment==2 ? 3 : 16384;
            assert(t->length==(fragment ? (i ? 32768-boundary : boundary) : 32768));
            char expected[64]; snprintf(expected,sizeof(expected),"/dev/loop7 %u",i ? 4096+boundary+32768 : 4096);
            assert(!strcmp((char *)(t+1),expected));
            logical+=t->length; position+=t->next;
        }
        assert(logical==32768); loaded++; return 0;
    }
    if (op==DM_DEV_SUSPEND) {
        assert((gated || (d->flags & DM_READONLY_FLAG)) && loaded); resumed++; d->dev=(253U<<8)|3;
        return bad==12 ? -1 : 0;
    }
    if (op==DM_DEV_REMOVE) { removed++; return 0; }
    assert(0); return -1;
}
static void put32(unsigned off, uint32_t value)
{ for(unsigned i=0;i<4;i++) disk_super[off+i]=value>>(8*i); }
static void setup(void)
{
    memset(disk_super,0,sizeof(disk_super)); memset(gate_text,0,sizeof(gate_text));
    registered[0]=0; raw_reads=0;
    put32(4,16384); put32(56,0x1ef53); put32(76,1); put32(88,128);
    put32(96,2); put32(100,3); memcpy(disk_super+120,"GAIJIN-STATE",12);
    calls=gated=loaded=resumed=removed=configured=nmap=bad=fragment=0;
}
int main(void)
{
    char *probe[]={"gaijin-state-map","probe",NULL};
    setup();assert(state_main(2,probe)==0 && !gated && !configured);
    disk_super[58]=0;
    assert(state_main(2,probe)==0 && !gated && !configured);
    char *token[]={"gaijin-state-map","reset-token",NULL};
    assert(state_main(2,token)==0 && !gated && !configured);
    char *ro[]={"gaijin-state-map","map-ro",NULL};
    setup();assert(state_main(2,ro)==0 && !gated && configured && resumed);
    char *flush[]={"gaijin-state-map","flush",NULL};
    setup(); assert(state_main(2,flush)==0 && !gated && !configured && !removed);
    for (int failure=16;failure<=18;failure++) {
        setup();bad=failure;assert(state_main(2,flush)==1 && !gated && !configured && !removed);
    }
    char *verify[]={"gaijin-state-map","verify-clean",NULL};
    setup();assert(state_main(2,verify)==0 && raw_reads==2 && !gated && !configured && !removed);
    disk_super[58]=0; /* Refuse reboot if unmount did not persist a clean header. */
    assert(state_main(2,verify)==1 && raw_reads==4 && !gated && !configured && !removed);
    char *argv[]={"gaijin-state-map",NULL};
    setup(); assert(state_main(1,argv)==0);
    assert(gated==1 && resumed==1 && nmap==32768 && !removed);
    assert(!strcmp(gate_text,"4159 32768\n"));
    /* A clean stop/start repeats the identical gate; raw header reads stay fresh. */
    char *stop[]={"gaijin-state-map","stop",NULL};
    assert(state_main(2,stop)==0 && removed==1);
    calls=gated=loaded=resumed=removed=configured=nmap=0;
    assert(state_main(1,argv)==0 && gated==1 && resumed==1 && raw_reads==4);
    assert(state_main(2,stop)==0);
    calls=gated=loaded=resumed=removed=configured=nmap=0;
    disk_super[58]=0; /* Dirty raw image must be rejected despite prior clean reads. */
    assert(state_main(1,argv)==1 && !gated && !configured && !resumed);
    setup(); fragment=1; assert(state_main(1,argv)==0);
    assert(!strcmp(gate_text,"4159 16384\n53311 16384\n"));
    setup(); fragment=2; /* Split allocation across the two superblock sectors. */
    assert(state_main(1,argv)==0 && raw_reads==2);
    assert(!strcmp(gate_text,"4159 3\n36930 32765\n"));
    for(int failure=1;failure<=15;failure++) {
        setup(); bad=failure; assert(state_main(1,argv)==1);
        if (failure<10 || failure>=13) assert(!gated && !resumed);
        if (failure==10 || failure==11) assert(removed && !resumed);
        if (failure==12) assert(removed && gated);
        if (failure==13) assert(removed);
    }
    /* Reject dirty state and each unsupported superblock feature before any ioctl. */
    unsigned offsets[]={58,24,76,88,92,96,100,120,4};
    for(unsigned i=0;i<sizeof(offsets)/sizeof(offsets[0]);i++) {
        setup(); disk_super[offsets[i]]^=4;
        assert(state_main(1,argv)==1 && !configured && !gated && raw_reads==2);
    }
    setup();
    assert(state_main(2,stop)==0 && removed==1);
    return 0;
}
