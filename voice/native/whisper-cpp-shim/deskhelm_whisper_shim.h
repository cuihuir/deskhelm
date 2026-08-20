#ifndef DESKHELM_WHISPER_SHIM_H
#define DESKHELM_WHISPER_SHIM_H

#include <stddef.h>

#if defined(_WIN32)
#    if defined(DESKHELM_WHISPER_SHIM_BUILD)
#        define DESKHELM_WHISPER_SHIM_API __declspec(dllexport)
#    else
#        define DESKHELM_WHISPER_SHIM_API __declspec(dllimport)
#    endif
#else
#    define DESKHELM_WHISPER_SHIM_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

enum deskhelm_whisper_status {
    DESKHELM_WHISPER_OK = 0,
    DESKHELM_WHISPER_CANCELLED = 1,
    DESKHELM_WHISPER_NO_TRANSCRIPT = 2,
    DESKHELM_WHISPER_NATIVE_ERROR = 3,
    DESKHELM_WHISPER_OUTPUT_TOO_SMALL = 4,
};

DESKHELM_WHISPER_SHIM_API const char *deskhelm_whisper_shim_abi(void);

DESKHELM_WHISPER_SHIM_API void *deskhelm_whisper_create(
    const char *model_path,
    int n_threads);

DESKHELM_WHISPER_SHIM_API void deskhelm_whisper_free(void *context);

DESKHELM_WHISPER_SHIM_API int deskhelm_whisper_transcribe(
    void *context,
    const float *samples,
    size_t sample_count,
    const char *language,
    volatile int *abort_flag,
    char *output,
    size_t output_size);

DESKHELM_WHISPER_SHIM_API const char *deskhelm_whisper_last_error(
    void *context);

#ifdef __cplusplus
}
#endif

#endif
