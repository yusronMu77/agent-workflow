"""Skill-relative launcher; follows a junction to the portable kit, not the calling repo."""
from pathlib import Path
import runpy
import sys

sys.dont_write_bytecode = True

root = Path(__file__).resolve().parents[3]
# Direct installations carry the same core inside the skill directory.
if not (root / 'workflow_core').is_dir():
    root = Path(__file__).resolve().parents[1] / '_kit'
if not any(arg == '--project' or arg.startswith('--project=') for arg in sys.argv[1:]) and '--version' not in sys.argv[1:]:
    sys.exit('Skill launcher requires --project ABSOLUTE_TARGET; plugin cache is not a user project')
sys.path.insert(0, str(root))
runpy.run_path(str(root / "wf.py"), run_name="__main__")
