"""Install this portable kit's skill links; preserve existing skill ownership."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

KIT = Path(__file__).resolve().parents[1]


def install(project, claude=False):
    source = KIT / "skills/agent-workflow"
    results = []
    for namespace in ([".agents", ".claude"] if claude else [".agents"]):
        destination = project / namespace / "skills/agent-workflow"
        for ancestor in project.parents:
            inherited = ancestor / namespace / "skills/agent-workflow"
            if inherited.exists() and inherited.resolve() == source.resolve():
                results.append(f"{namespace}: inherited from {ancestor}")
                break
        else:
            if destination.exists() or destination.is_symlink():
                if destination.resolve() != source.resolve():
                    raise ValueError(f"Existing skill preserved: {destination}")
                results.append(f"{namespace}: already installed")
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                env = dict(os.environ, WF_LINK=str(destination), WF_SOURCE=str(source))
                subprocess.run(["pwsh", "-NoProfile", "-Command",
                                "New-Item -ItemType Junction -Path $env:WF_LINK -Target $env:WF_SOURCE | Out-Null"],
                               env=env, check=True, capture_output=True)
            else:
                destination.symlink_to(source, target_is_directory=True)
            results.append(f"{namespace}: installed {destination}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=KIT)
    parser.add_argument("--claude", action="store_true")
    args = parser.parse_args()
    try:
        target = args.project.resolve()
        if not target.is_dir():
            raise ValueError("Project must exist")
        print("\n".join(install(target, args.claude)))
        print("Start a new chat/reload the client to discover the skill.")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
