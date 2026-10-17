/* Host-only qualification of the exact source-built static archive. */
#include <ffi.h>
#include <stdint.h>
#include <stdio.h>

#if !defined(__APPLE__) || !(defined(__aarch64__) || defined(__arm64__))
#error "This smoke test must run natively on Apple ARM64."
#endif

_Static_assert(sizeof(void *) == 8, "A 64-bit host is required");

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "libffi source smoke failed at line %d\n", __LINE__); \
        return 1; \
    } \
} while (0)

static int64_t integer_call(int64_t a, int32_t b) {
    return a + (int64_t)b * 17;
}

static double floating_call(double a, int32_t b) {
    return a * 0.5 + b;
}

typedef struct { double x, y; } Pair;

static Pair aggregate_call(Pair value, double factor) {
    return (Pair){value.x * factor, value.y + factor};
}

static void closure_call(ffi_cif *cif, void *result, void **args, void *context) {
    (void)cif;
    *(double *)result = (double)*(int64_t *)args[0] + *(double *)args[1]
        + *(double *)context;
}

int main(void) {
    ffi_cif cif;
    int64_t integer = INT64_C(0x123456789), integer_result = 0;
    int32_t small_integer = -31;
    ffi_type *integer_types[] = {&ffi_type_sint64, &ffi_type_sint32};
    void *integer_args[] = {&integer, &small_integer};
    CHECK(ffi_prep_cif(&cif, FFI_DEFAULT_ABI, 2, &ffi_type_sint64, integer_types) == FFI_OK);
    ffi_call(&cif, FFI_FN(integer_call), &integer_result, integer_args);
    CHECK(integer_result == integer_call(integer, small_integer));

    double floating = 3.5, floating_result = 0;
    ffi_type *floating_types[] = {&ffi_type_double, &ffi_type_sint32};
    void *floating_args[] = {&floating, &small_integer};
    CHECK(ffi_prep_cif(&cif, FFI_DEFAULT_ABI, 2, &ffi_type_double, floating_types) == FFI_OK);
    ffi_call(&cif, FFI_FN(floating_call), &floating_result, floating_args);
    CHECK(floating_result == floating_call(floating, small_integer));

    /* A homogeneous floating aggregate uses the ARM64 vector-register ABI. */
    ffi_type *pair_elements[] = {&ffi_type_double, &ffi_type_double, NULL};
    ffi_type pair_type = {0, 0, FFI_TYPE_STRUCT, pair_elements};
    ffi_type *aggregate_types[] = {&pair_type, &ffi_type_double};
    Pair pair = {1.25, -8.0}, pair_result = {0, 0};
    double factor = 0.5;
    void *aggregate_args[] = {&pair, &factor};
    CHECK(ffi_prep_cif(&cif, FFI_DEFAULT_ABI, 2, &pair_type, aggregate_types) == FFI_OK);
    ffi_call(&cif, FFI_FN(aggregate_call), &pair_result, aggregate_args);
    CHECK(pair_result.x == 0.625 && pair_result.y == -7.5);

    /* Exercise executable callback allocation and the patched Apple trampoline. */
    ffi_type *closure_types[] = {&ffi_type_sint64, &ffi_type_double};
    double context = 0.25;
    void *code = NULL;
    ffi_closure *closure = ffi_closure_alloc(sizeof(ffi_closure), &code);
    CHECK(closure != NULL && code != NULL);
    CHECK(ffi_prep_cif(&cif, FFI_DEFAULT_ABI, 2, &ffi_type_double, closure_types) == FFI_OK);
    CHECK(ffi_prep_closure_loc(closure, &cif, closure_call, &context, code) == FFI_OK);
    double (*callback)(int64_t, double) = (double (*)(int64_t, double))code;
    CHECK(callback(19, 1.5) == 20.75);
    ffi_closure_free(closure);

    puts("libffi source smoke passed: arm64 integer/floating/aggregate calls and closure");
    return 0;
}
