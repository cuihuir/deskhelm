# DeskHelm whisper.cpp Shim

This directory contains the small native ABI used by the optional
`WhisperCppAsrProvider`. It is compiled outside the DeskHelm repository
against the pinned `whisper.cpp` `v1.9.2` headers and library. Model weights,
the upstream checkout, and the resulting shared library are not repository
artifacts.

The shim exposes model creation, one serialized float-PCM transcription call,
an abort flag, and fixed status codes. It deliberately does not expose
`whisper_full_params` to Python. The provider is explicit and lazy; it is not
part of `LocalVoiceConfig` or the default Bridge voice composition.

From an external `whisper.cpp` `v1.9.2` checkout, a host build can use a
command shaped like this (adjust the library directory for the checkout):

```bash
cc -std=c11 -fPIC -fvisibility=hidden -DDESKHELM_WHISPER_SHIM_BUILD \
  -I/path/to/whisper.cpp/include \
  -I/path/to/whisper.cpp/ggml/include \
  -shared deskhelm_whisper_shim.c \
  -L/path/to/whisper.cpp/build/src -lwhisper \
  -Wl,-rpath,/path/to/whisper.cpp/build/src \
  -o libdeskhelm_whisper_shim.so
```

The exact upstream build flags and transitive libraries remain the responsibility
of the external pinned checkout. Validate the resulting ABI before pointing a
provider at it; never silently substitute another ASR provider.
