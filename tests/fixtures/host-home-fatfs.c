// SPDX-License-Identifier: MIT
/* The actual home.c and ESP-IDF FatFs run on a disposable regular-file disk.
 * Only ESP hardware/FDT/display and the ROM inflater are replaced on the host. */
#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <zlib.h>
#include "ff.h"
#include "diskio.h"
#include "home_store.h"
#include "sha256.h"
#include "home_seed.h"

static int diskfd;
static char boot_status[32];
PARTITION VolToPart[FF_VOLUMES]={{0,0}};
DSTATUS disk_initialize(BYTE drive) {(void)drive;return 0;}
DSTATUS disk_status(BYTE drive) {(void)drive;return 0;}
DRESULT disk_read(BYTE drive,BYTE *buffer,LBA_t lba,UINT count)
{
    (void)drive;size_t bytes=(size_t)count*512;
    return pread(diskfd,buffer,bytes,(off_t)lba*512)==(ssize_t)bytes?RES_OK:RES_ERROR;
}
DRESULT disk_write(BYTE drive,const BYTE *buffer,LBA_t lba,UINT count)
{
    (void)drive;size_t bytes=(size_t)count*512;
    return pwrite(diskfd,buffer,bytes,(off_t)lba*512)==(ssize_t)bytes?RES_OK:RES_ERROR;
}
DRESULT disk_ioctl(BYTE drive,BYTE command,void *value)
{
    (void)drive;
    switch(command) {
        case CTRL_SYNC:return fsync(diskfd)?RES_ERROR:RES_OK;
        case GET_SECTOR_SIZE:*(WORD *)value=512;return RES_OK;
        case GET_SECTOR_COUNT:*(DWORD *)value=2U*1024U*1024U*1024U/512;return RES_OK;
        case GET_BLOCK_SIZE:*(DWORD *)value=1;return RES_OK;
        default:return RES_PARERR;
    }
}
DWORD get_fattime(void) {return (46U<<25)|(11U<<21)|(9U<<16);}
void *ff_memalloc(unsigned bytes) {void *p=malloc(bytes);if(p) memset(p,0xa5,bytes);return p;}
void ff_memfree(void *p) {free(p);}
int ff_mutex_create(int n) {(void)n;return 1;}
void ff_mutex_delete(int n) {(void)n;}
int ff_mutex_take(int n) {(void)n;return 1;}
void ff_mutex_give(int n) {(void)n;}

typedef struct {struct {unsigned sector_size;} csd;unsigned long raw_cid[4];} sdmmc_card_t;
static sdmmc_card_t card={.csd={512}};
static sdmmc_card_t *gaijin_sd_card(void) {return &card;}
static int sdmmc_read_sectors(sdmmc_card_t *c,void *p,LBA_t lba,UINT count)
{(void)c;return disk_read(0,p,lba,count)==RES_OK?0:-1;}
#define ESP_OK 0
#define MALLOC_CAP_INTERNAL 0
#define MALLOC_CAP_8BIT 0
static void *heap_caps_malloc(size_t n,int caps) {(void)caps;return malloc(n);}
static void *heap_caps_calloc(size_t n,size_t size,int caps) {(void)caps;return calloc(n,size);}
#define heap_caps_free free
static void host_log(const char *tag,const char *format,...)
{va_list ap;(void)tag;va_start(ap,format);vfprintf(stderr,format,ap);fputc('\n',stderr);va_end(ap);}
#define ESP_LOGW host_log
#define ESP_LOGI host_log
static int fdt_path_offset(const void *dtb,const char *path) {(void)dtb;(void)path;return 1;}
static const char *fdt_getprop(const void *dtb,int node,const char *name,int *length)
{(void)dtb;(void)node;(void)name;(void)length;return "";}
static int fdt_setprop_string(void *dtb,int node,const char *name,const char *value)
{(void)dtb;(void)node;if(!strcmp(name,"gaijin,home-status")) snprintf(boot_status,sizeof(boot_status),"%s",value);return 0;}
static int fdt_setprop_u32(void *dtb,int node,const char *name,unsigned value)
{(void)dtb;(void)node;(void)name;(void)value;return 0;}
static void gaijin_display_progress_start(void) {}
static void gaijin_display_progress(unsigned n) {(void)n;}
static int gaijin_display_progress_stop(void) {return 0;}

/* zlib is an independent host adapter, not an emulation of the P4 ROM. */
#define TINFL_LZ_DICT_SIZE 32768
#define TINFL_FLAG_PARSE_ZLIB_HEADER 1
#define TINFL_STATUS_DONE 0
#define TINFL_STATUS_HAS_MORE_OUTPUT 1
typedef int tinfl_status;
typedef struct {z_stream stream;int started;} tinfl_decompressor;
static void tinfl_init(tinfl_decompressor *d) {memset(d,0,sizeof(*d));}
static tinfl_status tinfl_decompress(tinfl_decompressor *d,const unsigned char *in,size_t *in_bytes,
                                    unsigned char *start,unsigned char *out,size_t *out_bytes,int flags)
{
    (void)start;(void)flags;
    if(!d->started) {if(inflateInit(&d->stream)!=Z_OK) return -1;d->started=1;}
    d->stream.next_in=(Bytef *)in;d->stream.avail_in=(uInt)*in_bytes;
    d->stream.next_out=out;d->stream.avail_out=(uInt)*out_bytes;
    int result=inflate(&d->stream,Z_NO_FLUSH);
    *in_bytes-=d->stream.avail_in;*out_bytes-=d->stream.avail_out;
    if(result==Z_STREAM_END) {inflateEnd(&d->stream);d->started=0;return TINFL_STATUS_DONE;}
    return result==Z_OK && !d->stream.avail_out?TINFL_STATUS_HAS_MORE_OUTPUT:-1;
}

/* HOME_IMPLEMENTATION */

static int reset_request(char token[128])
{
    FIL file={0};UINT n;unsigned char super[1024];char hash[65];
    if(f_open(&file,"0:/gaijin/" GH_IMAGE,FA_READ|FA_WRITE)!=FR_OK) return -1;
    unsigned long bytes=f_size(&file);
    if(f_lseek(&file,1024)!=FR_OK || f_read(&file,super,sizeof(super),&n)!=FR_OK || n!=sizeof(super)) return -1;
    super[58]=super[59]=0;
    if(f_lseek(&file,1024)!=FR_OK || f_write(&file,super,sizeof(super),&n)!=FR_OK || n!=sizeof(super) || f_close(&file)!=FR_OK) return -1;
    gh_sha_hex(super,sizeof(super),hash);
    snprintf(token,128,GH_RESET_PREFIX "%032u:%08lx:%s",0U,bytes,hash);
    return 0;
}
int main(int argc,char **argv)
{
    if(argc!=3) return 2;
    diskfd=open(argv[1],O_RDWR|O_CLOEXEC);
    struct stat st;
    if(diskfd<0 || fstat(diskfd,&st) || !S_ISREG(st.st_mode) || st.st_size!=(off_t)2*1024*1024*1024) return 3;
    FATFS filesystem={0};
    if(f_mount(&filesystem,"0:",1)!=FR_OK) return 4;
    char token[128]="";
    if(!strcmp(argv[2],"reset") && reset_request(token)) return 5;
    if(gaijin_home_prepare(NULL,-1,token)) return 6;
    printf("STATUS:%s\n",boot_status);
    FIL file={0};UINT count;unsigned char buffer[32768];char hash[65];struct gh_sha256 sha;
    if(f_open(&file,"0:/gaijin/" GH_IMAGE,FA_READ)!=FR_OK) return 7;
    gh_sha_init(&sha);
    do {
        if(f_read(&file,buffer,sizeof(buffer),&count)!=FR_OK) return 8;
        gh_sha_update(&sha,buffer,count);
    } while(count);
    unsigned char digest[32];gh_sha_final(&sha,digest);
    for(unsigned i=0;i<32;i++) snprintf(hash+i*2,3,"%02x",digest[i]);
    if(f_close(&file)!=FR_OK) return 9;
    printf("HASH:%s\n",hash);
    FILINFO info;
    if(f_stat("0:/gaijin/" GH_TRANSACTION,&info)!=FR_NO_FILE || f_stat("0:/gaijin/" GH_TEMP,&info)!=FR_NO_FILE) return 10;
    if(f_mount(NULL,"0:",0)!=FR_OK || close(diskfd)) return 11;
    return 0;
}
