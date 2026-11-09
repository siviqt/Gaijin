// SPDX-License-Identifier: MIT
#include "home.h"
#include "sd.h"
#include "sd_card.h"
#include "boot_contract.h"
#include "home_store.h"
#include "sha256.h"
#include "home_seed.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "ff.h"
#include "diskio.h"
#include "miniz.h"
#include <libfdt.h>
#include <stdio.h>
#include <string.h>

static const char *TAG = "Gaijin home";
static unsigned char sector[512] __attribute__((aligned(64)));
struct home_context { FATFS *fs; char cid[33]; };

int gaijin_home_boot_selection(const void *memory, size_t bytes, char argument[128])
{
    const unsigned char *p=memory;
    unsigned long long magic;
    argument[0]=0;
    if(bytes < GH_APPFS_ARG_OFFSET+GH_APPFS_ARG_BYTES) return -1;
    magic=gh_le32(p)|((unsigned long long)gh_le32(p+4)<<32);
    if(magic!=GH_APPFS_MAGIC || gh_le32(p+8)>254) return -1;
    if(memchr(p+GH_APPFS_ARG_OFFSET,0,GH_APPFS_ARG_BYTES))
        memcpy(argument,p+GH_APPFS_ARG_OFFSET,GH_APPFS_ARG_BYTES);
    else return -1;
    return (int)gh_le32(p+8);
}

static int path_for(char out[96],const char *name)
{
    int n=snprintf(out,96,"0:/gaijin/%s",name);
    return n>0 && n<96?0:-1;
}
static int home_exists(void *ctx,const char *name)
{
    (void)ctx;char path[96];FILINFO info;
    if(path_for(path,name)) return -1;
    FRESULT r=f_stat(path,&info);
    if(r==FR_NO_FILE || r==FR_NO_PATH) return 0;
    if(r!=FR_OK || (info.fattrib & AM_DIR)) {
        ESP_LOGW(TAG,"Cannot inspect %s: FatFs=%d attributes=0x%02x",name,r,r==FR_OK?info.fattrib:0);
        return -1;
    }
    return 1;
}
static int home_sync(struct home_context *ctx)
{ return disk_ioctl(ctx->fs->pdrv,CTRL_SYNC,NULL)==RES_OK?0:-1; }

/* A clean flag is not a full FAT scan. Reject known dirty/error state and
 * inconsistent copies, never attempt to repair this shared card. */
static int volume_check(struct home_context *ctx)
{
    DWORD free_clusters;
    FRESULT result=f_getfree("0:",&free_clusters,&ctx->fs);
    if(result!=FR_OK) {ESP_LOGW(TAG,"FAT free-space query failed: FatFs=%d",result);return -1;}
    FATFS *fs=ctx->fs;
    sdmmc_card_t *card=gaijin_sd_card();
    if(!card || card->csd.sector_size!=512 || (fs->fs_type!=FS_FAT16 && fs->fs_type!=FS_FAT32) ||
        !fs->csize || fs->csize>128 || !fs->n_fats || fs->n_fats>2) {
        ESP_LOGW(TAG,"Unsupported FAT/card geometry for home creation");return -1;
    }
    if(sdmmc_read_sectors(card,sector,fs->volbase,1)!=ESP_OK) {
        ESP_LOGW(TAG,"Cannot read FAT boot sector at LBA %lu",(unsigned long)fs->volbase);return -1;
    }
    if(sector[510]!=0x55 || sector[511]!=0xaa || gh_le16(sector+11)!=512) {
        ESP_LOGW(TAG,"Invalid FAT boot-sector signature or sector size");return -1;
    }
    unsigned state=sector[fs->fs_type==FS_FAT32?65:37];
    if(state&1) {
        ESP_LOGW(TAG,"FAT boot-sector dirty flag is set (0x%02x); check SD offline and safely unmount it",state);
        return -1;
    }
    if(fs->fs_type==FS_FAT32) {
        if(gh_le16(sector+40)&0x80) {ESP_LOGW(TAG,"FAT32 mirroring is disabled");return -1;}
    }
    unsigned int first=0;
    for(unsigned i=0;i<fs->n_fats;i++) {
        if(sdmmc_read_sectors(card,sector,fs->fatbase+(LBA_t)i*fs->fsize,1)!=ESP_OK) {
            ESP_LOGW(TAG,"Cannot read FAT copy %u",i);return -1;
        }
        unsigned int entry=fs->fs_type==FS_FAT32?gh_le32(sector+4):gh_le16(sector+2);
        unsigned int flags=fs->fs_type==FS_FAT32?0x0c000000U:0xc000U;
        /* FAT32's high nibble is reserved; it is not a consistency flag. */
        if(fs->fs_type==FS_FAT32) entry&=0x0fffffffU;
        if((entry&flags)!=flags) {
            ESP_LOGW(TAG,"FAT copy %u FAT[1]=0x%08x reports dirty/error state; check SD offline",i,entry);
            return -1;
        }
        if(i && entry!=first) {ESP_LOGW(TAG,"FAT[1] differs between copies");return -1;}
        first=entry;
    }
    snprintf(ctx->cid,sizeof(ctx->cid),"%08lx%08lx%08lx%08lx",
        (unsigned long)card->raw_cid[3],(unsigned long)card->raw_cid[2],
        (unsigned long)card->raw_cid[1],(unsigned long)card->raw_cid[0]);
    return 0;
}
static int home_eligible(void *context)
{
    struct home_context *ctx=context;DWORD clusters;FATFS *fs;FILINFO info;
    if(volume_check(ctx) || f_getfree("0:",&clusters,&fs)!=FR_OK) return -1;
    unsigned long long available=(unsigned long long)clusters*fs->csize*512;
    if(available < (unsigned long long)GH_FACTORY_BYTES+GH_RESERVE_BYTES) return 0;
    FRESULT r=f_stat("0:/gaijin",&info);
    if(r==FR_NO_FILE || r==FR_NO_PATH) {
        if(f_mkdir("0:/gaijin")!=FR_OK || home_sync(ctx)) return -1;
    } else if(r!=FR_OK || !(info.fattrib&AM_DIR) || (info.fattrib&AM_RDO)) return -1;
    return 1;
}
static int home_matches_dirty(void *context,const char *request)
{
    struct home_context *ctx=context;FIL file={0};UINT count;unsigned char sb[1024];
    char token[128],digest[65];
    if(!gh_reset_valid(request) || volume_check(ctx)) return 0;
    if(f_open(&file,"0:/gaijin/" GH_IMAGE,FA_READ)!=FR_OK) return -1;
    unsigned long long bytes=f_size(&file);
    int ok=f_lseek(&file,1024)==FR_OK && f_read(&file,sb,sizeof(sb),&count)==FR_OK && count==sizeof(sb);
    if(f_close(&file)!=FR_OK) ok=0;
    if(!ok || !gh_format(sb,bytes) || gh_le16(sb+58)!=0) return 0;
    gh_sha_hex(sb,sizeof(sb),digest);
    snprintf(token,sizeof(token),GH_RESET_PREFIX "%s:%08lx:%s",ctx->cid,(unsigned long)bytes,digest);
    return !strcmp(token,request);
}
static int home_mark(void *context,int reset)
{
    /* ESP-IDF's FF_USE_DYN_BUFFER reads FIL.buf before allocating its cache. */
    struct home_context *ctx=context;FIL file={0};UINT count;
    const char *text=reset?"gaijin-reset-v1\n":"gaijin-create-v1\n";
    if(f_open(&file,"0:/gaijin/" GH_TRANSACTION,FA_WRITE|FA_CREATE_NEW)!=FR_OK) return -1;
    int ok=f_write(&file,text,strlen(text),&count)==FR_OK && count==strlen(text) && f_sync(&file)==FR_OK;
    if(f_close(&file)!=FR_OK) ok=0;
    return ok && !home_sync(ctx)?0:-1;
}
static int home_create_verified(void *context)
{
    struct home_context *ctx=context;
    tinfl_decompressor *decoder=heap_caps_calloc(1,sizeof(*decoder),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    unsigned char *window=heap_caps_malloc(TINFL_LZ_DICT_SIZE,MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    FIL file={0};int opened=0,ok=0;size_t input=0;unsigned int output=0;unsigned char digest[32];
    struct gh_sha256 hash;
    if(!decoder || !window) goto out;
    if(f_open(&file,"0:/gaijin/" GH_TEMP,FA_WRITE|FA_READ|FA_CREATE_NEW)!=FR_OK) goto out;
    opened=1;
    if(f_expand(&file,GH_FACTORY_BYTES,1)!=FR_OK) goto out;
    tinfl_init(decoder);gh_sha_init(&hash);gaijin_display_progress_start();
    for(;;) {
        size_t in=sizeof(gh_seed_zlib)-input,out_bytes=TINFL_LZ_DICT_SIZE;
        tinfl_status status=tinfl_decompress(decoder,gh_seed_zlib+input,&in,window,window,&out_bytes,TINFL_FLAG_PARSE_ZLIB_HEADER);
        input+=in;
        if(out_bytes>GH_FACTORY_BYTES-output) goto out;
        UINT written=0;
        if(f_write(&file,window,out_bytes,&written)!=FR_OK || written!=out_bytes) goto out;
        gh_sha_update(&hash,window,out_bytes);output+=out_bytes;
        gaijin_display_progress((unsigned)((unsigned long long)output*50/GH_FACTORY_BYTES));
        if(status==TINFL_STATUS_DONE) break;
        if(status!=TINFL_STATUS_HAS_MORE_OUTPUT || out_bytes!=TINFL_LZ_DICT_SIZE || (!in && !out_bytes)) goto out;
    }
    gh_sha_final(&hash,digest);
    if(output!=GH_FACTORY_BYTES || input!=sizeof(gh_seed_zlib) || memcmp(digest,gh_seed_hash,32) ||
        f_sync(&file)!=FR_OK) goto out;
    if(f_close(&file)!=FR_OK) { opened=0;goto out; }
    opened=0;
    if(home_sync(ctx) || f_open(&file,"0:/gaijin/" GH_TEMP,FA_READ)!=FR_OK) goto out;
    opened=1;
    if(f_size(&file)!=GH_FACTORY_BYTES) goto out;
    gh_sha_init(&hash);output=0;
    while(output<GH_FACTORY_BYTES) {
        UINT count=0;
        if(f_read(&file,window,TINFL_LZ_DICT_SIZE,&count)!=FR_OK || count!=TINFL_LZ_DICT_SIZE) goto out;
        gh_sha_update(&hash,window,count);output+=count;
        gaijin_display_progress(50+(unsigned)((unsigned long long)output*50/GH_FACTORY_BYTES));
    }
    gh_sha_final(&hash,digest);
    ok=!memcmp(digest,gh_seed_hash,32);
out:
    if(opened && f_close(&file)!=FR_OK) ok=0;
    gaijin_display_progress_stop();
    heap_caps_free(window);heap_caps_free(decoder);
    return ok?0:-1;
}
static int home_remove(void *context,const char *name)
{
    char path[96];struct home_context *ctx=context;
    return !path_for(path,name) && f_unlink(path)==FR_OK && !home_sync(ctx)?0:-1;
}
static int home_rename(void *context,const char *from,const char *to)
{
    char a[96],b[96];struct home_context *ctx=context;
    return !path_for(a,from) && !path_for(b,to) && f_rename(a,b)==FR_OK && !home_sync(ctx)?0:-1;
}
int gaijin_home_prepare(void *dtb,int appfs_fd,const char *argument)
{
    const struct gh_store_ops ops={home_exists,home_eligible,home_matches_dirty,home_mark,
        home_create_verified,home_remove,home_rename};
    struct home_context ctx={0};const char *status;
    const char *reset=argument && !strncmp(argument,"ghr",3)?argument:NULL;
    int clean=home_exists(&ctx,"clean-start");
    int chosen=fdt_path_offset(dtb,"/chosen");
    const char *args=fdt_getprop(dtb,chosen,"bootargs",NULL);
    int clean_args=args && (strstr(args," gaijin.clean") || strstr(args," gaijin.rescue"));
    if(clean>0 || clean_args) status="skipped";
    else if(clean<0 || volume_check(&ctx)) status="fat-error";
    else status=gh_result_name(gh_store_run(&ops,&ctx,reset));
    ESP_LOGI(TAG,"%s: %s",GH_SEED_ID,status);
    int node=fdt_path_offset(dtb,"/chosen");
    if(node<0 || fdt_setprop_string(dtb,node,"gaijin,home-status",status)) return -1;
    node=fdt_path_offset(dtb,"/soc/power-controller@5010800c");
    if(node<0) return -1;
    if(appfs_fd>=0 && fdt_setprop_u32(dtb,node,"gaijin,appfs-fd",appfs_fd)) return -1;
    return 0;
}
