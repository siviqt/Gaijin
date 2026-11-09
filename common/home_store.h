// SPDX-License-Identifier: MIT
#ifndef GAIJIN_HOME_STORE_H
#define GAIJIN_HOME_STORE_H
#include "gaijin_home.h"
/* The backend owns all open handles. Every mutating operation must return
 * only after checked writeback/close. No operation replaces a destination. */
struct gh_store_ops {
    int (*exists)(void *, const char *);
    int (*eligible)(void *);
    int (*matches_dirty)(void *, const char *);
    int (*mark)(void *, int);
    int (*create_verified)(void *);
    int (*remove)(void *, const char *);
    int (*rename)(void *, const char *, const char *);
};
enum gh_result { GH_EXISTING, GH_CREATED, GH_RECREATED, GH_SKIPPED,
    GH_INCOMPLETE, GH_NO_SPACE, GH_RESET_REFUSED, GH_IO_ERROR };
static inline const char *gh_result_name(enum gh_result r)
{
    static const char *const names[]={"existing","created","recreated","skipped",
        "incomplete","no-space","reset-refused","io-error"};
    return names[(unsigned)r<sizeof(names)/sizeof(names[0])?(unsigned)r:GH_IO_ERROR];
}
enum gh_result gh_store_run(const struct gh_store_ops *, void *, const char *);
#endif
