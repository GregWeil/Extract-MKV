from collections.abc import Callable, Iterable
import subprocess
import logging

from src.environment import EnvironmentConfig

MAKEMKV_STANDARD_ARGS = ["--robot", "--noscan", "--minlength=0", "--messages=-stdout", "--debug=-null"]

def exec(args: Iterable[str], print_output: bool, parse_progress: Callable[[str], float | None]=None):
    logging.debug(' '.join(args))
    progress_total = 40
    progress_segments = -1
    output = ""
    with subprocess.Popen(args, stdout=subprocess.PIPE, text=True, universal_newlines=True, encoding="UTF-8") as process:
        for line in process.stdout:
            if print_output: print(line, end="")
            elif parse_progress:
                progress = parse_progress(line)
                if progress != None:
                    segments = int(progress * progress_total)
                    if segments != progress_segments:
                        print("\r[" + ("#" * segments) + ("-" * (progress_total - segments)) + "]", end="")
                    progress_segments = segments
            output += line
    if process.returncode != 0:
        if not print_output:
            if parse_progress: print("")
            print(output, end="")
        raise subprocess.CalledProcessError(process.returncode, process.args)
    elif parse_progress and not print_output: print("\r  " + (" " * progress_total), end="\r")
    return output

def parse_makemkv_progress(line: str) -> float | None:
    if not line.startswith("PRGV:"): return None
    [current, total, max] = line[5:].split(",")
    return int(current) / int(max)

def parse_mkvmerge_progress(line: str) -> float | None:
    if not line.startswith("Progress:"): return None
    return int(line[10:-2]) / 100

def exec_makemkv(args: Iterable[str], env: EnvironmentConfig, print_output: bool):
    progress_output = "--progress=-null" if print_output else "--progress=-stdout"
    return exec([*env.makemkvcon, *MAKEMKV_STANDARD_ARGS, progress_output, *args], print_output, parse_makemkv_progress)

def exec_mkvmerge(args: Iterable[str], env: EnvironmentConfig, print_output: bool):
    return exec([*env.mkvmerge, *args], print_output, parse_mkvmerge_progress)