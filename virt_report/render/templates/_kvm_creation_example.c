#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/kvm.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#if !defined(__x86_64__) && !defined(__i386__)
#error "This example requires an x86 Linux host"
#endif

int main(void)
{
    const size_t ram_size = 2 * 1024 * 1024;
    int kvm = -1, vm = -1, vcpu = -1, result = 1;
    int run_size = 0, rc;
    unsigned char *ram = MAP_FAILED;
    struct kvm_run *run = MAP_FAILED;
    const char *operation = "open /dev/kvm";

#define CHECK(call) do { \
    operation = #call; \
    if ((call) < 0) goto fail; \
} while (0)

    CHECK(kvm = open("/dev/kvm", O_RDWR | O_CLOEXEC));
    CHECK(rc = ioctl(kvm, KVM_GET_API_VERSION, 0));
    if (rc != KVM_API_VERSION) {
        fprintf(stderr, "Unsupported KVM API: %d\n", rc);
        goto cleanup;
    }
    const int capabilities[] = {
        KVM_CAP_USER_MEMORY, KVM_CAP_IRQCHIP,
        KVM_CAP_SET_TSS_ADDR, KVM_CAP_IMMEDIATE_EXIT
    };
    for (size_t i = 0; i < sizeof(capabilities) / sizeof(capabilities[0]); i++) {
        CHECK(rc = ioctl(kvm, KVM_CHECK_EXTENSION, capabilities[i]));
        if (rc == 0) {
            fprintf(stderr, "Required KVM capability missing: %d\n", capabilities[i]);
            goto cleanup;
        }
    }
    CHECK(vm = ioctl(kvm, KVM_CREATE_VM, 0));
    /* Reserve three GPA pages for Intel's real-mode TSS, outside guest RAM. */
    CHECK(ioctl(vm, KVM_SET_TSS_ADDR, 0xfffbd000UL));
    CHECK(ioctl(vm, KVM_CREATE_IRQCHIP, 0));

    operation = "mmap guest RAM";
    ram = mmap(NULL, ram_size, PROT_READ | PROT_WRITE,
               MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (ram == MAP_FAILED) goto fail;
    struct kvm_userspace_memory_region region = {
        .slot = 0, .guest_phys_addr = 0, .memory_size = ram_size,
        .userspace_addr = (uintptr_t)ram
    };
    CHECK(ioctl(vm, KVM_SET_USER_MEMORY_REGION, &region));
    /* 16-bit real mode: store 42 at 0x2000, then OUT 42 to port 0xe9. */
    const unsigned char guest[] = {
        0xc6, 0x06, 0x00, 0x20, 0x2a, /* mov byte [0x2000], 42 */
        0xb0, 0x2a,                   /* mov al, 42 */
        0xe6, 0xe9,                   /* out 0xe9, al */
        0xf4                          /* hlt: not executed in this example */
    };
    memcpy(ram + 0x1000, guest, sizeof(guest));

    CHECK(vcpu = ioctl(vm, KVM_CREATE_VCPU, 0));
    CHECK(run_size = ioctl(kvm, KVM_GET_VCPU_MMAP_SIZE, 0));
    if ((size_t)run_size < sizeof(*run)) {
        fprintf(stderr, "KVM run mapping is too small\n");
        goto cleanup;
    }
    operation = "mmap kvm_run";
    run = mmap(NULL, (size_t)run_size, PROT_READ | PROT_WRITE,
               MAP_SHARED, vcpu, 0);
    if (run == MAP_FAILED) goto fail;
    struct kvm_sregs sregs;
    CHECK(ioctl(vcpu, KVM_GET_SREGS, &sregs));
    sregs.cs.base = sregs.cs.selector = 0;
    sregs.ds.base = sregs.ds.selector = 0;
    struct kvm_regs regs = { .rip = 0x1000, .rsp = 0x8000, .rflags = 2 };
    CHECK(ioctl(vcpu, KVM_SET_SREGS, &sregs));
    CHECK(ioctl(vcpu, KVM_SET_REGS, &regs));
    struct kvm_mp_state mp = { .mp_state = KVM_MP_STATE_RUNNABLE };
    CHECK(ioctl(vcpu, KVM_SET_MP_STATE, &mp));

    operation = "KVM_RUN";
    do {
        rc = ioctl(vcpu, KVM_RUN, 0);
    } while (rc < 0 && errno == EINTR);
    if (rc < 0) goto fail;
    if (run->exit_reason != KVM_EXIT_IO ||
        run->io.direction != KVM_EXIT_IO_OUT || run->io.port != 0xe9 ||
        run->io.size != 1 || run->io.count != 1 ||
        run->io.data_offset >= (uint64_t)run_size) {
        fprintf(stderr, "Unexpected KVM exit: %u\n", run->exit_reason);
        goto cleanup;
    }
    unsigned char value = *((unsigned char *)run + run->io.data_offset);
    if (value != 42 || ram[0x2000] != 42) {
        fprintf(stderr, "Guest result mismatch\n");
        goto cleanup;
    }
    /* Complete the pending OUT, but do not execute the following HLT. */
    run->immediate_exit = 1;
    rc = ioctl(vcpu, KVM_RUN, 0);
    if (rc != -1 || errno != EINTR) {
        fprintf(stderr, "Expected EINTR after completing the OUT\n");
        goto cleanup;
    }
    CHECK(ioctl(vcpu, KVM_GET_REGS, &regs));
    if (regs.rip != 0x1009) {
        fprintf(stderr, "Unexpected RIP: 0x%llx\n", (unsigned long long)regs.rip);
        goto cleanup;
    }
    printf("RAM[0x2000]=42, OUT(0xe9)=42, RIP=0x%llx\n",
           (unsigned long long)regs.rip);
    result = 0;
    goto cleanup;

fail:
    fprintf(stderr, "%s: %s\n", operation, strerror(errno));
cleanup:
    if (run != MAP_FAILED) munmap(run, (size_t)run_size);
    if (vcpu >= 0) close(vcpu);
    if (vm >= 0) close(vm);
    if (ram != MAP_FAILED) munmap(ram, ram_size);
    if (kvm >= 0) close(kvm);
    return result;
}
