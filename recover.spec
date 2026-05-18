# recover.spec
# Build with:  pyinstaller recover.spec

a = Analysis(
    ['recover.py'],
    hiddenimports=[],
    datas=[],
    hookspath=[],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name='recover',
    console=True,   # keep the console — user needs to read the menu and type
    onefile=True,
)
