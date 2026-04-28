Import("env")

import os
import subprocess


FREQ_MAP = {
    "20000000L": "20m",
    "26000000L": "26m",
    "40000000L": "40m",
    "80000000L": "80m",
}


def _normalize_flash_freq(raw_value):
    value = str(raw_value or "").strip()
    return FREQ_MAP.get(value, value.lower().replace("hz", "")) or "40m"


def _merge_firmware(source, target, env):
    build_dir = env.subst("$BUILD_DIR")

    bootloader = os.path.join(build_dir, "bootloader.bin")
    partitions = os.path.join(build_dir, "partitions.bin")
    firmware = os.path.join(build_dir, "firmware.bin")
    merged = os.path.join(build_dir, "firmware.merged.bin")

    missing = [p for p in (bootloader, partitions, firmware) if not os.path.exists(p)]
    if missing:
        print("[merge_firmware] skipped: missing build artifacts")
        for path in missing:
            print(f"  - {path}")
        return

    board_cfg = env.BoardConfig()
    flash_mode = board_cfg.get("build.flash_mode", "dio")
    flash_freq = _normalize_flash_freq(board_cfg.get("build.f_flash", "40000000L"))
    flash_size = board_cfg.get("upload.flash_size", "4MB")

    esptool_dir = env.PioPlatform().get_package_dir("tool-esptoolpy")
    esptool_py = os.path.join(esptool_dir, "esptool.py")

    cmd = [
        env.subst("$PYTHONEXE"),
        esptool_py,
        "--chip",
        "esp32",
        "merge_bin",
        "-o",
        merged,
        "--flash_mode",
        str(flash_mode),
        "--flash_freq",
        str(flash_freq),
        "--flash_size",
        str(flash_size),
        "0x1000",
        bootloader,
        "0x8000",
        partitions,
        "0x10000",
        firmware,
    ]

    print("[merge_firmware] generating firmware.merged.bin")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError("[merge_firmware] failed to generate firmware.merged.bin")


env.AddPostAction("$BUILD_DIR/${PROGNAME}.bin", _merge_firmware)
