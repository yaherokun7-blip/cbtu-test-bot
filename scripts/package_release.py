"""Build a Railway source archive from an explicit allowlist, never local data."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL = (".gitignore", ".dockerignore", ".env.example", "Dockerfile", "railway.json",
             "README.md", "RAILWAY.md", "requirements.txt", "main.py", "serve.py")
SOURCE_DIRS = ("cogs", "utils", "portal", "tests", "scripts")
SUFFIXES = {".py", ".html", ".css", ".js", ".svg"}


def build():
    files = [ROOT / name for name in TOP_LEVEL]
    for directory in SOURCE_DIRS:
        files.extend(path for path in (ROOT / directory).rglob("*")
                     if path.is_file() and not path.is_symlink() and path.suffix in SUFFIXES
                     and "__pycache__" not in path.parts and not path.name.startswith("preview"))
    output = ROOT / "dist" / "cbtu-test-bot-railway.zip"
    output.parent.mkdir(exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(ROOT).as_posix())
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        assert ".env" not in archive.namelist()
        assert all(not name.startswith(("data/", ".venv/", "cloud/")) for name in archive.namelist())
        print(f"Created {output} ({len(archive.namelist())} files)")


if __name__ == "__main__":
    build()
