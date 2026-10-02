"""Build a Linux Lambda zip. Run via uv; deployment is a separate explicit step."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
source = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix='honnoki-mcp-') as staging:
    target = Path(staging)
    subprocess.run(['uv', 'pip', 'install', '--python-version', '3.12',
        '--python-platform', 'x86_64-manylinux2014', '--only-binary', ':all:',
        '--target', staging, '-r', str(source / 'requirements-aws.txt')], check=True)
    for name in ['server.py', 'lambda_handler.py', 'book-cards.html']:
        shutil.copy2(source / name, target / name)
    shutil.copy2(source.parents[1] / 'frontend/public/app-icon.png', target / 'app-icon.png')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.make_archive(str(args.output.with_suffix('')), 'zip', target)
