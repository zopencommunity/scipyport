# scipyport

z/OS port of [scipy](https://github.com/scipy/scipy) — fundamental algorithms
for scientific computing.

## Status

Targets scipy 1.18.0, which requires Python 3.12 or later — matching the three
interpreters available here.

## Installation

```sh
zopen install scipy
```

Or from the wheel index:

```sh
pip install scipy --extra-index-url https://repo.zopen.community/pypi/wheels/simple/
```

## Linear algebra is real here

Unlike [numpy](https://github.com/zopencommunity/numpyport), which currently falls
back to its bundled `lapack_lite`, this links a genuine BLAS and LAPACK:
[blis](https://github.com/zopencommunity/blisport) for BLAS and
[lapack](https://github.com/zopencommunity/lapackport) for LAPACK, both located
through pkg-config via `-Dblas=blis -Dlapack=lapack`.

## What this port has to deal with

**Almost no Fortran.** scipy 1.18 has four fixed-form Fortran sources, all in
`scipy/odr/odrpack`. The rest of what was Fortran has been translated to C and
C++ upstream. The Fortran that remains is built through the
[fortran](https://github.com/zopencommunity/fortranport) port.

**The build helpers are all pure Python.** meson-python, Cython, pybind11 and
pythran each have pure-Python wheels at the versions scipy pins, so none of them
is compiled during the build. They are installed into the venv rather than
resolved by build isolation, so the build uses the system meson — which carries
this platform's compiler support — instead of fetching its own.

**No vendored meson.** numpy vendors a fork of meson and needs its z/OS support
patched into that copy; scipy uses the system one, so
[mesonport](https://github.com/zopencommunity/mesonport) applies directly.

**String literals must be ASCII.** The C compiler emits EBCDIC literals by
default while the interpreter is an ASCII build, so extensions compile, link and
install perfectly and then fail to import. `-fzos-le-char-mode=ascii` fixes it.
setuptools passes these flags for extension builds; meson does not.

**Extensions must bind against libpython's side deck**, or every `Py*` symbol
comes back `UNRESOLVED` from the binder.

**The interpreters ship unrelocated build metadata.** Their pkg-config files and
`sysconfig`'s `LIBDIR` name the machine they were built on rather than where they
are installed. Both are recomputed from `sys.base_prefix` and verified before
use, per interpreter.
