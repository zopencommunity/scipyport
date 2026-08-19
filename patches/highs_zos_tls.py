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


def main():
    root = sys.argv[1]
    patch(root + "/highs/parallel/HighsTaskExecutor.h",
          HEADER_FROM, HEADER_TO, "accessor declarations")
    patch(root + "/highs/parallel/HighsTaskExecutor.cpp",
          CPP_ANCHOR, CPP_INSERT, "thread-local definitions")


if __name__ == "__main__":
    main()
