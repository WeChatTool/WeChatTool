from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .analyze import CompatibilityError, analyze
from .macho import MachOError
from .prepare import prepare


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WeChatTool — inspect compatibility and prepare a plugin-enabled app copy.")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("analyze", help="Read-only compatibility analysis; never writes the app.")
    inspect.add_argument("--app", type=Path, default=Path("/Applications/WeChat.app"))
    inspect.add_argument("--image", help="Bundle-relative core path, for future layouts.")
    inspect.add_argument("--json", action="store_true", help="Print the full machine-readable plan.")
    make = commands.add_parser("prepare", help="Create a separate signed app copy. Does not launch WeChat.")
    make.add_argument("--app", type=Path, default=Path("/Applications/WeChat.app"))
    make.add_argument("--image", help="Bundle-relative core path, for future layouts.")
    make.add_argument("--output", required=True, type=Path, help="A new .app path; existing paths are never overwritten.")
    make.add_argument("--plugin", type=Path, default=Path(__file__).resolve().parent.parent / "build/WeChatTool.dylib")
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            report = analyze(args.app, args.image)
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                print(f"WeChat {report['version']} ({report['build']}): {report['status']}")
                for hook in report["hooks"]:
                    print(f"  {hook['arch']}: {hook['image']} @ 0x{hook['address']:x} ({hook['id']})")
                for problem in report["problems"]:
                    print(f"  {problem}")
                print("Static checks only. Confirm preservation with a live revoke test before relying on it.")
            return 0 if report["status"] == "structurally-compatible" else 2
        if sys.platform != "darwin":
            raise CompatibilityError("Preparing app copies requires macOS.")
        report = prepare(args.app, args.output, args.plugin, image_relative=args.image)
        print(json.dumps(report, indent=2))
        return 0
    except (CompatibilityError, MachOError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"WeChatTool: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr.decode(errors="replace").strip(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
