// SPDX-License-Identifier: MIT
#include "home_store.h"
enum gh_result gh_store_run(const struct gh_store_ops *o, void *ctx, const char *reset)
{
    int transaction=o->exists(ctx,GH_TRANSACTION), temp=o->exists(ctx,GH_TEMP);
    int exists=o->exists(ctx,GH_IMAGE);
    if(transaction<0 || temp<0 || exists<0) return GH_IO_ERROR;
    if(transaction || temp) return GH_INCOMPLETE;
    if(reset && (!gh_reset_valid(reset) || !exists || o->matches_dirty(ctx,reset)!=1))
        return GH_RESET_REFUSED;
    if(exists && !reset) return GH_EXISTING;
    int eligible=o->eligible(ctx);
    if(eligible<0) return GH_IO_ERROR;
    if(!eligible) return GH_NO_SPACE;
    if(o->mark(ctx,!!reset)) return GH_INCOMPLETE;
    if(o->create_verified(ctx)) return GH_INCOMPLETE;
    /* Recheck identity immediately before the first destructive operation. */
    if(reset && o->matches_dirty(ctx,reset)!=1) return GH_RESET_REFUSED;
    if(reset && o->remove(ctx,GH_IMAGE)) return GH_INCOMPLETE;
    if(o->rename(ctx,GH_TEMP,GH_IMAGE)) return GH_INCOMPLETE;
    if(o->remove(ctx,GH_TRANSACTION)) return GH_INCOMPLETE;
    return reset?GH_RECREATED:GH_CREATED;
}
