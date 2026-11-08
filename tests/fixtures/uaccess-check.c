
int main(void) {
    char out[128];
    const uintptr_t bad[]={0,1,0x47ffffff,0x500d2000,UINTPTR_MAX};
    for (unsigned i=0;i<sizeof(bad)/sizeof(bad[0]);i++) {
        assert(!__access_ok((void *)bad[i],1));
        assert(strncpy_from_user(out,(void *)bad[i],32)==-EFAULT);
        assert(strnlen_user((void *)bad[i],32)==0);
    }
    assert(__access_ok(ram,sizeof(ram)));
    assert(__access_ok(ram+sizeof(ram),0));
    assert(!__access_ok(ram+sizeof(ram),1));
    assert(!__access_ok(ram,sizeof(ram)+1));
    assert(!__access_ok(ram+1,ULONG_MAX));
    assert(strncpy_from_user(out,ram+64,1)==-EFAULT);
    assert(strnlen_user(ram+64,1)==0);
    memset(ram,'x',sizeof(ram));
    assert(strncpy_from_user(out,ram+63,8)==-EFAULT);
    assert(strnlen_user(ram+63,8)==0);
    /* A NUL near the physical end succeeds even with a larger count. */
    ram[63]=0;
    assert(strncpy_from_user(out,ram+60,32)==3);
    assert(!memcmp(out,"xxx",4));
    assert(strnlen_user(ram+60,32)==4);
    assert(strncpy_from_user(out,ram+63,32)==0);
    assert(strnlen_user(ram+63,32)==1);
    memcpy(ram,"abcdef",7);
    assert(strncpy_from_user(out,ram,4)==4);
    assert(!memcmp(out,"abcd",4));
    assert(strnlen_user(ram,4)>4);
    assert(strncpy_from_user(out,ram,32)==6);
    assert(!strcmp(out,"abcdef"));
    assert(strnlen_user(ram,32)==7);
    assert(strncpy_from_user(out,NULL,0)==0);
    assert(strnlen_user(NULL,0)==0);
    puts("P4 user-access bounds and generic strings PASS");
}
