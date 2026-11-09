// SPDX-License-Identifier: MIT
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef GAIJIN_FSROOT
#define GAIJIN_FSROOT ""
#endif
#define P(s) GAIJIN_FSROOT s
#define POWER_READY P("/sys/bus/platform/devices/5010800c.power-controller/home_reset_ready")

static int read_text(const char *path,char *out,size_t capacity)
{
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);struct stat st;
    if(fd<0) return -1;
    ssize_t n=-1;char extra;
    if(!fstat(fd,&st) && S_ISREG(st.st_mode)) {
        n=read(fd,out,capacity-1);
        if(n>=0 && read(fd,&extra,1)!=0) n=-1;
    }
    int saved=errno;close(fd);errno=saved;
    if(n<0) return -1;
    out[n]=0;return 0;
}
static int active(void) {return access(P("/run/gaijin-persist"),F_OK)==0;}
static int record(int dir,const char *name,const char *text)
{
    char temporary[64];snprintf(temporary,sizeof(temporary),".%s.%ld",name,(long)getpid());
    int fd=openat(dir,temporary,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
    if(fd<0) return -1;
    size_t len=strlen(text);
    int ok=write(fd,text,len)==(ssize_t)len && !fsync(fd);
    if(close(fd)) ok=0;
    if(ok && !renameat(dir,temporary,dir,name) && !fsync(dir)) return 0;
    (void)unlinkat(dir,temporary,0);return -1;
}
static int settings_dir(void)
{
    struct statfs fs;
    if(!active()) {errno=EROFS;return -1;}
    int fd=open(P("/persist/settings"),O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    if(fd<0) return -1;
    if(fstatfs(fd,&fs) || fs.f_type!=0xef53) {close(fd);errno=EINVAL;return -1;}
    return fd;
}
static int save_prompt(const char *name)
{
    size_t n=strlen(name);
    if(!n || n>32 || strspn(name,"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")!=n) {
        errno=EINVAL;return -1;
    }
    int dir=settings_dir();if(dir<0) return -1;
    char text[34];snprintf(text,sizeof(text),"%s\n",name);
    int rc=record(dir,"prompt",text);close(dir);
    if(!rc) {
        int fd=open(P("/run/gaijin-prompt"),O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW|O_CLOEXEC,0600);
        if(fd<0) return -1;
        int ok=write(fd,text,strlen(text))==(ssize_t)strlen(text);
        if(close(fd)) ok=0;
        if(!ok) return -1;
    }
    return rc;
}
static int complete(void)
{
    int dir=settings_dir();if(dir<0) return -1;
    int rc=record(dir,"setup-version","gaijin-setup-v1\n");
    if(!rc && unlinkat(dir,"setup-pending",0) && errno!=ENOENT) rc=-1;
    if(!rc && fsync(dir)) rc=-1;
    close(dir);return rc;
}
static int run_stop(void)
{
    char *const args[]={P("/sbin/gaijin-persist"),"stop",NULL};
    pid_t pid=vfork();int status;
    if(pid<0) return -1;
    if(!pid) {execv(args[0],args);_exit(127);}
    while(waitpid(pid,&status,0)<0) if(errno!=EINTR) return -1;
    return WIFEXITED(status) && !WEXITSTATUS(status)?0:-1;
}
static int input(char *text,size_t size)
{
    if(!fgets(text,size,stdin)) return -1;
    char *newline=strchr(text,'\n');
    if(!newline) {int c;while((c=getchar())!='\n' && c!=EOF){} return -1;}
    *newline=0;return 0;
}
static void storage_help(const char *state)
{
    if(!strcmp(state,"fat-error"))
        puts("The SD card FAT could not be validated; home creation was blocked.\n"
             "Shut down, check the unmounted card on a host, then safely eject it.\n"
             "The loader log identifies the failed FAT check; Gaijin does not repair FAT.");
    else if(!strcmp(state,"incomplete"))
        puts("Home creation was interrupted. Preserve /gaijin/provision-v1 and\n"
             "/gaijin/userdata.new for offline host inspection before retrying.");
    else if(!strcmp(state,"no-space"))
        puts("Creating home needs a contiguous 32 MiB file plus 16 MiB free headroom.\n"
             "Free space on the host and safely eject the card before retrying.");
}
static void host_help(const char *state)
{
    storage_help(state);
    puts("Home image: /gaijin/userdata.ext2 on the SD card.\n"
         "To keep existing files, shut down Gaijin and recover a copy on a host.\n"
         "Use tools/check_state.py after host repair or migration.\n"
         "Do not remove the SD card while Linux is running.\n"
         "Files in temporary RAM homes disappear when the device restarts.");
}
int main(int argc,char **argv)
{
    int initial=argc==2 && !strcmp(argv[1],"--initial");
    if(argc>2 || (argc==2 && !initial && strcmp(argv[1],"--status"))) return 2;
    char state[32]="unavailable",ready[8]="0",version[32]="";
    (void)read_text(P("/run/gaijin-home-state"),state,sizeof(state));
    state[strcspn(state,"\n")]=0;
    if(argc==2 && !strcmp(argv[1],"--status")) {
        printf("Home storage: %s (%s)\n",active()?"persistent":"temporary RAM",state);return 0;
    }
    if(geteuid()!=0) {fputs("Gaijin setup requires root\n",stderr);return 1;}
    if(initial && !active()) {
        printf("Gaijin home unavailable (%s); using temporary RAM.\n",state);
        storage_help(state);
        puts("Run gaijin-setup for storage status and host instructions.");return 0;
    }
    if(initial && (!read_text(P("/persist/settings/setup-version"),version,sizeof(version)) ||
        access(P("/persist/settings/setup-pending"),F_OK))) return 0;
    if(!isatty(STDIN_FILENO)) {fputs("Run gaijin-setup on the local or USB terminal\n",stderr);return 1;}
    int lock=open(P("/run/gaijin-setup.lock"),O_RDWR|O_CREAT|O_NOFOLLOW|O_CLOEXEC,0600);
    if(lock<0 || flock(lock,LOCK_EX|LOCK_NB)) {fputs("Setup is already open on another console\n",stderr);return 1;}
    (void)read_text(POWER_READY,ready,sizeof(ready));
    int can_reset=!active() && !strcmp(state,"dirty") && !strcmp(ready,"1\n");
    char answer[80];
    for(;;) {
        printf("\nGaijin setup - home: %s (%s)\n",active()?"persistent":"temporary RAM",state);
        if(!active()) storage_help(state);
        struct stat capacity;
        if(active() && !stat(P("/sd/gaijin/userdata.ext2"),&capacity) && S_ISREG(capacity.st_mode))
            printf("Home image capacity: %llu MiB\n",(unsigned long long)capacity.st_size/(1024*1024));
        if(active()) puts("1. Set prompt name");
        puts("2. Continue\n3. Host recovery instructions");
        if(can_reset) puts("4. Erase home and recreate (all files and settings lost)");
        if(initial && active()) puts("5. Use temporary storage for this session");
        puts("6. Shut down to Launcher");
        fputs("Choice [2]: ",stdout);fflush(stdout);
        if(input(answer,sizeof(answer))) {close(lock);return 1;}
        if(!answer[0] || !strcmp(answer,"2")) {
            if(active() && complete()) {perror("Saving setup");continue;}
            close(lock);return 0;
        }
        if(!strcmp(answer,"1")) {
            if(!active()) {puts("Persistent home is unavailable. Preferences cannot be saved.");continue;}
            fputs("Prompt name (1-32 letters, digits, _ . -): ",stdout);fflush(stdout);
            if(input(answer,sizeof(answer)) || save_prompt(answer)) perror("Saving prompt");
            else puts("Prompt saved; it takes effect at the next login.");
        } else if(!strcmp(answer,"3")) host_help(state);
        else if(!strcmp(answer,"4") && can_reset) {
            puts("This permanently deletes all home files and saved settings.\n"
                 "An empty 32 MiB home will replace the current image.\n"
                 "To keep your data, cancel and recover it on a host.");
            fputs("Type ERASE to continue: ",stdout);fflush(stdout);
            if(input(answer,sizeof(answer)) || strcmp(answer,"ERASE")) {puts("Erase cancelled.");continue;}
            execl(P("/sbin/gaijin-power"),"gaijin-power","reset-home","--erase-confirmed",(char *)NULL);
            perror("Starting home reset");
        } else if(!strcmp(answer,"5") && initial && active()) {
            if(chdir("/") || run_stop() ||
                mount("tmpfs",P("/root"),"tmpfs",MS_NOSUID|MS_NODEV,"size=1m,mode=0700") ||
                mount("tmpfs",P("/home"),"tmpfs",MS_NOSUID|MS_NODEV,"size=1m,mode=0755")) {
                perror("Temporary session");continue;
            }
            puts("Temporary RAM homes selected; changes will not survive restart.");
            close(lock);return 0;
        } else if(!strcmp(answer,"6")) {
            execl(P("/sbin/gaijin-power"),"gaijin-power","shutdown",(char *)NULL);
            perror("Starting shutdown");
        } else puts("Invalid choice.");
    }
}
