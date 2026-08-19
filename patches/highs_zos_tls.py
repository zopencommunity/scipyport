#!/usr/bin/env python3
"""Let HiGHS build on z/OS, where a thread_local cannot have a destructor.

    HighsTaskExecutor.cpp:28:24: error: type of thread-local variable has
    non-trivial destruction

IBM's clang rejects this because the platform has no __cxa_thread_atexit, so
there is nowhere to register the destructor. ExecutorHandle needs one: its
dispose() stops the HiGHS worker threads when a thread ends, and dropping it
would leave them running.

HiGHS already routes every access through threadLocalExecutorHandle(), with an
_WIN32 branch that declares the accessors as functions instead of using plain
thread_local variables. This adds the same shape for z/OS, backed by a pthread
key whose destructor gives the same at-thread-exit disposal.

Applied from the port rather than as a .patch file because HiGHS is a submodule
of scipy -- zopen-build applies patches/ with 'git apply' from the source root,
and git refuses paths inside a submodule. Keying on distinctive text also keeps
this working across HiGHS versions rather than breaking on line numbers.

Idempotent, and fails loudly if the code no longer looks the way it expects.
"""
import os
import sys

MARKER = "__MVS__"

HEADER_FROM = """#ifdef _WIN32
  static HighsSplitDeque*& threadLocalWorkerDeque();
  static ExecutorHandle& threadLocalExecutorHandle();"""
HEADER_TO = """#if defined(_WIN32) || defined(__MVS__)
  static HighsSplitDeque*& threadLocalWorkerDeque();
  static ExecutorHandle& threadLocalExecutorHandle();"""

CPP_ANCHOR = """#else
thread_local HighsSplitDeque* HighsTaskExecutor::threadLocalWorkerDequePtr{"""
CPP_INSERT = """#elif defined(__MVS__)
// z/OS: a thread_local variable may not have a non-trivial destructor, so hold
// the handle in a pthread key instead. The key's destructor runs at thread
// exit, which is exactly what ~ExecutorHandle() would have done.
#include <pthread.h>

static thread_local HighsSplitDeque* zosThreadLocalWorkerDequePtr{nullptr};

HighsSplitDeque*& HighsTaskExecutor::threadLocalWorkerDeque() {
  return zosThreadLocalWorkerDequePtr;
}

static pthread_key_t zosExecutorHandleKey;
static pthread_once_t zosExecutorHandleOnce = PTHREAD_ONCE_INIT;

static void zosExecutorHandleDispose(void* handle) {
  delete static_cast<HighsTaskExecutor::ExecutorHandle*>(handle);
}

static void zosExecutorHandleInit() {
  pthread_key_create(&zosExecutorHandleKey, zosExecutorHandleDispose);
}

HighsTaskExecutor::ExecutorHandle&
HighsTaskExecutor::threadLocalExecutorHandle() {
  pthread_once(&zosExecutorHandleOnce, zosExecutorHandleInit);
  void* handle = pthread_getspecific(zosExecutorHandleKey);
  if (handle == nullptr) {
    handle = new ExecutorHandle();
    pthread_setspecific(zosExecutorHandleKey, handle);
  }
  return *static_cast<ExecutorHandle*>(handle);
}
#else
thread_local HighsSplitDeque* HighsTaskExecutor::threadLocalWorkerDequePtr{"""


UARRAY_FROM = """thread_local global_state_t * current_global_state = global_domain_map.get();
thread_local global_state_t thread_local_domain_map;
thread_local local_state_t local_domain_map;"""

UARRAY_TO = """#if defined(__MVS__)
// z/OS: a thread_local may neither have a non-trivial destructor nor a
// non-constant initializer, and these break both rules -- the maps are
// std::unordered_map, and current_global_state is seeded from another object.
// Hold them in pthread keys, whose destructors free them at thread exit, which
// is what the implicit thread_local destructors did. The macros below keep
// every use site in this file unchanged.
#include <pthread.h>

namespace {

pthread_key_t zos_global_map_key;
pthread_once_t zos_global_map_once = PTHREAD_ONCE_INIT;
void zos_global_map_free(void * p) { delete static_cast<global_state_t *>(p); }
void zos_global_map_init() {
  pthread_key_create(&zos_global_map_key, zos_global_map_free);
}

pthread_key_t zos_local_map_key;
pthread_once_t zos_local_map_once = PTHREAD_ONCE_INIT;
void zos_local_map_free(void * p) { delete static_cast<local_state_t *>(p); }
void zos_local_map_init() {
  pthread_key_create(&zos_local_map_key, zos_local_map_free);
}

pthread_key_t zos_current_key;
pthread_once_t zos_current_once = PTHREAD_ONCE_INIT;
void zos_current_free(void * p) { delete static_cast<global_state_t **>(p); }
void zos_current_init() {
  pthread_key_create(&zos_current_key, zos_current_free);
}

global_state_t & zos_thread_local_domain_map() {
  pthread_once(&zos_global_map_once, zos_global_map_init);
  void * value = pthread_getspecific(zos_global_map_key);
  if (value == nullptr) {
    value = new global_state_t();
    pthread_setspecific(zos_global_map_key, value);
  }
  return *static_cast<global_state_t *>(value);
}

local_state_t & zos_local_domain_map() {
  pthread_once(&zos_local_map_once, zos_local_map_init);
  void * value = pthread_getspecific(zos_local_map_key);
  if (value == nullptr) {
    value = new local_state_t();
    pthread_setspecific(zos_local_map_key, value);
  }
  return *static_cast<local_state_t *>(value);
}

global_state_t *& zos_current_global_state() {
  pthread_once(&zos_current_once, zos_current_init);
  void * value = pthread_getspecific(zos_current_key);
  if (value == nullptr) {
    value = new global_state_t *(global_domain_map.get());
    pthread_setspecific(zos_current_key, value);
  }
  return *static_cast<global_state_t **>(value);
}

}  // namespace

#define current_global_state zos_current_global_state()
#define thread_local_domain_map zos_thread_local_domain_map()
#define local_domain_map zos_local_domain_map()
#else
thread_local global_state_t * current_global_state = global_domain_map.get();
thread_local global_state_t thread_local_domain_map;
thread_local local_state_t local_domain_map;
#endif"""


def patch(path, frm, to, what):
    with open(path, "r", encoding="utf-8", errors="surrogateescape") as fh:
        text = fh.read()
    if MARKER in text:
        print("  already patched: %s" % what)
        return
    if frm not in text:
        sys.exit("highs_zos_tls.py: could not find the %s it patches in %s -- "
                 "HiGHS has changed and this needs revisiting" % (what, path))
    with open(path, "w", encoding="utf-8", errors="surrogateescape") as fh:
        fh.write(text.replace(frm, to, 1))
    print("  patched: %s" % what)


def sweep_boost_math(root):
    """Drop thread_local from Boost.Math's function-local constants.

        hypergeometric_1F1.hpp:637: error: initializer for thread-local
        variable must be a constant expression

    z/OS requires a thread_local to be constant-initialised, and these are not:

        static const thread_local long long max_scaling =
            lltrunc(boost::math::tools::log_max_value<T>()) - 2;

    They are const, function-local, and derived only from the type -- every
    thread would compute the same value -- so an ordinary static is both correct
    and thread-safe here, C++11 guaranteeing the initialisation is run once.

    Swept across the tree rather than fixed line by line: the build only reveals
    them one template instantiation at a time, and there are several.
    """
    import os

    changed = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith((".hpp", ".ipp", ".h")):
                continue
            path = os.path.join(dirpath, name)
            with open(path, "r", encoding="utf-8", errors="surrogateescape") as fh:
                text = fh.read()
            if "static const thread_local " not in text:
                continue
            with open(path, "w", encoding="utf-8", errors="surrogateescape") as fh:
                fh.write(text.replace("static const thread_local ", "static const "))
            changed += 1
    print("  boost_math: dropped thread_local from constants in %d file(s)" % changed)


def main():
    scipy_root = sys.argv[1]
    highs = scipy_root + "/subprojects/highs"
    patch(highs + "/highs/parallel/HighsTaskExecutor.h",
          HEADER_FROM, HEADER_TO, "HiGHS accessor declarations")
    patch(highs + "/highs/parallel/HighsTaskExecutor.cpp",
          CPP_ANCHOR, CPP_INSERT, "HiGHS thread-local definitions")
    patch(scipy_root + "/scipy/_lib/_uarray/_uarray_dispatch.cxx",
          UARRAY_FROM, UARRAY_TO, "_uarray thread-local state")
    boost = scipy_root + "/subprojects/boost_math"
    if os.path.isdir(boost):
        sweep_boost_math(boost)


if __name__ == "__main__":
    main()
