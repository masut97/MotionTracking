"""Entry point shared by the extended application and isolated measurement children."""
from pathlib import Path
import sys

NORMAL_GUI_START = False


def main():
    from .single_instance import is_auxiliary, run_gui
    return run_application() if is_auxiliary(sys.argv) else run_gui(run_application)


def run_application():
    global NORMAL_GUI_START
    from .single_instance import is_auxiliary
    NORMAL_GUI_START = not is_auxiliary(sys.argv)
    if len(sys.argv) == 4 and sys.argv[1] == "--launch-after-exit":
        from .update_ui import launch_after_exit
        return launch_after_exit(int(sys.argv[2]), Path(sys.argv[3]))
    if len(sys.argv) >= 4 and sys.argv[1] == "--sync-worker":
        from .worker import main as run_worker
        return run_worker(int(sys.argv[2]), sys.argv[3])
    from .local_migration import initialize_updated_runtime
    initialize_updated_runtime(Path(sys.executable).resolve().parent)
    from .integration import install
    install()
    if len(sys.argv) >= 3 and sys.argv[1] == "--sync-camera-self-test":
        from .camera_self_test import run
        return run(Path(sys.argv[2]))
    if len(sys.argv) >= 3 and sys.argv[1] == "--sync-self-test":
        from .self_test import run
        return run(Path(sys.argv[2]))
    from atracsys_tracker.app import main as tracker_main
    return tracker_main()


if __name__ == "__main__":
    raise SystemExit(main())
