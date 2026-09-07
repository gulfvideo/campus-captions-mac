"""Starting Campus Captions.

launch.py is the double-click launcher for a source checkout (two
subprocesses); embedded.py is the same job done inside one process, for the
packaged macOS and Windows apps. A real package rather than an implicit
namespace one, because PyInstaller has to collect these modules into a frozen
build and namespace packages are where that goes wrong.
"""
