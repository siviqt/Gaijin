// SPDX-License-Identifier: MIT
/* Execute actual loader progress code with single-core interrupt injection. */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#define IRAM_ATTR
#define DRAM_ATTR
#define ESP_OK 0
#define ESP_ERR_INVALID_ARG 1
#define ESP_FAIL 2
#define ESP_CACHE_MSYNC_FLAG_DIR_M2C 1
#define ESP_CACHE_MSYNC_FLAG_DIR_C2M 2
#define ESP_INTR_FLAG_IRAM 0x400
#define ESP_INTR_FLAG_LEVEL1 2
#define ESP_INTR_FLAG_INTRDISABLED 0x800
#define ETS_DW_GDMA_INTR_SOURCE 24
#define DW_GDMA_LL_CHANNEL_EVENT_DMA_TFR_DONE 2
#define MIPI_DSI_BRG_LL_EVENT_UNDERRUN 1
#define M5_FB_BYTES 768000U
#define M5_FB_RESERVE 0xbc000U
typedef int esp_err_t;
typedef int portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED 0
static unsigned critical_depth;
#define portENTER_CRITICAL(p) ((void)(p),critical_depth++)
#define portEXIT_CRITICAL(p) ((void)(p),critical_depth--)
#define pdMS_TO_TICKS(ms) ((ms)/10)
static struct {
    uint32_t before[16];
    unsigned char bytes[M5_FB_RESERVE];
    uint32_t after[16];
} memory __attribute__((aligned(64)));
#define M5_FB_BASE ((uintptr_t)memory.bytes)
#define M5_FB_DESC (M5_FB_BASE+M5_FB_BYTES)
typedef struct { uint32_t words[16]; } __attribute__((aligned(64))) dw_gdma_link_list_item_t;
static struct {
    struct { unsigned val; } chen0;
    uint32_t status, generation, propagation;
    uintptr_t head;
} DW_GDMA;
static struct { struct {unsigned val;} int_raw; bool enabled; } bridge;
#define MIPI_DSI_LL_GET_BRG(n) ((void)(n),&bridge)
struct host_irq {void (*handler)(void *);void *arg;bool enabled;};
typedef struct host_irq *intr_handle_t;
static struct host_irq irq;
static bool allocated;
static int64_t clock_us, next_frame_us;
static unsigned cache_calls, cache_largest, cache_bad, descriptor_reads, descriptor_writes;
static unsigned dma_starts, allocs, frees, logs, flags_used, bad_start_order, bad_source;
static unsigned fail_cache_call, fail_alloc, fail_enable, fail_disable, fail_free, stuck;
static unsigned inject_cache, inject_stop, auto_frames, first_dirty_frames;
static char last_log[512];
static bool progress_ready;
static const char *TAG="display";

static void host_complete(uint32_t status);
static int64_t esp_timer_get_time(void) {return clock_us;}
static void vTaskDelay(unsigned ticks)
{
    int64_t end=clock_us+(int64_t)ticks*10000;
    while(auto_frames && allocated && irq.enabled && bridge.enabled && next_frame_us<=end) {
        clock_us=next_frame_us;next_frame_us+=16677;
        host_complete(2);
    }
    clock_us=end;
}
static void host_log(const char *tag,const char *fmt,...)
{(void)tag;logs++;va_list ap;va_start(ap,fmt);vsnprintf(last_log,sizeof(last_log),fmt,ap);va_end(ap);}
#define ESP_LOGI host_log
static esp_err_t esp_cache_msync(void *address,size_t bytes,int direction)
{
    uintptr_t at=(uintptr_t)address;
    cache_calls++;
    if(bytes>cache_largest) cache_largest=(unsigned)bytes;
    if((at&63) || (bytes&63) || at<M5_FB_BASE || at+bytes>M5_FB_BASE+M5_FB_RESERVE || bytes>4096) cache_bad++;
    if(at==M5_FB_DESC) {
        if(direction==ESP_CACHE_MSYNC_FLAG_DIR_M2C) descriptor_reads++;
        else descriptor_writes++;
    }
    if(fail_cache_call==cache_calls) return ESP_FAIL;
    if(inject_cache && at!=M5_FB_DESC && allocated && irq.enabled && !critical_depth) {
        inject_cache=0;host_complete(2);
    }
    return ESP_OK;
}
static void dw_gdma_ll_lli_set_block_markers(void *desc,bool event,bool last,bool valid)
{uint32_t *p=desc;(void)event;p[9]=(p[9]&0x3fffffffU)|(last?0x40000000U:0)|(valid?0x80000000U:0);}
static void dw_gdma_ll_channel_enable_intr_propagation(void *dev,unsigned channel,uint32_t mask,bool enable)
{(void)dev;(void)channel;if(enable) DW_GDMA.propagation|=mask;else DW_GDMA.propagation&=~mask;}
static void dw_gdma_ll_channel_enable_intr_generation(void *dev,unsigned channel,uint32_t mask,bool enable)
{(void)dev;(void)channel;if(enable) DW_GDMA.generation|=mask;else DW_GDMA.generation&=~mask;}
static uint32_t dw_gdma_ll_channel_get_intr_status(void *dev,unsigned channel)
{(void)dev;(void)channel;return DW_GDMA.status;}
static void dw_gdma_ll_channel_clear_intr(void *dev,unsigned channel,uint32_t mask)
{(void)dev;(void)channel;DW_GDMA.status&=~mask;}
static void dw_gdma_ll_channel_set_link_list_head_addr(void *dev,unsigned channel,uintptr_t address)
{(void)dev;(void)channel;DW_GDMA.head=address;}
static void dw_gdma_ll_channel_enable(void *dev,unsigned channel,bool enable)
{(void)dev;(void)channel;if(enable){dma_starts++;DW_GDMA.chen0.val=1;}else if(!stuck) DW_GDMA.chen0.val=0;}
static void mipi_dsi_brg_ll_enable_dpi_output(void *dev,bool enable)
{(void)dev;if(enable && !DW_GDMA.chen0.val) bad_start_order++;bridge.enabled=enable;}
static void mipi_dsi_brg_ll_update_dpi_config(void *dev) {(void)dev;}
static void mipi_dsi_brg_ll_clear_interrupt_status(void *dev,uint32_t mask)
{(void)dev;bridge.int_raw.val&=~mask;}
static esp_err_t esp_intr_alloc(int source,int flags,void (*handler)(void *),void *arg,intr_handle_t *out)
{
    allocs++;flags_used=(unsigned)flags;if(source!=24) bad_source++;
    if(fail_alloc) return ESP_FAIL;
    allocated=true;irq=(struct host_irq){.handler=handler,.arg=arg};*out=&irq;return ESP_OK;
}
static esp_err_t esp_intr_enable(intr_handle_t handle)
{if(fail_enable) return ESP_FAIL;handle->enabled=true;return ESP_OK;}
static esp_err_t esp_intr_disable(intr_handle_t handle)
{
    if(inject_stop) {inject_stop=0;DW_GDMA.status=2;handle->handler(handle->arg);}
    if(fail_disable) return ESP_FAIL;
    handle->enabled=false;return ESP_OK;
}
static esp_err_t esp_intr_free(intr_handle_t handle)
{frees++;if(fail_free){fail_free--;return ESP_FAIL;}handle->enabled=false;allocated=false;return ESP_OK;}
int gaijin_display_progress_stop(void);

/* PROGRESS_IMPLEMENTATION */

static void host_complete(uint32_t status)
{
    DW_GDMA.chen0.val=0;DW_GDMA.status=status;
    ((uint32_t *)M5_FB_DESC)[9]&=0x7fffffffU; /* DMA clears ownership in memory. */
    if(allocated && irq.enabled && (DW_GDMA.generation&DW_GDMA.propagation&status)) irq.handler(irq.arg);
}
void mock_reset(void)
{
    memset(&progress,0,sizeof(progress));progress_irq=NULL;progress_ready=true;
    memset(&DW_GDMA,0,sizeof(DW_GDMA));memset(&bridge,0,sizeof(bridge));memset(&memory,0,sizeof(memory));
    for(unsigned n=0;n<16;n++) {memory.before[n]=0xdeadbeef;memory.after[n]=0xdeadbeef;}
    uint32_t *desc=(void *)M5_FB_DESC;
    desc[0]=0x48e00000;desc[2]=0x50105000;desc[4]=95999;desc[9]=0xc0000000;
    allocated=false;memset(&irq,0,sizeof(irq));clock_us=1000000;next_frame_us=clock_us+16677;
    critical_depth=cache_calls=cache_largest=cache_bad=descriptor_reads=descriptor_writes=0;
    dma_starts=allocs=frees=logs=flags_used=bad_start_order=bad_source=0;
    fail_cache_call=fail_alloc=fail_enable=fail_disable=fail_free=stuck=0;
    inject_cache=inject_stop=auto_frames=first_dirty_frames=0;last_log[0]=0;
}
void mock_start(void) {gaijin_display_progress_start();}
int mock_stop(void) {return gaijin_display_progress_stop();}
void mock_test(void) {auto_frames=1;gaijin_display_progress_test();}
void mock_frame(uint32_t status,int64_t now,unsigned underrun)
{clock_us=now;bridge.int_raw.val|=underrun;host_complete(status);}
void mock_draw(unsigned percent,int64_t now)
{clock_us=now;gaijin_display_progress(percent);}
void mock_corrupt_descriptor(void) {((uint32_t *)M5_FB_DESC)[0]=0;}
unsigned mock_pixel(unsigned x,unsigned y) {return ((uint16_t *)M5_FB_BASE)[x*480+(479-y)];}
void mock_fault(const char *key,unsigned value)
{
#define SET(k,v) if(!strcmp(key,k)){v=value;return;}
    SET("cache-call",fail_cache_call) SET("alloc",fail_alloc) SET("enable",fail_enable)
    SET("disable",fail_disable) SET("free",fail_free) SET("stuck",stuck)
    SET("inject-cache",inject_cache) SET("inject-stop",inject_stop)
    SET("ready",progress_ready)
#undef SET
}
uint64_t mock_stat(const char *key)
{
#define GET(k,v) if(!strcmp(key,k)) return (uint64_t)(v);
    GET("running",progress.running) GET("started",progress.started) GET("fault",progress.fault)
    GET("frames",progress.frames) GET("dma-errors",progress.dma_errors) GET("cache-errors",progress.cache_errors)
    GET("underruns",progress.underruns) GET("max-frame-us",progress.max_frame_us)
    GET("draws",progress.draws) GET("sync-bytes",progress.sync_bytes) GET("percent",progress.percent)
    GET("cache-calls",cache_calls) GET("largest-cache",cache_largest) GET("bad-cache",cache_bad)
    GET("dma-starts",dma_starts) GET("allocs",allocs) GET("frees",frees) GET("irq",allocated)
    GET("generation",DW_GDMA.generation) GET("propagation",DW_GDMA.propagation)
    GET("dpi",bridge.enabled) GET("dma",DW_GDMA.chen0.val) GET("pending",DW_GDMA.status)
    GET("logs",logs) GET("flags",flags_used) GET("bad-start-order",bad_start_order)
    GET("clock",clock_us) GET("descriptor-reads",descriptor_reads) GET("descriptor-writes",descriptor_writes)
    GET("bad-source",bad_source)
    GET("descriptor-ok",((uint32_t *)M5_FB_DESC)[0]==0x48e00000 && ((uint32_t *)M5_FB_DESC)[9]==0xc0000000)
    if(!strcmp(key,"guards")) {
        for(unsigned n=0;n<16;n++) if(memory.before[n]!=0xdeadbeef || memory.after[n]!=0xdeadbeef) return 0;
        return 1;
    }
#undef GET
    return UINT64_MAX;
}
const char *mock_log(void) {return last_log;}
