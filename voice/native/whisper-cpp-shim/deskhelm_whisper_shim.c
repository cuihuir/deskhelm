#include "deskhelm_whisper_shim.h"

#include <limits.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#include "whisper.h"

#define DESKHELM_WHISPER_SHIM_ABI "deskhelm_whisper_shim_v1"
#define DESKHELM_WHISPER_ERROR_SIZE 32

struct deskhelm_whisper_context {
    struct whisper_context *whisper;
    int n_threads;
    char last_error[DESKHELM_WHISPER_ERROR_SIZE];
};

static void set_error(
    struct deskhelm_whisper_context *context,
    const char *error_code) {
    if (context == NULL) {
        return;
    }
    (void)strncpy(
        context->last_error,
        error_code,
        DESKHELM_WHISPER_ERROR_SIZE - 1);
    context->last_error[DESKHELM_WHISPER_ERROR_SIZE - 1] = '\0';
}

static bool should_abort(void *data) {
    const volatile int *abort_flag = (const volatile int *)data;
    return abort_flag != NULL && *abort_flag != 0;
}

static void silence_log(enum ggml_log_level level, const char *text, void *data) {
    (void)level;
    (void)text;
    (void)data;
}

const char *deskhelm_whisper_shim_abi(void) {
    return DESKHELM_WHISPER_SHIM_ABI;
}

void *deskhelm_whisper_create(const char *model_path, int n_threads) {
    if (model_path == NULL || model_path[0] == '\0' ||
        n_threads < 1 || n_threads > 32) {
        return NULL;
    }

    struct deskhelm_whisper_context *context =
        (struct deskhelm_whisper_context *)calloc(1, sizeof(*context));
    if (context == NULL) {
        return NULL;
    }
    context->n_threads = n_threads;
    set_error(context, "");

    /* Keep model/runtime diagnostics out of the host process by default. */
    whisper_log_set(silence_log, NULL);
    struct whisper_context_params params = whisper_context_default_params();
    params.use_gpu = false;
    params.flash_attn = false;
    params.gpu_device = 0;
    context->whisper = whisper_init_from_file_with_params(model_path, params);
    if (context->whisper == NULL) {
        set_error(context, "model_load_failed");
        free(context);
        return NULL;
    }
    return context;
}

void deskhelm_whisper_free(void *opaque_context) {
    struct deskhelm_whisper_context *context =
        (struct deskhelm_whisper_context *)opaque_context;
    if (context == NULL) {
        return;
    }
    if (context->whisper != NULL) {
        whisper_free(context->whisper);
    }
    free(context);
}

int deskhelm_whisper_transcribe(
    void *opaque_context,
    const float *samples,
    size_t sample_count,
    const char *language,
    volatile int *abort_flag,
    char *output,
    size_t output_size) {
    struct deskhelm_whisper_context *context =
        (struct deskhelm_whisper_context *)opaque_context;
    if (context == NULL || context->whisper == NULL) {
        return DESKHELM_WHISPER_NATIVE_ERROR;
    }
    set_error(context, "");
    if (samples == NULL || sample_count == 0 || sample_count > INT_MAX ||
        output == NULL || output_size == 0) {
        set_error(context, "invalid_input");
        return DESKHELM_WHISPER_NATIVE_ERROR;
    }
    output[0] = '\0';
    if (should_abort((void *)abort_flag)) {
        set_error(context, "cancelled");
        return DESKHELM_WHISPER_CANCELLED;
    }

    struct whisper_full_params params =
        whisper_full_default_params(WHISPER_SAMPLING_GREEDY);
    params.n_threads = context->n_threads;
    params.translate = false;
    params.no_context = true;
    params.no_timestamps = true;
    params.single_segment = false;
    params.print_special = false;
    params.print_progress = false;
    params.print_realtime = false;
    params.print_timestamps = false;
    params.language = (language != NULL && language[0] != '\0' &&
                       strcmp(language, "auto") != 0)
                          ? language
                          : NULL;
    params.detect_language = params.language == NULL;
    params.abort_callback = should_abort;
    params.abort_callback_user_data = (void *)abort_flag;

    const int result = whisper_full(
        context->whisper,
        params,
        samples,
        (int)sample_count);
    if (result != 0) {
        if (should_abort((void *)abort_flag)) {
            set_error(context, "cancelled");
            return DESKHELM_WHISPER_CANCELLED;
        }
        set_error(context, "decode_failed");
        return DESKHELM_WHISPER_NATIVE_ERROR;
    }

    size_t output_length = 0;
    const int segment_count = whisper_full_n_segments(context->whisper);
    for (int index = 0; index < segment_count; ++index) {
        const char *text = whisper_full_get_segment_text(
            context->whisper,
            index);
        if (text == NULL || text[0] == '\0') {
            continue;
        }
        const size_t text_length = strlen(text);
        if (text_length >= output_size - output_length) {
            output[0] = '\0';
            set_error(context, "output_too_small");
            return DESKHELM_WHISPER_OUTPUT_TOO_SMALL;
        }
        (void)memcpy(output + output_length, text, text_length);
        output_length += text_length;
        output[output_length] = '\0';
    }
    if (output_length == 0) {
        set_error(context, "no_transcript");
        return DESKHELM_WHISPER_NO_TRANSCRIPT;
    }
    return DESKHELM_WHISPER_OK;
}

const char *deskhelm_whisper_last_error(void *opaque_context) {
    const struct deskhelm_whisper_context *context =
        (const struct deskhelm_whisper_context *)opaque_context;
    if (context == NULL) {
        return "invalid_context";
    }
    return context->last_error;
}
