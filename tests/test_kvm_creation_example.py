"""Compile the published program; emulate API calls without opening real KVM."""
import os
import platform
import re
import shutil
import subprocess
from html import unescape

import pytest

from virt_report.config import Config
from virt_report.render import render


SOURCE = render.TEMPLATES_DIR / '_kvm_creation_example.c'
MOCK = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <linux/kvm.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

static int calls, system_open, vm_open, vcpu_open, irqchip, entered;
static unsigned char *ram;
static struct kvm_run *run;

static int fail_now(void) {
    const char *value = getenv("FAIL_AT");
    ++calls;
    if (value && calls == atoi(value)) { errno = EIO; return 1; }
    return 0;
}
int __wrap_open(const char *path, int flags, ...) {
    (void)flags;
    assert(strcmp(path, "/dev/kvm") == 0);
    if (fail_now()) return -1;
    system_open = 1;
    return 10;
}
int __wrap_ioctl(int fd, unsigned long request, ...) {
    if (fail_now()) return -1;
    if (request == KVM_GET_API_VERSION) { assert(fd == 10); return KVM_API_VERSION; }
    if (request == KVM_CHECK_EXTENSION) { assert(fd == 10); return 1; }
    if (request == KVM_CREATE_VM) { assert(fd == 10); vm_open = 1; return 11; }
    if (request == KVM_SET_TSS_ADDR) { assert(fd == 11); return 0; }
    if (request == KVM_CREATE_IRQCHIP) { assert(fd == 11 && !vcpu_open); irqchip = 1; return 0; }
    if (request == KVM_CREATE_VCPU) { assert(fd == 11 && irqchip && ram); vcpu_open = 1; return 12; }
    if (request == KVM_GET_VCPU_MMAP_SIZE) { assert(fd == 10); return 8192; }
    if (request == KVM_RUN) {
        assert(fd == 12 && run && ram);
        if (entered++) {
            assert(run->immediate_exit == 1);
            errno = EINTR;
            return -1;
        }
        assert(ram[0x1000] == 0xc6 && ram[0x1008] == 0xe9);
        ram[0x2000] = 42;  /* Simulated result, not CPU execution. */
        run->exit_reason = KVM_EXIT_IO;
        run->io.direction = KVM_EXIT_IO_OUT;
        run->io.port = 0xe9;
        run->io.size = run->io.count = 1;
        run->io.data_offset = 4096;
        ((unsigned char *)run)[4096] = 42;
        return 0;
    }
    va_list ap;
    va_start(ap, request);
    void *argument = va_arg(ap, void *);
    va_end(ap);
    if (request == KVM_SET_USER_MEMORY_REGION) {
        struct kvm_userspace_memory_region *region = argument;
        assert(fd == 11 && region->slot == 0 && region->guest_phys_addr == 0);
        assert(region->memory_size == 2 * 1024 * 1024);
        assert(region->userspace_addr == (uintptr_t)ram);
    } else if (request == KVM_GET_SREGS) {
        memset(argument, 0, sizeof(struct kvm_sregs));
    } else if (request == KVM_SET_SREGS) {
        struct kvm_sregs *s = argument;
        assert(s->cs.base == 0 && s->ds.base == 0);
    } else if (request == KVM_SET_REGS) {
        struct kvm_regs *r = argument;
        assert(r->rip == 0x1000 && r->rflags == 2);
    } else if (request == KVM_SET_MP_STATE) {
        assert(((struct kvm_mp_state *)argument)->mp_state == KVM_MP_STATE_RUNNABLE);
    } else if (request == KVM_GET_REGS) {
        assert(entered == 2);
        ((struct kvm_regs *)argument)->rip = 0x1009;
    } else {
        abort();
    }
    return 0;
}
void *__wrap_mmap(void *addr, size_t length, int prot, int flags, int fd, off_t offset) {
    (void)addr; (void)prot; (void)flags; (void)offset;
    if (fail_now()) return MAP_FAILED;
    void *p = calloc(1, length);
    assert(p);
    if (fd == -1) { assert(!ram); ram = p; }
    else { assert(fd == 12 && vcpu_open); run = p; }
    return p;
}
int __wrap_munmap(void *p, size_t size) {
    (void)size;
    if (p == run) run = NULL;
    else { assert(p == ram && !vm_open && !vcpu_open); ram = NULL; }
    free(p);
    return 0;
}
int __wrap_close(int fd) {
    if (fd == 12) { assert(vcpu_open && !run); vcpu_open = 0; }
    else if (fd == 11) { assert(vm_open && !vcpu_open); vm_open = 0; }
    else { assert(fd == 10 && system_open && !vm_open); system_open = 0; }
    return 0;
}
__attribute__((destructor)) static void verify_cleanup(void) {
    assert(!system_open && !vm_open && !vcpu_open && !ram && !run);
    printf("mock calls=%d; resources released\n", calls);
}
'''


@pytest.fixture(scope='module')
def mocked_program(tmp_path_factory):
    if not shutil.which('cc') or platform.machine().lower() not in {'x86_64', 'amd64', 'i386', 'i686'}:
        pytest.skip('The x86 C compiler is optional')
    directory = tmp_path_factory.mktemp('kvm-create-example')
    stub = directory / 'mock.c'
    stub.write_text(MOCK)
    binary = directory / 'example'
    subprocess.run([
        'cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', str(SOURCE), str(stub),
        '-Wl,--wrap=open,--wrap=ioctl,--wrap=mmap,--wrap=munmap,--wrap=close',
        '-o', str(binary),
    ], check=True, capture_output=True, text=True, timeout=30)
    return binary


def test_published_example_is_exactly_the_compiled_source():
    page = render.render_mechanisms_html(Config(), 'kvm-vm-creation')
    blocks = [unescape(block) for block in re.findall(r'<pre><code>(.*?)</code></pre>', page, re.S)]
    code = next(block for block in blocks if '#define _GNU_SOURCE' in block)
    assert code.strip() == SOURCE.read_text().strip()
    assert '尚未在真实 KVM 上验证' in page
    assert 'timeout 5s ./kvm-minimal' in page


def test_example_completes_pending_io_and_releases_resources(mocked_program):
    result = subprocess.run([str(mocked_program)], capture_output=True, text=True,
                            env={**os.environ, 'FAIL_AT': '0'}, timeout=5)
    assert result.returncode == 0, result.stderr
    assert 'RAM[0x2000]=42, OUT(0xe9)=42, RIP=0x1009' in result.stdout
    assert 'mock calls=21; resources released' in result.stdout


@pytest.mark.parametrize('failure', range(1, 22))
def test_each_system_call_failure_releases_only_acquired_resources(mocked_program, failure):
    result = subprocess.run([str(mocked_program)], capture_output=True, text=True,
                            env={**os.environ, 'FAIL_AT': str(failure)}, timeout=5)
    assert result.returncode == 1, result.stderr
    assert result.stderr.strip()
    assert result.stdout == f'mock calls={failure}; resources released\n'
